from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.book_pipeline import IterativePipeline, approved_fixes, missing_review_ids, newly_translated, validate_review_payload
from scripts.book_workspace import BookWorkspace


def manifest(translated: str = "") -> dict:
    return {
        "id": "book",
        "title": "Book",
        "source_type": "txt",
        "source_file": "source.txt",
        "chapters": [{"id": "c1", "paragraphs": [{"id": "p1", "source": "銀行員の美樹", "translated": translated}]}],
    }


class PipelineFunctionTests(unittest.TestCase):
    def test_newly_translated_only_returns_blank_to_filled(self) -> None:
        self.assertEqual(newly_translated(manifest(), manifest("银行职员美树"))[0]["id"], "p1")
        self.assertEqual(newly_translated(manifest("旧译"), manifest("新译")), [])

    def test_approved_fixes_requires_all_guards(self) -> None:
        items = [
            {"id": "a", "auto_apply": True, "confidence": 0.9, "approved_translation": "修复"},
            {"id": "b", "auto_apply": True, "confidence": 0.89, "approved_translation": "修复"},
            {"id": "c", "auto_apply": False, "confidence": 1, "approved_translation": "修复"},
        ]
        self.assertEqual([item["id"] for item in approved_fixes(items)], ["a"])
        self.assertEqual([item["id"] for item in approved_fixes(items, autonomous=True)], ["a", "c"])

    def test_review_payload_rejects_missing_ids(self) -> None:
        payload = validate_review_payload({"items": [], "term_updates": []}, {"p1"})
        self.assertEqual(payload["items"][0]["id"], "p1")

    def test_review_payload_rejects_unknown_ids(self) -> None:
        with self.assertRaisesRegex(ValueError, "未知 ID"):
            validate_review_payload(
                {"items": [{"id": "unknown"}], "term_updates": []}, {"p1"}
            )

    def test_missing_review_ids_are_detected_for_retry(self) -> None:
        self.assertEqual(missing_review_ids({"items": [{"id": "p1"}]}, {"p1", "p2"}), {"p2"})

    def test_one_cycle_translates_reviews_updates_terms_and_applies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest()), encoding="utf-8")
            workspace = BookWorkspace.at(root / "output", "女银行职员")
            calls: list[tuple[str, ...]] = []

            def tool_call(*args: str) -> dict:
                calls.append(args)
                if args[0] == "translate":
                    manifest_path.write_text(json.dumps(manifest("银行职员美树")), encoding="utf-8")
                if args[0] == "quality-report":
                    return {"status": "ok", "summary": {"translated": 1, "untranslated": 0}}
                return {"status": "ok", "summary": {"command": args[0]}}

            def reviewer(input_path: Path, output_path: Path) -> None:
                payload = json.loads(input_path.read_text())
                self.assertEqual(payload["items"][0]["id"], "p1")
                output_path.write_text(
                    json.dumps(
                        {
                            "items": [{"id": "p1", "severity": "low", "issues": ["用词"], "suggestion": "银行职员美树", "approved_translation": "银行职员美树", "auto_apply": True, "confidence": 0.99}],
                            "term_updates": [{"source": "銀行員", "target": "银行职员", "category": "title", "note": "职业", "confidence": 0.99}],
                        }
                    ),
                    encoding="utf-8",
                )

            pipeline = IterativePipeline(
                book="book", workspace=workspace, manifest=manifest_path,
                tool_call=tool_call, reviewer=reviewer, apply=True,
            )
            pipeline.initialize()
            result = pipeline.run_cycle(1)
            self.assertEqual(result["translated"], 1)
            self.assertEqual(result["term_updates"]["added"], 1)
            self.assertIn("import-terminology", [call[0] for call in calls])
            self.assertIn("apply-review-fixes", [call[0] for call in calls])
            glossary = json.loads(workspace.glossary_path.read_text())
            self.assertEqual(glossary["terms"][0]["target"], "银行职员")

    def test_finalize_exports_and_validates_completed_book(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest("完成")), encoding="utf-8")
            workspace = BookWorkspace.at(root / "output", "成品")
            calls: list[tuple[str, ...]] = []

            def tool_call(*args: str) -> dict:
                calls.append(args)
                if args[0] == "translation-status":
                    return {"status": "ok", "summary": {"pending": 0}}
                if args[0] == "export":
                    Path(args[args.index("--output") + 1]).write_bytes(b"epub")
                return {"status": "ok", "summary": {"command": args[0]}}

            pipeline = IterativePipeline(
                book="book", workspace=workspace, manifest=manifest_path,
                tool_call=tool_call, reviewer=lambda _input, _output: None,
            )
            pipeline.initialize()
            result = pipeline.finalize()
            self.assertEqual(result["status"], "exported")
            self.assertEqual([call[0] for call in calls], ["translation-status", "validate-export", "export", "validate-epub"])
            self.assertTrue(Path(result["output"]).exists())


if __name__ == "__main__":
    unittest.main()
