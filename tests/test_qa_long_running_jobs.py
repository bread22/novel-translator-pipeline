from __future__ import annotations

import threading
import time
from pathlib import Path

from translator.core.job_manager import JobManager
from translator.core.workspace import write_json
from translator.web.models import PipelineStartRequest


def test_pause_does_not_report_paused_while_provider_call_is_blocked(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "data" / "books" / "book-1" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    write_json(manifest, {
        "book": "book-1",
        "title": "Blocked Book",
        "chapters": [{"id": "c1", "title": "Chapter 1", "paragraphs": []}],
    })

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class BlockingPipeline:
        def __init__(self, **_kwargs) -> None:
            pass

        def is_chapter_completed(self, _chapter_id: str) -> bool:
            return False

        def run_chapter(self, _chapter_id: str, cycle: int) -> dict:
            started.set()
            release.wait(2)
            finished.set()
            return {"translated": 0, "reviewed": 0, "issues": 0, "fixes": 0}

    monkeypatch.setattr("translator.core.job_manager.manifest_path", lambda _book_id: manifest)
    monkeypatch.setattr("translator.core.job_manager.ChapterPipeline", BlockingPipeline)
    manager = JobManager(output_root=tmp_path / "output")

    task = manager.start_pipeline(PipelineStartRequest(book_id="book-1", finalize=False, max_cycles=1))
    assert started.wait(2)

    try:
        paused = manager.pause_pipeline(task.task_id)
        assert paused is not None
        assert paused.status == "pausing"
        assert not finished.is_set()
    finally:
        release.set()
        manager.stop_pipeline(task.task_id)
        with manager._lock:
            worker = manager._running_threads.get(task.task_id)
        if worker:
            worker.join(2)


def test_stop_does_not_emit_terminal_event_before_worker_stops(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "data" / "books" / "book-1" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    write_json(manifest, {
        "book": "book-1",
        "title": "Blocked Book",
        "chapters": [{"id": "c1", "title": "Chapter 1", "paragraphs": []}],
    })

    started = threading.Event()
    release = threading.Event()
    events: list[tuple[str, dict]] = []

    class BlockingPipeline:
        def __init__(self, **_kwargs) -> None:
            pass

        def is_chapter_completed(self, _chapter_id: str) -> bool:
            return False

        def run_chapter(self, _chapter_id: str, cycle: int) -> dict:
            started.set()
            release.wait(2)
            return {"translated": 0, "reviewed": 0, "issues": 0, "fixes": 0}

    monkeypatch.setattr("translator.core.job_manager.manifest_path", lambda _book_id: manifest)
    monkeypatch.setattr("translator.core.job_manager.ChapterPipeline", BlockingPipeline)
    monkeypatch.setattr(
        "translator.core.job_manager.broadcaster.broadcast_sync",
        lambda event_type, data, book_id=None: events.append((event_type, data)),
    )
    manager = JobManager(output_root=tmp_path / "output")
    task = manager.start_pipeline(PipelineStartRequest(book_id="book-1", finalize=False, max_cycles=1))
    assert started.wait(2)
    events.clear()

    try:
        stopped = manager.stop_pipeline(task.task_id)
        assert stopped is not None
        assert stopped.status == "cancelling"
        assert not any(
            event_type == "pipeline_stopped" and data.get("status") == "cancelling"
            for event_type, data in events
        )
    finally:
        release.set()
        with manager._lock:
            worker = manager._running_threads.get(task.task_id)
        if worker:
            worker.join(2)
