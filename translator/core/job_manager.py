from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
import time
import traceback
from typing import Any
import uuid

from translator.core.config import load_config
from translator.core.job_control import CancellationToken, JobCancelled, PauseGate
from translator.core.novel_tool import NOVEL_TRANSLATOR_ROOT
from translator.core.paths import PathResolver
from translator.core.state_migrations import migrate_queue_state_v1
from translator.core.workspace import BookWorkspace, read_json, write_json
from translator.pipeline.chapter_pipeline import ChapterPipeline, manifest_path
from translator.providers.translator import ProviderTranslator
from translator.web.events import broadcaster
from translator.web.models import (
    PipelineStartRequest,
    QueueItem,
    QueueStatusResponse,
    TaskStatusResponse,
)

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger("translator.core.job_manager")

ACTIVE_STATUSES = {"pending", "running", "pausing", "paused", "cancelling", "recovery_pending"}
SLOT_STATUSES = {"running", "pausing", "paused", "cancelling"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ALLOWED_TRANSITIONS = {
    "pending": {"running", "cancelled"},
    "recovery_pending": {"running", "cancelled"},
    "running": {"pausing", "cancelling", "completed", "failed"},
    "pausing": {"paused", "cancelling", "failed"},
    "paused": {"running", "cancelling", "failed"},
    "cancelling": {"cancelled"},
    "failed": {"pending"},
    "cancelled": {"pending"},
    "completed": set(),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobManager:
    """Thread-safe Queue Management and Execution Engine for multi-book batch translation."""

    def __init__(self, output_root: Path | None = None) -> None:
        try:
            config = load_config()
        except FileNotFoundError:
            # Importing the wheel (including creating the FastAPI app for a
            # health/version probe) must not require a deployment-local config.
            # Operational commands still validate config.toml when they use it.
            config = {}
        self.output_root = output_root or (
            PathResolver.for_config().output_root(config)
            if config
            else (Path.cwd() / "output").resolve()
        )
        self._items: dict[str, QueueItem] = {}
        self._pending_order: list[str] = []
        self._running_threads: dict[str, threading.Thread] = {}
        self._stop_events: dict[str, threading.Event] = {}
        self._pause_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self.process_id = uuid.uuid4().hex
        self.history_limit = 200

        # Config state
        queue_cfg = config.get("queue", {})
        self.concurrency: int = max(1, min(4, int(queue_cfg.get("concurrency", 1))))
        self.stop_on_error: bool = bool(queue_cfg.get("stop_on_error", False))
        self.is_paused: bool = True

        # Load persisted state
        self._load_state()
        if not self.is_paused:
            self._dispatch()

    @property
    def state_file(self) -> Path:
        p = self.output_root / "jobs" / "job_state.v2.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def legacy_state_file(self) -> Path:
        return self.output_root / "queue" / "queue_state.json"

    def get_status(self) -> QueueStatusResponse:
        with self._lock:
            self._recalculate_order_indexes()
            # Order items: running first, then pending in pending_order, then completed/failed/cancelled by updated/enqueued time
            ordered_items: list[QueueItem] = []

            # 1. Running items
            running_items = [item for item in self._items.values() if item.status in SLOT_STATUSES]
            ordered_items.extend(running_items)

            # 2. Pending items in order
            for item_id in self._pending_order:
                if item_id in self._items and self._items[item_id].status in {"pending", "recovery_pending"}:
                    ordered_items.append(self._items[item_id])

            # 3. Finished / failed / cancelled items (reverse chronological)
            finished = [
                item
                for item in self._items.values()
                if item.status in TERMINAL_STATUSES
                and item not in ordered_items
            ]
            finished.sort(key=lambda x: x.completed_at or x.enqueued_at, reverse=True)
            ordered_items.extend(finished)

            running_cnt = sum(1 for i in self._items.values() if i.status in SLOT_STATUSES)
            pending_cnt = sum(1 for i in self._items.values() if i.status in {"pending", "recovery_pending"})
            completed_cnt = sum(1 for i in self._items.values() if i.status == "completed")
            failed_cnt = sum(1 for i in self._items.values() if i.status == "failed")

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

    def _find_item_locked(self, task_or_book_id: str) -> QueueItem | None:
        item = self._items.get(task_or_book_id)
        if item is not None:
            return item
        matches = [candidate for candidate in self._items.values() if candidate.book_id == task_or_book_id]
        matches.sort(key=lambda candidate: candidate.enqueued_at, reverse=True)
        return next((candidate for candidate in matches if candidate.status in ACTIVE_STATUSES), matches[0] if matches else None)

    def _transition_locked(self, item: QueueItem, expected: set[str], target: str) -> bool:
        if item.status not in expected or target not in ALLOWED_TRANSITIONS.get(item.status, set()):
            return False
        item.status = target
        item.updated_at = utc_now()
        return True

    def transition(self, item_id: str, expected: set[str], target: str) -> bool:
        with self._lock:
            item = self._items.get(item_id)
            changed = bool(item and self._transition_locked(item, expected, target))
            if changed:
                self._save_state()
        if changed:
            self._emit_queue_updated()
        return changed

    @staticmethod
    def _as_task(item: QueueItem) -> TaskStatusResponse:
        return TaskStatusResponse(
            task_id=item.id,
            book_id=item.book_id,
            status=item.status,
            phase=item.phase,
            reviewer_states=item.reviewer_states,
            reviewer_details=item.reviewer_details,
            overall_progress=item.overall_progress,
            current_chapter=item.current_chapter,
            current_chapter_index=item.current_chapter_index,
            total_chapters=item.total_chapters,
            message=item.message,
            error_detail=item.error_detail,
            started_at=item.started_at,
            updated_at=item.updated_at or item.completed_at or item.started_at,
        )

    def get_task(self, task_or_book_id: str) -> TaskStatusResponse | None:
        with self._lock:
            item = self._find_item_locked(task_or_book_id)
            return self._as_task(item) if item else None

    def list_tasks(self) -> list[TaskStatusResponse]:
        with self._lock:
            return [self._as_task(item) for item in sorted(self._items.values(), key=lambda value: value.enqueued_at, reverse=True)]

    def start_pipeline(self, request: PipelineStartRequest, output_root: Path | None = None) -> TaskStatusResponse:
        if output_root is not None:
            self.output_root = output_root
        item = self.enqueue(request.book_id, options=request)
        with self._lock:
            self.is_paused = False
            self._save_state()
        self._dispatch()
        return self._as_task(item)

    def pause_pipeline(self, task_or_book_id: str) -> TaskStatusResponse | None:
        with self._lock:
            item = self._find_item_locked(task_or_book_id)
            if item is None:
                return None
            if item.status != "running":
                return None
            self._transition_locked(item, {"running"}, "pausing")
            gate = self._pause_events.get(item.id)
            if gate:
                gate.clear()
            item.message = "正在等待安全暂停点..."
            self._save_state()
            response = self._as_task(item)
        self._emit_queue_updated()
        return response

    def resume_pipeline(self, task_or_book_id: str) -> TaskStatusResponse | None:
        with self._lock:
            item = self._find_item_locked(task_or_book_id)
            if item is None:
                return None
            if item.status != "paused":
                return None
            gate = self._pause_events.get(item.id)
            if gate:
                gate.set()
            self._transition_locked(item, {"paused"}, "running")
            item.message = "继续推进中..."
            self._save_state()
            response = self._as_task(item)
        broadcaster.broadcast_sync("pipeline_resumed", response.model_dump(), book_id=item.book_id)
        self._emit_queue_updated()
        return response

    def stop_pipeline(self, task_or_book_id: str) -> TaskStatusResponse | None:
        emit_stopped = False
        with self._lock:
            item = self._find_item_locked(task_or_book_id)
            if item is None:
                return None
            if item.status not in {"pending", "recovery_pending", "running", "pausing", "paused"}:
                return None
            if item.status in {"pending", "recovery_pending"}:
                if item.id in self._pending_order:
                    self._pending_order.remove(item.id)
                self._transition_locked(item, {item.status}, "cancelled")
                item.completed_at = utc_now()
                item.message = "已从等待队列取消"
                emit_stopped = True
            elif item.status in {"running", "pausing", "paused"}:
                stop_event = self._stop_events.get(item.id)
                pause_event = self._pause_events.get(item.id)
                if stop_event:
                    stop_event.set()
                if pause_event:
                    pause_event.set()
                self._transition_locked(item, {item.status}, "cancelling")
                item.message = "正在安全取消..."
            self._save_state()
            response = self._as_task(item)
        if emit_stopped:
            broadcaster.broadcast_sync("pipeline_stopped", response.model_dump(), book_id=item.book_id)
        self._emit_queue_updated()
        if emit_stopped:
            self._dispatch()
        return response

    def active_items_for_book(self, book_id: str) -> list[QueueItem]:
        with self._lock:
            return [item.model_copy(deep=True) for item in self._items.values() if item.book_id == book_id and item.status in ACTIVE_STATUSES]

    def cancel_book_and_wait(self, book_id: str, timeout: float = 30.0) -> bool:
        active = self.active_items_for_book(book_id)
        for item in active:
            self.stop_pipeline(item.id)
        deadline = time.monotonic() + timeout
        for item in active:
            with self._lock:
                thread = self._running_threads.get(item.id)
            if thread:
                thread.join(max(0.0, deadline - time.monotonic()))
        with self._lock:
            return not any(item.book_id == book_id and item.status in SLOT_STATUSES | {"cancelling"} for item in self._items.values())

    def enqueue(
        self,
        book_id: str,
        options: PipelineStartRequest | None = None,
        insert_front: bool = False,
        book_name: str | None = None,
    ) -> QueueItem:
        with self._lock:
            # Enforce one active job per book across every entry point.
            for item in self._items.values():
                if item.book_id == book_id and item.status in ACTIVE_STATUSES:
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
                message="已加入待办队列，点击「启动队列」开始调度...",
                updated_at=utc_now(),
                process_id=self.process_id,
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
                if any(i.book_id == book_id and i.status in ACTIVE_STATUSES for i in self._items.values()):
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
                    message="已加入待办队列，点击「启动队列」开始调度...",
                    updated_at=utc_now(),
                    process_id=self.process_id,
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
        return self.stop_pipeline(item_id) is not None

    def retry_item(self, item_id: str) -> QueueItem | None:
        with self._lock:
            if item_id not in self._items:
                return None

            item = self._items[item_id]
            if item.status in ACTIVE_STATUSES:
                return item
            if item.status not in {"failed", "cancelled"}:
                return None
            self._transition_locked(item, {item.status}, "pending")
            item.retry_count += 1
            item.error_detail = None
            item.message = f"重新入队重试 (第 {item.retry_count} 次)"
            item.started_at = None
            item.completed_at = None
            item.enqueued_at = utc_now()
            item.updated_at = utc_now()
            item.recovery_reason = None
            item.process_id = self.process_id

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
            if self._items[item_id].status not in {"pending", "recovery_pending"}:
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
                iid for iid in item_ids if iid in self._items and self._items[iid].status in {"pending", "recovery_pending"}
            ]
            # Add any pending items that were omitted to the end
            for iid in self._pending_order:
                if iid in self._items and self._items[iid].status in {"pending", "recovery_pending"} and iid not in valid_new_order:
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
        self._prune_history_locked()
        data = {
            "schema_version": 2,
            "process_id": self.process_id,
            "is_paused": self.is_paused,
            "concurrency": self.concurrency,
            "stop_on_error": self.stop_on_error,
            "pending_order": self._pending_order,
            "items": {k: v.model_dump() for k, v in self._items.items()},
            "updated_at": utc_now(),
        }
        write_json(self.state_file, data)

    def _load_state(self) -> None:
        if not self.state_file.exists() and self.legacy_state_file.is_file():
            report = migrate_queue_state_v1(
                self.legacy_state_file,
                self.state_file,
                apply=True,
                process_id=self.process_id,
            )
            logger.info(
                "Migrated queue state v1 to v2: %d/%d items, backup=%s",
                report["items_after"],
                report["items_before"],
                report["backup"],
            )
        if not self.state_file.exists():
            return
        try:
            raw = read_json(self.state_file, default=None)
            if not isinstance(raw, dict) or raw.get("schema_version") != 2:
                return

            self.is_paused = bool(raw.get("is_paused", True))
            self.concurrency = max(1, min(4, int(raw.get("concurrency", 1))))
            self.stop_on_error = bool(raw.get("stop_on_error", False))
            pending_raw = raw.get("pending_order", [])

            items_dict: dict[str, QueueItem] = {}
            for item_id, item_data in raw.get("items", {}).items():
                try:
                    item = QueueItem.model_validate(item_data)
                    if item.status in SLOT_STATUSES | {"cancelling", "pausing"}:
                        item.status = "recovery_pending"
                        item.recovery_reason = f"服务重启：原状态 {item_data.get('status')}"
                        item.process_id = self.process_id
                        item.message = "服务重启恢复待调度"
                    items_dict[item_id] = item
                except Exception:
                    pass

            self._items = items_dict
            self._pending_order = [
                iid for iid in pending_raw if iid in self._items and self._items[iid].status in {"pending", "recovery_pending"}
            ]
            # Interrupted / recovering items must be placed at the FRONT of pending_order so they resume first
            recovering_items = [
                iid for iid, it in self._items.items()
                if it.status == "recovery_pending" and iid not in self._pending_order
            ]
            self._pending_order = recovering_items + self._pending_order

            # Also catch any other pending items not in pending_order
            for iid, it in self._items.items():
                if it.status == "pending" and iid not in self._pending_order:
                    self._pending_order.append(iid)

            self._recalculate_order_indexes()
            logger.info("Loaded queue state with %d items (%d pending)", len(self._items), len(self._pending_order))
        except Exception as exc:
            logger.warning("Failed to load queue state: %s", exc)

    def _prune_history_locked(self) -> None:
        terminal = sorted(
            (item for item in self._items.values() if item.status in TERMINAL_STATUSES),
            key=lambda item: item.completed_at or item.updated_at or item.enqueued_at,
            reverse=True,
        )
        for item in terminal[self.history_limit :]:
            self._items.pop(item.id, None)
            if item.id in self._pending_order:
                self._pending_order.remove(item.id)

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

            running_count = sum(1 for i in self._items.values() if i.status in SLOT_STATUSES)
            available_slots = max(0, self.concurrency - running_count)
            if available_slots <= 0 or not self._pending_order:
                return

            items_to_start: list[QueueItem] = []
            for _ in range(available_slots):
                if not self._pending_order:
                    break
                next_id = self._pending_order.pop(0)
                if next_id in self._items and self._items[next_id].status in {"pending", "recovery_pending"}:
                    item = self._items[next_id]
                    self._transition_locked(item, {item.status}, "running")
                    item.started_at = utc_now()
                    item.process_id = self.process_id
                    item.phase = "initializing"
                    item.message = "正在初始化流水线..."
                    items_to_start.append(item)

            self._recalculate_order_indexes()
            self._save_state()

        # Start worker threads outside of lock
        for item in items_to_start:
            stop_ev = threading.Event()
            pause_ev = threading.Event()
            pause_ev.set()

            thread = threading.Thread(
                target=self._run_queue_worker,
                args=(item, stop_ev, pause_ev),
                daemon=True,
                name=f"QueueWorker-{item.id}",
            )
            with self._lock:
                self._stop_events[item.id] = stop_ev
                self._pause_events[item.id] = pause_ev
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
        out_root = self.output_root.resolve()
        cancellation = CancellationToken(stop_event)
        pause_gate = PauseGate(pause_event)

        def checkpoint(boundary: str) -> None:
            item.checkpoint = {"boundary": boundary, "chapter": item.current_chapter, "updated_at": utc_now()}
            item.updated_at = utc_now()
            paused_payload: dict[str, Any] | None = None
            if not pause_event.is_set():
                with self._lock:
                    if item.status == "pausing":
                        self._transition_locked(item, {"pausing"}, "paused")
                        item.message = "已暂停；当前 worker 槽位仍保留"
                        self._save_state()
                        paused_payload = self._as_task(item).model_dump()
                if paused_payload is not None:
                    broadcaster.broadcast_sync("pipeline_paused", paused_payload, book_id=item.book_id)
                    self._emit_queue_updated()
            pause_gate.wait(cancellation)
            cancellation.check()

        try:
            checkpoint("worker_started")
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
                if rem_p > 0:
                    item.message = f"第 {item.current_chapter_index}/{item.total_chapters} 章 · 正在准备批次 #{b_idx + 1} (本章剩余 {rem_p} 段)..."
                else:
                    item.message = f"第 {item.current_chapter_index}/{item.total_chapters} 章 · 章节翻译完成，准备进入一致性审阅..."

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
                        "message": f"批次 #{b_idx} 翻译完成 (已译 {b_paras} 段)",
                    },
                    book_id=item.book_id,
                )
                self._emit_item_progress(item)

            def handle_phase_changed(phase_info: dict[str, Any]) -> None:
                phase = str(phase_info.get("phase", "")).strip()
                if phase not in {"translating", "reviewing"}:
                    return
                chapter_id = str(phase_info.get("chapter_id", item.current_chapter))
                with self._lock:
                    item.phase = phase
                    if phase == "translating":
                        item.reviewer_states = {"primary": "standby", "secondary": "standby"}
                        item.reviewer_details = {}
                    elif phase == "reviewing":
                        item.reviewer_states = {"primary": "pending", "secondary": "pending"}
                        item.reviewer_details = {}
                    if chapter_id:
                        item.current_chapter = chapter_id
                    action = "翻译" if phase == "translating" else "审阅"
                    item.message = f"正在{action}第 {item.current_chapter_index}/{item.total_chapters} 章：{chapter_id}"
                    item.updated_at = utc_now()
                    self._save_state()
                    payload = self._as_task(item).model_dump()
                broadcaster.broadcast_sync("pipeline_phase_changed", payload, book_id=item.book_id)
                self._emit_item_progress(item)

            def handle_reviewer_status(status_info: dict[str, Any]) -> None:
                role = str(status_info.get("role", "")).strip()
                status = str(status_info.get("status", "")).strip()
                if role not in {"primary", "secondary"} or status not in {"standby", "pending", "reviewing", "retry_wait", "retrying", "completed", "failed", "cancelled"}:
                    return
                with self._lock:
                    item.reviewer_states = {**item.reviewer_states, role: status}
                    detail = {key: value for key, value in status_info.items() if key != "role"}
                    item.reviewer_details = {**item.reviewer_details, role: detail}
                    role_label = "主审" if role == "primary" else "副审"
                    backend = str(status_info.get("backend", "")).strip() or "-"
                    attempt = int(status_info.get("attempt", 0) or 0)
                    chunk_index = int(status_info.get("chunk_index", 0) or 0)
                    total_chunks = int(status_info.get("total_chunks", 0) or 0)
                    split_path = str(status_info.get("split_path", "root"))
                    action = {
                        "reviewing": "审阅中",
                        "retry_wait": "退让等待",
                        "retrying": "正在重试",
                        "completed": "已完成",
                        "failed": "调用失败",
                        "cancelled": "已取消",
                    }.get(status, status)
                    parts = [f"{role_label} {backend} {action}"]
                    if attempt:
                        parts.append(f"尝试 #{attempt}")
                    if status == "retry_wait":
                        delay = float(status_info.get("retry_delay_seconds", 0) or 0)
                        retry_index = int(status_info.get("retry_index", 0) or 0)
                        retry_total = int(status_info.get("retry_total", 0) or 0)
                        reason = str(status_info.get("retry_reason", "瞬态故障"))
                        parts.append(f"{reason} 退让 {delay:.1f} 秒（{retry_index}/{retry_total}）")
                    if chunk_index and total_chunks:
                        parts.append(f"分块 {chunk_index}/{total_chunks}")
                    if split_path and split_path != "root":
                        parts.append(f"子段 {split_path}")
                    item.message = " · ".join(parts)
                    item.updated_at = utc_now()
                    self._save_state()
                    payload = {
                        **self._as_task(item).model_dump(),
                        "reviewer_role": role,
                        "reviewer_backend": status_info.get("backend", ""),
                        "reviewer_status": status,
                        **{key: value for key, value in status_info.items() if key not in {"role", "backend", "status"}},
                    }
                broadcaster.broadcast_sync("pipeline_reviewer_status", payload, book_id=item.book_id)

            def handle_translation_attempt(attempt_info: dict[str, Any]) -> None:
                broadcaster.broadcast_sync("translation_attempt", attempt_info, book_id=item.book_id)

            def handle_fallback_triggered(route_info: dict[str, Any]) -> None:
                broadcaster.broadcast_sync("fallback_triggered", route_info, book_id=item.book_id)

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
                    glossary_path=workspace.glossary_path,
                ),
                on_batch_completed=handle_batch_completed,
                on_phase_changed=handle_phase_changed,
                on_reviewer_status=handle_reviewer_status,
                on_translation_attempt=handle_translation_attempt,
                on_fallback_triggered=handle_fallback_triggered,
                cancellation_token=cancellation,
                pause_gate=pause_gate,
            )

            # 3. Iterate over chapters
            max_cycles = max(0, int(item.options.max_cycles))
            for idx, chapter in enumerate(chapters[:max_cycles], start=1):
                checkpoint("before_chapter")

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
                checkpoint("after_chapter")

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
                checkpoint("before_finalize")
                with self._lock:
                    item.phase = "finalizing"
                    item.message = "全部章节翻译完成，正在导出与校验最终中文 EPUB..."
                    item.updated_at = utc_now()
                    self._save_state()
                    payload = self._as_task(item).model_dump()
                broadcaster.broadcast_sync("pipeline_phase_changed", payload, book_id=item.book_id)
                self._emit_item_progress(item)
                pipeline.finalize()
                checkpoint("after_finalize")

            cancellation.check()
            with self._lock:
                if not self._transition_locked(item, {"running"}, "completed"):
                    raise JobCancelled("完成前任务状态已改变")
                item.overall_progress = 1.0 if max_cycles >= len(chapters) else item.overall_progress
                item.phase = "idle"
                item.message = "全书翻译与审阅已完成！" if max_cycles >= len(chapters) else f"已达到 max_cycles={max_cycles} 检查点"
                item.completed_at = utc_now()
                self._save_state()
            broadcaster.broadcast_sync("queue_item_completed", item.model_dump(), book_id=item.book_id)
            logger.info("Queue item completed successfully: %s (%s)", item.id, item.book_name)

        except JobCancelled:
            with self._lock:
                if item.status == "cancelling":
                    self._transition_locked(item, {"cancelling"}, "cancelled")
                elif item.status not in TERMINAL_STATUSES:
                    item.status = "cancelled"
                    item.updated_at = utc_now()
                item.message = "流水线已安全取消"
                item.phase = "idle"
                item.reviewer_states = {
                    role: "cancelled" if state in {"pending", "reviewing"} else state
                    for role, state in item.reviewer_states.items()
                }
                item.completed_at = utc_now()
                self._save_state()
            broadcaster.broadcast_sync("pipeline_stopped", self._as_task(item).model_dump(), book_id=item.book_id)
        except Exception as exc:
            logger.error("Queue worker failed for item %s: %s", item.id, exc, exc_info=True)
            with self._lock:
                if item.status not in TERMINAL_STATUSES and item.status != "cancelling":
                    self._transition_locked(item, {item.status}, "failed")
                    item.message = f"执行出错: {exc}"
                    item.phase = "idle"
                    item.error_detail = traceback.format_exc()
                    item.completed_at = utc_now()
                self._save_state()
            broadcaster.broadcast_sync("queue_item_failed", item.model_dump(), book_id=item.book_id)

            if self.stop_on_error:
                with self._lock:
                    self.is_paused = True
                    self._save_state()
                broadcaster.broadcast_sync("queue_paused", {"reason": "stop_on_error", "failed_item": item.id})

        finally:
            with self._lock:
                self._running_threads.pop(item.id, None)
                self._stop_events.pop(item.id, None)
                self._pause_events.pop(item.id, None)
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
                "phase": item.phase,
                "reviewer_states": item.reviewer_states,
                "reviewer_details": item.reviewer_details,
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


# Global singleton job manager
job_manager = JobManager()
