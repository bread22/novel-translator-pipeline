#!/usr/bin/env python3
"""Normalize the pending knowledge queue without inventing evidence locations.

The migration is dry-run by default.  Candidate records that already carry a
chapter/window provenance are upgraded in place; records whose evidence IDs
still have no reliable chapter remain pending with an explicit migration
reason.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from translator.core.workspace import utc_now, write_json
from translator.review.knowledge_extractor import (
    _candidate_provenance,
    _candidate_store_key,
    _merge_candidate_records,
)


NORMALIZATION_VERSION = "candidate-lifecycle-v1"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def migrate(path: Path, *, apply: bool = False) -> dict[str, Any]:
    original = path.read_bytes()
    payload = json.loads(original)
    if not isinstance(payload, dict):
        return {"path": str(path), "mode": "apply" if apply else "dry-run", "status": "skipped", "reason": "queue_not_object"}

    raw_items = payload.get("items", [])
    if not isinstance(raw_items, list):
        raw_items = []
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str, str]] = []
    missing_provenance = 0
    normalized = 0
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        evidence_ids = [
            str(value).strip() for value in item.get("evidence_ids", []) if str(value).strip()
        ]
        provenance, unresolved = _candidate_provenance(item, evidence_ids)
        item["evidence_ids"] = evidence_ids
        item["evidence_provenance"] = provenance
        item["evidence"] = list(provenance)
        item["unresolved_evidence_ids"] = unresolved
        item["status"] = "pending"
        item["queue_reason"] = str(
            item.get("queue_reason")
            or ("evidence_provenance_missing" if unresolved else item.get("final_reason", "pending_review"))
        )
        item["attempt_count"] = _safe_int(item.get("attempt_count"))
        item["first_seen_chapter"] = str(
            item.get("first_seen_chapter") or item.get("chapter_id") or ""
        )
        item["last_reviewed_chapter"] = str(
            item.get("last_reviewed_chapter") or item.get("chapter_id") or ""
        )
        item["normalization_version"] = NORMALIZATION_VERSION
        if unresolved:
            missing_provenance += 1
        normalized += 1
        key = _candidate_store_key(item)
        if key not in grouped:
            grouped[key] = item
            order.append(key)
        else:
            grouped[key] = _merge_candidate_records(grouped[key], item)

    updated = {
        **payload,
        "schema_version": "1.0",
        "items": [grouped[key] for key in order],
        "updated_at": utc_now(),
    }
    rendered = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    before_hash = hashlib.sha256(original).hexdigest()
    after_hash = hashlib.sha256(rendered).hexdigest()
    report: dict[str, Any] = {
        "path": str(path),
        "mode": "apply" if apply else "dry-run",
        "status": "ok",
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "changed": before_hash != after_hash,
        "items_before": len(raw_items),
        "items_after": len(updated["items"]),
        "normalized_items": normalized,
        "missing_provenance": missing_provenance,
        "backup": None,
        "reopen_validated": False,
    }
    if apply and report["changed"]:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.candidate-lifecycle-{timestamp}.bak")
        shutil.copy2(path, backup)
        write_json(path, updated)
        reopened = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(reopened, dict) or not isinstance(reopened.get("items"), list):
            shutil.copy2(backup, path)
            raise ValueError("pending queue reopen validation failed")
        report["backup"] = str(backup)
        report["reopen_validated"] = True
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate knowledge-candidates.json provenance")
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--book", help="workspace directory name; omit to scan every workspace")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    paths = [args.output_root / args.book / "data" / "knowledge-candidates.json"] if args.book else sorted(
        args.output_root.glob("*/data/knowledge-candidates.json")
    )
    reports = [migrate(path, apply=args.apply) for path in paths if path.is_file()]
    print(json.dumps({"schema_version": "1.0", "mode": "apply" if args.apply else "dry-run", "reports": reports}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
