from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.book_workspace import (
    BookWorkspace,
    merge_term_updates,
    novel_translator_terms,
    read_json,
    utc_now,
    write_json,
)
from scripts.codex_review import run_codex_review
from scripts.codex_review import run_codex_window_review
from scripts.novel_translator_tool import (
    NOVEL_TRANSLATOR_ROOT,
    call_novel_translator,
    call_novel_translator_with_batch_limit,
)


ToolCall = Callable[..., dict[str, Any]]
Reviewer = Callable[[Path, Path], None]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Iterative EPUB translation and Codex review pipeline")
    parser.add_argument("--book", required=True, help="Novel Translator book id")
    parser.add_argument("--name", required=True, help="output/ 下的书籍目录名和中文书名")
    parser.add_argument("--output-root", type=Path, default=ROOT / "output")
    parser.add_argument("--max-cycles", type=int, default=1, help="本次最多翻译并审阅多少个批次")
    parser.add_argument("--review-chunk-size", type=int, default=30)
    parser.add_argument("--review-window-size", type=int, default=4, help="每次合并多少个翻译 batch 调用一次 GPT")
    parser.add_argument("--review-char-limit", type=int, default=40000, help="单个 GPT 审阅窗口的最大字符数")
    parser.add_argument("--translate-retries", type=int, default=3, help="本地翻译失败批次的最大尝试次数")
    parser.add_argument("--recovery-batch-max-chars", type=int, default=700, help="失败批次最后一次重试使用的临时 batch 字符上限")
    parser.add_argument("--apply", action="store_true", help="应用高置信度译文修复")
    parser.add_argument("--autonomous", action="store_true", help="全自动应用 Codex 置信度 >= 0.9 的有效修复")
    parser.add_argument("--finalize", action="store_true", help="全部翻译完成后导出并校验中文 EPUB")
    return parser.parse_args()


def manifest_path(book: str) -> Path:
    return NOVEL_TRANSLATOR_ROOT / "data" / "books" / book / "manifest.json"


def paragraph_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(paragraph["id"]): paragraph
        for chapter in manifest.get("chapters", [])
        for paragraph in chapter.get("paragraphs", [])
        if isinstance(paragraph, dict) and paragraph.get("id")
    }


def newly_translated(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, str]]:
    old = paragraph_map(before)
    items: list[dict[str, str]] = []
    for paragraph_id, paragraph in paragraph_map(after).items():
        translated = str(paragraph.get("translated", "")).strip()
        previous = str(old.get(paragraph_id, {}).get("translated", "")).strip()
        if translated and not previous:
            items.append({"id": paragraph_id, "source": str(paragraph.get("source", "")), "translated": translated})
    return items


def approved_fixes(items: list[dict[str, Any]], threshold: float = 0.9, *, autonomous: bool = False) -> list[dict[str, Any]]:
    return [
        item for item in items
        if (autonomous or item.get("auto_apply") is True)
        and float(item.get("confidence", 0) or 0) >= threshold
        and str(item.get("approved_translation", "")).strip()
    ]


def validate_review_payload(payload: dict[str, Any], expected_ids: set[str]) -> dict[str, Any]:
    items = payload.get("items")
    term_updates = payload.get("term_updates")
    if not isinstance(items, list) or not isinstance(term_updates, list):
        raise ValueError("审阅结果必须包含 items 和 term_updates 数组")
    received = {str(item.get("id", "")) for item in items if isinstance(item, dict)}
    missing = sorted(expected_ids - received)
    unknown = sorted(received - expected_ids)
    if unknown:
        details = []
        if unknown:
            details.append(f"未知 ID：{', '.join(unknown)}")
        raise ValueError("审阅结果段落不匹配；" + "；".join(details))
    if missing:
        payload = dict(payload)
        payload["items"] = list(items) + [
            {
                "id": item_id,
                "severity": "info",
                "issues": [],
                "suggestion": "",
                "approved_translation": "",
                "auto_apply": False,
                "confidence": 0,
            }
            for item_id in missing
        ]
    return payload


def missing_review_ids(payload: dict[str, Any], expected_ids: set[str]) -> set[str]:
    items = payload.get("items", []) if isinstance(payload, dict) else []
    received = {str(item.get("id", "")) for item in items if isinstance(item, dict)}
    return expected_ids - received


