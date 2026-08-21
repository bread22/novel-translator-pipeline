from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
from typing import Any, Callable

from translator.core.config import load_config, setting
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
    parser.add_argument("--max-provider-split-depth", type=int, default=pipeline.get("max_provider_split_depth", 8), help="provider blocked 后最多二分深度")
    parser.add_argument(
        "--primary-translator", dest="primary_translator",
        default=setting(config, "roles.primary_translator", "PRIMARY_TRANSLATOR"),
        choices=["antigravity", "opencode"],
        help="primary_translator 使用的 provider",
    )
    parser.add_argument(
        "--fallback-translator", dest="fallback_translator",
        default=setting(config, "roles.fallback_translator", "FALLBACK_TRANSLATOR"),
        choices=["lmstudio", "opencode"],
        help="fallback_translator 使用的 provider",
    )
    parser.add_argument("--translation-max-tokens", type=int, default=pipeline.get("translation_max_tokens", 8192), help="单个翻译窗口的最大输出 token")
    parser.add_argument("--apply", action="store_true", help="应用高置信度译文修复")
    parser.add_argument("--autonomous", action="store_true", help="全自动应用置信度 >= 0.9 的有效修复")
    parser.add_argument("--finalize", action="store_true", help="全部翻译完成后导出并校验中文 EPUB")
    parser.add_argument("--layout", choices=["preserve", "horizontal"], default=pipeline.get("layout", "preserve"), help="导出 EPUB 的版式")
    parser.add_argument("--health-check-timeout", type=int, default=pipeline.get("health_check_timeout", 60), help="启动前健康检查超时秒数")
    parser.add_argument(
        "--reviewer", dest="reviewer",
        default=setting(config, "roles.reviewer", "REVIEWER"),
        choices=["codex", "opencode"],
        help="审阅后端",
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
        primary_batch_max_chars: int = 4000,
        primary_translator: str = "antigravity",
        max_provider_split_depth: int = 8,
        fallback_translator: str = "lmstudio",
        translation_max_tokens: int = 8192,
        max_chapter_batches: int = 1000,
        translation_policy: Path | None = None,
        apply: bool = False,
        autonomous: bool = False,
        reviewer: str = "opencode",
        layout: str = "preserve",
        translated_root: Path | None = None,
    ) -> None:
        self.book = book
        self.workspace = workspace
        self.manifest = manifest
        self.tool_call = tool_call
        self.targeted_translator = targeted_translator
        self.chapter_reviewer = chapter_reviewer
        if primary_batch_max_chars < 1:
            raise ValueError("primary_batch_max_chars 必须大于 0")
        self.primary_batch_max_chars = primary_batch_max_chars
        if primary_translator not in {"antigravity", "opencode"}:
            raise ValueError(f"未知 primary_translator provider：{primary_translator}")
        self.primary_translator = primary_translator
        if max_provider_split_depth < 0:
            raise ValueError("max_provider_split_depth 必须大于等于 0")
        self.max_provider_split_depth = max_provider_split_depth
        if fallback_translator not in {"lmstudio", "opencode"}:
            raise ValueError(f"未知 fallback_translator provider：{fallback_translator}")
        self.fallback_translator = fallback_translator
        if translation_max_tokens < 1:
            raise ValueError("translation_max_tokens 必须大于 0")
        self.translation_max_tokens = translation_max_tokens
        if max_chapter_batches < 1:
            raise ValueError("max_chapter_batches 必须大于 0")
        self.max_chapter_batches = max_chapter_batches
        self.translation_policy = translation_policy
        self.apply = apply
        self.autonomous = autonomous
        if reviewer not in {"opencode", "codex"}:
            raise ValueError(f"未知 reviewer provider：{reviewer}")
        self.reviewer_provider = reviewer
        if layout not in {"preserve", "horizontal"}:
            raise ValueError(f"未知 EPUB layout：{layout}")
        self.layout = layout
        self.translated_root = (translated_root or ROOT / "translated").expanduser().resolve()

    def initialize(self) -> None:
        raw = read_json(self.manifest)
        if not isinstance(raw, dict):
            raise FileNotFoundError(f"Novel Translator manifest not found: {self.manifest}")
        source = Path(str(raw.get("source_file", ""))).expanduser()
        self.workspace.initialize(source if source.suffix.casefold() == ".epub" else None, book_id=self.book)

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
            if provider != "antigravity":
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
        except Exception as exc:  # noqa: BLE001 - provider diagnostics are part of the run record
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
        if reason in {"content_filter", "output_format"}:
            if len(ids) > 1 and depth < self.max_provider_split_depth:
                midpoint = max(1, len(ids) // 2)
                left = [item for item in paragraphs[:midpoint] if str(item["id"]) in remaining]
                right = [item for item in paragraphs[midpoint:] if str(item["id"]) in remaining]
                if left:
                    self._translate_segment_with_recovery(chapter_id, left, attempts, depth + 1)
                if right:
                    self._translate_segment_with_recovery(chapter_id, right, attempts, depth + 1)
                return
            fallback_ids = sorted(set(ids) & remaining, key=ids.index)
            fallback_result = self._translate_target(self.fallback_translator, fallback_ids, source_chars)
            fallback_remaining = {str(item["id"]) for item in self._chapter_pending_paragraphs(chapter_id)}
            fallback_reason = f"{self.primary_translator}_{reason}"
            fallback_attempt = {
                "provider": self.fallback_translator,
                "depth": depth,
                "ids": ids,
                "source_chars": source_chars,
                "result": fallback_result,
                "reason": fallback_reason,
                "remaining": sorted(fallback_remaining),
            }
            attempts.append(fallback_attempt)
            self._record_provider_attempt(fallback_attempt)
            if set(ids) & fallback_remaining:
                raise RuntimeError(f"fallback 未完成章节 {chapter_id}：{', '.join(sorted(set(ids) & fallback_remaining))}")
            self._record_translation_provenance(ids, self.fallback_translator, fallback_reason)
            return
        raise RuntimeError(f"{self.primary_translator} provider error in {chapter_id}: {reason}; ids={','.join(ids)}")

    def _translate_chapter(self, chapter_id: str, _cycle: int) -> dict[str, Any]:
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
        layout_result = {"status": "preserved", "layout": "preserve"}
        if self.layout == "horizontal":
            layout_result = apply_horizontal_layout(output)
        epub_validation = self.tool_call("validate-epub", "--path", str(output))
        translated_output = self.translated_root / output.name
        self.translated_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(output, translated_output)
        manifest = read_json(self.manifest, {})
        report_path = generate_work_report(
            workspace=self.workspace.root,
            book=self.book,
            primary_translator=self.primary_translator,
            fallback_translator=self.fallback_translator,
            reviewer=self.reviewer_provider,
            layout=self.layout,
            novel_root=NOVEL_TRANSLATOR_ROOT,
            manifest=manifest,
        )
        result = {
            "status": "exported",
            "output": str(output),
            "translated_output": str(translated_output),
            "validation": validation,
            "export": exported,
            "layout": layout_result,
            "epub_validation": epub_validation,
            "work_report": str(report_path),
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
    if args.health_check_timeout <= 0:
        raise ValueError("health_check_timeout 必须大于 0")
    workspace = BookWorkspace.at(args.output_root, args.name)
    targeted_translator = ProviderTranslator(novel_root=NOVEL_TRANSLATOR_ROOT, manifest=manifest_path(args.book))
    try:
        preflight = run_preflight(
            targeted_translator,
            timeout=args.health_check_timeout,
            primary_translator=args.primary_translator,
            fallback_translator=args.fallback_translator,
            reviewer=args.reviewer,
        )
    except PreflightError as exc:
        print(json.dumps(exc.report, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2
    pipeline = IterativePipeline(
        book=args.book,
        workspace=workspace,
        manifest=manifest_path(args.book),
        targeted_translator=targeted_translator,
        chapter_reviewer=lambda input_path, output_path: run_chapter_review(input_path, output_path, autonomous=args.autonomous, backend=args.reviewer),
        primary_batch_max_chars=args.primary_batch_max_chars,
        primary_translator=args.primary_translator,
        max_provider_split_depth=args.max_provider_split_depth,
        fallback_translator=args.fallback_translator,
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


if __name__ == "__main__":
    raise SystemExit(main())
