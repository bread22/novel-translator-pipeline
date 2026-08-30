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


def test_cross_window_candidate_aggregation_and_chinese_categories(tmp_path: Path) -> None:
    from translator.glossary.taxonomy import canonical_category, category_tier, CategoryTier
    from translator.review.knowledge_extractor import aggregate_candidates, build_finalization_payload

    # 1. Test Chinese category aliases
    assert canonical_category("人物") == "person"
    assert category_tier("人物") == CategoryTier.DIRECT_ALLOWED
    assert canonical_category("地点") == "location"
    assert category_tier("地点") == CategoryTier.DIRECT_ALLOWED
    assert canonical_category("医疗器具") == "medical_device"
    assert category_tier("医疗器具") == CategoryTier.GATED_ALLOWED

    # 2. Test multi-window candidates aggregation
    candidates = [
        {
            "candidate_id": "c0003:window:0001:cand-01",
            "kind": "glossary",
            "source": "小泉宏美",
            "target": "小泉宏美",
            "category": "人物",
            "confidence": 0.95,
            "source_window": "c0003:window:0001",
            "source_paragraph_ids": ["c0003-p00002"],
            "evidence_ids": ["c0003-p00002"],
            "source_fragment": "新人看護婦の小泉宏美は",
            "target_fragment": "新护士小泉宏美",
        },
        {
            "candidate_id": "c0003:window:0004:cand-03",
            "kind": "glossary",
            "source": "小泉宏美",
            "target": "小泉宏美",
            "category": "person",
            "confidence": 0.90,
            "source_window": "c0003:window:0004",
            "source_paragraph_ids": ["c0003-p00108"],
            "evidence_ids": ["c0003-p00108"],
            "source_fragment": "小泉宏美は息を呑んだ",
            "target_fragment": "小泉宏美倒吸了一口凉气",
        },
        {
            "candidate_id": "c0003:window:0002:cand-02",
            "kind": "glossary",
            "source": "辻裕子",
            "target": "辻裕子",
            "category": "人物",
            "confidence": 0.95,
            "source_window": "c0003:window:0002",
            "source_paragraph_ids": ["c0003-p00068"],
            "evidence_ids": ["c0003-p00068"],
            "source_fragment": "先輩看護婦辻裕子",
            "target_fragment": "前辈护士辻裕子",
        },
    ]

    aggregated = aggregate_candidates(candidates)
    assert len(aggregated) == 2
    hiromi = next(c for c in aggregated if c["source"] == "小泉宏美")
    assert set(hiromi["source_paragraph_ids"]) == {"c0003-p00002", "c0003-p00108"}
    assert set(hiromi["evidence_ids"]) == {"c0003-p00002", "c0003-p00108"}
    assert len(hiromi["source_paragraph_ids"]) == 2
    assert "c0003:window:0001:cand-01" in hiromi["alias_candidate_ids"]
    assert "c0003:window:0004:cand-03" in hiromi["alias_candidate_ids"]
    assert hiromi["category"] == "person"

    # 3. Test build_finalization_payload uses aggregated candidates
    payload = build_finalization_payload(
        candidates, conflicts=[], glossary={"terms": []}, memory={"entries": []}
    )
    assert len(payload["candidates"]) == 2
    final_hiromi = next(c for c in payload["candidates"] if c["source"] == "小泉宏美")
    assert len(final_hiromi["source_paragraph_ids"]) == 2

    # 4. Test apply_knowledge_delta with decisions on aggregated candidates
    workspace = BookWorkspace.at(tmp_path / "output", "test-book-2")
    evidence_texts = {
        "c0003-p00002": "新人看護婦の小泉宏美は",
        "c0003-p00108": "小泉宏美は息を呑んだ",
        "c0003-p00068": "先輩看護婦辻裕子",
    }
    decisions = [
        {"candidate_id": hiromi["candidate_id"], "action": "active", "reason": "多段落复现主角名"},
        {"candidate_id": aggregated[1]["candidate_id"], "action": "candidate", "reason": "单次出现"},
    ]
    summary = apply_knowledge_delta(
        workspace, "c0003", candidates, decisions, evidence_texts=evidence_texts
    )
    assert summary["active"] == 1
    glossary_data = json.loads(workspace.glossary_path.read_text(encoding="utf-8"))
    assert len(glossary_data["terms"]) == 1
    assert glossary_data["terms"][0]["source"] == "小泉宏美"
    assert glossary_data["terms"][0]["category"] == "person"
    evidence_pids = {item["paragraph_id"] for item in glossary_data["terms"][0]["evidence"]}
    assert evidence_pids == {"c0003-p00002", "c0003-p00108"}
