from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from translator.core.workspace import BookWorkspace
from translator.pipeline.chapter_pipeline import ChapterPipeline
from translator.web.models import PipelineStartRequest
from translator.core.job_manager import JobManager


def _dummy_manifest(book_id: str, title: str) -> dict:
    return {
        "id": book_id,
        "title": title,
        "source_type": "txt",
        "source_file": "source.txt",
        "chapters": [
            {
                "id": "c0001",
                "title": "第一章",
                "index": 1,
                "paragraphs": [
                    {"id": "c0001-p00001", "source": "女教師 翔子と高校生", "translated": ""},
                    {"id": "c0001-p00002", "source": "雨宮慶", "translated": ""},
                ],
            },
            {
                "id": "c0002",
                "title": "第二章",
                "index": 2,
                "paragraphs": [
                    {"id": "c0002-p00001", "source": "放課後の教室で", "translated": ""},
                ],
            },
        ],
    }


class _MockResponse:
    def __init__(self, data: dict, status: int = 200) -> None:
        self.data = data
        self.status = status

    def read(self) -> bytes:
        return json.dumps(self.data).encode("utf-8")

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> _MockResponse:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def _mock_reviewer(input_path: Path, output_path: Path, **_kwargs: object) -> None:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    ids = [item["id"] for item in data.get("items", [])]
    output_path.write_text(
        json.dumps({
            "checked_ids": ids,
            "fixes": [],
            "glossary_delta": {"add": [], "update": [], "conflicts": []},
            "memory_delta": {"add": [], "update": [], "conflicts": []},
            "chapter_state": {"summary": "章节完成", "important_changes": []},
        }),
        encoding="utf-8",
    )


