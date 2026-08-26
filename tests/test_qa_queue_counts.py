from pathlib import Path

from translator.core.job_manager import JobManager


def test_cancelled_items_do_not_increment_failed_count(tmp_path: Path) -> None:
    manager = JobManager(tmp_path)
    manager.is_paused = True
    item = manager.enqueue("cancelled-fixture")
    manager.cancel_item(item.id)

    status = manager.get_status()

    assert status.failed_count == 0
