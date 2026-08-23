from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any, AsyncGenerator


logger = logging.getLogger("translator.web.events")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EventBroadcaster:
    """Async event broadcaster using asyncio.Queue for Server-Sent Events (SSE)."""

    def __init__(self) -> None:
        self._subscribers: set[tuple[asyncio.Queue[dict[str, Any] | None], str | None]] = set()
        self._lock = asyncio.Lock()

    async def broadcast(self, event_type: str, data: dict[str, Any], book_id: str | None = None) -> None:
        payload = {
            "event": event_type,
            "data": data,
            "book_id": book_id or data.get("book_id", ""),
            "timestamp": utc_now(),
        }
        async with self._lock:
            dead_subscribers = []
            for queue, sub_book_id in self._subscribers:
                if sub_book_id is None or sub_book_id == payload["book_id"]:
                    try:
                        queue.put_nowait(payload)
                    except asyncio.QueueFull:
                        dead_subscribers.append((queue, sub_book_id))
            for dead in dead_subscribers:
                self._subscribers.discard(dead)

    def broadcast_sync(self, event_type: str, data: dict[str, Any], book_id: str | None = None) -> None:
        """Sync convenience method for callbacks running in background threads."""
        payload = {
            "event": event_type,
            "data": data,
            "book_id": book_id or data.get("book_id", ""),
            "timestamp": utc_now(),
        }
        for queue, sub_book_id in list(self._subscribers):
            if sub_book_id is None or sub_book_id == payload["book_id"]:
                try:
                    queue.put_nowait(payload)
                except Exception:
                    pass

    async def subscribe(self, book_id: str | None = None) -> AsyncGenerator[str, None]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=500)
        sub = (queue, book_id)
        async with self._lock:
            self._subscribers.add(sub)

        # Initial connect event
        yield f"event: connect\ndata: {json.dumps({'status': 'connected', 'book_id': book_id, 'timestamp': utc_now()}, ensure_ascii=False)}\n\n"

        try:
            while True:
                try:
                    # Wait for next event or send ping every 15s
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if payload is None:
                        break
                    event_type = payload.get("event", "message")
                    data_json = json.dumps(payload.get("data", {}), ensure_ascii=False)
                    yield f"event: {event_type}\ndata: {data_json}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive heartbeat ping
                    yield f": ping {utc_now()}\n\n"
        finally:
            async with self._lock:
                self._subscribers.discard(sub)


# Global singleton instance
broadcaster = EventBroadcaster()
