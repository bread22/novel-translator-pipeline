from __future__ import annotations

from pathlib import Path

from translator.core.workspace import write_json
from translator.web.models import TaskStatusResponse
from translator.web.routes import books


def test_book_summary_exposes_active_task_state(tmp_path: Path, monkeypatch) -> None:
    manifest = {
        "book": "book-1",
        "title": "Active Book",
        "chapters": [{"id": "c1", "paragraphs": [{"id": "p1", "translated": "部分译文"}]}],
    }
    active_task = TaskStatusResponse(
        task_id="task-1", book_id="book-1", status="running", phase="translating"
    )
    monkeypatch.setattr(books.job_manager, "get_task", lambda _book_id: active_task)

    summary = books.summarize_book("book-1", manifest, tmp_path / "output")

    assert summary.status == "translating"


def test_chapter_is_not_reviewed_without_review_artifact(tmp_path: Path, monkeypatch) -> None:
    manifest_path = tmp_path / "manifest.json"
    write_json(manifest_path, {
        "book": "book-1",
        "title": "Translated But Unreviewed",
        "chapters": [{
            "id": "c1",
            "title": "Chapter 1",
            "paragraphs": [{"id": "p1", "source": "源", "translated": "译"}],
        }],
    })
    monkeypatch.setattr(books, "manifest_path", lambda _book_id: manifest_path)

    result = books.list_chapters("book-1")

    assert result[0].status == "translated"
