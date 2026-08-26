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
