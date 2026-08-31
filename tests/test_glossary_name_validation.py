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


def test_review_boundary_preserves_ambiguous_name_for_queue(tmp_path: Path) -> None:
    from translator.core.workspace import BookWorkspace
    from translator.review.knowledge_extractor import apply_knowledge_delta

    workspace = BookWorkspace.at(tmp_path / "output", "test-book")
    candidates = [{
        "candidate_id": "c1",
        "kind": "glossary",
        "source": "甲髙乙君",
        "target": "甲高乙君",
        "category": "person",
        "confidence": 0.99,
        "source_window": "w1",
        "source_paragraph_ids": ["p1"],
        "evidence_ids": ["p1"],
        "source_fragment": "甲髙乙君",
        "target_fragment": "甲高乙君",
    }]
    decisions = [{"candidate_id": "c1", "action": "active", "reason": "ok"}]
    summary = apply_knowledge_delta(workspace, "c1", candidates, decisions, evidence_texts={"p1": "甲髙乙君"})
    assert summary["candidate"] == 1
    assert workspace.knowledge_candidates_path.exists()


def test_katakana_and_phonetic_names_bypass_kanji_alignment() -> None:
    # Katakana names contain no CJK characters in source and must return None (not applicable)
    assert check_person_name("ラルス", "拉尔斯", "person") is None
    assert check_person_name("ルイーズ・ベネット", "路易丝·贝内特", "person") is None
    assert check_person_name("エマ", "艾玛", "person") is None
    assert check_person_name("マイク", "麦克", "person") is None
    assert check_person_name("キャロル", "卡罗尔", "person") is None

    # validate_term_candidate must validate and accept katakana/phonetic person terms
    res = validate_term_candidate(
        {
            "source": "ラルス",
            "target": "拉尔斯",
            "category": "person",
            "confidence": 0.95,
            "evidence_ids": ["p1"],
        },
        evidence_texts={"p1": "ラルス走进房间"},
    )
    assert res.valid
    assert res.candidate is not None
    assert res.candidate.source == "ラルス"
    assert res.candidate.target == "拉尔斯"


def test_katakana_names_with_honorifics_and_aliases() -> None:
    from translator.glossary.taxonomy import canonical_category

    assert canonical_category("人物称谓") == "fixed_person_title"
    assert canonical_category("游戏") == "system_term"
    assert canonical_category("家族名") == "group"

    res = validate_term_candidate(
        {
            "source": "ルイーズ・ベネット",
            "target": "路易丝·贝内特",
            "category": "person",
            "confidence": 0.95,
            "evidence_ids": ["p1", "p2"],
        },
        evidence_texts={"p1": "ルイーズ・ベネット说", "p2": "ルイーズ・ベネット来了"},
    )
    assert res.valid
    assert res.candidate is not None
    assert res.candidate.target == "路易丝·贝内特"
