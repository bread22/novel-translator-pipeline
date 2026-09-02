from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import re
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


def _legacy_translation_events(
    workspace: BookWorkspace,
    book_id: str,
    existing: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project provider diagnostics into the event contract for pre-SSE runs.

    Older pipeline runs recorded every provider attempt but did not append those
    attempts to events.jsonl. Keeping this projection here makes the first page
    refresh useful while new runs use the live callbacks below.
    """
    diagnostics_file = workspace.data_dir / "provider-diagnostics.json"
    if not diagnostics_file.exists():
        return []
    try:
        diagnostics = json.loads(diagnostics_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    attempts = diagnostics.get("attempts", []) if isinstance(diagnostics, dict) else []
    if not isinstance(attempts, list):
        return []

    provenance_file = workspace.data_dir / "translation-provenance.json"
    try:
        provenance = json.loads(provenance_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        provenance = {}
    provenance_items = provenance.get("items", {}) if isinstance(provenance, dict) else {}
    recovered_timestamps = {
        str(item.get("attempt_id")): str(item.get("recovered_at") or item.get("updated_at"))
        for item in provenance_items.values()
        if isinstance(item, dict) and item.get("attempt_id") and (item.get("recovered_at") or item.get("updated_at"))
    }
    try:
        default_timestamp = datetime.fromtimestamp(diagnostics_file.stat().st_mtime, timezone.utc).isoformat()
    except OSError:
        default_timestamp = utc_now()

    existing_attempt_ids = {
        str(event.get("data", {}).get("attempt_id"))
        for event in existing
        if isinstance(event, dict) and event.get("event") == "translation_attempt"
        and isinstance(event.get("data"), dict) and event["data"].get("attempt_id")
    }
    existing_fallback_attempt_ids = {
        str(event.get("data", {}).get("attempt_id"))
        for event in existing
        if isinstance(event, dict) and event.get("event") == "fallback_triggered"
        and isinstance(event.get("data"), dict) and event["data"].get("attempt_id")
    }
    projected: list[dict[str, Any]] = []
    previous_provider = ""
    previous_reason = ""
    for index, attempt in enumerate(attempts):
        if not isinstance(attempt, dict):
            continue
        provider = str(attempt.get("provider", "-")).strip() or "-"
        raw_result = attempt.get("result")
        result: dict[str, Any] = raw_result if isinstance(raw_result, dict) else {}
        attempted_ids = attempt.get("attempted_ids", attempt.get("ids", []))
        recovered_ids = attempt.get("recovered_ids")
        attempted_ids = [str(item) for item in attempted_ids] if isinstance(attempted_ids, list) else []
        if isinstance(recovered_ids, list):
            recovered_ids = [str(item) for item in recovered_ids]
        else:
            remaining_ids = attempt.get("remaining", [])
            remaining_ids = {str(item) for item in remaining_ids} if isinstance(remaining_ids, list) else set()
            recovered_ids = (
                [item_id for item_id in attempted_ids if item_id not in remaining_ids]
                if str(result.get("status", "")).casefold() == "ok"
                else []
            )
        attempt_id = str(attempt.get("attempt_id", "")).strip()
        stable_id = attempt_id or hashlib.sha1(
            json.dumps({"book": book_id, "index": index, "attempt": attempt}, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        reason = str(attempt.get("reason") or result.get("reason") or "unknown")
        fallback_match = re.search(r"_fb(\d+)$", reason)
        is_fallback = bool(attempt.get("fallback_from") or fallback_match)
        fallback_index = int(fallback_match.group(1)) if fallback_match else None
        fallback_from = str(attempt.get("fallback_from") or (previous_provider if is_fallback else "")) or None
        timestamp = recovered_timestamps.get(attempt_id, default_timestamp)
        event_data: dict[str, Any] = {
            "book_id": book_id,
            "chapter_id": str(attempt.get("chapter_id", "")),
            "provider": provider,
            "attempt_id": stable_id,
            "attempted_ids": attempted_ids,
            "recovered_ids": recovered_ids,
            "failed_ids": [item_id for item_id in attempted_ids if item_id not in recovered_ids],
            "status": "ok" if recovered_ids else "failed",
            "provider_status": str(attempt.get("status") or result.get("status") or "error"),
            "reason": "ok" if recovered_ids and str(result.get("status", "")).casefold() == "ok" else reason,
            "depth": attempt.get("depth", 0),
            "is_fallback": is_fallback,
            "legacy": True,
        }
        if fallback_from:
            event_data["fallback_from"] = fallback_from
        if fallback_index is not None:
            event_data["fallback_index"] = fallback_index
            event_data["fallback_reason"] = reason
        if attempt.get("latency_ms") is not None:
            event_data["latency_ms"] = attempt["latency_ms"]
        for key in (
            "error", "http_status", "finish_reason", "format", "split",
            "failure_class", "residue_tokens", "repair_rule_ids", "repair_rule_version",
            "repair_attempts",
        ):
            value = result.get(key)
            if value in (None, ""):
                value = attempt.get(key)
            if value not in (None, ""):
                event_data[key] = str(value)[:800] if key == "error" else value

        if is_fallback and stable_id not in existing_fallback_attempt_ids:
            projected.append({
                "event": "fallback_triggered",
                "data": {
                    "book_id": book_id,
                    "chapter_id": event_data["chapter_id"],
                    "from_provider": fallback_from or "-",
                    "to_provider": provider,
                    "reason": previous_reason or reason,
                    "paragraph_ids": attempted_ids,
                    "depth": attempt.get("depth", 0),
                    "fallback_index": fallback_index,
                    "attempt_id": stable_id,
                    "legacy": True,
                },
                "book_id": book_id,
                "timestamp": timestamp,
                "event_id": f"diagnostic-fallback-{stable_id}",
            })
        if stable_id not in existing_attempt_ids:
            projected.append({
                "event": "translation_attempt",
                "data": event_data,
                "book_id": book_id,
                "timestamp": timestamp,
                "event_id": f"diagnostic-attempt-{stable_id}",
            })
        previous_provider = provider
        previous_reason = reason
    return projected


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
        events: list[dict[str, Any]] = []
        if events_file.exists():
            with _event_file_lock(events_file):
                with open(events_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            try:
                                events.append(json.loads(line))
                            except Exception:
                                pass
        events.extend(_legacy_translation_events(ws, book_id, events))
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
