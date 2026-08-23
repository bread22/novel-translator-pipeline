from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from pathlib import Path
import threading
import time
import traceback
from typing import Any, Callable

from translator.core.config import load_config
from translator.core.novel_tool import NOVEL_TRANSLATOR_ROOT, call_novel_translator
from translator.core.workspace import BookWorkspace, read_json, safe_book_name
from translator.pipeline.chapter_pipeline import ChapterPipeline, manifest_path
from translator.pipeline.preflight import run_preflight
from translator.providers.translator import ProviderTranslator
from translator.web.events import broadcaster
from translator.web.models import PipelineStartRequest, TaskStatusResponse


logger = logging.getLogger("translator.web.task_manager")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunningTask:
    def __init__(self, task_id: str, book_id: str, options: PipelineStartRequest) -> None:
        self.task_id = task_id
        self.book_id = book_id
        self.options = options
        self.status = "running"  # running, paused, stopped, completed, failed
        self.pause_event = threading.Event()
        self.pause_event.set()  # set means not paused
        self.stop_event = threading.Event()
        self.overall_progress: float = 0.0
        self.current_chapter: str = ""
        self.current_chapter_index: int = 0
        self.total_chapters: int = 0
        self.current_batch: int = 0
        self.total_batches: int = 0
        self.recovered_paragraphs: int = 0
        self.message: str = "正在初始化流水线..."
        self.error_detail: str | None = None
        self.started_at: str = utc_now()
        self.updated_at: str = utc_now()
        self.thread: threading.Thread | None = None

    def to_response(self) -> TaskStatusResponse:
        return TaskStatusResponse(
            task_id=self.task_id,
            book_id=self.book_id,
            status=self.status,
            overall_progress=self.overall_progress,
            current_chapter=self.current_chapter,
            current_chapter_index=self.current_chapter_index,
            total_chapters=self.total_chapters,
            current_batch=self.current_batch,
            total_batches=self.total_batches,
            recovered_paragraphs=self.recovered_paragraphs,
            message=self.message,
            error_detail=self.error_detail,
            started_at=self.started_at,
            updated_at=self.updated_at,
        )


