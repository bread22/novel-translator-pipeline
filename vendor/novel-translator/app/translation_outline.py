from __future__ import annotations

import hashlib
import json
from collections import Counter, OrderedDict
from pathlib import Path
from typing import Any

from app.models import Book, Paragraph


TRANSLATION_OUTLINE_SCHEMA_VERSION = 1
DEFAULT_UNIT_CONSTRAINTS = [
    "preserve_meaning",
    "preserve_outer_punctuation_style",
    "preserve_line_break_count",
    "keep_placeholders",
]


def build_translation_outline_payload(
    *,
    book: Book,
    include_translated: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    selected = [
        paragraph
        for paragraph in book.paragraphs
        if paragraph.source.strip() and (include_translated or not paragraph.translated.strip())
    ]
    grouped: OrderedDict[str, list[Paragraph]] = OrderedDict()
    for paragraph in selected:
        grouped.setdefault(_source_key(paragraph), []).append(paragraph)

    units: list[dict[str, Any]] = []
    location_map: dict[str, list[str]] = {}
    duplicate_histogram: Counter[str] = Counter()
    chapters_by_id = {chapter.id: chapter for chapter in book.chapters}
    for index, (source_key, group) in enumerate(grouped.items(), start=1):
        if limit is not None and len(units) >= limit:
            break
        first = group[0]
        chapter = chapters_by_id.get(first.chapter_id)
        unit_id = f"u{index:06d}"
        original_lines = _paragraph_lines(first.source)
        duplicate_count = len(group)
        duplicate_histogram[str(duplicate_count)] += 1
        units.append(
            {
                "id": unit_id,
                "original_lines": original_lines,
                "chapter_id": first.chapter_id,
                "chapter_title": chapter.title if chapter else "",
                "chapter_index": chapter.index if chapter else 0,
                "paragraph_index": first.index,
                "duplicate_count": duplicate_count,
                "source_hash": _stable_source_hash(source_key),
                "constraints": DEFAULT_UNIT_CONSTRAINTS,
            }
        )
        location_map[unit_id] = [paragraph.id for paragraph in group]

    full_entry_bytes = len(json.dumps([_paragraph_record(book, item) for item in selected], ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    outline_bytes = len(json.dumps(units, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    location_map_bytes = len(json.dumps(location_map, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    exported_location_count = sum(len(ids) for ids in location_map.values())
    return {
        "schema_version": TRANSLATION_OUTLINE_SCHEMA_VERSION,
        "kind": "novel-translator.translation_outline",
        "book": book.id,
        "title": book.title,
        "usage": {
            "model_input": "Send only units or a limited slice of units to the model.",
            "reexpand": "Use location_map after validation to fan out each translated unit to duplicate paragraphs.",
            "safety": "This outline does not replace terminology, placeholder, quality, or EPUB validation gates.",
        },
        "summary": {
            "paragraph_count": len(book.paragraphs),
            "selected_paragraph_count": len(selected),
            "unique_unit_count": len(grouped),
            "exported_unit_count": len(units),
            "exported_location_count": exported_location_count,
            "deduplicated_paragraph_count": max(0, len(selected) - len(grouped)),
            "excluded_counts": {"already_translated": len(book.paragraphs) - len(selected)} if not include_translated else {},
            "full_selected_entry_bytes": full_entry_bytes,
            "outline_units_bytes": outline_bytes,
            "location_map_bytes": location_map_bytes,
            "outline_units_ratio": round(outline_bytes / full_entry_bytes, 6) if full_entry_bytes else 0,
            "outline_with_map_ratio": round((outline_bytes + location_map_bytes) / full_entry_bytes, 6) if full_entry_bytes else 0,
            "duplicate_count_histogram": dict(sorted(duplicate_histogram.items(), key=lambda item: int(item[0]))),
            "truncated_by_limit": limit is not None and len(grouped) > len(units),
        },
        "units": units,
        "location_map": location_map,
    }


def build_compact_translation_outline_payload(
    *,
    book: Book,
    include_translated: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    verbose_payload = build_translation_outline_payload(book=book, include_translated=include_translated, limit=limit)
    verbose_units = _json_array(verbose_payload.get("units"), "translation_outline.units")
    chapter_titles = _ordered_unique(_str(unit.get("chapter_title")) for unit in verbose_units if isinstance(unit, dict))
    chapter_ids = {value: index for index, value in enumerate(chapter_titles)}
    compact_units: list[dict[str, Any]] = []
    for raw_unit in verbose_units:
        unit = _json_object(raw_unit, "translation outline unit")
        compact_unit: dict[str, Any] = {
            "id": _str(unit.get("id")),
            "t": _str_list(unit.get("original_lines"), "original_lines"),
        }
        chapter_title = _str(unit.get("chapter_title"))
        if chapter_title:
            compact_unit["c"] = chapter_ids[chapter_title]
        duplicate_count = unit.get("duplicate_count")
        if isinstance(duplicate_count, int) and duplicate_count != 1:
            compact_unit["n"] = duplicate_count
        compact_units.append(compact_unit)

    summary = dict(_json_object(verbose_payload.get("summary"), "translation_outline.summary"))
    compact_model_input = {
        "constraints": DEFAULT_UNIT_CONSTRAINTS,
        "dictionaries": {"c": chapter_titles},
        "units": compact_units,
    }
    compact_units_bytes = len(json.dumps(compact_units, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    compact_model_input_bytes = len(json.dumps(compact_model_input, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    full_entry_bytes = summary.get("full_selected_entry_bytes") if isinstance(summary.get("full_selected_entry_bytes"), int) else 0
    location_map_bytes = summary.get("location_map_bytes") if isinstance(summary.get("location_map_bytes"), int) else 0
    summary.update(
        {
            "format": "compact",
            "compact_units_bytes": compact_units_bytes,
            "compact_model_input_bytes": compact_model_input_bytes,
            "compact_model_input_ratio": round(compact_model_input_bytes / full_entry_bytes, 6) if full_entry_bytes else 0,
            "compact_with_map_ratio": round((compact_model_input_bytes + location_map_bytes) / full_entry_bytes, 6) if full_entry_bytes else 0,
        }
    )
    return {
        "schema_version": TRANSLATION_OUTLINE_SCHEMA_VERSION,
        "kind": "novel-translator.translation_outline.compact",
        "book": book.id,
        "title": book.title,
        "usage": {
            "model_input": "Send constraints, dictionaries and units to the model; keep location_map local.",
            "unit_fields": "id=unit id, t=original lines, c=chapter title id, n=duplicate count.",
            "reexpand": "Use location_map after validation to fan out each translated unit to duplicate paragraphs.",
            "safety": "This outline does not replace terminology, placeholder, quality, or EPUB validation gates.",
        },
        "constraints": DEFAULT_UNIT_CONSTRAINTS,
        "dictionaries": {"c": chapter_titles},
        "summary": summary,
        "units": compact_units,
        "location_map": verbose_payload["location_map"],
    }


def write_translation_outline_payload(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_manual_translation_payload_from_outline_result(
    *,
    outline_payload: dict[str, Any],
    result_payload: Any,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    units_by_id = _outline_units_by_id(outline_payload)
    location_map = _json_object(outline_payload.get("location_map"), "translation_outline.location_map")
    result_entries = _normalize_outline_result_entries(result_payload)
    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    converted_unit_count = 0
    converted_location_count = 0
    line_count_mismatch_count = 0

    for entry in result_entries:
        unit_id = _str(entry.get("id")).strip()
        if not unit_id:
            errors.append({"code": "missing_unit_id", "message": "translation result entry missing id", "entry": entry})
            continue
        if unit_id in seen_ids:
            errors.append({"code": "duplicate_unit_id", "message": f"duplicate translation result id: {unit_id}"})
            continue
        seen_ids.add(unit_id)
        unit = units_by_id.get(unit_id)
        if unit is None:
            errors.append({"code": "unknown_unit_id", "message": f"translation result id not found in outline: {unit_id}"})
            continue
        try:
            original_lines = _unit_original_lines(unit)
            translated_lines = _result_translation_lines(entry)
            paragraph_ids = _str_list(location_map.get(unit_id), f"location_map.{unit_id}")
        except Exception as error:
            errors.append({"code": "invalid_unit_translation", "message": f"{unit_id}: {type(error).__name__}: {error}"})
            continue
        if len(original_lines) != len(translated_lines):
            line_count_mismatch_count += 1
            errors.append(
                {
                    "code": "line_count_mismatch",
                    "message": f"{unit_id}: translation line count {len(translated_lines)} does not match original line count {len(original_lines)}",
                }
            )
            continue
        for paragraph_id in paragraph_ids:
            items.append(
                {
                    "id": paragraph_id,
                    "outline_unit_id": unit_id,
                    "source": "\n".join(original_lines),
                    "translated": "\n".join(translated_lines),
                }
            )
            converted_location_count += 1
        converted_unit_count += 1

    manual_payload = {
        "book": _str(outline_payload.get("book")),
        "kind": "outline_manual_translations",
        "items": items,
    }
    summary = {
        "result_entry_count": len(result_entries),
        "converted_unit_count": converted_unit_count,
        "converted_location_count": converted_location_count,
        "manual_entry_count": len(items),
        "error_count": len(errors),
        "line_count_mismatch_count": line_count_mismatch_count,
    }
    return manual_payload, summary, errors


def write_manual_translation_payload(payload: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _paragraph_record(book: Book, paragraph: Paragraph) -> dict[str, Any]:
    chapter = next((item for item in book.chapters if item.id == paragraph.chapter_id), None)
    return {
        "id": paragraph.id,
        "chapter_id": paragraph.chapter_id,
        "chapter_title": chapter.title if chapter else "",
        "source": paragraph.source,
        "translated": bool(paragraph.translated.strip()),
    }


def _paragraph_lines(value: str) -> list[str]:
    lines = value.splitlines()
    return lines if lines else [value]


def _source_key(paragraph: Paragraph) -> str:
    return json.dumps(_paragraph_lines(paragraph.source), ensure_ascii=False, separators=(",", ":"))


def _stable_source_hash(source_key: str) -> str:
    return hashlib.sha1(source_key.encode("utf-8")).hexdigest()[:16]


def _ordered_unique(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _outline_units_by_id(outline_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    units = _json_array(outline_payload.get("units"), "translation_outline.units")
    result: dict[str, dict[str, Any]] = {}
    for raw_unit in units:
        unit = _json_object(raw_unit, "translation outline unit")
        unit_id = _str(unit.get("id")).strip()
        if unit_id:
            result[unit_id] = unit
    return result


def _normalize_outline_result_entries(result_payload: Any) -> list[dict[str, Any]]:
    if isinstance(result_payload, list):
        return [_json_object(item, "translation result entry") for item in result_payload]
    payload = _json_object(result_payload, "translation result")
    raw_translations = payload.get("translations")
    if isinstance(raw_translations, list):
        return [_json_object(item, "translation result entry") for item in raw_translations]
    entries: list[dict[str, Any]] = []
    for unit_id, value in payload.items():
        if unit_id in {"schema_version", "kind", "book", "title", "summary"}:
            continue
        if isinstance(value, list):
            entries.append({"id": str(unit_id), "translated_lines": value})
        elif isinstance(value, dict):
            entry = dict(value)
            entry.setdefault("id", str(unit_id))
            entries.append(entry)
    if entries:
        return entries
    raise TypeError("translation result must be a list, an object with translations, or an id-to-lines mapping")


def _unit_original_lines(unit: dict[str, Any]) -> list[str]:
    if "original_lines" in unit:
        return _str_list(unit.get("original_lines"), "unit.original_lines")
    return _str_list(unit.get("t"), "unit.t")


def _result_translation_lines(entry: dict[str, Any]) -> list[str]:
    for key in ("translated_lines", "translation_lines", "t"):
        if key in entry:
            return _str_list(entry.get(key), f"result.{key}")
    raise TypeError("translation result entry must contain translated_lines, translation_lines, or t")


def _json_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be a JSON object")
    return value


def _json_array(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a JSON array")
    return value


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _str_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise TypeError(f"{name} must be a string array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError(f"{name} must be a string array")
        result.append(item)
    return result
