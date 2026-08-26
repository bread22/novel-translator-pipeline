from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import stat
import shutil
import tempfile
import threading
import zipfile
from types import ModuleType
from typing import Any, Iterable

from translator.glossary.lifecycle import merge_term_candidates
from translator.glossary.projection import build_translation_term_projection

fcntl: ModuleType | None
try:
    import fcntl as _fcntl
    fcntl = _fcntl
except ImportError:  # pragma: no cover - Windows uses the process-local lock.
    fcntl = None


INVALID_DIRECTORY_CHARS = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")
_JSON_LOCKS: dict[Path, threading.RLock] = {}
_JSON_LOCKS_GUARD = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_book_name(value: str) -> str:
    name = INVALID_DIRECTORY_CHARS.sub("_", value).strip().strip(".")
    if not name or name in {".", ".."}:
        raise ValueError("书籍目录名为空或无效")
    return name


@contextmanager
def json_file_lock(path: Path):
    """Serialize read-modify-write operations for one JSON path."""
    resolved = path.expanduser().resolve()
    with _JSON_LOCKS_GUARD:
        thread_lock = _JSON_LOCKS.setdefault(resolved, threading.RLock())

    with thread_lock:
        lock_path = resolved.with_name(f".{resolved.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return path


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def safely_extract_epub(
    epub_path: Path,
    target: Path,
    *,
    max_files: int = 10_000,
    max_single_size: int = 100 * 1024 * 1024,
    max_total_size: int = 500 * 1024 * 1024,
    max_compression_ratio: float = 200.0,
) -> None:
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    with zipfile.ZipFile(epub_path) as archive:
        members = archive.infolist()
        if len(members) > max_files:
            raise ValueError(f"EPUB 文件数超过限制：{len(members)} > {max_files}")
        total_size = 0
        normalized_names: set[str] = set()
        for member in members:
            normalized = member.filename.replace("\\", "/")
            if normalized in normalized_names:
                raise ValueError(f"EPUB 包含重复路径：{member.filename}")
            normalized_names.add(normalized)
            destination = (target / normalized).resolve()
            if destination != root and root not in destination.parents:
                raise ValueError(f"EPUB 包含越界路径：{member.filename}")
            mode = member.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            if stat.S_ISLNK(mode) or (file_type and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))):
                raise ValueError(f"EPUB 包含不支持的文件类型：{member.filename}")
            if member.file_size > max_single_size:
                raise ValueError(f"EPUB 单文件展开大小超过限制：{member.filename}")
            total_size += member.file_size
            if total_size > max_total_size:
                raise ValueError("EPUB 总展开大小超过限制")
            ratio = member.file_size / max(1, member.compress_size)
            if ratio > max_compression_ratio:
                raise ValueError(f"EPUB 压缩膨胀率超过限制：{member.filename}")
        for member in members:
            normalized = member.filename.replace("\\", "/")
            destination = (target / normalized).resolve()
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