def missing_checked_ids(payload: dict[str, Any], expected_ids: set[str]) -> set[str]:
    checked = payload.get("checked_ids", []) if isinstance(payload, dict) else []
    return expected_ids - {str(item) for item in checked}


def failed_batch_count(payload: dict[str, Any]) -> int:
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    try:
        return max(0, int(summary.get("failed", 0) or 0))
    except (TypeError, ValueError):
        return 0


def validate_window_review_payload(payload: dict[str, Any], expected_ids: set[str]) -> dict[str, Any]:
    checked = payload.get("checked_ids")
    issues = payload.get("issues")
    terms = payload.get("term_updates")
    if not isinstance(checked, list) or not isinstance(issues, list) or not isinstance(terms, list):
        raise ValueError("窗口审阅结果必须包含 checked_ids、issues 和 term_updates 数组")
    checked_ids = {str(item) for item in checked}
    unknown_checked = sorted(checked_ids - expected_ids)
    unknown_issues = sorted(
        {str(item.get("id", "")) for item in issues if isinstance(item, dict)} - expected_ids
    )
    if unknown_checked or unknown_issues:
        details = []
        if unknown_checked:
            details.append(f"checked_ids 未知 ID：{', '.join(unknown_checked)}")
        if unknown_issues:
            details.append(f"issues 未知 ID：{', '.join(unknown_issues)}")
        raise ValueError("窗口审阅结果段落不匹配；" + "；".join(details))
    return payload


