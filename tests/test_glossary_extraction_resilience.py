from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from translator.core.workspace import BookWorkspace, write_json
from translator.review.knowledge_extractor import (
    apply_knowledge_delta,
    run_knowledge_extractor_window,
    run_knowledge_finalization,
)


class MockExtractorProvider:
    def __init__(self, *, fail_count: int = 0) -> None:
        self.fail_count = fail_count
        self.calls = 0

    def review(self, kind: str, payload: dict[str, Any], _schema_path: Path, *, timeout: int = 300, autonomous: bool = False) -> dict[str, Any]:
        del timeout, autonomous
        self.calls += 1
        if self.calls <= self.fail_count:
            raise RuntimeError("HTTP 503 Service Temporarily Overloaded")
        if kind == "knowledge_window":
            items = payload.get("items", [])
            first_id = items[0]["id"] if items else "p1"
            first_src = items[0].get("source", "") if items else "東都銀行"
            first_tgt = items[0].get("translated", "") if items else "东都银行"
            return {
                "schema_version": "1.0",
                "rolling_context_delta": {"locations": [first_src]},
                "knowledge_candidates": [
                    {
                        "candidate_id": "cand-001",
                        "kind": "glossary",
                        "source": first_src,
                        "target": first_tgt,
                        "category": "location",
                        "confidence": 0.95,
                        "source_window": str(payload.get("window_id", "w1")),
                        "source_paragraph_ids": [first_id],
                        "evidence_ids": [first_id],
                        "source_fragment": first_src,
                        "target_fragment": first_tgt,
                    }
                ],
                "conflicts": [],
            }
        elif kind == "knowledge_finalize":
            candidates = payload.get("candidates", [])
            decisions = [
                {
                    "candidate_id": str(c.get("candidate_id", "")),
                    "action": "active",
                    "reason": "通过测试验证",
                    "conflict_id": "",
                }
                for c in candidates
            ]
            return {"schema_version": "1.0", "decisions": decisions}
        return {}


def test_knowledge_extractor_window_and_persistence(tmp_path: Path) -> None:
    workspace = BookWorkspace.at(tmp_path / "output", "test-book")
    output_path = tmp_path / "window_knowledge.json"
    provider = MockExtractorProvider()

    payload = {
        "schema_version": "1.0",
        "window_id": "c0001:window:0001",
        "chapter_id": "c0001",
        "window_index": 1,
        "total_windows": 1,
        "items": [{"id": "p1", "source": "東都銀行", "translated": "东都银行"}],
        "context_before": [],
        "context_after": [],
        "current_chapter_review_context": {},
    }

    config = {
        "knowledge_extractor": {
            "enabled": True,
            "provider": "mock",
        }
    }

    result = run_knowledge_extractor_window(
        payload,
        output_path=output_path,
        provider_factory=lambda _name: provider,
        config=config,
    )

    assert result["status"] == "completed"
    assert len(result["knowledge_candidates"]) == 1
    assert result["knowledge_candidates"][0]["source"] == "東都銀行"
    assert result["knowledge_candidates"][0]["target"] == "东都银行"
    assert output_path.exists()

    # Finalization
    final_payload = {
        "schema_version": "1.0",
        "candidates": result["knowledge_candidates"],
        "conflicts": [],
        "active_glossary": [],
        "related_memory": [],
    }
    final_result = run_knowledge_finalization(
        final_payload,
        provider_factory=lambda _name: provider,
        config=config,
    )
    assert final_result["status"] == "completed"
    assert len(final_result["decisions"]) == 1
    assert final_result["decisions"][0]["action"] == "active"

    # Persistence
    summary = apply_knowledge_delta(
        workspace,
        "c0001",
        result["knowledge_candidates"],
        final_result["decisions"],
        evidence_texts={"p1": "東都銀行"},
    )
    assert summary["active"] == 1
    assert workspace.glossary_path.exists()
    glossary = json.loads(workspace.glossary_path.read_text(encoding="utf-8"))
    assert len(glossary["terms"]) == 1
    assert glossary["terms"][0]["source"] == "東都銀行"
    assert glossary["terms"][0]["target"] == "东都银行"
