"""Storage boundaries and job lifecycle regressions; providers are simulated."""

import asyncio
import io
import zipfile
from unittest.mock import patch

import pytest

from fastapi import UploadFile
from fastapi.testclient import TestClient
from translator.core.workspace import BookWorkspace, write_json, read_json
from translator.providers.translator import ProviderTranslator
from translator.web.routes import books
from translator.web.models import ParagraphUpdateRequest
from translator.core.job_manager import JobManager
from translator.web.app import create_app


pytestmark = pytest.mark.interleaving


def manifest(path):
    write_json(
        path,
        {
            "book": "fixture",
            "title": "fixture",
            "chapters": [
                {
                    "id": "c1",
                    "paragraphs": [
                        {"id": "p1", "source": "一", "translated": ""},
                        {"id": "p2", "source": "二", "translated": ""},
                    ],
                }
            ],
        },
    )


def test_translation_preserves_concurrent_manual_edit(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    manifest(path)
    monkeypatch.setattr(books, "manifest_path", lambda _: path)
    translator = ProviderTranslator(novel_root=tmp_path, manifest=path)
    monkeypatch.setattr(translator, "_request", lambda *a: ([{"id": "p1", "text": "自动译文"}], {"status": "ok"}))

    # Edit the selected paragraph while the external provider is in flight.
    def request(*args):
        books.update_paragraph("fixture", "p1", ParagraphUpdateRequest(translated="人工校对"))
        return [{"id": "p1", "text": "自动译文"}], {"status": "ok"}

    monkeypatch.setattr(translator, "_request", request)
    result = translator("fixture", "fixture", ["p1"], source_chars=1, max_tokens=100)
    assert result["reason"] == "write_conflict"
    assert read_json(path)["chapters"][0]["paragraphs"][0]["translated"] == "人工校对"


def test_dispatch_preserves_early_cancel_signal(tmp_path, monkeypatch):
    manager = JobManager(output_root=tmp_path / "output")
    monkeypatch.setattr(manager, "_emit_queue_updated", lambda: None)
    item = manager.enqueue("fixture")
    observed = {}

    class InterleavedThread:
        def __init__(self, *, target, args, **kwargs):
            observed["response"] = manager.stop_pipeline(item.id).status
            self.args = args

        def start(self):
            observed["cancel_event"] = self.args[1].is_set()

    manager.is_paused = False
    with (
        patch("translator.core.job_manager.threading.Thread", InterleavedThread),
        patch("translator.core.job_manager.broadcaster.broadcast_sync"),
    ):
        manager._dispatch()
    assert observed == {"response": "cancelling", "cancel_event": True}


def epub_bytes(marker):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("chapter.txt", marker)
    return stream.getvalue()


def test_replacement_upload_refreshes_source_and_state(tmp_path, monkeypatch):
    path = tmp_path / "manifest.json"
    manifest(path)
    out = tmp_path / "output"
    old = tmp_path / "old.epub"
    old.write_bytes(epub_bytes("OLD"))
    workspace = BookWorkspace.at(out, "fixture")
    workspace.initialize(source_epub=old, book_id="fixture")
    write_json(workspace.progress_path, {"state": "completed", "completed_cycles": 9})
    monkeypatch.setattr(books, "manifest_path", lambda _: path)
    monkeypatch.setattr(books, "get_output_root", lambda: out)

    def register(*args):
        manifest(path)
        return {"status": "ok", "summary": {"book": "fixture"}}

    monkeypatch.setattr(books, "call_novel_translator", register)
    monkeypatch.setattr(books, "summarize_book", lambda *args: {"registered": True})
    asyncio.run(books.upload_book(UploadFile(filename="fixture.epub", file=io.BytesIO(epub_bytes("NEW"))), replace=True))
    assert workspace.original_epub.read_bytes() == epub_bytes("NEW")
    assert read_json(workspace.progress_path)["state"] == "initialized"


def test_spa_rejects_files_outside_dist(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>fixture</html>")
    (tmp_path / "outside.txt").write_text("FIXTURE_OUTSIDE_DIST")
    monkeypatch.setenv("WEB_AUTH_TOKEN", "fixture-required-token")
    with TestClient(create_app(static_dir=dist)) as client:
        response = client.get("/%2e%2e/outside.txt")
    assert response.status_code == 404
    assert "FIXTURE_OUTSIDE_DIST" not in response.text


def test_provider_error_during_cancel_releases_slot(tmp_path, monkeypatch):
    import threading
    import translator.core.job_manager as jobs

    path = tmp_path / "manifest.json"
    manifest(path)
    monkeypatch.setattr(jobs, "manifest_path", lambda _: path)
    monkeypatch.setattr(jobs.broadcaster, "broadcast_sync", lambda *a, **kw: None)
    manager = JobManager(output_root=tmp_path / "output")
    item = manager.enqueue("fixture")
    item.status = "running"
    stop = threading.Event()
    pause = threading.Event()
    pause.set()
    manager._stop_events[item.id] = stop
    manager._pause_events[item.id] = pause

    class FailingPipeline:
        def __init__(self, **kwargs):
            pass

        def is_chapter_completed(self, chapter):
            return False

        def run_chapter(self, chapter, cycle):
            manager.stop_pipeline(item.id)
            raise RuntimeError("fixture provider failure during cancellation")

    monkeypatch.setattr(jobs, "ChapterPipeline", FailingPipeline)
    manager._run_queue_worker(item, stop, pause)
    status = manager.get_status()
    assert item.status == "cancelled"
    assert status.running_count == 0
    assert item.id not in manager._stop_events
