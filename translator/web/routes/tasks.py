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
        raise HTTPException(status_code=404, detail=f"未找到运行中任务: {task_or_book_id}")
    return res


@router.post("/pipeline/resume", response_model=TaskStatusResponse)
def resume_pipeline(task_or_book_id: str) -> TaskStatusResponse:
    res = task_manager.resume_pipeline(task_or_book_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"未找到已暂停任务: {task_or_book_id}")
    return res


@router.post("/pipeline/stop", response_model=TaskStatusResponse)
def stop_pipeline(task_or_book_id: str) -> TaskStatusResponse:
    res = task_manager.stop_pipeline(task_or_book_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"未找到任务: {task_or_book_id}")
    return res


@router.get("/status/{task_or_book_id}", response_model=TaskStatusResponse)
def get_task_status(task_or_book_id: str) -> TaskStatusResponse:
    res = task_manager.get_task(task_or_book_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"未找到任务记录: {task_or_book_id}")
    return res


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
    provider_inst = create_provider(provider_name, config)
    translator = ProviderTranslator(novel_root=NOVEL_TRANSLATOR_ROOT, manifest=path, provider=provider_inst)

    result = translator.translate_paragraphs([request.paragraph_id])
    if result.get("status") != "ok" or not result.get("items"):
        raise HTTPException(status_code=500, detail=f"重新翻译失败: {result}")

    translated_text = result["items"][0].get("text", "")
    target_para["translated"] = translated_text
    write_json(path, manifest)

    return {
        "status": "ok",
        "book_id": request.book_id,
        "paragraph_id": request.paragraph_id,
        "provider": provider_name,
        "translated": translated_text,
    }

