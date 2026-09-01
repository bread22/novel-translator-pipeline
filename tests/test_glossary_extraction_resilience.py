from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from translator.core.workspace import BookWorkspace, write_json
from translator.review.knowledge_extractor import (
    aggregate_candidates,
    apply_knowledge_delta,
    compact_finalization_payload,
    partition_finalization_candidates,
    run_knowledge_extractor_window,
    run_knowledge_finalization,
    knowledge_extractor_connection_test,
    validate_finalization_coverage,
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


def test_knowledge_extractor_window_uses_ordered_fallback(tmp_path: Path) -> None:
    payload = {
        "window_id": "c0001:window:0001",
        "items": [{"id": "p1", "source": "東都銀行", "translated": "东都银行"}],
    }
    calls: list[str] = []

    class FailingPrimary:
        def review(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("primary")
            raise RuntimeError("primary unavailable")

    class WorkingFallback:
        def review(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
            calls.append("fallback")
            return {"rolling_context_delta": {}, "knowledge_candidates": [], "conflicts": []}

    providers = {"primary": FailingPrimary(), "fallback": WorkingFallback()}
    result = run_knowledge_extractor_window(
        payload,
        provider_factory=lambda name: providers[name],
        config={
            "knowledge_extractor": {
                "enabled": True,
                "provider": "primary",
                "fallback_providers": ["fallback"],
            }
        },
    )

    assert calls == ["primary", "fallback"]
    assert result["status"] == "completed"
    assert result["provider"] == "fallback"
    assert result["is_fallback"] is True
    assert result["fallback_from"] == "primary"
    assert result["fallback_index"] == 1
    assert result["provider_attempts"][0]["status"] == "error"

    final_result = run_knowledge_finalization(
        {"candidates": [], "conflicts": [], "active_glossary": [], "related_memory": []},
        provider_factory=lambda name: providers[name],
        config={
            "knowledge_extractor": {
                "enabled": True,
                "provider": "primary",
                "fallback_providers": ["fallback"],
            }
        },
    )
    assert final_result["status"] == "completed"
    assert final_result["provider"] == "fallback"
    assert final_result["fallback_from"] == "primary"


def test_knowledge_extractor_connection_test_uses_fallback(monkeypatch) -> None:
    calls: list[str] = []

    class HealthProvider:
        def __init__(self, name: str) -> None:
            self.name = name

        def health_check(self, timeout: int) -> dict[str, Any]:
            del timeout
            calls.append(self.name)
            return {"status": "error", "error": "offline"} if self.name == "primary" else {"status": "ok"}

    monkeypatch.setattr(
        "translator.review.knowledge_extractor._provider",
        lambda name, _config, _settings: HealthProvider(name),
    )
    result = knowledge_extractor_connection_test({
        "knowledge_extractor": {
            "provider": "primary",
            "fallback_providers": ["fallback"],
            "request_timeout": 5,
        }
    })

    assert calls == ["primary", "fallback"]
    assert result["status"] == "ok"
    assert result["provider"] == "fallback"
    assert result["is_fallback"] is True
    assert result["fallback_from"] == "primary"


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


def test_finalization_coverage_rejects_empty_partial_unknown_and_duplicate() -> None:
    candidates = [
        {"candidate_id": "c1", "alias_candidate_ids": ["alias-1"]},
        {"candidate_id": "c2", "alias_candidate_ids": []},
    ]
    empty = validate_finalization_coverage(candidates, [])
    assert empty["complete"] is False
    assert empty["missing_candidate_ids"] == ["c1", "c2"]

    partial = validate_finalization_coverage(
        candidates, [{"candidate_id": "alias-1", "action": "candidate"}]
    )
    assert partial["decisions"][0]["candidate_id"] == "c1"
    assert partial["missing_candidate_ids"] == ["c2"]

    invalid = validate_finalization_coverage(candidates, [
        {"candidate_id": "c1", "action": "active"},
        {"candidate_id": "c1", "action": "candidate"},
        {"candidate_id": "invented", "action": "discard"},
    ])
    assert invalid["decisions"] == []
    assert invalid["duplicate_candidate_ids"] == ["c1"]
    assert invalid["unknown_candidate_ids"] == ["invented"]
    assert invalid["missing_candidate_ids"] == ["c1", "c2"]


def test_deterministic_prefilter_and_compact_payload() -> None:
    singleton = {
        "candidate_id": "single", "kind": "glossary", "source": "裕子", "target": "裕子",
        "category": "person", "source_scope": "body", "confidence": 0.95,
        "source_window": "c1:window:1", "evidence_ids": ["c1-p1"],
    }
    repeated = {
        "candidate_id": "repeat", "kind": "glossary", "source": "東都銀行", "target": "东都银行",
        "category": "organization", "source_scope": "body", "confidence": 0.96,
        "source_window": "c1:window:1, c1:window:2", "evidence_ids": ["c1-p2", "c1-p3"],
        "source_fragment": "unused", "target_fragment": "unused", "note": "unused",
        "referenced_glossary_ids": [], "referenced_memory_keys": [],
    }
    blocked = {
        "candidate_id": "blocked", "kind": "glossary", "source": "白皙", "target": "白皙",
        "category": "adjective", "source_scope": "body", "confidence": 0.99,
        "source_window": "c1:window:1", "evidence_ids": ["c1-p4", "c1-p5"],
    }
    known = {
        "candidate_id": "known", "kind": "glossary", "source": "宏美", "target": "宏美",
        "category": "person", "source_scope": "body", "confidence": 0.95,
        "source_window": "c2:window:1", "evidence_ids": ["c2-p1"],
    }
    deterministic, model_candidates = partition_finalization_candidates(
        [singleton, repeated, blocked, known], [], {"terms": [{
            "source": "宏美", "target": "宏美", "category": "person", "status": "active",
        }]}, {"entries": []},
    )
    assert {item["candidate_id"]: item["action"] for item in deterministic} == {
        "single": "candidate", "blocked": "discard", "known": "active",
    }
    assert [item["candidate_id"] for item in model_candidates] == ["repeat"]

    payload, id_map = compact_finalization_payload({
        "chapter_id": "c1",
        "candidates": model_candidates,
        "conflicts": [],
        "active_glossary": [],
        "related_memory": [],
    })
    assert id_map == {"k1": "repeat"}
    assert payload["candidates"] == [{
        "candidate_id": "k1",
        "kind": "glossary",
        "category": "organization",
        "confidence": 0.96,
        "evidence_count": 2,
        "chapter_count": 1,
        "source": "東都銀行",
        "target": "东都银行",
    }]


def test_candidate_and_active_stores_are_idempotent(tmp_path: Path) -> None:
    workspace = BookWorkspace.at(tmp_path / "output", "idempotent-book")

    def candidate(candidate_id: str, paragraph_id: str) -> dict[str, Any]:
        return {
            "candidate_id": candidate_id,
            "kind": "glossary",
            "source": "小泉宏美",
            "target": "小泉宏美",
            "category": "person",
            "source_scope": "body",
            "confidence": 0.96,
            "source_window": f"window:{paragraph_id}",
            "source_paragraph_ids": [paragraph_id],
            "evidence_ids": [paragraph_id],
            "source_fragment": "小泉宏美",
            "target_fragment": "小泉宏美",
        }

    first = candidate("cand-1", "p1")
    for _ in range(2):
        apply_knowledge_delta(
            workspace,
            "c1",
            [first],
            [{"candidate_id": "cand-1", "action": "candidate"}],
            evidence_texts={"p1": "小泉宏美が来た"},
        )
    pending = json.loads(workspace.knowledge_candidates_path.read_text(encoding="utf-8"))
    assert len(pending["items"]) == 1
    assert pending["items"][0]["evidence_ids"] == ["p1"]

    second = candidate("cand-2", "p2")
    aggregated = aggregate_candidates([second], historical_candidates=pending["items"])
    deterministic, model_candidates = partition_finalization_candidates(
        aggregated, [], {"terms": []}, {"entries": []},
    )
    assert deterministic == []
    assert len(model_candidates) == 1
    decision = [{"candidate_id": aggregated[0]["candidate_id"], "action": "active"}]
    evidence = {"p1": "小泉宏美が来た", "p2": "小泉宏美は答えた"}
    for _ in range(2):
        apply_knowledge_delta(workspace, "c2", aggregated, decision, evidence_texts=evidence)

    pending = json.loads(workspace.knowledge_candidates_path.read_text(encoding="utf-8"))
    glossary = json.loads(workspace.glossary_path.read_text(encoding="utf-8"))
    assert pending["items"] == []
    assert len(glossary["terms"]) == 1
    assert {item["paragraph_id"] for item in glossary["terms"][0]["evidence"]} == {"p1", "p2"}


def test_pipeline_finalization_batches_and_retries_missing_decisions(tmp_path: Path, monkeypatch) -> None:
    from translator.pipeline import chapter_pipeline as pipeline_module
    from translator.pipeline.chapter_pipeline import IterativePipeline

    workspace = BookWorkspace.at(tmp_path / "output", "batch-book")
    manifest_path = tmp_path / "manifest.json"
    paragraphs = [
        {"id": f"p{index}", "source": f"人物{index}", "translated": f"人物{index}"}
        for index in range(5)
    ] + [
        {"id": f"q{index}", "source": f"人物{index}", "translated": f"人物{index}"}
        for index in range(5)
    ]
    manifest_path.write_text(json.dumps({
        "book": "batch-book",
        "chapters": [{"id": "c1", "paragraphs": paragraphs}],
    }, ensure_ascii=False), encoding="utf-8")
    calls: dict[tuple[str, ...], int] = {}

    def extractor(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert kind == "finalize"
        ids = tuple(str(item["candidate_id"]) for item in payload["candidates"])
        batch_key = tuple(str(item.get("source") or item.get("key")) for item in payload["candidates"])
        calls[batch_key] = calls.get(batch_key, 0) + 1
        # Force the coverage validator to retry every multi-item batch.
        selected = [] if len(ids) > 1 and calls[batch_key] == 1 else list(ids)
        return {"decisions": [
            {"candidate_id": candidate_id, "action": "candidate"} for candidate_id in selected
        ]}

    pipeline = IterativePipeline(
        book="batch-book",
        workspace=workspace,
        manifest=manifest_path,
        tool_call=lambda *_args: {"status": "ok"},
        knowledge_extractor=extractor,
    )
    monkeypatch.setattr(pipeline_module, "load_config", lambda: {
        "knowledge_extractor": {
            "enabled": True,
            "finalization_batch_size": 2,
            "finalization_max_retries": 2,
            "input_hard_limit_chars": 30_000,
        }
    })
    pipeline._knowledge_candidates["c1"] = [
        {
            "candidate_id": f"cand-{index}",
            "kind": "glossary",
            "source": f"人物{index}",
            "target": f"人物{index}",
            "category": "person",
            "source_scope": "body",
            "confidence": 0.95,
            "source_window": f"c1:window:{index}",
            "source_paragraph_ids": [f"p{index}", f"q{index}"],
            "evidence_ids": [f"p{index}", f"q{index}"],
            "source_fragment": f"人物{index}",
            "target_fragment": f"人物{index}",
        }
        for index in range(5)
    ]

    summary = pipeline._finalize_chapter_knowledge("c1", paragraphs)
    assert summary["status"] == "completed"
    assert summary["batch_count"] == 3
    assert summary["missing_decisions"] == 0
    assert summary["attempt_count"] == 5
    stored = json.loads(workspace.knowledge_candidates_path.read_text(encoding="utf-8"))
    assert len(stored["items"]) == 5


def test_pipeline_empty_finalization_is_incomplete_after_retries(tmp_path: Path, monkeypatch) -> None:
    from translator.pipeline import chapter_pipeline as pipeline_module
    from translator.pipeline.chapter_pipeline import IterativePipeline

    workspace = BookWorkspace.at(tmp_path / "output", "incomplete-book")
    manifest_path = tmp_path / "manifest.json"
    paragraph = {"id": "p1", "source": "小泉宏美", "translated": "小泉宏美"}
    second_paragraph = {"id": "p2", "source": "小泉宏美", "translated": "小泉宏美"}
    manifest_path.write_text(json.dumps({
        "book": "incomplete-book",
        "chapters": [{"id": "c1", "paragraphs": [paragraph, second_paragraph]}],
    }, ensure_ascii=False), encoding="utf-8")
    pipeline = IterativePipeline(
        book="incomplete-book",
        workspace=workspace,
        manifest=manifest_path,
        tool_call=lambda *_args: {"status": "ok"},
        knowledge_extractor=lambda _kind, _payload: {"decisions": []},
    )
    monkeypatch.setattr(pipeline_module, "load_config", lambda: {
        "knowledge_extractor": {
            "enabled": True,
            "finalization_batch_size": 12,
            "finalization_max_retries": 1,
            "input_hard_limit_chars": 30_000,
        }
    })
    pipeline._knowledge_candidates["c1"] = [{
        "candidate_id": "cand-1",
        "kind": "glossary",
        "source": "小泉宏美",
        "target": "小泉宏美",
        "category": "person",
        "source_scope": "body",
        "confidence": 0.95,
        "source_window": "c1:window:1",
        "source_paragraph_ids": ["p1", "p2"],
        "evidence_ids": ["p1", "p2"],
        "source_fragment": "小泉宏美",
        "target_fragment": "小泉宏美",
    }]

    summary = pipeline._finalize_chapter_knowledge("c1", [paragraph, second_paragraph])
    output = json.loads((workspace.reviews_dir / "c1-knowledge-finalize.json").read_text(encoding="utf-8"))
    assert summary["status"] == "incomplete"
    assert summary["missing_decisions"] == 1
    assert summary["attempt_count"] == 2
    assert output["missing_decision_ids"] == ["cand-1"]


def test_zero_model_candidates_skips_provider_calls_completely(tmp_path: Path, monkeypatch) -> None:
    from translator.pipeline import chapter_pipeline as pipeline_module
    from translator.pipeline.chapter_pipeline import IterativePipeline

    workspace = BookWorkspace.at(tmp_path / "output", "zero-model-book")
    manifest_path = tmp_path / "manifest.json"
    paragraphs = [
        {"id": f"p{i}", "source": f"原文{i}", "translated": f"译文{i}"} for i in range(1, 4)
    ]
    manifest_path.write_text(json.dumps({
        "book": "zero-model-book",
        "chapters": [{"id": "c1", "paragraphs": paragraphs}],
    }, ensure_ascii=False), encoding="utf-8")

    def failing_extractor(*_args, **_kwargs):
        raise AssertionError("Provider should NOT be called when model_candidates is empty")

    pipeline = IterativePipeline(
        book="zero-model-book",
        workspace=workspace,
        manifest=manifest_path,
        tool_call=lambda *_args: {"status": "ok"},
        knowledge_extractor=failing_extractor,
    )
    monkeypatch.setattr(pipeline_module, "load_config", lambda: {
        "knowledge_extractor": {
            "enabled": True,
            "finalization_batch_size": 12,
            "finalization_max_retries": 1,
            "input_hard_limit_chars": 30_000,
        }
    })
    # 2 single-evidence candidates + 1 non-body candidate
    pipeline._knowledge_candidates["c1"] = [
        {
            "candidate_id": "single-1",
            "kind": "glossary",
            "source": "角色甲",
            "target": "角色甲",
            "category": "person",
            "source_scope": "body",
            "confidence": 0.95,
            "source_window": "c1:window:1",
            "source_paragraph_ids": ["p1"],
            "evidence_ids": ["p1"],
            "source_fragment": "角色甲",
            "target_fragment": "角色甲",
        },
        {
            "candidate_id": "single-2",
            "kind": "glossary",
            "source": "角色乙",
            "target": "角色乙",
            "category": "person",
            "source_scope": "body",
            "confidence": 0.95,
            "source_window": "c1:window:2",
            "source_paragraph_ids": ["p2"],
            "evidence_ids": ["p2"],
            "source_fragment": "角色乙",
            "target_fragment": "角色乙",
        },
        {
            "candidate_id": "title-cand",
            "kind": "glossary",
            "source": "书名",
            "target": "书名",
            "category": "work_title",
            "source_scope": "title",
            "confidence": 0.99,
            "source_window": "c1:window:0",
            "source_paragraph_ids": ["p3"],
            "evidence_ids": ["p3"],
            "source_fragment": "书名",
            "target_fragment": "书名",
        },
    ]

    summary = pipeline._finalize_chapter_knowledge("c1", paragraphs)
    assert summary["status"] == "completed"
    assert summary["deterministic_decisions"] == 3
    assert summary["model_candidates"] == 0
    assert summary["decisions"] == 3
    assert summary["missing_decisions"] == 0

    stored = json.loads(workspace.knowledge_candidates_path.read_text(encoding="utf-8"))
    assert len(stored["items"]) == 3
    actions = {item["candidate_id"]: item["final_action"] for item in stored["items"]}
    reasons = {item["candidate_id"]: item["final_reason"] for item in stored["items"]}
    assert actions == {"single-1": "candidate", "single-2": "candidate", "title-cand": "candidate"}
    assert reasons["single-1"] == "insufficient_recurrence"
    assert reasons["title-cand"] == "non_body_source"


def test_conflict_bypasses_single_evidence_filter() -> None:
    single_with_conflict = {
        "candidate_id": "conflicted-cand",
        "kind": "glossary",
        "source": "魔法使",
        "target": "魔法师",
        "category": "system_term",
        "source_scope": "body",
        "confidence": 0.95,
        "source_window": "c1:window:1",
        "evidence_ids": ["c1-p1"],
    }
    conflict = {
        "conflict_id": "cf-1",
        "candidate_id": "conflicted-cand",
        "kind": "glossary",
        "key": "魔法使",
        "existing_value": "魔导士",
        "proposed_value": "魔法师",
    }
    deterministic, model_candidates = partition_finalization_candidates(
        [single_with_conflict], [conflict], {"terms": []}, {"entries": []},
    )
    assert deterministic == []
    assert len(model_candidates) == 1
    assert model_candidates[0]["candidate_id"] == "conflicted-cand"


def test_compact_payload_compresses_rich_candidates_significantly() -> None:
    rich_candidate = {
        "candidate_id": "cand-very-long-unique-uuid-1234567890",
        "kind": "glossary",
        "source": "香月てる美",
        "target": "香月照美",
        "category": "person",
        "source_scope": "body",
        "confidence": 0.95,
        "source_window": "c1:window:1, c2:window:1",
        "source_paragraph_ids": ["c1-p1", "c1-p2", "c2-p1"],
        "evidence_ids": ["c1-p1", "c1-p2", "c2-p1"],
        "source_fragment": "長い日本語原文テキスト" * 10,
        "target_fragment": "很长的中文译文段落" * 10,
        "referenced_glossary_ids": ["g1", "g2"],
        "referenced_memory_keys": ["m1", "m2"],
        "note": "详细的上下文解释说明",
    }
    raw_json = json.dumps(rich_candidate, ensure_ascii=False)
    compact_doc, id_map = compact_finalization_payload({
        "chapter_id": "c2",
        "candidates": [rich_candidate],
        "conflicts": [],
        "active_glossary": [],
        "related_memory": [],
    })
    compact_json = json.dumps(compact_doc["candidates"][0], ensure_ascii=False)
    assert id_map["k1"] == "cand-very-long-unique-uuid-1234567890"
    assert compact_doc["candidates"][0]["evidence_count"] == 3
    assert compact_doc["candidates"][0]["chapter_count"] == 2
    assert "source_fragment" not in compact_doc["candidates"][0]
    assert "referenced_glossary_ids" not in compact_doc["candidates"][0]
    # Compact candidate JSON is at least 60% smaller than raw candidate JSON
    assert len(compact_json) < len(raw_json) * 0.4
