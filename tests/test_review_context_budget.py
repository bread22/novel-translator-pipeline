from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from translator.review.context_budget import (
    ReviewContextOverflowError,
    ReviewTargetSplitRequired,
    build_budgeted_review_context,
    build_memory_index,
)
from translator.review.reviewer import _update_rolling_payload, should_adaptively_split


SCHEMA = Path(__file__).resolve().parents[1] / "schemas" / "chapter-review-output.schema.json"


def item(ident: str, source: str, translated: str = "译文") -> dict[str, str]:
    return {"id": ident, "source": source, "translated": translated}


def test_selector_is_deterministic_and_preserves_authoritative_sources() -> None:
    authoritative = {
        "translation_policy": "稳定政策",
        "glossary": [
            {"term_id": "term-hero", "source": "勇者", "target": "勇者", "status": "active", "note": "内部备注"},
            {"term_id": "term-unused", "source": "无关", "target": "无关译名", "status": "active"},
        ],
        "book_memory": {
            "schema_version": "2.0",
            "entries": [
                {"fact_id": "fact-hero", "key": "勇者", "value": "勇者与王都有关", "entities": ["勇者", "王都"]},
                {"fact_id": "fact-unused", "key": "海港", "value": "遥远海港的天气"},
            ],
        },
        "previous_chapter_state": {"active_entities": ["勇者"], "summary": "上一章"},
    }
    original = deepcopy(authoritative)
    kwargs = dict(
        items=[item("p1", "勇者回到王都", "勇者回到了王都")],
        context_before=[item("p0", "他推开门")],
        context_after=[item("p2", "众人欢呼")],
        budget={"enabled": True, "operational_input_hard_limit_chars": 50_000},
        schema_path=SCHEMA,
    )

    first, first_diag, first_payload = build_budgeted_review_context(authoritative, **kwargs)
    second, second_diag, second_payload = build_budgeted_review_context(authoritative, **kwargs)

    assert first.context_snapshot_id == second.context_snapshot_id
    assert first_diag == second_diag
    assert first_payload == second_payload
    assert authoritative == original
    assert [entry["term_id"] for entry in first_payload["glossary"]] == ["term-hero"]
    assert first_diag["required_reason_counts"]["direct_term_match"] == 1
    assert first_diag["required_reason_counts"]["local_context_minimum"] == 2
    assert "prompt" not in first_diag

    rebuilt_a = build_memory_index(authoritative["book_memory"])
    rebuilt_b = build_memory_index(deepcopy(authoritative["book_memory"]))
    assert rebuilt_a.content_hash == rebuilt_b.content_hash
    assert rebuilt_a.facts[0].stable_id == "fact:fact-hero"


def test_persisted_glossary_document_projects_selected_terms_in_document_shape() -> None:
    _snapshot, _diagnostics, payload = build_budgeted_review_context(
        {
            "translation_policy": "政策",
            "glossary": {"schema_version": "3.0", "terms": [
                {"term_id": "t1", "source": "甲", "target": "阿甲", "status": "active"},
                {"term_id": "t2", "source": "乙", "target": "阿乙", "status": "active"},
            ]},
            "book_memory": {},
        },
        items=[item("p1", "甲出现了")],
        budget={"enabled": True, "operational_input_hard_limit_chars": 50_000},
        schema_path=SCHEMA,
    )

    assert [term["term_id"] for term in payload["glossary"]["terms"]] == ["t1"]


def test_optional_local_context_is_evicted_as_complete_paragraphs() -> None:
    before = [item(f"b{i}", "前文" * 500) for i in range(5)]
    after = [item(f"a{i}", "后文" * 500) for i in range(5)]
    snapshot, diagnostics, payload = build_budgeted_review_context(
        {"translation_policy": "政策", "glossary": [], "book_memory": {}},
        items=[item("p1", "目标")],
        context_before=before,
        context_after=after,
        budget={
            "enabled": True,
            "operational_input_hard_limit_chars": 16_000,
            "operational_headroom_chars": 500,
            "background_soft_limit_chars": 30_000,
        },
        schema_path=SCHEMA,
    )

    assert diagnostics["prompt_chars"] <= 16_000
    assert diagnostics["excluded_optional_entries"] > 0
    assert payload["context_before"][-1]["id"] == "b4"
    assert payload["context_after"][0]["id"] == "a0"
    assert all(paragraph["source"] == "前文" * 500 for paragraph in snapshot.context_before)


