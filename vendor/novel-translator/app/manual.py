from __future__ import annotations

from pathlib import Path
import json

from app.models import Book, persist_book


def reset_translations(root_books_dir: Path, book: Book, *, input_path: Path | None = None, reset_all: bool = False) -> dict:
    if reset_all and input_path is not None:
        raise ValueError("--input 和 --all 不能同时使用")
    if reset_all:
        targets = {paragraph.id for paragraph in book.paragraphs}
    elif input_path is not None:
        raw = _load_json_or_lines(input_path)
        targets = set(_ids_from_reset_input(raw))
    else:
        raise ValueError("必须传入 --input 或 --all")
    by_id = {paragraph.id: paragraph for paragraph in book.paragraphs}
    missing = sorted(target for target in targets if target not in by_id)
    if missing:
        return {
            "status": "error",
            "errors": [{"code": "reset_target_invalid", "message": f"未知段落 ID：{item}"} for item in missing],
            "warnings": [],
            "summary": {"book": book.id, "reset": 0},
            "details": {},
        }
    reset_count = 0
    for paragraph_id in targets:
        paragraph = by_id[paragraph_id]
        if paragraph.translated:
            paragraph.translated = ""
            reset_count += 1
    persist_book(root_books_dir, book)
    return {
        "status": "ok",
        "warnings": [],
        "summary": {"book": book.id, "mode": "all" if reset_all else "input", "reset": reset_count},
        "details": {"ids": sorted(targets)[:100]},
    }


def _load_json_or_lines(path: Path):
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return [line.strip() for line in text.splitlines() if line.strip()]


def _ids_from_reset_input(raw) -> list[str]:
    if isinstance(raw, dict):
        raw = raw.get("items", raw.get("ids", []))
    if not isinstance(raw, list):
        raise ValueError("重置输入必须是数组，或包含 items/ids 数组的对象")
    ids = []
    for item in raw:
        if isinstance(item, str):
            ids.append(item.strip())
        elif isinstance(item, dict):
            ids.append(str(item.get("id", item.get("paragraph_id", ""))).strip())
    return [item for item in ids if item]
