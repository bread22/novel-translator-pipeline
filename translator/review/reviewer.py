from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from translator.core.config import load_config, setting
from translator.core.novel_tool import call_novel_translator
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
from translator.providers.registry import get_provider


ROOT = Path(__file__).resolve().parents[2]
CHAPTER_SCHEMA = ROOT / "schemas" / "chapter-review-output.schema.json"
GLOBAL_SCHEMA = ROOT / "schemas" / "global-consistency-output.schema.json"

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


def manifest_path(book: str) -> Path:
    from translator.core.novel_tool import NOVEL_TRANSLATOR_ROOT
    return NOVEL_TRANSLATOR_ROOT / "data" / "books" / book / "manifest.json"


def paragraph_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(paragraph["id"]): paragraph
        for chapter in manifest.get("chapters", [])
        for paragraph in chapter.get("paragraphs", [])
        if isinstance(paragraph, dict) and paragraph.get("id")
    }


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


def missing_checked_ids(payload: dict[str, Any], expected_ids: set[str]) -> set[str]:
    checked = payload.get("checked_ids", []) if isinstance(payload, dict) else []
    return expected_ids - {str(item) for item in checked}


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


def _selected_backend(backend: str | None = None) -> str:
    config = load_config()
    return (backend or setting(config, "roles.reviewer", "REVIEWER")).strip()


def _review_backends(backend: str | None = None) -> list[str]:
    config = load_config()
    primary = (backend or setting(config, "roles.reviewer", "REVIEWER")).strip()
    fallbacks = [
        str(item).strip()
        for item in config.get("roles", {}).get("fallback_reviewers", [])
        if str(item).strip() and str(item).strip() != primary
    ]
    if not fallbacks:
        fallbacks = [
            str(item).strip()
            for item in config.get("roles", {}).get("fallback_translators", [])
            if str(item).strip() and str(item).strip() != primary
        ]
        primary_trans = str(config.get("roles", {}).get("primary_translator", "")).strip()
        if primary_trans and primary_trans != primary and primary_trans not in fallbacks:
            fallbacks.append(primary_trans)
        if "muse" not in fallbacks and primary != "muse":
            fallbacks.append("muse")
    return [primary] + fallbacks


def check_reviewer(timeout: int = 60, *, backend: str | None = None) -> dict[str, Any]:
    selected = _selected_backend(backend)
    try:
        provider = get_provider(selected)
        return provider.health_check(timeout=timeout)
    except Exception as exc:
        return {"name": f"reviewer:{selected}", "status": "error", "error": str(exc)}


