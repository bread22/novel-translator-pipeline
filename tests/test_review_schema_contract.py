from __future__ import annotations

import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from translator.review.models import ChapterReviewOutput
from translator.review.reviewer import (
    _combine_chunk_reviews,
    _execute_single_segment_review,
    _update_rolling_payload,
    merge_chapter_reviews,
)


def review(label: str) -> dict:
    return {
        "checked_ids": [f"p-{label}"],
        "fixes": [],
        "glossary_delta": {
            "add": [{"source": f"add-{label}", "target": f"新增-{label}"}],
            "update": [{"source": "shared", "target": f"更新-{label}"}],
            "conflicts": [{"key": f"g-{label}", "existing_value": "旧", "proposed_value": "新"}],
        },
        "memory_delta": {
            "add": [{"key": f"memory-{label}", "value": f"事实-{label}"}],
            "update": [{"key": "shared-memory", "value": f"更新-{label}"}],
            "conflicts": [{"key": f"m-{label}", "existing_value": "旧", "proposed_value": "新"}],
        },
        "chapter_state": {"summary": f"summary-{label}", "important_changes": [label]},
    }


class ReviewSchemaContractTests(unittest.TestCase):
    def test_checked_in_schema_matches_pydantic_model(self) -> None:
        path = Path(__file__).resolve().parents[1] / "schemas" / "chapter-review-output.schema.json"
        checked_in = json.loads(path.read_text(encoding="utf-8"))
        generated = ChapterReviewOutput.model_json_schema()
        self.assertEqual(checked_in["$defs"], generated["$defs"])
        self.assertEqual(checked_in["properties"], generated["properties"])

    def test_three_chunks_keep_every_delta_section(self) -> None:
        merged = _combine_chunk_reviews(_combine_chunk_reviews(review("a"), review("b")), review("c"))
        self.assertEqual(len(merged["glossary_delta"]["add"]), 3)
        self.assertEqual(len(merged["glossary_delta"]["conflicts"]), 3)
        self.assertEqual(len(merged["memory_delta"]["add"]), 3)
        self.assertEqual(len(merged["memory_delta"]["conflicts"]), 3)
        self.assertEqual(merged["glossary_delta"]["update"][0]["target"], "更新-c")

    def test_rolling_context_applies_memory_add_and_update(self) -> None:
        base = {"glossary": [{"source": "shared", "target": "旧"}], "book_memory": {"entries": [{"key": "shared-memory", "value": "旧"}]}}
        rolling = _update_rolling_payload(base, review("next"))
        memory = {item["key"]: item for item in rolling["book_memory"]["entries"]}
        glossary = {item["source"]: item for item in rolling["glossary"]}
        self.assertEqual(memory["shared-memory"]["value"], "更新-next")
        self.assertIn("memory-next", memory)
        self.assertEqual(glossary["shared"]["target"], "更新-next")

    def test_dual_review_keeps_secondary_data_and_reporters(self) -> None:
        merged = merge_chapter_reviews(review("primary"), review("secondary"))
        memory_keys = {item["key"] for item in merged["memory_delta"]["add"]}
        self.assertEqual(memory_keys, {"memory-primary", "memory-secondary"})
        shared = merged["memory_delta"]["update"][0]
        self.assertEqual(set(shared["reporters"]), {"primary", "secondary"})
        self.assertEqual(len(merged["glossary_delta"]["conflicts"]), 2)

    def test_dual_review_reports_each_reviewer_state(self) -> None:
        states: list[dict[str, str]] = []
        both_started = threading.Barrier(2)

        def execute(_kind, _payload, _schema, *, backend=None, **_kwargs):
            both_started.wait(timeout=1)
            return review("primary" if backend == "reviewer-a" else "secondary")

        with patch("translator.review.reviewer._execute_review_with_fallbacks", side_effect=execute):
            _execute_single_segment_review(
                {},
                Path("schema.json"),
                backend="reviewer-a",
                secondary_backend="reviewer-b",
                is_dual=True,
                on_reviewer_status=states.append,
            )

        self.assertEqual(states[:2], [
            {"role": "primary", "backend": "reviewer-a", "status": "reviewing"},
            {"role": "secondary", "backend": "reviewer-b", "status": "reviewing"},
        ])
        self.assertEqual(
            {(item["role"], item["backend"], item["status"]) for item in states[2:]},
            {
                ("primary", "reviewer-a", "completed"),
                ("secondary", "reviewer-b", "completed"),
            },
        )


if __name__ == "__main__":
    unittest.main()
