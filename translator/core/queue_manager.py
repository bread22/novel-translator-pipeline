from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import threading
import time
import traceback
from typing import Any, Callable
import uuid

from translator.core.config import load_config
from translator.core.novel_tool import NOVEL_TRANSLATOR_ROOT
from translator.core.workspace import BookWorkspace, read_json, write_json

ROOT = Path(__file__).resolve().parents[2]
from translator.pipeline.chapter_pipeline import ChapterPipeline, manifest_path
from translator.providers.translator import ProviderTranslator
from translator.web.events import broadcaster
from translator.web.models import (
    EnqueueRequest,
    PipelineStartRequest,
    QueueClearRequest,
    QueueConfigUpdateRequest,
    QueueItem,
    QueueItemMoveRequest,
    QueueReorderRequest,
    QueueStatusResponse,
    TaskStatusResponse,
)

logger = logging.getLogger("translator.core.queue_manager")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QueueManager:
    """Thread-safe Queue Management and Execution Engine for multi-book batch translation."""

    def __init__(self, output_root: Path | None = None) -> None:
        self.output_root = output_root or (ROOT / "output")
        self._items: dict[str, QueueItem] = {}
        self._pending_order: list[str] = []
        self._running_threads: dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._pause_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

        # Config state
        config = load_config()
        queue_cfg = config.get("queue", {})
        self.concurrency: int = max(1, min(4, int(queue_cfg.get("concurrency", 1))))
        self.stop_on_error: bool = bool(queue_cfg.get("stop_on_error", False))
        self.is_paused: bool = False

        # Load persisted state
        self._load_state()

    @property
    def state_file(self) -> Path:
        p = self.output_root / "queue" / "queue_state.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def get_status(self) -> QueueStatusResponse:
        with self._lock:
            self._recalculate_order_indexes()
            # Order items: running first, then pending in pending_order, then completed/failed/cancelled by updated/enqueued time
            ordered_items: list[QueueItem] = []

            # 1. Running items
            running_items = [item for item in self._items.values() if item.status == "running"]
            ordered_items.extend(running_items)

            # 2. Pending items in order
            for item_id in self._pending_order:
                if item_id in self._items and self._items[item_id].status == "pending":
                    ordered_items.append(self._items[item_id])

            # 3. Finished / failed / cancelled items (reverse chronological)
            finished = [
                item
                for item in self._items.values()
                if item.status in {"completed", "failed", "cancelled", "paused"}
                and item not in ordered_items
            ]
            finished.sort(key=lambda x: x.completed_at or x.enqueued_at, reverse=True)
            ordered_items.extend(finished)

            running_cnt = sum(1 for i in self._items.values() if i.status == "running")
            pending_cnt = sum(1 for i in self._items.values() if i.status == "pending")
            completed_cnt = sum(1 for i in self._items.values() if i.status == "completed")
            failed_cnt = sum(1 for i in self._items.values() if i.status in {"failed", "cancelled"})

            return QueueStatusResponse(
                is_paused=self.is_paused,
                concurrency=self.concurrency,
                total_items=len(self._items),
                running_count=running_cnt,
                pending_count=pending_cnt,
                completed_count=completed_cnt,
                failed_count=failed_cnt,
                items=ordered_items,
            )

    def enqueue(
        self,
        book_id: str,
        options: PipelineStartRequest | None = None,
        insert_front: bool = False,
        book_name: str | None = None,
    ) -> QueueItem:
        with self._lock:
            # Check if book already has a running or pending item
            for item in self._items.values():
                if item.book_id == book_id and item.status in {"pending", "running"}:
                    logger.info("Book %s is already in queue with status %s", book_id, item.status)
                    return item

            manifest = read_json(manifest_path(book_id), default={})
            resolved_name = book_name or manifest.get("title", book_id)
            source_type = manifest.get("source_type", "epub")
            opts = options or PipelineStartRequest(book_id=book_id)

            item_id = f"qitem-{int(time.time())}-{uuid.uuid4().hex[:6]}-{book_id}"
            item = QueueItem(
                id=item_id,
                book_id=book_id,
                book_name=resolved_name,
                source_type=source_type,
                options=opts,
                status="pending",
                order_index=0,
                enqueued_at=utc_now(),
                message="已加入翻译队列，等待调度...",
            )

            self._items[item_id] = item
            if insert_front:
                self._pending_order.insert(0, item_id)
            else:
                self._pending_order.append(item_id)

            self._recalculate_order_indexes()
            self._save_state()

        self._emit_queue_updated()
        self._dispatch()
        return item

    def enqueue_batch(
        self,
        book_ids: list[str],
        options: PipelineStartRequest | None = None,
        insert_front: bool = False,
    ) -> list[QueueItem]:
        added: list[QueueItem] = []
        with self._lock:
            new_ids: list[str] = []
            for book_id in book_ids:
                # Skip if already pending or running
                if any(i.book_id == book_id and i.status in {"pending", "running"} for i in self._items.values()):
                    continue

                manifest = read_json(manifest_path(book_id), default={})
                resolved_name = manifest.get("title", book_id)
                source_type = manifest.get("source_type", "epub")
                opts = options.model_copy() if options else PipelineStartRequest(book_id=book_id)
                opts.book_id = book_id

                item_id = f"qitem-{int(time.time())}-{uuid.uuid4().hex[:6]}-{book_id}"
                item = QueueItem(
                    id=item_id,
                    book_id=book_id,
                    book_name=resolved_name,
                    source_type=source_type,
                    options=opts,
                    status="pending",
                    order_index=0,
                    enqueued_at=utc_now(),
                    message="已加入翻译队列，等待调度...",
                )
                self._items[item_id] = item
                new_ids.append(item_id)
                added.append(item)

            if insert_front:
                self._pending_order = new_ids + self._pending_order
            else:
                self._pending_order.extend(new_ids)

            self._recalculate_order_indexes()
            self._save_state()

        self._emit_queue_updated()
        self._dispatch()
        return added

    def cancel_item(self, item_id: str) -> bool:
        with self._lock:
            if item_id not in self._items:
                return False

            item = self._items[item_id]
            if item.status == "running":
                # Signal stop
                if item_id in self._stop_events:
                    self._stop_events[item_id].set()
                if item_id in self._pause_events:
                    self._pause_events[item_id].set()
                item.status = "cancelled"
                item.message = "已由用户取消终止"
                item.completed_at = utc_now()
            elif item.status == "pending":
                if item_id in self._pending_order:
                    self._pending_order.remove(item_id)
                item.status = "cancelled"
                item.message = "已移出等待队列"
                item.completed_at = utc_now()
            else:
                # Already completed / failed / cancelled, just remove
                del self._items[item_id]
                if item_id in self._pending_order:
                    self._pending_order.remove(item_id)

            self._recalculate_order_indexes()
            self._save_state()

        self._emit_queue_updated()
        self._dispatch()
        return True

    def retry_item(self, item_id: str) -> QueueItem | None:
        with self._lock:
            if item_id not in self._items:
                return None

            item = self._items[item_id]
            if item.status in {"running", "pending"}:
                return item

            item.status = "pending"
            item.retry_count += 1
            item.error_detail = None
            item.message = f"重新入队重试 (第 {item.retry_count} 次)"
            item.started_at = None
            item.completed_at = None
            item.enqueued_at = utc_now()

            if item_id not in self._pending_order:
                self._pending_order.append(item_id)

            self._recalculate_order_indexes()
            self._save_state()

        self._emit_queue_updated()
        self._dispatch()
        return item

    def move_item(self, item_id: str, direction: str) -> bool:
        with self._lock:
            if item_id not in self._pending_order or item_id not in self._items:
                return False
            if self._items[item_id].status != "pending":
                return False

            idx = self._pending_order.index(item_id)
            if direction == "top":
                self._pending_order.pop(idx)
                self._pending_order.insert(0, item_id)
            elif direction == "up":
                if idx > 0:
                    self._pending_order[idx], self._pending_order[idx - 1] = (
                        self._pending_order[idx - 1],
                        self._pending_order[idx],
                    )
            elif direction == "down":
                if idx < len(self._pending_order) - 1:
                    self._pending_order[idx], self._pending_order[idx + 1] = (
                        self._pending_order[idx + 1],
                        self._pending_order[idx],
                    )
            else:
                return False

            self._recalculate_order_indexes()
            self._save_state()

        self._emit_queue_updated()
        return True

    def reorder(self, item_ids: list[str]) -> bool:
        """Atomic reordering for drag-and-drop pending queue items."""
        with self._lock:
            # Filter valid pending items in the new requested order
            valid_new_order = [
                iid for iid in item_ids if iid in self._items and self._items[iid].status == "pending"
            ]
            # Add any pending items that were omitted to the end
            for iid in self._pending_order:
                if iid in self._items and self._items[iid].status == "pending" and iid not in valid_new_order:
                    valid_new_order.append(iid)

            self._pending_order = valid_new_order
            self._recalculate_order_indexes()
            self._save_state()

        self._emit_queue_updated()
        return True

    def pause_queue(self) -> None:
        with self._lock:
            self.is_paused = True
            self._save_state()
        broadcaster.broadcast_sync("queue_paused", {"is_paused": True})
        self._emit_queue_updated()

    def resume_queue(self) -> None:
        with self._lock:
            self.is_paused = False
            self._save_state()
        broadcaster.broadcast_sync("queue_resumed", {"is_paused": False})
        self._emit_queue_updated()
        self._dispatch()

    def clear(self, scope: str = "completed") -> int:
        cleared_count = 0
        with self._lock:
            to_remove: list[str] = []
            for item_id, item in self._items.items():
                if scope == "completed" and item.status == "completed":
                    to_remove.append(item_id)
                elif scope == "failed" and item.status in {"failed", "cancelled"}:
                    to_remove.append(item_id)
                elif scope == "all_finished" and item.status in {"completed", "failed", "cancelled"}:
                    to_remove.append(item_id)

            for item_id in to_remove:
                del self._items[item_id]
                if item_id in self._pending_order:
                    self._pending_order.remove(item_id)
                cleared_count += 1

            self._recalculate_order_indexes()
            self._save_state()

        self._emit_queue_updated()
        return cleared_count

    def update_config(self, concurrency: int | None = None, stop_on_error: bool | None = None) -> None:
        with self._lock:
            if concurrency is not None:
                self.concurrency = max(1, min(4, concurrency))
            if stop_on_error is not None:
                self.stop_on_error = stop_on_error
            self._save_state()
        self._emit_queue_updated()
        self._dispatch()

    def _recalculate_order_indexes(self) -> None:
        # Re-index all pending items 1..N
        for idx, item_id in enumerate(self._pending_order, start=1):
            if item_id in self._items:
                self._items[item_id].order_index = idx

    def _save_state(self) -> None:
        try:
            data = {
                "is_paused": self.is_paused,
                "concurrency": self.concurrency,
                "stop_on_error": self.stop_on_error,
                "pending_order": self._pending_order,
                "items": {k: v.model_dump() for k, v in self._items.items()},
                "updated_at": utc_now(),
            }
            write_json(self.state_file, data)
        except Exception as exc:
            logger.warning("Failed to save queue state: %s", exc)

    def _load_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            raw = read_json(self.state_file, default=None)
            if not isinstance(raw, dict):
                return

            self.is_paused = bool(raw.get("is_paused", False))
            self.concurrency = max(1, min(4, int(raw.get("concurrency", 1))))
            self.stop_on_error = bool(raw.get("stop_on_error", False))
            pending_raw = raw.get("pending_order", [])

            items_dict: dict[str, QueueItem] = {}
            for item_id, item_data in raw.get("items", {}).items():
                try:
                    item = QueueItem.model_validate(item_data)
                    # If it was left in running state across crash/restart, reset to pending
                    if item.status == "running":
                        item.status = "pending"
                        item.message = "服务重启恢复待调度"
                    items_dict[item_id] = item
                except Exception:
                    pass

            self._items = items_dict
            self._pending_order = [
                iid for iid in pending_raw if iid in self._items and self._items[iid].status == "pending"
            ]
            # Also catch any pending items not in pending_order
            for iid, it in self._items.items():
                if it.status == "pending" and iid not in self._pending_order:
                    self._pending_order.append(iid)

            self._recalculate_order_indexes()
            logger.info("Loaded queue state with %d items (%d pending)", len(self._items), len(self._pending_order))
        except Exception as exc:
            logger.warning("Failed to load queue state: %s", exc)

    def _emit_queue_updated(self) -> None:
        try:
            status_data = self.get_status().model_dump()
            broadcaster.broadcast_sync("queue_updated", status_data)
        except Exception as exc:
            logger.debug("Broadcast queue_updated failed: %s", exc)

    def _dispatch(self) -> None:
        """Non-blocking dispatcher loop running under lock check."""
        with self._lock:
            if self.is_paused:
                return

            running_count = sum(1 for i in self._items.values() if i.status == "running")
            available_slots = max(0, self.concurrency - running_count)
            if available_slots <= 0 or not self._pending_order:
                return

            items_to_start: list[QueueItem] = []
            for _ in range(available_slots):
                if not self._pending_order:
                    break
                next_id = self._pending_order.pop(0)
                if next_id in self._items and self._items[next_id].status == "pending":
                    item = self._items[next_id]
                    item.status = "running"
                    item.started_at = utc_now()
                    item.message = "正在初始化流水线..."
                    items_to_start.append(item)

            self._recalculate_order_indexes()
            self._save_state()

        # Start worker threads outside of lock
        for item in items_to_start:
            stop_ev = threading.Event()
            pause_ev = threading.Event()
            pause_ev.set()
            self._stop_events[item.id] = stop_ev
            self._pause_events[item.id] = pause_ev

            thread = threading.Thread(
                target=self._run_queue_worker,
                args=(item, stop_ev, pause_ev),
                daemon=True,
                name=f"QueueWorker-{item.id}",
            )
            self._running_threads[item.id] = thread
            thread.start()

            broadcaster.broadcast_sync("queue_item_started", item.model_dump(), book_id=item.book_id)

        self._emit_queue_updated()

    def _run_queue_worker(
        self,
        item: QueueItem,
        stop_event: threading.Event,
        pause_event: threading.Event,
    ) -> None:
        logger.info("Started queue worker for item %s (book: %s)", item.id, item.book_id)
        config = load_config()
        out_root = self.output_root.resolve()

        try:
            # 1. Inspect manifest and workspace
            manifest = read_json(manifest_path(item.book_id))
            if not manifest:
                raise FileNotFoundError(f"找不到书籍 manifest.json: {item.book_id}")

            book_title = manifest.get("title", item.book_id)
            workspace = BookWorkspace.at(out_root, book_title)
            workspace.initialize(book_id=item.book_id)

            chapters = manifest.get("chapters", [])
            item.total_chapters = len(chapters)
            item.message = f"共 {item.total_chapters} 章节，流水线推进中..."
            self._emit_item_progress(item)

            policy_path = (
                Path(item.options.translation_policy).resolve()
                if item.options.translation_policy
                else None
            )

            def _get_paragraph_progress() -> tuple[int, int, float]:
                m = read_json(manifest_path(item.book_id), default={})
                ch_list = m.get("chapters", [])
                tot_p = sum(len(c.get("paragraphs", [])) for c in ch_list)
                tra_p = sum(
                    sum(1 for p in c.get("paragraphs", []) if bool(str(p.get("translated", "")).strip()))
                    for c in ch_list
                )
                ratio = round(tra_p / max(1, tot_p), 3) if tot_p > 0 else 0.0
                return tra_p, tot_p, ratio

            def handle_batch_completed(batch_info: dict[str, Any]) -> None:
                b_idx = batch_info.get("batch_index", 1)
                b_paras = batch_info.get("batch_paragraphs", 0)
                rem_p = batch_info.get("remaining_pending", 0)
                ch_id = batch_info.get("chapter_id", item.current_chapter)

                t_p, tot_p, prog_ratio = _get_paragraph_progress()
                item.overall_progress = prog_ratio
                item.message = f"第 {item.current_chapter_index}/{item.total_chapters} 章 · 批次 #{b_idx} 已译 {b_paras} 段 (进度 {int(round(prog_ratio * 100))}%)"

                broadcaster.broadcast_sync(
                    "batch_completed",
                    {
                        "book_id": item.book_id,
                        "chapter_id": ch_id,
                        "chapter_index": item.current_chapter_index,
                        "total_chapters": item.total_chapters,
                        "batch_index": b_idx,
                        "batch_paragraphs": b_paras,
                        "chapter_pending_paragraphs": rem_p,
                        "translated_paragraphs": t_p,
                        "total_paragraphs": tot_p,
                        "overall_progress": prog_ratio,
                        "message": item.message,
                    },
                    book_id=item.book_id,
                )
                self._emit_item_progress(item)

            # 2. Instantiate pipeline
            pipeline = ChapterPipeline(
                book=item.book_id,
                workspace=workspace,
                manifest=manifest_path(item.book_id),
                apply=item.options.apply,
                autonomous=item.options.autonomous,
                layout=item.options.layout,
                primary_translator=item.options.primary_translator or None,
                fallback_translators=item.options.fallback_translators or None,
                reviewer=item.options.reviewer or None,
                translation_policy=policy_path,
                targeted_translator=ProviderTranslator(
                    novel_root=NOVEL_TRANSLATOR_ROOT,
                    manifest=manifest_path(item.book_id),
                ),
                on_batch_completed=handle_batch_completed,
            )

            # 3. Iterate over chapters
            for idx, chapter in enumerate(chapters, start=1):
                if stop_event.is_set():
                    item.status = "cancelled"
                    item.message = "流水线已由用户终止"
                    item.completed_at = utc_now()
                    self._emit_item_progress(item)
                    return

                pause_event.wait()
                if stop_event.is_set():
                    return

                chapter_id = chapter.get("id", f"c{idx:04d}")
                chapter_title = chapter.get("title", chapter_id)

                if pipeline.is_chapter_completed(chapter_id):
                    logger.info("Chapter %s completed, skipping", chapter_id)
                    continue

                t_p, tot_p, prog_ratio = _get_paragraph_progress()
                item.current_chapter = chapter_id
                item.current_chapter_index = idx
                item.overall_progress = prog_ratio
                item.message = f"正在处理第 {idx}/{item.total_chapters} 章：{chapter_title}"
                self._emit_item_progress(item)

                result = pipeline.run_chapter(chapter_id, cycle=idx)

                t_p, tot_p, prog_ratio = _get_paragraph_progress()
                item.overall_progress = prog_ratio

                issues_cnt = (
                    result.get("issues")
                    if result.get("issues") is not None
                    else result.get("review", {}).get("issues", 0)
                )
                fixes_cnt = (
                    result.get("fixes")
                    if result.get("fixes") is not None
                    else result.get("review", {}).get("fixes", 0)
                )
                broadcaster.broadcast_sync(
                    "chapter_completed",
                    {
                        "book_id": item.book_id,
                        "chapter_id": chapter_id,
                        "chapter_index": idx,
                        "total_chapters": item.total_chapters,
                        "issues": issues_cnt,
                        "fixes": fixes_cnt,
                        "result": result,
                    },
                    book_id=item.book_id,
                )

            # 4. Finalize
            if item.options.finalize:
                item.message = "全部章节翻译完成，正在导出与校验最终中文 EPUB..."
                self._emit_item_progress(item)
                pipeline.finalize()

            item.status = "completed"
            item.overall_progress = 1.0
            item.message = "全书翻译与审阅已完成！"
            item.completed_at = utc_now()
            broadcaster.broadcast_sync("queue_item_completed", item.model_dump(), book_id=item.book_id)
            logger.info("Queue item completed successfully: %s (%s)", item.id, item.book_name)

        except Exception as exc:
            logger.error("Queue worker failed for item %s: %s", item.id, exc, exc_info=True)
            item.status = "failed"
            item.message = f"执行出错: {exc}"
            item.error_detail = traceback.format_exc()
            item.completed_at = utc_now()
            broadcaster.broadcast_sync("queue_item_failed", item.model_dump(), book_id=item.book_id)

            if self.stop_on_error:
                self.is_paused = True
                broadcaster.broadcast_sync("queue_paused", {"reason": "stop_on_error", "failed_item": item.id})

        finally:
            self._running_threads.pop(item.id, None)
            self._stop_events.pop(item.id, None)
            self._pause_events.pop(item.id, None)

            with self._lock:
                self._save_state()

            self._emit_queue_updated()
            # Trigger dispatch to immediately pick up next queued item
            self._dispatch()

    def _emit_item_progress(self, item: QueueItem) -> None:
        broadcaster.broadcast_sync(
            "pipeline_progress",
            {
                "task_id": item.id,
                "book_id": item.book_id,
                "status": item.status,
                "overall_progress": item.overall_progress,
                "current_chapter": item.current_chapter,
                "current_chapter_index": item.current_chapter_index,
                "total_chapters": item.total_chapters,
                "message": item.message,
                "error_detail": item.error_detail,
                "started_at": item.started_at,
                "updated_at": utc_now(),
            },
            book_id=item.book_id,
        )


# Global singleton queue manager
queue_manager = QueueManager()
