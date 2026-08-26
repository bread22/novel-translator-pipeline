from __future__ import annotations

import json
from pathlib import Path

from translator.glossary.lifecycle import merge_term_candidates
from translator.glossary.name_validation import check_person_name
from translator.glossary.validation import validate_term_candidate


def empty() -> dict:
    return {"schema_version": "3.0", "terms": [], "conflicts": [], "revisions": []}


def test_person_name_strips_honorifics_and_corrects_deterministic_target() -> None:
    check = check_person_name("甲緒乙先生", "甲绒乙老师", "person")
    assert check is not None
    assert check.status == "corrected"
    assert check.name_source == "甲緒乙"
    assert check.expected_target == "甲绪乙"
    assert check.source_honorific == "先生"
    assert check.target_honorific == "老师"

    result = validate_term_candidate(
        {
            "source": "甲緒乙先生",
            "target": "甲绒乙老师",
            "category": "person",
            "confidence": 0.96,
            "evidence_ids": ["p1"],
        },
        evidence_texts={"p1": "甲緒乙先生来了"},
    )
    assert result.valid
    assert result.candidate is not None
    assert result.candidate.source == "甲緒乙"
    assert result.candidate.target == "甲绪乙"


def test_person_name_unknown_mapping_goes_to_queue(tmp_path: Path) -> None:
    queue = tmp_path / "data" / "name-mapping-review.jsonl"
    glossary, summary = merge_term_candidates(
        empty(),
        [{
            "source": "甲髙乙君",
            "target": "甲高乙君",
            "category": "person",
            "confidence": 0.99,
            "evidence_ids": ["p1"],
        }],
        chapter_id="c1",
        reporter="chapter_reviewer",
        evidence_texts={"p1": "甲髙乙君出现了"},
        name_mapping_queue_path=queue,
    )
    assert glossary["terms"] == []
    assert summary["blocked_by_name"] == 1
    assert summary["name_review_queued"] == 1
    record = json.loads(queue.read_text(encoding="utf-8").splitlines()[0])
    assert record["status"] == "pending"
    assert record["name_source"] == "甲髙乙"
    assert record["name_target"] == "甲高乙"
    assert record["reason"] == "unmapped_character_mismatch"

    _, replay_summary = merge_term_candidates(
        empty(),
        [{
            "source": "甲髙乙君",
            "target": "甲高乙君",
            "category": "person",
            "confidence": 0.99,
            "evidence_ids": ["p1"],
        }],
        chapter_id="c2",
        reporter="chapter_reviewer",
        evidence_texts={"p1": "甲髙乙君出现了"},
        name_mapping_queue_path=queue,
    )
    assert replay_summary["blocked_by_name"] == 1
    assert replay_summary["name_review_queued"] == 0
    assert len(queue.read_text(encoding="utf-8").splitlines()) == 1


def test_person_name_same_characters_pass_with_separate_honorific() -> None:
    check = check_person_name("丙丁君", "丙丁君", "person")
    assert check is not None
    assert check.status == "pass"
    assert check.name_source == "丙丁"
    assert check.name_target == "丙丁"


def test_review_boundary_preserves_ambiguous_name_for_queue() -> None:
    from translator.review.reviewer import validate_chapter_review_payload

    payload = {
        "checked_ids": ["p1"],
        "fixes": [],
        "glossary_delta": {
            "add": [{
                "source": "甲髙乙君",
                "target": "甲高乙君",
                "category": "person",
                "confidence": 0.99,
                "evidence_ids": ["p1"],
            }],
            "update": [],
            "conflicts": [],
        },
        "memory_delta": {"add": [], "update": [], "conflicts": []},
        "chapter_state": {},
    }
    normalized = validate_chapter_review_payload(payload, {"p1"})
    assert len(normalized["glossary_delta"]["add"]) == 1