class IterativePipeline:
    def __init__(
        self,
        *,
        book: str,
        workspace: BookWorkspace,
        manifest: Path,
        tool_call: ToolCall = call_novel_translator,
        recovery_tool_call: Callable[..., dict[str, Any]] = call_novel_translator_with_batch_limit,
        reviewer: Reviewer = run_codex_review,
        window_reviewer: Reviewer = run_codex_window_review,
        review_chunk_size: int = 30,
        review_char_limit: int = 40000,
        translate_retries: int = 3,
        recovery_batch_max_chars: int = 700,
        apply: bool = False,
        autonomous: bool = False,
    ) -> None:
        if review_chunk_size < 1:
            raise ValueError("review_chunk_size 必须大于 0")
        self.book = book
        self.workspace = workspace
        self.manifest = manifest
        self.tool_call = tool_call
        self.recovery_tool_call = recovery_tool_call
        self.reviewer = reviewer
        self.window_reviewer = window_reviewer
        self.review_chunk_size = review_chunk_size
        if review_char_limit < 1:
            raise ValueError("review_char_limit 必须大于 0")
        self.review_char_limit = review_char_limit
        if translate_retries < 1:
            raise ValueError("translate_retries 必须大于 0")
        self.translate_retries = translate_retries
        if recovery_batch_max_chars < 1:
            raise ValueError("recovery_batch_max_chars 必须大于 0")
        self.recovery_batch_max_chars = recovery_batch_max_chars
        self.apply = apply
        self.autonomous = autonomous

    def initialize(self) -> None:
        raw = read_json(self.manifest)
        if not isinstance(raw, dict):
            raise FileNotFoundError(f"Novel Translator manifest not found: {self.manifest}")
        source = Path(str(raw.get("source_file", ""))).expanduser()
        self.workspace.initialize(source if source.suffix.casefold() == ".epub" else None, book_id=self.book)

    def _translate_only(self, cycle: int) -> dict[str, Any]:
        before = read_json(self.manifest)
        chunk_id = f"chunk-{cycle:05d}"
        snapshot = self.tool_call("snapshot", "--book", self.book, "--name", f"before-{chunk_id}")
        write_json(self.workspace.snapshots_dir / f"{chunk_id}.json", snapshot)
        attempts: list[dict[str, Any]] = []
        for attempt in range(1, self.translate_retries + 1):
            try:
                translation = self.tool_call("translate", "--book", self.book, "--max-batches", "1")
            except Exception as exc:  # noqa: BLE001 - record the CLI failure before recovery
                translation = {"status": "error", "error": str(exc)}
            try:
                failed = self.tool_call("failed-batches", "--book", self.book)
            except Exception as exc:  # noqa: BLE001 - failure status itself is diagnostic data
                failed = {"status": "error", "error": str(exc), "summary": {"failed": 1}}
            failed_count = failed_batch_count(failed)
            after = read_json(self.manifest)
            items = newly_translated(before, after)
            try:
                status = self.tool_call("translation-status", "--book", self.book)
            except Exception as exc:  # noqa: BLE001 - preserve the recovery record
                status = {"status": "error", "error": str(exc), "summary": {"pending": 1}}
            summary = status.get("summary", {}) if isinstance(status, dict) else {}
            try:
                pending = max(0, int(summary.get("pending", 1) or 0))
            except (TypeError, ValueError):
                pending = 1
            attempts.append({
                "attempt": attempt,
                "translation": translation,
                "failed_batches": failed,
                "newly_translated": len(items),
                "pending": pending,
            })
            if failed_count == 0:
                if items or pending == 0:
                    return {
                        "chunk_id": chunk_id,
                        "translation": translation,
                        "items": items,
                        "pending": pending,
                        "done": pending == 0 and not items,
                        "attempts": attempts,
                    }
                error = "翻译没有产生新段落，但仍有待译段落"
                self._save_translation_failure(chunk_id, attempts, error)
                raise RuntimeError(error)
            if attempt < self.translate_retries:
                if attempt == self.translate_retries - 1:
                    self.recovery_tool_call(self.recovery_batch_max_chars, "retry-failed", "--book", self.book)
                else:
                    self.tool_call("retry-failed", "--book", self.book)

        error = f"本地翻译失败批次连续 {self.translate_retries} 次未恢复"
        self._save_translation_failure(chunk_id, attempts, error)
        raise RuntimeError(error)

    def _save_translation_failure(self, chunk_id: str, attempts: list[dict[str, Any]], error: str) -> None:
        report = {
            "book": self.book,
            "chunk_id": chunk_id,
            "error": error,
            "attempts": attempts,
            "updated_at": utc_now(),
        }
        write_json(self.workspace.reports_dir / f"translation-failure-{chunk_id}.json", report)
        progress = read_json(self.workspace.progress_path, {})
        progress.update({"book": self.book, "state": "paused", "last_chunk": chunk_id, "failure_report": str(self.workspace.reports_dir / f"translation-failure-{chunk_id}.json"), "updated_at": utc_now()})
        write_json(self.workspace.progress_path, progress)

    def run_cycle(self, cycle: int) -> dict[str, Any]:
        translated = self._translate_only(cycle)
        chunk_id = translated["chunk_id"]
        translation = translated["translation"]
        items = translated["items"]
        if not items:
            return {"chunk_id": chunk_id, "translated": 0, "done": True, "translation": translation}

        glossary = read_json(self.workspace.glossary_path, {"book": self.book, "terms": [], "conflicts": []})
        all_reviews: list[dict[str, Any]] = []
        all_terms: list[dict[str, Any]] = []
        for offset in range(0, len(items), self.review_chunk_size):
            part = offset // self.review_chunk_size + 1
            review_items = items[offset: offset + self.review_chunk_size]
            input_path = self.workspace.reviews_dir / f"{chunk_id}-part-{part:03d}-input.json"
            output_path = self.workspace.reviews_dir / f"{chunk_id}-part-{part:03d}-output.json"
            write_json(
                input_path,
                {"book": self.book, "chunk_id": chunk_id, "items": review_items, "glossary": glossary.get("terms", [])},
            )
            self.reviewer(input_path, output_path)
            review = read_json(output_path)
            if not isinstance(review, dict):
                raise ValueError(f"审阅结果不是 JSON 对象：{output_path}")
            expected_ids = {item["id"] for item in review_items}
            for retry in range(1, 3):
                missing = missing_review_ids(review, expected_ids)
                if not missing:
                    break
                retry_path = self.workspace.reviews_dir / f"{chunk_id}-part-{part:03d}-retry-{retry:02d}.json"
                self.reviewer(input_path, retry_path)
                retried = read_json(retry_path)
                if not isinstance(retried, dict):
                    raise ValueError(f"重审结果不是 JSON 对象：{retry_path}")
                review = retried
            review = validate_review_payload(review, expected_ids)
            all_reviews.extend(review.get("items", []))
            all_terms.extend(review.get("term_updates", []))

        glossary, term_summary = merge_term_updates(glossary, all_terms, chunk_id=chunk_id)
        write_json(self.workspace.glossary_path, glossary)
        tool_terms_path = self.workspace.data_dir / "novel-translator-terms.json"
        write_json(tool_terms_path, novel_translator_terms(glossary))
        terminology = self.tool_call("import-terminology", "--book", self.book, "--input", str(tool_terms_path))

        fixes = approved_fixes(all_reviews, autonomous=self.autonomous)
        fixes_path = self.workspace.reviews_dir / f"{chunk_id}-approved-fixes.json"
        write_json(fixes_path, {"book": self.book, "items": fixes})
        applied: dict[str, Any] | bool = False
        if self.apply and fixes:
            applied = self.tool_call("apply-review-fixes", "--book", self.book, "--input", str(fixes_path))

        quality = self.tool_call("quality-report", "--book", self.book)
        write_json(self.workspace.reports_dir / f"{chunk_id}-quality.json", quality)
        progress = read_json(self.workspace.progress_path, {})
        progress.update(
            {
                "book": self.book,
                "state": "running",
                "completed_cycles": cycle,
                "last_chunk": chunk_id,
                "last_translated": len(items),
                "last_reviewed": len(all_reviews),
                "updated_at": utc_now(),
            }
        )
        write_json(self.workspace.progress_path, progress)
        return {
            "chunk_id": chunk_id,
            "translated": len(items),
            "reviewed": len(all_reviews),
            "term_updates": term_summary,
            "candidate_fixes": len(fixes),
            "applied": applied,
            "terminology": terminology.get("summary", terminology),
            "quality": quality.get("summary", quality),
            "done": False,
        }

    def run_window(self, first_cycle: int, width: int) -> dict[str, Any]:
        if width < 1:
            raise ValueError("review_window_size 必须大于 0")
        window_id = f"window-{first_cycle:05d}"
        collected: list[dict[str, str]] = []
        cycles: list[dict[str, Any]] = []
        last_cycle = first_cycle
        done = False
        for cycle in range(first_cycle, first_cycle + width):
            translated = self._translate_only(cycle)
            last_cycle = cycle
            items = translated["items"]
            cycles.append({"chunk_id": translated["chunk_id"], "translated": len(items)})
            if not items:
                done = bool(translated.get("done", False))
                break
            collected.extend(items)

        if not collected:
            return {"window_id": window_id, "cycles": cycles, "translated": 0, "done": done}

        glossary = read_json(self.workspace.glossary_path, {"book": self.book, "terms": [], "conflicts": []})
        windows: list[list[dict[str, str]]] = []
        current: list[dict[str, str]] = []
        current_chars = 0
        for item in collected:
            size = len(item["source"]) + len(item["translated"])
            if current and current_chars + size > self.review_char_limit:
                windows.append(current)
                current = []
                current_chars = 0
            current.append(item)
            current_chars += size
        if current:
            windows.append(current)

        all_issues: list[dict[str, Any]] = []
        all_terms: list[dict[str, Any]] = []
        for part, review_items in enumerate(windows, 1):
            input_path = self.workspace.reviews_dir / f"{window_id}-part-{part:03d}-input.json"
            output_path = self.workspace.reviews_dir / f"{window_id}-part-{part:03d}-output.json"
            write_json(
                input_path,
                {"book": self.book, "window_id": window_id, "items": review_items, "glossary": glossary.get("terms", [])},
            )
            self.window_reviewer(input_path, output_path)
            review = read_json(output_path)
            if not isinstance(review, dict):
                raise ValueError(f"窗口审阅结果不是 JSON 对象：{output_path}")
            expected_ids = {item["id"] for item in review_items}
            for retry in range(1, 3):
                if not missing_checked_ids(review, expected_ids):
                    break
                retry_path = self.workspace.reviews_dir / f"{window_id}-part-{part:03d}-retry-{retry:02d}.json"
                self.window_reviewer(input_path, retry_path)
                review = read_json(retry_path)
                if not isinstance(review, dict):
                    raise ValueError(f"窗口重审结果不是 JSON 对象：{retry_path}")
            review = validate_window_review_payload(review, expected_ids)
            if missing_checked_ids(review, expected_ids):
                raise ValueError(f"窗口审阅两次重试后仍漏回 ID：{', '.join(sorted(missing_checked_ids(review, expected_ids)))}")
            all_issues.extend(review["issues"])
            all_terms.extend(review["term_updates"])

        glossary, term_summary = merge_term_updates(glossary, all_terms, chunk_id=window_id)
        write_json(self.workspace.glossary_path, glossary)
        tool_terms_path = self.workspace.data_dir / "novel-translator-terms.json"
        write_json(tool_terms_path, novel_translator_terms(glossary))
        terminology = self.tool_call("import-terminology", "--book", self.book, "--input", str(tool_terms_path))
        fixes = approved_fixes(all_issues, autonomous=self.autonomous)
        fixes_path = self.workspace.reviews_dir / f"{window_id}-approved-fixes.json"
        write_json(fixes_path, {"book": self.book, "items": fixes})
        applied: dict[str, Any] | bool = False
        if self.apply and fixes:
            applied = self.tool_call("apply-review-fixes", "--book", self.book, "--input", str(fixes_path))
        quality = self.tool_call("quality-report", "--book", self.book)
        write_json(self.workspace.reports_dir / f"{window_id}-quality.json", quality)
        progress = read_json(self.workspace.progress_path, {})
        progress.update(
            {
                "book": self.book,
                "state": "running",
                "completed_cycles": last_cycle,
                "last_chunk": window_id,
                "last_translated": len(collected),
                "last_reviewed": len(collected),
                "updated_at": utc_now(),
            }
        )
        write_json(self.workspace.progress_path, progress)
        return {
            "window_id": window_id,
            "cycles": cycles,
            "translated": len(collected),
            "reviewed": len(collected),
            "issues": len(all_issues),
            "term_updates": term_summary,
            "candidate_fixes": len(fixes),
            "applied": applied,
            "terminology": terminology.get("summary", terminology),
            "quality": quality.get("summary", quality),
            "done": done,
        }

    def finalize(self) -> dict[str, Any]:
        status = self.tool_call("translation-status", "--book", self.book)
        if int(status.get("summary", {}).get("pending", 1)) != 0:
            return {"status": "pending", "translation": status.get("summary", status)}
        failed = self.tool_call("failed-batches", "--book", self.book)
        if failed_batch_count(failed) > 0:
            report = {"book": self.book, "status": "paused", "error": "仍存在失败翻译批次", "failed_batches": failed, "updated_at": utc_now()}
            write_json(self.workspace.reports_dir / "finalize-blocked.json", report)
            return report
        output = self.workspace.root / f"{self.workspace.root.name}-中文.epub"
        validation = self.tool_call("validate-export", "--book", self.book, "--format", "epub")
        exported = self.tool_call("export", "--book", self.book, "--format", "epub", "--output", str(output), "--monolingual")
        epub_validation = self.tool_call("validate-epub", "--path", str(output))
        result = {
            "status": "exported",
            "output": str(output),
            "validation": validation,
            "export": exported,
            "epub_validation": epub_validation,
        }
        write_json(self.workspace.reports_dir / "final-export.json", result)
        progress = read_json(self.workspace.progress_path, {})
        progress.update({"state": "completed", "output": str(output), "updated_at": utc_now()})
        write_json(self.workspace.progress_path, progress)
        return result


