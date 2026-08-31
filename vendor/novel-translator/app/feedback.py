from __future__ import annotations

from pathlib import Path
import json
import re

from app.models import Book


def verify_feedback_text(book: Book, input_path: Path) -> dict:
    queries = _load_feedback_queries(input_path)
    chapters = {chapter.id: chapter for chapter in book.chapters}
    matched_source = []
    matched_translation = []
    not_found = []
    for query in queries:
        source_matches = [
            _match_record(query, paragraph, chapters[paragraph.chapter_id].title, "source")
            for paragraph in book.paragraphs
            if _fuzzy_contains(paragraph.source, query)
        ]
        translation_matches = [
            _match_record(query, paragraph, chapters[paragraph.chapter_id].title, "translation")
            for paragraph in book.paragraphs
            if paragraph.translated and _fuzzy_contains(paragraph.translated, query)
        ]
        if source_matches:
            matched_source.extend(source_matches)
        if translation_matches:
            matched_translation.extend(translation_matches)
        if not source_matches and not translation_matches:
            not_found.append(query)
    status = "ok" if not not_found else "warning"
    return {
        "status": status,
        "warnings": [],
        "summary": {
            "book": book.id,
            "queries": len(queries),
            "matched_source": len(matched_source),
            "matched_translation": len(matched_translation),
            "not_found": len(not_found),
        },
        "details": {
            "matched_source": matched_source[:100],
            "matched_translation": matched_translation[:100],
            "not_found": not_found[:100],
        },
    }


def _load_feedback_queries(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return [line.strip() for line in text.splitlines() if line.strip()]
    if isinstance(raw, dict):
        raw = raw.get("items", raw.get("queries", []))
    if not isinstance(raw, list):
        raise ValueError("反馈输入必须是数组、包含 items/queries 数组的对象，或普通文本行")
    queries = []
    for item in raw:
        if isinstance(item, str):
            value = item.strip()
        elif isinstance(item, dict):
            value = str(item.get("text", item.get("query", ""))).strip()
        else:
            value = ""
        if value:
            queries.append(value)
    return queries


def _fuzzy_contains(text: str, query: str) -> bool:
    normalized_text = _normalize(text)
    normalized_query = _normalize(query)
    if not normalized_query:
        return False
    return normalized_query in normalized_text


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _match_record(query: str, paragraph, chapter_title: str, match_type: str) -> dict:
    return {
        "query": query,
        "match_type": match_type,
        "id": paragraph.id,
        "chapter_id": paragraph.chapter_id,
        "chapter_title": chapter_title,
        "source": paragraph.source,
        "translated": paragraph.translated,
    }

