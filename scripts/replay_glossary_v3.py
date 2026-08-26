#!/usr/bin/env python3
"""Replay persisted review/extraction deltas through the v3 glossary lifecycle.

Dry-run is the default.  ``--apply`` creates a timestamped glossary backup,
reopens the written file, validates it, and rebuilds the disposable projection.
Translation text is never rewritten by this utility.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translator.glossary.service import persist_glossary
from translator.glossary.taxonomy import category_tier
from translator.glossary.validation import validate_glossary_document
from translator.core.workspace import read_json
from scripts.migrate_glossary_v3 import STATUSES, migrate_term


CHAPTER_OUTPUT_RE = re.compile(r"^(c[^-]+)-output\.json$")
EXTRACT_OUTPUT_RE = re.compile(r"^(c[^-]+)-glossary-extract-output\.json$")


def _evidence_texts(path: Path) -> dict[str, str]:
    payload = read_json(path, {})
    if not isinstance(payload, dict):
        return {}
    items = payload.get("items", [])
    if not isinstance(items, list):
        return {}
    return {
        str(item["id"]): str(item.get("source", ""))
        for item in items
        if isinstance(item, dict) and item.get("id")
    }


def _prepare_updates(payload: dict[str, Any], evidence_texts: dict[str, str]) -> list[dict[str, Any]]:
    glossary_delta = payload.get("glossary_delta", {})
    if not isinstance(glossary_delta, dict):
        return []
    updates: list[dict[str, Any]] = []
    for section in ("add", "update"):
        values = glossary_delta.get(section, [])
        for raw in values if isinstance(values, list) else []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            source = str(item.get("source", "")).strip()
            if not item.get("evidence_ids"):
                item["evidence_ids"] = [item_id for item_id, text in evidence_texts.items() if source and source in text]
            updates.append(item)
    return updates


def _prepare_extraction(payload: dict[str, Any], evidence_texts: dict[str, str]) -> list[dict[str, Any]]:
    values = payload.get("candidates", [])
    if not isinstance(values, list):
        return []
    updates: list[dict[str, Any]] = []
    for raw in values:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        source = str(item.get("source", "")).strip()
        if not item.get("evidence_ids"):
            item["evidence_ids"] = [item_id for item_id, text in evidence_texts.items() if source and source in text]
        updates.append(item)
    return updates


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
    glossary_path = path / "data" / "glossary.json"
    reviews_dir = path / "reviews"
    if not glossary_path.is_file():
        return {"workspace": str(path), "status": "skipped", "reason": "missing_glossary"}
    original = glossary_path.read_bytes()
    glossary = json.loads(original)
    if not isinstance(glossary, dict):
        return {"workspace": str(path), "status": "skipped", "reason": "glossary_not_object"}

    current = _as_v3(glossary)
    chapter_summaries: list[dict[str, Any]] = []
    output_files = sorted(reviews_dir.glob("*.json"))
    stages: list[tuple[str, Path, Path, str, str]] = []
    for output_path in output_files:
        match = EXTRACT_OUTPUT_RE.match(output_path.name)
        if match:
            chapter_id = match.group(1)
            stages.append((chapter_id, output_path, reviews_dir / f"{chapter_id}-glossary-extract-input.json", "preextractor", "extract"))
            continue
        match = CHAPTER_OUTPUT_RE.match(output_path.name)
        if match:
            chapter_id = match.group(1)
            stages.append((chapter_id, output_path, reviews_dir / f"{chapter_id}-input.json", "chapter_reviewer", "review"))
    stages.sort(key=lambda item: (item[0], 0 if item[4] == "extract" else 1))

    for chapter_id, output_path, input_path, reporter, stage in stages:
        payload = read_json(output_path, {})
        if not isinstance(payload, dict):
            continue
        evidence_texts = _evidence_texts(input_path) if input_path.is_file() else {}
        updates = _prepare_extraction(payload, evidence_texts) if stage == "extract" else _prepare_updates(payload, evidence_texts)
        if not updates:
            continue
        from translator.glossary.lifecycle import merge_term_candidates

        current, summary = merge_term_candidates(
            current,
            updates,
            chapter_id=chapter_id,
            reporter=reporter,
            evidence_texts=evidence_texts,
        )
        chapter_summaries.append({"chapter_id": chapter_id, "stage": stage, **summary})

    rendered = json.dumps(current, ensure_ascii=False, indent=2) + "\n"
    after_hash = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    report: dict[str, Any] = {
        "workspace": str(path),
        "mode": "apply" if apply else "dry-run",
        "status": "ok",
        "before_sha256": hashlib.sha256(original).hexdigest(),
        "after_sha256": after_hash,
        "changed": hashlib.sha256(original).hexdigest() != after_hash,
        "chapters": chapter_summaries,
        "terms_before": len(glossary.get("terms", [])),
        "terms_after": len(current.get("terms", [])),
        "active_after": sum(1 for item in current.get("terms", []) if isinstance(item, dict) and item.get("status") == "active"),
    }
    if apply and report["changed"]:
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
            raise ValueError("replay reopen validation failed: " + ", ".join(errors))
        report["backup"] = str(backup)
        report["reopen_validated"] = True
    else:
        report["backup"] = None
        report["reopen_validated"] = False
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay glossary v3 deltas through the lifecycle")
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--book", help="workspace directory name; omit to scan every workspace")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    paths = [args.output_root / args.book] if args.book else sorted(path for path in args.output_root.iterdir() if path.is_dir())
    reports = [replay_workspace(path, apply=args.apply) for path in paths if path.is_dir()]
    print(json.dumps({"schema_version": "3.0", "mode": "apply" if args.apply else "dry-run", "reports": reports}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
