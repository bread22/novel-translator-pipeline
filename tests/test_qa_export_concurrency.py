from pathlib import Path
import threading
import time
import zipfile

from fastapi import HTTPException
import pytest

from translator.core.workspace import write_json
from translator.web.routes import books


@pytest.mark.xfail(
    strict=True,
    reason="export_book has no per-book lock around the shared workspace EPUB",
)
def test_concurrent_exports_both_publish_valid_artifacts(tmp_path: Path, monkeypatch) -> None:
    books_root = tmp_path / "data" / "books"
    manifest_file = books_root / "book-1" / "manifest.json"
    write_json(manifest_file, {
        "book": "book-1", "title": "Concurrent Export", "source_type": "txt", "chapters": [],
    })
    output = tmp_path / "output"
    translated = tmp_path / "translated"
    first_ready = threading.Event()
    second_started = threading.Event()
    state = {"export_calls": 0}
    state_lock = threading.Lock()

    def valid_epub(path: Path) -> None:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip")
            archive.writestr("META-INF/container.xml", "<container/>")

    def fake_novel_call(*args: str) -> dict:
        if args[0] == "export":
            with state_lock:
                state["export_calls"] += 1
                current = state["export_calls"]
            target = Path(args[args.index("--output") + 1])
            target.parent.mkdir(parents=True, exist_ok=True)
            if current == 1:
                valid_epub(target)
                first_ready.set()
                second_started.wait(1)
            else:
                first_ready.wait(1)
                target.write_bytes(b"partial concurrent output")
                second_started.set()
                time.sleep(0.1)
                valid_epub(target)
            return {"status": "ok"}
        return {"status": "valid"}

    monkeypatch.setattr(books, "manifest_path", lambda _book: manifest_file)
    monkeypatch.setattr(books, "get_output_root", lambda: output)
    monkeypatch.setattr(books, "load_config", lambda: {"queue": {"translated_root": str(translated)}})
    monkeypatch.setattr(books, "call_novel_translator", fake_novel_call)
    monkeypatch.setattr(books, "extract_book_metadata", lambda *_args: {"title_zh": "Concurrent Export", "author_zh": ""})
    monkeypatch.setattr(books, "inject_epub_metadata", lambda *_args, **_kwargs: None)

    results: list[dict | HTTPException] = []
    result_lock = threading.Lock()

    def run_export() -> None:
        try:
            result: dict | HTTPException = books.export_book("book-1", layout="preserve")
        except HTTPException as exc:
            result = exc
        with result_lock:
            results.append(result)

    threads = [threading.Thread(target=run_export) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert all(result.get("status") == "exported" for result in results if isinstance(result, dict))
    assert all(isinstance(result, dict) for result in results)
