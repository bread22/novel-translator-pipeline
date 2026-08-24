#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


GROUPS = {
    "critical config/path/state/merge": (
        90.0,
        (
            "translator/core/config.py",
            "translator/core/paths.py",
            "translator/core/job_control.py",
            "translator/core/state_migrations.py",
            "translator/core/workspace.py",
            "translator/review/models.py",
        ),
    ),
    "provider adapters and API clients": (85.0, ("translator/providers/",)),
    "remaining core Python": (
        80.0,
        ("translator/core/", "translator/pipeline/", "translator/review/", "translator/web/"),
    ),
}


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(path == pattern or (pattern.endswith("/") and path.startswith(pattern)) for pattern in patterns)


def check_coverage(payload: dict[str, Any]) -> tuple[list[str], list[str]]:
    files = payload.get("files", {})
    failures: list[str] = []
    reports: list[str] = []
    for name, (threshold, patterns) in GROUPS.items():
        summaries = [entry["summary"] for path, entry in files.items() if _matches(path, patterns)]
        statements = sum(int(summary["num_statements"]) for summary in summaries)
        covered = sum(int(summary["covered_lines"]) for summary in summaries)
        percent = covered / statements * 100 if statements else 0.0
        report = f"{name}: {percent:.2f}% ({covered}/{statements}), required {threshold:.0f}%"
        reports.append(report)
        if percent + 1e-9 < threshold:
            failures.append(report)
    return reports, failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce remediation-plan coverage groups")
    parser.add_argument("coverage_json", nargs="?", type=Path, default=Path("coverage.json"))
    args = parser.parse_args()
    reports, failures = check_coverage(json.loads(args.coverage_json.read_text(encoding="utf-8")))
    print("\n".join(reports))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
