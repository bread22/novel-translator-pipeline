from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import time
from typing import Any, Callable, Mapping
import uuid
import zipfile

from translator.core.config import (
    dual_review_enabled,
    fallback_translators_names,
    fallback_reviewers_names,
    load_config,
    primary_translator_name,
    reviewer_name,
    secondary_reviewer_name,
    setting,
)
from translator.core.layout import apply_horizontal_layout, inject_epub_metadata
from translator.core.metadata import extract_book_metadata, sanitize_epub_filename
from translator.core.job_control import CancellationToken, JobCancelled, PauseGate
from translator.core.novel_tool import (
    NOVEL_TRANSLATOR_ROOT,
    call_novel_translator,
    provider_failure_reason,
)
from translator.core.paths import PathResolver
from translator.core.report import generate_work_report
from translator.core.workspace import (
    BookWorkspace,
    empty_book_memory,
    read_json,
    utc_now,
    write_json,
)
from translator.pipeline.preflight import PreflightError, run_preflight
from translator.providers.translator import ProviderTranslator
from translator.review.context_budget import ReviewContextOverflowError
from translator.review.knowledge_extractor import (
    aggregate_candidates,
    apply_knowledge_delta,
    build_finalization_payload,
    compact_finalization_payload,
    finalization_prompt_chars,
    knowledge_extractor_enabled,
    normalize_finalize_output,
    normalize_window_output,
    partition_finalization_candidates,
    run_knowledge_extractor_window,
    run_knowledge_finalization,
    validate_finalization_coverage,
)
from translator.review.prescan import deterministic_known_hit_scan
from translator.review.reviewer import (
    evaluate_apply_gate,
    finalize_writeback_states,
    has_hangul,
    has_japanese_kana,
    has_target_script_residue,
    compose_approved_fixes,
    missing_checked_ids,
    run_chapter_review,
    unique_writeback_fixes,
    validate_chapter_review_payload,
    review_report_counts,
)
from translator.script_residue import ScriptResidueFinding, inspect_target_script
from translator.translation_repairs import (
    REPAIR_RULE_VERSION,
    apply_deterministic_repairs,
)


ROOT = Path(__file__).resolve().parents[2]
ToolCall = Callable[..., dict[str, Any]]
Reviewer = Callable[..., Any]


def parse_args() -> argparse.Namespace:
    config = load_config()
    pipeline = config["pipeline"]
    paths = config["paths"]
    parser = argparse.ArgumentParser(description="Iterative EPUB chapter translation and review pipeline")
    parser.add_argument("--book", required=True, help="Novel Translator book id")
    parser.add_argument("--name", required=True, help="output/ 下的书籍目录名和中文书名")
    parser.add_argument("--output-root", type=Path, default=ROOT / paths["output_root"])
    parser.add_argument("--max-cycles", type=int, default=pipeline.get("max_cycles", 1000), help="最多处理多少章")
    parser.add_argument("--max-chapter-batches", type=int, default=pipeline.get("max_chapter_batches", 1000), help="单章最多推进多少个翻译 batch")
    parser.add_argument("--translation-policy", type=Path, default=ROOT / paths["translation_policy"])
    parser.add_argument("--primary-batch-max-chars", type=int, default=pipeline.get("primary_batch_max_chars", 4000), help="主译每个大窗口的原文字符上限")
    parser.add_argument("--max-provider-split-depth", type=int, default=pipeline.get("max_provider_split_depth", 2), help="遇到审查/格式错误时最多二分拆分的深度上限，达到后直接转入 fallback 备用翻译器（默认 2）")
    parser.add_argument(
        "--primary-translator", dest="primary_translator",
        default=primary_translator_name(config),
        help="primary_translator 使用的 provider",
    )
    parser.add_argument(
        "--fallback-translators", dest="fallback_translators", nargs="+",
        default=None,
        help="fallback_translators 备用翻译器列表（按先后顺序）",
    )
    parser.add_argument(
        "--fallback-translator", dest="fallback_translator",
        default=None,
        help="第一级备用翻译器",
    )
    parser.add_argument(
        "--secondary-fallback-translator", dest="secondary_fallback_translator",
        default=None,
        help="第二级备用翻译器",
    )
    parser.add_argument("--split-on-content-filter", action=argparse.BooleanOptionalAction, default=pipeline.get("split_on_content_filter", False), help="遇到审查/模型内部拒答时是否二分；默认 False（立即 fallback）")
    parser.add_argument("--translation-max-tokens", type=int, default=pipeline.get("translation_max_tokens", 8192), help="单个翻译窗口的最大输出 token")
    parser.add_argument("--apply", action="store_true", help="应用通过证据与写回策略校验的译文修复")
    parser.add_argument("--autonomous", action="store_true", help="全自动应用通过证据校验的有效修复")
    parser.add_argument("--finalize", action="store_true", help="全部翻译完成后导出并校验中文 EPUB")
    parser.add_argument("--layout", choices=["preserve", "horizontal"], default=pipeline.get("layout", "preserve"), help="导出 EPUB 的版式")
    parser.add_argument("--health-check-timeout", type=int, default=pipeline.get("health_check_timeout", 60), help="启动前健康检查超时秒数")
    parser.add_argument(
        "--reviewer", dest="reviewer",
        default=reviewer_name(config),
        help="审阅后端",
    )
    parser.add_argument(
        "--secondary-reviewer", dest="secondary_reviewer",
        default=secondary_reviewer_name(config),
        help="第二审阅后端（用于双模型独立审阅）",
    )
    parser.add_argument(
        "--dual-review", action=argparse.BooleanOptionalAction,
        default=dual_review_enabled(config),
        help="是否启用双模型独立全量审阅",
    )
    parser.add_argument(
        "--review-chunk-min-chars", type=int,
        default=pipeline.get("review_chunk_min_chars", 1000),
        help="审阅目标分块的原文最小字符数（只在自然段边界切分）",
    )
    parser.add_argument(
        "--review-chunk-max-chars", type=int,
        default=pipeline.get("review_chunk_max_chars", 1500),
        help="审阅目标分块的原文最大字符数（只在自然段边界切分）",
    )
    parser.add_argument(
        "--review-context-before", type=int,
        default=pipeline.get("review_context_before", 3),
        help="每个审阅分块附带的前文自然段数量",
    )
    parser.add_argument(
        "--review-context-after", type=int,
        default=pipeline.get("review_context_after", 3),
        help="每个审阅分块附带的后文自然段数量",
    )
    parser.add_argument(
        "--review-backtrack", action=argparse.BooleanOptionalAction,
        default=pipeline.get("review_backtrack_enabled", True),
        help="是否对前文 context 中被后文发现的问题执行定向复核",
    )
    parser.add_argument(
        "--review-backtrack-min-confidence", type=float,
        default=pipeline.get("review_backtrack_min_confidence", 0.8),
        help="触发前文定向复核的最低置信度",
    )
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


def failed_batch_count(payload: dict[str, Any]) -> int:
    summary = payload.get("summary", {}) if isinstance(payload, dict) else {}
    try:
        return max(0, int(summary.get("failed", 0) or 0))
    except (TypeError, ValueError):
        return 0


