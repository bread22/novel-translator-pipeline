from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from translator.core import job_manager as job_manager_module
from translator.core.job_manager import JobManager
from translator.core.workspace import write_json
from translator.web.models import PipelineStartRequest
from translator.web.routes import tasks


def test_terminal_task_controls_are_rejected(tmp_path: Path, monkeypatch) -> None:
    manifest_file = tmp_path / "manifest.json"
    write_json(manifest_file, {"book": "book-1", "title": "Test Book", "chapters": []})
    monkeypatch.setattr(job_manager_module, "manifest_path", lambda _book_id: manifest_file)

    manager = JobManager(output_root=tmp_path / "output")
    item = manager.enqueue("book-1", options=PipelineStartRequest(book_id="book-1"))
    item.status = "completed"
    monkeypatch.setattr(tasks, "job_manager", manager)

    for action in (tasks.pause_pipeline, tasks.resume_pipeline, tasks.stop_pipeline):
        with pytest.raises(HTTPException) as exc:
            action(item.id)
        assert exc.value.status_code == 409
