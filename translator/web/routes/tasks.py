from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from translator.core.config import load_config, primary_translator_name
from translator.core.novel_tool import NOVEL_TRANSLATOR_ROOT
from translator.core.workspace import read_json
from translator.pipeline.chapter_pipeline import manifest_path
from translator.providers.translator import ProviderTranslator
from translator.core.job_manager import job_manager
from translator.web.models import (
    PipelineStartRequest,
    RetranslateParagraphRequest,
    TaskStatusResponse,
)


router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.post("/pipeline/start", response_model=TaskStatusResponse)
def start_pipeline(request: PipelineStartRequest) -> TaskStatusResponse:
    manifest = read_json(manifest_path(request.book_id), default=None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"未找到书籍: {request.book_id}")
    return job_manager.start_pipeline(request)


@router.post("/pipeline/pause", response_model=TaskStatusResponse)
def pause_pipeline(task_or_book_id: str) -> TaskStatusResponse:
    res = job_manager.pause_pipeline(task_or_book_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"未找到运行中任务: {task_or_book_id}")
    return res


@router.post("/pipeline/resume", response_model=TaskStatusResponse)
def resume_pipeline(task_or_book_id: str) -> TaskStatusResponse:
    res = job_manager.resume_pipeline(task_or_book_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"未找到已暂停任务: {task_or_book_id}")
    return res


@router.post("/pipeline/stop", response_model=TaskStatusResponse)
def stop_pipeline(task_or_book_id: str) -> TaskStatusResponse:
    res = job_manager.stop_pipeline(task_or_book_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"未找到任务: {task_or_book_id}")
    return res


@router.get("/status/{task_or_book_id}", response_model=TaskStatusResponse)
def get_task_status(task_or_book_id: str) -> TaskStatusResponse:
    res = job_manager.get_task(task_or_book_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"未找到任务: {task_or_book_id}")
    return res


@router.get("", response_model=list[TaskStatusResponse])
def list_tasks() -> list[TaskStatusResponse]:
    return job_manager.list_tasks()


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
