from translator.review.reviewer import approved_fixes, evaluate_apply_gate, merge_chapter_reviews


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


def test_inconsistent_fix_reason_is_normalized_to_pass():
    result = evaluate_apply_gate([proposal(reason="当前译文正确、无实质错误，无需修改")], autonomous=True)[0]
    assert result["decision"] == "PASS"
    assert result["replacement"] == ""
    assert result["apply_reason"] == "reason_indicates_pass"
    assert result["auto_apply"] is False


def test_fix_without_replacement_is_report_only_and_never_approved():
    result = evaluate_apply_gate([proposal(replacement="")], autonomous=True)[0]
    assert result["decision"] == "REPORT_ONLY"
    assert result["apply_reason"] == "missing_replacement"
    assert result["apply_state"] == "blocked"
    assert approved_fixes([proposal(replacement="")], autonomous=True) == []


def test_style_only_mistranslation_is_normalized_to_pass():
    result = evaluate_apply_gate([
        proposal(category="mistranslation", reason="语序润色，让表达更自然", replacement="润色后的译文")
    ], autonomous=True)[0]
    assert result["decision"] == "PASS"
    assert result["category"] == "style"
    assert result["apply_reason"] == "style_only_finding"


def test_unsupported_terminology_is_report_only_but_evidenced_term_can_apply():
    unsupported = evaluate_apply_gate([
        proposal(category="terminology", severity="major", reason="称谓不统一", replacement="新称谓")
    ], autonomous=True)[0]
    assert unsupported["decision"] == "REPORT_ONLY"
    assert unsupported["apply_reason"] == "terminology_evidence_required"

    evidenced = evaluate_apply_gate([
        proposal(
            category="terminology",
            reason="glossary 明确固定译名，当前译法不一致",
            replacement="固定译名",
        )
    ], autonomous=True)[0]
    assert evidenced["apply_reason"] == "gate_passed"


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