def merge_chapter_reviews(primary_review: dict[str, Any], secondary_review: dict[str, Any]) -> dict[str, Any]:
    checked_a = primary_review.get("checked_ids", []) or []
    checked_b = secondary_review.get("checked_ids", []) or []
    merged_checked = sorted(set(checked_a) | set(checked_b))

    fixes_a = primary_review.get("fixes", []) or []
    fixes_b = secondary_review.get("fixes", []) or []
    by_id_a = {str(item.get("id", "")): item for item in fixes_a if isinstance(item, dict) and item.get("id")}
    by_id_b = {str(item.get("id", "")): item for item in fixes_b if isinstance(item, dict) and item.get("id")}

    all_fix_ids = sorted(set(by_id_a) | set(by_id_b))
    merged_fixes = []

    for fix_id in all_fix_ids:
        in_a = by_id_a.get(fix_id)
        in_b = by_id_b.get(fix_id)
        if in_a and in_b:
            conf_a = float(in_a.get("confidence", 0) or 0)
            conf_b = float(in_b.get("confidence", 0) or 0)
            chosen = dict(in_a if conf_a >= conf_b else in_b)
            chosen["confidence"] = max(conf_a, conf_b, 0.95)
            chosen["consensus"] = True
            chosen["reporters"] = ["primary", "secondary"]
            merged_fixes.append(chosen)
        elif in_a:
            item = dict(in_a)
            item["consensus"] = False
            item["reporters"] = ["primary"]
            merged_fixes.append(item)
        else:
            item = dict(in_b)
            item["consensus"] = False
            item["reporters"] = ["secondary"]
            merged_fixes.append(item)

    gloss_a = primary_review.get("glossary_delta", {}) or {}
    gloss_b = secondary_review.get("glossary_delta", {}) or {}
    add_a = gloss_a.get("add", []) if isinstance(gloss_a, dict) else []
    add_b = gloss_b.get("add", []) if isinstance(gloss_b, dict) else []
    seen_sources = set()
    merged_add = []
    for item in add_a + add_b:
        if isinstance(item, dict) and item.get("source"):
            src = str(item["source"]).strip()
            if src not in seen_sources:
                seen_sources.add(src)
                merged_add.append(item)
    merged_glossary_delta = {"add": merged_add}

    mem_a = primary_review.get("memory_delta", {}) or {}
    mem_b = secondary_review.get("memory_delta", {}) or {}
    merged_memory = {**mem_b, **mem_a}

    state_a = primary_review.get("chapter_state", {}) or {}
    state_b = secondary_review.get("chapter_state", {}) or {}
    merged_state = {**state_b, **state_a}

    return {
        "checked_ids": merged_checked,
        "fixes": merged_fixes,
        "glossary_delta": merged_glossary_delta,
        "memory_delta": merged_memory,
        "chapter_state": merged_state,
        "dual_review": {
            "enabled": True,
            "primary_fixes_count": len(fixes_a),
            "secondary_fixes_count": len(fixes_b),
            "consensus_fixes_count": sum(1 for f in merged_fixes if f.get("consensus")),
            "merged_fixes_count": len(merged_fixes),
        },
    }


def _execute_review_with_fallbacks(
    kind: str,
    input_payload: dict[str, Any],
    schema_path: Path,
    autonomous: bool = False,
    backend: str | None = None,
) -> dict[str, Any]:
    backends = _review_backends(backend)
    last_exc = None
    for candidate in backends:
        try:
            provider = get_provider(candidate)
            return provider.review(kind, input_payload, schema_path, autonomous=autonomous)
        except Exception as exc:
            last_exc = exc
            continue
    raise RuntimeError(f"所有审阅端均失败 (kind={kind}, primary={backend}): {last_exc}") from last_exc


