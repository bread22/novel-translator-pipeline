#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from translator.core.workspace import utc_now, write_json


def normalize_term(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    term = dict(raw)
    changes: list[str] = []
    if "note" not in term and "notes" in term:
        term["note"] = term.pop("notes")
        changes.append("notes->note")
    if "first_seen_chunk" not in term and "first_chapter" in term:
        term["first_seen_chunk"] = term.pop("first_chapter")
        changes.append("first_chapter->first_seen_chunk")
    term.setdefault("last_seen_chunk", term.get("first_seen_chunk"))
    term.setdefault("occurrences", 0)
    term.setdefault("sample_ids", [])
    return term, changes


def migrate(path: Path, *, apply: bool = False) -> dict[str, Any]:
    original = path.read_bytes()
    payload = json.loads(original)
    terms = payload.get("terms", []) if isinstance(payload, dict) else []
    migrated: list[Any] = []
    changes: list[dict[str, Any]] = []
    for index, raw in enumerate(terms):
        if not isinstance(raw, dict):
            changes.append({"index": index, "warning": "non-object term preserved"})
            migrated.append(raw)
            continue
        term, term_changes = normalize_term(raw)
        migrated.append(term)
        if term_changes:
            changes.append({"index": index, "source": term.get("source"), "changes": term_changes})
    updated = dict(payload)
    updated["schema_version"] = "2.0"
    updated["terms"] = migrated
    updated["updated_at"] = payload.get("updated_at") or utc_now()
    before_hash = hashlib.sha256(original).hexdigest()
    rendered = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    after_hash = hashlib.sha256(rendered).hexdigest()
    backup = path.with_name(f"{path.name}.v1.bak")
    if apply and before_hash != after_hash:
        shutil.copy2(path, backup)
        write_json(path, updated)
    return {
        "path": str(path),
        "mode": "apply" if apply else "dry-run",
        "terms": len(migrated),
        "changes": changes,
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "backup": str(backup) if apply and before_hash != after_hash else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate glossary JSON files to schema v2 (dry-run by default)")
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--book", help="Workspace directory name; omit to scan every workspace")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    paths = [args.output_root / args.book / "data" / "glossary.json"] if args.book else sorted(args.output_root.glob("*/data/glossary.json"))
    reports = [migrate(path, apply=args.apply) for path in paths if path.is_file()]
    print(json.dumps({"schema_version": "2.0", "reports": reports}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