class PipelineE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_root = self.root / "output"
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.novel_data_root = self.root / "novel_data"
        self.book_id = "test-book-e2e"
        self.book_dir = self.novel_data_root / "data" / "books" / self.book_id
        self.book_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_file = self.book_dir / "manifest.json"
        self.manifest_file.write_text(
            json.dumps(_dummy_manifest(self.book_id, "测试小说"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @patch("translator.providers.openai_provider.urlopen")
    def test_chapter_pipeline_e2e_with_provider_translator(self, mock_urlopen: MagicMock) -> None:
        """Test ChapterPipeline executes chapter translation using real ProviderTranslator end-to-end."""
        def urlopen_side_effect(req, *args, **kwargs):
            payload = json.loads(req.data.decode("utf-8"))
            user_prompt = payload["messages"][-1]["content"]
            input_items = json.loads(user_prompt)["items"]
            translated_items = [
                {"id": item["id"], "text": f"译文：{item['id']}"}
                for item in input_items
            ]
            response_json = {
                "choices": [
                    {
                        "message": {"content": json.dumps({"items": translated_items}, ensure_ascii=False)},
                        "finish_reason": "stop",
                    }
                ]
            }
            return _MockResponse(response_json)

        mock_urlopen.side_effect = urlopen_side_effect

        workspace = BookWorkspace.at(self.output_root, "测试小说")
        workspace.initialize(book_id=self.book_id)

        pipeline = ChapterPipeline(
            book=self.book_id,
            workspace=workspace,
            manifest=self.manifest_file,
            chapter_reviewer=_mock_reviewer,
            apply=True,
            autonomous=True,
        )

        # Run Chapter 1
        res1 = pipeline.run_chapter("c0001", cycle=1)
        self.assertEqual(res1["chapter_id"], "c0001")

        # Verify manifest was atomically updated with translated text
        manifest_data = json.loads(self.manifest_file.read_text(encoding="utf-8"))
        c1_paragraphs = manifest_data["chapters"][0]["paragraphs"]
        self.assertEqual(c1_paragraphs[0]["translated"], "译文：c0001-p00001")
        self.assertEqual(c1_paragraphs[1]["translated"], "译文：c0001-p00002")

        # Run Chapter 2
        res2 = pipeline.run_chapter("c0002", cycle=2)
        self.assertEqual(res2["chapter_id"], "c0002")
        manifest_data = json.loads(self.manifest_file.read_text(encoding="utf-8"))
        c2_paragraphs = manifest_data["chapters"][1]["paragraphs"]
        self.assertEqual(c2_paragraphs[0]["translated"], "译文：c0002-p00001")

    @patch("translator.pipeline.chapter_pipeline.run_chapter_review", side_effect=_mock_reviewer)
    @patch("translator.core.job_manager.manifest_path")
    @patch("translator.providers.openai_provider.urlopen")
    def test_job_manager_runs_pipeline_worker_e2e(
        self, mock_urlopen: MagicMock, mock_manifest_path: MagicMock, mock_rev: MagicMock
    ) -> None:
        """Test JobManager starts worker thread and executes ChapterPipeline to completion without errors."""
        mock_manifest_path.return_value = self.manifest_file

        def urlopen_side_effect(req, *args, **kwargs):
            payload = json.loads(req.data.decode("utf-8"))
            user_prompt = payload["messages"][-1]["content"]
            input_items = json.loads(user_prompt)["items"]
            translated_items = [
                {"id": item["id"], "text": f"译文：{item['id']}"}
                for item in input_items
            ]
            response_json = {
                "choices": [
                    {
                        "message": {"content": json.dumps({"items": translated_items}, ensure_ascii=False)},
                        "finish_reason": "stop",
                    }
                ]
            }
            return _MockResponse(response_json)

        mock_urlopen.side_effect = urlopen_side_effect

        manager = JobManager(output_root=self.output_root)
        req = PipelineStartRequest(
            book_id=self.book_id,
            apply=True,
            autonomous=True,
            finalize=False,
        )

        task = manager.start_pipeline(req, output_root=self.output_root)
        self.assertEqual(task.book_id, self.book_id)

        # Wait for thread execution
        with manager._lock:
            worker_thread = manager._running_threads.get(task.task_id)
        if worker_thread:
            worker_thread.join(timeout=10.0)
        with manager._lock:
            running_task = manager._items.get(task.task_id)
        self.assertIsNotNone(running_task)

        self.assertEqual(running_task.status, "completed")
        self.assertEqual(running_task.total_chapters, 2)
        self.assertEqual(running_task.overall_progress, 1.0)
        self.assertIn("完成", running_task.message)

        # Check manifest data
        manifest_data = json.loads(self.manifest_file.read_text(encoding="utf-8"))
        self.assertTrue(all(p["translated"] for ch in manifest_data["chapters"] for p in ch["paragraphs"]))

    @patch("translator.providers.openai_provider.urlopen")
    def test_pipeline_fallback_recovery_when_primary_fails(self, mock_urlopen: MagicMock) -> None:
        """Test ChapterPipeline recovers gracefully when primary translator fails (e.g. 500 error)."""
        call_count = 0

        def urlopen_side_effect(req, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            payload = json.loads(req.data.decode("utf-8"))
            model = payload.get("model", "")
            if "nemotron" in model:
                import urllib.error
                raise urllib.error.HTTPError(req.full_url, 500, "Internal Server Error", {}, None)
            
            user_prompt = payload["messages"][-1]["content"]
            input_items = json.loads(user_prompt)["items"]
            translated_items = [
                {"id": item["id"], "text": f"Fallback译文：{item['id']}"}
                for item in input_items
            ]
            response_json = {
                "choices": [
                    {
                        "message": {"content": json.dumps({"items": translated_items}, ensure_ascii=False)},
                        "finish_reason": "stop",
                    }
                ]
            }
            return _MockResponse(response_json)

        mock_urlopen.side_effect = urlopen_side_effect

        workspace = BookWorkspace.at(self.output_root, "测试小说-容灾")
        workspace.initialize(book_id=self.book_id)

        pipeline = ChapterPipeline(
            book=self.book_id,
            workspace=workspace,
            manifest=self.manifest_file,
            chapter_reviewer=_mock_reviewer,
            primary_translator="nemotron",
            fallback_translators=["gemini_lite", "deepseek"],
            apply=True,
            autonomous=True,
        )

        res = pipeline.run_chapter("c0001", cycle=1)
        self.assertEqual(res["chapter_id"], "c0001")

        # Verify fallback translated text was written
        manifest_data = json.loads(self.manifest_file.read_text(encoding="utf-8"))
        p1 = manifest_data["chapters"][0]["paragraphs"][0]
        self.assertEqual(p1["translated"], "Fallback译文：c0001-p00001")


if __name__ == "__main__":
    unittest.main()
