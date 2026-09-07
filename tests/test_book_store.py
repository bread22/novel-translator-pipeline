from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from translator.core import book_store
from translator.core.book_store import BookRepository, JsonDocument, VersionConflict
from translator.core.workspace import BookWorkspace, read_json, write_json, json_file_lock


pytestmark = pytest.mark.interleaving


def test_document_rejects_stale_revision_without_changing_bytes(tmp_path):
    document = JsonDocument(tmp_path / "progress.json")
    snapshot = document.snapshot(default={})
    document.patch({"state": "running"})
    before = document.path.read_bytes()
    with pytest.raises(VersionConflict):
        document.replace({"state": "completed"}, expected_revision=snapshot.revision)
    assert document.path.read_bytes() == before


def test_paragraph_updates_merge_and_detect_selected_record_conflicts(tmp_path):
    repository = BookRepository(manifest_path=tmp_path / "manifest.json")
    baseline = {"chapters": [{"paragraphs": [{"id": "p1", "translated": ""}, {"id": "p2", "translated": ""}]}]}
    write_json(repository.manifest.path, baseline)
    repository.update_paragraphs({"p2": "manual"})
    repository.update_paragraphs({"p1": "automatic"}, baseline=baseline)
    assert repository.paragraphs(repository.manifest.snapshot().value)["p2"]["translated"] == "manual"
    with pytest.raises(VersionConflict):
        repository.update_paragraphs({"p1": "stale"}, baseline=baseline)


def test_glossary_projection_failure_rolls_back_exact_bytes(tmp_path, monkeypatch):
    ws = BookWorkspace.at(tmp_path, "fixture")
    ws.initialize(book_id="fixture")
    original = {p: p.read_bytes() for p in [ws.glossary_path, ws.novel_translator_terms_path]}
    write = book_store.write_json

    def fail_projection(path, data):
        if path == ws.novel_translator_terms_path:
            raise OSError("projection storage failure")
        return write(path, data)

    monkeypatch.setattr(book_store, "write_json", fail_projection)
    with pytest.raises(OSError, match="projection storage failure"):
        BookRepository(workspace=ws).save_glossary(
            {
                "terms": [
                    {
                        "source": "A",
                        "target": "B",
                        "status": "active",
                        "category": "person",
                        "manual": True,
                        "evidence": [{"chapter_id": "c1", "paragraph_id": "p1"}],
                    }
                ]
            }
        )
    assert {p: p.read_bytes() for p in original} == original


def test_progress_patches_preserve_other_fields_and_locks_are_reentrant(tmp_path):
    document = JsonDocument(tmp_path / "progress.json")
    with json_file_lock(document.path):
        document.patch({"state": "running"})
    barrier = threading.Barrier(2)

    def patch(key):
        barrier.wait(timeout=2)
        document.patch({key: 1})

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(patch, ["reviewed", "translated"]))
    assert read_json(document.path) == {"state": "running", "reviewed": 1, "translated": 1}
