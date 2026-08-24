#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from translator.core.state_migrations import migrate_queue_state_v1


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate queue_state.json from v1 to v2 (dry-run by default)")
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--process-id", default="migration")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    source = args.source or args.output_root / "queue" / "queue_state.json"
    destination = args.destination or args.output_root / "jobs" / "job_state.v2.json"
    report = migrate_queue_state_v1(
        source,
        destination,
        apply=args.apply,
        process_id=args.process_id,
    )
    report.pop("payload", None)
    print(json.dumps({"schema_version": "2.0", "report": report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
