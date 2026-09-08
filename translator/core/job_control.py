from __future__ import annotations

import threading
from typing import Callable


class JobCancelled(RuntimeError):
    """Raised at a cooperative cancellation boundary."""


class CancellationToken:
    def __init__(self, event: threading.Event | None = None) -> None:
        self._event = event or threading.Event()

    @property
    def event(self) -> threading.Event:
        return self._event

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self.is_cancelled():
            raise JobCancelled("任务已取消")


class PauseGate:
    def __init__(self, event: threading.Event | None = None, *, on_pause: Callable[[], None] | None = None) -> None:
        self._event = event or threading.Event()
        self._on_pause = on_pause
        if event is None:
            self._event.set()

    @property
    def event(self) -> threading.Event:
        return self._event

    def pause(self) -> None:
        self._event.clear()

    def resume(self) -> None:
        self._event.set()

    def wait(self, cancellation: CancellationToken, poll_seconds: float = 0.1) -> None:
        cancellation.check()
        if not self._event.is_set() and self._on_pause is not None:
            self._on_pause()
        while not self._event.wait(poll_seconds):
            cancellation.check()
        cancellation.check()
