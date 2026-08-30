#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.check_version_consistency import check_versions
from scripts.check_frontend_api_contract import check_contract
from scripts.migrate_glossary_v3 import migrate
from scripts.migrate_memory_v2 import migrate as migrate_memory
from scripts.migrate_review_v2 import migrate as migrate_review
from scripts.verify_frontend_dist import verify_dist
from translator.core.config import CONFIG_PATH, load_config, validate_config_data
from translator.core.paths import PathResolver
from translator.version import __version__
from translator.web.app import create_app


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def generate(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = CONFIG_PATH
    config_before = hashlib.sha256(config_path.read_bytes()).hexdigest()
    config = load_config(config_path)
    validate_config_data(config)
    config_after = hashlib.sha256(config_path.read_bytes()).hexdigest()

    resolver = PathResolver.for_config(config_path)
    output_root = resolver.output_root(config)
    glossary_reports = [migrate(path) for path in sorted(output_root.glob("*/data/glossary.json")) if path.is_file()]
    glossary_runtime: dict[str, int] = {
        "reported": 0, "candidates": 0, "rejected": 0, "shape_blocked": 0,
        "category_blocked": 0, "evidence_insufficient": 0, "evidence_total": 0,
        "evidence_valid": 0, "evidence_discarded": 0,
        "known_hits": 0, "known_terms": 0, "activated": 0,
        "candidate": 0, "conflicts": 0, "discard": 0, "revisions": 0,
        "backfill_affected": 0, "backfill_changed": 0, "backfill_failed": 0,
        "injected": 0,
    }
    for report_path in sorted(output_root.glob("*/reports/*.json")):
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
        metrics = report.get("glossary", {}) if isinstance(report, dict) else {}
        if not isinstance(metrics, dict):
            metrics = {}
        knowledge = report.get("knowledge", {}) if isinstance(report, dict) else {}
        if not isinstance(knowledge, dict):
            knowledge = {}
        pre_scan = report.get("pre_scan", {}) if isinstance(report, dict) else {}
        if not isinstance(pre_scan, dict):
            pre_scan = {}
        glossary_runtime["reported"] += int(metrics.get("reported", 0) or 0)
        glossary_runtime["candidates"] += int(metrics.get("accepted_candidates", 0) or 0)
        glossary_runtime["rejected"] += int(metrics.get("rejected", 0) or 0)
        glossary_runtime["shape_blocked"] += int(metrics.get("blocked_by_shape", 0) or 0)
        glossary_runtime["activated"] += int(metrics.get("activated", 0) or 0)
        glossary_runtime["category_blocked"] += int(metrics.get("blocked_by_category", 0) or 0)
        glossary_runtime["evidence_insufficient"] += int(metrics.get("blocked_by_evidence", 0) or 0)
        glossary_runtime["evidence_total"] += int(metrics.get("evidence_total", 0) or 0)
        glossary_runtime["evidence_valid"] += int(metrics.get("evidence_valid", 0) or 0)
        glossary_runtime["evidence_discarded"] += int(metrics.get("evidence_discarded", 0) or 0)
        glossary_runtime["conflicts"] += int(metrics.get("disputed", 0) or 0)
        glossary_runtime["revisions"] += int(metrics.get("revised", 0) or 0)
        glossary_runtime["backfill_affected"] += int(metrics.get("backfill_affected", 0) or 0)
        glossary_runtime["backfill_changed"] += int(metrics.get("backfill_changed", 0) or 0)
        glossary_runtime["backfill_failed"] += int(metrics.get("backfill_failed", 0) or 0)
        glossary_runtime["injected"] += int(metrics.get("injected_into_translation", 0) or 0)
        glossary_runtime["known_hits"] += int(pre_scan.get("known_hit_count", 0) or 0)
        glossary_runtime["known_terms"] += int(pre_scan.get("known_term_count", 0) or 0)
        glossary_runtime["candidates"] += int(knowledge.get("candidates", 0) or 0)
        glossary_runtime["activated"] += int(knowledge.get("active", 0) or 0)
        glossary_runtime["candidate"] += int(knowledge.get("candidate", 0) or 0)
        glossary_runtime["conflicts"] += int(knowledge.get("conflict", knowledge.get("conflicts", 0)) or 0)
        glossary_runtime["discard"] += int(knowledge.get("discard", 0) or 0)
    memory_reports = [migrate_memory(path) for path in sorted(output_root.glob("*/data/book_memory.json")) if path.is_file()]
    review_reports = [migrate_review(path) for path in sorted(output_root.glob("*/reviews/c*-output.json")) if path.is_file()]
    queue_path = output_root / "jobs" / "job_state.v2.json"
    queue_payload = json.loads(queue_path.read_text(encoding="utf-8")) if queue_path.is_file() else None
    queue_report = {
        "path": str(queue_path),
        "mode": "dry-run",
        "present": queue_payload is not None,
        "schema_version": queue_payload.get("schema_version") if isinstance(queue_payload, dict) else None,
        "compatible": queue_payload is None or (isinstance(queue_payload, dict) and queue_payload.get("schema_version") == 2),
    }

    app = create_app(static_dir=ROOT / "frontend" / "dist")
    reports = {
        "version.json": check_versions(ROOT),
        "frontend-dist.json": verify_dist(ROOT / "frontend" / "dist"),
        "config-dry-run.json": {
            "status": "ok" if config_before == config_after else "error",
            "path": str(config_path),
            "before_sha256": config_before,
            "after_sha256": config_after,
            "mutated": config_before != config_after,
        },
        "glossary-migration-dry-run.json": {
            "schema_version": "3.0", "mode": "dry-run", "reports": glossary_reports,
            "runtime_observability": glossary_runtime,
        },
        "memory-migration-dry-run.json": {"schema_version": "2.0", "mode": "dry-run", "reports": memory_reports},
        "review-migration-dry-run.json": {"schema_version": "2.0", "mode": "dry-run", "reports": review_reports},
        "queue-state-migration-dry-run.json": queue_report,
        "frontend-api-contract.json": {
            "status": "ok" if not (contract_failures := check_contract()) else "error",
            "interfaces": 14,
            "failures": contract_failures,
        },
    }
    for filename, report in reports.items():
        _write(output_dir / filename, report)
    _write(output_dir / "openapi.json", app.openapi())

    evidence_files = sorted(path for path in output_dir.glob("*.json") if path.name != "manifest.json")
    manifest = {
        "version": __version__,
        "status": "ok" if all(isinstance(report, dict) and report.get("status", "ok") == "ok" for report in reports.values()) and queue_report["compatible"] else "error",
        "files": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in evidence_files
        },
    }
    _write(output_dir / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate non-mutating release evidence")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "release" / __version__)
    args = parser.parse_args()
    report = generate(args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
