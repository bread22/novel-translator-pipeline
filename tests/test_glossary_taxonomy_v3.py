from __future__ import annotations

import json
from pathlib import Path

import pytest

from translator.glossary.lifecycle import merge_term_candidates
from translator.glossary.models import GlossaryCandidate
from translator.glossary.taxonomy import BLOCKED, CATEGORY_VALUES, DIRECT_ALLOWED, GATED_ALLOWED
from translator.glossary.validation import validate_term_candidate


def test_fixture_covers_closed_taxonomy() -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "glossary_taxonomy_ja_zh.json").read_text(encoding="utf-8"))
    assert set(fixture["direct_allowed"]) == DIRECT_ALLOWED
    assert set(fixture["gated_allowed"]) == GATED_ALLOWED
    assert set(fixture["blocked"]) == BLOCKED
    assert set(CATEGORY_VALUES) == DIRECT_ALLOWED | GATED_ALLOWED | BLOCKED


def test_validator_requires_confidence_and_real_evidence() -> None:
    missing = validate_term_candidate(
        {"source": "人物", "target": "人物", "category": "person", "evidence_ids": ["p1"]},
        evidence_texts={"p1": "人物出现"},
    )
    assert not missing.valid and missing.reason == "missing_confidence"

    fake = validate_term_candidate(
        {"source": "人物", "target": "人物", "category": "person", "confidence": 0.96, "evidence_ids": ["p404"]},
        evidence_texts={"p1": "人物出现"},
    )
    assert not fake.valid and "unknown_evidence_id" in fake.reason


def test_blocked_candidate_never_becomes_active() -> None:
    result = validate_term_candidate(
        GlossaryCandidate(source="手", target="手", category="body_part", confidence=1.0, evidence_ids=["p1"]),
        evidence_texts={"p1": "手を握る"},
    )
    assert not result.valid and result.reason == "blocked_category"


def test_target_shape_and_kana_are_deterministic() -> None:
    for target in ("A/B", "中文（解释）", "カタカナ"):
        result = validate_term_candidate(
            {"source": "人物", "target": target, "category": "person", "confidence": 0.96, "evidence_ids": ["p1"]},
            evidence_texts={"p1": "人物出现"},
        )
        assert not result.valid


def test_metadata_sources_are_not_glossary_candidates() -> None:
    result = validate_term_candidate(
        {
            "source": "作者名",
            "target": "作者名",
            "category": "person",
            "confidence": 1.0,
            "evidence_ids": ["a1"],
            "source_scope": "author",
        },
        evidence_texts={"a1": "作者名"},
    )
    assert not result.valid and result.reason == "metadata_source"

    evidence_result = validate_term_candidate(
        {
            "source": "书名",
            "target": "书名",
            "category": "work_title",
            "confidence": 1.0,
            "evidence_ids": ["cover-1"],
        },
        evidence_texts={"cover-1": {"text": "书名", "source_scope": "cover"}},
    )
    assert not evidence_result.valid
    assert "metadata_source" in evidence_result.reason


def test_validator_keeps_valid_evidence_when_context_ids_do_not_match() -> None:
    result = validate_term_candidate(
        {
            "source": "人物",
            "target": "人物",
            "category": "person",
            "confidence": 0.96,
            "evidence_ids": ["p1", "p2"],
        },
        evidence_texts={"p1": "人物出现", "p2": "“你来了。”"},
    )
    assert result.valid
    assert result.evidence_ids == ("p1",)
    assert result.discarded_evidence == (("p2", "source_not_in_evidence"),)
