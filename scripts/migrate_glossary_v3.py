#!/usr/bin/env python3
"""Transactional v2/legacy glossary migration (dry-run unless --apply is supplied)."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any
import unicodedata

from translator.core.workspace import utc_now
from translator.glossary.lifecycle import stable_term_id
from translator.glossary.service import persist_glossary
from translator.glossary.taxonomy import BODY_SOURCE_SCOPE, BLOCKED, DIRECT_ALLOWED, GATED_ALLOWED, canonical_category, category_tier
from translator.glossary.validation import TARGET_FORBIDDEN_RE, KANA_RE, validate_glossary_document


STATUSES = {"candidate", "active", "disputed", "revised", "retired"}
KNOWN_TERM_FIELDS = {
    "term_id", "source", "source_normalized", "target", "category", "status", "confidence",
    "canonical_term_id", "note", "first_seen_chunk", "last_seen_chunk", "occurrences", "chapter_count",
    "sample_ids", "evidence", "provenance", "created_at", "updated_at", "retired_reason", "legacy",
    "source_scope",
}


def _evidence(raw: dict[str, Any]) -> list[dict[str, Any]]:
    values = raw.get("evidence", [])
    if isinstance(values, list):
        return [dict(item) for item in values if isinstance(item, dict) and item.get("paragraph_id")]
    return []


def migrate_term(raw: dict[str, Any], index: int) -> tuple[dict[str, Any], dict[str, Any]]:
    source = str(raw.get("source", "")).strip()
    normalized = unicodedata.normalize("NFKC", source)
    target = str(raw.get("target", "")).strip()
    raw_category = str(raw.get("category", "")).strip()
    category = canonical_category(raw_category)
    tier = category_tier(category)
    evidence = _evidence(raw)
    sample_ids = list(dict.fromkeys(str(item) for item in raw.get("sample_ids", []) if str(item).strip()))
    if not evidence and sample_ids:
        evidence = [{
            "chapter_id": str(raw.get("first_seen_chunk") or raw.get("first_chapter") or ""),
            "paragraph_id": paragraph_id,
            "reporter": "legacy_migration",
            "confidence": float(raw.get("confidence", 0) or 0),
        } for paragraph_id in sample_ids]
    status = "candidate"
    retired_reason: str | None = None
    source_scope = str(raw.get("source_scope", BODY_SOURCE_SCOPE)).strip().casefold() or BODY_SOURCE_SCOPE
    if tier is None:
        category = "unresolved"
        status = "retired"
        retired_reason = "unknown_legacy_category"
    elif category in BLOCKED:
        status = "retired"
        retired_reason = "legacy_blocked_category"
    elif not source or not target or TARGET_FORBIDDEN_RE.search(target) or KANA_RE.search(target):
        status = "retired"
        retired_reason = "legacy_invalid_shape"
    elif str(raw.get("status")) in STATUSES and evidence:
        status = str(raw.get("status"))
    if source_scope != BODY_SOURCE_SCOPE and status == "active":
        status = "candidate"
    # A v2 item has no verifiable source/evidence link, so it stays a candidate.
    now = utc_now()
    term: dict[str, Any] = {
        "term_id": str(raw.get("term_id") or stable_term_id(normalized)),
        "source": source,
        "source_normalized": normalized,
        "target": target,
        "category": category,
        "source_scope": source_scope,
        "status": status,
        "confidence": max(0.0, min(1.0, float(raw.get("confidence", 0) or 0))),
        "canonical_term_id": raw.get("canonical_term_id"),
        "note": str(raw.get("note") or raw.get("notes") or "").strip()[:120],
        "first_seen_chunk": str(raw.get("first_seen_chunk") or raw.get("first_chapter") or ""),
        "last_seen_chunk": str(raw.get("last_seen_chunk") or raw.get("first_seen_chunk") or raw.get("first_chapter") or ""),
        "occurrences": len({(str(item.get("chapter_id", "")), str(item.get("paragraph_id", "")), str(item.get("reporter", ""))) for item in evidence}),
        "chapter_count": len({str(item.get("chapter_id", "")) for item in evidence if item.get("chapter_id")}),
        "sample_ids": sample_ids or list(dict.fromkeys(str(item.get("paragraph_id")) for item in evidence)),
        "evidence": evidence,
        "provenance": list(dict.fromkeys(str(item) for item in raw.get("provenance", []) if str(item))) or (["legacy_migration"] if raw else []),
        "created_at": str(raw.get("created_at") or now),
        "updated_at": now,
        "retired_reason": retired_reason or raw.get("retired_reason"),
    }
    unknown = {key: value for key, value in raw.items() if key not in KNOWN_TERM_FIELDS and key not in {"notes", "first_chapter"}}
    if unknown:
        term["legacy"] = unknown
    change = {
        "index": index,
        "source": source,
        "old_category": raw_category,
        "category": category,
        "status": status,
        "retired_reason": retired_reason,
    }
    return term, change


def migrate(path: Path, *, apply: bool = False) -> dict[str, Any]:
    original = path.read_bytes()
    payload = json.loads(original)
    raw_terms = payload.get("terms", []) if isinstance(payload, dict) else []
    terms: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    unknown_categories = 0
    for index, raw in enumerate(raw_terms if isinstance(raw_terms, list) else []):
        if not isinstance(raw, dict):
            terms.append({
                "term_id": stable_term_id(f"legacy-{index}"), "source": "", "source_normalized": "",
                "target": "", "category": "unresolved", "status": "retired", "confidence": 0.0,
                "note": "", "first_seen_chunk": "", "last_seen_chunk": "", "occurrences": 0,
                "chapter_count": 0, "sample_ids": [], "evidence": [], "provenance": ["legacy_migration"],
                "created_at": utc_now(), "updated_at": utc_now(), "retired_reason": "legacy_non_object",
            })
            changes.append({"index": index, "status": "retired", "retired_reason": "legacy_non_object"})
            continue
        term, change = migrate_term(raw, index)
        terms.append(term)
        changes.append(change)
        if change["old_category"] and change["old_category"] != change["category"] and category_tier(change["old_category"]) is None:
            unknown_categories += 1
    updated = {
        "schema_version": "3.0",
        "book": str(payload.get("book", "")),
        "terms": sorted(terms, key=lambda item: str(item.get("source_normalized", ""))),
        "conflicts": list(payload.get("conflicts", [])) if isinstance(payload.get("conflicts"), list) else [],
        "revisions": list(payload.get("revisions", [])) if isinstance(payload.get("revisions"), list) else [],
        "updated_at": str(payload.get("updated_at") or utc_now()),
    }
    before_hash = hashlib.sha256(original).hexdigest()
    rendered = (json.dumps(updated, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    after_hash = hashlib.sha256(rendered).hexdigest()
    backup = path.with_name(path.name + ".v2.bak")
    report: dict[str, Any] = {
        "path": str(path), "mode": "apply" if apply else "dry-run", "terms": len(terms),
        "active": sum(1 for item in terms if item["status"] == "active"),
        "candidate": sum(1 for item in terms if item["status"] == "candidate"),
        "retired": sum(1 for item in terms if item["status"] == "retired"),
        "disputed": sum(1 for item in terms if item["status"] == "disputed"),
        "unknown_categories": unknown_categories,
        "conflicts": len(updated["conflicts"]),
        "before_sha256": before_hash, "after_sha256": after_hash,
        "backup": str(backup) if apply and before_hash != after_hash else None,
        "changes": changes,
    }
    if apply and before_hash != after_hash:
        shutil.copy2(path, backup)
        persist_glossary(type("MigrationWorkspace", (), {
            "glossary_path": path,
            "novel_translator_terms_path": path.parent / "novel-translator-terms.json",
        })(), updated)
        reopened = json.loads(path.read_text(encoding="utf-8"))
        errors = validate_glossary_document(reopened)
        if errors:
            shutil.copy2(backup, path)
            raise ValueError("v3 migration reopen validation failed: " + ", ".join(errors))
        report["reopen_validated"] = True
    else:
        report["reopen_validated"] = False
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate glossary data to v3 (dry-run by default)")
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--book", help="workspace directory name; omit to scan every workspace")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    paths = [args.output_root / args.book / "data" / "glossary.json"] if args.book else sorted(args.output_root.glob("*/data/glossary.json"))
    reports = [migrate(path, apply=args.apply) for path in paths if path.is_file()]
    print(json.dumps({"schema_version": "3.0", "mode": "apply" if args.apply else "dry-run", "reports": reports}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
