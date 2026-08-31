from __future__ import annotations

from pathlib import Path
import json

from app.models import Book, persist_book
from app.placeholders import extract_placeholders
from app.quality import quality_report
from app.terminology import Term, relevant_terms_for_text


def audit_coverage_report(book: Book, terms: list[Term]) -> dict:
    translated = [paragraph for paragraph in book.paragraphs if paragraph.translated.strip()]
    pending = [paragraph for paragraph in book.paragraphs if not paragraph.translated.strip()]
    term_sources = {term.source for term in terms if term.source}
    used_term_sources = {
        term.source
        for paragraph in book.paragraphs
        for term in relevant_terms_for_text(terms, paragraph.source)
    }
    return {
        "status": "ok" if not pending else "warning",
        "warnings": [],
        "summary": {
            "book": book.id,
            "total": len(book.paragraphs),
            "translated": len(translated),
            "pending": len(pending),
            "empty_translation": len(pending),
            "term_count": len(terms),
            "used_term_count": len(used_term_sources),
            "unused_term_count": len(term_sources - used_term_sources),
            "exportable_formats": ["txt", "epub"] if book.source_type == "epub" else ["txt"],
        },
        "details": {
            "pending_ids": [paragraph.id for paragraph in pending[:100]],
            "unused_terms": sorted(term_sources - used_term_sources)[:100],
        },
    }


def export_pending_translations(book: Book, terms: list[Term], output: Path) -> dict:
    records = [_paragraph_record(book, paragraph, terms) for paragraph in book.paragraphs if not paragraph.translated.strip()]
    return _write_records(output, records, book.id, "pending_translations")


def export_quality_fix(book: Book, terms: list[Term], quality_config, output: Path) -> dict:
    report = quality_report(book, quality_config, terms)
    reasons_by_id: dict[str, set[str]] = {}
    for paragraph_id in report["details"].get("untranslated", []):
        reasons_by_id.setdefault(paragraph_id, set()).add("untranslated")
    for item in report["details"].get("source_residual", []):
        reasons_by_id.setdefault(item["id"], set()).add("source_residual")
    for item in report["details"].get("terminology_mismatch", []):
        reasons_by_id.setdefault(item["id"], set()).add("terminology_mismatch")
    for item in report["details"].get("placeholder_mismatch", []):
        reasons_by_id.setdefault(item["id"], set()).add("placeholder_mismatch")
    by_id = {paragraph.id: paragraph for paragraph in book.paragraphs}
    records = []
    for paragraph_id, reasons in sorted(reasons_by_id.items()):
        paragraph = by_id.get(paragraph_id)
        if paragraph is None:
            continue
        record = _paragraph_record(book, paragraph, terms)
        record["reasons"] = sorted(reasons)
        records.append(record)
    return _write_records(output, records, book.id, "quality_fix")


def import_manual_translations(root_books_dir: Path, book: Book, input_path: Path) -> dict:
    raw = _load_json_or_lines(input_path)
    records = _normalize_records(raw)
    by_id = {paragraph.id: paragraph for paragraph in book.paragraphs}
    errors = []
    imported = 0
    skipped_empty = 0
    for record in records:
        paragraph_id = str(record.get("id", record.get("paragraph_id", ""))).strip()
        translated = str(record.get("translated", record.get("text", ""))).strip()
        paragraph = by_id.get(paragraph_id)
        if paragraph is None:
            errors.append(f"未知段落 ID：{paragraph_id}")
            continue
        if not translated:
            skipped_empty += 1
            continue
        paragraph.translated = translated
        imported += 1
    if errors:
        return {
            "status": "error",
            "errors": [{"code": "manual_translation_invalid", "message": message} for message in errors],
            "warnings": [],
            "summary": {"book": book.id, "input": str(input_path), "imported": 0},
            "details": {},
        }
    persist_book(root_books_dir, book)
    return {
        "status": "ok",
        "warnings": [],
        "summary": {
            "book": book.id,
            "input": str(input_path),
            "imported": imported,
            "skipped_empty": skipped_empty,
        },
        "details": {},
    }


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


def _paragraph_record(book: Book, paragraph, terms: list[Term]) -> dict:
    chapter_title = next((chapter.title for chapter in book.chapters if chapter.id == paragraph.chapter_id), "")
    return {
        "id": paragraph.id,
        "chapter_id": paragraph.chapter_id,
        "chapter_title": chapter_title,
        "source": paragraph.source,
        "translated": paragraph.translated,
        "terms": [
            {"source": term.source, "target": term.target, "category": term.category, "note": term.note}
            for term in relevant_terms_for_text(terms, paragraph.source)
        ],
        "placeholders": [item.value for item in extract_placeholders(paragraph.source)],
    }


def _write_records(output: Path, records: list[dict], book_id: str, kind: str) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"book": book_id, "kind": kind, "items": records}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "ok",
        "warnings": [],
        "summary": {"book": book_id, "output": str(output), "count": len(records)},
        "details": {},
    }


def _load_json_or_lines(path: Path):
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return [line.strip() for line in text.splitlines() if line.strip()]


def _normalize_records(raw) -> list[dict]:
    if isinstance(raw, dict):
        raw = raw.get("items", raw.get("translations", []))
    if not isinstance(raw, list):
        raise ValueError("输入必须是数组，或包含 items 数组的对象")
    result = []
    for item in raw:
        if isinstance(item, dict):
            result.append(item)
    return result


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
