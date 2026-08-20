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
    empty_book_memory,
    merge_chapter_state,
    merge_memory_delta,
    merge_term_updates,
    novel_translator_terms,
    read_json,
    utc_now,
    write_json,
)
from scripts.codex_review import run_codex_review
from scripts.codex_review import run_codex_chapter_review
from scripts.codex_review import run_codex_window_review
from scripts.novel_translator_tool import (
    NOVEL_TRANSLATOR_ROOT,
    call_novel_translator,
    call_novel_translator_with_batch_limit,
    provider_failure_reason,
)
from scripts.provider_translator import ProviderTranslator


ToolCall = Callable[..., dict[str, Any]]
Reviewer = Callable[[Path, Path], None]

OBJECTIVE_CATEGORIES = {
    "mistranslation",
    "subject_object",
    "pronoun_reference",
    "omission",
    "addition",
    "terminology",
    "factual_conflict",
    "context_conflict",
    "policy_violation",
}
OBJECTIVE_SEVERITIES = {"critical", "major"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Iterative EPUB translation and Codex review pipeline")
    parser.add_argument("--book", required=True, help="Novel Translator book id")
    parser.add_argument("--name", required=True, help="output/ 下的书籍目录名和中文书名")
    parser.add_argument("--output-root", type=Path, default=ROOT / "output")
    parser.add_argument("--max-cycles", type=int, default=1, help="chapter 模式下最多处理多少章；window 模式下最多推进多少批次")
    parser.add_argument("--review-chunk-size", type=int, default=30)
    parser.add_argument("--review-window-size", type=int, default=4, help="每次合并多少个翻译 batch 调用一次 GPT")
    parser.add_argument("--review-char-limit", type=int, default=40000, help="单个 GPT 审阅窗口的最大字符数")
    parser.add_argument("--review-mode", choices=["chapter", "window"], default="chapter", help="章节级审阅为默认；window 保留旧流程")
    parser.add_argument("--max-chapter-batches", type=int, default=1000, help="单章最多推进多少个翻译 batch")
    parser.add_argument("--translation-policy", type=Path, default=ROOT / "docs" / "prompts" / "translation-policy.md")
    parser.add_argument("--translate-retries", type=int, default=3, help="本地翻译失败批次的最大尝试次数")
    parser.add_argument("--recovery-batch-max-chars", type=int, default=700, help="失败批次最后一次重试使用的临时 batch 字符上限")
    parser.add_argument("--primary-batch-max-chars", type=int, default=12000, help="Gemini 主译每个大窗口的原文字符上限")
    parser.add_argument("--max-provider-split-depth", type=int, default=8, help="provider blocked 后最多二分深度")
    parser.add_argument("--fallback-provider", default="murasaki-local", choices=["murasaki-local"], help="Gemini blocked 后的 fallback provider")
    parser.add_argument("--translation-max-tokens", type=int, default=8192, help="单个翻译窗口的最大输出 token")
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
    approved: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        replacement = str(item.get("replacement", "") or item.get("approved_translation", "")).strip()
        is_new_contract = "category" in item or "replacement" in item
        if is_new_contract and (
            str(item.get("category", "")) not in OBJECTIVE_CATEGORIES
            or str(item.get("severity", "")) not in OBJECTIVE_SEVERITIES
        ):
            continue
        if (autonomous or item.get("auto_apply") is True) and float(item.get("confidence", 0) or 0) >= threshold and replacement:
            item["approved_translation"] = replacement
            item["replacement"] = replacement
            approved.append(item)
    return approved


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


def validate_chapter_review_payload(payload: dict[str, Any], expected_ids: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("章节审阅结果必须是 JSON 对象")
    checked = payload.get("checked_ids")
    fixes = payload.get("fixes")
    glossary_delta = payload.get("glossary_delta")
    memory_delta = payload.get("memory_delta")
    chapter_state = payload.get("chapter_state")
    if not isinstance(checked, list) or not isinstance(fixes, list):
        raise ValueError("章节审阅结果必须包含 checked_ids 和 fixes 数组")
    if not isinstance(glossary_delta, dict) or not isinstance(memory_delta, dict) or not isinstance(chapter_state, dict):
        raise ValueError("章节审阅结果必须包含 glossary_delta、memory_delta 和 chapter_state 对象")
    checked_ids = [str(item) for item in checked]
    received = set(checked_ids)
    unknown = sorted(received - expected_ids)
    missing = sorted(expected_ids - received)
    duplicate_ids = sorted({item for item in checked_ids if checked_ids.count(item) > 1})
    fix_ids = {str(item.get("id", "")) for item in fixes if isinstance(item, dict)}
    unknown_fixes = sorted(fix_ids - expected_ids)
    details: list[str] = []
    if unknown:
        details.append(f"checked_ids 未知 ID：{', '.join(unknown)}")
    if missing:
        details.append(f"checked_ids 缺少 ID：{', '.join(missing)}")
    if duplicate_ids:
        details.append(f"checked_ids 重复 ID：{', '.join(duplicate_ids)}")
    if unknown_fixes:
        details.append(f"fixes 未知 ID：{', '.join(unknown_fixes)}")
    if details:
        raise ValueError("章节审阅结果段落不匹配；" + "；".join(details))
    return payload


def validate_global_consistency_payload(payload: dict[str, Any], expected_chapter_ids: set[str]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("checked_chapters"), list):
        raise ValueError("全书一致性结果必须包含 checked_chapters 数组")
    checked = [str(item) for item in payload["checked_chapters"]]
    if len(checked) != len(set(checked)):
        raise ValueError("全书一致性结果包含重复章节 ID")
    unknown = sorted(set(checked) - expected_chapter_ids)
    missing = sorted(expected_chapter_ids - set(checked))
    if unknown or missing:
        details = []
        if unknown:
            details.append(f"未知章节 ID：{', '.join(unknown)}")
        if missing:
            details.append(f"缺少章节 ID：{', '.join(missing)}")
        raise ValueError("全书一致性结果覆盖范围不匹配；" + "；".join(details))
    if not isinstance(payload.get("conflicts"), list) or not isinstance(payload.get("recommendations"), list):
        raise ValueError("全书一致性结果必须包含 conflicts 和 recommendations 数组")
    return payload


def verify_applied_fixes(manifest: dict[str, Any], fixes: list[dict[str, Any]]) -> None:
    paragraphs = paragraph_map(manifest)
    mismatches: list[str] = []
    for fix in fixes:
        item_id = str(fix.get("id", ""))
        expected = str(fix.get("approved_translation", "") or fix.get("replacement", "")).strip()
        actual = str(paragraphs.get(item_id, {}).get("translated", "")).strip()
        if not item_id or not expected or actual != expected:
            mismatches.append(item_id or "<empty>")
    if mismatches:
        raise ValueError(f"应用修复后 manifest 未验证通过：{', '.join(mismatches)}")


class IterativePipeline:
    def __init__(
        self,
        *,
        book: str,
        workspace: BookWorkspace,
        manifest: Path,
        tool_call: ToolCall = call_novel_translator,
        targeted_translator: Callable[..., dict[str, Any]] | None = None,
        recovery_tool_call: Callable[..., dict[str, Any]] = call_novel_translator_with_batch_limit,
        reviewer: Reviewer = run_codex_review,
        window_reviewer: Reviewer = run_codex_window_review,
        chapter_reviewer: Reviewer = run_codex_chapter_review,
        review_chunk_size: int = 30,
        review_char_limit: int = 40000,
        translate_retries: int = 3,
        recovery_batch_max_chars: int = 700,
        primary_batch_max_chars: int = 12000,
        max_provider_split_depth: int = 8,
        fallback_provider: str = "murasaki-local",
        translation_max_tokens: int = 8192,
        max_chapter_batches: int = 1000,
        translation_policy: Path | None = None,
        apply: bool = False,
        autonomous: bool = False,
    ) -> None:
        if review_chunk_size < 1:
            raise ValueError("review_chunk_size 必须大于 0")
        self.book = book
        self.workspace = workspace
        self.manifest = manifest
        self.tool_call = tool_call
        self.targeted_translator = targeted_translator
        self.recovery_tool_call = recovery_tool_call
        self.reviewer = reviewer
        self.window_reviewer = window_reviewer
        self.chapter_reviewer = chapter_reviewer
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
        if primary_batch_max_chars < 1:
            raise ValueError("primary_batch_max_chars 必须大于 0")
        self.primary_batch_max_chars = primary_batch_max_chars
        if max_provider_split_depth < 0:
            raise ValueError("max_provider_split_depth 必须大于等于 0")
        self.max_provider_split_depth = max_provider_split_depth
        self.fallback_provider = fallback_provider
        if translation_max_tokens < 1:
            raise ValueError("translation_max_tokens 必须大于 0")
        self.translation_max_tokens = translation_max_tokens
        if max_chapter_batches < 1:
            raise ValueError("max_chapter_batches 必须大于 0")
        self.max_chapter_batches = max_chapter_batches
        self.translation_policy = translation_policy
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
                translation = self.tool_call(
                    "translate",
                    "--book", self.book,
                    "--max-batches", "1",
                    "--workers", "1",
                    "--rpm", "30",
                )
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

    def _chapter(self, chapter_id: str) -> dict[str, Any]:
        manifest = read_json(self.manifest)
        for chapter in manifest.get("chapters", []):
            if str(chapter.get("id", "")) == chapter_id:
                return chapter
        raise ValueError(f"没有匹配的章节：{chapter_id}")

    @staticmethod
    def _chapter_items(chapter: dict[str, Any]) -> list[dict[str, str]]:
        return [
            {
                "id": str(paragraph["id"]),
                "source": str(paragraph.get("source", "")),
                "translated": str(paragraph.get("translated", "")),
            }
            for paragraph in chapter.get("paragraphs", [])
            if isinstance(paragraph, dict) and paragraph.get("id") and str(paragraph.get("translated", "")).strip()
        ]

    @staticmethod
    def _chapter_pending_ids(chapter: dict[str, Any]) -> set[str]:
        return {
            str(paragraph["id"])
            for paragraph in chapter.get("paragraphs", [])
            if isinstance(paragraph, dict) and paragraph.get("id") and not str(paragraph.get("translated", "")).strip()
        }

    def _read_translation_policy(self) -> str:
        if self.translation_policy and self.translation_policy.exists():
            return self.translation_policy.read_text(encoding="utf-8")
        return "忠实翻译；不擅自增删事实、动作、人物关系、强度或信息。"

    def _previous_chapter_state(self, chapter_id: str) -> dict[str, Any]:
        manifest = read_json(self.manifest)
        chapters = manifest.get("chapters", [])
        for index, chapter in enumerate(chapters):
            if str(chapter.get("id", "")) == chapter_id and index > 0:
                previous_id = str(chapters[index - 1].get("id", ""))
                return read_json(self.workspace.chapter_states_dir / f"{previous_id}.json", {}) or {}
        return {}

    def _record_translation_provenance(self, ids: list[str], provider: str, reason: str = "") -> None:
        path = self.workspace.data_dir / "translation-provenance.json"
        records = read_json(path, {"book": self.book, "items": {}})
        records.setdefault("book", self.book)
        items = records.setdefault("items", {})
        for item_id in ids:
            items[item_id] = {"provider": provider, "reason": reason, "updated_at": utc_now()}
        write_json(path, records)

    def _record_provider_attempt(self, attempt: dict[str, Any]) -> None:
        path = self.workspace.data_dir / "provider-diagnostics.json"
        diagnostics = read_json(path, {"book": self.book, "attempts": []})
        diagnostics.setdefault("book", self.book)
        diagnostics.setdefault("attempts", []).append(attempt)
        write_json(path, diagnostics)

    def _chapter_pending_paragraphs(self, chapter_id: str) -> list[dict[str, Any]]:
        chapter = self._chapter(chapter_id)
        return [
            paragraph for paragraph in chapter.get("paragraphs", [])
            if isinstance(paragraph, dict) and paragraph.get("id") and not str(paragraph.get("translated", "")).strip()
        ]

    @staticmethod
    def _window(paragraphs: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
        window: list[dict[str, Any]] = []
        chars = 0
        for paragraph in paragraphs:
            size = len(str(paragraph.get("source", "")))
            if window and chars + size > max_chars:
                break
            window.append(paragraph)
            chars += size
        return window or paragraphs[:1]

    def _translate_target(self, provider: str, ids: list[str], source_chars: int) -> dict[str, Any]:
        if self.targeted_translator is None:
            if provider != "gemini":
                raise RuntimeError("测试/兼容模式未配置 fallback translator")
            return self.tool_call(
                "translate",
                "--book", self.book,
                "--max-batches", "1",
                "--workers", "1",
                "--rpm", "30",
            )
        return self.targeted_translator(
            provider,
            self.book,
            ids,
            source_chars=source_chars,
            max_tokens=self.translation_max_tokens,
        )

    def _translate_segment_with_recovery(
        self,
        chapter_id: str,
        paragraphs: list[dict[str, Any]],
        attempts: list[dict[str, Any]],
        depth: int = 0,
    ) -> None:
        ids = [str(item["id"]) for item in paragraphs]
        source_chars = sum(len(str(item.get("source", ""))) for item in paragraphs)
        try:
            result = self._translate_target("gemini", ids, source_chars)
            reason = provider_failure_reason(result)
        except Exception as exc:  # noqa: BLE001 - provider diagnostics are part of the run record
            result = {"status": "error", "error": str(exc)}
            reason = provider_failure_reason(result)
        remaining = {str(item["id"]) for item in self._chapter_pending_paragraphs(chapter_id)}
        attempt = {"provider": "gemini", "depth": depth, "ids": ids, "source_chars": source_chars, "result": result, "reason": reason, "remaining": sorted(remaining)}
        attempts.append(attempt)
        self._record_provider_attempt(attempt)
        if self.targeted_translator is None:
            self._record_translation_provenance(ids, "gemini")
            return
        if not (set(ids) & remaining):
            self._record_translation_provenance(ids, "gemini")
            return
        if reason == "content_filter":
            if len(ids) > 1 and depth < self.max_provider_split_depth:
                midpoint = max(1, len(ids) // 2)
                left = [item for item in paragraphs[:midpoint] if str(item["id"]) in remaining]
                right = [item for item in paragraphs[midpoint:] if str(item["id"]) in remaining]
                if left:
                    self._translate_segment_with_recovery(chapter_id, left, attempts, depth + 1)
                if right:
                    self._translate_segment_with_recovery(chapter_id, right, attempts, depth + 1)
                return
            fallback_result = self._translate_target("murasaki-local", sorted(set(ids) & remaining, key=ids.index), source_chars)
            fallback_remaining = {str(item["id"]) for item in self._chapter_pending_paragraphs(chapter_id)}
            fallback_attempt = {"provider": self.fallback_provider, "depth": depth, "ids": ids, "source_chars": source_chars, "result": fallback_result, "reason": "gemini_content_filter", "remaining": sorted(fallback_remaining)}
            attempts.append(fallback_attempt)
            self._record_provider_attempt(fallback_attempt)
            if set(ids) & fallback_remaining:
                raise RuntimeError(f"fallback 未完成章节 {chapter_id}：{', '.join(sorted(set(ids) & fallback_remaining))}")
            self._record_translation_provenance(ids, self.fallback_provider, "gemini_content_filter")
            return
        raise RuntimeError(f"Gemini provider error in {chapter_id}: {reason}; ids={','.join(ids)}")

    def _translate_chapter(self, chapter_id: str, cycle: int) -> dict[str, Any]:
        chapter = self._chapter(chapter_id)
        before_path = self.workspace.snapshots_dir / f"{chapter_id}-before.json"
        if not before_path.exists():
            snapshot = self.tool_call("snapshot", "--book", self.book, "--name", f"before-{chapter_id}")
            write_json(before_path, snapshot)
        attempts: list[dict[str, Any]] = []
        initial_pending = self._chapter_pending_ids(chapter)
        batches = 0
        while True:
            pending = self._chapter_pending_paragraphs(chapter_id)
            if not pending:
                break
            if batches >= self.max_chapter_batches:
                error = f"章节 {chapter_id} 超过 max_chapter_batches，仍有未译段落"
                self._save_translation_failure(f"chapter-{chapter_id}", attempts, error)
                raise RuntimeError(error)
            window = self._window(pending, self.primary_batch_max_chars)
            self._translate_segment_with_recovery(chapter_id, window, attempts)
            batches += 1
        final_chapter = self._chapter(chapter_id)
        items = self._chapter_items(final_chapter)
        return {"chapter_id": chapter_id, "items": items, "translated": len(items), "initial_pending": len(initial_pending), "attempts": attempts}

    def run_chapter(self, chapter_id: str, cycle: int) -> dict[str, Any]:
        translated = self._translate_chapter(chapter_id, cycle)
        items = translated["items"]
        if not items:
            return {"chapter_id": chapter_id, "translated": 0, "reviewed": 0, "done": True}
        chapter = self._chapter(chapter_id)
        glossary = read_json(self.workspace.glossary_path, {"book": self.book, "terms": [], "conflicts": []})
        memory = read_json(self.workspace.book_memory_path, empty_book_memory(self.book))
        previous_state = self._previous_chapter_state(chapter_id)
        provenance = read_json(self.workspace.data_dir / "translation-provenance.json", {"book": self.book, "items": {}})
        expected = {item["id"] for item in items}
        input_path = self.workspace.reviews_dir / f"{chapter_id}-input.json"
        output_path = self.workspace.reviews_dir / f"{chapter_id}-output.json"
        write_json(input_path, {
            "book": self.book,
            "chapter_id": chapter_id,
            "chapter_title": chapter.get("title", ""),
            "translation_policy": self._read_translation_policy(),
            "book_memory": memory,
            "previous_chapter_state": previous_state,
            "translation_provenance": {
                item_id: provenance.get("items", {}).get(item_id, {})
                for item_id in expected
                if item_id in provenance.get("items", {})
            },
            "items": items,
            "glossary": glossary.get("terms", []),
        })
        self.chapter_reviewer(input_path, output_path)
        review = read_json(output_path)
        if not isinstance(review, dict):
            raise ValueError(f"章节审阅结果不是 JSON 对象：{output_path}")
        for retry in range(1, 3):
            if not missing_checked_ids(review, expected):
                break
            retry_path = self.workspace.reviews_dir / f"{chapter_id}-retry-{retry:02d}.json"
            self.chapter_reviewer(input_path, retry_path)
            review = read_json(retry_path)
            if not isinstance(review, dict):
                raise ValueError(f"章节重审结果不是 JSON 对象：{retry_path}")
        review = validate_chapter_review_payload(review, expected)
        fixes = approved_fixes(review["fixes"], autonomous=self.autonomous)
        fixes_path = self.workspace.reviews_dir / f"{chapter_id}-approved-fixes.json"
        write_json(fixes_path, {"book": self.book, "chapter_id": chapter_id, "items": fixes})
        applied: dict[str, Any] | bool = False
        if self.apply and fixes:
            applied = self.tool_call("apply-review-fixes", "--book", self.book, "--input", str(fixes_path))
            verify_applied_fixes(read_json(self.manifest), fixes)
        glossary, term_summary = merge_term_updates(glossary, review["glossary_delta"].get("add", []) + review["glossary_delta"].get("update", []), chunk_id=f"chapter-{chapter_id}")
        write_json(self.workspace.glossary_path, glossary)
        terms_path = self.workspace.data_dir / "novel-translator-terms.json"
        write_json(terms_path, novel_translator_terms(glossary))
        terminology = self.tool_call("import-terminology", "--book", self.book, "--input", str(terms_path))
        memory, memory_summary = merge_memory_delta(memory, review["memory_delta"], chapter_id=chapter_id)
        write_json(self.workspace.book_memory_path, memory)
        chapter_state = merge_chapter_state(
            read_json(self.workspace.chapter_states_dir / f"{chapter_id}.json", {"chapter_id": chapter_id}),
            review["chapter_state"],
            chapter_id=chapter_id,
        )
        chapter_state.update({"status": "reviewed", "checked": len(expected), "fixes": len(fixes)})
        write_json(self.workspace.chapter_states_dir / f"{chapter_id}.json", chapter_state)
        after_snapshot = self.tool_call("snapshot", "--book", self.book, "--name", f"after-{chapter_id}")
        write_json(self.workspace.snapshots_dir / f"{chapter_id}-after.json", after_snapshot)
        quality = self.tool_call("quality-report", "--book", self.book)
        report = {
            "book": self.book,
            "chapter_id": chapter_id,
            "translated": len(items),
            "reviewed": len(expected),
            "fixes": len(fixes),
            "applied": applied,
            "term_updates": term_summary,
            "memory_delta": memory_summary,
            "terminology": terminology.get("summary", terminology),
            "quality": quality.get("summary", quality),
            "updated_at": utc_now(),
        }
        write_json(self.workspace.reports_dir / f"{chapter_id}.json", report)
        progress = read_json(self.workspace.progress_path, {})
        progress.update({
            "book": self.book,
            "state": "running",
            "completed_cycles": cycle,
            "last_chapter": chapter_id,
            "chapter_status": "reviewed",
            "last_translated": len(items),
            "last_reviewed": len(expected),
            "updated_at": utc_now(),
        })
        write_json(self.workspace.progress_path, progress)
        return report

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
        targeted_translator=ProviderTranslator(novel_root=NOVEL_TRANSLATOR_ROOT, manifest=manifest_path(args.book)),
        reviewer=lambda input_path, output_path: run_codex_review(input_path, output_path, autonomous=args.autonomous),
        window_reviewer=lambda input_path, output_path: run_codex_window_review(input_path, output_path, autonomous=args.autonomous),
        chapter_reviewer=lambda input_path, output_path: run_codex_chapter_review(input_path, output_path, autonomous=args.autonomous),
        review_chunk_size=args.review_chunk_size,
        review_char_limit=args.review_char_limit,
        translate_retries=args.translate_retries,
        recovery_batch_max_chars=args.recovery_batch_max_chars,
        primary_batch_max_chars=args.primary_batch_max_chars,
        max_provider_split_depth=args.max_provider_split_depth,
        fallback_provider=args.fallback_provider,
        translation_max_tokens=args.translation_max_tokens,
        max_chapter_batches=args.max_chapter_batches,
        translation_policy=args.translation_policy,
        apply=args.apply,
        autonomous=args.autonomous,
    )
    pipeline.initialize()
    results = []
    progress = read_json(workspace.progress_path, {})
    if args.review_mode == "chapter":
        manifest = read_json(manifest_path(args.book))
        chapters = manifest.get("chapters", [])
        last_chapter = str(progress.get("last_chapter", ""))
        start_index = 0
        if last_chapter:
            for index, chapter in enumerate(chapters):
                if str(chapter.get("id", "")) == last_chapter:
                    start_index = index + (1 if progress.get("chapter_status") == "reviewed" else 0)
                    break
        end_index = min(len(chapters), start_index + args.max_cycles)
        for index in range(start_index, end_index):
            chapter_id = str(chapters[index].get("id", ""))
            if not chapter_id:
                continue
            results.append(pipeline.run_chapter(chapter_id, index + 1))
    else:
        start = int(progress.get("completed_cycles", 0) or 0) + 1
        step = max(1, args.review_window_size)
        for cycle in range(start, start + args.max_cycles, step):
            result = pipeline.run_window(cycle, min(step, start + args.max_cycles - cycle))
            results.append(result)
            if result["done"]:
                break
    payload: dict[str, Any] = {"book": args.book, "workspace": str(workspace.root), "review_mode": args.review_mode, "cycles": results}
    if args.finalize:
        payload["finalize"] = pipeline.finalize()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
