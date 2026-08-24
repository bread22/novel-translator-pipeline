#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from translator.core.config import load_config
from translator.core.job_manager import JobManager, TERMINAL_STATUSES
from translator.core.novel_tool import NOVEL_TRANSLATOR_ROOT, call_novel_translator
from translator.core.paths import PathResolver
from translator.web.models import PipelineStartRequest


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def display_name(path: Path) -> str:
    return path.stem.split(" (", 1)[0].strip()


def requested_book_id(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u3040-\u30ff\u3400-\u9fff]+", "-", name).strip("-") or "book"


def registered_books() -> dict[str, str]:
    result: dict[str, str] = {}
    for manifest in sorted((NOVEL_TRANSLATOR_ROOT / "data" / "books").glob("*/manifest.json")):
        source = manifest.parent / "source.epub"
        if source.is_file():
            result[sha256(source)] = manifest.parent.name
    return result


def ensure_book(source: Path, registered: dict[str, str]) -> str:
    digest = sha256(source)
    if digest in registered:
        return registered[digest]
    name = display_name(source)
    result = call_novel_translator(
        "add-book", "--path", str(source.resolve()), "--title", name, "--id", requested_book_id(name)
    )
    book_id = str(result.get("summary", {}).get("book", "")).strip()
    if result.get("status") not in {"ok", "success"} or not book_id:
        raise RuntimeError(f"add-book failed: {result.get('errors') or result}")
    registered[digest] = book_id
    return book_id


def output_complete(name: str, output_root: Path, translated_root: Path) -> bool:
    candidates = (translated_root / f"{name}-中文.epub", output_root / name / f"{name}-中文.epub")
    if any(path.is_file() and path.stat().st_size > 5120 for path in candidates):
        return True
    progress = output_root / name / "data" / "progress.json"
    if progress.is_file():
        try:
            return json.loads(progress.read_text(encoding="utf-8")).get("state") == "completed"
        except (OSError, ValueError):
            return False
    return False


def run_batch(*, stop_on_error: bool = False, layout: str = "horizontal", poll_interval: float = 0.5) -> int:
    config = load_config()
    paths = PathResolver.for_config()
    source_root = paths.source_root(config)
    output_root = paths.output_root(config)
    translated_root = paths.translated_root(config)
    sources = sorted(source_root.glob("*.epub"), key=lambda path: path.name)
    manager = JobManager(output_root=output_root)
    manager.update_config(stop_on_error=stop_on_error)
    registered = registered_books()
    task_ids: list[str] = []
    failures: list[str] = []

    for source in sources:
        name = display_name(source)
        if output_complete(name, output_root, translated_root):
            print(f"SKIP completed: {name}", flush=True)
            continue
        try:
            book_id = ensure_book(source, registered)
            item = manager.enqueue(
                book_id,
                options=PipelineStartRequest(
                    book_id=book_id,
                    apply=bool(config.get("queue", {}).get("apply", True)),
                    autonomous=bool(config.get("queue", {}).get("autonomous", True)),
                    finalize=bool(config.get("queue", {}).get("finalize", True)),
                    layout=layout,
                    max_cycles=int(config.get("queue", {}).get("max_cycles", 1000)),
                ),
                book_name=name,
            )
            task_ids.append(item.id)
            print(f"ENQUEUED {name}: {item.id}", flush=True)
        except Exception as exc:
            failures.append(f"{name}: {exc}")
            print(f"REGISTER_FAILED {name}: {exc}", flush=True)
            if stop_on_error:
                return 1

    if task_ids:
        manager.resume_queue()
    while task_ids:
        unfinished = []
        for task_id in task_ids:
            task = manager.get_task(task_id)
            if task is None or task.status in TERMINAL_STATUSES:
                if task is None or task.status != "completed":
                    failures.append(f"{task_id}: {task.status if task else 'missing'}")
                continue
            unfinished.append(task_id)
        task_ids = unfinished
        if task_ids:
            time.sleep(poll_interval)

    stamp = datetime.now(timezone.utc).isoformat()
    print(f"BATCH_FINISHED at={stamp} failed={len(failures)}", flush=True)
    for failure in failures:
        print(f"- {failure}", flush=True)
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Register source EPUBs and run them through the unified JobManager")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--layout", choices=["preserve", "horizontal"], default="horizontal")
    parser.add_argument("--poll-interval", type=float, default=0.5)
    args = parser.parse_args()
    return run_batch(stop_on_error=args.stop_on_error, layout=args.layout, poll_interval=max(0.01, args.poll_interval))


if __name__ == "__main__":
    raise SystemExit(main())
