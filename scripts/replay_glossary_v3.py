#!/usr/bin/env python3
"""Validate and optionally migrate persisted Glossary data to v3.

Review artifacts are no longer replayed into authoritative knowledge. New
knowledge decisions are committed by the chapter pipeline through its single
knowledge persistence boundary. This utility remains for existing workspace
data that still needs a glossary-v3 migration.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.migrate_glossary_v3 import STATUSES, migrate_term
from scripts.migrate_knowledge_candidates import migrate as migrate_candidates
from translator.glossary.service import persist_glossary
from translator.glossary.taxonomy import category_tier
from translator.glossary.validation import validate_glossary_document
from translator.glossary.name_normalizer import normalization_metadata


def _as_v3(payload: dict[str, Any]) -> dict[str, Any]:
    raw_terms = payload.get("terms", [])
    already_v3 = payload.get("schema_version") == "3.0" and isinstance(raw_terms, list) and all(
        isinstance(raw, dict)
        and str(raw.get("status", "")) in STATUSES
        and category_tier(raw.get("category")) is not None
        for raw in raw_terms
    )
    if already_v3:
        return dict(payload)
    terms: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_terms if isinstance(raw_terms, list) else []):
        if isinstance(raw, dict):
            term, _ = migrate_term(raw, index)
            terms.append(term)
    return {
        "schema_version": "3.0",
        "book": str(payload.get("book", "")),
        "terms": terms,
        "conflicts": list(payload.get("conflicts", [])) if isinstance(payload.get("conflicts"), list) else [],
        "revisions": list(payload.get("revisions", [])) if isinstance(payload.get("revisions"), list) else [],
        "updated_at": str(payload.get("updated_at", "")),
    }


def replay_workspace(path: Path, *, apply: bool = False) -> dict[str, Any]:
    """Report or apply only the glossary-v2-to-v3 migration for one workspace."""
    glossary_path = path / "data" / "glossary.json"
    candidate_path = path / "data" / "knowledge-candidates.json"
    candidate_report = migrate_candidates(candidate_path, apply=apply) if candidate_path.is_file() else {
        "status": "skipped", "reason": "missing_candidate_queue",
    }
    if not glossary_path.is_file():
        return {
            "workspace": str(path),
            "status": "skipped" if candidate_report.get("status") == "skipped" else "ok",
            "reason": "missing_glossary",
            "candidate_queue": candidate_report,
        }

    original = glossary_path.read_bytes()
    glossary = json.loads(original)
    if not isinstance(glossary, dict):
        return {"workspace": str(path), "status": "skipped", "reason": "glossary_not_object"}

    current = _as_v3(glossary)
    rendered = json.dumps(current, ensure_ascii=False, indent=2) + "\n"
    before_hash = hashlib.sha256(original).hexdigest()
    after_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    report: dict[str, Any] = {
        "workspace": str(path),
        "mode": "apply" if apply else "dry-run",
        "status": "ok",
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "changed": before_hash != after_hash,
        "terms_before": len(glossary.get("terms", [])),
        "terms_after": len(current.get("terms", [])),
        "active_after": sum(
            1 for item in current.get("terms", [])
            if isinstance(item, dict) and item.get("status") == "active"
        ),
        "review_replay": "removed",
        "candidate_queue": candidate_report,
        "name_normalization": {
            **normalization_metadata(),
            "terms_with_diagnostics": sum(
                1 for item in current.get("terms", [])
                if isinstance(item, dict) and item.get("normalization_diagnostics")
            ),
            "terms_with_warnings": sum(
                1 for item in current.get("terms", [])
                if isinstance(item, dict) and item.get("normalization_warning")
            ),
            "preferred_selections": sum(
                1 for item in current.get("terms", [])
                if isinstance(item, dict) and item.get("selected_candidate")
            ),
        },
    }
    if apply:
        backup: Path | None = None
        if report["changed"]:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = glossary_path.with_name(f"{glossary_path.name}.replay-{timestamp}.bak")
            backup.write_bytes(original)
        persist_glossary(type("ReplayWorkspace", (), {
            "glossary_path": glossary_path,
            "novel_translator_terms_path": path / "data" / "novel-translator-terms.json",
        })(), current)
        reopened = json.loads(glossary_path.read_text(encoding="utf-8"))
        errors = validate_glossary_document(reopened)
        if errors:
            glossary_path.write_bytes(original)
            raise ValueError("glossary reopen validation failed: " + ", ".join(errors))
        report["backup"] = str(backup) if backup else None
        report["reopen_validated"] = True
    else:
        report["backup"] = None
        report["reopen_validated"] = False
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or migrate Glossary v3 data")
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--book", help="workspace directory name; omit to scan every workspace")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    paths = [args.output_root / args.book] if args.book else sorted(
        path for path in args.output_root.iterdir() if path.is_dir()
    )
    reports = [replay_workspace(path, apply=args.apply) for path in paths if path.is_dir()]
    print(json.dumps({
        "schema_version": "3.0",
        "mode": "apply" if args.apply else "dry-run",
        "reports": reports,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
