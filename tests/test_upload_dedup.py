"""Upload identity regressions: real source parsing, isolated storage, no providers."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import threading
import zipfile

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient

from translator.core import novel_tool
from translator.core.book_fingerprints import BODY_VERSION, FINGERPRINT_FILE, body_sha256, file_sha256
from translator.core.job_manager import JobManager
from translator.core.workspace import BookWorkspace, read_json, write_json
from translator.web.routes import books


def epub(text="Original prose.", *, title="Original", compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", '''<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0"><rootfiles><rootfile full-path="OEBPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>''')
        archive.writestr("OEBPS/book.opf", f'''<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">fixture</dc:identifier><dc:title>{title}</dc:title><dc:language>en</dc:language></metadata><manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c1"/><itemref idref="c2"/></spine></package>''')
        archive.writestr("OEBPS/c1.xhtml", f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head><body><p>{text}</p></body></html>')
        archive.writestr("OEBPS/c2.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Second chapter.</p></body></html>')
    return stream.getvalue()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "data" / "books"
    output = tmp_path / "output"
    api = novel_tool._vendor_api(novel_tool.resolve_novel_translator_root())
    manager = JobManager(output_root=output, config={})
    monkeypatch.setattr(books, "job_manager", manager)
    monkeypatch.setattr(books, "NOVEL_TRANSLATOR_ROOT", tmp_path)
    monkeypatch.setattr(books, "manifest_path", lambda book_id: root / book_id / "manifest.json")
    monkeypatch.setattr(books, "get_output_root", lambda: output)
    calls = []

    def register(*args):
        calls.append(args)
        path = Path(args[args.index("--path") + 1])
        book = api.load_source_book(path, title=args[args.index("--title") + 1], epub_config=api.EpubConfig())
        book.id = args[args.index("--id") + 1]
        api.save_book(root, book, path)
        return {"status": "ok", "summary": {"book": book.id}}

    monkeypatch.setattr(books, "call_novel_translator", register)
    return root, output, manager, calls


def upload(name, data, replace=False):
    return asyncio.run(books.upload_book(UploadFile(filename=name, file=io.BytesIO(data)), replace=replace))


def tree_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def assert_duplicate(name, data, existing, matched_by, **kwargs):
    with pytest.raises(HTTPException) as exc:
        upload(name, data, **kwargs)
    assert exc.value.status_code == 409
    assert exc.value.detail["existing_book_id"] == existing
    assert exc.value.detail["matched_by"] == matched_by


def test_renamed_identical_file_is_blocked_before_registration(storage):
    root, output, manager, calls = storage
    upload("old-name.txt", b"First paragraph.\n\nSecond paragraph.")
    before = tree_bytes(root / "old-name"), tree_bytes(output)
    assert_duplicate("new-name.txt", b"First paragraph.\n\nSecond paragraph.", "old-name", "source_sha256")
    assert len(calls) == 1
    assert not (root / "new-name").exists()
    assert before == (tree_bytes(root / "old-name"), tree_bytes(output))
    assert manager.get_status().total_items == 0
    saved = read_json(root / "old-name" / FINGERPRINT_FILE)
    assert saved["source_sha256"] == file_sha256(root / "old-name" / "source.txt")
    assert saved["body_version"] == BODY_VERSION
    assert saved["body_sha256"]


def test_repacked_epub_body_conflict_rolls_back(storage):
    root, output, _, _ = storage
    original = epub()
    repacked = epub(title="Changed metadata", compression=zipfile.ZIP_DEFLATED)
    assert original != repacked
    upload("old.epub", original)
    before = tree_bytes(root / "old"), tree_bytes(output)
    assert_duplicate("renamed.epub", repacked, "old", "body_sha256")
    assert not (root / "renamed").exists()
    assert before == (tree_bytes(root / "old"), tree_bytes(output))


def test_legacy_id_with_completed_translations_and_no_hashes(storage):
    root, output, _, _ = storage
    upload("old-id.txt", b"Original prose.\n\nMore text.")
    path = root / "old-id" / "manifest.json"
    manifest = read_json(path)
    for paragraph in manifest["chapters"][0]["paragraphs"]:
        paragraph["translated"] = "Completed translation"
    write_json(path, manifest)
    (root / "old-id" / FINGERPRINT_FILE).unlink()
    before = tree_bytes(root / "old-id"), tree_bytes(output)
    assert_duplicate("新版カナ.txt", b"Original prose.\n\nMore text.", "old-id", "source_sha256")
    assert_duplicate("another.txt", b"Original   prose.\r\n\r\nMore text.\r\n", "old-id", "body_sha256")
    assert before == (tree_bytes(root / "old-id"), tree_bytes(output))


def test_legacy_missing_source_still_matches_original_manifest(storage):
    root, _, _, _ = storage
    upload("old.txt", b"Original prose.")
    (root / "old" / "source.txt").unlink()
    (root / "old" / FINGERPRINT_FILE).unlink()
    assert_duplicate("new.txt", b"Original prose.\n", "old", "body_sha256")


def test_different_content_imports_and_explicit_same_id_replacement_works(storage):
    root, _, _, _ = storage
    upload("one.txt", b"Version one.")
    assert upload("two.txt", b"Version two.").id == "two"
    assert upload("one.txt", b"Revised version.", replace=True).id == "one"
    assert (root / "one" / "source.txt").read_bytes() == b"Revised version."
    # The prior hash must not block a fresh import after replacement.
    assert upload("three.txt", b"Version one.").id == "three"


def test_replace_does_not_bypass_other_book_identity_and_restores_target(storage):
    root, output, _, _ = storage
    upload("one.txt", b"First book.")
    upload("two.txt", b"Second book.")
    before = tree_bytes(root / "two"), tree_bytes(output)
    assert_duplicate("two.txt", b"First book.", "one", "source_sha256", replace=True)
    assert_duplicate("two.txt", b"First   book.\n", "one", "body_sha256", replace=True)
    assert before == (tree_bytes(root / "two"), tree_bytes(output))


def test_same_id_still_requires_explicit_replacement(storage):
    upload("one.txt", b"Source.")
    with pytest.raises(HTTPException) as exc:
        upload("one.txt", b"New source.")
    assert exc.value.status_code == 409


@pytest.mark.parametrize("contents", [(b"Same prose.", b"Same prose."), (b"Same prose.", b"Same   prose.\n")])
def test_concurrent_different_ids_only_one_import_succeeds(storage, contents):
    root, _, _, calls = storage
    barrier = threading.Barrier(2)

    def run(index):
        barrier.wait(timeout=5)
        try:
            upload(f"book-{index}.txt", contents[index])
            return 200
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(run, (0, 1))) == [200, 409]
    assert len(list(root.glob("*/manifest.json"))) == 1
    assert len(calls) == (1 if contents[0] == contents[1] else 2)


def test_http_conflict_exposes_existing_book_and_reason(storage, monkeypatch):
    from translator.web.app import create_app

    monkeypatch.setenv("WEB_AUTH_TOKEN", "")
    upload("old.txt", b"Source.")
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/books/upload", files={"file": ("new.txt", b"Source.", "text/plain")})
    assert response.status_code == 409
    assert "old" in response.text
    assert "source_sha256" in response.text
    assert "DUPLICATE_BOOK" in response.text


def test_body_normalization_is_conservative():
    def manifest(*chapters):
        return {"chapters": [{"paragraphs": [{"source": text} for text in chapter]} for chapter in chapters]}

    base = manifest(["Café  text.", "Second paragraph."], ["Last chapter."])
    changed = manifest(["Cafe\u0301\r\ntext.", " Second paragraph. "], ["Last chapter."])
    assert body_sha256(base) == body_sha256(changed)
    for other in [
        manifest(["Cafe text.", "Second paragraph."], ["Last chapter."]),
        manifest(["Café text!", "Second paragraph."], ["Last chapter."]),
        manifest(["Café text. Second paragraph."], ["Last chapter."]),
        manifest(["Last chapter."], ["Café text.", "Second paragraph."]),
        manifest(["Café text.", "Second paragraph.", "Last chapter."]),
    ]:
        assert body_sha256(base) != body_sha256(other)
    assert body_sha256({}) is None
    assert body_sha256(manifest([" \n\t"])) is None
    base["chapters"].insert(0, {"role": "cover", "paragraphs": [{"source": "Cover text"}]})
    base["chapters"].append({"role": "toc", "paragraphs": [{"source": "Contents"}]})
    base["chapters"][1]["paragraphs"][0]["translated"] = "Translation"
    assert body_sha256(base) == body_sha256(changed)


def test_empty_body_does_not_conflate_distinct_files(storage):
    upload("empty-one.txt", b"\n")
    assert upload("empty-two.txt", b"\n\n").id == "empty-two"


def test_saved_hash_survives_missing_original(storage):
    root, _, _, _ = storage
    upload("old.txt", b"Original prose.")
    (root / "old" / "source.txt").unlink()
    assert_duplicate("new.txt", b"Original prose.", "old", "source_sha256")


def test_failed_fingerprint_write_restores_replaced_book(storage, monkeypatch):
    root, output, _, _ = storage
    upload("one.txt", b"Original prose.")
    before = tree_bytes(root / "one"), tree_bytes(output)
    original_write = books.write_json

    def fail(path, data):
        if path.name == FINGERPRINT_FILE:
            raise OSError("fingerprint write failure")
        return original_write(path, data)

    monkeypatch.setattr(books, "write_json", fail)
    with pytest.raises(OSError, match="fingerprint write failure"):
        upload("one.txt", b"Different text.", replace=True)
    assert before == (tree_bytes(root / "one"), tree_bytes(output))


def test_body_conflict_does_not_restore_untouched_peer_workspace(storage, monkeypatch):
    root, output, _, _ = storage
    upload("old-id.txt", b"Original prose.")
    path = root / "old-id" / "manifest.json"
    manifest = read_json(path)
    # Legacy ID differs, but the title and therefore output path are identical.
    manifest["title"] = "new-id"
    write_json(path, manifest)
    workspace = BookWorkspace.at(output, "new-id")
    workspace.initialize(book_id="old-id")
    write_json(workspace.progress_path, {"state": "before"})
    register = books.call_novel_translator

    def concurrent_update(*args):
        result = register(*args)
        # A writer on the existing book advances after the upload backup.
        write_json(workspace.progress_path, {"state": "advanced"})
        return result

    monkeypatch.setattr(books, "call_novel_translator", concurrent_update)
    assert_duplicate("new-id.txt", b"Original   prose.\n", "old-id", "body_sha256")
    assert read_json(workspace.progress_path) == {"state": "advanced"}
    assert not (root / "new-id").exists()
