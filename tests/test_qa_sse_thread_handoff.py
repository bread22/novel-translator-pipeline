from __future__ import annotations

import asyncio
import threading
import time

import pytest

from translator.web.events import EventBroadcaster


def test_background_thread_event_wakes_sse_subscriber() -> None:
    asyncio.run(_test_background_thread_event_wakes_sse_subscriber())


async def _test_background_thread_event_wakes_sse_subscriber() -> None:
    asyncio.get_running_loop().set_debug(True)
    broadcaster = EventBroadcaster()
    stream = broadcaster.subscribe()
    connect = await stream.__anext__()
    assert "event: connect" in connect

    next_event = asyncio.create_task(stream.__anext__())
    thread = threading.Thread(target=lambda: broadcaster.broadcast_sync("thread_event", {"value": 1}))
    thread.start()
    try:
        payload = await asyncio.wait_for(next_event, timeout=0.5)
    finally:
        thread.join(timeout=1)
        await stream.aclose()

    assert "event: thread_event" in payload


@pytest.mark.xfail(
    strict=True,
    reason="broadcast_sync writes asyncio.Queue from a worker thread without waking the owning event loop",
)
def test_background_thread_event_is_delivered_without_polling_delay() -> None:
    asyncio.run(_test_background_thread_event_is_delivered_without_polling_delay())


async def _test_background_thread_event_is_delivered_without_polling_delay() -> None:
    failures = 0
    for _ in range(1000):
        broadcaster = EventBroadcaster()
        stream = broadcaster.subscribe()
        await stream.__anext__()
        next_event = asyncio.create_task(stream.__anext__())
        started = time.perf_counter()
        thread = threading.Thread(target=lambda: broadcaster.broadcast_sync("thread_event", {"value": 1}))
        thread.start()
        try:
            await asyncio.wait_for(next_event, timeout=0.05)
            if time.perf_counter() - started > 0.02:
                failures += 1
        except asyncio.TimeoutError:
            failures += 1
        finally:
            thread.join(timeout=1)
            await stream.aclose()

    assert failures == 0, f"{failures}/1000 events exceeded the immediate-delivery threshold"
