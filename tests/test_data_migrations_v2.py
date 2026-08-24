from __future__ import annotations

import json
from pathlib import Path

from scripts.migrate_glossary_v2 import migrate as migrate_glossary
from scripts.migrate_memory_v2 import migrate as migrate_memory
from scripts.migrate_review_v2 import migrate as migrate_review
from translator.core.workspace import merge_memory_delta, read_json, write_json
from translator.review.models import normalize_review_for_display


def test_glossary_report_counts_unknown_fields_and_writes_formal_fields(tmp_path: Path) -> None:
    path = tmp_path / "glossary.json"
    write_json(path, {"book": "b", "mystery": 1, "terms": [{"source": "x", "target": "y", "notes": "n", "vendor": 2}]})

    report = migrate_glossary(path, apply=True)

    assert report["modified"] == 1
    assert report["unknown_fields"] == 2
    assert set(read_json(path)["terms"][0]) <= {
        "source", "target", "category", "confidence", "note", "first_seen_chunk",
        "last_seen_chunk", "occurrences", "sample_ids",
    }


def test_memory_migration_converts_aliases_reports_conflict_and_backs_up(tmp_path: Path) -> None:
    path = tmp_path / "book_memory.json"
    write_json(path, {
        "book": "b",
        "version": 1,
        "unknown": True,
        "entries": [{"key": "Alice", "value": "existing"}],
        "characters": [{"name": "Alice", "summary": "different"}, {"name": "Bob", "summary": "hero"}],
        "world_settings": [{"term": "City", "explanation": "large"}],
    })

    dry_run = migrate_memory(path)
    original = path.read_bytes()
    applied = migrate_memory(path, apply=True)

    assert dry_run["added"] == 2
    assert dry_run["conflicts"] == 1
    assert dry_run["unknown_fields"] == 1
    assert Path(applied["backup"]).read_bytes() == original
    migrated = read_json(path)
    assert migrated["schema_version"] == "2.0"
    assert {entry["key"] for entry in migrated["entries"]} == {"Alice", "Bob", "City"}
    assert "characters" not in migrated and "world_settings" not in migrated and "unknown" not in migrated


def test_runtime_memory_write_normalizes_legacy_fields() -> None:
    memory, _ = merge_memory_delta(
        {"book": "b", "characters": [{"name": "Alice", "summary": "hero"}]},
        {"add": [], "update": [], "conflicts": []},
    )
    assert memory["schema_version"] == "2.0"
    assert memory["entries"][0]["key"] == "Alice"
    assert "characters" not in memory


def test_review_migration_validates_before_write_and_preserves_invalid(tmp_path: Path) -> None:
    valid = tmp_path / "c1-output.json"
    invalid = tmp_path / "c2-output.json"
    write_json(valid, {"checked_ids": ["p1", "p1"], "chapter_state": {"significant_changes": ["x"]}})
    write_json(invalid, {"checked_ids": [], "unexpected": True})
    invalid_original = invalid.read_bytes()

    valid_report = migrate_review(valid, apply=True)
    invalid_report = migrate_review(invalid, apply=True)

    assert valid_report["warning"] is None
    assert read_json(valid)["schema_version"] == "2.0"
    assert read_json(valid)["checked_ids"] == ["p1"]
    assert Path(valid_report["backup"]).is_file()
    assert invalid_report["warning"]
    assert invalid.read_bytes() == invalid_original
    displayed, warning = normalize_review_for_display(json.loads(invalid_original))
    assert displayed["unexpected"] is True and warning