class TaskManager:
    def __init__(self) -> None:
        self._tasks: dict[str, RunningTask] = {}
        self._book_to_task: dict[str, str] = {}
        self._lock = threading.Lock()

    def get_task(self, task_or_book_id: str) -> TaskStatusResponse | None:
        with self._lock:
            if task_or_book_id in self._tasks:
                return self._tasks[task_or_book_id].to_response()
            if task_or_book_id in self._book_to_task:
                tid = self._book_to_task[task_or_book_id]
                if tid in self._tasks:
                    return self._tasks[tid].to_response()
        return None

    def list_tasks(self) -> list[TaskStatusResponse]:
        with self._lock:
            return [task.to_response() for task in self._tasks.values()]

    def start_pipeline(self, request: PipelineStartRequest, output_root: Path | None = None) -> TaskStatusResponse:
        with self._lock:
            book_id = request.book_id
            # Check if there is an active task for this book
            if book_id in self._book_to_task:
                existing_tid = self._book_to_task[book_id]
                existing_task = self._tasks.get(existing_tid)
                if existing_task and existing_task.status in {"running", "paused"}:
                    return existing_task.to_response()

            task_id = f"task-{int(time.time())}-{book_id[:8]}"
            task = RunningTask(task_id, book_id, request)
            self._tasks[task_id] = task
            self._book_to_task[book_id] = task_id

            thread = threading.Thread(
                target=self._run_pipeline_worker,
                args=(task, output_root),
                daemon=True,
                name=f"Worker-{task_id}",
            )
            task.thread = thread
            thread.start()
            return task.to_response()

    def pause_pipeline(self, task_or_book_id: str) -> TaskStatusResponse | None:
        with self._lock:
            task = self._find_task(task_or_book_id)
            if not task:
                return None
            if task.status == "running":
                task.status = "paused"
                task.pause_event.clear()
                task.message = "流水线已暂停"
                task.updated_at = utc_now()
                self._emit_status(task, "pipeline_paused")
            return task.to_response()

    def resume_pipeline(self, task_or_book_id: str) -> TaskStatusResponse | None:
        with self._lock:
            task = self._find_task(task_or_book_id)
            if not task:
                return None
            if task.status == "paused":
                task.status = "running"
                task.pause_event.set()
                task.message = "流水线继续推进中..."
                task.updated_at = utc_now()
                self._emit_status(task, "pipeline_resumed")
            return task.to_response()

    def stop_pipeline(self, task_or_book_id: str) -> TaskStatusResponse | None:
        with self._lock:
            task = self._find_task(task_or_book_id)
            if not task:
                return None
            task.status = "stopped"
            task.stop_event.set()
            task.pause_event.set()  # Unblock if paused
            task.message = "流水线已由用户终止"
            task.updated_at = utc_now()
            self._emit_status(task, "pipeline_stopped")
            return task.to_response()

    def _find_task(self, task_or_book_id: str) -> RunningTask | None:
        if task_or_book_id in self._tasks:
            return self._tasks[task_or_book_id]
        if task_or_book_id in self._book_to_task:
            return self._tasks.get(self._book_to_task[task_or_book_id])
        return None

    def _emit_status(self, task: RunningTask, event_name: str = "pipeline_progress") -> None:
        broadcaster.broadcast_sync(
            event_name,
            task.to_response().model_dump(),
            book_id=task.book_id,
        )

    def _run_pipeline_worker(self, task: RunningTask, output_root: Path | None = None) -> None:
        logger.info(f"Starting pipeline worker for task {task.task_id} (book: {task.book_id})")
        config = load_config()
        out_root = (output_root or Path(config["paths"]["output_root"])).resolve()

        try:
            # 1. Inspect manifest and workspace
            manifest = read_json(manifest_path(task.book_id))
            if not manifest:
                raise FileNotFoundError(f"找不到书籍 manifest.json: {task.book_id}")

            book_title = manifest.get("title", task.book_id)
            workspace = BookWorkspace.at(out_root, book_title)
            workspace.initialize(book_id=task.book_id)

            # 2. Extract chapters
            chapters = manifest.get("chapters", [])
            task.total_chapters = len(chapters)
            task.message = f"共 {task.total_chapters} 章节，开始流水线处理..."
            self._emit_status(task, "pipeline_started")

            policy_path = Path(task.options.translation_policy).resolve() if task.options.translation_policy else None

            # 3. Instantiate pipeline
            pipeline = ChapterPipeline(
                book=task.book_id,
                workspace=workspace,
                manifest=manifest_path(task.book_id),
                apply=task.options.apply,
                autonomous=task.options.autonomous,
                layout=task.options.layout,
                primary_translator=task.options.primary_translator or None,
                fallback_translators=task.options.fallback_translators or None,
                reviewer=task.options.reviewer or None,
                translation_policy=policy_path,
                targeted_translator=ProviderTranslator(
                    novel_root=NOVEL_TRANSLATOR_ROOT,
                    manifest=manifest_path(task.book_id),
                ),
            )

            # 4. Iterate over chapters
            for idx, chapter in enumerate(chapters, start=1):
                if task.stop_event.is_set():
                    task.status = "stopped"
                    task.message = "流水线已终止"
                    self._emit_status(task, "pipeline_stopped")
                    return

                # Handle pause
                task.pause_event.wait()
                if task.stop_event.is_set():
                    return

                chapter_id = chapter.get("id", f"c{idx:04d}")
                chapter_title = chapter.get("title", chapter_id)

                # Skip chapters that are already fully translated and reviewed
                if pipeline.is_chapter_completed(chapter_id):
                    logger.info("章节 %s (%s) 已完成翻译与审阅，跳过并进入下一章", chapter_id, chapter_title)
                    continue

                task.current_chapter = chapter_id
                task.current_chapter_index = idx
                task.overall_progress = round(idx / max(1, task.total_chapters), 3)
                task.message = f"正在处理第 {idx}/{task.total_chapters} 章：{chapter_title}"
                task.updated_at = utc_now()
                self._emit_status(task, "chapter_started")

                # Run chapter translation & review
                result = pipeline.run_chapter(chapter_id, cycle=idx)

                # Emit chapter complete event
                broadcaster.broadcast_sync(
                    "chapter_completed",
                    {
                        "book_id": task.book_id,
                        "chapter_id": chapter_id,
                        "chapter_index": idx,
                        "total_chapters": task.total_chapters,
                        "result": result,
                    },
                    book_id=task.book_id,
                )

            # 5. Finalize if requested
            if task.options.finalize:
                task.message = "全部章节翻译完成，正在导出与校验最终中文 EPUB..."
                self._emit_status(task, "finalizing")
                pipeline.finalize()

            task.status = "completed"
            task.overall_progress = 1.0
            task.message = "全书翻译与审阅已完成！"
            task.updated_at = utc_now()
            self._emit_status(task, "pipeline_completed")
            logger.info(f"Pipeline completed for task {task.task_id}")

        except Exception as e:
            logger.error(f"Pipeline failed for task {task.task_id}: {e}", exc_info=True)
            task.status = "failed"
            task.message = f"执行出错: {e}"
            task.error_detail = traceback.format_exc()
            task.updated_at = utc_now()
            self._emit_status(task, "pipeline_failed")


# Global singleton
task_manager = TaskManager()
