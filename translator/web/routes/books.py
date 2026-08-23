from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from translator.core.config import load_config
from translator.core.layout import apply_horizontal_layout
from translator.core.novel_tool import NOVEL_TRANSLATOR_ROOT, call_novel_translator
from translator.core.workspace import BookWorkspace, read_json, safe_book_name, utc_now, write_json
from translator.pipeline.chapter_pipeline import manifest_path, paragraph_map
from translator.web.models import (
    BookSummary,
    ChapterDetail,
    ChapterSummary,
    ParagraphItem,
    ParagraphUpdateRequest,
)


router = APIRouter(prefix="/books", tags=["Books"])


def get_output_root() -> Path:
    config = load_config()
    return Path(config["paths"]["output_root"]).resolve()


def get_provenance_and_diagnostics(workspace: BookWorkspace) -> tuple[dict[str, Any], dict[str, Any]]:
    provenance = read_json(workspace.data_dir / "translation-provenance.json", default={})
    diagnostics = read_json(workspace.data_dir / "provider-diagnostics.json", default={})
    return provenance, diagnostics


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
    has_output_epub = workspace.epub_path.exists()
    status = "completed" if (total_paras > 0 and translated_paras == total_paras) else "pending"

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
async def upload_book(file: UploadFile = File(...)) -> BookSummary:
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名无效")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".epub", ".txt"}:
        raise HTTPException(status_code=400, detail="仅支持上传 .epub 或 .txt 格式电子书")

    # Save to temp location
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)

    book_title = Path(file.filename).stem
    book_id = safe_book_name(book_title)

    try:
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
        if result.get("status") != "ok":
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

    summaries = []
    for idx, ch in enumerate(manifest.get("chapters", []), start=1):
        ch_id = ch.get("id", f"c{idx:04d}")
        ch_title = ch.get("title", ch_id)
        paras = ch.get("paragraphs", [])
        total_p = len(paras)
        trans_p = sum(1 for p in paras if bool(str(p.get("translated", "")).strip()))
        status = "reviewed" if (total_p > 0 and trans_p == total_p) else ("translated" if trans_p > 0 else "pending")
        summaries.append(
            ChapterSummary(
                id=ch_id,
                index=idx,
                title=ch_title,
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

        prov_info = provenance.get(pid, {})
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
        total_paragraphs=total_p,
        translated_paragraphs=trans_p,
        status=ch_status,
        paragraphs=para_items,
        chapter_summary=summary_text,
    )


@router.put("/{book_id}/paragraphs/{paragraph_id}")
def update_paragraph(book_id: str, paragraph_id: str, request: ParagraphUpdateRequest) -> dict[str, Any]:
    path = manifest_path(book_id)
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


@router.post("/{book_id}/export")
def export_book(book_id: str, layout: str = Query("horizontal", pattern="^(horizontal|preserve)$")) -> dict[str, Any]:
    manifest = read_json(manifest_path(book_id), default=None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"未找到书籍: {book_id}")

    output_root = get_output_root()
    title = manifest.get("title", book_id)
    workspace = BookWorkspace.at(output_root, title)
    workspace.initialize(book_id=book_id)

    # Call novel-translator export
    call_novel_translator(
        "export",
        "--book",
        book_id,
        "--format",
        "epub",
        "--output",
        str(workspace.epub_path),
        "--monolingual",
    )

    if layout == "horizontal":
        apply_horizontal_layout(workspace.epub_path)

    # Copy to translated/ root directory
    translated_dir = Path("translated").resolve()
    translated_dir.mkdir(parents=True, exist_ok=True)
    target_epub = translated_dir / workspace.epub_path.name
    shutil.copy2(workspace.epub_path, target_epub)

    return {
        "status": "exported",
        "layout": layout,
        "epub_path": str(workspace.epub_path),
        "download_url": f"/api/v1/books/{book_id}/download",
    }


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

    return FileResponse(
        path=str(workspace.epub_path),
        filename=workspace.epub_path.name,
        media_type="application/epub+zip",
    )


@router.delete("/{book_id}")
def delete_book(book_id: str) -> dict[str, Any]:
    manifest = read_json(manifest_path(book_id), default=None)
    output_root = get_output_root()

    title = manifest.get("title", book_id) if manifest else book_id

    # 1. Remove output workspace
    workspace = BookWorkspace.at(output_root, title)
    if workspace.root.exists():
        shutil.rmtree(workspace.root, ignore_errors=True)

    safe_dir = output_root / safe_book_name(title)
    if safe_dir.exists() and safe_dir != workspace.root:
        shutil.rmtree(safe_dir, ignore_errors=True)

    # 2. Remove from novel-translator data/books
    data_book_dir = NOVEL_TRANSLATOR_ROOT / "data" / "books" / book_id
    if data_book_dir.exists():
        shutil.rmtree(data_book_dir, ignore_errors=True)

    return {"status": "ok", "message": f"书籍 '{title}' 已彻底删除"}


@router.post("/{book_id}/reset")
def reset_book(book_id: str) -> dict[str, Any]:
    manifest = read_json(manifest_path(book_id), default=None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"未找到书籍: {book_id}")

    output_root = get_output_root()
    title = manifest.get("title", book_id)
    workspace = BookWorkspace.at(output_root, title)

    # 1. Reset manifest paragraphs directly (100% reliable)
    for ch in manifest.get("chapters", []):
        for p in ch.get("paragraphs", []):
            p["translated"] = ""
    write_json(manifest_path(book_id), manifest)

    # Also try calling external CLI if available
    try:
        call_novel_translator("reset-translations", "--book", book_id, "--all")
    except Exception:
        pass

    # 2. Reset ALL workspace files (progress, memory, glossary, reports, reviews, snapshots)
    workspace.reset(book_id=book_id)

    return {"status": "ok", "message": f"书籍 '{title}' 翻译进度、长期记忆、术语库与质检报告已全部清空重置"}
