from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from translator.core.workspace import BookWorkspace, write_json
from translator.web.app import create_app
from translator.web.events import EventBroadcaster
from translator.web.task_manager import TaskManager


class WebApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)

        # Create dummy output directory
        self.output_root = self.root / "output"
        self.output_root.mkdir(parents=True, exist_ok=True)

        # Create dummy novel-translator data books directory
        self.novel_data_books = self.root / "data" / "books"
        self.novel_data_books.mkdir(parents=True, exist_ok=True)

        # Setup test book
        self.book_id = "test-novel-id"
        self.book_dir = self.novel_data_books / self.book_id
        self.book_dir.mkdir(parents=True, exist_ok=True)

        self.manifest_data = {
            "book": self.book_id,
            "title": "测试小说",
            "source_type": "epub",
            "chapters": [
                {
                    "id": "c0001",
                    "index": 0,
                    "title": "第一章 序幕",
                    "paragraphs": [
                        {"id": "p0001", "index": 0, "source": "夜が明けた。", "translated": "天亮了。"},
                        {"id": "p0002", "index": 1, "source": "冒険が始まる。", "translated": ""},
                    ],
                }
            ],
            "created_at": "2026-08-23T00:00:00Z",
            "updated_at": "2026-08-23T00:00:00Z",
        }
        write_json(self.book_dir / "manifest.json", self.manifest_data)

        # Setup workspace
        self.workspace = BookWorkspace.at(self.output_root, "测试小说")
        self.workspace.initialize(book_id=self.book_id)

        # Patch config and paths for tests
        self.config_mock = {
            "paths": {
                "output_root": str(self.output_root),
                "translation_policy": "docs/prompts/translation-policy.md",
            },
            "roles": {
                "primary_translator": "mock_primary",
                "fallback_translators": ["mock_fallback"],
                "reviewer": "mock_reviewer",
            },
            "providers": {
                "mock_primary": {"type": "antigravity", "model": "gemini-3.7-flash"},
                "mock_fallback": {"type": "opencode", "model": "muse-spark"},
            },
            "pipeline": {
                "max_cycles": 10,
                "primary_batch_max_chars": 4000,
                "layout": "horizontal",
            },
            "queue": {"source_root": "source"},
        }

        self.app = create_app()
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_health_check(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    @patch("translator.web.routes.books.get_output_root")
    @patch("translator.web.routes.books.NOVEL_TRANSLATOR_ROOT")
    def test_list_books(self, mock_novel_root: Path, mock_out_root: MagicMock) -> None:
        mock_novel_root.resolve.return_value = self.root
        mock_novel_root.__truediv__.side_effect = lambda path: self.root / path
        mock_out_root.return_value = self.output_root

        response = self.client.get("/api/v1/books")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], self.book_id)
        self.assertEqual(data[0]["name"], "测试小说")
        self.assertEqual(data[0]["total_chapters"], 1)
        self.assertEqual(data[0]["total_paragraphs"], 2)
        self.assertEqual(data[0]["translated_paragraphs"], 1)

    @patch("translator.web.routes.books.get_output_root")
    @patch("translator.web.routes.books.manifest_path")
    @patch("translator.web.routes.books.call_novel_translator")
    def test_upload_book_success(self, mock_call_tool: MagicMock, mock_manifest_path: MagicMock, mock_out_root: MagicMock) -> None:
        mock_out_root.return_value = self.output_root
        mock_manifest_path.return_value = self.book_dir / "manifest.json"
        mock_call_tool.return_value = {
            "status": "ok",
            "summary": {"book": self.book_id, "title": "测试小说"},
        }

        response = self.client.post(
            "/api/v1/books/upload",
            files={"file": ("test_novel.txt", b"\xe7\xac\xac\xe4\xb8\x80\xe7\xab\xa0", "text/plain")},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], self.book_id)
        self.assertEqual(data["name"], "测试小说")

    @patch("translator.web.routes.books.manifest_path")
    @patch("translator.web.routes.books.get_output_root")
    def test_get_book_chapters_and_detail(self, mock_out_root: MagicMock, mock_manifest_path: MagicMock) -> None:
        mock_out_root.return_value = self.output_root
        mock_manifest_path.return_value = self.book_dir / "manifest.json"

        # List chapters
        response = self.client.get(f"/api/v1/books/{self.book_id}/chapters")
        self.assertEqual(response.status_code, 200)
        chapters = response.json()
        self.assertEqual(len(chapters), 1)
        self.assertEqual(chapters[0]["id"], "c0001")
        self.assertEqual(chapters[0]["title"], "第一章 序幕")

        # Get chapter detail
        response = self.client.get(f"/api/v1/books/{self.book_id}/chapters/c0001")
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail["id"], "c0001")
        self.assertEqual(len(detail["paragraphs"]), 2)
        self.assertEqual(detail["paragraphs"][0]["translated"], "天亮了。")

    @patch("translator.web.routes.books.manifest_path")
    def test_update_paragraph_translation(self, mock_manifest_path: MagicMock) -> None:
        mock_manifest_path.return_value = self.book_dir / "manifest.json"

        response = self.client.put(
            f"/api/v1/books/{self.book_id}/paragraphs/p0002",
            json={"translated": "冒险开始了。"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

        # Verify manifest writeback
        updated = json.loads((self.book_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(updated["chapters"][0]["paragraphs"][1]["translated"], "冒险开始了。")

    @patch("translator.web.routes.knowledge.load_config")
    @patch("translator.web.routes.knowledge.manifest_path")
    def test_glossary_crud(self, mock_manifest_path: MagicMock, mock_load_config: MagicMock) -> None:
        mock_load_config.return_value = self.config_mock
        mock_manifest_path.return_value = self.book_dir / "manifest.json"

        # 1. Get initial empty glossary
        response = self.client.get(f"/api/v1/knowledge/{self.book_id}/glossary")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["terms"], [])

        # 2. Add terms
        payload = {
            "terms": [
                {"source": "モモンガ", "target": "莫莫伽", "category": "character", "confidence": 1.0, "notes": "主角"},
                {"source": "ナザリック", "target": "纳萨力克", "category": "location", "confidence": 1.0, "notes": "大坟墓"},
            ]
        }
        response = self.client.post(f"/api/v1/knowledge/{self.book_id}/glossary", json=payload)
        self.assertEqual(response.status_code, 200)
        terms = response.json()["terms"]
        self.assertEqual(len(terms), 2)
        self.assertEqual(terms[0]["source"], "モモンガ")
        self.assertEqual(terms[0]["target"], "莫莫伽")

    @patch("translator.web.routes.system.load_config")
    def test_system_config_get(self, mock_load_config: MagicMock) -> None:
        mock_load_config.return_value = self.config_mock
        response = self.client.get("/api/v1/system/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["roles"]["primary_translator"], "mock_primary")

    @patch("translator.web.routes.system.load_config")
    @patch("translator.web.routes.system.create_provider")
    def test_system_preflight(self, mock_create_provider: MagicMock, mock_load_config: MagicMock) -> None:
        mock_load_config.return_value = self.config_mock
        mock_inst = MagicMock()
        mock_inst.health_check.return_value = True
        mock_create_provider.return_value = mock_inst

        response = self.client.post("/api/v1/system/preflight")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["all_passed"])
        self.assertEqual(len(data["results"]), 2)

    def test_event_broadcaster(self) -> None:
        broadcaster = EventBroadcaster()
        broadcaster.broadcast_sync("test_event", {"message": "hello"}, book_id="book-1")
        # Ensure it handles empty subscribers gracefully without exception

    @patch("translator.web.routes.tasks.manifest_path")
    @patch("translator.web.task_manager.manifest_path")
    @patch("translator.web.task_manager.ChapterPipeline")
    def test_task_lifecycle_api(self, mock_pipeline_cls: MagicMock, mock_tm_manifest: MagicMock, mock_tasks_manifest: MagicMock) -> None:
        mock_manifest_file = self.book_dir / "manifest.json"
        mock_tm_manifest.return_value = mock_manifest_file
        mock_tasks_manifest.return_value = mock_manifest_file

        mock_pipeline_inst = MagicMock()
        mock_pipeline_inst.run_chapter.return_value = {"status": "ok"}
        mock_pipeline_inst.finalize.return_value = {"status": "exported"}
        mock_pipeline_cls.return_value = mock_pipeline_inst

        # 1. Start pipeline
        start_payload = {
            "book_id": self.book_id,
            "apply": True,
            "autonomous": True,
            "finalize": True,
            "layout": "horizontal",
        }
        res = self.client.post("/api/v1/tasks/pipeline/start", json=start_payload)
        self.assertEqual(res.status_code, 200)
        task_data = res.json()
        task_id = task_data["task_id"]
        self.assertEqual(task_data["book_id"], self.book_id)

        # 2. Get status
        res_status = self.client.get(f"/api/v1/tasks/status/{self.book_id}")
        self.assertEqual(res_status.status_code, 200)

        # 3. Pause pipeline
        res_pause = self.client.post(f"/api/v1/tasks/pipeline/pause?task_or_book_id={self.book_id}")
        self.assertEqual(res_pause.status_code, 200)

        # 4. Resume pipeline
        res_resume = self.client.post(f"/api/v1/tasks/pipeline/resume?task_or_book_id={self.book_id}")
        self.assertEqual(res_resume.status_code, 200)

        # 5. Stop pipeline
        res_stop = self.client.post(f"/api/v1/tasks/pipeline/stop?task_or_book_id={self.book_id}")
        self.assertEqual(res_stop.status_code, 200)


if __name__ == "__main__":
    unittest.main()

