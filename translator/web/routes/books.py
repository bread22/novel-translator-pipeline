from __future__ import annotations

import copy
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Any
import uuid
import zipfile

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from translator.core.config import load_config
from translator.core.layout import apply_horizontal_layout, inject_epub_metadata
from translator.core.metadata import extract_book_metadata, sanitize_epub_filename
from translator.core.novel_tool import NOVEL_TRANSLATOR_ROOT, call_novel_translator
from translator.core.paths import PathResolver
from translator.core.workspace import BookWorkspace, json_file_lock, read_json, safe_book_name, utc_now, write_json
from translator.pipeline.chapter_pipeline import manifest_path, paragraph_map
from translator.core.job_manager import job_manager
from translator.web.models import (
    BookSummary,
    ChapterDetail,
    ChapterSummary,
    ParagraphItem,
    ParagraphUpdateRequest,
)
from translator.web.path_policy import book_id_from_title, validate_book_id


router = APIRouter(prefix="/books", tags=["Books"])


@lru_cache(maxsize=None)
def _export_lock(book_id: str) -> threading.Lock:
    return threading.Lock()


def get_output_root() -> Path:
    config = load_config()
    return PathResolver.for_config().output_root(config)


def get_provenance_and_diagnostics(workspace: BookWorkspace) -> tuple[dict[str, Any], dict[str, Any]]:
    provenance = read_json(workspace.data_dir / "translation-provenance.json", default={})
    diagnostics = read_json(workspace.data_dir / "provider-diagnostics.json", default={})
    return provenance, diagnostics


