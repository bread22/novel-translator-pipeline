from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from translator.core.config import load_config
from translator.core.novel_tool import NOVEL_TRANSLATOR_PYTHON, NOVEL_TRANSLATOR_ROOT


ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "artifacts" / "translation-queue.log"


def log(message: str) -> None:
    line = f"[{datetime.now(timezone.utc).isoformat()}] {message}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(line + "\n")


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


def novel_call(arguments: list[str]) -> tuple[int, dict | None, str]:
    command = [str(NOVEL_TRANSLATOR_PYTHON), str(NOVEL_TRANSLATOR_ROOT / "main.py"), "--agent-mode", *arguments, "--json"]
    result = subprocess.run(command, cwd=NOVEL_TRANSLATOR_ROOT, text=True, capture_output=True, check=False)
    raw = (result.stdout or "").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = None
    if result.returncode:
        log(f"NOVEL_FAIL args={arguments[:3]} exit={result.returncode} stderr={result.stderr[-800:]} stdout={raw[-800:]}")
    return result.returncode, payload, raw


def registered_books() -> dict[str, str]:
    result: dict[str, str] = {}
    for manifest in sorted((NOVEL_TRANSLATOR_ROOT / "data" / "books").glob("*/manifest.json")):
        source = manifest.parent / "source.epub"
        if source.exists():
            result[sha256(source)] = manifest.parent.name
    return result


def ensure_book(source: Path, registered: dict[str, str]) -> str:
    digest = sha256(source)
    if digest in registered:
        return registered[digest]
    name = display_name(source)
    status, payload, raw = novel_call(["add-book", "--path", str(source), "--title", name, "--id", requested_book_id(name)])
    if status or not isinstance(payload, dict):
        raise RuntimeError(f"add-book failed: {raw[-1000:]}")
    book = str(payload.get("summary", {}).get("book", "")).strip()
    if not book:
        raise RuntimeError("add-book response missing book id")
    registered[digest] = book
    return book


def translation_status(book: str) -> tuple[int, int, int]:
    _status, payload, _raw = novel_call(["translation-status", "--book", book])
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    return int(summary.get("pending", 0)), int(summary.get("translated", 0)), int(summary.get("total", 0))


def output_complete(name: str, output_root: Path) -> bool:
    progress = output_root / name / "data" / "progress.json"
    if not progress.exists():
        return False
    try:
        payload = json.loads(progress.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    output = Path(str(payload.get("output", ""))) if payload.get("output") else output_root / name / f"{name}-中文.epub"
    return payload.get("state") == "completed" and output.exists()


def python_executable() -> str:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


class TranslationQueue:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_config()
        self.queue_cfg = self.config["queue"]
        self.source_root = ROOT / self.queue_cfg["source_root"]
        self.output_root = ROOT / self.config["paths"]["output_root"]

    def run_pipeline_for_book(self, book: str, name: str, cycles: int) -> int:
        command = [
            python_executable(), "scripts/book_pipeline.py",
            "--book", book, "--name", name, "--output-root", str(self.output_root),
            "--max-cycles", str(cycles),
        ]
        if self.queue_cfg["apply"]:
            command.append("--apply")
        if self.queue_cfg["autonomous"]:
            command.append("--autonomous")
        if self.queue_cfg["finalize"]:
            command.append("--finalize")
        log(f"PIPELINE_START name={name} book={book} cycles={cycles} roles={self.config['roles']}")
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        if result.stdout.strip():
            log("PIPELINE_OUT " + result.stdout[-2400:])
        if result.stderr.strip():
            log("PIPELINE_ERR " + result.stderr[-2400:])
        log(f"PIPELINE_EXIT name={name} status={result.returncode}")
        return result.returncode

    def run(self) -> int:
        sources = sorted(self.source_root.glob("*.epub"), key=lambda path: path.name)
        registered = registered_books()
        log(f"QUEUE_START total={len(sources)}")
        failures: list[str] = []
        for index, source in enumerate(sources, 1):
            name = display_name(source)
            if output_complete(name, self.output_root):
                log(f"[{index}/{len(sources)}] SKIP {name}")
                continue
            book = ensure_book(source, registered)
            pending, translated, total = translation_status(book)
            log(f"[{index}/{len(sources)}] TRANSLATE {name} {translated}/{total} pending={pending}")
            status = self.run_pipeline_for_book(book, name, int(self.queue_cfg["max_cycles"]) if pending else 0)
            if status:
                failures.append(name)
                if self.queue_cfg["stop_on_error"]:
                    break
        if failures:
            log("QUEUE_FAILED " + json.dumps(failures, ensure_ascii=False))
            return 1
        log("QUEUE_COMPLETE")
        return 0


def run_pipeline(book: str, name: str, cycles: int, config: dict[str, Any] | None = None) -> int:
    return TranslationQueue(config).run_pipeline_for_book(book, name, cycles)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run batch translation queue for pending EPUB books")
    parser.add_argument("--stop-on-error", action="store_true", help="遇到错误时停止")
    parser.parse_args()
    queue = TranslationQueue()
    return queue.run()


if __name__ == "__main__":
    raise SystemExit(main())
