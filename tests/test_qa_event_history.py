from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from translator.web.events import append_book_event, read_book_events


@pytest.mark.xfail(
    strict=True,
    reason="read_book_events uses events[-limit:], so limit=0 returns the complete history",
)
def test_event_history_zero_limit_returns_no_events() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for index in range(3):
            append_book_event(
                "event-fixture",
                {"event_id": str(index), "event": "fixture"},
                output_root=root,
            )

        assert read_book_events("event-fixture", limit=0, output_root=root) == []
