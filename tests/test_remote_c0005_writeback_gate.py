from __future__ import annotations

import json
from pathlib import Path
import tomllib

from translator.review.reviewer import (
    compose_approved_fixes,
    evaluate_apply_gate,
    unique_writeback_fixes,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "remote_c0005"
APPROVED_IDS = {
    "c0005-p00077",
    "c0005-p00157",
    "c0005-p00188",
    "c0005-p00218",
    "c0005-p00223",
    "c0005-p00260",
    "c0005-p00273",
    "c0005-p00275",
    "c0005-p00283",
    "c0005-p00284",
    "c0005-p00291",
    "c0005-p00294",
    "c0005-p00295",
    "c0005-p00297",
}
EVIDENCE_BLOCKED_IDS = {
    "c0005-p00110",
    "c0005-p00184",
    "c0005-p00192",
}


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


def chapter_paragraphs(name: str) -> dict[str, dict]:
    manifest = load_fixture(name)
    chapter = next(item for item in manifest["chapters"] if item["id"] == "c0005")
    return {str(item["id"]): item for item in chapter["paragraphs"]}


def test_remote_c0005_low_confidence_fixes_pass_with_valid_evidence() -> None:
    before = chapter_paragraphs("manifest-before-confidence-writeback.json")
    approved = load_fixture("c0005-confidence-gate-approved-fixes.json")["items"]
    current = {item_id: item["translated"] for item_id, item in before.items()}
    source = {item_id: item["source"] for item_id, item in before.items()}

    assert {item["id"] for item in approved} == APPROVED_IDS
    assert max(float(item["confidence"]) for item in approved) < 0.9

    results = evaluate_apply_gate(
        approved,
        threshold=1.0,  # legacy argument is deliberately ignored
        autonomous=True,
        current_translations=current,
        source_texts=source,
    )

    assert {item["id"] for item in results} == APPROVED_IDS
    assert all(item["apply_reason"] == "gate_passed" for item in results)
    assert all(item["validation_errors"] == [] for item in results)


def test_remote_c0005_evidence_gate_still_blocks_three_invalid_records() -> None:
    before = chapter_paragraphs("manifest-before-confidence-writeback.json")
    review = load_fixture("c0005-output.json")
    current = {item_id: item["translated"] for item_id, item in before.items()}
    source = {item_id: item["source"] for item_id, item in before.items()}
    candidates = [item for item in review["fixes"] if item["id"] in EVIDENCE_BLOCKED_IDS]

    results = evaluate_apply_gate(
        candidates,
        autonomous=True,
        current_translations=current,
        source_texts=source,
    )
    by_id = {item["id"]: item for item in results}

    assert set(by_id) == EVIDENCE_BLOCKED_IDS
    assert by_id["c0005-p00110"]["validation_errors"] == [
        "source_fragment_not_found",
        "current_fragment_not_found",
    ]
    assert by_id["c0005-p00184"]["validation_errors"] == ["proposed_fragment_not_found"]
    assert by_id["c0005-p00192"]["validation_errors"] == ["proposed_fragment_not_found"]
    assert all(item["apply_reason"] == "fix_evidence_validation_failed" for item in results)


def test_remote_c0005_fixture_replays_the_fourteen_paragraph_delta() -> None:
    before = chapter_paragraphs("manifest-before-confidence-writeback.json")
    after = chapter_paragraphs("manifest-after-confidence-writeback.json")
    approved = load_fixture("c0005-confidence-gate-approved-fixes.json")["items"]
    current = {item_id: item["translated"] for item_id, item in before.items()}
    source = {item_id: item["source"] for item_id, item in before.items()}

    gated = evaluate_apply_gate(
        approved,
        autonomous=True,
        current_translations=current,
        source_texts=source,
    )
    writebacks = unique_writeback_fixes(compose_approved_fixes(gated, current))
    projected = {item_id: dict(item) for item_id, item in before.items()}
    for item in writebacks:
        projected[item["id"]]["translated"] = item["replacement"]

    changed_ids = {
        item_id
        for item_id in before
        if before[item_id]["translated"] != after[item_id]["translated"]
    }
    assert changed_ids == APPROVED_IDS
    assert {item["id"] for item in writebacks} == APPROVED_IDS
    assert all(projected[item_id]["translated"] == after[item_id]["translated"] for item_id in APPROVED_IDS)
    assert all(
        projected[item_id]["translated"] == after[item_id]["translated"]
        for item_id in before
        if item_id not in APPROVED_IDS
    )
    assert all(before[item_id]["source"] == after[item_id]["source"] for item_id in before)


def test_remote_c0005_report_and_config_contract_match_new_gate() -> None:
    report = load_fixture("c0005-report.json")
    before = chapter_paragraphs("manifest-before-confidence-writeback.json")
    assert report["checked_paragraphs"] == len(before) == 304
    assert report["fix_required"] == 42
    assert report["blocked"] == 3

    config = tomllib.loads(Path("config.toml").read_text(encoding="utf-8"))
    example = tomllib.loads(Path("config.toml.example").read_text(encoding="utf-8"))
    assert "minimum_confidence" not in config["pipeline"]["review_apply"]
    assert "minimum_confidence" not in example["pipeline"]["review_apply"]

    schema = json.loads(Path("schemas/config.schema.json").read_text(encoding="utf-8"))
    assert "minimum_confidence" not in schema["properties"]["pipeline"]["properties"]["review_apply"]["properties"]
