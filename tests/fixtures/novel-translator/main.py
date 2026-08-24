#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys


def option(arguments: list[str], name: str) -> str:
    try:
        return arguments[arguments.index(name) + 1]
    except (ValueError, IndexError) as exc:
        raise ValueError(f"missing required option: {name}") from exc


def main() -> int:
    arguments = sys.argv[1:]
    try:
        command = option(arguments, "--agent-mode")
        if command != "add-book":
            raise ValueError(f"unsupported fixture command: {command}")

        source = Path(option(arguments, "--path")).resolve()
        title = option(arguments, "--title")
        book_id = option(arguments, "--id")
        book_dir = Path(__file__).resolve().parent / "data" / "books" / book_id
        book_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, book_dir / f"source{source.suffix.lower()}")

        text = source.read_text(encoding="utf-8") if source.suffix.lower() == ".txt" else ""
        manifest = {
            "book": book_id,
            "title": title,
            "source_type": source.suffix.lower().lstrip("."),
            "chapters": [
                {
                    "id": "c0001",
                    "index": 0,
                    "title": "Chapter One",
                    "paragraphs": [{"id": "p0001", "index": 0, "source": text.strip(), "translated": ""}],
                }
            ],
        }
        (book_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({"status": "ok", "summary": {"book": book_id, "title": title}}))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "errors": [{"code": "fixture_error", "message": str(exc)}]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
