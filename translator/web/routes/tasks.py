from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from translator.core.config import load_config, primary_translator_name
from translator.core.novel_tool import NOVEL_TRANSLATOR_ROOT
from translator.core.workspace import read_json, write_json
from translator.pipeline.chapter_pipeline import manifest_path
from translator.providers.registry import create_provider
from translator.providers.translator import ProviderTranslator
from translator.core.queue_manager import queue_manager
from translator.web.models import (
    PipelineStartRequest,
    RetranslateParagraphRequest,
    TaskStatusResponse,
)
from translator.web.task_manager import task_manager


router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.post("/pipeline/start", response_model=TaskStatusResponse)
def start_pipeline(request: PipelineStartRequest) -> TaskStatusResponse:
    manifest = read_json(manifest_path(request.book_id), default=None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"未找到书籍: {request.book_id}")
    return task_manager.start_pipeline(request)


@router.post("/pipeline/pause", response_model=TaskStatusResponse)
def pause_pipeline(task_or_book_id: str) -> TaskStatusResponse:
    res = task_manager.pause_pipeline(task_or_book_id)
    if not res:
        # Try queue_manager
        with queue_manager._lock:
            for item in queue_manager._items.values():
                if item.id == task_or_book_id or item.book_id == task_or_book_id:
                    if item.id in queue_manager._pause_events:
                        queue_manager._pause_events[item.id].clear()
                        item.status = "paused"
                        item.message = "已暂停"
                        queue_manager._save_state()
                        return TaskStatusResponse(
                            task_id=item.id,
                            book_id=item.book_id,
                            status=item.status,
                            overall_progress=item.overall_progress,
                            current_chapter=item.current_chapter,
                            current_chapter_index=item.current_chapter_index,
                            total_chapters=item.total_chapters,
                            message=item.message,
                        )
        raise HTTPException(status_code=404, detail=f"未找到运行中任务: {task_or_book_id}")
    return res


@router.post("/pipeline/resume", response_model=TaskStatusResponse)
def resume_pipeline(task_or_book_id: str) -> TaskStatusResponse:
    res = task_manager.resume_pipeline(task_or_book_id)
    if not res:
        # Try queue_manager
        with queue_manager._lock:
            for item in queue_manager._items.values():
                if item.id == task_or_book_id or item.book_id == task_or_book_id:
                    if item.id in queue_manager._pause_events:
                        queue_manager._pause_events[item.id].set()
                        item.status = "running"
                        item.message = "继续推进中..."
                        queue_manager._save_state()
                        return TaskStatusResponse(
                            task_id=item.id,
                            book_id=item.book_id,
                            status=item.status,
                            overall_progress=item.overall_progress,
                            current_chapter=item.current_chapter,
                            current_chapter_index=item.current_chapter_index,
                            total_chapters=item.total_chapters,
                            message=item.message,
                        )
        raise HTTPException(status_code=404, detail=f"未找到已暂停任务: {task_or_book_id}")
    return res


@router.post("/pipeline/stop", response_model=TaskStatusResponse)
def stop_pipeline(task_or_book_id: str) -> TaskStatusResponse:
    res = task_manager.stop_pipeline(task_or_book_id)
    if not res:
        # Try queue_manager
        with queue_manager._lock:
            for item in queue_manager._items.values():
                if item.id == task_or_book_id or item.book_id == task_or_book_id:
                    queue_manager.cancel_item(item.id)
                    return TaskStatusResponse(
                        task_id=item.id,
                        book_id=item.book_id,
                        status="stopped",
                        overall_progress=item.overall_progress,
                        current_chapter=item.current_chapter,
                        current_chapter_index=item.current_chapter_index,
                        total_chapters=item.total_chapters,
                        message="已由用户终止",
                    )
        raise HTTPException(status_code=404, detail=f"未找到任务: {task_or_book_id}")
    return res


@router.get("/status/{task_or_book_id}", response_model=TaskStatusResponse)
def get_task_status(task_or_book_id: str) -> TaskStatusResponse:
    res = task_manager.get_task(task_or_book_id)
    if res:
        return res

    # Check queue_manager
    with queue_manager._lock:
        item = queue_manager._items.get(task_or_book_id)
        if not item:
            for it in queue_manager._items.values():
                if it.book_id == task_or_book_id:
                    item = it
                    break
        if item:
            return TaskStatusResponse(
                task_id=item.id,
                book_id=item.book_id,
                status=item.status if item.status in ["running", "paused", "completed", "failed"] else "idle",
                overall_progress=item.overall_progress,
                current_chapter=item.current_chapter,
                current_chapter_index=item.current_chapter_index,
                total_chapters=item.total_chapters,
                message=item.message,
                error_detail=item.error_detail,
                started_at=item.started_at,
                updated_at=item.completed_at or item.started_at,
            )

    raise HTTPException(status_code=404, detail=f"未找到任务: {task_or_book_id}")


@router.get("", response_model=list[TaskStatusResponse])
def list_tasks() -> list[TaskStatusResponse]:
    return task_manager.list_tasks()


@router.post("/retranslate-paragraph")
def retranslate_paragraph(request: RetranslateParagraphRequest) -> dict[str, Any]:
    path = manifest_path(request.book_id)
    manifest = read_json(path, default=None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"未找到书籍: {request.book_id}")

    target_para = None
    for ch in manifest.get("chapters", []):
        for p in ch.get("paragraphs", []):
            if p.get("id") == request.paragraph_id:
                target_para = p
                break
        if target_para:
            break

    if not target_para:
        raise HTTPException(status_code=404, detail=f"未找到段落: {request.paragraph_id}")

    config = load_config()
    provider_name = request.provider or primary_translator_name(config)

    # Use ProviderTranslator for single paragraph targeted translation
    translator = ProviderTranslator(novel_root=NOVEL_TRANSLATOR_ROOT, manifest=path)
    source_chars = len(str(target_para.get("source", "")))
    result = translator(
        provider_name,
        request.book_id,
        [request.paragraph_id],
        source_chars=source_chars,
        max_tokens=1500,
    )
    if result.get("status") != "ok":
        raise HTTPException(status_code=500, detail=f"重新翻译失败: {result}")

    # Re-read manifest to get updated translated text
    manifest_after = read_json(path, default={})
    updated_text = ""
    for ch in manifest_after.get("chapters", []):
        for p in ch.get("paragraphs", []):
            if p.get("id") == request.paragraph_id:
                updated_text = p.get("translated", "")
                break
        if updated_text:
            break

    return {
        "status": "ok",
        "book_id": request.book_id,
        "paragraph_id": request.paragraph_id,
        "provider": provider_name,
        "translated": updated_text,
    }

