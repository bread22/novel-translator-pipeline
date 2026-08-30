from __future__ import annotations

import json
from pathlib import Path
import threading
import time
from typing import Any
import unittest
from unittest.mock import patch

from translator.review.models import ChapterReviewOutput
from translator.core.job_control import JobCancelled
from translator.review.reviewer import (
    _combine_chunk_reviews,
    _execute_single_segment_review,
    _review_backends,
    _update_rolling_payload,
    merge_chapter_reviews,
)


def review(label: str) -> dict:
    return {
        "checked_ids": [f"p-{label}"],
        "fixes": [
            {
                "id": f"p-{label}",
                "category": "mistranslation",
                "severity": "major",
                "confidence": 0.95,
                "replacement": f"修复-{label}",
                "auto_apply": True,
            }
        ],
        "context_findings": [],
    }


class ReviewSchemaContractTests(unittest.TestCase):
    def test_reviewer_fallbacks_are_only_explicitly_configured(self) -> None:
        with patch("translator.review.reviewer.load_config", return_value={
            "roles": {
                "reviewer": "reviewer-a",
                "fallback_reviewers": [],
                "fallback_translators": ["translator-fallback"],
            },
        }):
            self.assertEqual(_review_backends("reviewer-a"), ["reviewer-a"])

        with patch("translator.review.reviewer.load_config", return_value={
            "roles": {"reviewer": "reviewer-a", "fallback_reviewers": ["reviewer-b"]},
        }):
            self.assertEqual(_review_backends("reviewer-a"), ["reviewer-a", "reviewer-b"])

    def test_checked_in_schema_matches_pydantic_model(self) -> None:
        path = Path(__file__).resolve().parents[1] / "schemas" / "chapter-review-output.schema.json"
        checked_in = json.loads(path.read_text(encoding="utf-8"))
        generated = ChapterReviewOutput.model_json_schema()
        self.assertEqual(checked_in["$defs"], generated["$defs"])
        self.assertEqual(checked_in["properties"], generated["properties"])

    def test_three_chunks_keep_every_delta_section(self) -> None:
        merged = _combine_chunk_reviews(_combine_chunk_reviews(review("a"), review("b")), review("c"))
        self.assertEqual(merged["checked_ids"], ["p-a", "p-b", "p-c"])
        self.assertEqual(len(merged["fixes"]), 3)

    def test_dual_review_preserves_multiple_atomic_fixes_in_one_paragraph(self) -> None:
        primary = {
            "checked_ids": ["p1"],
            "fixes": [
                {
                    "id": "p1", "category": "mistranslation", "severity": "minor",
                    "source_fragment": "A", "current_fragment": "甲", "proposed_fragment": "乙",
                    "replacement": "乙 B", "confidence": 0.95,
                },
                {
                    "id": "p1", "category": "mistranslation", "severity": "minor",
                    "source_fragment": "B", "current_fragment": "B", "proposed_fragment": "丙",
                    "replacement": "A 丙", "confidence": 0.95,
                },
            ],
        }
        secondary = {
            "checked_ids": ["p1"],
            "fixes": [
                {
                    "id": "p1", "category": "mistranslation", "severity": "minor",
                    "source_fragment": "A", "current_fragment": "甲", "proposed_fragment": "乙",
                    "replacement": "乙 B", "confidence": 0.93,
                },
                {
                    "id": "p1", "category": "mistranslation", "severity": "minor",
                    "source_fragment": "B", "current_fragment": "B", "proposed_fragment": "丙",
                    "replacement": "A 丙", "confidence": 0.93,
                },
            ],
        }
        merged = merge_chapter_reviews(primary, secondary)
        self.assertEqual(len(merged["fixes"]), 2)
        self.assertTrue(all(item["consensus"] for item in merged["fixes"]))

    def test_rolling_context_applies_memory_add_and_update(self) -> None:
        base = {"current_chapter_review_context": {"active_entities": ["旧人物"]}}
        delta = {"rolling_context_delta": {"active_entities": ["新人物"], "locations": ["新宿"]}}
        rolling = _update_rolling_payload(base, delta)
        self.assertEqual(rolling["current_chapter_review_context"]["active_entities"], ["旧人物", "新人物"])
        self.assertEqual(rolling["current_chapter_review_context"]["locations"], ["新宿"])

    def test_dual_review_keeps_secondary_data_and_reporters(self) -> None:
        merged = merge_chapter_reviews(review("primary"), review("secondary"))
        fixes_by_id = {f["id"]: f for f in merged["fixes"]}
        self.assertEqual(fixes_by_id["p-primary"]["reporters"], ["primary"])
        self.assertEqual(fixes_by_id["p-secondary"]["reporters"], ["secondary"])
        self.assertEqual(merged["dual_review"]["merged_fixes_count"], 2)

    def test_dual_review_reports_each_reviewer_state(self) -> None:
        states: list[dict[str, Any]] = []
        both_started = threading.Barrier(2)

        class Provider:
            def __init__(self, backend: str) -> None:
                self.backend = backend

            def review(self, _kind, _payload, _schema, **_kwargs):
                both_started.wait(timeout=1)
                return review("primary" if self.backend == "reviewer-a" else "secondary")

        with (
            patch("translator.review.reviewer._review_backends", side_effect=lambda backend: [backend]),
            patch("translator.review.reviewer.get_provider", side_effect=Provider),
        ):
            _execute_single_segment_review(
                {},
                Path("schema.json"),
                backend="reviewer-a",
                secondary_backend="reviewer-b",
                is_dual=True,
                on_reviewer_status=states.append,
            )

        self.assertEqual(
            {(item["role"], item["backend"]) for item in states if item["status"] == "reviewing"},
            {("primary", "reviewer-a"), ("secondary", "reviewer-b")},
        )
        self.assertEqual(
            {(item["role"], item["backend"], item["status"]) for item in states[2:]},
            {
                ("primary", "reviewer-a", "completed"),
                ("secondary", "reviewer-b", "completed"),
            },
        )

    def test_cancellation_does_not_wait_for_blocked_reviewers(self) -> None:
        states: list[dict[str, Any]] = []
        both_started = threading.Event()
        release = threading.Event()
        cancelled = threading.Event()
        entered = 0
        entered_lock = threading.Lock()

        def execute(_kind, _payload, _schema, **_kwargs):
            nonlocal entered
            with entered_lock:
                entered += 1
                if entered == 2:
                    both_started.set()
            release.wait(timeout=2)
            return review("blocked")

        def cancel_check() -> None:
            if cancelled.is_set():
                raise JobCancelled("cancelled in test")

        def trigger_cancel() -> None:
            self.assertTrue(both_started.wait(timeout=1))
            cancelled.set()

        trigger = threading.Thread(target=trigger_cancel)
        trigger.start()
        started_at = time.monotonic()
        try:
            with patch("translator.review.reviewer._execute_review_with_fallbacks", side_effect=execute):
                with self.assertRaises(JobCancelled):
                    _execute_single_segment_review(
                        {}, Path("schema.json"), backend="reviewer-a",
                        secondary_backend="reviewer-b", is_dual=True,
                        on_reviewer_status=states.append,
                        cancel_check=cancel_check,
                    )
        finally:
            release.set()
            trigger.join(timeout=1)

        self.assertLess(time.monotonic() - started_at, 1)
        self.assertEqual(
            {item["role"] for item in states if item["status"] == "cancelled"},
            {"primary", "secondary"},
        )


if __name__ == "__main__":
    unittest.main()
