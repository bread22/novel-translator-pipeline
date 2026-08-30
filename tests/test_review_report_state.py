from translator.review.reviewer import finalize_writeback_states, review_report_counts


def test_only_exact_manifest_verification_becomes_applied():
    records = [
        {"id": "p1", "decision": "FIX_REQUIRED", "replacement": "新译", "apply_reason": "gate_passed", "apply_state": "not_applied"},
        {"id": "p2", "decision": "REPORT_ONLY", "replacement": "", "apply_reason": "report_only", "apply_state": "not_applied"},
    ]
    manifest = {"chapters": [{"paragraphs": [{"id": "p1", "translated": "新译"}]}]}
    final = finalize_writeback_states(records, manifest)
    counts = review_report_counts(4, final)
    assert final[0]["apply_state"] == "applied"
    assert counts == {"reviewed": 4, "pass": 2, "fix_required": 1, "suggestions": 1, "applied": 1, "blocked": 0}


def test_write_error_and_manifest_mismatch_are_failed():
    record = {"id": "p1", "decision": "FIX_REQUIRED", "replacement": "新译", "apply_reason": "gate_passed"}
    manifest = {"chapters": [{"paragraphs": [{"id": "p1", "translated": "旧译"}]}]}
    assert finalize_writeback_states([record], manifest)[0]["apply_reason"] == "manifest_verification_failed"
    assert finalize_writeback_states([record], manifest, execution_error=RuntimeError("boom"))[0]["apply_reason"] == "write_failed"
