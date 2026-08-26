from pathlib import Path
from tempfile import TemporaryDirectory

from translator.core.paths import PathResolver
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


def test_event_history_uses_configured_output_root_when_server_cwd_differs(monkeypatch) -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory) / "configured-output"
        monkeypatch.setattr(
            "translator.core.config.load_config",
            lambda: {"paths": {"output_root": "configured-output"}},
        )
        monkeypatch.setattr(
            PathResolver,
            "for_config",
            classmethod(lambda cls: PathResolver(Path(directory))),
        )

        append_book_event("event-fixture", {"event_id": "configured", "event": "fixture"})

        assert read_book_events("event-fixture", output_root=root) == [
            {"event_id": "configured", "event": "fixture"},
        ]
