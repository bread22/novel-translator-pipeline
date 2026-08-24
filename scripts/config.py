#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from translator.core.config import (
    CONFIG_PATH,
    CONFIG_SCHEMA_PATH,
    ROOT,
    config_sha256,
    config_value,
    list_config_backups,
    load_config,
    restore_config_backup,
    setting,
)

__all__ = [
    "CONFIG_PATH",
    "CONFIG_SCHEMA_PATH",
    "ROOT",
    "config_value",
    "load_config",
    "setting",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate or restore the pipeline configuration")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="Validate without changing the file")
    subparsers.add_parser("list-backups", help="List timestamped backups")
    restore = subparsers.add_parser("restore", help="Atomically restore a validated backup")
    restore.add_argument("--backup", type=Path, help="Backup path; defaults to the newest sibling backup")
    args = parser.parse_args()

    if args.command == "validate":
        config = load_config(args.config)
        print(json.dumps({
            "status": "ok",
            "path": str(args.config.resolve()),
            "sha256": config_sha256(args.config),
            "providers": len(config["providers"]),
        }, indent=2))
        return 0
    backups = list_config_backups(args.config)
    if args.command == "list-backups":
        print(json.dumps({"status": "ok", "backups": [str(path) for path in backups]}, indent=2))
        return 0
    selected = args.backup or (backups[0] if backups else None)
    if selected is None:
        parser.error("没有可恢复的时间戳备份")
    before = config_sha256(args.config) if args.config.exists() else None
    restore_config_backup(selected, args.config)
    print(json.dumps({
        "status": "ok",
        "path": str(args.config.resolve()),
        "restored_from": str(selected.resolve()),
        "before_sha256": before,
        "after_sha256": config_sha256(args.config),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
