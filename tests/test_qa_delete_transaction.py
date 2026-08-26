from __future__ import annotations

from pathlib import Path
import shutil as stdlib_shutil

import pytest
from fastapi import HTTPException

from translator.core.workspace import BookWorkspace, write_json
from translator.web.routes import books


def test_delete_does_not_report_success_when_storage_removal_is_ignored(tmp_path: Path, monkeypatch) -> None:
    manifest_file = tmp_path / "data" / "books" / "book-1" / "manifest.json"
    write_json(manifest_file, {"book": "book-1", "title": "Test Book", "chapters": []})
    output_root = tmp_path / "output"
    workspace = BookWorkspace.at(output_root, "Test Book")
    workspace.initialize(book_id="book-1")

    monkeypatch.setattr(books, "manifest_path", lambda _book_id: manifest_file)
    monkeypatch.setattr(books, "get_output_root", lambda: output_root)
    monkeypatch.setattr(books, "NOVEL_TRANSLATOR_ROOT", tmp_path)
    monkeypatch.setattr(books.job_manager, "cancel_book_and_wait", lambda _book_id: True)

    class NoOpShutil:
        @staticmethod
        def rmtree(_path: Path, **_kwargs) -> None:
            return None

        copytree = staticmethod(stdlib_shutil.copytree)

    monkeypatch.setattr(books, "shutil", NoOpShutil)

    with pytest.raises(HTTPException):
        books.delete_book("book-1")

    assert workspace.root.exists()
    assert manifest_file.exists()


def test_delete_rolls_back_when_a_later_storage_root_fails(tmp_path: Path, monkeypatch) -> None:
    manifest_file = tmp_path / "data" / "books" / "book-1" / "manifest.json"
    write_json(manifest_file, {"book": "book-1", "title": "Test Book", "chapters": []})
    output_root = tmp_path / "output"
    workspace = BookWorkspace.at(output_root, "Test Book")
    workspace.initialize(book_id="book-1")
    data_book_dir = tmp_path / "data" / "books" / "book-1"

    monkeypatch.setattr(books, "manifest_path", lambda _book_id: manifest_file)
    monkeypatch.setattr(books, "get_output_root", lambda: output_root)
    monkeypatch.setattr(books, "NOVEL_TRANSLATOR_ROOT", tmp_path)
    monkeypatch.setattr(books.job_manager, "cancel_book_and_wait", lambda _book_id: True)

    class PartialShutil:
        @staticmethod
        def rmtree(path: Path, **kwargs) -> None:
            if Path(path).resolve() == workspace.root.resolve():
                stdlib_shutil.rmtree(path, **kwargs)

        copytree = staticmethod(stdlib_shutil.copytree)

    monkeypatch.setattr(books, "shutil", PartialShutil)

    with pytest.raises(HTTPException):
        books.delete_book("book-1")

    assert workspace.root.exists()
    assert data_book_dir.exists()
    assert manifest_file.exists()
