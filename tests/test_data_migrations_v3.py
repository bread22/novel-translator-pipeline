from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.migrate_glossary_v3 import migrate
from translator.core.workspace import read_json, write_json


def test_v2_migration_is_dry_run_then_atomic_with_backup(tmp_path: Path) -> None:
    path = tmp_path / "glossary.json"
    write_json(path, {"book": "book", "terms": [
        {"source": "手", "target": "手", "category": "body_part", "confidence": 1.0},
        {"source": "雨宮慶", "target": "雨宫庆", "category": "character", "confidence": 0.95},
    ], "conflicts": []})
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    dry = migrate(path)
    assert dry["mode"] == "dry-run"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before

    applied = migrate(path, apply=True)
    assert applied["reopen_validated"] is True
    assert Path(applied["backup"]).is_file()
    result = read_json(path)
    assert result["schema_version"] == "3.0"
    assert len(result["terms"]) == 2
    blocked = next(item for item in result["terms"] if item["source"] == "手")
    assert blocked["status"] == "retired"
    assert blocked["retired_reason"] == "legacy_blocked_category"
    assert all(item["status"] != "active" for item in result["terms"])
