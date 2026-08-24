from __future__ import annotations

import json
from pathlib import Path

from translator.core.job_manager import JobManager
from translator.core.state_migrations import migrate_queue_state_v1


def _legacy_item(item_id: str, status: str) -> dict[str, object]:
    return {
        "id": item_id,
        "book_id": f"book-{item_id}",
        "book_name": f"Book {item_id}",
        "options": {"book_id": f"book-{item_id}"},
        "status": status,
        "order_index": 99,
        "enqueued_at": "2026-01-01T00:00:00+00:00",
    }


def _write_v1(path: Path) -> bytes:
    payload = {
        "is_paused": True,
        "concurrency": 9,
        "stop_on_error": True,
        "pending_order": ["paused", "pending", "running", "missing"],
        "items": {
            status: _legacy_item(status, status)
            for status in ("pending", "running", "paused", "cancelled", "completed")
        },
    }
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path.read_bytes()


def test_queue_migration_dry_run_maps_status_and_recomputes_order(tmp_path: Path) -> None:
    source = tmp_path / "queue" / "queue_state.json"
    destination = tmp_path / "jobs" / "job_state.v2.json"
    original = _write_v1(source)

    report = migrate_queue_state_v1(source, destination, process_id="pid")

    assert report["mode"] == "dry-run"
    assert report["status_mappings"] == {
        "cancelled->cancelled": 1,
        "completed->completed": 1,
        "paused->recovery_pending": 1,
        "pending->pending": 1,
        "running->recovery_pending": 1,
    }
    assert report["payload"]["pending_order"] == ["paused", "pending", "running"]
    assert [report["payload"]["items"][item]["order_index"] for item in ("paused", "pending", "running")] == [1, 2, 3]
    assert source.read_bytes() == original
    assert not destination.exists()


def test_queue_migration_apply_preserves_source_and_writes_backup(tmp_path: Path) -> None:
    source = tmp_path / "queue" / "queue_state.json"
    destination = tmp_path / "jobs" / "job_state.v2.json"
    original = _write_v1(source)

    report = migrate_queue_state_v1(source, destination, apply=True, process_id="pid")

    assert source.read_bytes() == original
    assert Path(report["backup"]).read_bytes() == original
    migrated = json.loads(destination.read_text(encoding="utf-8"))
    assert migrated["schema_version"] == 2
    assert migrated["concurrency"] == 4
    assert migrated["items"]["running"]["recovery_reason"] == "v1 migration: original status running"


def test_job_manager_automatically_loads_legacy_state(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "queue" / "queue_state.json"
    _write_v1(source)
    monkeypatch.setattr("translator.core.job_manager.load_config", lambda: {"queue": {"concurrency": 1}})

    manager = JobManager(output_root=tmp_path)

    assert manager.state_file.is_file()
    assert manager.get_status().pending_count == 3
    assert manager.get_status().failed_count == 1
    assert list(source.parent.glob("queue_state.json.v1.*.bak"))
