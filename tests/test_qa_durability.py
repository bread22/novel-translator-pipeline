from __future__ import annotations

from pathlib import Path

import pytest

from translator.core.job_manager import JobManager
from translator.core.workspace import write_json
from translator.web.models import EnqueueRequest
from translator.web.routes.queue import enqueue_items


def test_queue_mutation_does_not_claim_success_when_state_write_fails(tmp_path: Path, monkeypatch) -> None:
    manifest = tmp_path / "data" / "books" / "book-1" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    write_json(manifest, {
        "book": "book-1",
        "title": "Durability Book",
        "chapters": [],
    })

    monkeypatch.setattr("translator.core.job_manager.manifest_path", lambda _book_id: manifest)

    manager = JobManager(output_root=tmp_path / "output")

    def fail_state_write(path: Path, payload: object) -> Path:
        if path.name == "job_state.v2.json":
            raise OSError("simulated state-volume failure")
        return path

    monkeypatch.setattr("translator.core.job_manager.write_json", fail_state_write)
    monkeypatch.setattr("translator.web.routes.queue.job_manager", manager)

    with pytest.raises(OSError, match="state-volume failure"):
        enqueue_items(EnqueueRequest(book_ids=["book-1"]))

    assert not manager.state_file.exists()

    reopened = JobManager(output_root=tmp_path / "output")
    assert reopened.get_status().total_items == 0
