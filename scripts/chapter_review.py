from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.book_pipeline import approved_fixes, manifest_path, missing_checked_ids, validate_window_review_payload
from scripts.book_workspace import BookWorkspace, merge_term_updates, novel_translator_terms, read_json, utc_now, write_json
from scripts.codex_review import run_codex_chapter_review
from scripts.novel_translator_tool import call_novel_translator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run chapter-level consistency review")
    parser.add_argument("--book", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output-root", type=Path, default=ROOT / "output")
    parser.add_argument("--chapter-id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--autonomous", action="store_true")
    parser.add_argument("--export", action="store_true")
    return parser.parse_args()


def review_book(args: argparse.Namespace) -> dict[str, Any]:
    workspace = BookWorkspace.at(args.output_root, args.name)
    workspace.initialize(book_id=args.book)
    manifest = read_json(manifest_path(args.book))
    chapters = manifest.get("chapters", [])
    if args.chapter_id:
        chapters = [chapter for chapter in chapters if chapter.get("id") == args.chapter_id]
    if not chapters:
        raise ValueError("没有匹配的章节")

    snapshot = call_novel_translator("snapshot", "--book", args.book, "--name", "before-chapter-consistency")
    write_json(workspace.snapshots_dir / "before-chapter-consistency.json", snapshot)
    glossary = read_json(workspace.glossary_path, {"book": args.book, "terms": [], "conflicts": []})
    results = []
    for chapter in chapters:
        items = [
            {"id": p["id"], "source": p.get("source", ""), "translated": p.get("translated", "")}
            for p in chapter.get("paragraphs", [])
            if str(p.get("translated", "")).strip()
        ]
        if not items:
            continue
        chapter_id = str(chapter["id"])
        input_path = workspace.reviews_dir / f"{chapter_id}-consistency-input.json"
        output_path = workspace.reviews_dir / f"{chapter_id}-consistency-output.json"
        write_json(input_path, {"book": args.book, "chapter_id": chapter_id, "chapter_title": chapter.get("title", ""), "items": items, "glossary": glossary.get("terms", [])})
        run_codex_chapter_review(input_path, output_path, autonomous=args.autonomous)
        review = read_json(output_path)
        if not isinstance(review, dict):
            raise ValueError(f"章节审阅结果不是 JSON 对象：{output_path}")
        expected = {item["id"] for item in items}
        for retry in range(1, 3):
            if not missing_checked_ids(review, expected):
                break
            retry_path = workspace.reviews_dir / f"{chapter_id}-consistency-retry-{retry:02d}.json"
            run_codex_chapter_review(input_path, retry_path, autonomous=args.autonomous)
            review = read_json(retry_path)
        validate_window_review_payload(review, expected)
        if missing_checked_ids(review, expected):
            raise ValueError(f"章节 {chapter_id} 重试后仍有未检查段落")
        glossary, term_summary = merge_term_updates(glossary, review["term_updates"], chunk_id=f"chapter-{chapter_id}")
        fixes = approved_fixes(review["issues"], autonomous=args.autonomous)
        fixes_path = workspace.reviews_dir / f"{chapter_id}-consistency-fixes.json"
        write_json(fixes_path, {"book": args.book, "items": fixes})
        applied = False
        if args.apply and fixes:
            applied = call_novel_translator("apply-review-fixes", "--book", args.book, "--input", str(fixes_path))
        results.append({"chapter_id": chapter_id, "checked": len(expected), "issues": len(review["issues"]), "fixes": len(fixes), "applied": applied, "term_updates": term_summary})

    write_json(workspace.glossary_path, glossary)
    terms_path = workspace.data_dir / "novel-translator-terms.json"
    write_json(terms_path, novel_translator_terms(glossary))
    terminology = call_novel_translator("import-terminology", "--book", args.book, "--input", str(terms_path))
    quality = call_novel_translator("quality-report", "--book", args.book)
    report = {"book": args.book, "chapters": results, "terminology": terminology, "quality": quality, "updated_at": utc_now()}
    write_json(workspace.reports_dir / "chapter-consistency.json", report)
    if args.export:
        output = workspace.root / f"{workspace.root.name}-中文.epub"
        validation = call_novel_translator("validate-export", "--book", args.book, "--format", "epub")
        exported = call_novel_translator("export", "--book", args.book, "--format", "epub", "--output", str(output), "--monolingual")
        epub_validation = call_novel_translator("validate-epub", "--path", str(output))
        report["export"] = {"output": str(output), "validation": validation, "exported": exported, "epub_validation": epub_validation}
        write_json(workspace.reports_dir / "chapter-consistency.json", report)
    return report


if __name__ == "__main__":
    args = parse_args()
    print(json.dumps(review_book(args), ensure_ascii=False, indent=2))
