from __future__ import annotations

from pathlib import Path
import json

from app.models import Book, persist_book
from app.placeholders import placeholder_mismatches


def apply_review_fixes(root_books_dir: Path, book: Book, input_path: Path) -> dict:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    items = raw.get("items", raw if isinstance(raw, list) else [])
    by_id = {paragraph.id: paragraph for paragraph in book.paragraphs}
    errors = []
    applied = 0
    for item in items:
        if not isinstance(item, dict) or not item.get("approved_translation"):
            continue
        paragraph_id = str(item.get("id", ""))
        translated = str(item.get("approved_translation", "")).strip()
        paragraph = by_id.get(paragraph_id)
        if paragraph is None:
            errors.append({"code": "review_fix_invalid", "message": f"未知段落 ID：{paragraph_id}"})
            continue
        if not translated:
            errors.append({"code": "review_fix_invalid", "message": f"{paragraph_id} 的 approved_translation 为空"})
            continue
        original = paragraph.translated
        paragraph.translated = translated
        missing = placeholder_mismatches(paragraph)
        if missing:
            paragraph.translated = original
            errors.append({"code": "review_fix_invalid", "message": f"{paragraph_id} 缺少占位符"})
            continue
        applied += 1
    if errors:
        return {"status": "error", "errors": errors, "warnings": [], "summary": {"book": book.id, "applied": 0}, "details": {}}
    if applied:
        persist_book(root_books_dir, book)
    return {"status": "ok", "warnings": [], "summary": {"book": book.id, "input": str(input_path), "applied": applied}, "details": {}}
