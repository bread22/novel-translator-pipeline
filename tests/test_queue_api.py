from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from translator.core.job_manager import job_manager
from translator.core.workspace import write_json
from translator.web.app import create_app


@unittest.skipIf(sys.version_info >= (3, 15), "Python 3.15+ is outside the declared compatibility range")
class QueueApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_root = self.root / "output"
        self.output_root.mkdir(parents=True, exist_ok=True)

        # Reset singleton queue manager for testing
        job_manager.output_root = self.output_root
        job_manager.is_paused = True  # Keep paused during API tests
        with job_manager._lock:
            job_manager._items.clear()
            job_manager._pending_order.clear()

        # Setup test books in novel translator data
        self.books_dir = self.root / "data" / "books"
        self.books_dir.mkdir(parents=True, exist_ok=True)
        for b_id in ["test-book-1", "test-book-2"]:
            b_path = self.books_dir / b_id
            b_path.mkdir(parents=True, exist_ok=True)
            write_json(
                b_path / "manifest.json",
                {
                    "book": b_id,
                    "title": f"小说 {b_id}",
                    "source_type": "epub",
                    "chapters": [],
                },
            )

        self.app = create_app()
        self._client_context = TestClient(self.app)
        self.client = self._client_context.__enter__()

    def tearDown(self) -> None:
        self._client_context.__exit__(None, None, None)
        self.temp_dir.cleanup()

    def test_get_queue_empty(self) -> None:
        res = self.client.get("/api/v1/queue")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["total_items"], 0)
        self.assertEqual(data["items"], [])

    def test_enqueue_items(self) -> None:
        payload = {"book_ids": ["test-book-1", "test-book-2"]}
        res = self.client.post("/api/v1/queue/items", json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["total_items"], 2)
        self.assertEqual(data["pending_count"], 2)
        self.assertEqual(len(data["items"]), 2)

    def test_reorder_queue(self) -> None:
        self.client.post("/api/v1/queue/items", json={"book_ids": ["test-book-1", "test-book-2"]})
        status_res = self.client.get("/api/v1/queue").json()
        id1, id2 = status_res["items"][0]["id"], status_res["items"][1]["id"]

        # Reorder [id2, id1]
        reorder_res = self.client.post("/api/v1/queue/reorder", json={"item_ids": [id2, id1]})
        self.assertEqual(reorder_res.status_code, 200)
        reordered_data = reorder_res.json()
        self.assertEqual([i["id"] for i in reordered_data["items"]], [id2, id1])

    def test_move_queue_item(self) -> None:
        self.client.post("/api/v1/queue/items", json={"book_ids": ["test-book-1", "test-book-2"]})
        status_res = self.client.get("/api/v1/queue").json()
        id2 = status_res["items"][1]["id"]

        move_res = self.client.post(f"/api/v1/queue/items/{id2}/move", json={"direction": "top"})
        self.assertEqual(move_res.status_code, 200)
        data = move_res.json()
        self.assertEqual(data["items"][0]["id"], id2)

    def test_cancel_and_retry(self) -> None:
        self.client.post("/api/v1/queue/items", json={"book_ids": ["test-book-1"]})
        status_res = self.client.get("/api/v1/queue").json()
        item_id = status_res["items"][0]["id"]

        # Cancel
        del_res = self.client.delete(f"/api/v1/queue/items/{item_id}")
        self.assertEqual(del_res.status_code, 200)
        data = del_res.json()
        self.assertEqual(data["pending_count"], 0)
        self.assertEqual(data["failed_count"], 1)

        # Retry
        retry_res = self.client.post(f"/api/v1/queue/items/{item_id}/retry")
        self.assertEqual(retry_res.status_code, 200)
        retried_data = retry_res.json()
        self.assertEqual(retried_data["pending_count"], 1)
        self.assertEqual(retried_data["items"][0]["status"], "pending")

    def test_pause_and_resume(self) -> None:
        p_res = self.client.post("/api/v1/queue/pause")
        self.assertEqual(p_res.status_code, 200)
        self.assertTrue(p_res.json()["is_paused"])

        r_res = self.client.post("/api/v1/queue/resume")
        self.assertEqual(r_res.status_code, 200)
        self.assertFalse(r_res.json()["is_paused"])

    def test_update_config(self) -> None:
        cfg_res = self.client.post("/api/v1/queue/config", json={"concurrency": 2, "stop_on_error": True})
        self.assertEqual(cfg_res.status_code, 200)
        data = cfg_res.json()
        self.assertEqual(data["concurrency"], 2)


if __name__ == "__main__":
    unittest.main()