@dataclass(frozen=True)
class BookWorkspace:
    root: Path

    @classmethod
    def at(cls, output_root: Path, display_name: str) -> "BookWorkspace":
        return cls(output_root.resolve() / safe_book_name(display_name))

    @property
    def input_dir(self) -> Path:
        return self.root / "input"

    @property
    def original_epub(self) -> Path:
        return self.input_dir / "original.epub"

    @property
    def unpacked_dir(self) -> Path:
        return self.root / "unpacked"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def glossary_path(self) -> Path:
        return self.data_dir / "glossary.json"

    @property
    def novel_translator_terms_path(self) -> Path:
        return self.data_dir / "novel-translator-terms.json"

    @property
    def terms_path(self) -> Path:
        return self.novel_translator_terms_path

    @property
    def name_mapping_review_path(self) -> Path:
        return self.data_dir / "name-mapping-review.jsonl"

    @property
    def progress_path(self) -> Path:
        return self.data_dir / "progress.json"

    @property
    def book_memory_path(self) -> Path:
        return self.data_dir / "book_memory.json"

    @property
    def book_metadata_path(self) -> Path:
        return self.data_dir / "book_metadata.json"

    @property
    def chapter_states_dir(self) -> Path:
        return self.data_dir / "chapter_states"

    @property
    def reviews_dir(self) -> Path:
        return self.root / "reviews"

    @property
    def snapshots_dir(self) -> Path:
        return self.root / "snapshots"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def epub_path(self) -> Path:
        return self.root / f"{self.root.name}-中文.epub"

    @property
    def output_epub(self) -> Path:
        return self.epub_path

    def initialize(self, source_epub: Path | None = None, *, book_id: str = "") -> None:
        for directory in (
            self.input_dir,
            self.data_dir,
            self.reviews_dir,
            self.snapshots_dir,
            self.reports_dir,
            self.chapter_states_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        if source_epub is not None:
            source_epub = source_epub.expanduser().resolve()
            if not source_epub.exists():
                raise FileNotFoundError(source_epub)
            if not self.original_epub.exists():
                shutil.copy2(source_epub, self.original_epub)
            if not self.unpacked_dir.exists():
                safely_extract_epub(self.original_epub, self.unpacked_dir)
        if not self.glossary_path.exists():
            write_json(
                self.glossary_path,
                {"schema_version": "3.0", "book": book_id, "updated_at": utc_now(), "terms": [], "conflicts": [], "revisions": []},
            )
        else:
            existing_glossary = read_json(self.glossary_path, {})
            if isinstance(existing_glossary, dict) and existing_glossary.get("schema_version") != "3.0":
                # Keep the compatibility import here (rather than at module load)
                # to avoid a core<->script import cycle.  The migration itself
                # creates the v2 backup and verifies the reopened v3 document.
                from scripts.migrate_glossary_v3 import migrate
                migrate(self.glossary_path, apply=True)
        if not self.progress_path.exists():
            write_json(
                self.progress_path,
                {"book": book_id, "state": "initialized", "completed_cycles": 0, "last_chunk": "", "updated_at": utc_now()},
            )
        if not self.book_memory_path.exists():
            write_json(self.book_memory_path, empty_book_memory(book_id))

    def reset(self, book_id: str = "") -> None:
        """Completely reset all runtime progress, memory, glossary, reports, reviews, snapshots, and generated artifacts."""
        if self.data_dir.exists():
            write_json(
                self.progress_path,
                {"book": book_id, "state": "initialized", "completed_cycles": 0, "last_chunk": "", "updated_at": utc_now()},
            )
            write_json(
                self.book_memory_path,
                empty_book_memory(book_id),
            )
            write_json(
                self.glossary_path,
                {"schema_version": "3.0", "book": book_id, "updated_at": utc_now(), "terms": [], "conflicts": [], "revisions": []},
            )
            write_json(
                self.novel_translator_terms_path,
                {"terms": []},
            )
            write_json(
                self.data_dir / "translation-provenance.json",
                {},
            )
            write_json(
                self.data_dir / "provider-diagnostics.json",
                {},
            )
            if self.chapter_states_dir.exists():
                for p in self.chapter_states_dir.glob("*.json"):
                    p.unlink(missing_ok=True)
            # Event history belongs to the resettable runtime state as well.
            (self.data_dir / "events.jsonl").unlink(missing_ok=True)
            self.name_mapping_review_path.unlink(missing_ok=True)
            self.name_mapping_review_path.with_name(f".{self.name_mapping_review_path.name}.lock").unlink(missing_ok=True)

        for dir_to_clean in (self.reviews_dir, self.reports_dir, self.snapshots_dir):
            if dir_to_clean.exists():
                for p in dir_to_clean.glob("*"):
                    if p.is_file():
                        p.unlink(missing_ok=True)

        if self.epub_path.exists():
            self.epub_path.unlink(missing_ok=True)


def merge_term_updates(
    glossary: dict[str, Any],
    updates: Iterable[dict[str, Any]],
    chunk_id: str = "",
    *,
    threshold: float = 0.9,
    reporter: str = "legacy_merge",
    evidence_texts: dict[str, Any] | None = None,
    name_mapping_queue_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    prepared: list[dict[str, Any]] = []
    for index, raw in enumerate(updates):
        if not isinstance(raw, dict):
            prepared.append({})
            continue
        item = dict(raw)
        confidence = float(item.get("confidence", 0) or 0)
        if confidence < threshold:
            prepared.append({})
            continue
        # This compatibility wrapper is retained for callers that predate v3.  The
        # normal pipeline uses apply_glossary_delta with explicit evidence and category.
        if "category" not in item:
            item["category"] = "person"
        evidence_ids = list(item.get("evidence_ids", []) or item.get("sample_ids", []) or [f"{chunk_id}:{index}"])
        item["evidence_ids"] = [str(value) for value in evidence_ids]
        prepared.append(item)
    supplied_texts = dict(evidence_texts or {})
    for item in prepared:
        if isinstance(item, dict) and item.get("source"):
            for evidence_id in item.get("evidence_ids", []):
                supplied_texts.setdefault(str(evidence_id), str(item.get("source", "")))
    merged, summary = merge_term_candidates(
        glossary,
        (item for item in prepared if item),
        chapter_id=chunk_id,
        reporter=reporter,
        evidence_texts=supplied_texts,
        name_mapping_queue_path=name_mapping_queue_path,
    )
    rejected_by_threshold = sum(1 for item in prepared if not item)
    summary["rejected"] = int(summary.get("rejected", 0)) + rejected_by_threshold
    summary["added"] = int(summary.get("added", 0))
    summary["confirmed"] = int(summary.get("confirmed", 0))
    summary["conflicted"] = int(summary.get("conflicted", 0))
    return merged, summary


def novel_translator_terms(glossary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return build_translation_term_projection(glossary)


def empty_book_memory(book: str = "") -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "book": book,
        "entries": [],
        "conflicts": [],
        "timeline": [],
        "chapter_states": [],
        "updated_at": utc_now(),
    }


def normalize_book_memory_v2(memory: dict[str, Any], book: str = "") -> tuple[dict[str, Any], dict[str, int]]:
    """Convert legacy character/world-setting collections to the canonical entry list."""
    current = memory if isinstance(memory, dict) else {}
    entries = [dict(item) for item in current.get("entries", []) if isinstance(item, dict)]
    conflicts = [dict(item) for item in current.get("conflicts", []) if isinstance(item, dict)]
    by_key = {str(item.get("key", "")).strip(): item for item in entries if str(item.get("key", "")).strip()}
    added = modified = conflicted = 0

    def ingest(raw: Any, *, category: str, key_field: str, value_field: str) -> None:
        nonlocal added, modified, conflicted
        if not isinstance(raw, dict):
            return
        key = str(raw.get(key_field, "")).strip()
        value = str(raw.get(value_field, "")).strip()
        if not key or not value:
            return
        candidate = {
            "key": key,
            "value": value,
            "category": str(raw.get("category") or category),
            "note": str(raw.get("note") or raw.get("role") or ""),
            "confidence": float(raw.get("confidence", 1.0)),
            "first_seen_chapter": raw.get("first_seen") or raw.get("first_seen_chapter") or "",
            "last_seen_chapter": raw.get("last_seen") or raw.get("last_seen_chapter") or "",
        }
        existing = by_key.get(key)
        if existing is None:
            entries.append(candidate)
            by_key[key] = candidate
            added += 1
        elif str(existing.get("value", "")) != value:
            conflicts.append({
                "key": key,
                "existing_value": str(existing.get("value", "")),
                "proposed_value": value,
                "note": "legacy memory migration conflict",
            })
            conflicted += 1
        else:
            modified += 1

    for character in current.get("characters", []) if isinstance(current.get("characters"), list) else []:
        ingest(character, category="character", key_field="name", value_field="summary")
    for setting in current.get("world_settings", []) if isinstance(current.get("world_settings"), list) else []:
        ingest(setting, category="fact", key_field="term", value_field="explanation")

    known = {"schema_version", "version", "book", "entries", "conflicts", "characters", "world_settings", "timeline", "chapter_states", "updated_at"}
    normalized = {
        "schema_version": "2.0",
        "book": str(current.get("book") or book),
        "entries": sorted(entries, key=lambda item: str(item.get("key", ""))),
        "conflicts": conflicts,
        "timeline": list(current.get("timeline", [])) if isinstance(current.get("timeline"), list) else [],
        "chapter_states": list(current.get("chapter_states", [])) if isinstance(current.get("chapter_states"), list) else [],
        "updated_at": str(current.get("updated_at") or utc_now()),
    }
    return normalized, {
        "added": added,
        "modified": modified,
        "conflicts": conflicted,
        "unknown_fields": len(set(current) - known),
    }


def merge_memory_delta(
    memory: dict[str, Any],
    delta: dict[str, Any],
    chapter_id: str = "",
    *,
    threshold: float = 0.9,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Merge structured long-term memory without allowing a model to replace it wholesale."""
    current, _migration = normalize_book_memory_v2(memory or empty_book_memory(), str((memory or {}).get("book", "")))
    entries = [dict(item) for item in current.get("entries", []) if isinstance(item, dict)]
    conflicts = [dict(item) for item in current.get("conflicts", []) if isinstance(item, dict)]
    by_key = {str(item.get("key", "")).strip(): item for item in entries if str(item.get("key", "")).strip()}
    added = updated = rejected = conflicted = 0
    if not isinstance(delta, dict):
        return current, {"added": 0, "updated": 0, "rejected": 1, "conflicted": 0}
    for operation in ("add", "update"):
        raw_items = delta.get(operation, [])
        if not isinstance(raw_items, list):
            rejected += 1
            continue
        for raw in raw_items:
            if not isinstance(raw, dict):
                rejected += 1
                continue
            key = str(raw.get("key", "")).strip()
            value = str(raw.get("value", "")).strip()
            confidence = float(raw.get("confidence", 0) or 0)
            if not key or not value or confidence < threshold:
                rejected += 1
                continue
            existing = by_key.get(key)
            if existing is not None and str(existing.get("value", "")).strip() != value:
                conflicts.append({
                    "key": key,
                    "existing_value": existing.get("value", ""),
                    "proposed_value": value,
                    "confidence": confidence,
                    "chapter_id": chapter_id,
                    "created_at": utc_now(),
                })
                conflicted += 1
                continue
            if existing is None:
                existing = {
                    "key": key,
                    "value": value,
                    "category": str(raw.get("category", "fact")).strip() or "fact",
                    "note": str(raw.get("note", "")).strip(),
                    "confidence": confidence,
                    "first_seen_chapter": chapter_id,
                    "last_seen_chapter": chapter_id,
                }
                entries.append(existing)
                by_key[key] = existing
                added += 1
            else:
                existing["confidence"] = max(float(existing.get("confidence", 0) or 0), confidence)
                existing["last_seen_chapter"] = chapter_id
                if raw.get("note"):
                    existing["note"] = str(raw["note"]).strip()
                updated += 1
    current["entries"] = sorted(entries, key=lambda item: str(item.get("key", "")))
    current["conflicts"] = conflicts
    current["updated_at"] = utc_now()
    return current, {"added": added, "updated": updated, "rejected": rejected, "conflicted": conflicted}


def merge_chapter_state(
    chapter_id: str | dict[str, Any],
    title: str | dict[str, Any] = "",
    delta: dict[str, Any] | None = None,
    *,
    chapter_id_kw: str = "",
) -> dict[str, Any]:
    if isinstance(chapter_id, dict):
        current = dict(chapter_id)
        delta_dict = title if isinstance(title, dict) else {}
        for key, value in delta_dict.items():
            if key not in {"chapter_id", "updated_at"}:
                current[key] = value
        if chapter_id_kw:
            current["chapter_id"] = chapter_id_kw
        current["status"] = "reviewed"
        current["updated_at"] = utc_now()
        return current

    cid = str(chapter_id)
    current = {
        "chapter_id": cid,
        "title": str(title),
        "status": "reviewed",
        "updated_at": utc_now(),
    }
    if isinstance(delta, dict):
        for key, value in delta.items():
            if key not in {"chapter_id", "updated_at"}:
                current[key] = value
    return current
