from __future__ import annotations

import json
from pathlib import Path

import pytest

import translator.glossary.name_normalizer as name_normalizer
from translator.glossary.name_validation import check_person_name
from translator.glossary.validation import validate_term_candidate


FIXTURE = Path(__file__).parent / "fixtures" / "opencc_name_normalization.json"


def test_opencc_golden_fixture_is_deterministic() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    metadata = name_normalizer.normalization_metadata()
    assert metadata["method"] == fixture["backend"]["method"]
    assert metadata["version"] == fixture["backend"]["version"]
    assert metadata["data_version"] == fixture["backend"]["data_version"]
    for case in fixture["cases"]:
        result = name_normalizer.normalize_japanese_name(case["source"])
        assert result.preferred == case["preferred"]
        if "diagnostic" in case:
            assert case["diagnostic"] in result.diagnostics
        assert result == name_normalizer.normalize_japanese_name(case["source"])


def test_name_validation_uses_preferred_candidate_and_preserves_ambiguous_input() -> None:
    preferred = check_person_name("戸", "户", "person")
    assert preferred is not None
    assert preferred.status == "pass"
    assert preferred.expected_target == "户"
    assert preferred.normalized_candidates == ("户", "戸")
    assert "opencc_ambiguous" in preferred.normalization_diagnostics

    nonpreferred = check_person_name("戸", "戸", "person")
    assert nonpreferred is not None
    assert nonpreferred.status == "pass"
    assert nonpreferred.expected_target == "户"
    assert nonpreferred.reason == "opencc_nonpreferred_candidate"


def test_unmapped_and_mixed_names_do_not_enter_name_review_path() -> None:
    unmapped = check_person_name("髙", "高", "person")
    assert unmapped is not None
    assert unmapped.status == "pass"
    assert unmapped.expected_target == "高"
    assert unmapped.normalization_warning == "opencc_unmapped"
    assert check_person_name("甲カ乙", "甲卡乙", "person") is None

    invalid_target = validate_term_candidate(
        {
            "source": "甲乙",
            "target": "甲カ",
            "category": "person",
            "confidence": 0.96,
            "evidence_ids": ["p1"],
        },
        evidence_texts={"p1": "甲乙"},
    )
    assert not invalid_target.valid
    assert invalid_target.reason == "target_contains_japanese_kana"


def test_backend_failure_and_candidate_overflow_are_nonblocking(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_backend() -> object:
        raise RuntimeError("fixture backend failure")

    name_normalizer.reset_backend_for_tests()
    monkeypatch.setattr(name_normalizer, "_build_backend", fail_backend)
    failed = check_person_name("甲乙", "甲乙", "person")
    assert failed is not None
    assert failed.status == "pass"
    assert failed.normalization_warning == "opencc_backend_error"

    name_normalizer.reset_backend_for_tests()
    monkeypatch.undo()
    name_normalizer.normalize_japanese_name("戸")
    monkeypatch.setattr(name_normalizer, "MAX_NAME_CANDIDATES", 1)
    overflow = check_person_name("戸", "错", "person")
    assert overflow is not None
    assert overflow.status == "pass"
    assert overflow.expected_target == "错"
    assert overflow.normalization_warning == "candidate_overflow"
    name_normalizer.reset_backend_for_tests()
