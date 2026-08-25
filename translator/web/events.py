from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any, AsyncGenerator
from uuid import uuid4


from pathlib import Path

logger = logging.getLogger("translator.web.events")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_book_event(book_id: str, payload: dict[str, Any], output_root: str | Path | None = None) -> None:
    """Append event payload to book's persistent events.jsonl on disk."""
    if not book_id:
        return
    try:
        from translator.core.workspace import BookWorkspace
        from translator.core.config import load_config
        root = output_root or load_config().get("paths", {}).get("output_root", "output")
        ws = BookWorkspace.at(Path(root), book_id)
        events_file = ws.data_dir / "events.jsonl"
        events_file.parent.mkdir(parents=True, exist_ok=True)
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("Failed to append book event to disk: %s", e)


def read_book_events(book_id: str, limit: int = 500, output_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Read last N events from book's persistent events.jsonl."""
    if not book_id:
        return []
    try:
        from translator.core.workspace import BookWorkspace
        from translator.core.config import load_config
        root = output_root or load_config().get("paths", {}).get("output_root", "output")
        ws = BookWorkspace.at(Path(root), book_id)
        events_file = ws.data_dir / "events.jsonl"
        if not events_file.exists():
            return []
        events: list[dict[str, Any]] = []
        with open(events_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except Exception:
                        pass
        return events[-limit:]
    except Exception as e:
        logger.debug("Failed to read book events: %s", e)
        return []


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
            "event_id": uuid4().hex,
        }
        if payload["book_id"]:
            append_book_event(payload["book_id"], payload)
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
            "event_id": uuid4().hex,
        }
        if payload["book_id"]:
            append_book_event(payload["book_id"], payload)
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

        # The browser remembers this ID across automatic reconnects. State snapshots
        # are refreshed client-side on each open, so no durable replay is claimed.
        connect_payload = {
            "event": "connect",
            "data": {"status": "connected"},
            "book_id": book_id,
            "timestamp": utc_now(),
            "event_id": uuid4().hex,
        }
        connect_json = json.dumps(connect_payload, ensure_ascii=False)
        yield f"id: {connect_payload['event_id']}\nevent: connect\ndata: {connect_json}\n\n"

        try:
            while True:
                try:
                    # Wait for next event or send ping every 15s
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    if payload is None:
                        break
                    event_type = payload.get("event", "message")
                    data_json = json.dumps(payload, ensure_ascii=False)
                    yield f"id: {payload['event_id']}\nevent: {event_type}\ndata: {data_json}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive heartbeat ping
                    yield f": ping {utc_now()}\n\n"
        finally:
            async with self._lock:
                self._subscribers.discard(sub)


# Global singleton instance
broadcaster = EventBroadcaster()
