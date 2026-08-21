from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from translator.core.report import generate_work_report


class WorkReportTests(unittest.TestCase):
    def test_generates_fixed_yaml_with_provider_and_review_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            (root / "reviews").mkdir()
            (root / "reports").mkdir()
            (root / "data" / "translation-provenance.json").write_text(
                json.dumps({"items": {"p1": {"provider": "antigravity"}, "p2": {"provider": "lmstudio"}}}), encoding="utf-8"
            )
            (root / "data" / "provider-diagnostics.json").write_text(
                json.dumps({"attempts": [{"provider": "antigravity", "reason": "content_filter", "ids": ["p2"]}]}), encoding="utf-8"
            )
            (root / "reviews" / "c0001-output.json").write_text(
                json.dumps({"checked_ids": ["p1", "p2"], "fixes": [{"id": "p2", "category": "omission"}]}), encoding="utf-8"
            )
            (root / "reviews" / "c0001-approved-fixes.json").write_text(
                json.dumps({"items": [{"id": "p2"}]}), encoding="utf-8"
            )
            manifest = {"title": "Book", "chapters": [{"paragraphs": [{"id": "p1", "translated": "a"}, {"id": "p2", "translated": "b"}]}]}
            path = generate_work_report(
                workspace=root,
                book="book",
                primary_translator="antigravity",
                fallback_translator="lmstudio",
                reviewer="opencode",
                novel_root=root,
                manifest=manifest,
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("schema_version: 1", text)
            self.assertIn("paragraphs: 1", text)
            self.assertIn("fallback_reasons:", text)
            self.assertIn("fixes_applied: 1", text)
            self.assertIn("fix_categories_reported:", text)
            self.assertIn("  primary:", text)
            self.assertIn("  fallback:", text)
            self.assertIn("    omission: 1", text)


if __name__ == "__main__":
    unittest.main()
