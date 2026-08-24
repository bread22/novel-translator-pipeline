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
from scripts.migrate_glossary_v2 import migrate
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
        "glossary-migration-dry-run.json": {"schema_version": "2.0", "mode": "dry-run", "reports": glossary_reports},
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
        "status": "ok" if all(report.get("status", "ok") == "ok" for report in reports.values()) and queue_report["compatible"] else "error",
        "files": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in evidence_files
        },
    }
    _write(output_dir / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate non-mutating v0.3 release evidence")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "release" / __version__)
    args = parser.parse_args()
    report = generate(args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