def main() -> int:
    args = parse_args()
    if args.max_cycles < 0:
        raise ValueError("max_cycles 必须大于或等于 0")
    workspace = BookWorkspace.at(args.output_root, args.name)
    pipeline = IterativePipeline(
        book=args.book,
        workspace=workspace,
        manifest=manifest_path(args.book),
        reviewer=lambda input_path, output_path: run_codex_review(input_path, output_path, autonomous=args.autonomous),
        window_reviewer=lambda input_path, output_path: run_codex_window_review(input_path, output_path, autonomous=args.autonomous),
        review_chunk_size=args.review_chunk_size,
        review_char_limit=args.review_char_limit,
        translate_retries=args.translate_retries,
        recovery_batch_max_chars=args.recovery_batch_max_chars,
        apply=args.apply,
        autonomous=args.autonomous,
    )
    pipeline.initialize()
    results = []
    progress = read_json(workspace.progress_path, {})
    start = int(progress.get("completed_cycles", 0) or 0) + 1
    step = max(1, args.review_window_size)
    for cycle in range(start, start + args.max_cycles, step):
        result = pipeline.run_window(cycle, min(step, start + args.max_cycles - cycle))
        results.append(result)
        if result["done"]:
            break
    payload: dict[str, Any] = {"book": args.book, "workspace": str(workspace.root), "cycles": results}
    if args.finalize:
        payload["finalize"] = pipeline.finalize()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
