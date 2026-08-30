import json
from pathlib import Path
from tempfile import TemporaryDirectory

from translator.core.paths import PathResolver
from translator.core.workspace import BookWorkspace
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


def test_event_history_projects_legacy_provider_diagnostics() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        workspace = BookWorkspace.at(root, "legacy-book")
        workspace.initialize(book_id="legacy-book")
        (workspace.data_dir / "provider-diagnostics.json").write_text(json.dumps({
            "attempts": [
                {
                    "provider": "primary",
                    "ids": ["p1"],
                    "recovered_ids": [],
                    "status": "error",
                    "reason": "network",
                    "result": {"status": "error", "reason": "network", "error": "timed out"},
                },
                {
                    "provider": "fallback",
                    "ids": ["p1"],
                    "status": "ok",
                    "reason": "primary_network_fb1",
                    "remaining": [],
                    "result": {"status": "ok"},
                },
            ],
        }), encoding="utf-8")

        events = read_book_events("legacy-book", output_root=root)

        assert [event["event"] for event in events] == [
            "translation_attempt", "fallback_triggered", "translation_attempt",
        ]
        assert events[0]["data"]["reason"] == "network"
        assert events[2]["data"]["is_fallback"] is True
        assert events[2]["data"]["fallback_from"] == "primary"
        assert events[1]["data"]["to_provider"] == "fallback"
