#!/usr/bin/env python3
"""Restore files from backups made before the OpenCC name-validation rollout.

Usage::

    python scripts/rollback_opencc_name_validation.py \
        --backup-dir /path/to/backups --root /path/to/repository

The backup directory mirrors repository-relative paths.  The operation is
explicit and limited to the OpenCC rollout files; it never rewrites glossary
data or the active translation projection.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil


ROLLBACK_FILES = (
    "pyproject.toml",
    "constraints/py310-314.txt",
    "translator/glossary/name_normalizer.py",
    "translator/glossary/name_validation.py",
    "translator/glossary/validation.py",
    "translator/glossary/lifecycle.py",
    "translator/glossary/__init__.py",
    "scripts/replay_glossary_v3.py",
    "THIRD_PARTY_NOTICES.md",
    "tests/test_glossary_name_validation.py",
    "tests/fixtures/opencc_name_normalization.json",
    "tests/test_glossary_opencc_name_normalizer.py",
)
NEW_FILES = frozenset({
    "translator/glossary/name_normalizer.py",
    "tests/fixtures/opencc_name_normalization.json",
    "tests/test_glossary_opencc_name_normalizer.py",
})


def rollback(backup_dir: Path, root: Path) -> list[str]:
    restored: list[str] = []
    for relative in ROLLBACK_FILES:
        backup = backup_dir / relative
        if not backup.is_file():
            if relative in NEW_FILES:
                destination = root / relative
                destination.unlink(missing_ok=True)
                restored.append(relative + ":removed")
            continue
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, destination)
        restored.append(relative)
    return restored


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore the pre-OpenCC name-validation source files")
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if not args.backup_dir.is_dir():
        raise SystemExit(f"backup directory not found: {args.backup_dir}")
    restored = rollback(args.backup_dir, args.root.resolve())
    if not restored:
        raise SystemExit("no OpenCC rollout backups found")
    print("restored=" + ",".join(restored))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
