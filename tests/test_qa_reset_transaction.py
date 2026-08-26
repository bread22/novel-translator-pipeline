from __future__ import annotations

from pathlib import Path

import pytest

from translator.core.workspace import BookWorkspace, read_json, write_json
from translator.web.routes import books


def test_reset_failure_does_not_publish_partial_manifest(tmp_path: Path, monkeypatch) -> None:
    manifest_file = tmp_path / "data" / "books" / "book-1" / "manifest.json"
    write_json(
        manifest_file,
        {
            "book": "book-1",
            "title": "Test Book",
            "chapters": [{"id": "c1", "paragraphs": [{"id": "p1", "translated": "已有译文"}]}],
        },
    )
    output_root = tmp_path / "output"
    workspace = BookWorkspace.at(output_root, "Test Book")
    workspace.initialize(book_id="book-1")
    write_json(workspace.glossary_path, {"book": "book-1", "terms": [{"source": "原词", "target": "旧译"}]})

    monkeypatch.setattr(books, "manifest_path", lambda _book_id: manifest_file)
    monkeypatch.setattr(books, "get_output_root", lambda: output_root)
    monkeypatch.setattr(books.job_manager, "cancel_book_and_wait", lambda _book_id: True)
    monkeypatch.setattr(books, "call_novel_translator", lambda *_args: {"status": "ok"})

    def fail_reset(self: BookWorkspace, book_id: str = "") -> None:
        write_json(self.glossary_path, {"book": book_id, "terms": [{"source": "被破坏", "target": "状态"}]})
        raise OSError("workspace reset failed")

    monkeypatch.setattr(BookWorkspace, "reset", fail_reset)

    with pytest.raises(OSError, match="workspace reset failed"):
        books.reset_book("book-1")

    assert read_json(manifest_file)["chapters"][0]["paragraphs"][0]["translated"] == "已有译文"
    assert read_json(workspace.glossary_path)["terms"][0]["source"] == "原词"


def test_reset_removes_persistent_event_history(tmp_path: Path) -> None:
    workspace = BookWorkspace.at(tmp_path / "output", "Test Book")
    workspace.initialize(book_id="book-1")
    events_file = workspace.data_dir / "events.jsonl"
    events_file.write_text('{"event":"pipeline_completed"}\n', encoding="utf-8")

    workspace.reset(book_id="book-1")

    assert not events_file.exists()


def test_reset_manifest_publish_failure_restores_workspace(tmp_path: Path, monkeypatch) -> None:
    manifest_file = tmp_path / "data" / "books" / "book-1" / "manifest.json"
    write_json(
        manifest_file,
        {
            "book": "book-1",
            "title": "Test Book",
            "chapters": [{"id": "c1", "paragraphs": [{"id": "p1", "translated": "已有译文"}]}],
        },
    )
    output_root = tmp_path / "output"
    workspace = BookWorkspace.at(output_root, "Test Book")
    workspace.initialize(book_id="book-1")
    write_json(workspace.glossary_path, {"book": "book-1", "terms": [{"source": "原词", "target": "旧译"}]})

    monkeypatch.setattr(books, "manifest_path", lambda _book_id: manifest_file)
    monkeypatch.setattr(books, "get_output_root", lambda: output_root)
    monkeypatch.setattr(books.job_manager, "cancel_book_and_wait", lambda _book_id: True)
    monkeypatch.setattr(books, "call_novel_translator", lambda *_args: {"status": "ok"})

    real_write_json = books.write_json
    failed_once = False

    def fail_manifest_publish_once(path: Path, value) -> None:
        nonlocal failed_once
        if path == manifest_file and not failed_once:
            failed_once = True
            raise OSError("manifest publish failed")
        real_write_json(path, value)

    monkeypatch.setattr(books, "write_json", fail_manifest_publish_once)

    with pytest.raises(OSError, match="manifest publish failed"):
        books.reset_book("book-1")

    assert read_json(manifest_file)["chapters"][0]["paragraphs"][0]["translated"] == "已有译文"
    assert read_json(workspace.glossary_path)["terms"][0]["source"] == "原词"
