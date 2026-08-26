from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from translator.glossary.models import GlossaryCandidate
from translator.glossary.taxonomy import CATEGORY_VALUES
from translator.providers.registry import get_provider


RETRYABLE_EXTRACTION_ERROR = re.compile(r"(?:429|500|502|503|504|timeout|timed? out|temporar|connection|dns|network)", re.IGNORECASE)


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
    fallback_backends: Sequence[str] | None = None,
    max_attempts: int = 3,
    retry_backoff_seconds: float = 1.0,
) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    items = [item for item in payload.get("items", []) if isinstance(item, dict) and item.get("id")]
    expected_ids = {str(item["id"]) for item in items}
    chunks = chunk_paragraphs(items, chunk_size)
    backends = list(dict.fromkeys([backend, *(fallback_backends or [])]))
    attempts_limit = max(1, int(max_attempts))
    results: list[dict[str, Any]] = []
    completed_chunks: list[int] = []
    failed_chunks: list[dict[str, Any]] = []
    attempt_log: list[dict[str, Any]] = []
    checkpoint_path = output_path.with_suffix(".checkpoint.json")

    def persist_checkpoint(status: str, merged: Mapping[str, Any]) -> None:
        checkpoint = {
            "schema_version": "3.0",
            "status": status,
            "input": str(input_path),
            "output": str(output_path),
            "expected_ids": sorted(expected_ids),
            "completed_chunks": completed_chunks,
            "failed_chunks": failed_chunks,
            "attempts": attempt_log,
            "result": dict(merged),
        }
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def persist_output(merged: Mapping[str, Any]) -> None:
        artifact = {key: merged.get(key) for key in ("schema_version", "checked_ids", "candidates")}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for chunk_index, chunk in enumerate(chunks, start=1):
        if cancel_check:
            cancel_check()
        request = dict(payload)
        request["items"] = chunk
        schema_path = Path(__file__).resolve().parents[2] / "schemas" / "glossary-extract-output.schema.json"
        chunk_result: dict[str, Any] | None = None
        chunk_attempts: list[dict[str, Any]] = []
        for backend_name in backends:
            provider = None
            try:
                provider = provider_factory(backend_name) if provider_factory else get_provider(backend_name)
            except Exception as exc:
                chunk_attempts.append({"backend": backend_name, "attempt": 0, "status": "provider_init_failed", "error": str(exc)})
                continue
            for attempt in range(1, attempts_limit + 1):
                if cancel_check:
                    cancel_check()
                record: dict[str, Any] = {"backend": backend_name, "attempt": attempt, "chunk": chunk_index}
                try:
                    result = provider.review("glossary_extract", request, schema_path, timeout=300)
                    if not isinstance(result, dict):
                        raise ValueError("glossary extraction provider returned a non-object")
                    if str(result.get("status", "")).casefold() == "error":
                        http_status = result.get("http_status") or result.get("status_code") or ""
                        detail = result.get("error") or result.get("reason") or "provider returned status=error"
                        raise RuntimeError(f"{detail} http_status={http_status}")
                    chunk_result = result
                    record["status"] = "ok"
                    chunk_attempts.append(record)
                    break
                except Exception as exc:
                    retryable = bool(RETRYABLE_EXTRACTION_ERROR.search(str(exc)))
                    record.update({"status": "failed", "retryable": retryable, "error": str(exc)})
                    chunk_attempts.append(record)
                    if retryable and attempt < attempts_limit:
                        time.sleep(max(0.0, float(retry_backoff_seconds)) * (2 ** (attempt - 1)))
                        continue
                    break
            if chunk_result is not None:
                break

        attempt_log.extend(chunk_attempts)
        if chunk_result is None:
            failed_chunks.append({"chunk": chunk_index, "paragraph_ids": [str(item.get("id")) for item in chunk], "attempts": chunk_attempts})
        else:
            results.append(chunk_result)
            completed_chunks.append(chunk_index)
        merged_checkpoint = merge_extraction_results(results, expected_ids)
        persist_output(merged_checkpoint)
        persist_checkpoint("partial", merged_checkpoint)

    merged = merge_extraction_results(results, expected_ids)
    status = "completed" if not failed_chunks and not merged["missing_ids"] else "partial" if results else "failed"
    merged.update({
        "extraction_status": status,
        "completed_chunks": completed_chunks,
        "failed_chunks": failed_chunks,
        "attempts": attempt_log,
    })
    persist_output(merged)
    persist_checkpoint(status, merged)
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
