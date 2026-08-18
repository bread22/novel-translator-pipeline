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
from scripts.novel_translator_tool import NOVEL_TRANSLATOR_ROOT, call_novel_translator


ToolCall = Callable[..., dict[str, Any]]
Reviewer = Callable[[Path, Path], None]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Iterative EPUB translation and Codex review pipeline")
    parser.add_argument("--book", required=True, help="Novel Translator book id")
    parser.add_argument("--name", required=True, help="output/ 下的书籍目录名和中文书名")
    parser.add_argument("--output-root", type=Path, default=ROOT / "output")
    parser.add_argument("--max-cycles", type=int, default=1, help="本次最多翻译并审阅多少个批次")
    parser.add_argument("--review-chunk-size", type=int, default=30)
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


class IterativePipeline:
    def __init__(
        self,
        *,
        book: str,
        workspace: BookWorkspace,
        manifest: Path,
        tool_call: ToolCall = call_novel_translator,
        reviewer: Reviewer = run_codex_review,
        review_chunk_size: int = 30,
        apply: bool = False,
        autonomous: bool = False,
    ) -> None:
        if review_chunk_size < 1:
            raise ValueError("review_chunk_size 必须大于 0")
        self.book = book
        self.workspace = workspace
        self.manifest = manifest
        self.tool_call = tool_call
        self.reviewer = reviewer
        self.review_chunk_size = review_chunk_size
        self.apply = apply
        self.autonomous = autonomous

    def initialize(self) -> None:
        raw = read_json(self.manifest)
        if not isinstance(raw, dict):
            raise FileNotFoundError(f"Novel Translator manifest not found: {self.manifest}")
        source = Path(str(raw.get("source_file", ""))).expanduser()
        self.workspace.initialize(source if source.suffix.casefold() == ".epub" else None, book_id=self.book)

    def run_cycle(self, cycle: int) -> dict[str, Any]:
        before = read_json(self.manifest)
        chunk_id = f"chunk-{cycle:05d}"
        snapshot = self.tool_call("snapshot", "--book", self.book, "--name", f"before-{chunk_id}")
        write_json(self.workspace.snapshots_dir / f"{chunk_id}.json", snapshot)
        translation = self.tool_call("translate", "--book", self.book, "--max-batches", "1")
        after = read_json(self.manifest)
        items = newly_translated(before, after)
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

    def finalize(self) -> dict[str, Any]:
        status = self.tool_call("translation-status", "--book", self.book)
        if int(status.get("summary", {}).get("pending", 1)) != 0:
            return {"status": "pending", "translation": status.get("summary", status)}
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
        review_chunk_size=args.review_chunk_size,
        apply=args.apply,
        autonomous=args.autonomous,
    )
    pipeline.initialize()
    results = []
    progress = read_json(workspace.progress_path, {})
    start = int(progress.get("completed_cycles", 0) or 0) + 1
    for cycle in range(start, start + args.max_cycles):
        result = pipeline.run_cycle(cycle)
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
