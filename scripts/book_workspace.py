from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import zipfile
from typing import Any, Iterable


INVALID_DIRECTORY_CHARS = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_book_name(value: str) -> str:
    name = INVALID_DIRECTORY_CHARS.sub("_", value).strip().strip(".")
    if not name or name in {".", ".."}:
        raise ValueError("书籍目录名为空或无效")
    return name


def write_json(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def safely_extract_epub(epub_path: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    with zipfile.ZipFile(epub_path) as archive:
        for member in archive.infolist():
            destination = (target / member.filename).resolve()
            if destination != root and root not in destination.parents:
                raise ValueError(f"EPUB 包含越界路径：{member.filename}")
        archive.extractall(target)


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
    def progress_path(self) -> Path:
        return self.data_dir / "progress.json"

    @property
    def reviews_dir(self) -> Path:
        return self.root / "reviews"

    @property
    def snapshots_dir(self) -> Path:
        return self.root / "snapshots"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    def initialize(self, source_epub: Path | None = None, *, book_id: str = "") -> None:
        for directory in (
            self.input_dir,
            self.data_dir,
            self.reviews_dir,
            self.snapshots_dir,
            self.reports_dir,
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
            write_json(self.glossary_path, {"book": book_id, "updated_at": utc_now(), "terms": [], "conflicts": []})
        if not self.progress_path.exists():
            write_json(
                self.progress_path,
                {"book": book_id, "state": "initialized", "completed_cycles": 0, "last_chunk": "", "updated_at": utc_now()},
            )


def merge_term_updates(
    glossary: dict[str, Any],
    updates: Iterable[dict[str, Any]],
    *,
    chunk_id: str,
    threshold: float = 0.9,
) -> tuple[dict[str, Any], dict[str, int]]:
    terms = [dict(item) for item in glossary.get("terms", []) if isinstance(item, dict)]
    conflicts = [dict(item) for item in glossary.get("conflicts", []) if isinstance(item, dict)]
    by_source = {str(item.get("source", "")).strip(): item for item in terms if str(item.get("source", "")).strip()}
    added = 0
    confirmed = 0
    rejected = 0
    conflicted = 0
    for raw in updates:
        source = str(raw.get("source", "")).strip()
        target = str(raw.get("target", "")).strip()
        confidence = float(raw.get("confidence", 0) or 0)
        if not source or not target or confidence < threshold:
            rejected += 1
            continue
        existing = by_source.get(source)
        if existing is not None and str(existing.get("target", "")).strip() != target:
            conflicts.append(
                {
                    "source": source,
                    "existing_target": existing.get("target", ""),
                    "proposed_target": target,
                    "confidence": confidence,
                    "chunk_id": chunk_id,
                    "created_at": utc_now(),
                }
            )
            conflicted += 1
            continue
        if existing is None:
            existing = {
                "source": source,
                "target": target,
                "category": str(raw.get("category", "other")).strip() or "other",
                "note": str(raw.get("note", "")).strip(),
                "occurrences": 0,
                "sample_ids": [],
                "confidence": confidence,
                "first_seen_chunk": chunk_id,
                "last_seen_chunk": chunk_id,
            }
            terms.append(existing)
            by_source[source] = existing
            added += 1
        else:
            existing["confidence"] = max(float(existing.get("confidence", 0) or 0), confidence)
            existing["last_seen_chunk"] = chunk_id
            if raw.get("note"):
                existing["note"] = str(raw["note"]).strip()
            confirmed += 1
    glossary = dict(glossary)
    glossary["terms"] = sorted(terms, key=lambda item: str(item.get("source", "")))
    glossary["conflicts"] = conflicts
    glossary["updated_at"] = utc_now()
    return glossary, {"added": added, "confirmed": confirmed, "rejected": rejected, "conflicted": conflicted}


def novel_translator_terms(glossary: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    allowed = {"source", "target", "category", "note", "occurrences", "sample_ids"}
    return {
        "terms": [
            {key: value for key, value in item.items() if key in allowed}
            for item in glossary.get("terms", [])
            if isinstance(item, dict) and str(item.get("source", "")).strip() and str(item.get("target", "")).strip()
        ]
    }
