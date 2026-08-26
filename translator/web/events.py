from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import threading
from typing import TYPE_CHECKING, Any, AsyncGenerator
from uuid import uuid4

from pathlib import Path

if TYPE_CHECKING:
    from translator.core.workspace import BookWorkspace

logger = logging.getLogger("translator.web.events")

_event_file_locks: dict[Path, threading.Lock] = {}
_event_file_locks_guard = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_book_workspace(book_id_or_title: str, output_root: Path) -> BookWorkspace:
    """Resolve the canonical BookWorkspace directory whether given slug ID or display title."""
    from translator.pipeline.chapter_pipeline import manifest_path
    from translator.core.workspace import BookWorkspace, read_json
    mf = read_json(manifest_path(book_id_or_title), default=None)
    title = mf.get("title", book_id_or_title) if mf else book_id_or_title
    ws = BookWorkspace.at(output_root, title)
    if ws.root.exists():
        return ws
    ws_slug = BookWorkspace.at(output_root, book_id_or_title)
    if ws_slug.root.exists():
        return ws_slug
    return ws


def _event_output_root(output_root: str | Path | None = None) -> Path:
    """Resolve event storage against the config file, not the server CWD."""
    if output_root is not None:
        return Path(output_root).expanduser().resolve()

    from translator.core.config import load_config
    from translator.core.paths import PathResolver

    config = load_config()
    return PathResolver.for_config().output_root(config)


def _event_file_lock(events_file: Path) -> threading.Lock:
    key = events_file.resolve()
    with _event_file_locks_guard:
        return _event_file_locks.setdefault(key, threading.Lock())


def append_book_event(book_id: str, payload: dict[str, Any], output_root: str | Path | None = None) -> None:
    """Append event payload to book's persistent events.jsonl on disk."""
    if not book_id:
        return
    try:
        ws = _resolve_book_workspace(book_id, _event_output_root(output_root))
        events_file = ws.data_dir / "events.jsonl"
        with _event_file_lock(events_file):
            events_file.parent.mkdir(parents=True, exist_ok=True)
            with open(events_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
    except Exception as e:
        logger.debug("Failed to append book event to disk: %s", e)


def read_book_events(book_id: str, limit: int = 500, output_root: str | Path | None = None) -> list[dict[str, Any]]:
    """Read last N events from book's persistent events.jsonl."""
    if not book_id or limit <= 0:
        return []
    try:
        ws = _resolve_book_workspace(book_id, _event_output_root(output_root))
        events_file = ws.data_dir / "events.jsonl"
        if not events_file.exists():
            return []
        events: list[dict[str, Any]] = []
        with _event_file_lock(events_file):
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
        self._loop: asyncio.AbstractEventLoop | None = None

    async def _broadcast_payload(self, payload: dict[str, Any]) -> None:
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

    async def broadcast(self, event_type: str, data: dict[str, Any], book_id: str | None = None) -> None:
        payload = {
            "event": event_type,
            "data": data,
            "book_id": book_id or data.get("book_id", ""),
            "timestamp": utc_now(),
            "event_id": uuid4().hex,
        }
        if payload["book_id"]:
            append_book_event(str(payload["book_id"]), payload)
        await self._broadcast_payload(payload)

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
            append_book_event(str(payload["book_id"]), payload)
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        payload_coro = self._broadcast_payload(payload)
        try:
            loop.call_soon_threadsafe(asyncio.create_task, payload_coro)
        except RuntimeError:
            # The application loop may close while a worker is emitting its final event.
            payload_coro.close()
            return

    async def subscribe(self, book_id: str | None = None) -> AsyncGenerator[str, None]:
        self._loop = asyncio.get_running_loop()
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
                    # Wait for next event or send ping every 10s
                    payload = await asyncio.wait_for(queue.get(), timeout=10.0)
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