def _has_valid_epub(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0 or not zipfile.is_zipfile(path):
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return archive.testzip() is None and "META-INF/container.xml" in archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False


def summarize_book(book_id: str, manifest: dict[str, Any], output_root: Path) -> BookSummary:
    title = manifest.get("title", book_id)
    source_type = manifest.get("source_type", "epub")
    chapters = manifest.get("chapters", [])
    total_chapters = len(chapters)

    total_paras = 0
    translated_paras = 0
    translated_chaps = 0

    for ch in chapters:
        ch_paras = ch.get("paragraphs", [])
        total_paras += len(ch_paras)
        ch_translated = sum(1 for p in ch_paras if bool(str(p.get("translated", "")).strip()))
        translated_paras += ch_translated
        if len(ch_paras) > 0 and ch_translated == len(ch_paras):
            translated_chaps += 1

    progress = round(translated_paras / max(1, total_paras), 3) if total_paras > 0 else 0.0

    workspace = BookWorkspace.at(output_root, title)
    has_output_epub = _has_valid_epub(workspace.epub_path)
    status = "completed" if (total_paras > 0 and translated_paras == total_paras) else "pending"
    active_task = job_manager.get_task(book_id)
    if active_task:
        if active_task.status == "paused":
            status = "paused"
        elif active_task.status == "failed":
            status = "error"
        elif active_task.status in {"running", "pausing", "cancelling", "recovery_pending"}:
            status = active_task.phase if active_task.phase in {"translating", "reviewing"} else "translating"

    return BookSummary(
        id=book_id,
        name=title,
        source_type=source_type,
        total_chapters=total_chapters,
        translated_chapters=translated_chaps,
        total_paragraphs=total_paras,
        translated_paragraphs=translated_paras,
        progress_percentage=progress,
        status=status,
        has_output_epub=has_output_epub,
        epub_download_url=f"/api/v1/books/{book_id}/download" if has_output_epub else None,
        created_at=manifest.get("created_at"),
        updated_at=manifest.get("updated_at"),
    )


@router.get("", response_model=list[BookSummary])
def list_books() -> list[BookSummary]:
    output_root = get_output_root()
    books_dir = NOVEL_TRANSLATOR_ROOT / "data" / "books"
    if not books_dir.exists():
        return []

    summaries = []
    for manifest_file in sorted(books_dir.glob("*/manifest.json")):
        book_id = manifest_file.parent.name
        manifest = read_json(manifest_file, default=None)
        if manifest:
            summaries.append(summarize_book(book_id, manifest, output_root))
    return summaries


@router.post("/upload", response_model=BookSummary)
async def upload_book(file: UploadFile = File(...), replace: bool = Query(False)) -> BookSummary:
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名无效")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".epub", ".txt"}:
        raise HTTPException(status_code=400, detail="仅支持上传 .epub 或 .txt 格式电子书")

    max_upload_bytes = int(os.environ.get("MAX_UPLOAD_BYTES", 50 * 1024 * 1024))
    written = 0
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp_path = Path(tmp.name)
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > max_upload_bytes:
                    raise HTTPException(status_code=413, detail=f"上传文件超过 {max_upload_bytes} 字节限制")
                tmp.write(chunk)
            tmp.flush()
            os.fsync(tmp.fileno())
    except Exception:
        if "tmp_path" in locals():
            tmp_path.unlink(missing_ok=True)
        raise

    book_title = Path(file.filename).stem
    try:
        book_id = book_id_from_title(book_title)
    except ValueError as exc:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        if written == 0:
            raise HTTPException(status_code=400, detail="上传文件为空")
        if suffix == ".txt":
            try:
                tmp_path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                raise HTTPException(status_code=400, detail="TXT 文件必须使用 UTF-8 编码") from exc
        else:
            try:
                with zipfile.ZipFile(tmp_path) as archive:
                    if archive.testzip() is not None or "META-INF/container.xml" not in archive.namelist():
                        raise ValueError("EPUB ZIP 结构不完整")
            except (zipfile.BadZipFile, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"EPUB 文件无效：{exc}") from exc

        existing_manifest = manifest_path(book_id)
        if existing_manifest.exists() and not replace:
            raise HTTPException(status_code=409, detail=f"书籍 ID 已存在：{book_id}；使用 replace=true 显式替换")
        # Register book in novel-translator
        result = call_novel_translator(
            "add-book",
            "--path",
            str(tmp_path),
            "--title",
            book_title,
            "--id",
            book_id,
        )
        registration_status = str(result.get("status", "")).strip().lower()
        registration_returncode = result.get("returncode")
        registration_succeeded = (
            registration_status in {"ok", "success", "warning"}
            and registration_returncode in {None, 0}
        )
        if not registration_succeeded:
            error_msg = "; ".join([e.get("message", "") for e in result.get("errors", [])]) or "Novel Translator 注册失败"
            raise HTTPException(status_code=500, detail=error_msg)

        summary = result.get("summary", {})
        registered_id = str(summary.get("book", "") or summary.get("book_id", "") or book_id)

        manifest = read_json(manifest_path(registered_id))
        if not manifest:
            raise HTTPException(status_code=500, detail=f"未找到已注册书籍 manifest: {registered_id}")

        output_root = get_output_root()
        title = manifest.get("title", book_title)
        workspace = BookWorkspace.at(output_root, title)
        workspace.initialize(source_epub=tmp_path if suffix == ".epub" else None, book_id=registered_id)

        return summarize_book(registered_id, manifest, output_root)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


@router.get("/{book_id}", response_model=BookSummary)
def get_book(book_id: str) -> BookSummary:
    manifest = read_json(manifest_path(book_id), default=None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"未找到书籍: {book_id}")
    output_root = get_output_root()
    return summarize_book(book_id, manifest, output_root)


@router.get("/{book_id}/chapters", response_model=list[ChapterSummary])
def list_chapters(book_id: str) -> list[ChapterSummary]:
    manifest = read_json(manifest_path(book_id), default=None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"未找到书籍: {book_id}")

    workspace = BookWorkspace.at(get_output_root(), manifest.get("title", book_id))
    summaries = []
    for idx, ch in enumerate(manifest.get("chapters", []), start=1):
        ch_id = ch.get("id", f"c{idx:04d}")
        ch_title = ch.get("title", ch_id)
        paras = ch.get("paragraphs", [])
        total_p = len(paras)
        trans_p = sum(1 for p in paras if bool(str(p.get("translated", "")).strip()))
        has_review_artifact = any(
            artifact.is_file()
            for artifact in (
                workspace.reviews_dir / f"{ch_id}-output.json",
                workspace.reviews_dir / f"{ch_id}-approved-fixes.json",
                workspace.reports_dir / f"{ch_id}.json",
            )
        )
        status = "reviewed" if (total_p > 0 and trans_p == total_p and has_review_artifact) else ("translated" if trans_p > 0 else "pending")
        summaries.append(
            ChapterSummary(
                id=ch_id,
                index=idx,
                title=ch_title,
                role=str(ch.get("role", "chapter")),
                total_paragraphs=total_p,
                translated_paragraphs=trans_p,
                status=status,
            )
        )
    return summaries


@router.get("/{book_id}/chapters/{chapter_id}", response_model=ChapterDetail)
def get_chapter_detail(book_id: str, chapter_id: str) -> ChapterDetail:
    manifest = read_json(manifest_path(book_id), default=None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"未找到书籍: {book_id}")

    output_root = get_output_root()
    title = manifest.get("title", book_id)
    workspace = BookWorkspace.at(output_root, title)
    provenance, _ = get_provenance_and_diagnostics(workspace)

    target_ch = None
    target_idx = 1
    for idx, ch in enumerate(manifest.get("chapters", []), start=1):
        if ch.get("id") == chapter_id or f"c{idx:04d}" == chapter_id:
            target_ch = ch
            target_idx = idx
            break

    if not target_ch:
        raise HTTPException(status_code=404, detail=f"未找到章节: {chapter_id}")

    paras_raw = target_ch.get("paragraphs", [])
    para_items = []
    for p_idx, p in enumerate(paras_raw):
        pid = str(p.get("id", f"p{p_idx:04d}"))
        source = str(p.get("source", ""))
        trans = str(p.get("translated", ""))

        prov_info = provenance.get("items", provenance).get(pid, {})
        provider = prov_info.get("provider")
        fallback_from = prov_info.get("fallback_from")
        fallback_reason = prov_info.get("reason")

        status = "pending"
        if trans.strip():
            if prov_info.get("action") == "binary_split_fallback" or fallback_from:
                status = "fallback_recovered"
            else:
                status = "translated"

        para_items.append(
            ParagraphItem(
                id=pid,
                index=p_idx,
                chapter_id=chapter_id,
                source=source,
                translated=trans,
                status=status,
                provider=provider,
                fallback_from=fallback_from,
                fallback_reason=fallback_reason,
                metadata=p.get("metadata", {}),
            )
        )

    total_p = len(para_items)
    trans_p = sum(1 for p in para_items if bool(p.translated.strip()))
    ch_status = "completed" if (total_p > 0 and trans_p == total_p) else ("in_progress" if trans_p > 0 else "pending")

    # Read chapter state summary if exists
    chapter_state_file = workspace.chapter_states_dir / f"{chapter_id}.json"
    ch_state = read_json(chapter_state_file, default={})
    summary_text = ch_state.get("summary", "")

    return ChapterDetail(
        id=chapter_id,
        index=target_idx,
        title=target_ch.get("title", chapter_id),
        role=str(target_ch.get("role", "chapter")),
        total_paragraphs=total_p,
        translated_paragraphs=trans_p,
        status=ch_status,
        paragraphs=para_items,
        chapter_summary=summary_text,
    )


@router.put("/{book_id}/paragraphs/{paragraph_id}")
def update_paragraph(book_id: str, paragraph_id: str, request: ParagraphUpdateRequest) -> dict[str, Any]:
    path = manifest_path(book_id)
    with json_file_lock(path):
        manifest = read_json(path, default=None)
        if not manifest:
            raise HTTPException(status_code=404, detail=f"未找到书籍: {book_id}")

        found = False
        for ch in manifest.get("chapters", []):
            for p in ch.get("paragraphs", []):
                if p.get("id") == paragraph_id:
                    p["translated"] = request.translated
                    p["updated_at"] = utc_now()
                    found = True
                    break
            if found:
                break

        if not found:
            raise HTTPException(status_code=404, detail=f"未找到段落: {paragraph_id}")

        write_json(path, manifest)
    return {"status": "ok", "paragraph_id": paragraph_id, "translated": request.translated}


def _export_book_locked(book_id: str, layout: str = Query("horizontal", pattern="^(horizontal|preserve)$")) -> dict[str, Any]:
    manifest = read_json(manifest_path(book_id), default=None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"未找到书籍: {book_id}")

    output_root = get_output_root()
    title = manifest.get("title", book_id)
    workspace = BookWorkspace.at(output_root, title)
    workspace.initialize(book_id=book_id)

    # Call novel-translator export
    exported = call_novel_translator(
        "export",
        "--book",
        book_id,
        "--format",
        "epub",
        "--output",
        str(workspace.epub_path),
        "--monolingual",
    )
    if exported.get("status") not in {"ok", "success", "exported"}:
        raise HTTPException(status_code=502, detail=f"EPUB export payload 未通过：{exported}")
    if not workspace.epub_path.is_file() or workspace.epub_path.stat().st_size == 0 or not zipfile.is_zipfile(workspace.epub_path):
        raise HTTPException(status_code=502, detail="EPUB export 产物无效")
    meta = extract_book_metadata(book_id, manifest, workspace)
    if layout == "horizontal":
        apply_horizontal_layout(workspace.epub_path, metadata=meta)
    else:
        inject_epub_metadata(workspace.epub_path, metadata=meta)

    validated = call_novel_translator("validate-epub", "--path", str(workspace.epub_path))
    if (
        not isinstance(validated, dict)
        or validated.get("status") not in {"ok", "success", "valid", "warning"}
        or bool(validated.get("errors"))
    ):
        raise HTTPException(status_code=502, detail=f"EPUB validate payload 未通过：{validated}")

    # Copy to translated/ root directory
    translated_dir = PathResolver.for_config().translated_root(load_config())
    translated_dir.mkdir(parents=True, exist_ok=True)
    target_name = sanitize_epub_filename(meta.get("title_zh", title), meta.get("author_zh", ""))
    target_epub = translated_dir / target_name
    temporary_target = translated_dir / f".{target_epub.name}.{uuid.uuid4().hex}.tmp"
    shutil.copy2(workspace.epub_path, temporary_target)
    source_hash = hashlib.sha256(workspace.epub_path.read_bytes()).hexdigest()
    copied_hash = hashlib.sha256(temporary_target.read_bytes()).hexdigest()
    if source_hash != copied_hash:
        temporary_target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="EPUB 临时交付副本 hash 不一致")
    temporary_target.replace(target_epub)

    return {
        "status": "exported",
        "layout": layout,
        "epub_path": str(workspace.epub_path),
        "target_filename": target_name,
        "download_url": f"/api/v1/books/{book_id}/download",
        "sha256": source_hash,
    }


