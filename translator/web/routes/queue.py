from __future__ import annotations

from fastapi import APIRouter, HTTPException

from translator.core.queue_manager import queue_manager
from translator.web.models import (
    EnqueueRequest,
    QueueClearRequest,
    QueueConfigUpdateRequest,
    QueueItem,
    QueueItemMoveRequest,
    QueueReorderRequest,
    QueueStatusResponse,
)

router = APIRouter(prefix="/queue", tags=["Queue"])


@router.get("", response_model=QueueStatusResponse)
def get_queue() -> QueueStatusResponse:
    """获取当前翻译任务队列状态与全部队列项"""
    return queue_manager.get_status()


@router.post("/items", response_model=QueueStatusResponse)
def enqueue_items(request: EnqueueRequest) -> QueueStatusResponse:
    """单本或批量添加书籍至翻译队列"""
    if not request.book_ids:
        raise HTTPException(status_code=400, detail="请至少指定一部书籍 ID")
    queue_manager.enqueue_batch(
        book_ids=request.book_ids,
        options=request.options,
        insert_front=request.insert_front,
    )
    return queue_manager.get_status()


@router.delete("/items/{item_id}", response_model=QueueStatusResponse)
def cancel_queue_item(item_id: str) -> QueueStatusResponse:
    """取消或移出指定队列项 (运行中任务将被安全终止)"""
    success = queue_manager.cancel_item(item_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"未找到队列项: {item_id}")
    return queue_manager.get_status()


@router.post("/items/{item_id}/retry", response_model=QueueStatusResponse)
def retry_queue_item(item_id: str) -> QueueStatusResponse:
    """单键重新入队重试失败或取消的队列项"""
    res = queue_manager.retry_item(item_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"未找到队列项或无法重试: {item_id}")
    return queue_manager.get_status()


@router.post("/items/{item_id}/move", response_model=QueueStatusResponse)
def move_queue_item(item_id: str, request: QueueItemMoveRequest) -> QueueStatusResponse:
    """调整排队中任务的次序 (置顶 top / 上移 up / 下移 down)"""
    success = queue_manager.move_item(item_id, request.direction)
    if not success:
        raise HTTPException(status_code=400, detail=f"无法调整该项排位: {item_id} (direction={request.direction})")
    return queue_manager.get_status()


@router.post("/reorder", response_model=QueueStatusResponse)
def reorder_queue(request: QueueReorderRequest) -> QueueStatusResponse:
    """拖拽调序后批量原子更新待处理任务顺序"""
    queue_manager.reorder(request.item_ids)
    return queue_manager.get_status()


@router.post("/pause", response_model=QueueStatusResponse)
def pause_queue() -> QueueStatusResponse:
    """暂停队列调度器 (阻止后续任务自动启动，运行中任务继续执行)"""
    queue_manager.pause_queue()
    return queue_manager.get_status()


@router.post("/resume", response_model=QueueStatusResponse)
def resume_queue() -> QueueStatusResponse:
    """恢复队列调度器 (自动继续派发待办任务)"""
    queue_manager.resume_queue()
    return queue_manager.get_status()


@router.post("/clear", response_model=QueueStatusResponse)
def clear_queue(request: QueueClearRequest) -> QueueStatusResponse:
    """清理已完成、失败或取消的历史队列项"""
    queue_manager.clear(scope=request.scope)
    return queue_manager.get_status()


@router.post("/config", response_model=QueueStatusResponse)
def update_queue_config(request: QueueConfigUpdateRequest) -> QueueStatusResponse:
    """动态更新队列并发数与熔断配置"""
    queue_manager.update_config(
        concurrency=request.concurrency,
        stop_on_error=request.stop_on_error,
    )
    return queue_manager.get_status()