def run_chapter_review(
    input_path: Path,
    output_path: Path,
    autonomous: bool = False,
    *,
    backend: str | None = None,
    secondary_backend: str | None = None,
    dual_review: bool | None = None,
) -> None:
    try:
        input_payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Reviewer input is invalid: {input_path}: {exc}") from exc

    config = load_config()
    is_dual = (
        dual_review
        if dual_review is not None
        else (
            bool(config.get("roles", {}).get("dual_review", False))
            or bool(os.environ.get("DUAL_REVIEW", "").lower() in {"1", "true", "yes", "on"})
        )
    )
    primary_cand = (backend or setting(config, "roles.reviewer", "REVIEWER")).strip()
    sec_cand = (
        secondary_backend
        or config.get("roles", {}).get("secondary_reviewer", "")
        or os.environ.get("SECONDARY_REVIEWER", "")
    ).strip()

    if not is_dual or not sec_cand or sec_cand == primary_cand:
        payload = _execute_review_with_fallbacks("chapter", input_payload, CHAPTER_SCHEMA, autonomous, backend=primary_cand)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return

    primary_payload = None
    secondary_payload = None
    try:
        primary_payload = _execute_review_with_fallbacks("chapter", input_payload, CHAPTER_SCHEMA, autonomous, backend=primary_cand)
    except Exception:
        pass

    try:
        secondary_payload = _execute_review_with_fallbacks("chapter", input_payload, CHAPTER_SCHEMA, autonomous, backend=sec_cand)
    except Exception:
        pass

    if primary_payload and secondary_payload:
        merged_payload = merge_chapter_reviews(primary_payload, secondary_payload)
    elif primary_payload:
        merged_payload = primary_payload
    elif secondary_payload:
        merged_payload = secondary_payload
    else:
        raise RuntimeError(f"双审阅端均失败: {input_path.name}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_global_consistency_review(input_path: Path, output_path: Path, *, backend: str | None = None) -> None:
    try:
        input_payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Reviewer input is invalid: {input_path}: {exc}") from exc
    payload = _execute_review_with_fallbacks("global", input_payload, GLOBAL_SCHEMA, autonomous=False, backend=backend)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def review_book(
    *,
    book: str,
    name: str,
    output_root: Path,
    chapter_id: str | None = None,
    global_consistency: bool = False,
    translation_policy: Path | None = None,
    apply: bool = False,
    autonomous: bool = False,
    export: bool = False,
    reviewer: str | None = None,
) -> dict[str, Any]:
    workspace = BookWorkspace.at(output_root, name)
    workspace.initialize(book_id=book)
    manifest = read_json(manifest_path(book))
    all_chapters = manifest.get("chapters", [])
    chapters = all_chapters
    if chapter_id:
        chapters = [c for c in chapters if c.get("id") == chapter_id]
    if not chapters:
        raise ValueError("没有匹配的章节")

    snapshot = call_novel_translator("snapshot", "--book", book, "--name", "before-chapter-consistency")
    write_json(workspace.snapshots_dir / "before-chapter-consistency.json", snapshot)
    glossary = read_json(workspace.glossary_path, {"book": book, "terms": [], "conflicts": []})
    memory = read_json(workspace.book_memory_path, empty_book_memory(book))
    policy_path = translation_policy or ROOT / "docs" / "prompts" / "translation-policy.md"
    policy = policy_path.read_text(encoding="utf-8") if policy_path.exists() else ""
    results = []
    for chapter in chapters:
        items = [
            {"id": p["id"], "source": p.get("source", ""), "translated": p.get("translated", "")}
            for p in chapter.get("paragraphs", [])
            if str(p.get("translated", "")).strip()
        ]
        if not items:
            continue
        c_id = str(chapter["id"])
        input_path = workspace.reviews_dir / f"{c_id}-consistency-input.json"
        output_path = workspace.reviews_dir / f"{c_id}-consistency-output.json"
        previous_state = {}
        index = all_chapters.index(chapter)
        if index > 0:
            previous_id = str(all_chapters[index - 1].get("id", ""))
            previous_state = read_json(workspace.chapter_states_dir / f"{previous_id}.json", {}) or {}
        write_json(input_path, {
            "book": book,
            "chapter_id": c_id,
            "chapter_title": chapter.get("title", ""),
            "translation_policy": policy,
            "book_memory": memory,
            "previous_chapter_state": previous_state,
            "items": items,
            "glossary": glossary.get("terms", []),
        })
        run_chapter_review(input_path, output_path, autonomous=autonomous, backend=reviewer)
        review = read_json(output_path)
        if not isinstance(review, dict):
            raise ValueError(f"章节审阅结果不是 JSON 对象：{output_path}")
        expected = {item["id"] for item in items}
        for retry in range(1, 3):
            if not missing_checked_ids(review, expected):
                break
            retry_path = workspace.reviews_dir / f"{c_id}-consistency-retry-{retry:02d}.json"
            run_chapter_review(input_path, retry_path, autonomous=autonomous, backend=reviewer)
            review = read_json(retry_path)
        validate_chapter_review_payload(review, expected)
        fixes = approved_fixes(review["fixes"], autonomous=autonomous)
        fixes_path = workspace.reviews_dir / f"{c_id}-consistency-fixes.json"
        write_json(fixes_path, {"book": book, "items": fixes})
        applied_fixes = False
        if apply and fixes:
            applied_fixes = call_novel_translator("apply-review-fixes", "--book", book, "--input", str(fixes_path))
            verify_applied_fixes(read_json(manifest_path(book)), fixes)
        glossary, term_summary = merge_term_updates(
            glossary,
            review["glossary_delta"].get("add", []) + review["glossary_delta"].get("update", []),
            c_id,
        )
        memory, mem_summary = merge_memory_delta(memory, review["memory_delta"], c_id)
        write_json(workspace.glossary_path, glossary)
        write_json(workspace.book_memory_path, memory)
        write_json(workspace.novel_translator_terms_path, novel_translator_terms(glossary))
        chapter_state = merge_chapter_state(c_id, str(chapter.get("title", "")), review["chapter_state"])
        write_json(workspace.chapter_states_dir / f"{c_id}.json", chapter_state)
        report_path = workspace.reports_dir / f"{c_id}.json"
        write_json(report_path, {
            "book": book,
            "chapter_id": c_id,
            "reviewed_at": utc_now(),
            "checked_paragraphs": len(expected),
            "reported_issues": len(review["fixes"]),
            "applied_fixes": len(fixes) if apply else 0,
            "approved_fixes": fixes,
            "term_summary": term_summary,
            "memory_summary": mem_summary,
            "applied": applied_fixes,
        })
        results.append({
            "chapter_id": c_id,
            "issues": len(review["fixes"]),
            "fixes": len(fixes),
            "applied": applied_fixes,
        })

    global_report = None
    if global_consistency:
        global_input = workspace.reviews_dir / "global-consistency-input.json"
        global_output = workspace.reviews_dir / "global-consistency-output.json"
        states = {
            c["id"]: read_json(workspace.chapter_states_dir / f"{c['id']}.json", {})
            for c in all_chapters
            if (workspace.chapter_states_dir / f"{c['id']}.json").exists()
        }
        write_json(global_input, {
            "book": book,
            "chapters": [{"id": c["id"], "title": c.get("title", ""), "state": states.get(c["id"], {})} for c in all_chapters],
            "book_memory": memory,
            "glossary": glossary.get("terms", []),
        })
        run_global_consistency_review(global_input, global_output, backend=reviewer)
        global_payload = read_json(global_output)
        validate_global_consistency_payload(global_payload, {str(c["id"]) for c in all_chapters})
        global_report = workspace.reports_dir / "global-consistency.json"
        write_json(global_report, {
            "book": book,
            "reviewed_at": utc_now(),
            "conflicts": global_payload.get("conflicts", []),
            "recommendations": global_payload.get("recommendations", []),
        })

    if export:
        call_novel_translator("export", "--book", book, "--format", "epub", "--output", str(workspace.epub_path), "--monolingual")
    return {
        "status": "ok",
        "book": book,
        "name": name,
        "reviewed_chapters": len(results),
        "results": results,
        "global_consistency": str(global_report) if global_report else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chapter consistency reviewer")
    parser.add_argument("--book", required=True, help="Novel Translator book id")
    parser.add_argument("--name", required=True, help="output/ 下的书籍目录名和中文书名")
    parser.add_argument("--output-root", type=Path, default=ROOT / "output")
    parser.add_argument("--chapter", default=None, help="只审阅特定章节 ID")
    parser.add_argument("--global-consistency", action="store_true", help="整书全部章节审阅完成后执行全书一致性检查")
    parser.add_argument("--translation-policy", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="应用高置信度客观修复")
    parser.add_argument("--autonomous", action="store_true", help="全自动模式，仅对客观高置信度修复置 auto_apply=true")
    parser.add_argument("--export", action="store_true", help="审阅完成后导出 EPUB")
    parser.add_argument("--reviewer", default=None, help="审阅 backend 名称")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    review_book(
        book=args.book,
        name=args.name,
        output_root=args.output_root,
        chapter_id=args.chapter,
        global_consistency=args.global_consistency,
        translation_policy=args.translation_policy,
        apply=args.apply,
        autonomous=args.autonomous,
        export=args.export,
        reviewer=args.reviewer,
    )
    return 0


cli_main = main


if __name__ == "__main__":
    sys.exit(main())

