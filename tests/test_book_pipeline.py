from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from translator.core.workspace import BookWorkspace
from translator.pipeline.chapter_pipeline import (
    IterativePipeline,
    newly_translated,
)
from translator.review.reviewer import (
    approved_fixes,
    merge_chapter_reviews,
    missing_checked_ids,
    validate_chapter_review_payload,
    validate_global_consistency_payload,
    verify_applied_fixes,
)


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

    def test_approved_fixes_rejects_japanese_kana_hallucinations(self) -> None:
        items = [
            {
                "id": "p1",
                "category": "mistranslation",
                "severity": "major",
                "confidence": 0.98,
                "replacement": "车子已经来到那栋公寓的すぐそば。",
                "auto_apply": True,
            },
            {
                "id": "p2",
                "category": "mistranslation",
                "severity": "major",
                "confidence": 0.98,
                "replacement": "从事カタカナ职业的独身女性的房间。",
                "auto_apply": True,
            },
            {
                "id": "p3",
                "category": "mistranslation",
                "severity": "major",
                "confidence": 0.98,
                "replacement": "车子已经开到了那栋公寓的近旁。",
                "auto_apply": True,
            },
        ]
        approved = approved_fixes(items, autonomous=True)
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["id"], "p3")
        self.assertEqual(approved[0]["replacement"], "车子已经开到了那栋公寓的近旁。")

    def test_merge_chapter_reviews_consensus_and_deduplication(self) -> None:
        rev_a = {
            "checked_ids": ["p1", "p2"],
            "fixes": [
                {"id": "p1", "category": "mistranslation", "severity": "major", "confidence": 0.92, "replacement": "修复A"},
                {"id": "p2", "category": "omission", "severity": "major", "confidence": 0.88, "replacement": "修复A2"},
            ],
            "glossary_delta": {"add": [{"source": "東京", "target": "东京"}]},
            "memory_delta": {"k1": "v1"},
            "chapter_state": {"summary": "s1"},
        }
        rev_b = {
            "checked_ids": ["p2", "p3"],
            "fixes": [
                {"id": "p1", "category": "mistranslation", "severity": "major", "confidence": 0.95, "replacement": "修复B (更准确)"},
                {"id": "p3", "category": "subject_object", "severity": "major", "confidence": 0.91, "replacement": "修复B3"},
            ],
            "glossary_delta": {"add": [{"source": "東京", "target": "东京"}, {"source": "京都", "target": "京都"}]},
            "memory_delta": {"k2": "v2"},
            "chapter_state": {"summary": "s2"},
        }
        merged = merge_chapter_reviews(rev_a, rev_b)
        self.assertEqual(merged["checked_ids"], ["p1", "p2", "p3"])
        
        fixes_by_id = {f["id"]: f for f in merged["fixes"]}
        # p1 was reported by both A and B -> consensus: True, confidence >= 0.95, chosen higher confidence replacement
        self.assertTrue(fixes_by_id["p1"]["consensus"])
        self.assertEqual(fixes_by_id["p1"]["confidence"], 0.95)
        self.assertEqual(fixes_by_id["p1"]["replacement"], "修复B (更准确)")
        
        # p2 was only reported by A
        self.assertFalse(fixes_by_id["p2"]["consensus"])
        self.assertEqual(fixes_by_id["p2"]["reporters"], ["primary"])
        
        # p3 was only reported by B
        self.assertFalse(fixes_by_id["p3"]["consensus"])
        self.assertEqual(fixes_by_id["p3"]["reporters"], ["secondary"])
        
        # Glossary deduplication
        self.assertEqual(len(merged["glossary_delta"]["add"]), 2)

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

    def test_verify_applied_fixes(self) -> None:
        man = manifest("修复译文")
        man["chapters"][0]["paragraphs"][0]["id"] = "p1"
        verify_applied_fixes(man, [{"id": "p1", "replacement": "修复译文"}])
        with self.assertRaises(ValueError):
            verify_applied_fixes(man, [{"id": "p1", "replacement": "不匹配译文"}])

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

    def test_two_level_fallback_recovers_when_primary_and_fb1_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            raw = manifest()
            raw["chapters"][0]["id"] = "c1"
            raw["chapters"][0]["paragraphs"] = [
                {"id": "p1", "source": "第一段", "translated": ""},
                {"id": "p2", "source": "第二段", "translated": ""},
            ]
            manifest_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            workspace = BookWorkspace.at(root / "output", "成品")
            calls: list[tuple[str, tuple[str, ...]]] = []

            def targeted(provider: str, book: str, ids: list[str], **_kwargs: object) -> dict:
                calls.append((provider, tuple(ids)))
                if provider == "antigravity":
                    return {"status": "error", "error": "provider_blocked: content_filter"}
                if provider == "opencode":
                    return {"status": "error", "error": "provider_blocked: content_filter"}
                if provider == "lmstudio":
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    for chapter in data["chapters"]:
                        for item in chapter["paragraphs"]:
                            if item["id"] in ids:
                                item["translated"] = f"{provider}-{item['id']}"
                    manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    return {"status": "ok", "summary": {"translated": len(ids)}}
                return {"status": "error", "error": "unknown"}

            pipeline = IterativePipeline(
                book="book", workspace=workspace, manifest=manifest_path,
                tool_call=lambda *_args: {"status": "ok"},
                targeted_translator=targeted,
                primary_translator="antigravity",
                fallback_translators=["opencode", "lmstudio"],
                split_on_content_filter=True,
                primary_batch_max_chars=100,
            )
            pipeline.initialize()
            result = pipeline._translate_chapter("c1", 1)
            self.assertEqual(result["translated"], 2)
            self.assertEqual(calls[0], ("antigravity", ("p1", "p2")))
            self.assertEqual(
                calls[1:],
                [
                    ("antigravity", ("p1",)),
                    ("opencode", ("p1",)),
                    ("lmstudio", ("p1",)),
                    ("antigravity", ("p2",)),
                    ("opencode", ("p2",)),
                    ("lmstudio", ("p2",)),
                ],
            )
            provenance = json.loads((workspace.data_dir / "translation-provenance.json").read_text(encoding="utf-8"))
            self.assertEqual(provenance["items"]["p1"]["provider"], "lmstudio")
            self.assertIn("_fb2", provenance["items"]["p1"]["reason"])

    def test_opencode_can_be_selected_as_primary_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            raw = manifest()
            raw["chapters"][0]["id"] = "c1"
            raw["chapters"][0]["paragraphs"] = [{"id": "p1", "source": "第一段", "translated": ""}]
            manifest_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            workspace = BookWorkspace.at(root / "output", "成品")
            calls: list[str] = []

            def targeted(provider: str, _book: str, ids: list[str], **_kwargs: object) -> dict:
                calls.append(provider)
                if provider == "opencode":
                    return {"status": "error", "error": "provider_blocked: content_filter"}
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                data["chapters"][0]["paragraphs"][0]["translated"] = "fallback译文"
                manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return {"status": "ok", "summary": {"translated": len(ids)}}

            pipeline = IterativePipeline(
                book="book", workspace=workspace, manifest=manifest_path,
                tool_call=lambda *_args: {"status": "ok"}, targeted_translator=targeted,
                primary_translator="opencode",
                fallback_translators=["lmstudio"],
                primary_batch_max_chars=100,
            )
            pipeline.initialize()
            result = pipeline._translate_chapter("c1", 1)
            self.assertEqual(result["translated"], 1)
            self.assertEqual(calls, ["opencode", "lmstudio"])

    def test_finalize_exports_and_validates_completed_book(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest("完成")), encoding="utf-8")
            workspace = BookWorkspace.at(root / "output", "成品")
            calls: list[tuple[str, ...]] = []

            def tool_call(*args: str) -> dict:
                calls.append(args)
                if args[0] == "export":
                    Path(args[args.index("--output") + 1]).write_bytes(b"epub")
                return {"status": "ok", "summary": {"command": args[0]}}

            pipeline = IterativePipeline(
                book="book", workspace=workspace, manifest=manifest_path,
                tool_call=tool_call, chapter_reviewer=lambda _input, _output: None,
                translated_root=root / "translated", layout="preserve",
            )
            pipeline.initialize()
            result = pipeline.finalize()
            self.assertEqual(result["status"], "exported")
            self.assertEqual([call[0] for call in calls], ["export", "validate-epub"])
            self.assertTrue(Path(result["output"]).exists())
            self.assertTrue(Path(result["translated_output"]).exists())
            self.assertEqual(Path(result["output"]).read_bytes(), Path(result["translated_output"]).read_bytes())


    def test_max_provider_split_depth_limits_binary_splits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            raw = {
                "id": "book",
                "title": "Book",
                "source_type": "txt",
                "source_file": "source.txt",
                "chapters": [{
                    "id": "c1",
                    "paragraphs": [
                        {"id": "p1", "source": "段落1", "translated": ""},
                        {"id": "p2", "source": "段落2", "translated": ""},
                        {"id": "p3", "source": "段落3", "translated": ""},
                        {"id": "p4", "source": "段落4", "translated": ""},
                    ],
                }],
            }
            manifest_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
            workspace = BookWorkspace.at(root / "output", "成品")
            calls: list[tuple[str, tuple[str, ...]]] = []

            def targeted(provider: str, _book: str, ids: list[str], **_kwargs: object) -> dict:
                calls.append((provider, tuple(ids)))
                if provider == "antigravity":
                    return {"status": "error", "error": "provider_blocked: content_filter"}
                if provider == "opencode":
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                    for chapter in data["chapters"]:
                        for item in chapter["paragraphs"]:
                            if item["id"] in ids:
                                item["translated"] = f"opencode-{item['id']}"
                    manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                    return {"status": "ok", "summary": {"translated": len(ids)}}
                return {"status": "error", "error": "unknown"}

            # Test split depth = 1: depth 0 (4 items) -> split 1 -> depth 1 (2 items) -> fallback
            pipeline = IterativePipeline(
                book="book", workspace=workspace, manifest=manifest_path,
                tool_call=lambda *_args: {"status": "ok"},
                targeted_translator=targeted,
                primary_translator="antigravity",
                fallback_translators=["opencode"],
                split_on_content_filter=True,
                max_provider_split_depth=1,
                primary_batch_max_chars=1000,
            )
            pipeline.initialize()
            result = pipeline._translate_chapter("c1", 1)
            self.assertEqual(result["translated"], 4)
            self.assertEqual(calls, [
                ("antigravity", ("p1", "p2", "p3", "p4")),
                ("antigravity", ("p1", "p2")),
                ("opencode", ("p1", "p2")),
                ("antigravity", ("p3", "p4")),
                ("opencode", ("p3", "p4")),
            ])

    def test_immediate_fallback_on_content_filter_without_split(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            raw = {
                "title": "测试",
                "chapters": [{
                    "id": "c1",
                    "title": "第1章",
                    "paragraphs": [
                        {"id": "p1", "source": "源1", "translated": ""},
                        {"id": "p2", "source": "源2", "translated": ""},
                        {"id": "p3", "source": "源3", "translated": ""},
                        {"id": "p4", "source": "源4", "translated": ""},
                    ],
                }],
            }
            manifest_path.write_text(json.dumps(raw), encoding="utf-8")
            workspace = BookWorkspace.at(root / "output", "测试")
            calls: list[tuple[str, tuple[str, ...]]] = []

            def targeted(provider: str, _book: str, ids: list[str], **_kwargs: Any) -> dict:
                calls.append((provider, tuple(ids)))
                if provider == "opencode":
                    return {"status": "blocked", "reason": "content_filter", "error": "explicit sexual content"}
                if provider == "gemini":
                    # gemini translates all
                    current = json.loads(manifest_path.read_text(encoding="utf-8"))
                    for p in current["chapters"][0]["paragraphs"]:
                        if p["id"] in ids:
                            p["translated"] = f"译-{p['id']}"
                    manifest_path.write_text(json.dumps(current), encoding="utf-8")
                    return {"status": "ok", "summary": {"translated": len(ids)}}
                return {"status": "error", "error": "unknown"}

            pipeline = IterativePipeline(
                book="book", workspace=workspace, manifest=manifest_path,
                tool_call=lambda *_args: {"status": "ok"},
                targeted_translator=targeted,
                primary_translator="opencode",
                fallback_translators=["gemini", "muse"],
                split_on_content_filter=False,
                primary_batch_max_chars=1000,
            )
            pipeline.initialize()
            result = pipeline._translate_chapter("c1", 1)
            self.assertEqual(result["translated"], 4)
            # opencode blocked -> immediately fallback to gemini with all 4 items, NO binary split!
            self.assertEqual(calls, [
                ("opencode", ("p1", "p2", "p3", "p4")),
                ("gemini", ("p1", "p2", "p3", "p4")),
            ])

    def test_run_chapter_review_chunks_large_chapter_and_forwards_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_path = root / "c1-input.json"
            output_path = root / "c1-output.json"

            input_data = {
                "book": "test-book",
                "chapter_id": "c1",
                "chapter_title": "第一章",
                "translation_policy": "政策",
                "book_memory": {"characters": [], "world_settings": [], "plot_hints": [], "entries": []},
                "previous_chapter_state": {},
                "glossary": [],
                "items": [
                    {"id": "p1", "source": "源1", "translated": "译1"},
                    {"id": "p2", "source": "源2", "translated": "译2"},
                    {"id": "p3", "source": "源3", "translated": "译3"},
                    {"id": "p4", "source": "源4", "translated": "译4"},
                    {"id": "p5", "source": "源5", "translated": "译5"},
                ],
            }
            input_path.write_text(json.dumps(input_data), encoding="utf-8")

            # Mock _execute_review_with_fallbacks
            from unittest.mock import patch
            from translator.review.reviewer import run_chapter_review

            def mock_execute(*args: Any, **kwargs: Any) -> dict:
                payload = kwargs.get("input_payload") or (args[1] if len(args) > 1 else {})
                chunk_items = payload.get("items", [])
                cids = [item["id"] for item in chunk_items]
                return {
                    "checked_ids": cids,
                    "fixes": [
                        {
                            "id": cids[0],
                            "category": "mistranslation",
                            "severity": "major",
                            "confidence": 0.95,
                            "reason": "更准确",
                            "replacement": f"优化-{cids[0]}",
                            "auto_apply": True,
                        }
                    ],
                    "glossary_delta": {"add": [{"source": f"term-{cids[0]}", "target": f"词-{cids[0]}"}]},
                    "memory_delta": {"characters": [{"name": f"char-{cids[0]}"}]},
                    "chapter_state": {"summary": f"总结-{','.join(cids)}"},
                }

            with patch("translator.review.reviewer._execute_review_with_fallbacks", side_effect=mock_execute):
                run_chapter_review(input_path, output_path, chunk_size=2)

            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["checked_ids"], ["p1", "p2", "p3", "p4", "p5"])
            self.assertEqual(len(result["fixes"]), 3)  # One per chunk (chunk1: p1, chunk2: p3, chunk3: p5)
            self.assertEqual(len(result["glossary_delta"]["add"]), 3)
            self.assertEqual(len(result["memory_delta"]["characters"]), 3)
            self.assertIn("总结-p1,p2", result["chapter_state"]["summary"])
            self.assertIn("总结-p5", result["chapter_state"]["summary"])

    def test_run_chapter_review_adaptive_split_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            input_path = root / "c1-input.json"
            output_path = root / "c1-output.json"

            input_data = {
                "book": "test-book",
                "chapter_id": "c1",
                "chapter_title": "第一章",
                "translation_policy": "政策",
                "book_memory": {},
                "previous_chapter_state": {},
                "glossary": [],
                "items": [
                    {"id": "p1", "source": "源1", "translated": "译1"},
                    {"id": "p2", "source": "源2", "translated": "译2"},
                    {"id": "p3", "source": "源3", "translated": "译3"},
                    {"id": "p4", "source": "源4", "translated": "译4"},
                ],
            }
            input_path.write_text(json.dumps(input_data), encoding="utf-8")

            from unittest.mock import patch
            from translator.review.reviewer import run_chapter_review

            def mock_failing_execute(*args: Any, **kwargs: Any) -> dict:
                payload = kwargs.get("input_payload") or (args[1] if len(args) > 1 else {})
                chunk_items = payload.get("items", [])
                cids = [item["id"] for item in chunk_items]
                # Fail if asked to review more than 2 items at once (simulating timeout on large input)
                if len(cids) > 2:
                    raise RuntimeError("Prefill Timeout on large chunk")
                return {
                    "checked_ids": cids,
                    "fixes": [],
                    "glossary_delta": {"add": []},
                    "memory_delta": {},
                    "chapter_state": {"summary": f"完成-{','.join(cids)}"},
                }

            with patch("translator.review.reviewer._execute_review_with_fallbacks", side_effect=mock_failing_execute):
                # chunk_size=4 will initially fail on all 4 items, then adaptively binary split into 2 + 2 and succeed!
                run_chapter_review(input_path, output_path, chunk_size=4)

            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["checked_ids"], ["p1", "p2", "p3", "p4"])


if __name__ == "__main__":
    unittest.main()