class IterativePipeline:
    def __init__(
        self,
        *,
        book: str,
        workspace: BookWorkspace,
        manifest: Path,
        tool_call: ToolCall = call_novel_translator,
        targeted_translator: Callable[..., dict[str, Any]] | None = None,
        chapter_reviewer: Reviewer | None = None,
        primary_batch_max_chars: int | None = None,
        primary_translator: str | None = None,
        max_provider_split_depth: int | None = None,
        split_on_content_filter: bool | None = None,
        fallback_translators: list[str] | None = None,
        fallback_translator: str | None = None,
        secondary_fallback_translator: str | None = None,
        translation_max_tokens: int | None = None,
        max_chapter_batches: int | None = None,
        translation_policy: Path | None = None,
        apply: bool = False,
        autonomous: bool = False,
        review_apply_mode: str | None = None,
        review_apply_minimum_confidence: float | None = None,
        reviewer: str | None = None,
        layout: str | None = None,
        translated_root: Path | None = None,
        on_batch_completed: Callable[[dict[str, Any]], None] | None = None,
        on_phase_changed: Callable[[dict[str, Any]], None] | None = None,
        on_reviewer_status: Callable[[dict[str, Any]], None] | None = None,
        on_translation_attempt: Callable[[dict[str, Any]], None] | None = None,
        on_fallback_triggered: Callable[[dict[str, Any]], None] | None = None,
        cancellation_token: CancellationToken | None = None,
        pause_gate: PauseGate | None = None,
        knowledge_extractor: Callable[..., Any] | None = None,
    ) -> None:
        self.book = book
        self.workspace = workspace
        self.manifest = manifest
        self.tool_call = tool_call
        self.targeted_translator: Callable[..., dict[str, Any]] | None
        if targeted_translator is not None:
            self.targeted_translator = targeted_translator
        elif tool_call is call_novel_translator:
            self.targeted_translator = ProviderTranslator(
                novel_root=NOVEL_TRANSLATOR_ROOT,
                manifest=manifest,
                glossary_path=workspace.glossary_path,
            )
        else:
            self.targeted_translator = None
        self.chapter_reviewer = chapter_reviewer or run_chapter_review

        config = load_config()
        pipeline_cfg = config.get("pipeline", {})

        eff_batch_max_chars = primary_batch_max_chars if primary_batch_max_chars is not None else int(pipeline_cfg.get("primary_batch_max_chars", 4000))
        if eff_batch_max_chars < 1:
            raise ValueError("primary_batch_max_chars 必须大于 0")
        self.primary_batch_max_chars = eff_batch_max_chars

        self.primary_translator = primary_translator or primary_translator_name(config)
        eff_split_depth = max_provider_split_depth if max_provider_split_depth is not None else int(pipeline_cfg.get("max_provider_split_depth", 2))
        self.max_provider_split_depth = max(0, eff_split_depth)
        self.split_on_content_filter = (
            split_on_content_filter
            if split_on_content_filter is not None
            else bool(pipeline_cfg.get("split_on_content_filter", False))
        )

        # Configure fallback chain
        if fallback_translators:
            self.fallback_translators = list(fallback_translators)
        elif fallback_translator:
            self.fallback_translators = [fallback_translator]
            if secondary_fallback_translator and secondary_fallback_translator not in self.fallback_translators:
                self.fallback_translators.append(secondary_fallback_translator)
        else:
            self.fallback_translators = fallback_translators_names(config)

        self.fallback_translator = self.fallback_translators[0] if self.fallback_translators else "lmstudio"
        self.secondary_fallback_translator = (
            secondary_fallback_translator
            or (self.fallback_translators[1] if len(self.fallback_translators) > 1 else None)
            or str(config.get("roles", {}).get("secondary_fallback_translator", "")).strip()
            or None
        )
        eff_max_tokens = translation_max_tokens if translation_max_tokens is not None else int(pipeline_cfg.get("translation_max_tokens", 8192))
        self.translation_max_tokens = max(512, eff_max_tokens)
        eff_max_batches = max_chapter_batches if max_chapter_batches is not None else int(pipeline_cfg.get("max_chapter_batches", 1000))
        self.max_chapter_batches = max(1, eff_max_batches)
        paths = PathResolver.for_config()
        self.translation_policy = translation_policy or paths.translation_policy(config)
        self.apply = apply
        review_apply_cfg = dict(pipeline_cfg.get("review_apply", {}) or {})
        self.review_apply_mode = str(review_apply_mode or review_apply_cfg.get("mode", "report_only"))
        # Retain the constructor keyword for callers that still pass it; the
        # value is intentionally ignored because confidence is not a writeback gate.
        self.review_apply_enabled = bool(apply and self.review_apply_mode == "hard_fix")
        self.autonomous = autonomous
        self.reviewer = reviewer or reviewer_name(config)
        self.layout = layout or str(pipeline_cfg.get("layout", "preserve"))
        self.translated_root = translated_root or paths.translated_root(config)
        self.on_batch_completed = on_batch_completed
        self.on_phase_changed = on_phase_changed
        self.on_reviewer_status = on_reviewer_status
        self.on_translation_attempt = on_translation_attempt
        self.on_fallback_triggered = on_fallback_triggered
        self.cancellation_token = cancellation_token or CancellationToken()
        self.pause_gate = pause_gate or PauseGate()
        self.knowledge_extractor = knowledge_extractor
        self._builtin_reviewer = chapter_reviewer is None or bool(
            getattr(chapter_reviewer, "_uses_window_knowledge", False)
        )
        self._prescan_reports: dict[str, dict[str, Any]] = {}
        self._knowledge_candidates: dict[str, list[dict[str, Any]]] = {}
        self._knowledge_conflicts: dict[str, list[dict[str, Any]]] = {}
        self._knowledge_windows: dict[str, list[dict[str, Any]]] = {}
        self._deferred_knowledge_windows: dict[str, list[tuple[dict[str, list[dict[str, Any]]], int, int]]] = {}
        self._translation_recovery: dict[str, dict[str, Any]] = {}
        self._repair_events: dict[str, list[dict[str, Any]]] = {}

    def _checkpoint(self) -> None:
        self.pause_gate.wait(self.cancellation_token)

    def initialize(self) -> None:
        self.workspace.initialize(book_id=self.book)

    def _chapter(self, chapter_id: str) -> dict[str, Any]:
        manifest = read_json(self.manifest)
        for chapter in manifest.get("chapters", []):
            if str(chapter.get("id", "")) == chapter_id:
                return chapter
        raise ValueError(f"章节未在 manifest 中找到：{chapter_id}")

    @staticmethod
    def _chapter_pending_ids(chapter: dict[str, Any]) -> set[str]:
        return {
            str(paragraph["id"])
            for paragraph in chapter.get("paragraphs", [])
            if isinstance(paragraph, dict) and paragraph.get("id") and IterativePipeline._paragraph_needs_translation(paragraph)
        }

    @staticmethod
    def _paragraph_needs_translation(paragraph: dict[str, Any]) -> bool:
        """Treat blank, source-copied, or script-residue output as unfinished."""
        source = str(paragraph.get("source", "")).strip()
        translated = str(paragraph.get("translated", "")).strip()
        if not translated:
            return True
        copied = bool(source) and (translated == source or translated.replace(" ", "") == source.replace(" ", ""))
        if copied:
            # Source-copied paragraphs stay pending when the source contains Japanese
            # or Korean script, even if that source discusses a quoted kana object.
            return has_japanese_kana(source) or has_hangul(source)
        if not has_target_script_residue(translated, source=source):
            return False
        # A previously written provider result may predate the deterministic
        # repair stage.  Treat a strictly source-triggered, fully repairable
        # idiom as recoverable; the chapter path persists the repaired value
        # before selecting its next translation batch.
        repaired, repairs = apply_deterministic_repairs(source=source, translated=translated)
        return not repairs or has_target_script_residue(repaired, source=source)

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

    def _record_translation_provenance(
        self,
        ids: list[str],
        provider: str,
        reason: str = "",
        *,
        attempt_id: str,
        fallback_from: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        path = self.workspace.data_dir / "translation-provenance.json"
        records = read_json(path, {"book": self.book, "items": {}})
        records.setdefault("book", self.book)
        items = records.setdefault("items", {})
        paragraphs = paragraph_map(read_json(self.manifest, {}))
        for item_id in ids:
            paragraph = paragraphs.get(item_id, {})
            source = str(paragraph.get("source", ""))
            translated = str(paragraph.get("translated", ""))
            record: dict[str, Any] = {
                "attempt_id": attempt_id,
                "provider": provider,
                "recovered_at": utc_now(),
                "fallback_from": fallback_from,
                "reason": reason,
                "source_hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                "translation_hash": hashlib.sha256(translated.encode("utf-8")).hexdigest(),
            }
            if metadata:
                for key in (
                    "failure_class",
                    "repair_rule_version",
                    "repair_rule_ids",
                    "repair_attempts",
                    "residue_tokens",
                ):
                    if key in metadata and metadata[key] not in (None, "", [], {}):
                        value = metadata[key]
                        if key == "repair_attempts" and isinstance(value, list):
                            value = [
                                item for item in value
                                if isinstance(item, Mapping) and str(item.get("id", "")) == item_id
                            ]
                        if key == "residue_tokens" and isinstance(value, Mapping):
                            value = value.get(item_id, [])
                        record[key] = value
            items[item_id] = record
        write_json(path, records)

    def _record_provider_attempt(self, attempt: dict[str, Any]) -> None:
        path = self.workspace.data_dir / "provider-diagnostics.json"
        diagnostics = read_json(path, {"book": self.book, "attempts": []})
        diagnostics.setdefault("book", self.book)
        diagnostics.setdefault("attempts", []).append(attempt)
        write_json(path, diagnostics)

    def _record_repair_events(
        self,
        chapter_id: str,
        repair_attempts: list[dict[str, Any]],
        *,
        phase: str,
    ) -> None:
        if not repair_attempts:
            return
        events = self._repair_events.setdefault(chapter_id, [])
        for repair in repair_attempts:
            if not isinstance(repair, Mapping):
                continue
            events.append({"chapter_id": chapter_id, "phase": phase, **dict(repair)})
        path = self.workspace.data_dir / "provider-diagnostics.json"
        diagnostics = read_json(path, {"book": self.book, "attempts": []})
        diagnostics.setdefault("book", self.book)
        diagnostics.setdefault("attempts", [])
        if not isinstance(diagnostics.get("repairs"), list):
            diagnostics["repairs"] = []
        diagnostics["repairs"].extend(
            {"chapter_id": chapter_id, "phase": phase, **dict(item)}
            for item in repair_attempts if isinstance(item, Mapping)
        )
        write_json(path, diagnostics)

    @staticmethod
    def _serialize_residue_finding(finding: ScriptResidueFinding) -> dict[str, Any]:
        return {
            "token": finding.token,
            "start": finding.start,
            "end": finding.end,
            "classification": finding.classification,
            "source_match": finding.source_match,
            "context_match": finding.context_match,
            "source_context_match": finding.source_context_match,
            "target_context_match": finding.target_context_match,
            "preserve_policy_enabled": finding.preserve_policy_enabled,
        }

    def _repair_translated_ids(self, ids: list[str]) -> dict[str, Any]:
        """Apply source-aware repairs to fresh manifest values and revalidate them."""
        diagnostics: dict[str, Any] = {
            "repair_rule_version": REPAIR_RULE_VERSION,
            "repaired_ids": [],
            "changed_ids": [],
            "repair_attempts": [],
            "repair_rule_ids": [],
            "residue_tokens": {},
            "findings": {},
            "source_copy_ids": [],
            "remaining": [],
            "errors": [],
        }
        if not ids:
            return diagnostics

        try:
            manifest_data = read_json(self.manifest, {})
            p_map = paragraph_map(manifest_data)
            changed = False
            for item_id in ids:
                paragraph = p_map.get(item_id)
                if paragraph is None:
                    continue
                source = str(paragraph.get("source", ""))
                before = str(paragraph.get("translated", ""))
                repaired, repairs = apply_deterministic_repairs(source=source, translated=before)
                if repaired != before:
                    paragraph["translated"] = repaired
                    changed = True
                    diagnostics["repaired_ids"].append(item_id)
                    diagnostics["changed_ids"].append(item_id)
                    before_hash = hashlib.sha256(before.encode("utf-8")).hexdigest()
                    after_hash = hashlib.sha256(repaired.encode("utf-8")).hexdigest()
                    for repair in repairs:
                        repair_data = {
                            "id": item_id,
                            "rule_id": repair.rule_id,
                            "source_match": repair.source_match,
                            "target_pattern": repair.target_pattern,
                            "replacement": repair.replacement,
                            "count": repair.count,
                            "target_match": repair.target_match,
                            "target_start": repair.target_start,
                            "target_end": repair.target_end,
                            "before_hash": before_hash,
                            "after_hash": after_hash,
                        }
                        diagnostics["repair_attempts"].append(repair_data)
                        if repair.rule_id not in diagnostics["repair_rule_ids"]:
                            diagnostics["repair_rule_ids"].append(repair.rule_id)
            if changed:
                write_json(self.manifest, manifest_data)

            # Re-open after write so validation observes exactly what the next
            # recovery path will observe, rather than a stale in-memory object.
            fresh_map = paragraph_map(read_json(self.manifest, {}))
            for item_id in ids:
                paragraph = fresh_map.get(item_id, {})
                source = str(paragraph.get("source", ""))
                translated = str(paragraph.get("translated", ""))
                findings = inspect_target_script(translated, source=source)
                diagnostics["findings"][item_id] = [
                    self._serialize_residue_finding(item) for item in findings
                ]
                residue = [
                    item.token for item in findings
                    if item.classification in {
                        "target_script_residue", "target_hangul", "source_copy", "ambiguous",
                    }
                ]
                if residue:
                    diagnostics["residue_tokens"][item_id] = residue
                if any(item.classification == "source_copy" for item in findings):
                    diagnostics["source_copy_ids"].append(item_id)
                if self._paragraph_needs_translation(paragraph):
                    diagnostics["remaining"].append(item_id)
        except Exception as exc:  # noqa: BLE001
            # A diagnostic/repair failure must leave provider recovery intact.
            diagnostics["errors"].append(str(exc)[:800])
            diagnostics["remaining"] = list(ids)
        return diagnostics

    def _translation_failure_class(
        self,
        *,
        attempted_ids: list[str],
        remaining: set[str],
        result: Mapping[str, Any] | None,
        repair_diagnostics: Mapping[str, Any] | None = None,
    ) -> str:
        """Classify provider failure separately from output residue."""
        remaining_ids = set(attempted_ids) & set(remaining)
        repair_data = repair_diagnostics if isinstance(repair_diagnostics, Mapping) else {}
        repaired_ids = set(str(item) for item in repair_data.get("repaired_ids", []) if item)
        if repaired_ids and not remaining_ids:
            return "deterministic_repair_recovered"
        provider_reason = provider_failure_reason(dict(result) if isinstance(result, Mapping) else None)
        # An explicit transport/provider failure keeps the existing split and
        # fallback routing semantics, even if a previous partial write happens
        # to leave script residue in the manifest.
        if remaining_ids and provider_reason not in {"ok", "unknown"}:
            return provider_reason
        if remaining_ids:
            source_copy_ids = set(str(item) for item in repair_data.get("source_copy_ids", []) if item)
            if source_copy_ids & remaining_ids:
                return "source_copy"
            residue_tokens = repair_data.get("residue_tokens", {})
            if isinstance(residue_tokens, Mapping) and any(
                str(item_id) in remaining_ids and value for item_id, value in residue_tokens.items()
            ):
                return "target_script_residue"
            return "incomplete_output"
        return provider_reason if provider_reason != "ok" else "provider_success"

    def _emit_translation_attempt(
        self,
        *,
        chapter_id: str,
        provider: str,
        attempt_id: str,
        attempted_ids: list[str],
        recovered_ids: list[str],
        result: Mapping[str, Any] | None,
        reason: str,
        depth: int,
        latency_ms: float,
        fallback_from: str | None = None,
        fallback_index: int | None = None,
        fallback_reason: str | None = None,
        failure_class: str | None = None,
        residue_tokens: list[str] | None = None,
        repair_rule_ids: list[str] | None = None,
        repair_attempts: list[dict[str, Any]] | None = None,
    ) -> None:
        """Notify the web layer without forwarding provider raw responses or prompts."""
        if self.on_translation_attempt is None:
            return
        result_data = result if isinstance(result, Mapping) else {}
        status = str(result_data.get("status", "error")).strip().casefold()
        payload: dict[str, Any] = {
            "book_id": self.book,
            "chapter_id": chapter_id,
            "provider": provider,
            "attempt_id": attempt_id,
            "attempted_ids": attempted_ids,
            "recovered_ids": recovered_ids,
            "failed_ids": [item_id for item_id in attempted_ids if item_id not in recovered_ids],
            "status": "ok" if recovered_ids else "failed",
            "provider_status": status,
            "reason": reason or "unknown",
            "latency_ms": latency_ms,
            "depth": depth,
            "is_fallback": fallback_from is not None,
        }
        if fallback_from is not None:
            payload["fallback_from"] = fallback_from
        if fallback_index is not None:
            payload["fallback_index"] = fallback_index
        if fallback_reason:
            payload["fallback_reason"] = fallback_reason
        if failure_class:
            payload["failure_class"] = failure_class
        if residue_tokens:
            payload["residue_tokens"] = list(dict.fromkeys(str(item) for item in residue_tokens))
        if repair_rule_ids:
            payload["repair_rule_ids"] = list(dict.fromkeys(str(item) for item in repair_rule_ids))
        if repair_attempts:
            payload["repair_attempts"] = repair_attempts
        for key in ("error", "http_status", "finish_reason", "format", "split"):
            value = result_data.get(key)
            if value not in (None, ""):
                payload[key] = str(value)[:800] if key == "error" else value
        try:
            self.on_translation_attempt(payload)
        except Exception:
            # Observability must never change translation behavior.
            pass

    def _emit_fallback_triggered(self, payload: dict[str, Any]) -> None:
        if self.on_fallback_triggered is None:
            return
        try:
            self.on_fallback_triggered(payload)
        except Exception:
            # Observability must never change translation behavior.
            pass

    def _chapter_pending_paragraphs(self, chapter_id: str) -> list[dict[str, Any]]:
        chapter = self._chapter(chapter_id)
        return [
            paragraph for paragraph in chapter.get("paragraphs", [])
            if isinstance(paragraph, dict) and paragraph.get("id") and self._paragraph_needs_translation(paragraph)
        ]

    def is_chapter_completed(self, chapter_id: str) -> bool:
        """Check if a chapter is completely translated and reviewed without residual errors."""
        pending = self._chapter_pending_paragraphs(chapter_id)
        if pending:
            return False
        # Japanese kana or Korean script in a Chinese translation keeps the chapter incomplete.
        chapter = self._chapter(chapter_id)
        if any(self._paragraph_needs_translation(p) for p in chapter.get("paragraphs", []) if isinstance(p, dict)):
            return False
        state_path = self.workspace.chapter_states_dir / f"{chapter_id}.json"
        report_path = self.workspace.reports_dir / f"{chapter_id}.json"
        if not (state_path.exists() or report_path.exists()):
            return False
        if report_path.exists():
            report_data = read_json(report_path, {})
            if report_data.get("remaining_kana_ids"):
                return False
        return True

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
        self._checkpoint()
        if self.targeted_translator is None:
            if provider != self.primary_translator:
                raise RuntimeError("测试/兼容模式未配置 fallback translator")
            result = self.tool_call(
                "translate",
                "--book", self.book,
                "--max-batches", "1",
                "--workers", "1",
                "--rpm", "30",
            )
            self._checkpoint()
            return result
        result = self.targeted_translator(
            provider,
            self.book,
            ids,
            source_chars=source_chars,
            max_tokens=self.translation_max_tokens,
        )
        self._checkpoint()
        return result

    def _record_injected_terms(self, chapter_id: str, result: Mapping[str, Any]) -> None:
        summary = result.get("summary", {}) if isinstance(result, Mapping) else {}
        count = int(summary.get("glossary_terms_injected", 0) or 0) if isinstance(summary, Mapping) else 0
        if count:
            report = self._prescan_reports.setdefault(chapter_id, {})
            report["injected_into_translation"] = int(report.get("injected_into_translation", 0) or 0) + count

    def _translate_segment_with_recovery(
        self,
        chapter_id: str,
        paragraphs: list[dict[str, Any]],
        attempts: list[dict[str, Any]],
        depth: int = 0,
    ) -> None:
        self._checkpoint()
        ids = [str(item["id"]) for item in paragraphs]
        source_by_id = {str(item["id"]): str(item.get("source", "")) for item in paragraphs}
        before_pending = {item_id for item_id in ids if item_id in self._chapter_pending_ids(self._chapter(chapter_id))}
        ids = [item_id for item_id in ids if item_id in before_pending]
        if not ids:
            return
        source_chars = sum(len(source_by_id[item_id]) for item_id in ids)
        attempt_id = uuid.uuid4().hex
        started = time.monotonic()
        try:
            primary_translator = self.primary_translator
            result = self._translate_target(primary_translator, ids, source_chars)
            self._record_injected_terms(chapter_id, result)
        except JobCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            result = {"status": "error", "error": str(exc)}
        repair_diagnostics = self._repair_translated_ids(ids)
        remaining = self._chapter_pending_ids(self._chapter(chapter_id))
        recovered_ids = [item_id for item_id in ids if item_id in before_pending and item_id not in remaining]
        failure_class = self._translation_failure_class(
            attempted_ids=ids,
            remaining=remaining,
            result=result,
            repair_diagnostics=repair_diagnostics,
        )
        provider_reason = provider_failure_reason(result)
        reason = (
            "deterministic_repair_recovered"
            if failure_class == "deterministic_repair_recovered"
            else failure_class
            if failure_class in {"target_script_residue", "source_copy", "incomplete_output"}
            else provider_reason
        )
        latency_ms = round((time.monotonic() - started) * 1000, 3)
        attempt: dict[str, Any] = {
            "attempt_id": attempt_id,
            "chapter_id": chapter_id,
            "provider": primary_translator,
            "depth": depth,
            "attempted_ids": ids,
            "recovered_ids": recovered_ids,
            "source_chars": source_chars,
            "status": "ok" if recovered_ids else "error",
            "latency_ms": latency_ms,
            "result": result,
            "reason": reason,
            "failure_class": failure_class,
            "repair_rule_version": repair_diagnostics.get("repair_rule_version", REPAIR_RULE_VERSION),
            "repair_attempts": repair_diagnostics.get("repair_attempts", []),
            "repair_rule_ids": repair_diagnostics.get("repair_rule_ids", []),
            "residue_tokens": repair_diagnostics.get("residue_tokens", {}),
            "remaining": sorted(remaining),
        }
        attempts.append(attempt)
        self._record_provider_attempt(attempt)
        self._emit_translation_attempt(
            chapter_id=chapter_id,
            provider=primary_translator,
            attempt_id=attempt_id,
            attempted_ids=ids,
            recovered_ids=recovered_ids,
            result=result,
            reason=reason,
            depth=depth,
            latency_ms=latency_ms,
            failure_class=failure_class,
            residue_tokens=[
                token
                for item_id, tokens in repair_diagnostics.get("residue_tokens", {}).items()
                if item_id in ids and isinstance(tokens, list)
                for token in tokens
            ],
            repair_rule_ids=repair_diagnostics.get("repair_rule_ids", []),
            repair_attempts=repair_diagnostics.get("repair_attempts", []),
        )
        if recovered_ids:
            self._record_translation_provenance(
                recovered_ids,
                primary_translator,
                reason,
                attempt_id=attempt_id,
                metadata={
                    "failure_class": failure_class,
                    "repair_rule_version": repair_diagnostics.get("repair_rule_version"),
                    "repair_rule_ids": repair_diagnostics.get("repair_rule_ids", []),
                    "repair_attempts": repair_diagnostics.get("repair_attempts", []),
                    "residue_tokens": repair_diagnostics.get("residue_tokens", {}),
                },
            )
        if not (set(ids) & remaining):
            return
        if self.targeted_translator is None:
            return
        should_split = (
            len(ids) > 1
            and depth < self.max_provider_split_depth
            and reason in {"content_filter", "output_format", "provider_blocked"}
            and (reason not in {"content_filter", "provider_blocked"} or self.split_on_content_filter)
        )
        if should_split:
            midpoint = max(1, len(ids) // 2)
            left = [item for item in paragraphs[:midpoint] if str(item["id"]) in remaining]
            right = [item for item in paragraphs[midpoint:] if str(item["id"]) in remaining]
            if left:
                self._translate_segment_with_recovery(chapter_id, left, attempts, depth + 1)
            if right:
                self._translate_segment_with_recovery(chapter_id, right, attempts, depth + 1)
            return

        fallback_ids = sorted(set(ids) & remaining, key=ids.index)
        route_from = self.primary_translator
        route_reason = reason
        for fb_idx, fb_provider in enumerate(self.fallback_translators):
            self._checkpoint()
            current_pending = self._chapter_pending_ids(self._chapter(chapter_id))
            attempted_ids = [item_id for item_id in fallback_ids if item_id in current_pending]
            if not attempted_ids:
                break
            fb_source_chars = sum(len(source_by_id[item_id]) for item_id in attempted_ids)
            fb_attempt_id = uuid.uuid4().hex
            fb_started = time.monotonic()
            self._emit_fallback_triggered({
                "book_id": self.book,
                "chapter_id": chapter_id,
                "from_provider": route_from,
                "to_provider": fb_provider,
                "reason": route_reason or "provider_error",
                "paragraph_ids": attempted_ids,
                "depth": depth,
                "fallback_index": fb_idx + 1,
                "attempt_id": fb_attempt_id,
            })
            try:
                fb_result = self._translate_target(fb_provider, attempted_ids, fb_source_chars)
                self._record_injected_terms(chapter_id, fb_result)
            except JobCancelled:
                raise
            except Exception as exc:  # noqa: BLE001
                fb_result = {"status": "error", "error": str(exc)}
            fb_repair_diagnostics = self._repair_translated_ids(attempted_ids)
            self._checkpoint()
            fb_remaining = self._chapter_pending_ids(self._chapter(chapter_id))
            fb_recovered = [item_id for item_id in attempted_ids if item_id not in fb_remaining]
            fb_failure_class = self._translation_failure_class(
                attempted_ids=attempted_ids,
                remaining=fb_remaining,
                result=fb_result,
                repair_diagnostics=fb_repair_diagnostics,
            )
            fb_provider_reason = provider_failure_reason(fb_result)
            fb_reason = (
                "deterministic_repair_recovered"
                if fb_failure_class == "deterministic_repair_recovered"
                else f"{self.primary_translator}_{reason}_fb{fb_idx+1}"
            )
            fb_latency_ms = round((time.monotonic() - fb_started) * 1000, 3)
            fb_attempt: dict[str, Any] = {
                "attempt_id": fb_attempt_id,
                "chapter_id": chapter_id,
                "provider": fb_provider,
                "depth": depth,
                "attempted_ids": attempted_ids,
                "recovered_ids": fb_recovered,
                "source_chars": fb_source_chars,
                "status": "ok" if fb_recovered else "error",
                "latency_ms": fb_latency_ms,
                "result": fb_result,
                "reason": fb_reason,
                "failure_class": fb_failure_class,
                "repair_rule_version": fb_repair_diagnostics.get("repair_rule_version", REPAIR_RULE_VERSION),
                "repair_attempts": fb_repair_diagnostics.get("repair_attempts", []),
                "repair_rule_ids": fb_repair_diagnostics.get("repair_rule_ids", []),
                "residue_tokens": fb_repair_diagnostics.get("residue_tokens", {}),
                "remaining": sorted(fb_remaining),
            }
            attempts.append(fb_attempt)
            self._record_provider_attempt(fb_attempt)
            self._emit_translation_attempt(
                chapter_id=chapter_id,
                provider=fb_provider,
                attempt_id=fb_attempt_id,
                attempted_ids=attempted_ids,
                recovered_ids=fb_recovered,
                result=fb_result,
                reason=fb_reason,
                depth=depth,
                latency_ms=fb_latency_ms,
                fallback_from=route_from,
                fallback_index=fb_idx + 1,
                fallback_reason=fb_reason,
                failure_class=fb_failure_class,
                residue_tokens=[
                    token
                    for item_id, tokens in fb_repair_diagnostics.get("residue_tokens", {}).items()
                    if item_id in attempted_ids and isinstance(tokens, list)
                    for token in tokens
                ],
                repair_rule_ids=fb_repair_diagnostics.get("repair_rule_ids", []),
                repair_attempts=fb_repair_diagnostics.get("repair_attempts", []),
            )
            if fb_recovered:
                self._record_translation_provenance(
                    fb_recovered,
                    fb_provider,
                    fb_reason,
                    attempt_id=fb_attempt_id,
                    fallback_from=self.primary_translator,
                    metadata={
                        "failure_class": fb_failure_class,
                        "repair_rule_version": fb_repair_diagnostics.get("repair_rule_version"),
                        "repair_rule_ids": fb_repair_diagnostics.get("repair_rule_ids", []),
                        "repair_attempts": fb_repair_diagnostics.get("repair_attempts", []),
                        "residue_tokens": fb_repair_diagnostics.get("residue_tokens", {}),
                    },
                )
            if not (set(ids) & fb_remaining):
                break
            route_from = fb_provider
            route_reason = (
                fb_failure_class
                if fb_failure_class not in {"provider_success", "deterministic_repair_recovered"}
                else fb_provider_reason or fb_reason
            )

        unresolved_now = set(ids) & self._chapter_pending_ids(self._chapter(chapter_id))
        if unresolved_now and len(fallback_ids) > 1:
            for item in paragraphs:
                if str(item["id"]) in {str(p["id"]) for p in self._chapter_pending_paragraphs(chapter_id)}:
                    self._translate_segment_with_recovery(chapter_id, [item], attempts, depth + 1)
            remaining_after = {str(item["id"]) for item in self._chapter_pending_paragraphs(chapter_id)}
            if not (set(ids) & remaining_after):
                return

        unresolved = sorted(set(ids) & self._chapter_pending_ids(self._chapter(chapter_id)))
        if unresolved:
            summary = [
                {
                    "provider": str(item.get("provider", "")),
                    "failure_class": str(item.get("failure_class", item.get("reason", "unknown"))),
                    "residue_tokens": {
                        str(item_id): tokens
                        for item_id, tokens in (item.get("residue_tokens", {}) or {}).items()
                        if str(item_id) in unresolved and tokens
                    },
                }
                for item in attempts
                if set(str(value) for value in item.get("attempted_ids", [])) & set(unresolved)
            ]
            raise RuntimeError(
                f"所有 fallback ({', '.join(self.fallback_translators)}) 均未完成章节 {chapter_id} 段落："
                f"{', '.join(unresolved)}；恢复摘要：{json.dumps(summary, ensure_ascii=False, separators=(',', ':'))}"
            )
        return

    def _prescan_chapter(self, chapter_id: str) -> dict[str, Any]:
        """Find existing glossary hits after translation; this operation is read-only."""
        self._checkpoint()
        chapter = self._chapter(chapter_id)
        items = [
            {"id": str(item["id"]), "source": str(item.get("source", "")), "translated": str(item.get("translated", ""))}
            for item in chapter.get("paragraphs", [])
            if isinstance(item, dict) and item.get("id")
        ]
        glossary = read_json(self.workspace.glossary_path, {"terms": []})
        report = deterministic_known_hit_scan(items, glossary, chapter_id=chapter_id)
        report.update({"status": "completed", "deterministic": True})
        write_json(self.workspace.reviews_dir / f"{chapter_id}-known-hits.json", report)
        previous_report = self._prescan_reports.get(chapter_id, {})
        self._prescan_reports[chapter_id] = {
            "extraction_status": "deterministic",
            "known_hit_count": report["hit_count"],
            "known_term_count": report["term_count"],
            "diagnostic": "",
        }
        if previous_report.get("injected_into_translation"):
            self._prescan_reports[chapter_id]["injected_into_translation"] = previous_report["injected_into_translation"]
        return report

    def _translate_chapter(self, chapter_id: str, _cycle: int) -> dict[str, Any]:
        chapter = self._chapter(chapter_id)
        # The normal pipeline translates the complete chapter before the
        # deterministic known-hit scan. Knowledge extraction starts only in
        # the review phase and never participates in translation.
        before_path = self.workspace.snapshots_dir / f"{chapter_id}-before.json"
        if not before_path.exists():
            try:
                snapshot = self.tool_call("snapshot", "--book", self.book, "--name", f"before-{chapter_id}")
            except Exception:
                snapshot = read_json(self.manifest, {})
            write_json(before_path, snapshot)
        # Resume-safe migration for already written provider output.  Only
        # source-triggered rules can change a value and the operation is
        # idempotent, so completed paragraphs are byte-stable after the first
        # pass.
        resume_repairs = self._repair_translated_ids([
            str(item.get("id"))
            for item in chapter.get("paragraphs", [])
            if isinstance(item, dict) and item.get("id")
        ])
        self._record_repair_events(chapter_id, resume_repairs.get("repair_attempts", []), phase="translation_resume")
        attempts: list[dict[str, Any]] = []
        initial_pending = self._chapter_pending_ids(chapter)
        batches = 0
        while batches < self.max_chapter_batches:
            self._checkpoint()
            pending_paragraphs = self._chapter_pending_paragraphs(chapter_id)
            if not pending_paragraphs:
                break
            batch_window = self._window(pending_paragraphs, self.primary_batch_max_chars)
            self._translate_segment_with_recovery(chapter_id, batch_window, attempts)
            self._checkpoint()
            batches += 1
            if self.on_batch_completed is not None:
                remaining_after = self._chapter_pending_paragraphs(chapter_id)
                try:
                    self.on_batch_completed({
                        "book": self.book,
                        "chapter_id": chapter_id,
                        "batch_index": batches,
                        "batch_paragraphs": len(batch_window),
                        "remaining_pending": len(remaining_after),
                    })
                except Exception:
                    pass

        untranslated = self._chapter_pending_ids(self._chapter(chapter_id))
        failure_class_counts: dict[str, int] = {}
        recovered_from_repairs: list[str] = []
        repair_rule_ids: list[str] = []
        residue_ids: list[str] = []
        for attempt in attempts:
            failure_class = str(attempt.get("failure_class", "") or "")
            if failure_class:
                failure_class_counts[failure_class] = failure_class_counts.get(failure_class, 0) + 1
            if failure_class == "deterministic_repair_recovered":
                recovered_from_repairs.extend(str(item) for item in attempt.get("recovered_ids", []) if item)
            repair_rule_ids.extend(str(item) for item in attempt.get("repair_rule_ids", []) if item)
            if failure_class == "target_script_residue":
                residue_ids.extend(str(item) for item in attempt.get("attempted_ids", []) if item)
        self._translation_recovery[chapter_id] = {
            "attempt_count": len(attempts),
            "recovered_ids": sorted({str(item) for attempt in attempts for item in attempt.get("recovered_ids", []) if item}),
            "deterministic_repair_recovered_ids": sorted(set(recovered_from_repairs)),
            "repair_rule_version": REPAIR_RULE_VERSION,
            "repair_rule_ids": sorted(set(repair_rule_ids)),
            "failure_class_counts": failure_class_counts,
            "target_script_residue_ids": sorted(set(residue_ids)),
            "remaining_ids": sorted(untranslated),
        }
        if untranslated:
            raise RuntimeError(f"章节 {chapter_id} 在 {batches} 个批次后仍有未翻译段落：{', '.join(sorted(untranslated))}")
        after_path = self.workspace.snapshots_dir / f"{chapter_id}-after.json"
        try:
            snapshot = self.tool_call("snapshot", "--book", self.book, "--name", f"after-{chapter_id}")
        except Exception:
            snapshot = read_json(self.manifest, {})
        write_json(after_path, snapshot)
        return {
            "chapter_id": chapter_id,
            "batches": batches,
            "translated": len(initial_pending),
            "translated_paragraphs": len(initial_pending),
            "attempts": len(attempts),
            "recovery_summary": self._translation_recovery.get(chapter_id, {}),
        }

    def _repair_remaining_kana(self, chapter_id: str, remaining_kana_ids: list[str]) -> list[str]:
        """Repair paragraphs where Japanese or Korean script remained after review writeback."""
        repaired: list[str] = []
        if not remaining_kana_ids:
            return repaired

        # The same registry is used during translation recovery and review
        # writeback.  This keeps the shape rules source-aware and idempotent.
        repair_diagnostics = self._repair_translated_ids(remaining_kana_ids)
        self._record_repair_events(chapter_id, repair_diagnostics.get("repair_attempts", []), phase="review_writeback")
        remaining_after_shapes = [
            item_id for item_id in remaining_kana_ids
            if item_id in set(repair_diagnostics.get("remaining", []))
        ]
        repaired.extend(
            item_id for item_id in remaining_kana_ids
            if item_id not in remaining_after_shapes
            and item_id in set(repair_diagnostics.get("repaired_ids", []))
        )

        if not remaining_after_shapes or self.targeted_translator is None:
            return repaired

        # 2. Targeted re-translation for any remaining paragraphs
        providers_to_try = [self.primary_translator] + [p for p in self.fallback_translators if p != self.primary_translator]
        p_map = paragraph_map(read_json(self.manifest, {}))
        for item_id in remaining_after_shapes:
            p_data = p_map.get(item_id, {})
            source_text = str(p_data.get("source", ""))
            source_chars = len(source_text)
            if not source_text:
                continue
            for provider in providers_to_try:
                try:
                    result = self._translate_target(provider, [item_id], source_chars)
                    if result.get("status") == "ok":
                        fresh_manifest = read_json(self.manifest)
                        fresh_p_map = paragraph_map(fresh_manifest)
                        new_trans = str(fresh_p_map.get(item_id, {}).get("translated", ""))
                        if new_trans and not self._paragraph_needs_translation(fresh_p_map.get(item_id, {})):
                            repaired.append(item_id)
                            break
                except Exception:
                    continue
        return repaired

    def _knowledge_config(self) -> dict[str, Any]:
        return dict(load_config().get("knowledge_extractor", {}) or {})

    @staticmethod
    def _project_window_fixes(window: dict[str, list[dict[str, Any]]], review: dict[str, Any], *, apply: bool) -> dict[str, list[dict[str, Any]]]:
        projected = {key: [dict(item) for item in values] for key, values in window.items()}
        if not apply:
            return projected
        by_id = {str(item.get("id", "")): item for item in projected.get("items", []) if isinstance(item, dict)}
        for fix in review.get("fixes", []) if isinstance(review.get("fixes"), list) else []:
            if not isinstance(fix, dict):
                continue
            if fix.get("apply_state") != "applied":
                continue
            item = by_id.get(str(fix.get("id", "")))
            if item is None:
                continue
            if str(fix.get("operation", "replace")) == "clear":
                item["translated"] = ""
            elif fix.get("replacement") or fix.get("approved_translation"):
                item["translated"] = str(fix.get("replacement") or fix.get("approved_translation"))
        return projected

    def _extract_window_knowledge(
        self,
        chapter_id: str,
        window: dict[str, list[dict[str, Any]]],
        review: dict[str, Any],
        window_index: int,
        total_windows: int,
    ) -> dict[str, Any]:
        """Extract one temporary window result without touching formal stores."""
        # The window starts from manifest-backed accepted text. Only fixes whose
        # write and exact verification completed may override it.
        manifest_paragraphs = paragraph_map(read_json(self.manifest))
        accepted_window = {key: [dict(item) for item in values] for key, values in window.items()}
        for values in accepted_window.values():
            for item in values:
                accepted = manifest_paragraphs.get(str(item.get("id", "")))
                if accepted is not None:
                    item["translated"] = str(accepted.get("translated", ""))
        projected = self._project_window_fixes(accepted_window, review, apply=self.review_apply_enabled)
        window_id = f"{chapter_id}:window:{window_index:04d}"
        rolling_context: dict[str, list[str]] = {}
        for previous_window in self._knowledge_windows.get(chapter_id, []):
            delta = previous_window.get("rolling_context_delta", {}) if isinstance(previous_window, dict) else {}
            if not isinstance(delta, dict):
                continue
            for key in ("adopted_terms", "active_entities", "locations", "relationships", "important_states", "notes"):
                values = delta.get(key, [])
                if isinstance(values, list):
                    rolling_context[key] = list(dict.fromkeys(rolling_context.get(key, []) + [str(value) for value in values]))
        payload = {
            "schema_version": "1.0",
            "window_id": window_id,
            "chapter_id": chapter_id,
            "window_index": window_index,
            "total_windows": total_windows,
            "items": projected.get("items", []),
            "context_before": projected.get("context_before", []),
            "context_after": projected.get("context_after", []),
            "current_chapter_review_context": rolling_context,
        }
        output_path = self.workspace.reviews_dir / f"{chapter_id}-window-{window_index:04d}-knowledge.json"
        try:
            if self.knowledge_extractor is not None:
                try:
                    result = self.knowledge_extractor("window", payload)
                except TypeError:
                    result = self.knowledge_extractor(chapter_id, projected.get("items", []), payload)
                if not isinstance(result, dict):
                    result = {}
                result = {
                    **normalize_window_output(
                        result,
                        window_id=window_id,
                        items=[item for item in projected.get("items", []) if isinstance(item, dict)],
                    ),
                    "status": "completed",
                }
                write_json(output_path, result)
            else:
                result = run_knowledge_extractor_window(payload, output_path=output_path)
        except JobCancelled:
            raise
        except Exception as exc:
            result = {"schema_version": "1.0", "status": "failed", "error": str(exc), "rolling_context_delta": {}, "knowledge_candidates": [], "conflicts": []}
            write_json(output_path, result)
        result.setdefault("knowledge_candidates", [])
        result.setdefault("conflicts", [])
        for candidate in result["knowledge_candidates"] if isinstance(result["knowledge_candidates"], list) else []:
            if isinstance(candidate, dict):
                candidate.setdefault("source_window", window_id)
        for conflict in result["conflicts"] if isinstance(result["conflicts"], list) else []:
            if isinstance(conflict, dict):
                conflict.setdefault("source_window", window_id)
        self._knowledge_candidates.setdefault(chapter_id, []).extend(
            item for item in result["knowledge_candidates"] if isinstance(item, dict)
        )
        self._knowledge_conflicts.setdefault(chapter_id, []).extend(
            item for item in result["conflicts"] if isinstance(item, dict)
        )
        self._knowledge_windows.setdefault(chapter_id, []).append(result)
        return result

    def _finalize_chapter_knowledge(self, chapter_id: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        candidates = self._knowledge_candidates.get(chapter_id, [])
        conflicts = self._knowledge_conflicts.get(chapter_id, [])
        config = self._knowledge_config()
        if self.knowledge_extractor is None and not knowledge_extractor_enabled(config):
            return {"status": "skipped", "reason": "disabled", "candidates": len(candidates), "active": 0}

        final_candidates: list[dict[str, Any]] = []

        def retain_without_promotion(status: str, *, error: str = "") -> dict[str, Any]:
            """Keep extraction evidence auditable when finalization is unavailable."""
            evidence = {str(item.get("id", "")): str(item.get("source", "")) for item in items if item.get("id")}
            try:
                retained = apply_knowledge_delta(
                    self.workspace, chapter_id, final_candidates or candidates, {}, conflicts,
                    evidence_texts=evidence,
                )
            except Exception as commit_exc:
                return {
                    "status": "commit_failed",
                    "error": str(commit_exc),
                    "finalization_error": error,
                    "candidates": len(candidates),
                    "active": 0,
                }
            return {
                "status": status,
                "error": error,
                "candidates": len(candidates),
                "active": 0,
                **retained,
            }

        try:
            glossary = read_json(self.workspace.glossary_path, {"terms": []})
            memory = read_json(self.workspace.book_memory_path, empty_book_memory(self.book))
            candidate_store = read_json(self.workspace.knowledge_candidates_path, {"items": []})
            hard_limit = int(config.get("input_hard_limit_chars", 30_000) or 30_000)
            historical = candidate_store.get("items", []) if isinstance(candidate_store, dict) else []
            final_candidates = aggregate_candidates(candidates, historical_candidates=historical)
            deterministic_decisions, model_candidates = partition_finalization_candidates(
                final_candidates, conflicts, glossary, memory,
            )
        except JobCancelled:
            raise
        except Exception as exc:
            return retain_without_promotion("failed", error=str(exc))

        input_path = self.workspace.reviews_dir / f"{chapter_id}-knowledge-finalize-input.json"
        output_path = self.workspace.reviews_dir / f"{chapter_id}-knowledge-finalize.json"
        batch_size = max(1, int(config.get("finalization_batch_size", 12) or 12))
        max_retries = max(0, int(config.get("finalization_max_retries", 2) or 0))

        def build_bounded_payload(batch: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, str]]:
            rich_payload = build_finalization_payload(
                batch, conflicts, glossary, memory,
                candidate_store=None,
                max_chars=1_000_000_000,
            )
            rich_payload.update({
                "chapter_id": chapter_id,
                "candidate_count": len(batch),
                "conflict_count": len(conflicts),
            })
            payload, id_map = compact_finalization_payload(rich_payload)
            if len(id_map) == len(batch) and finalization_prompt_chars(payload) <= hard_limit:
                return payload, id_map
            raise ValueError(f"Knowledge Finalization batch exceeds input_hard_limit_chars={hard_limit}")

        prepared_batches: list[list[dict[str, Any]]] = []

        def prepare(batch: list[dict[str, Any]]) -> None:
            if not batch:
                return
            try:
                build_bounded_payload(batch)
                prepared_batches.append(batch)
            except ValueError:
                if len(batch) == 1:
                    raise
                midpoint = len(batch) // 2
                prepare(batch[:midpoint])
                prepare(batch[midpoint:])

        try:
            for start in range(0, len(model_candidates), batch_size):
                prepare(model_candidates[start:start + batch_size])
        except Exception as exc:
            return retain_without_promotion("failed", error=str(exc))

        write_json(input_path, {
            "schema_version": "1.0",
            "chapter_id": chapter_id,
            "candidate_count": len(final_candidates),
            "raw_candidate_count": len(candidates),
            "deterministic_decision_count": len(deterministic_decisions),
            "model_candidate_count": len(model_candidates),
            "batch_size": batch_size,
            "batches": [
                [str(item.get("candidate_id", "")) for item in batch] for batch in prepared_batches
            ],
        })

        decisions: list[dict[str, Any]] = list(deterministic_decisions)
        missing_ids: list[str] = []
        validation_errors: list[dict[str, Any]] = []
        provider_attempts: list[dict[str, Any]] = []
        total_attempts = 0
        for batch_index, batch in enumerate(prepared_batches, start=1):
            pending = list(batch)
            accepted_for_batch: dict[str, dict[str, Any]] = {}
            for retry_index in range(max_retries + 1):
                if not pending:
                    break
                total_attempts += 1
                final_input, short_id_map = build_bounded_payload(pending)
                attempt_stem = f"{chapter_id}-knowledge-finalize-batch-{batch_index:04d}-attempt-{retry_index + 1:02d}"
                attempt_input_path = self.workspace.reviews_dir / f"{attempt_stem}-input.json"
                attempt_output_path = self.workspace.reviews_dir / f"{attempt_stem}.json"
                write_json(attempt_input_path, final_input)
                try:
                    if self.knowledge_extractor is not None:
                        try:
                            finalized = self.knowledge_extractor("finalize", final_input)
                        except TypeError:
                            finalized = self.knowledge_extractor(final_input)
                        finalized = normalize_finalize_output(finalized if isinstance(finalized, dict) else {})
                        finalized["status"] = "completed"
                        write_json(attempt_output_path, finalized)
                    else:
                        finalized = run_knowledge_finalization(final_input, output_path=attempt_output_path)
                except JobCancelled:
                    raise
                except Exception as exc:
                    validation_errors.append({
                        "batch": batch_index, "attempt": retry_index + 1, "error": str(exc),
                    })
                    continue

                if isinstance(finalized, dict) and finalized.get("provider"):
                    provider_attempts.append({
                        "batch": batch_index,
                        "attempt": retry_index + 1,
                        "provider": finalized.get("provider"),
                        "is_fallback": bool(finalized.get("is_fallback", False)),
                        "fallback_from": finalized.get("fallback_from"),
                        "fallback_index": finalized.get("fallback_index"),
                        "attempts": finalized.get("provider_attempts", []),
                    })

                coverage = validate_finalization_coverage(
                    pending,
                    [
                        {
                            **dict(item),
                            "candidate_id": short_id_map.get(
                                str(item.get("candidate_id", "")), str(item.get("candidate_id", ""))
                            ),
                        }
                        for item in (finalized.get("decisions", []) if isinstance(finalized, dict) else [])
                        if isinstance(item, dict)
                    ],
                )
                unknown_ids = coverage["unknown_candidate_ids"]
                duplicate_ids = coverage["duplicate_candidate_ids"]
                if unknown_ids or duplicate_ids:
                    validation_errors.append({
                        "batch": batch_index,
                        "attempt": retry_index + 1,
                        "unknown_candidate_ids": unknown_ids,
                        "duplicate_candidate_ids": duplicate_ids,
                    })
                if unknown_ids:
                    # A hallucinated ID makes the whole response untrustworthy.
                    continue
                for item in coverage["decisions"]:
                    accepted_for_batch[str(item["candidate_id"])] = item
                pending_ids = set(coverage["missing_candidate_ids"])
                pending = [item for item in pending if str(item.get("candidate_id", "")) in pending_ids]

            decisions.extend(accepted_for_batch.values())
            missing_ids.extend(str(item.get("candidate_id", "")) for item in pending)

        status = "completed" if not missing_ids else "incomplete"
        finalized = {
            "schema_version": "1.0",
            "status": status,
            "decisions": decisions,
            "candidate_count": len(final_candidates),
            "decision_count": len(decisions),
            "deterministic_decision_count": len(deterministic_decisions),
            "model_candidate_count": len(model_candidates),
            "missing_decision_ids": list(dict.fromkeys(missing_ids)),
            "batch_count": len(prepared_batches),
            "attempt_count": total_attempts,
            "provider_attempts": provider_attempts,
            "validation_errors": validation_errors,
        }
        write_json(output_path, finalized)
        decision_map = {
            str(item.get("candidate_id", "")): item
            for item in decisions if isinstance(item, dict) and item.get("candidate_id")
        }
        manifest_doc = read_json(self.manifest, default={})
        all_paragraphs = paragraph_map(manifest_doc)
        evidence = {str(p_id): str(p.get("source", "")) for p_id, p in all_paragraphs.items()} if all_paragraphs else {
            str(item.get("id", "")): str(item.get("source", "")) for item in items if item.get("id")
        }
        try:
            applied = apply_knowledge_delta(
                self.workspace, chapter_id, final_candidates, decision_map, conflicts,
                evidence_texts=evidence,
            )
            self._save_chapter_state(chapter_id)
        except Exception as exc:
            # Keep review/translation valid even when the final persistence
            # transaction fails; active stores are rolled back by the entry point.
            return {"status": "commit_failed", "error": str(exc), "candidates": len(candidates), "active": 0}
        return {
            "status": finalized.get("status", "completed"),
            "candidates": len(candidates),
            "aggregated_candidates": len(final_candidates),
            "deterministic_decisions": len(deterministic_decisions),
            "model_candidates": len(model_candidates),
            "conflicts": len(conflicts),
            "decisions": len(decision_map),
            "missing_decisions": len(finalized.get("missing_decision_ids", [])),
            "batch_count": len(prepared_batches),
            "attempt_count": total_attempts,
            "provider_attempts": provider_attempts,
            **applied,
        }

    def _save_chapter_state(self, chapter_id: str) -> None:
        """Aggregate window rolling context into persistent chapter state summary."""
        windows = self._knowledge_windows.get(chapter_id, [])
        all_entities: list[str] = []
        all_locations: list[str] = []
        all_relationships: list[str] = []
        all_states: list[str] = []
        all_notes: list[str] = []
        for win in windows:
            delta = win.get("rolling_context_delta", {}) if isinstance(win, dict) else {}
            if not isinstance(delta, dict):
                continue
            for e in delta.get("active_entities", []):
                if e and str(e) not in all_entities:
                    all_entities.append(str(e))
            for loc in delta.get("locations", []):
                if loc and str(loc) not in all_locations:
                    all_locations.append(str(loc))
            for rel in delta.get("relationships", []):
                if rel and str(rel) not in all_relationships:
                    all_relationships.append(str(rel))
            for st in delta.get("important_states", []):
                if st and str(st) not in all_states:
                    all_states.append(str(st))
            for nt in delta.get("notes", []):
                if nt and str(nt) not in all_notes:
                    all_notes.append(str(nt))
        summary_lines = all_states if all_states else all_notes
        summary_text = "；".join(summary_lines) if summary_lines else ""
        state_doc = {
            "chapter_id": chapter_id,
            "summary": summary_text,
            "characters": all_entities,
            "locations": all_locations,
            "relationships": all_relationships,
            "notes": all_notes,
            "updated_at": utc_now(),
        }
        self.workspace.chapter_states_dir.mkdir(parents=True, exist_ok=True)
        write_json(self.workspace.chapter_states_dir / f"{chapter_id}.json", state_doc)

    def _review_chapter(self, chapter_id: str) -> dict[str, Any]:
        self._checkpoint()
        self._knowledge_candidates[chapter_id] = []
        self._knowledge_conflicts[chapter_id] = []
        self._knowledge_windows[chapter_id] = []
        chapter = self._chapter(chapter_id)
        paragraphs = [p for p in chapter.get("paragraphs", []) if isinstance(p, dict) and p.get("id")]
        items = [{"id": str(p["id"]), "source": str(p.get("source", "")), "translated": str(p.get("translated", ""))} for p in paragraphs if str(p.get("translated", "")).strip()]
        glossary = read_json(self.workspace.glossary_path, {"book": self.book, "terms": [], "conflicts": []})
        memory = read_json(self.workspace.book_memory_path, empty_book_memory(self.book))
        known_hits_doc = read_json(self.workspace.reviews_dir / f"{chapter_id}-known-hits.json", {})
        input_path = self.workspace.reviews_dir / f"{chapter_id}-input.json"
        output_path = self.workspace.reviews_dir / f"{chapter_id}-output.json"
        write_json(input_path, {
            "book": self.book,
            "chapter_id": chapter_id,
            "chapter_title": str(chapter.get("title", "")),
            "translation_policy": self._read_translation_policy(),
            "book_memory": memory,
            "previous_chapter_state": self._previous_chapter_state(chapter_id),
            "items": items,
            "glossary": glossary,
            "known_hits": known_hits_doc.get("known_hits", []) if isinstance(known_hits_doc, dict) else [],
            "current_chapter_review_context": {},
        })
        window_callback = None
        if self._builtin_reviewer:
            def window_callback(review_result: dict[str, Any], window: dict[str, list[dict[str, Any]]], index: int, total: int) -> dict[str, Any]:
                if self.review_apply_enabled:
                    self._deferred_knowledge_windows.setdefault(chapter_id, []).append((window, index, total))
                    return {}
                return self._extract_window_knowledge(chapter_id, window, review_result, index, total)
            if self.chapter_reviewer is run_chapter_review:
                run_chapter_review(
                    input_path,
                    output_path,
                    autonomous=self.autonomous,
                    backend=self.reviewer,
                    on_reviewer_status=self.on_reviewer_status,
                    cancel_check=self.cancellation_token.check,
                    on_window_completed=window_callback,
                )
            else:
                self.chapter_reviewer(input_path, output_path, on_window_completed=window_callback)
        else:
            self.chapter_reviewer(input_path, output_path)
        self._checkpoint()
        review = read_json(output_path)
        if not isinstance(review, dict):
            raise ValueError(f"章节审阅结果不是 JSON 对象：{output_path}")
        expected_ids = {str(item["id"]) for item in items}
        for retry in range(1, 3):
            if not missing_checked_ids(review, expected_ids):
                break
            retry_path = self.workspace.reviews_dir / f"{chapter_id}-retry-{retry:02d}.json"
            if self._builtin_reviewer:
                if self.chapter_reviewer is run_chapter_review:
                    run_chapter_review(
                        input_path,
                        retry_path,
                        autonomous=self.autonomous,
                        backend=self.reviewer,
                        on_reviewer_status=self.on_reviewer_status,
                        cancel_check=self.cancellation_token.check,
                    )
                else:
                    self.chapter_reviewer(input_path, retry_path)
            else:
                self.chapter_reviewer(input_path, retry_path)
            self._checkpoint()
            review = read_json(retry_path)
        review = validate_chapter_review_payload(
            review,
            expected_ids,
            context_before_ids=expected_ids,
            source_texts={str(item["id"]): str(item.get("source", "")) for item in items},
        )
        current_translations = {item["id"]: item["translated"] for item in items}
        source_texts = {str(item["id"]): str(item.get("source", "")) for item in items}
        gate_results = evaluate_apply_gate(
            review["fixes"],
            autonomous=self.autonomous,
            current_translations=current_translations,
            source_texts=source_texts,
        )
        # Re-read immediately before producing the write artifact so a manifest
        # change during review becomes stale instead of being overwritten.
        fresh_paragraphs = paragraph_map(read_json(self.manifest))
        fresh_translations = {
            item_id: str(fresh_paragraphs.get(item_id, {}).get("translated", ""))
            for item_id in expected_ids
        }
        fresh_sources = {
            item_id: str(fresh_paragraphs.get(item_id, {}).get("source", ""))
            for item_id in expected_ids
        }
        gate_results = evaluate_apply_gate(
            gate_results,
            autonomous=self.autonomous,
            current_translations=fresh_translations,
            source_texts=fresh_sources,
        )
        if self.review_apply_enabled:
            gate_results = compose_approved_fixes(gate_results, fresh_translations)
        if not self.review_apply_enabled:
            for item in gate_results:
                if item.get("apply_reason") == "gate_passed":
                    item["apply_reason"] = "report_only_mode"
        pass_diagnostics = [
            {"id": item.get("id", ""), "apply_reason": item.get("apply_reason", "pass")}
            for item in gate_results if item.get("decision") == "PASS"
        ]
        if pass_diagnostics:
            review.setdefault("review_diagnostics", {})["apply_gate_pass"] = pass_diagnostics
        gate_results = [item for item in gate_results if item.get("decision") != "PASS"]
        review["fixes"] = gate_results
        fixes = [item for item in gate_results if item.get("apply_reason") == "gate_passed"]
        fixes_path = self.workspace.reviews_dir / f"{chapter_id}-approved-fixes.json"
        write_json(fixes_path, {"book": self.book, "items": unique_writeback_fixes(fixes)})
        applied_fixes: Any = False
        remaining_kana: list[str] = []
        if self.review_apply_enabled:
            if fixes:
                self._checkpoint()
                replacement_fixes = [item for item in unique_writeback_fixes(fixes) if item.get("operation", "replace") != "clear"]
                clear_fixes: list[dict[str, Any]] = []  # clear is disabled by the shared gate
                write_error: Exception | None = None
                try:
                    if replacement_fixes:
                        tool_fixes_path = fixes_path
                        if clear_fixes:
                            tool_fixes_path = self.workspace.reviews_dir / f"{chapter_id}-approved-replacements.json"
                            write_json(tool_fixes_path, {"book": self.book, "items": replacement_fixes})
                        applied_fixes = self.tool_call("apply-review-fixes", "--book", self.book, "--input", str(tool_fixes_path))
                except Exception as exc:
                    write_error = exc
                    applied_fixes = {"status": "error", "error": str(exc)}
            manifest_after_fixes = read_json(self.manifest)
            gate_results = finalize_writeback_states(gate_results, manifest_after_fixes, execution_error=locals().get("write_error"))
            review["fixes"] = gate_results
            fixes = [item for item in gate_results if item.get("apply_state") == "applied"]
            remaining_kana = [
                item_id
                for item_id, paragraph in paragraph_map(manifest_after_fixes).items()
                if item_id in expected_ids
                and has_target_script_residue(
                    str(paragraph.get("translated", "")),
                    source=str(paragraph.get("source", "")),
                )
            ]
            if remaining_kana:
                repaired_ids = self._repair_remaining_kana(chapter_id, remaining_kana)
                if repaired_ids:
                    manifest_after_fixes = read_json(self.manifest)
                    remaining_kana = [
                        item_id
                        for item_id, paragraph in paragraph_map(manifest_after_fixes).items()
                        if item_id in expected_ids
                        and has_target_script_residue(
                            str(paragraph.get("translated", "")),
                            source=str(paragraph.get("source", "")),
                        )
                    ]
            if remaining_kana:
                guarded_fixes = list(review["fixes"])
                guarded_fixes.extend({
                    "id": item_id,
                    "decision": "FIX_REQUIRED",
                    "category": "policy_violation",
                    "severity": "critical",
                    "confidence": 1.0,
                    "reason": "最终写回校验发现译文仍残留日文假名或韩文字符；审阅器及定向微修复均未提供合格替换，已阻止章节完成。",
                    "replacement": "",
                    "auto_apply": False,
                    "invalid_reason": "最终写回校验发现未解决的日文假名或韩文字符残留",
                    "apply_state": "blocked",
                    "apply_reason": "remaining_target_script",
                    "validation_errors": ["target_script_residue"],
                    "reporters": ["writeback_guard"],
                } for item_id in remaining_kana)
                review = {**review, "fixes": guarded_fixes}
                # Persist the guard findings before failing, so the UI never reports 0 issues
                # after earlier fixes have already been written to the manifest.
                write_json(output_path, review)
        # Persist the final gate decisions, including report_only/blocked/failed
        # reasons, rather than leaving the raw provider response on disk.
        write_json(output_path, review)
        self._checkpoint()
        for deferred_window, deferred_index, deferred_total in self._deferred_knowledge_windows.pop(chapter_id, []):
            self._extract_window_knowledge(
                chapter_id, deferred_window, review, deferred_index, deferred_total
            )
        # Custom reviewer integrations do not expose window callbacks. Treat
        # the complete chapter as one window so the same extractor/finalizer
        # boundary still applies.
        if not self._knowledge_windows.get(chapter_id):
            self._extract_window_knowledge(
                chapter_id,
                {"items": items, "context_before": [], "context_after": []},
                review,
                1,
                1,
            )
        knowledge_summary = self._finalize_chapter_knowledge(chapter_id, items)
        window_results = self._knowledge_windows.get(chapter_id, [])
        knowledge_summary.update({
            "window_count": len(window_results),
            "window_candidate_count": sum(
                len(item.get("knowledge_candidates", []) or [])
                for item in window_results if isinstance(item, dict)
            ),
            "window_conflict_count": sum(
                len(item.get("conflicts", []) or [])
                for item in window_results if isinstance(item, dict)
            ),
            "window_failure_count": sum(
                1 for item in window_results
                if isinstance(item, dict) and item.get("status") == "failed"
            ),
        })
        term_summary = {
            "reported": len(self._knowledge_candidates.get(chapter_id, [])),
            "known_hits": len(known_hits_doc.get("known_hits", [])) if isinstance(known_hits_doc, dict) else 0,
            "knowledge": knowledge_summary,
        }
        report_path = self.workspace.reports_dir / f"{chapter_id}.json"
        counts = review_report_counts(len(expected_ids), review["fixes"])
        write_json(report_path, {
            "book": self.book,
            "chapter_id": chapter_id,
            "reviewed_at": utc_now(),
            "checked_paragraphs": len(expected_ids),
            "reported_issues": counts["fix_required"],
            **counts,
            "context_findings": len(review.get("context_findings", []) or []),
            "review_diagnostics": review.get("review_diagnostics", {}),
            "applied_fixes": counts["applied"],
            "approved_fixes": [item for item in review["fixes"] if item.get("apply_state") == "applied"],
            "fixes": review["fixes"],
            "term_summary": term_summary,
            "pre_scan": self._prescan_reports.get(chapter_id, {}),
            "recovery_summary": self._translation_recovery.get(chapter_id, {
                "attempt_count": 0,
                "recovered_ids": [],
                "deterministic_repair_recovered_ids": [],
                "repair_rule_version": REPAIR_RULE_VERSION,
                "repair_rule_ids": [],
                "failure_class_counts": {},
                "target_script_residue_ids": [],
                "remaining_ids": [],
            }),
            "repair_events": self._repair_events.get(chapter_id, []),
            "knowledge": knowledge_summary,
            "applied": applied_fixes,
            "remaining_kana_ids": remaining_kana,
        })
        self._checkpoint()
        if remaining_kana:
            raise ValueError(f"章节 {chapter_id} 写回后仍残留日文假名或韩文字符：{', '.join(sorted(remaining_kana))}")
        return {
            "chapter_id": chapter_id,
            "reviewed": len(expected_ids),
            "checked_paragraphs": len(expected_ids),
            "issues": len(review["fixes"]),
            "context_findings": len(review.get("context_findings", []) or []),
            "fixes": len(fixes),
            "applied": applied_fixes,
        }

    def run_chapter(self, chapter_id: str, cycle: int) -> dict[str, Any]:
        self._checkpoint()
        progress = read_json(self.workspace.progress_path, {
            "book": self.book,
            "state": "running",
            "completed_cycles": 0,
            "last_chunk": "",
            "updated_at": utc_now(),
        })
        if self.on_phase_changed:
            self.on_phase_changed({"phase": "translating", "chapter_id": chapter_id})
        translated_summary = self._translate_chapter(chapter_id, cycle)
        progress.update({
            "state": "running",
            "last_chapter": chapter_id,
            "chapter_status": "translated",
            "last_translated": translated_summary["translated_paragraphs"],
            "updated_at": utc_now(),
        })
        write_json(self.workspace.progress_path, progress)
        self._checkpoint()
        try:
            known_hits = self._prescan_chapter(chapter_id)
        except JobCancelled:
            raise
        except Exception as exc:
            # The scan is advisory: an unreadable glossary or malformed
            # chapter must not prevent translation/review from continuing.
            known_hits = {
                "schema_version": "1.0",
                "chapter_id": chapter_id,
                "known_hits": [],
                "hit_count": 0,
                "term_count": 0,
                "status": "failed",
                "deterministic": True,
                "error": str(exc),
            }
            write_json(self.workspace.reviews_dir / f"{chapter_id}-known-hits.json", known_hits)
            previous_report = self._prescan_reports.get(chapter_id, {})
            self._prescan_reports[chapter_id] = {
                "extraction_status": "failed",
                "known_hit_count": 0,
                "known_term_count": 0,
                "diagnostic": str(exc),
            }
            if previous_report.get("injected_into_translation"):
                self._prescan_reports[chapter_id]["injected_into_translation"] = previous_report["injected_into_translation"]
        progress.update({
            "chapter_status": "known_hits_scanned",
            "known_hit_count": int(known_hits.get("hit_count", 0) or 0),
            "updated_at": utc_now(),
        })
        write_json(self.workspace.progress_path, progress)
        self._checkpoint()
        if self.on_phase_changed:
            self.on_phase_changed({"phase": "reviewing", "chapter_id": chapter_id})
        try:
            reviewed_summary = self._review_chapter(chapter_id)
        except ReviewContextOverflowError as exc:
            progress.update({
                "state": "running",
                "last_chapter": chapter_id,
                "chapter_status": "needs_oversized_review",
                "review_overflow": {
                    "reason": exc.reason,
                    "context_snapshot_id": exc.diagnostics.get("context_snapshot_id"),
                    "prompt_chars": exc.diagnostics.get("prompt_chars"),
                    "operational_input_hard_limit_chars": exc.diagnostics.get("operational_input_hard_limit_chars"),
                },
                "updated_at": utc_now(),
            })
            write_json(self.workspace.progress_path, progress)
            raise
        self._checkpoint()
        # Knowledge extraction is advisory; its failure never invalidates an
        # otherwise translated and semantically reviewed chapter.
        review_status = "reviewed"
        progress.update({
            "state": "running",
            "completed_cycles": cycle,
            "last_chapter": chapter_id,
            "chapter_status": review_status,
            "last_reviewed": reviewed_summary["checked_paragraphs"],
            "updated_at": utc_now(),
        })
        write_json(self.workspace.progress_path, progress)
        return {
            "chapter_id": chapter_id,
            "translated": translated_summary["translated_paragraphs"],
            "reviewed": reviewed_summary["checked_paragraphs"],
            "issues": reviewed_summary.get("issues", 0),
            "fixes": reviewed_summary.get("fixes", 0),
            "translation": translated_summary,
            "review": reviewed_summary,
        }

    def finalize(self) -> dict[str, Any]:
        self._checkpoint()
        manifest = read_json(self.manifest)
        untranslated = [
            str(paragraph["id"])
            for chapter in manifest.get("chapters", [])
            for paragraph in chapter.get("paragraphs", [])
            if isinstance(paragraph, dict) and paragraph.get("id") and not str(paragraph.get("translated", "")).strip()
        ]
        if untranslated:
            raise ValueError(f"全书尚有未翻译段落，无法导出：{', '.join(untranslated)}")
        missing_reviews = [
            str(chapter.get("id", ""))
            for chapter in manifest.get("chapters", [])
            if chapter.get("id")
            and not (self.workspace.chapter_states_dir / f"{chapter['id']}.json").exists()
            and not (self.workspace.reports_dir / f"{chapter['id']}.json").exists()
        ]
        if missing_reviews:
            raise ValueError(f"章节审阅状态不完整，无法导出：{', '.join(missing_reviews)}")
        output = self.workspace.epub_path
        self._checkpoint()
        exported = self.tool_call("export", "--book", self.book, "--format", "epub", "--output", str(output), "--monolingual")
        if not isinstance(exported, dict) or exported.get("status") not in {"ok", "success", "exported"}:
            raise ValueError(f"EPUB export payload 未通过：{exported}")
        if not output.is_file() or output.stat().st_size == 0 or not zipfile.is_zipfile(output):
            raise ValueError(f"EPUB export 产物无效：{output}")
        self._checkpoint()
        meta = extract_book_metadata(
            self.book,
            manifest,
            self.workspace,
            primary_provider=self.primary_translator,
            fallback_providers=self.fallback_translators,
        )
        self._checkpoint()

        if self.layout == "horizontal":
            apply_horizontal_layout(output, metadata=meta)
        else:
            inject_epub_metadata(output, metadata=meta)
        self._checkpoint()

        validation = self.tool_call("validate-epub", "--path", str(output))
        if (
            not isinstance(validation, dict)
            or validation.get("status") not in {"ok", "success", "valid", "warning"}
            or bool(validation.get("errors"))
        ):
            raise ValueError(f"EPUB validate payload 未通过：{validation}")
        self._checkpoint()

        self.translated_root.mkdir(parents=True, exist_ok=True)
        target_filename = sanitize_epub_filename(meta.get("title_zh", ""), meta.get("author_zh", ""))
        destination = self.translated_root / target_filename
        temporary_destination = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        self._checkpoint()
        shutil.copy2(output, temporary_destination)
        source_hash = hashlib.sha256(output.read_bytes()).hexdigest()
        copied_hash = hashlib.sha256(temporary_destination.read_bytes()).hexdigest()
        if source_hash != copied_hash:
            temporary_destination.unlink(missing_ok=True)
            raise ValueError("EPUB 临时交付副本 hash 不一致")
        temporary_destination.replace(destination)
        self._checkpoint()
        report_path = generate_work_report(
            workspace=self.workspace.root,
            book=self.book,
            primary_translator=self.primary_translator,
            fallback_translators=self.fallback_translators,
            fallback_translator=self.fallback_translator,
            reviewer=self.reviewer,
            novel_root=NOVEL_TRANSLATOR_ROOT,
            manifest=manifest,
            layout=self.layout,
        )
        result = {
            "status": "exported",
            "book": self.book,
            "output": str(output),
            "epub": str(output),
            "translated_output": str(destination),
            "translated_copy": str(destination),
            "target_filename": target_filename,
            "metadata": meta,
            "layout": self.layout,
            "exported": exported,
            "validation": validation,
            "sha256": source_hash,
            "work_report": str(self.workspace.data_dir / "work-report.yaml"),
        }
        write_json(self.workspace.reports_dir / "final-delivery.json", result)
        progress = read_json(self.workspace.progress_path, {"book": self.book})
        progress.update({"state": "completed", "output": str(output), "updated_at": utc_now(), "target_filename": target_filename})
        write_json(self.workspace.progress_path, progress)

        return result


def main() -> int:
    args = parse_args()
    if args.max_cycles < 0:
        raise ValueError("max_cycles 必须大于或等于 0")
    if args.health_check_timeout <= 0:
        raise ValueError("health_check_timeout 必须大于 0")
    workspace = BookWorkspace.at(args.output_root, args.name)
    targeted_translator = ProviderTranslator(
        novel_root=NOVEL_TRANSLATOR_ROOT,
        manifest=manifest_path(args.book),
        glossary_path=workspace.glossary_path,
    )
    try:
        preflight = run_preflight(
            targeted_translator,
            timeout=args.health_check_timeout,
            primary_translator=args.primary_translator,
            fallback_translators=args.fallback_translators,
            fallback_translator=args.fallback_translator,
            secondary_fallback_translator=args.secondary_fallback_translator,
            reviewer=args.reviewer,
            secondary_reviewer=args.secondary_reviewer,
            dual_review=args.dual_review,
        )
    except PreflightError as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    def configured_chapter_reviewer(
        input_path: Path,
        output_path: Path,
        *,
        on_window_completed: Callable[..., Any] | None = None,
    ) -> None:
        run_chapter_review(
            input_path,
            output_path,
            autonomous=args.autonomous,
            backend=args.reviewer,
            secondary_backend=args.secondary_reviewer,
            dual_review=args.dual_review,
            chunk_min_chars=args.review_chunk_min_chars,
            chunk_max_chars=args.review_chunk_max_chars,
            context_before=args.review_context_before,
            context_after=args.review_context_after,
            backtrack_enabled=args.review_backtrack,
            backtrack_min_confidence=args.review_backtrack_min_confidence,
            on_window_completed=on_window_completed,
        )

    setattr(configured_chapter_reviewer, "_uses_window_knowledge", True)

    pipeline = IterativePipeline(
        book=args.book,
        workspace=workspace,
        manifest=manifest_path(args.book),
        targeted_translator=targeted_translator,
        chapter_reviewer=configured_chapter_reviewer,
        primary_batch_max_chars=args.primary_batch_max_chars,
        primary_translator=args.primary_translator,
        max_provider_split_depth=args.max_provider_split_depth,
        split_on_content_filter=args.split_on_content_filter,
        fallback_translators=args.fallback_translators,
        fallback_translator=args.fallback_translator,
        secondary_fallback_translator=args.secondary_fallback_translator,
        translation_max_tokens=args.translation_max_tokens,
        max_chapter_batches=args.max_chapter_batches,
        translation_policy=args.translation_policy,
        apply=args.apply,
        autonomous=args.autonomous,
        reviewer=args.reviewer,
        layout=args.layout,
    )
    pipeline.initialize()
    write_json(workspace.data_dir / "preflight.json", preflight)
    results = []
    progress = read_json(workspace.progress_path, {})
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

    payload: dict[str, Any] = {"book": args.book, "workspace": str(workspace.root), "preflight": preflight, "chapters": results}
    if args.finalize:
        payload["finalize"] = pipeline.finalize()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


ChapterPipeline = IterativePipeline

__all__ = ["ChapterPipeline", "IterativePipeline", "manifest_path", "paragraph_map", "newly_translated"]


if __name__ == "__main__":
    raise SystemExit(main())