@router.post("/{book_id}/export")
def export_book(book_id: str, layout: str = Query("horizontal", pattern="^(horizontal|preserve)$")) -> dict[str, Any]:
    with _export_lock(book_id):
        return _export_book_locked(book_id, layout)


@router.get("/{book_id}/download")
def download_book_epub(book_id: str) -> FileResponse:
    manifest = read_json(manifest_path(book_id), default=None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"未找到书籍: {book_id}")

    output_root = get_output_root()
    title = manifest.get("title", book_id)
    workspace = BookWorkspace.at(output_root, title)

    if not workspace.epub_path.exists():
        raise HTTPException(status_code=404, detail="尚未生成导出 EPUB，请先执行导出")

    meta = read_json(workspace.book_metadata_path, default=None)
    if isinstance(meta, dict) and meta.get("title_zh"):
        download_filename = sanitize_epub_filename(meta["title_zh"], meta.get("author_zh", ""))
    else:
        download_filename = workspace.epub_path.name

    return FileResponse(
        path=str(workspace.epub_path),
        filename=download_filename,
        media_type="application/epub+zip",
    )


@router.delete("/{book_id}")
def delete_book(book_id: str) -> dict[str, Any]:
    try:
        validate_book_id(book_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    manifest = read_json(manifest_path(book_id), default=None)
    output_root = get_output_root()

    title = manifest.get("title", book_id) if manifest else book_id
    if not job_manager.cancel_book_and_wait(book_id):
        raise HTTPException(status_code=409, detail="活动任务未在超时内停止；书籍与任务均保持不变")

    workspace = BookWorkspace.at(output_root, title)
    safe_dir = output_root / safe_book_name(title)
    data_book_dir = NOVEL_TRANSLATOR_ROOT / "data" / "books" / book_id

    deletion_roots: list[Path] = []
    seen_roots: set[Path] = set()
    for candidate in (workspace.root, safe_dir, data_book_dir):
        if not candidate.exists():
            continue
        resolved = candidate.resolve()
        if resolved not in seen_roots:
            seen_roots.add(resolved)
            deletion_roots.append(resolved)

    with tempfile.TemporaryDirectory(prefix="book-delete-") as backup_dir:
        backups: list[tuple[Path, Path]] = []
        for index, root in enumerate(deletion_roots):
            backup = Path(backup_dir) / str(index)
            shutil.copytree(root, backup, symlinks=True)
            backups.append((root, backup))

        try:
            for root, _backup in backups:
                shutil.rmtree(root)
                if root.exists():
                    raise OSError(f"删除未完成: {root}")
        except Exception as exc:
            rollback_errors: list[Exception] = []
            for root, backup in backups:
                try:
                    if root.exists():
                        shutil.rmtree(root)
                    if root.exists():
                        shutil.copytree(backup, root, symlinks=True, dirs_exist_ok=True)
                    else:
                        shutil.copytree(backup, root, symlinks=True)
                except Exception as rollback_error:
                    rollback_errors.append(rollback_error)
            if rollback_errors:
                raise HTTPException(status_code=500, detail="删除书籍失败，且回滚未完全成功") from rollback_errors[0]
            raise HTTPException(status_code=500, detail=f"删除书籍失败，已回滚: {exc}") from exc

    return {"status": "ok", "message": f"书籍 '{title}' 已彻底删除"}


@router.post("/{book_id}/reset")
def reset_book(book_id: str) -> dict[str, Any]:
    try:
        validate_book_id(book_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    manifest = read_json(manifest_path(book_id), default=None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"未找到书籍: {book_id}")

    output_root = get_output_root()
    title = manifest.get("title", book_id)
    workspace = BookWorkspace.at(output_root, title)
    if not job_manager.cancel_book_and_wait(book_id):
        raise HTTPException(status_code=409, detail="活动任务未在超时内停止；书籍与任务均保持不变")

    manifest_file = manifest_path(book_id)
    original_manifest = copy.deepcopy(manifest)
    with tempfile.TemporaryDirectory(prefix="book-reset-") as backup_dir:
        backup_root = Path(backup_dir) / "workspace"
        workspace_existed = workspace.root.exists()
        if workspace_existed:
            shutil.copytree(workspace.root, backup_root, symlinks=True)

        try:
            # Also try calling external CLI if available.
            try:
                call_novel_translator("reset-translations", "--book", book_id, "--all")
            except Exception:
                pass

            # Reset ALL workspace files (progress, memory, glossary, reports, reviews, snapshots).
            workspace.reset(book_id=book_id)

            # Publish the manifest reset only after the workspace reset succeeds.
            for ch in manifest.get("chapters", []):
                for p in ch.get("paragraphs", []):
                    p["translated"] = ""
            write_json(manifest_file, manifest)
        except Exception:
            # Restore both authorities so a failed reset cannot expose a partial state.
            write_json(manifest_file, original_manifest)
            if workspace.root.exists():
                shutil.rmtree(workspace.root)
            if workspace_existed:
                shutil.copytree(backup_root, workspace.root, symlinks=True)
            raise

    return {"status": "ok", "message": f"书籍 '{title}' 翻译进度、长期记忆、术语库与质检报告已全部清空重置"}


@router.get("/{book_id}/events")
def get_book_events(book_id: str, limit: int = 500) -> list[dict[str, Any]]:
    """Retrieve persistent historical event logs for this book."""
    try:
        validate_book_id(book_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    manifest = read_json(manifest_path(book_id), default=None)
    title = manifest.get("title", book_id) if manifest else book_id
    output_root = get_output_root()
    from translator.web.events import read_book_events
    # Keep the API's canonical book ID in projected diagnostic events. The
    # workspace itself is still resolved through the display title when needed.
    events = read_book_events(book_id, limit=limit, output_root=output_root)
    if not events and title != book_id:
        events = read_book_events(title, limit=limit, output_root=output_root)
    return events
