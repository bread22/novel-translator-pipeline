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


def output_complete(name: str, output_root: Path, translated_root: Path = ROOT / "translated") -> bool:
    translated_epub = translated_root / f"{name}-中文.epub"
    if translated_epub.exists() and translated_epub.stat().st_size > 5120:
        return True
    output_epub = output_root / name / f"{name}-中文.epub"
    if output_epub.exists() and output_epub.stat().st_size > 5120:
        return True
    progress = output_root / name / "data" / "progress.json"
    if progress.exists():
        try:
            payload = json.loads(progress.read_text(encoding="utf-8"))
            if payload.get("state") == "completed":
                return True
        except Exception:
            pass
    return False


def python_executable() -> str:
    venv_python = ROOT / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


class TranslationQueue:
    def __init__(self, config: dict[str, Any] | None = None, *, stop_on_error: bool | None = None) -> None:
        self.config = config or load_config()
        self.queue_cfg = self.config.get("queue", {})
        self.source_root = ROOT / self.queue_cfg.get("source_root", "source")
        self.output_root = ROOT / self.config.get("paths", {}).get("output_root", "output")
        self.translated_root = ROOT / "translated"
        self.translated_root.mkdir(parents=True, exist_ok=True)
        self.stop_on_error = (
            stop_on_error
            if stop_on_error is not None
            else bool(self.queue_cfg.get("stop_on_error", False))
        )
        self.layout = str(self.queue_cfg.get("layout", "horizontal"))

    def run_pipeline_for_book(self, book: str, name: str, cycles: int) -> tuple[int, str]:
        command = [
            python_executable(), "scripts/book_pipeline.py",
            "--book", book, "--name", name, "--output-root", str(self.output_root),
            "--max-cycles", str(cycles),
            "--layout", self.layout,
        ]
        if self.queue_cfg.get("apply", True):
            command.append("--apply")
        if self.queue_cfg.get("autonomous", True):
            command.append("--autonomous")
        if self.queue_cfg.get("finalize", True):
            command.append("--finalize")
        log(f"PIPELINE_START name={name} book={book} cycles={cycles}")
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
        last_error = ""
        if result.stdout.strip():
            log("PIPELINE_OUT " + result.stdout[-2400:])
        if result.stderr.strip():
            last_error = result.stderr[-2400:]
            log("PIPELINE_ERR " + last_error)
        log(f"PIPELINE_EXIT name={name} status={result.returncode}")
        return result.returncode, last_error

    def run(self) -> int:
        sources = sorted(self.source_root.glob("*.epub"), key=lambda path: path.name)
        log(f"QUEUE_START total_books={len(sources)} stop_on_error={self.stop_on_error} layout={self.layout}")
        print(f"\n🚀 启动全量批量翻译队列（共 {len(sources)} 本书籍）\n" + "=" * 60, flush=True)

        registered = registered_books()
        skipped: list[str] = []
        succeeded: list[str] = []
        failures: list[dict[str, Any]] = []

        summary_path = self.output_root / "reports" / "batch-summary.json"
        failures_path = self.output_root / "reports" / "batch-failures.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)

        for index, source in enumerate(sources, 1):
            name = display_name(source)
            prefix = f"[{index}/{len(sources)}]"

            if output_complete(name, self.output_root, self.translated_root):
                log(f"{prefix} SKIP {name}")
                print(f"{prefix} ⏩ 已完成，跳过：《{name}》", flush=True)
                skipped.append(name)
                continue

            print(f"\n{prefix} 📖 开始处理：《{name}》...", flush=True)
            try:
                book = ensure_book(source, registered)
            except Exception as exc:
                err_msg = f"注册解包失败: {exc}"
                log(f"{prefix} REGISTER_FAIL {name}: {err_msg}")
                print(f"{prefix} ❌ 《{name}》解包注册异常: {err_msg}，记录错误并跳到下一本...", flush=True)
                failures.append({
                    "name": name,
                    "source": str(source),
                    "error": err_msg,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                self._save_reports(summary_path, failures_path, len(sources), skipped, succeeded, failures)
                if self.stop_on_error:
                    break
                continue

            try:
                pending, translated, total = translation_status(book)
                log(f"{prefix} TRANSLATE {name} (id={book}) {translated}/{total} pending={pending}")
                print(f"{prefix} ⏳ 进度状态: {translated}/{total} 段 (待译 {pending})，启动流水线...", flush=True)
                status, last_err = self.run_pipeline_for_book(book, name, int(self.queue_cfg.get("max_cycles", 1000)))
                if status == 0:
                    log(f"{prefix} SUCCESS {name}")
                    print(f"{prefix} ✅ 《{name}》全书翻译并导出完成！", flush=True)
                    succeeded.append(name)
                else:
                    err_msg = f"流水线执行失败 (exit_code={status}): {last_err[-300:] if last_err else '未知错误'}"
                    log(f"{prefix} FAIL {name}: {err_msg}")
                    print(f"{prefix} ❌ 《{name}》翻译出错，已记录失败日志，继续处理下一本！", flush=True)
                    failures.append({
                        "name": name,
                        "book_id": book,
                        "source": str(source),
                        "error": err_msg,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    if self.stop_on_error:
                        break
            except Exception as exc:
                err_msg = f"未捕获运行时异常: {exc}"
                log(f"{prefix} EXCEPTION {name}: {err_msg}")
                print(f"{prefix} ❌ 《{name}》运行异常: {err_msg}，记录后继续下一本...", flush=True)
                failures.append({
                    "name": name,
                    "book_id": book,
                    "source": str(source),
                    "error": err_msg,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
                if self.stop_on_error:
                    break

            self._save_reports(summary_path, failures_path, len(sources), skipped, succeeded, failures)

        # Final reporting
        self._save_reports(summary_path, failures_path, len(sources), skipped, succeeded, failures)
        print("\n" + "=" * 60)
        print(f"🏁 批量任务结束！\n总书籍数: {len(sources)} | 成功: {len(succeeded)} | 跳过已完成: {len(skipped)} | 失败: {len(failures)}")
        if failures:
            print(f"⚠️ 失败清单已持久化记录至: {failures_path}")
            for f in failures:
                print(f"   - 《{f['name']}》: {f['error'][:100]}")
        print("=" * 60 + "\n", flush=True)

        return 1 if failures and self.stop_on_error else 0

    def _save_reports(
        self,
        summary_path: Path,
        failures_path: Path,
        total: int,
        skipped: list[str],
        succeeded: list[str],
        failures: list[dict[str, Any]],
    ) -> None:
        summary_data = {
            "total_books": total,
            "skipped_completed_count": len(skipped),
            "newly_succeeded_count": len(succeeded),
            "failed_count": len(failures),
            "skipped_books": skipped,
            "succeeded_books": succeeded,
            "failed_books": [f["name"] for f in failures],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        summary_path.write_text(json.dumps(summary_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        failures_path.write_text(json.dumps(failures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_pipeline(book: str, name: str, cycles: int, config: dict[str, Any] | None = None) -> int:
    status, _ = TranslationQueue(config).run_pipeline_for_book(book, name, cycles)
    return status


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run batch translation queue for pending EPUB books")
    parser.add_argument("--stop-on-error", action="store_true", default=False, help="遇到错误时停止（默认 False，出错自动记录并继续下一本）")
    parser.add_argument("--layout", choices=["preserve", "horizontal"], default="horizontal", help="导出 EPUB 版式（默认横排）")
    args = parser.parse_args()
    queue = TranslationQueue(stop_on_error=args.stop_on_error)
    queue.layout = args.layout
    return queue.run()


if __name__ == "__main__":
    raise SystemExit(main())
