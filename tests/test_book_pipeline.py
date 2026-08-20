from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.book_pipeline import (
    IterativePipeline,
    approved_fixes,
    missing_checked_ids,
    missing_review_ids,
    newly_translated,
    validate_chapter_review_payload,
    validate_global_consistency_payload,
    validate_review_payload,
    validate_window_review_payload,
)
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

    def test_approved_fixes_rejects_non_objective_chapter_fixes(self) -> None:
        items = [
            {"id": "a", "category": "mistranslation", "severity": "major", "confidence": 0.95, "replacement": "修复", "auto_apply": True},
            {"id": "b", "category": "explicitness_intensity", "severity": "major", "confidence": 0.99, "replacement": "风格改写", "auto_apply": True},
            {"id": "c", "category": "terminology", "severity": "minor", "confidence": 0.99, "replacement": "轻微改写", "auto_apply": True},
        ]
        self.assertEqual([item["id"] for item in approved_fixes(items, autonomous=True)], ["a"])

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

    def test_window_requires_checked_ids_and_reviews_two_batches_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            raw = manifest()
            raw["chapters"][0]["paragraphs"] = [
                {"id": "p1", "source": "第一段", "translated": ""},
                {"id": "p2", "source": "第二段", "translated": ""},
            ]
            manifest_path.write_text(json.dumps(raw), encoding="utf-8")
            workspace = BookWorkspace.at(root / "output", "成品")
            calls: list[str] = []
            translate_count = 0

            def tool_call(*args: str) -> dict:
                nonlocal translate_count
                calls.append(args[0])
                if args[0] == "translate":
                    translate_count += 1
                    data = json.loads(manifest_path.read_text())
                    data["chapters"][0]["paragraphs"][translate_count - 1]["translated"] = f"译文{translate_count}"
                    manifest_path.write_text(json.dumps(data), encoding="utf-8")
                if args[0] == "quality-report":
                    return {"status": "ok", "summary": {"translated": 2, "untranslated": 0}}
                return {"status": "ok", "summary": {"command": args[0]}}

            reviewer_calls = 0

            def reviewer(input_path: Path, output_path: Path) -> None:
                nonlocal reviewer_calls
                reviewer_calls += 1
                payload = json.loads(input_path.read_text())
                ids = [item["id"] for item in payload["items"]]
                output_path.write_text(
                    json.dumps(
                        {
                            "checked_ids": ids,
                            "issues": [{"id": "p2", "severity": "low", "issues": ["病句"], "suggestion": "", "approved_translation": "修正译文", "auto_apply": True, "confidence": 0.99}],
                            "term_updates": [],
                        }
                    ),
                    encoding="utf-8",
                )

            pipeline = IterativePipeline(
                book="book", workspace=workspace, manifest=manifest_path,
                tool_call=tool_call, window_reviewer=reviewer, apply=True, autonomous=True,
                review_char_limit=10000,
            )
            pipeline.initialize()
            result = pipeline.run_window(1, 2)
            self.assertEqual(result["translated"], 2)
            self.assertEqual(result["reviewed"], 2)
            self.assertEqual(reviewer_calls, 1)
            self.assertIn("apply-review-fixes", calls)

    def test_window_checked_id_validation_rejects_unknown_ids(self) -> None:
        payload = {"checked_ids": ["unknown"], "issues": [], "term_updates": []}
        self.assertEqual(missing_checked_ids(payload, {"p1"}), {"p1"})
        with self.assertRaisesRegex(ValueError, "未知 ID"):
            validate_window_review_payload(payload, {"p1"})

    def test_chapter_validation_requires_exact_checked_ids(self) -> None:
        payload = {
            "checked_ids": ["p1"],
            "fixes": [],
            "glossary_delta": {"add": [], "update": [], "conflicts": []},
            "memory_delta": {"add": [], "update": [], "conflicts": []},
            "chapter_state": {"summary": "", "important_changes": []},
        }
        with self.assertRaisesRegex(ValueError, "缺少 ID"):
            validate_chapter_review_payload(payload, {"p1", "p2"})

    def test_global_consistency_validation_requires_all_chapters(self) -> None:
        with self.assertRaisesRegex(ValueError, "缺少章节 ID"):
            validate_global_consistency_payload({"checked_chapters": ["c1"], "conflicts": [], "recommendations": []}, {"c1", "c2"})

    def test_translation_failure_retries_failed_batch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest()), encoding="utf-8")
            workspace = BookWorkspace.at(root / "output", "成品")
            calls: list[str] = []
            translate_count = 0

            def tool_call(*args: str) -> dict:
                nonlocal translate_count
                calls.append(args[0])
                if args[0] == "translate":
                    translate_count += 1
                    if translate_count == 2:
                        manifest_path.write_text(json.dumps(manifest("译文")), encoding="utf-8")
                if args[0] == "failed-batches":
                    return {"status": "warning" if translate_count == 1 else "ok", "summary": {"failed": 1 if translate_count == 1 else 0}}
                if args[0] == "translation-status":
                    return {"status": "ok", "summary": {"pending": 0 if translate_count == 2 else 1}}
                return {"status": "ok", "summary": {"command": args[0]}}

            pipeline = IterativePipeline(
                book="book", workspace=workspace, manifest=manifest_path,
                tool_call=tool_call, translate_retries=3,
            )
            pipeline.initialize()
            result = pipeline._translate_only(1)
            self.assertEqual([item["id"] for item in result["items"]], ["p1"])
            self.assertEqual(translate_count, 2)
            self.assertIn("retry-failed", calls)

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

    def test_chapter_translates_until_complete_and_reviews_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            raw = manifest()
            raw["chapters"][0]["id"] = "c1"
            raw["chapters"][0]["paragraphs"] = [
                {"id": "p1", "source": "第一段", "translated": ""},
                {"id": "p2", "source": "第二段", "translated": ""},
            ]
            manifest_path.write_text(json.dumps(raw), encoding="utf-8")
            workspace = BookWorkspace.at(root / "output", "成品")
            calls: list[tuple[str, ...]] = []
            translate_count = 0

            def tool_call(*args: str) -> dict:
                nonlocal translate_count
                calls.append(args)
                if args[0] == "translate":
                    translate_count += 1
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    data["chapters"][0]["paragraphs"][translate_count - 1]["translated"] = f"译文{translate_count}"
                    manifest_path.write_text(json.dumps(data), encoding="utf-8")
                if args[0] == "apply-review-fixes":
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    data["chapters"][0]["paragraphs"][1]["translated"] = "修正译文"
                    manifest_path.write_text(json.dumps(data), encoding="utf-8")
                if args[0] == "quality-report":
                    return {"status": "ok", "summary": {"translated": 2, "untranslated": 0}}
                return {"status": "ok", "summary": {"command": args[0]}}

            reviewer_calls = 0

            def chapter_reviewer(input_path: Path, output_path: Path) -> None:
                nonlocal reviewer_calls
                reviewer_calls += 1
                payload = json.loads(input_path.read_text(encoding="utf-8"))
                self.assertEqual([item["id"] for item in payload["items"]], ["p1", "p2"])
                output_path.write_text(json.dumps({
                    "checked_ids": ["p1", "p2"],
                    "fixes": [{"id": "p2", "category": "mistranslation", "severity": "major", "confidence": 0.99, "reason": "动作错误", "replacement": "修正译文", "auto_apply": True}],
                    "glossary_delta": {"add": [{"source": "第一段", "target": "译文一", "category": "other", "note": "测试", "confidence": 0.99}], "update": [], "conflicts": []},
                    "memory_delta": {"add": [{"key": "fact-1", "value": "持续事实", "category": "fact", "note": "测试", "confidence": 0.99}], "update": [], "conflicts": []},
                    "chapter_state": {"summary": "章节摘要", "important_changes": ["状态变化"]},
                }, ensure_ascii=False), encoding="utf-8")

            pipeline = IterativePipeline(
                book="book", workspace=workspace, manifest=manifest_path,
                tool_call=tool_call, chapter_reviewer=chapter_reviewer,
                apply=True, autonomous=True, max_chapter_batches=5,
            )
            pipeline.initialize()
            result = pipeline.run_chapter("c1", 1)
            self.assertEqual(result["translated"], 2)
            self.assertEqual(result["reviewed"], 2)
            self.assertEqual(reviewer_calls, 1)
            self.assertEqual(translate_count, 2)
            self.assertIn("apply-review-fixes", [call[0] for call in calls])
            memory = json.loads(workspace.book_memory_path.read_text(encoding="utf-8"))
            self.assertEqual(memory["entries"][0]["key"], "fact-1")
            state = json.loads((workspace.chapter_states_dir / "c1.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "reviewed")

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
            self.assertEqual([call[0] for call in calls], ["translation-status", "failed-batches", "validate-export", "export", "validate-epub"])
            self.assertTrue(Path(result["output"]).exists())


if __name__ == "__main__":
    unittest.main()
