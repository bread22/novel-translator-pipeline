"""Deterministic interleavings: barriers/events, fixture storage, no live providers."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import threading
from unittest.mock import Mock

import pytest

from translator.core import book_store
from translator.core.book_store import BookRepository
from translator.core.job_manager import BookBusyError, JobManager
from translator.core.workspace import BookWorkspace, read_json, write_json
from translator.providers.translator import ProviderTranslator
from translator.web.models import ParagraphUpdateRequest, PipelineStartRequest
from translator.web.routes import books

pytestmark = pytest.mark.interleaving


@pytest.fixture
def book_fixture(tmp_path, monkeypatch):
    import translator.core.job_manager as jobs

    manifest = tmp_path / "vendor" / "data" / "books" / "fixture" / "manifest.json"
    write_json(
        manifest,
        {
            "book": "fixture",
            "title": "Fixture",
            "chapters": [
                {
                    "id": "c1",
                    "paragraphs": [
                        {"id": "p1", "source": "one", "translated": ""},
                        {"id": "p2", "source": "two", "translated": ""},
                    ],
                }
            ],
        },
    )
    ws = BookWorkspace.at(tmp_path / "output", "Fixture")
    ws.initialize(book_id="fixture")
    source = manifest.parent / "source.txt"
    source.write_text("old source")
    upload = tmp_path / "new.txt"
    upload.write_text("new source")
    manager = JobManager(output_root=tmp_path / "output", config={})
    monkeypatch.setattr(jobs.broadcaster, "broadcast_sync", lambda *a, **kw: None)
    monkeypatch.setattr(jobs, "manifest_path", lambda _: manifest)
    monkeypatch.setattr(books, "manifest_path", lambda _: manifest)
    monkeypatch.setattr(books, "get_output_root", lambda: tmp_path / "output")
    monkeypatch.setattr(books, "job_manager", manager)
    return manifest, ws, upload, manager


@pytest.mark.parametrize("edited_id", ["p1", "p2"])
def test_edit_while_translation_is_in_flight(book_fixture, monkeypatch, edited_id):
    manifest, ws, _, _ = book_fixture
    translator = ProviderTranslator(novel_root=manifest.parent, manifest=manifest, config={})
    entered, release = threading.Event(), threading.Event()

    def request(*args):
        entered.set()
        assert release.wait(3), "provider fixture was not released"
        return [{"id": "p1", "text": "automatic"}], {"status": "ok"}

    monkeypatch.setattr(translator, "_request", request)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(translator, "fixture", "fixture", ["p1"], source_chars=3, max_tokens=100)
        try:
            assert entered.wait(3)
            books.update_paragraph("fixture", edited_id, ParagraphUpdateRequest(translated="manual"))
        finally:
            release.set()
        result = pending.result(timeout=3)
    paragraphs = BookRepository.paragraphs(read_json(manifest))
    assert paragraphs[edited_id]["translated"] == "manual"
    if edited_id == "p1":
        assert result["reason"] == "write_conflict"
    else:
        assert result["status"] == "ok"
        assert paragraphs["p1"]["translated"] == "automatic"


def test_edit_arriving_at_translation_publish_preserves_both_records(book_fixture, monkeypatch):
    manifest, _, _, _ = book_fixture
    repository = BookRepository(manifest_path=manifest)
    before = repository.manifest.snapshot().value
    publishing, release, editing = threading.Event(), threading.Event(), threading.Event()
    original = book_store.write_json

    def delayed_write(path, value):
        if path == manifest and BookRepository.paragraphs(value)["p1"]["translated"] == "automatic" and not publishing.is_set():
            publishing.set()
            assert release.wait(3)
        return original(path, value)

    monkeypatch.setattr(book_store, "write_json", delayed_write)

    def edit():
        editing.set()
        return books.update_paragraph("fixture", "p2", ParagraphUpdateRequest(translated="manual"))

    with ThreadPoolExecutor(2) as pool:
        translation = pool.submit(repository.update_paragraphs, {"p1": "automatic"}, baseline=before)
        try:
            assert publishing.wait(3)
            manual = pool.submit(edit)
            assert editing.wait(3)
        finally:
            release.set()
        translation.result(timeout=3)
        manual.result(timeout=3)
    current = BookRepository.paragraphs(read_json(manifest))
    assert current["p1"]["translated"] == "automatic"
    assert current["p2"]["translated"] == "manual"


def test_cancel_and_provider_failure_release_slot_and_dispatch_next(book_fixture, monkeypatch):
    import translator.core.job_manager as jobs

    _, _, _, manager = book_fixture
    entered, release, next_entered = threading.Event(), threading.Event(), threading.Event()

    class Pipeline:
        def __init__(self, **kwargs):
            self.book = kwargs["book"]

        def is_chapter_completed(self, chapter):
            return False

        def run_chapter(self, chapter, cycle):
            if self.book == "fixture":
                entered.set()
                assert release.wait(3)
                raise RuntimeError("fixture provider failed after cancellation")
            next_entered.set()
            return {"translated": 0, "reviewed": 0}

    monkeypatch.setattr(jobs, "ChapterPipeline", Pipeline)
    request = PipelineStartRequest(book_id="fixture", finalize=False, max_cycles=1)
    first = manager.start_pipeline(request)
    try:
        assert entered.wait(3)
        second = manager.enqueue("next", options=PipelineStartRequest(book_id="next", finalize=False, max_cycles=1))
        first_thread = manager._running_threads[first.task_id]
        assert manager.stop_pipeline(first.task_id).status == "cancelling"
    finally:
        release.set()
    first_thread.join(3)
    assert not first_thread.is_alive()
    assert next_entered.wait(3)
    with manager._lock:
        remaining = list(manager._running_threads.values())
    for thread in remaining:
        thread.join(3)
        assert not thread.is_alive()
    assert manager.get_task(first.task_id).status == "cancelled"
    assert manager.get_task(second.id).status == "completed"
    assert manager.get_status().running_count == 0


def hashes(*roots):
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for root in roots for p in root.rglob("*") if p.is_file()}


def test_replace_rejects_active_job_before_any_storage_change(book_fixture, monkeypatch):
    manifest, ws, upload, manager = book_fixture
    manager.enqueue("fixture")
    before = hashes(manifest.parent, ws.root)
    register = Mock(side_effect=AssertionError("registration must not run"))
    monkeypatch.setattr(books, "call_novel_translator", register)
    with pytest.raises(BookBusyError):
        books._register_uploaded_book(upload, ".txt", "fixture", "Fixture", True)
    register.assert_not_called()
    assert hashes(manifest.parent, ws.root) == before


def test_replace_reservation_blocks_enqueue_batch_and_retry(book_fixture, monkeypatch):
    manifest, ws, upload, manager = book_fixture
    item = manager.enqueue("fixture")
    manager.stop_pipeline(item.id)
    entered, release = threading.Event(), threading.Event()

    def register(*args):
        entered.set()
        assert release.wait(3)
        return {"status": "ok", "summary": {"book": "fixture"}}

    monkeypatch.setattr(books, "call_novel_translator", register)
    monkeypatch.setattr(books, "summarize_book", lambda *args: {"status": "ok"})
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(books._register_uploaded_book, upload, ".txt", "fixture", "Fixture", True)
        try:
            assert entered.wait(3)
            for action in [
                lambda: manager.enqueue("fixture"),
                lambda: manager.enqueue_batch(["other", "fixture"]),
                lambda: manager.retry_item(item.id),
            ]:
                with pytest.raises(BookBusyError):
                    action()
            assert manager.get_status().total_items == 1
        finally:
            release.set()
        assert future.result(timeout=3)["status"] == "ok"
    assert manager.enqueue("fixture").status == "pending"


def test_replace_failure_restores_manifest_source_and_workspace(book_fixture, monkeypatch):
    manifest, ws, upload, manager = book_fixture
    write_json(ws.progress_path, {"state": "completed", "completed_cycles": 9})
    before = hashes(manifest.parent, ws.root)

    def register(*args):
        write_json(manifest, {"book": "fixture", "title": "Fixture", "chapters": []})
        (manifest.parent / "source.txt").write_text("new source")
        return {"status": "ok", "summary": {"book": "fixture"}}

    def fail_initialize(*args, **kwargs):
        raise OSError("fixture workspace disk failure")

    monkeypatch.setattr(books, "call_novel_translator", register)
    monkeypatch.setattr(BookWorkspace, "initialize", fail_initialize)
    with pytest.raises(OSError, match="workspace disk failure"):
        books._register_uploaded_book(upload, ".txt", "fixture", "Fixture", True)
    assert hashes(manifest.parent, ws.root) == before
    assert manager.enqueue("fixture").status == "pending"


def test_replacement_conflict_is_http_409(book_fixture, monkeypatch):
    from fastapi.testclient import TestClient
    from translator.web.app import create_app

    manifest, ws, _, manager = book_fixture
    monkeypatch.setenv("WEB_AUTH_TOKEN", "")
    manager.enqueue("fixture")
    before = hashes(manifest.parent, ws.root)
    with TestClient(create_app()) as client:
        result = client.post("/api/v1/books/upload?replace=true", files={"file": ("fixture.txt", b"new source", "text/plain")})
    assert result.status_code == 409
    assert hashes(manifest.parent, ws.root) == before


def test_spa_rejects_symlink_escape_but_serves_valid_asset(tmp_path):
    from fastapi.testclient import TestClient
    from translator.web.app import create_app

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>fixture</html>")
    (dist / "public.txt").write_text("public fixture")
    outside = tmp_path / "outside.txt"
    outside.write_text("private fixture")
    (dist / "link.txt").symlink_to(outside)
    with TestClient(create_app(static_dir=dist)) as client:
        assert client.get("/link.txt").status_code == 404
        assert client.get("/public.txt").text == "public fixture"
        assert client.get("/reader").text == "<html>fixture</html>"
