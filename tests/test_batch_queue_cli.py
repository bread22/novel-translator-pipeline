from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scripts import translation_queue


class FakeManager:
    created: "FakeManager | None" = None

    def __init__(self, output_root: Path) -> None:
        self.output_root = output_root
        self.items: dict[str, SimpleNamespace] = {}
        self.resumed = False
        FakeManager.created = self

    def update_config(self, **_kwargs) -> None:
        return None

    def enqueue(self, book_id: str, options, book_name: str):
        item = SimpleNamespace(id=f"task-{book_id}")
        self.items[item.id] = SimpleNamespace(status="completed")
        return item

    def resume_queue(self) -> None:
        self.resumed = True

    def get_task(self, task_id: str):
        return self.items[task_id]


def test_batch_cli_registers_and_runs_through_job_manager(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "Book.epub").write_bytes(b"epub")
    output = tmp_path / "output"
    translated = tmp_path / "translated"
    config = {
        "paths": {"output_root": str(output)},
        "queue": {
            "source_root": str(source),
            "translated_root": str(translated),
            "max_cycles": 2,
        },
    }
    monkeypatch.setattr(translation_queue, "load_config", lambda: config)
    monkeypatch.setattr(translation_queue, "JobManager", FakeManager)
    monkeypatch.setattr(translation_queue, "registered_books", lambda: {})
    monkeypatch.setattr(translation_queue, "ensure_book", lambda _source, _registered: "book-1")

    assert translation_queue.run_batch(poll_interval=0.01) == 0
    assert FakeManager.created is not None and FakeManager.created.resumed


def test_ensure_book_uses_existing_hash_and_validates_new_result(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "Book.epub"
    source.write_bytes(b"same")
    assert translation_queue.ensure_book(source, {translation_queue.sha256(source): "existing"}) == "existing"

    monkeypatch.setattr(translation_queue, "call_novel_translator", lambda *_args: {"status": "ok", "summary": {"book": "new"}})
    registered: dict[str, str] = {}
    assert translation_queue.ensure_book(source, registered) == "new"
    assert registered[translation_queue.sha256(source)] == "new"
