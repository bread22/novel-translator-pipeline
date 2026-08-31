from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import hashlib
import json
from datetime import datetime, timezone

from app.models import book_dir
from app.terminology import Term


@dataclass
class MemoryEntry:
    source_hash: str
    source: str
    translated: str
    term_hash: str
    model: str
    quality_status: str
    created_at: str


def memory_path(root_books_dir: Path, book_id: str) -> Path:
    return book_dir(root_books_dir, book_id) / "memory.json"


def source_hash(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def terminology_hash(terms: list[Term]) -> str:
    payload = [
        {"source": term.source, "target": term.target}
        for term in sorted(terms, key=lambda item: item.source)
        if term.source and term.target
    ]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def load_memory(root_books_dir: Path, book_id: str) -> list[MemoryEntry]:
    path = memory_path(root_books_dir, book_id)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("items", raw if isinstance(raw, list) else [])
    return [
        MemoryEntry(
            source_hash=str(item.get("source_hash", "")),
            source=str(item.get("source", "")),
            translated=str(item.get("translated", "")),
            term_hash=str(item.get("term_hash", "")),
            model=str(item.get("model", "")),
            quality_status=str(item.get("quality_status", "")),
            created_at=str(item.get("created_at", "")),
        )
        for item in items
        if isinstance(item, dict)
    ]


def save_memory(root_books_dir: Path, book_id: str, entries: list[MemoryEntry]) -> Path:
    path = memory_path(root_books_dir, book_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    deduped = {}
    for entry in entries:
        if entry.source_hash and entry.term_hash and entry.translated:
            deduped[(entry.source_hash, entry.term_hash)] = entry
    payload = {"items": [asdict(entry) for entry in deduped.values()]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def lookup_memory(entries: list[MemoryEntry], source: str, term_hash: str) -> MemoryEntry | None:
    key = source_hash(source)
    for entry in entries:
        if entry.source_hash == key and entry.term_hash == term_hash and entry.translated:
            return entry
    return None


def remember_translation(entries: list[MemoryEntry], *, source: str, translated: str, term_hash: str, model: str, quality_status: str = "unchecked") -> None:
    entries.append(
        MemoryEntry(
            source_hash=source_hash(source),
            source=source,
            translated=translated,
            term_hash=term_hash,
            model=model,
            quality_status=quality_status,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    )


def memory_status(root_books_dir: Path, book_id: str, terms: list[Term]) -> dict:
    entries = load_memory(root_books_dir, book_id)
    current_term_hash = terminology_hash(terms)
    reusable = [entry for entry in entries if entry.term_hash == current_term_hash]
    return {
        "status": "ok",
        "warnings": [],
        "summary": {
            "book": book_id,
            "entries": len(entries),
            "reusable_entries": len(reusable),
            "current_term_hash": current_term_hash,
        },
        "details": {},
    }


def export_memory(root_books_dir: Path, book_id: str, output: Path) -> dict:
    entries = load_memory(root_books_dir, book_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"items": [asdict(entry) for entry in entries]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "warnings": [], "summary": {"book": book_id, "output": str(output), "count": len(entries)}, "details": {}}


def import_memory(root_books_dir: Path, book_id: str, input_path: Path) -> dict:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    items = raw.get("items", raw if isinstance(raw, list) else [])
    existing = load_memory(root_books_dir, book_id)
    imported = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source", ""))
        translated = str(item.get("translated", ""))
        term_hash = str(item.get("term_hash", ""))
        if not source or not translated or not term_hash:
            continue
        existing.append(
            MemoryEntry(
                source_hash=str(item.get("source_hash", source_hash(source))),
                source=source,
                translated=translated,
                term_hash=term_hash,
                model=str(item.get("model", "")),
                quality_status=str(item.get("quality_status", "imported")),
                created_at=str(item.get("created_at", datetime.now(timezone.utc).isoformat())),
            )
        )
        imported += 1
    save_memory(root_books_dir, book_id, existing)
    return {"status": "ok", "warnings": [], "summary": {"book": book_id, "input": str(input_path), "imported": imported}, "details": {}}

