from pathlib import Path

import pytest

from translator.core.workspace import BookWorkspace
from translator.web.routes.books import summarize_book


@pytest.mark.xfail(
    strict=True,
    reason="summarize_book treats any existing output.epub path as a usable EPUB",
)
def test_empty_epub_is_not_reported_as_downloadable(tmp_path: Path) -> None:
    workspace = BookWorkspace.at(tmp_path, "Corrupt EPUB")
    workspace.initialize(book_id="book-1")
    workspace.epub_path.write_bytes(b"")

    summary = summarize_book(
        "book-1",
        {"title": "Corrupt EPUB", "source_type": "txt", "chapters": [{"paragraphs": []}]},
        tmp_path,
    )

    assert summary.has_output_epub is False
