from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Callable

from translator.core.config import (
    dual_review_enabled,
    fallback_translators_names,
    load_config,
    primary_translator_name,
    reviewer_name,
    secondary_reviewer_name,
    setting,
)
from translator.core.layout import apply_horizontal_layout
from translator.core.novel_tool import (
    NOVEL_TRANSLATOR_ROOT,
    call_novel_translator,
    provider_failure_reason,
)
from translator.core.report import generate_work_report
from translator.core.workspace import (
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
from translator.pipeline.preflight import PreflightError, run_preflight
from translator.providers.translator import ProviderTranslator
from translator.review.reviewer import (
    approved_fixes,
    missing_checked_ids,
    run_chapter_review,
    validate_chapter_review_payload,
    verify_applied_fixes,
)


ROOT = Path(__file__).resolve().parents[2]
ToolCall = Callable[..., dict[str, Any]]
Reviewer = Callable[[Path, Path], None]


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
    parser.add_argument("--apply", action="store_true", help="应用高置信度译文修复")
    parser.add_argument("--autonomous", action="store_true", help="全自动应用置信度 >= 0.9 的有效修复")
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
        chapter_reviewer: Reviewer = run_chapter_review,
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
        reviewer: str | None = None,
        layout: str | None = None,
        translated_root: Path | None = None,
    ) -> None:
        self.book = book
        self.workspace = workspace
        self.manifest = manifest
        self.tool_call = tool_call
        if targeted_translator is not None:
            self.targeted_translator = targeted_translator
        elif tool_call is call_novel_translator:
            self.targeted_translator = ProviderTranslator(
                novel_root=NOVEL_TRANSLATOR_ROOT,
                manifest=manifest,
            )
        else:
            self.targeted_translator = None
        self.chapter_reviewer = chapter_reviewer

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
        eff_max_tokens = translation_max_tokens if translation_max_tokens is not None else int(pipeline_cfg.get("translation_max_tokens", 8192))
        self.translation_max_tokens = max(512, eff_max_tokens)
        eff_max_batches = max_chapter_batches if max_chapter_batches is not None else int(pipeline_cfg.get("max_chapter_batches", 1000))
        self.max_chapter_batches = max(1, eff_max_batches)
        self.translation_policy = translation_policy or ROOT / config.get("paths", {}).get("translation_policy", "docs/prompts/translation-policy.md")
        self.apply = apply
        self.autonomous = autonomous
        self.reviewer = reviewer or reviewer_name(config)
        self.layout = layout or str(pipeline_cfg.get("layout", "preserve"))
        self.translated_root = translated_root or ROOT / "translated"

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
            if provider != self.primary_translator:
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
            primary_translator = self.primary_translator
            result = self._translate_target(primary_translator, ids, source_chars)
            reason = provider_failure_reason(result)
        except Exception as exc:  # noqa: BLE001
            result = {"status": "error", "error": str(exc)}
            reason = provider_failure_reason(result)
        remaining = {str(item["id"]) for item in self._chapter_pending_paragraphs(chapter_id)}
        attempt = {
            "provider": primary_translator,
            "depth": depth,
            "ids": ids,
            "source_chars": source_chars,
            "result": result,
            "reason": reason,
            "remaining": sorted(remaining),
        }
        attempts.append(attempt)
        self._record_provider_attempt(attempt)
        if self.targeted_translator is None:
            self._record_translation_provenance(ids, primary_translator)
            return
        if not (set(ids) & remaining):
            self._record_translation_provenance(ids, primary_translator)
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
        recovered = False
        for fb_idx, fb_provider in enumerate(self.fallback_translators):
            fb_result = self._translate_target(fb_provider, fallback_ids, source_chars)
            fb_remaining = {str(item["id"]) for item in self._chapter_pending_paragraphs(chapter_id)}
            fb_reason = f"{self.primary_translator}_{reason}_fb{fb_idx+1}"
            fb_attempt = {
                "provider": fb_provider,
                "depth": depth,
                "ids": ids,
                "source_chars": source_chars,
                "result": fb_result,
                "reason": fb_reason,
                "remaining": sorted(fb_remaining),
            }
            attempts.append(fb_attempt)
            self._record_provider_attempt(fb_attempt)
            if not (set(ids) & fb_remaining):
                self._record_translation_provenance(ids, fb_provider, fb_reason)
                recovered = True
                break

        if not recovered and len(fallback_ids) > 1:
            for item in paragraphs:
                if str(item["id"]) in {str(p["id"]) for p in self._chapter_pending_paragraphs(chapter_id)}:
                    self._translate_segment_with_recovery(chapter_id, [item], attempts, depth + 1)
            remaining_after = {str(item["id"]) for item in self._chapter_pending_paragraphs(chapter_id)}
            if not (set(ids) & remaining_after):
                return

        if not recovered:
            unresolved = sorted(set(ids) & {str(item["id"]) for item in self._chapter_pending_paragraphs(chapter_id)})
            raise RuntimeError(f"所有 fallback ({', '.join(self.fallback_translators)}) 均未完成章节 {chapter_id} 段落：{', '.join(unresolved)}")
        return

    def _translate_chapter(self, chapter_id: str, _cycle: int) -> dict[str, Any]:
        chapter = self._chapter(chapter_id)
        before_path = self.workspace.snapshots_dir / f"{chapter_id}-before.json"
        if not before_path.exists():
            snapshot = self.tool_call("snapshot", "--book", self.book, "--name", f"before-{chapter_id}")
            write_json(before_path, snapshot)
        attempts: list[dict[str, Any]] = []
        initial_pending = self._chapter_pending_ids(chapter)
        batches = 0
        while batches < self.max_chapter_batches:
            pending_paragraphs = self._chapter_pending_paragraphs(chapter_id)
            if not pending_paragraphs:
                break
            batch_window = self._window(pending_paragraphs, self.primary_batch_max_chars)
            self._translate_segment_with_recovery(chapter_id, batch_window, attempts)
            batches += 1

        untranslated = self._chapter_pending_ids(self._chapter(chapter_id))
        if untranslated:
            raise RuntimeError(f"章节 {chapter_id} 在 {batches} 个批次后仍有未翻译段落：{', '.join(sorted(untranslated))}")
        after_path = self.workspace.snapshots_dir / f"{chapter_id}-after.json"
        snapshot = self.tool_call("snapshot", "--book", self.book, "--name", f"after-{chapter_id}")
        write_json(after_path, snapshot)
        return {
            "chapter_id": chapter_id,
            "batches": batches,
            "translated": len(initial_pending),
            "translated_paragraphs": len(initial_pending),
            "attempts": len(attempts),
        }

    def _review_chapter(self, chapter_id: str) -> dict[str, Any]:
        chapter = self._chapter(chapter_id)
        paragraphs = [p for p in chapter.get("paragraphs", []) if isinstance(p, dict) and p.get("id")]
        items = [{"id": str(p["id"]), "source": str(p.get("source", "")), "translated": str(p.get("translated", ""))} for p in paragraphs if str(p.get("translated", "")).strip()]
        glossary = read_json(self.workspace.glossary_path, {"book": self.book, "terms": [], "conflicts": []})
        memory = read_json(self.workspace.book_memory_path, empty_book_memory(self.book))
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
            "glossary": glossary.get("terms", []),
        })
        self.chapter_reviewer(input_path, output_path)
        review = read_json(output_path)
        if not isinstance(review, dict):
            raise ValueError(f"章节审阅结果不是 JSON 对象：{output_path}")
        expected_ids = {str(item["id"]) for item in items}
        for retry in range(1, 3):
            if not missing_checked_ids(review, expected_ids):
                break
            retry_path = self.workspace.reviews_dir / f"{chapter_id}-retry-{retry:02d}.json"
            self.chapter_reviewer(input_path, retry_path)
            review = read_json(retry_path)
        validate_chapter_review_payload(review, expected_ids)
        fixes = approved_fixes(review["fixes"], autonomous=self.autonomous)
        fixes_path = self.workspace.reviews_dir / f"{chapter_id}-approved-fixes.json"
        write_json(fixes_path, {"book": self.book, "items": fixes})
        applied_fixes = False
        if self.apply and fixes:
            applied_fixes = self.tool_call("apply-review-fixes", "--book", self.book, "--input", str(fixes_path))
            manifest_after_fixes = read_json(self.manifest)
            verify_applied_fixes(manifest_after_fixes, fixes)
        merged_terms, term_summary = merge_term_updates(
            glossary,
            review["glossary_delta"].get("add", []) + review["glossary_delta"].get("update", []),
            chapter_id,
        )
        merged_memory, mem_summary = merge_memory_delta(memory, review["memory_delta"], chapter_id)
        write_json(self.workspace.glossary_path, merged_terms)
        write_json(self.workspace.book_memory_path, merged_memory)
        write_json(self.workspace.novel_translator_terms_path, novel_translator_terms(merged_terms))
        chapter_state = merge_chapter_state(chapter_id, str(chapter.get("title", "")), review["chapter_state"])
        write_json(self.workspace.chapter_states_dir / f"{chapter_id}.json", chapter_state)
        report_path = self.workspace.reports_dir / f"{chapter_id}.json"
        write_json(report_path, {
            "book": self.book,
            "chapter_id": chapter_id,
            "reviewed_at": utc_now(),
            "checked_paragraphs": len(expected_ids),
            "reported_issues": len(review["fixes"]),
            "applied_fixes": len(fixes) if self.apply else 0,
            "approved_fixes": fixes,
            "term_summary": term_summary,
            "memory_summary": mem_summary,
            "applied": applied_fixes,
        })
        return {
            "chapter_id": chapter_id,
            "reviewed": len(expected_ids),
            "checked_paragraphs": len(expected_ids),
            "issues": len(review["fixes"]),
            "fixes": len(fixes),
            "applied": applied_fixes,
        }

    def run_chapter(self, chapter_id: str, cycle: int) -> dict[str, Any]:
        progress = read_json(self.workspace.progress_path, {
            "book": self.book,
            "state": "running",
            "completed_cycles": 0,
            "last_chunk": "",
            "updated_at": utc_now(),
        })
        translated_summary = self._translate_chapter(chapter_id, cycle)
        progress.update({
            "state": "running",
            "last_chapter": chapter_id,
            "chapter_status": "translated",
            "last_translated": translated_summary["translated_paragraphs"],
            "updated_at": utc_now(),
        })
        write_json(self.workspace.progress_path, progress)
        reviewed_summary = self._review_chapter(chapter_id)
        progress.update({
            "state": "running",
            "completed_cycles": cycle,
            "last_chapter": chapter_id,
            "chapter_status": "reviewed",
            "last_reviewed": reviewed_summary["checked_paragraphs"],
            "updated_at": utc_now(),
        })
        write_json(self.workspace.progress_path, progress)
        return {
            "chapter_id": chapter_id,
            "translated": translated_summary["translated_paragraphs"],
            "reviewed": reviewed_summary["checked_paragraphs"],
            "translation": translated_summary,
            "review": reviewed_summary,
        }

    def finalize(self) -> dict[str, Any]:
        manifest = read_json(self.manifest)
        untranslated = [
            str(paragraph["id"])
            for chapter in manifest.get("chapters", [])
            for paragraph in chapter.get("paragraphs", [])
            if isinstance(paragraph, dict) and paragraph.get("id") and not str(paragraph.get("translated", "")).strip()
        ]
        if untranslated:
            raise ValueError(f"全书尚有未翻译段落，无法导出：{', '.join(untranslated)}")
        output = self.workspace.epub_path
        exported = self.tool_call("export", "--book", self.book, "--format", "epub", "--output", str(output), "--monolingual")
        validation = self.tool_call("validate-epub", "--path", str(output))
        if self.layout == "horizontal":
            apply_horizontal_layout(output)
            validation = self.tool_call("validate-epub", "--path", str(output))
        self.translated_root.mkdir(parents=True, exist_ok=True)
        destination = self.translated_root / output.name
        shutil.copy2(output, destination)
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
            "layout": self.layout,
            "exported": exported,
            "validation": validation,
            "work_report": str(self.workspace.data_dir / "work-report.yaml"),
        }
        write_json(self.workspace.reports_dir / "final-delivery.json", result)
        progress = read_json(self.workspace.progress_path, {"book": self.book})
        progress.update({"state": "completed", "output": str(output), "updated_at": utc_now()})
        write_json(self.workspace.progress_path, progress)
        return result


def main() -> int:
    args = parse_args()
    if args.max_cycles < 0:
        raise ValueError("max_cycles 必须大于或等于 0")
    if args.health_check_timeout <= 0:
        raise ValueError("health_check_timeout 必须大于 0")
    workspace = BookWorkspace.at(args.output_root, args.name)
    targeted_translator = ProviderTranslator(novel_root=NOVEL_TRANSLATOR_ROOT, manifest=manifest_path(args.book))
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
    pipeline = IterativePipeline(
        book=args.book,
        workspace=workspace,
        manifest=manifest_path(args.book),
        targeted_translator=targeted_translator,
        chapter_reviewer=lambda input_path, output_path: run_chapter_review(
            input_path,
            output_path,
            autonomous=args.autonomous,
            backend=args.reviewer,
            secondary_backend=args.secondary_reviewer,
            dual_review=args.dual_review,
        ),
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

