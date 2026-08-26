from pathlib import Path

import pytest

from translator.core.workspace import write_json
from translator.web.models import RetranslateParagraphRequest
from translator.web.routes import tasks


@pytest.mark.xfail(
    strict=True,
    reason="retranslate_paragraph searches paragraph_id globally and ignores chapter_id",
)
def test_retranslate_uses_the_requested_chapter_when_ids_repeat(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {
        "book": "book-1", "title": "Duplicate IDs", "chapters": [
            {"id": "c1", "paragraphs": [{"id": "p1", "source": "first", "translated": "old first"}]},
            {"id": "c2", "paragraphs": [{"id": "p1", "source": "requested chapter source", "translated": "old second"}]},
        ],
    })
    source_chars: list[int] = []

    class FakeTranslator:
        def __init__(self, **_kwargs) -> None:
            pass

        def __call__(self, *_args, **kwargs):
            source_chars.append(kwargs["source_chars"])
            return {"status": "ok"}

    monkeypatch.setattr(tasks, "manifest_path", lambda _book: manifest)
    monkeypatch.setattr(tasks, "load_config", lambda: {"roles": {"primary_translator": "fake"}})
    monkeypatch.setattr(tasks, "ProviderTranslator", FakeTranslator)

    tasks.retranslate_paragraph(
        RetranslateParagraphRequest(book_id="book-1", chapter_id="c2", paragraph_id="p1")
    )

    assert source_chars == [len("requested chapter source")]
