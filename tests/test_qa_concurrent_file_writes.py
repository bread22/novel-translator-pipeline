from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading

from translator.core.workspace import write_json
from translator.core.workspace import BookWorkspace
from translator.web.models import GlossaryCreateRequest, GlossaryUpsert, ParagraphUpdateRequest
from translator.web.routes import books, knowledge


def test_concurrent_reader_edits_preserve_both_paragraphs(tmp_path: Path, monkeypatch) -> None:
    manifest_path = tmp_path / "data" / "books" / "book-1" / "manifest.json"
    manifest_path.parent.mkdir(parents=True)
    initial = {
        "book": "book-1",
        "title": "Concurrent Book",
        "chapters": [{
            "id": "c1",
            "title": "Chapter 1",
            "paragraphs": [
                {"id": "p1", "source": "一", "translated": ""},
                {"id": "p2", "source": "二", "translated": ""},
            ],
        }],
    }
    write_json(manifest_path, initial)
    barrier = threading.Barrier(2)

    monkeypatch.setattr(books, "manifest_path", lambda _book_id: manifest_path)

    def edit(paragraph_id: str, translated: str) -> dict:
        barrier.wait(2)
        return books.update_paragraph(
            "book-1", paragraph_id, ParagraphUpdateRequest(translated=translated)
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(edit, "p1", "译文一")
        second = pool.submit(edit, "p2", "译文二")
        assert first.result(timeout=2)["status"] == "ok"
        assert second.result(timeout=2)["status"] == "ok"

    stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    paragraphs = {item["id"]: item for item in stored["chapters"][0]["paragraphs"]}
    assert paragraphs["p1"]["translated"] == "译文一"
    assert paragraphs["p2"]["translated"] == "译文二"


def test_concurrent_glossary_updates_preserve_both_terms(tmp_path: Path, monkeypatch) -> None:
    workspace = BookWorkspace.at(tmp_path / "output", "Concurrent Glossary")
    workspace.initialize(book_id="book-1")
    initial = {"terms": [], "conflicts": []}
    write_json(workspace.glossary_path, initial)
    barrier = threading.Barrier(2)

    def get_workspace(_book_id: str) -> BookWorkspace:
        barrier.wait(2)
        return workspace

    monkeypatch.setattr(knowledge, "get_workspace_for_book", get_workspace)

    def add_term(source: str, target: str) -> dict:
        return knowledge.update_glossary(
            "book-1",
            GlossaryCreateRequest(terms=[GlossaryUpsert(source=source, target=target)]),
        ).model_dump()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(add_term, "词一", "译一")
        second = pool.submit(add_term, "词二", "译二")
        assert first.result(timeout=2)["book_id"] == "book-1"
        assert second.result(timeout=2)["book_id"] == "book-1"

    stored = json.loads(workspace.glossary_path.read_text(encoding="utf-8"))
    terms = {item["source"]: item for item in stored["terms"]}
    assert terms["词一"]["target"] == "译一"
    assert terms["词二"]["target"] == "译二"
