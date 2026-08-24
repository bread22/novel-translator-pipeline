from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from translator.core.job_manager import JobManager
from translator.core.workspace import write_json
from translator.web.models import PipelineStartRequest


class JobManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.output_root = self.root / "output"
        self.output_root.mkdir(parents=True, exist_ok=True)

        # Setup mock manifest
        self.books_dir = self.root / "data" / "books"
        self.books_dir.mkdir(parents=True, exist_ok=True)

        for b_id in ["book-1", "book-2", "book-3"]:
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

        self.qm = JobManager(output_root=self.output_root)
        self.qm.is_paused = True  # Keep paused during unit tests to control worker spawning

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_enqueue_and_status(self) -> None:
        item = self.qm.enqueue("book-1")
        self.assertEqual(item.book_id, "book-1")
        self.assertEqual(item.status, "pending")
        self.assertEqual(item.order_index, 1)

        status = self.qm.get_status()
        self.assertEqual(status.total_items, 1)
        self.assertEqual(status.pending_count, 1)
        self.assertEqual(status.running_count, 0)
        self.assertEqual(status.items[0].id, item.id)
        self.assertEqual(status.items[0].phase, "queued")
        self.assertEqual(self.qm.get_task(item.id).phase, "queued")

    def test_enqueue_duplicate(self) -> None:
        item1 = self.qm.enqueue("book-1")
        item2 = self.qm.enqueue("book-1")
        self.assertEqual(item1.id, item2.id)
        self.assertEqual(self.qm.get_status().total_items, 1)

    def test_enqueue_batch(self) -> None:
        items = self.qm.enqueue_batch(["book-1", "book-2", "book-3"])
        self.assertEqual(len(items), 3)
        status = self.qm.get_status()
        self.assertEqual(status.total_items, 3)
        self.assertEqual(status.pending_count, 3)
        self.assertEqual([i.order_index for i in status.items], [1, 2, 3])

    def test_reorder_and_drag_drop(self) -> None:
        items = self.qm.enqueue_batch(["book-1", "book-2", "book-3"])
        id1, id2, id3 = items[0].id, items[1].id, items[2].id

        # Reorder to [id3, id1, id2]
        self.qm.reorder([id3, id1, id2])
        status = self.qm.get_status()
        self.assertEqual([i.id for i in status.items], [id3, id1, id2])
        self.assertEqual([i.order_index for i in status.items], [1, 2, 3])

    def test_move_item(self) -> None:
        items = self.qm.enqueue_batch(["book-1", "book-2", "book-3"])
        id1, id2, id3 = items[0].id, items[1].id, items[2].id

        # Move item 3 to top
        self.qm.move_item(id3, "top")
        status = self.qm.get_status()
        self.assertEqual([i.id for i in status.items], [id3, id1, id2])

        # Move item 2 up
        self.qm.move_item(id2, "up")
        status = self.qm.get_status()
        self.assertEqual([i.id for i in status.items], [id3, id2, id1])

        # Move item 3 down
        self.qm.move_item(id3, "down")
        status = self.qm.get_status()
        self.assertEqual([i.id for i in status.items], [id2, id3, id1])

    def test_cancel_item(self) -> None:
        items = self.qm.enqueue_batch(["book-1", "book-2"])
        id1, id2 = items[0].id, items[1].id

        success = self.qm.cancel_item(id1)
        self.assertTrue(success)

        status = self.qm.get_status()
        self.assertEqual(status.pending_count, 1)
        self.assertEqual(status.failed_count, 1)  # Cancelled counts towards non-active/finished
        cancelled_item = next(i for i in status.items if i.id == id1)
        self.assertEqual(cancelled_item.status, "cancelled")

    def test_retry_item(self) -> None:
        item = self.qm.enqueue("book-1")
        self.qm.cancel_item(item.id)

        retried = self.qm.retry_item(item.id)
        self.assertIsNotNone(retried)
        self.assertEqual(retried.status, "pending")
        self.assertEqual(retried.retry_count, 1)

        status = self.qm.get_status()
        self.assertEqual(status.pending_count, 1)

    def test_pause_and_resume_queue(self) -> None:
        self.qm.resume_queue()
        self.assertFalse(self.qm.is_paused)

        self.qm.pause_queue()
        self.assertTrue(self.qm.is_paused)

    def test_clear_queue(self) -> None:
        items = self.qm.enqueue_batch(["book-1", "book-2"])
        id1, id2 = items[0].id, items[1].id
        self.qm.cancel_item(id1)

        # Clear failed/cancelled
        cleared = self.qm.clear("failed")
        self.assertEqual(cleared, 1)
        self.assertEqual(self.qm.get_status().total_items, 1)

    def test_update_config(self) -> None:
        self.qm.update_config(concurrency=3, stop_on_error=True)
        self.assertEqual(self.qm.concurrency, 3)
        self.assertTrue(self.qm.stop_on_error)

    def test_persistence_and_reload(self) -> None:
        items = self.qm.enqueue_batch(["book-1", "book-2"])
        self.assertTrue(self.qm.state_file.exists())

        # Create new manager instance pointing to same output_root
        new_qm = JobManager(output_root=self.output_root)
        new_status = new_qm.get_status()
        self.assertEqual(new_status.total_items, 2)
        self.assertEqual(new_status.pending_count, 2)
        self.assertEqual([i.book_id for i in new_status.items], ["book-1", "book-2"])

    def test_state_uses_v2_schema(self) -> None:
        self.qm.enqueue("book-1")
        state = json.loads(self.qm.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["schema_version"], 2)
        self.assertIn("process_id", state)

    def test_task_and_queue_entrypoints_share_one_active_job(self) -> None:
        queued = self.qm.enqueue("book-1")
        with patch.object(self.qm, "_dispatch"):
            task = self.qm.start_pipeline(PipelineStartRequest(book_id="book-1"))
        self.assertEqual(task.task_id, queued.id)
        self.assertEqual(self.qm.get_status().total_items, 1)

    def test_paused_job_keeps_concurrency_slot(self) -> None:
        first, second = self.qm.enqueue_batch(["book-1", "book-2"])
        with self.qm._lock:
            self.qm._transition_locked(first, {"pending"}, "running")
            self.qm._transition_locked(first, {"running"}, "pausing")
            self.qm._transition_locked(first, {"pausing"}, "paused")
        status = self.qm.get_status()
        self.assertEqual(status.running_count, 1)
        self.assertEqual(status.pending_count, 1)
        self.assertEqual(second.status, "pending")

    def test_restart_recovery_dispatches_when_queue_was_active(self) -> None:
        item = self.qm.enqueue("book-1")
        with self.qm._lock:
            self.qm.is_paused = False
            self.qm._transition_locked(item, {"pending"}, "running")
            self.qm._save_state()
        with patch.object(JobManager, "_dispatch") as dispatch:
            recovered = JobManager(output_root=self.output_root)
        dispatch.assert_called_once()
        recovered_item = recovered.get_status().items[0]
        self.assertEqual(recovered_item.status, "recovery_pending")
        self.assertIn("服务重启", recovered_item.recovery_reason or "")

    def test_cancelled_last_chapter_never_finalizes(self) -> None:
        started = threading.Event()
        finalized = threading.Event()

        class BlockingPipeline:
            def __init__(self, **kwargs):
                self.token = kwargs["cancellation_token"]

            def is_chapter_completed(self, _chapter_id):
                return False

            def run_chapter(self, _chapter_id, cycle):
                started.set()
                while not self.token.is_cancelled():
                    time.sleep(0.01)
                self.token.check()

            def finalize(self):
                finalized.set()

        manifest = self.books_dir / "book-1" / "manifest.json"
        write_json(manifest, {"book": "book-1", "title": "Book", "chapters": [{"id": "c1", "paragraphs": []}]})
        manager = JobManager(output_root=self.output_root)
        manager.is_paused = True
        with patch("translator.core.job_manager.manifest_path", return_value=manifest), patch(
            "translator.core.job_manager.ChapterPipeline", BlockingPipeline
        ):
            task = manager.start_pipeline(PipelineStartRequest(book_id="book-1", finalize=True))
            self.assertTrue(started.wait(2))
            stopped = manager.stop_pipeline(task.task_id)
            self.assertEqual(stopped.status, "cancelling")
            with manager._lock:
                worker = manager._running_threads.get(task.task_id)
            if worker:
                worker.join(2)
        self.assertFalse(finalized.is_set())
        self.assertEqual(manager.get_task(task.task_id).status, "cancelled")


if __name__ == "__main__":
    unittest.main()
