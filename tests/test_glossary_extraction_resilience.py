from __future__ import annotations

import json
from pathlib import Path

from translator.glossary.extractor import run_glossary_extraction
from translator.core.workspace import write_json


class RetryingProvider:
    def __init__(self, *, fail_count: int = 0) -> None:
        self.fail_count = fail_count
        self.calls = 0

    def review(self, _kind: str, payload: dict, _schema_path: Path, *, timeout: int) -> dict:
        del timeout
        self.calls += 1
        if self.calls <= self.fail_count:
            raise RuntimeError("HTTP 503 Service Temporarily Overloaded")
        ids = [str(item["id"]) for item in payload["items"]]
        source = str(payload["items"][0]["source"])
        return {
            "schema_version": "3.0",
            "checked_ids": ids,
            "candidates": [{
                "source": source,
                "target": source,
                "category": "person",
                "confidence": 0.96,
                "evidence_ids": [ids[0]],
            }],
        }


def test_extraction_retries_and_keeps_schema_clean_output(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    write_json(input_path, {"schema_version": "3.0", "items": [{"id": "p1", "source": "人物出现"}]})
    provider = RetryingProvider(fail_count=1)

    result = run_glossary_extraction(
        input_path,
        output_path,
        backend="primary",
        provider_factory=lambda _name: provider,
        max_attempts=2,
        retry_backoff_seconds=0,
    )

    assert result["extraction_status"] == "completed"
    assert result["completed_chunks"] == [1]
    assert len(result["attempts"]) == 2
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "schema_version": "3.0",
        "checked_ids": ["p1"],
        "candidates": [{
            "source": "人物出现",
            "target": "人物出现",
            "category": "person",
            "confidence": 0.96,
            "evidence_ids": ["p1"],
        }],
    }
    assert (tmp_path / "output.checkpoint.json").exists()


def test_extraction_fallback_preserves_successful_chunks(tmp_path: Path) -> None:
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    write_json(input_path, {
        "schema_version": "3.0",
        "items": [{"id": "p1", "source": "人物出现"}, {"id": "p2", "source": "地点出现"}],
    })
    primary = RetryingProvider(fail_count=99)
    fallback = RetryingProvider()
    providers = {"primary": primary, "fallback": fallback}

    result = run_glossary_extraction(
        input_path,
        output_path,
        backend="primary",
        fallback_backends=["fallback"],
        provider_factory=lambda name: providers[name],
        chunk_size=3,
        max_attempts=1,
        retry_backoff_seconds=0,
    )

    assert result["extraction_status"] == "completed"
    assert result["completed_chunks"] == [1, 2]
    assert result["failed_chunks"] == []
    assert len(result["candidates"]) == 2
