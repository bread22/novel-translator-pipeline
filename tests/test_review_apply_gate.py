from translator.review.reviewer import evaluate_apply_gate, merge_chapter_reviews


def proposal(**overrides):
    item = {
        "id": "p1", "decision": "FIX_REQUIRED", "category": "mistranslation",
        "severity": "major", "confidence": .95, "replacement": "正确译文", "auto_apply": True,
    }
    item.update(overrides)
    return item


def test_style_low_confidence_clear_and_consensus_do_not_bypass_gate():
    records = evaluate_apply_gate([
        proposal(id="style", category="style", confidence=1),
        proposal(id="low", confidence=.88, consensus=True),
        proposal(id="clear", operation="clear", replacement="", confidence=1, consensus=True),
    ], autonomous=True)
    assert [r["apply_reason"] for r in records] == [
        "style_not_auto_applied", "below_threshold", "clear_disabled"
    ]


def test_replacement_validator_blocks_scripts_masking_meta_and_latin_hiccup():
    for replacement in ("残留かな", "遮掩×", "建议修改为：正确译文", "hiccup", "答案一或答案二"):
        result = evaluate_apply_gate([proposal(replacement=replacement)], autonomous=True)[0]
        assert result["apply_state"] == "blocked"
        assert result["validation_errors"]


def test_identical_replacement_becomes_pass_and_stale_hash_is_not_applied():
    same = evaluate_apply_gate([proposal(replacement="当前译文")], current_translations={"p1": "当前译文"})[0]
    stale = evaluate_apply_gate([proposal(base_translation_hash="bad")], current_translations={"p1": "当前译文"})[0]
    assert (same["decision"], same["apply_reason"]) == ("PASS", "no_op")
    assert stale["apply_reason"] == "stale_base_translation"


def test_dual_review_divergence_is_report_only_without_selected_replacement():
    base = {"checked_ids": ["p1"]}
    merged = merge_chapter_reviews(
        {**base, "fixes": [proposal(replacement="译文甲")]},
        {**base, "fixes": [proposal(replacement="译文乙")]},
    )
    item = merged["fixes"][0]
    assert item["decision"] == "REPORT_ONLY"
    assert item["replacement"] == ""
    assert item["apply_reason"] == "replacement_disagreement"


def test_checked_in_calibration_fixture_replays_without_provider():
    import json
    from pathlib import Path
    root = Path(__file__).parent / "fixtures" / "review_calibration"
    fixture = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    blocked = [case for case in fixture["cases"] if case.get("expected") == "blocked"]
    results = [
        evaluate_apply_gate([proposal(id=case["id"], replacement=case["replacement"])], autonomous=True)[0]
        for case in blocked
    ]
    assert all(item["apply_state"] == "blocked" for item in results)
    baseline = json.loads((root / "baseline-report.json").read_text(encoding="utf-8"))
    assert baseline["original_report"]["findings"] == 1167
    import hashlib
    assert hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest() == baseline["manifest_sha256"]
