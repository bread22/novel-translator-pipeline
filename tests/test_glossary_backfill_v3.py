from __future__ import annotations

from translator.glossary.backfill import affected_paragraph_ids, run_targeted_backfill


def test_revision_targets_only_source_or_old_translation_matches() -> None:
    manifest = {"chapters": [{"id": "c1", "paragraphs": [
        {"id": "p1", "source": "雨宮慶来了", "translated": "旧译来了"},
        {"id": "p2", "source": "无关", "translated": "无关"},
        {"id": "p3", "source": "别的", "translated": "旧译在这里"},
    ]}]}
    revision = {"source": "雨宮慶", "baseline_target": "旧译", "new_target": "新译"}
    assert affected_paragraph_ids(manifest, revision) == ["p1", "p3"]


def test_backfill_reports_changed_unchanged_and_failed() -> None:
    manifest = {"chapters": [{"id": "c1", "paragraphs": [
        {"id": "p1", "source": "雨宮慶", "translated": "旧译"},
        {"id": "p2", "source": "雨宮慶", "translated": "旧译"},
    ]}]}
    revision = {"source": "雨宮慶", "baseline_target": "旧译", "new_target": "新译"}
    result = run_targeted_backfill(manifest, revision, rewrite=lambda item_id, _p: "新译" if item_id == "p1" else "すぐ")
    assert result.changed == ["p1"]
    assert result.failed == ["p2"]
    assert manifest["chapters"][0]["paragraphs"][0]["translated"] == "新译"
