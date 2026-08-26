from pathlib import Path
from tempfile import TemporaryDirectory

from translator.web.events import append_book_event, read_book_events


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
