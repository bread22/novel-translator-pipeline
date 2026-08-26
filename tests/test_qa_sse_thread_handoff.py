from __future__ import annotations

import asyncio
import threading

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


def test_background_thread_event_is_delivered_without_polling_delay() -> None:
    asyncio.run(_test_background_thread_event_is_delivered_without_polling_delay())


async def _test_background_thread_event_is_delivered_without_polling_delay() -> None:
    loop = asyncio.get_running_loop()
    original_call_soon_threadsafe = loop.call_soon_threadsafe
    handoff_seen = threading.Event()

    def record_handoff(callback, *args):
        handoff_seen.set()
        return original_call_soon_threadsafe(callback, *args)

    loop.call_soon_threadsafe = record_handoff
    broadcaster = EventBroadcaster()
    stream = broadcaster.subscribe()
    await stream.__anext__()
    next_event = asyncio.create_task(stream.__anext__())
    thread = threading.Thread(target=lambda: broadcaster.broadcast_sync("thread_event", {"value": 1}))
    thread.start()
    try:
        thread.join(timeout=1)
        assert handoff_seen.is_set(), "broadcast_sync did not hand the event back to the owning event loop"
        payload = await asyncio.wait_for(next_event, timeout=0.5)
    finally:
        loop.call_soon_threadsafe = original_call_soon_threadsafe
        await stream.aclose()

    assert "event: thread_event" in payload
