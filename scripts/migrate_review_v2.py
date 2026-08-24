#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from translator.core.workspace import write_json
from translator.review.models import normalize_review_for_display


def migrate(path: Path, *, apply: bool = False) -> dict[str, Any]:
    original = path.read_bytes()
    payload = json.loads(original)
    normalized, warning = normalize_review_for_display(payload)
    rendered = (json.dumps(normalized, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    changed = warning is None and original != rendered
    backup = path.with_name(f"{path.name}.v1.bak")
    if apply and changed:
        shutil.copy2(path, backup)
        write_json(path, normalized)
    return {
        "path": str(path),
        "mode": "apply" if apply else "dry-run",
        "migrated": bool(changed),
        "warning": warning,
        "source_preserved": not apply or warning is not None or not changed,
        "before_sha256": hashlib.sha256(original).hexdigest(),
        "after_sha256": hashlib.sha256(rendered).hexdigest(),
        "backup": str(backup) if apply and changed else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate chapter review outputs to schema v2 (dry-run by default)")
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--book")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    pattern = f"{args.book}/reviews/c*-output.json" if args.book else "*/reviews/c*-output.json"
    reports = [migrate(path, apply=args.apply) for path in sorted(args.output_root.glob(pattern))]
    print(json.dumps({"schema_version": "2.0", "reports": reports}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
