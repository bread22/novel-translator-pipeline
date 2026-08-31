from __future__ import annotations

from pathlib import Path

from app.book_io import load_txt_book
from app.manual import import_manual_translations
from app.models import save_book
from app.translation_outline import (
    build_compact_translation_outline_payload,
    build_manual_translation_payload_from_outline_result,
    build_translation_outline_payload,
)


def _book(tmp_path: Path):
    source = tmp_path / "novel.txt"
    source.write_text("同じ文。\n\n別の文。\n\n同じ文。", encoding="utf-8")
    book = load_txt_book(source, title="Outline")
    book.paragraphs[1].translated = "已翻译。"
    return book


def test_translation_outline_deduplicates_pending_paragraphs(tmp_path: Path) -> None:
    book = _book(tmp_path)

    payload = build_translation_outline_payload(book=book)

    assert payload["kind"] == "novel-translator.translation_outline"
    assert payload["summary"]["selected_paragraph_count"] == 2
    assert payload["summary"]["unique_unit_count"] == 1
    assert payload["summary"]["deduplicated_paragraph_count"] == 1
    assert payload["summary"]["excluded_counts"] == {"already_translated": 1}
    assert payload["units"][0]["original_lines"] == ["同じ文。"]
    assert payload["units"][0]["duplicate_count"] == 2
    assert payload["location_map"]["u000001"] == [book.paragraphs[0].id, book.paragraphs[2].id]


def test_compact_translation_outline_includes_chapter_dictionary(tmp_path: Path) -> None:
    book = _book(tmp_path)

    payload = build_compact_translation_outline_payload(book=book)

    assert payload["kind"] == "novel-translator.translation_outline.compact"
    assert payload["constraints"]
    assert payload["dictionaries"]["c"] == ["Outline"]
    assert payload["units"] == [{"id": "u000001", "t": ["同じ文。"], "c": 0, "n": 2}]
    assert payload["summary"]["format"] == "compact"
    assert payload["summary"]["compact_model_input_bytes"] > 0


def test_outline_result_conversion_expands_to_manual_import_payload(tmp_path: Path) -> None:
    books_dir = tmp_path / "books"
    book = _book(tmp_path)
    save_book(books_dir, book, tmp_path / "novel.txt")
    outline = build_compact_translation_outline_payload(book=book)
    result = {"translations": [{"id": "u000001", "translated_lines": ["相同的句子。"]}]}

    payload, summary, errors = build_manual_translation_payload_from_outline_result(
        outline_payload=outline,
        result_payload=result,
    )

    assert errors == []
    assert summary["converted_unit_count"] == 1
    assert summary["converted_location_count"] == 2
    assert [item["id"] for item in payload["items"]] == [book.paragraphs[0].id, book.paragraphs[2].id]
    assert payload["items"][0]["translated"] == "相同的句子。"
    imported = import_manual_translations(books_dir, book, _write_json(tmp_path / "manual.json", payload))
    assert imported["status"] == "ok"
    assert imported["summary"]["imported"] == 2


def test_outline_result_conversion_reports_unknown_id(tmp_path: Path) -> None:
    outline = build_compact_translation_outline_payload(book=_book(tmp_path))

    payload, summary, errors = build_manual_translation_payload_from_outline_result(
        outline_payload=outline,
        result_payload={"u999999": ["未知。"]},
    )

    assert payload["items"] == []
    assert summary["error_count"] == 1
    assert errors[0]["code"] == "unknown_unit_id"


def test_outline_result_conversion_reports_duplicate_id(tmp_path: Path) -> None:
    outline = build_compact_translation_outline_payload(book=_book(tmp_path))

    payload, summary, errors = build_manual_translation_payload_from_outline_result(
        outline_payload=outline,
        result_payload={
            "translations": [
                {"id": "u000001", "translated_lines": ["相同的句子。"]},
                {"id": "u000001", "translated_lines": ["重复。"]},
            ]
        },
    )

    assert len(payload["items"]) == 2
    assert summary["error_count"] == 1
    assert errors[0]["code"] == "duplicate_unit_id"


def test_outline_result_conversion_rejects_line_count_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "multi.txt"
    source.write_text("一行目\n二行目", encoding="utf-8")
    book = load_txt_book(source, title="Multi")
    outline = build_compact_translation_outline_payload(book=book)

    payload, summary, errors = build_manual_translation_payload_from_outline_result(
        outline_payload=outline,
        result_payload={"translations": [{"id": "u000001", "translated_lines": ["只有一行"]}]},
    )

    assert payload["items"] == []
    assert summary["line_count_mismatch_count"] == 1
    assert errors[0]["code"] == "line_count_mismatch"


def _write_json(path: Path, payload: dict) -> Path:
    import json

    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path
