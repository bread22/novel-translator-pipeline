from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from translator.core.queue_manager import QueueManager
from translator.core.workspace import write_json
from translator.web.models import PipelineStartRequest, QueueItem


class QueueManagerTests(unittest.TestCase):
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

        self.qm = QueueManager(output_root=self.output_root)
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
        new_qm = QueueManager(output_root=self.output_root)
        new_status = new_qm.get_status()
        self.assertEqual(new_status.total_items, 2)
        self.assertEqual(new_status.pending_count, 2)
        self.assertEqual([i.book_id for i in new_status.items], ["book-1", "book-2"])


if __name__ == "__main__":
    unittest.main()
