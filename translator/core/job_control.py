from __future__ import annotations

import threading


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
    def __init__(self, event: threading.Event | None = None) -> None:
        self._event = event or threading.Event()
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
        while not self._event.wait(poll_seconds):
            cancellation.check()
        cancellation.check()
