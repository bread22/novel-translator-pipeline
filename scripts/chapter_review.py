from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.book_pipeline import (
    approved_fixes,
    manifest_path,
    missing_checked_ids,
    validate_chapter_review_payload,
    validate_global_consistency_payload,
)
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
from scripts.codex_review import run_codex_chapter_review, run_codex_global_consistency_review
from scripts.novel_translator_tool import call_novel_translator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run chapter-level consistency review")
    parser.add_argument("--book", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output-root", type=Path, default=ROOT / "output")
    parser.add_argument("--chapter-id")
    parser.add_argument("--all", action="store_true", help="审阅 manifest 中的全部已翻译章节")
    parser.add_argument("--global-consistency", action="store_true", help="章节审阅后检查全书状态之间的一致性")
    parser.add_argument("--translation-policy", type=Path, default=ROOT / "docs" / "prompts" / "translation-policy.md")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--autonomous", action="store_true")
    parser.add_argument("--export", action="store_true")
    return parser.parse_args()


def review_book(args: argparse.Namespace) -> dict[str, Any]:
    workspace = BookWorkspace.at(args.output_root, args.name)
    workspace.initialize(book_id=args.book)
    manifest = read_json(manifest_path(args.book))
    all_chapters = manifest.get("chapters", [])
    chapters = all_chapters
    if args.chapter_id:
        chapters = [chapter for chapter in chapters if chapter.get("id") == args.chapter_id]
    if not chapters:
        raise ValueError("没有匹配的章节")

    snapshot = call_novel_translator("snapshot", "--book", args.book, "--name", "before-chapter-consistency")
    write_json(workspace.snapshots_dir / "before-chapter-consistency.json", snapshot)
    glossary = read_json(workspace.glossary_path, {"book": args.book, "terms": [], "conflicts": []})
    memory = read_json(workspace.book_memory_path, empty_book_memory(args.book))
    policy = args.translation_policy.read_text(encoding="utf-8") if args.translation_policy.exists() else ""
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
        previous_state = {}
        index = all_chapters.index(chapter)
        if index > 0:
            previous_id = str(all_chapters[index - 1].get("id", ""))
            previous_state = read_json(workspace.chapter_states_dir / f"{previous_id}.json", {}) or {}
        write_json(input_path, {
            "book": args.book,
            "chapter_id": chapter_id,
            "chapter_title": chapter.get("title", ""),
            "translation_policy": policy,
            "book_memory": memory,
            "previous_chapter_state": previous_state,
            "items": items,
            "glossary": glossary.get("terms", []),
        })
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
        validate_chapter_review_payload(review, expected)
        fixes = approved_fixes(review["fixes"], autonomous=args.autonomous)
        fixes_path = workspace.reviews_dir / f"{chapter_id}-consistency-fixes.json"
        write_json(fixes_path, {"book": args.book, "items": fixes})
        applied = False
        if args.apply and fixes:
            applied = call_novel_translator("apply-review-fixes", "--book", args.book, "--input", str(fixes_path))
        glossary, term_summary = merge_term_updates(
            glossary,
            review["glossary_delta"].get("add", []) + review["glossary_delta"].get("update", []),
            chunk_id=f"chapter-{chapter_id}",
        )
        memory, memory_summary = merge_memory_delta(memory, review["memory_delta"], chapter_id=chapter_id)
        chapter_state = merge_chapter_state(
            read_json(workspace.chapter_states_dir / f"{chapter_id}.json", {"chapter_id": chapter_id}),
            review["chapter_state"],
            chapter_id=chapter_id,
        )
        chapter_state.update({"status": "reviewed", "checked": len(expected), "fixes": len(fixes)})
        write_json(workspace.chapter_states_dir / f"{chapter_id}.json", chapter_state)
        results.append({"chapter_id": chapter_id, "checked": len(expected), "issues": len(review["fixes"]), "fixes": len(fixes), "applied": applied, "term_updates": term_summary, "memory_delta": memory_summary})

    write_json(workspace.glossary_path, glossary)
    write_json(workspace.book_memory_path, memory)
    terms_path = workspace.data_dir / "novel-translator-terms.json"
    write_json(terms_path, novel_translator_terms(glossary))
    terminology = call_novel_translator("import-terminology", "--book", args.book, "--input", str(terms_path))
    quality = call_novel_translator("quality-report", "--book", args.book)
    report = {"book": args.book, "chapters": results, "terminology": terminology, "quality": quality, "updated_at": utc_now()}
    if args.global_consistency:
        chapter_states = []
        for chapter in chapters:
            chapter_id = str(chapter.get("id", ""))
            state = read_json(workspace.chapter_states_dir / f"{chapter_id}.json", None)
            if state:
                chapter_states.append(state)
        global_input = workspace.reviews_dir / "global-consistency-input.json"
        global_output = workspace.reviews_dir / "global-consistency-output.json"
        write_json(global_input, {
            "book": args.book,
            "chapter_ids": [str(chapter.get("id", "")) for chapter in chapters],
            "glossary": glossary,
            "book_memory": memory,
            "chapter_states": chapter_states,
        })
        run_codex_global_consistency_review(global_input, global_output)
        global_review = read_json(global_output)
        expected_chapters = {str(chapter.get("id", "")) for chapter in chapters}
        global_review = validate_global_consistency_payload(global_review, expected_chapters)
        report["global_consistency"] = global_review
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
