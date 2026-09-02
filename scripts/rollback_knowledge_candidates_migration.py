#!/usr/bin/env python3
"""Restore a backup created by migrate_knowledge_candidates.py."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore a knowledge candidate queue backup")
    parser.add_argument("backup", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    if not args.backup.is_file():
        raise SystemExit(f"backup not found: {args.backup}")
    args.target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.backup, args.target)
    print(f"restored={args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
