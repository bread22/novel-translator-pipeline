from __future__ import annotations

from translator.glossary.lifecycle import merge_term_candidates
from translator.glossary.projection import build_translation_term_projection


def empty() -> dict:
    return {"schema_version": "3.0", "terms": [], "conflicts": [], "revisions": []}


def test_gated_activation_and_idempotent_evidence() -> None:
    candidate = {"source": "止血鉗", "target": "止血钳", "category": "medical_device", "confidence": 0.95, "evidence_ids": ["p1"]}
    glossary, first = merge_term_candidates(empty(), [candidate], chapter_id="c1", reporter="preextractor", evidence_texts={"p1": "止血鉗を使う"})
    assert glossary["terms"][0]["status"] == "candidate"
    assert first["activated"] == 0

    glossary, replay = merge_term_candidates(glossary, [candidate], chapter_id="c1", reporter="preextractor", evidence_texts={"p1": "止血鉗を使う"})
    assert glossary["terms"][0]["occurrences"] == 1
    assert replay["activated"] == 0

    glossary, second = merge_term_candidates(glossary, [candidate], chapter_id="c1", reporter="chapter_reviewer", evidence_texts={"p1": "止血鉗を使う"})
    assert glossary["terms"][0]["status"] == "active"
    assert second["activated"] == 1


def test_single_conflict_is_disputed_then_two_independent_reports_revise() -> None:
    glossary = {
        **empty(),
        "terms": [{
            "source": "雨宮慶",
            "target": "旧译",
            "category": "person",
            "status": "active",
            "confidence": 0.96,
            "evidence": [{"chapter_id": "c1", "paragraph_id": "p1", "reporter": "r0", "confidence": 0.96}],
        }],
    }
    new = {"source": "雨宮慶", "target": "雨宫庆", "category": "person", "confidence": 0.99, "evidence_ids": ["p2"]}
    glossary, _ = merge_term_candidates(glossary, [new], chapter_id="c2", reporter="r1", evidence_texts={"p2": "雨宮慶"})
    assert glossary["terms"][0]["status"] == "disputed"
    glossary, summary = merge_term_candidates(glossary, [{**new, "evidence_ids": ["p3"]}], chapter_id="c3", reporter="r2", evidence_texts={"p3": "雨宮慶"})
    assert glossary["terms"][0]["status"] == "active"
    assert glossary["terms"][0]["target"] == "雨宫庆"
    assert summary["revised"] == 1
    assert glossary["revisions"]


def test_projection_excludes_non_active_and_diagnostic_fields() -> None:
    glossary, _ = merge_term_candidates(
        empty(),
        [{"source": "人物", "target": "人物", "category": "person", "confidence": 0.96, "evidence_ids": ["p1"]}],
        chapter_id="c1", reporter="r", evidence_texts={"p1": "人物"},
    )
    glossary["terms"].append({"source": "身体", "target": "身体", "category": "body_part", "status": "retired", "evidence": [{"paragraph_id": "p2"}]})
    projection = build_translation_term_projection(glossary)
    assert projection["terms"] == [{"source": "人物", "target": "人物", "category": "person"}]


def test_review_reporters_are_projected_into_independent_evidence() -> None:
    candidate = {
        "source": "人物",
        "target": "人物",
        "category": "person_alias",
        "confidence": 0.96,
        "evidence_ids": ["p1"],
        "reporters": ["primary", "secondary"],
    }
    glossary, summary = merge_term_candidates(
        empty(),
        [candidate],
        chapter_id="c1",
        reporter="chapter_reviewer",
        evidence_texts={"p1": "人物出现"},
    )
    assert summary["blocked_by_shape"] == 0
    assert glossary["terms"][0]["status"] == "active"
    assert {item["reporter"] for item in glossary["terms"][0]["evidence"]} == {"primary", "secondary"}