def test_targeted_evidence_promotes_distant_local_paragraph() -> None:
    _snapshot, diagnostics, payload = build_budgeted_review_context(
        {"translation_policy": "政策", "glossary": [], "book_memory": {}},
        items=[item("p1", "目标")],
        context_after=[item("a1", "近证据"), item("a2", "远证据")],
        trigger_evidence=[{"id": "p1", "evidence_ids": ["a2"]}],
        budget={"enabled": True, "operational_input_hard_limit_chars": 50_000},
        schema_path=SCHEMA,
    )

    assert [paragraph["id"] for paragraph in payload["context_after"]] == ["a1", "a2"]
    assert diagnostics["required_reason_counts"]["targeted_evidence"] == 1


def test_speaker_metadata_promotes_identity_fact_without_name_in_text() -> None:
    _snapshot, diagnostics, payload = build_budgeted_review_context(
        {
            "translation_policy": "政策",
            "glossary": [],
            "book_memory": {"entries": [
                {"fact_id": "speaker-fact", "key": "甲", "value": "甲是当前说话者", "entities": ["甲"]},
            ]},
        },
        items=[{"id": "p1", "source": "「快走。」", "translated": "“快走。”", "speaker_id": "甲"}],
        budget={"enabled": True, "operational_input_hard_limit_chars": 50_000},
        schema_path=SCHEMA,
    )

    assert payload["book_memory"]["entries"][0]["fact_id"] == "speaker-fact"
    assert diagnostics["required_reason_counts"]["speaker_identity"] == 1


def test_memory_retrieval_derives_scene_entities_from_current_text() -> None:
    _snapshot, diagnostics, payload = build_budgeted_review_context(
        {
            "translation_policy": "政策",
            "glossary": [],
            "book_memory": {"entries": [{
                "key": "明彦—玲子关系",
                "value": "玲子是明彦的妻子",
                "category": "relationship",
                "confidence": 0.95,
            }]},
        },
        items=[item("p1", "明彦回到家里", "明彦回到家里")],
        budget={"enabled": True, "operational_input_hard_limit_chars": 50_000},
        schema_path=SCHEMA,
    )

    assert diagnostics["scene_entity_signal_insufficient"] is False
    assert "明彦" in diagnostics["scene_entities"]
    assert payload["book_memory"]["entries"][0]["key"] == "明彦—玲子关系"
    assert diagnostics["required_reason_counts"]["active_relationship"] == 1


def test_required_overflow_splits_target_then_stops_at_one_paragraph() -> None:
    authoritative = {
        "translation_policy": "政策",
        "glossary": [{"term_id": "huge", "source": "巨词", "target": "译" * 8_000, "status": "active"}],
        "book_memory": {},
    }
    common = dict(
        authoritative_context=authoritative,
        budget={"enabled": True, "operational_input_hard_limit_chars": 10_000, "operational_headroom_chars": 0},
        schema_path=SCHEMA,
    )

    with pytest.raises(ReviewTargetSplitRequired) as split:
        build_budgeted_review_context(items=[item("p1", "巨词"), item("p2", "巨词")], **common)
    assert should_adaptively_split(split.value)

    with pytest.raises(ReviewContextOverflowError) as overflow:
        build_budgeted_review_context(items=[item("p1", "巨词")], **common)
    assert overflow.value.reason == "required_context_overflow"
    assert not should_adaptively_split(overflow.value)


def test_rolling_state_does_not_replace_previous_chapter_seed() -> None:
    base = {
        "previous_chapter_state": {"summary": "上一章"},
        "current_chapter_review_context": {"active_entities": ["甲"]},
    }
    rolled = _update_rolling_payload(base, {
        "rolling_context_delta": {"active_entities": ["乙"], "locations": ["新宿"]},
    })

    assert rolled["previous_chapter_state"]["summary"] == "上一章"
    assert rolled["current_chapter_review_context"]["active_entities"] == ["甲", "乙"]
    assert rolled["current_chapter_review_context"]["locations"] == ["新宿"]
