from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import json
import shutil

from app.models import Book, book_dir, load_book


def snapshots_dir(root_books_dir: Path, book_id: str) -> Path:
    return book_dir(root_books_dir, book_id) / "snapshots"


def new_snapshot_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]


def create_snapshot(root_books_dir: Path, book: Book, name: str) -> dict:
    snapshot_id = new_snapshot_id()
    directory = snapshots_dir(root_books_dir, book.id) / snapshot_id
    directory.mkdir(parents=True, exist_ok=True)
    manifest = book_dir(root_books_dir, book.id) / "manifest.json"
    target = directory / "manifest.json"
    shutil.copy2(manifest, target)
    meta = {
        "snapshot_id": snapshot_id,
        "book": book.id,
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest": str(target),
    }
    (directory / "snapshot.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "ok", "warnings": [], "summary": meta, "details": {}}


def list_snapshots(root_books_dir: Path, book_id: str) -> dict:
    items = []
    directory = snapshots_dir(root_books_dir, book_id)
    if directory.exists():
        for meta_path in sorted(directory.glob("*/snapshot.json")):
            try:
                items.append(json.loads(meta_path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                continue
    return {"status": "ok", "warnings": [], "summary": {"book": book_id, "count": len(items)}, "details": {"snapshots": items}}


def restore_snapshot(root_books_dir: Path, book_id: str, snapshot_id: str) -> dict:
    source = snapshots_dir(root_books_dir, book_id) / snapshot_id / "manifest.json"
    if not source.exists():
        raise FileNotFoundError(f"未找到快照：{snapshot_id}")
    target = book_dir(root_books_dir, book_id) / "manifest.json"
    shutil.copy2(source, target)
    book = load_book(root_books_dir, book_id)
    return {
        "status": "ok",
        "warnings": [],
        "summary": {"book": book.id, "snapshot": snapshot_id, "paragraphs": len(book.paragraphs)},
        "details": {},
    }
