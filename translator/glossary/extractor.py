from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from translator.glossary.models import GlossaryCandidate
from translator.glossary.taxonomy import CATEGORY_VALUES
from translator.providers.registry import get_provider


def chunk_paragraphs(items: list[dict[str, Any]], max_chars: int = 6000) -> list[list[dict[str, Any]]]:
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    chars = 0
    for item in items:
        size = len(str(item.get("source", "")))
        if current and chars + size > max_chars:
            chunks.append(current)
            current, chars = [], 0
        current.append(item)
        chars += size
    if current:
        chunks.append(current)
    return chunks or [[]]


def merge_extraction_results(results: Iterable[Mapping[str, Any]], expected_ids: set[str]) -> dict[str, Any]:
    by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    checked: list[str] = []
    for result in results:
        for item_id in result.get("checked_ids", []) if isinstance(result.get("checked_ids", []), list) else []:
            item_id = str(item_id)
            if item_id in expected_ids and item_id not in checked:
                checked.append(item_id)
        candidates = result.get("candidates", result.get("items", []))
        for raw in candidates if isinstance(candidates, list) else []:
            if not isinstance(raw, dict):
                continue
            key = (str(raw.get("source", "")).strip(), str(raw.get("target", "")).strip(), str(raw.get("category", "")).strip())
            if not key[0] or not key[1]:
                continue
            existing = by_key.setdefault(key, {**raw, "evidence_ids": []})
            evidence = list(existing.get("evidence_ids", []) or []) + list(raw.get("evidence_ids", []) or [])
            existing["evidence_ids"] = list(dict.fromkeys(str(value) for value in evidence if str(value).strip()))
            existing["confidence"] = max(float(existing.get("confidence", 0) or 0), float(raw.get("confidence", 0) or 0))
    return {
        "schema_version": "3.0",
        "checked_ids": checked,
        "candidates": sorted(by_key.values(), key=lambda item: (str(item.get("source", "")), str(item.get("target", "")))),
        "missing_ids": sorted(expected_ids - set(checked)),
    }


def run_glossary_extraction(
    input_path: Path,
    output_path: Path,
    *,
    backend: str,
    chunk_size: int = 6000,
    provider_factory: Callable[[str], Any] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    items = [item for item in payload.get("items", []) if isinstance(item, dict) and item.get("id")]
    expected_ids = {str(item["id"]) for item in items}
    chunks = chunk_paragraphs(items, chunk_size)
    provider = provider_factory(backend) if provider_factory else get_provider(backend)
    results: list[dict[str, Any]] = []
    for chunk in chunks:
        if cancel_check:
            cancel_check()
        request = dict(payload)
        request["items"] = chunk
        schema_path = Path(__file__).resolve().parents[2] / "schemas" / "glossary-extract-output.schema.json"
        result = provider.review("glossary_extract", request, schema_path, timeout=300)
        results.append(result if isinstance(result, dict) else {})
    merged = merge_extraction_results(results, expected_ids)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return merged


def candidates_from_output(payload: Mapping[str, Any]) -> list[GlossaryCandidate]:
    result: list[GlossaryCandidate] = []
    for raw in payload.get("candidates", []) if isinstance(payload.get("candidates", []), list) else []:
        try:
            result.append(GlossaryCandidate.model_validate(raw))
        except Exception:
            continue
    return result


def extraction_prompt_categories() -> tuple[str, ...]:
    return CATEGORY_VALUES
