from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from translator.web.events import broadcaster


router = APIRouter(prefix="/events", tags=["Events"])


@router.get("/stream")
async def event_stream(book_id: str | None = Query(None, description="可选订阅特定书籍事件")):
    """SSE (Server-Sent Events) endpoint for real-time pipeline events."""
    return StreamingResponse(
        broadcaster.subscribe(book_id=book_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
