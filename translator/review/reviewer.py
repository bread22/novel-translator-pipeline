from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Any, Callable

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
from translator.review.models import ChapterReviewOutput, GlobalReviewOutput


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
OBJECTIVE_SEVERITIES = {"critical", "major", "minor"}
CATEGORY_ALIASES = {
    "translation_error": "mistranslation",
}


import re

JAPANESE_KANA_REGEX = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")


def has_japanese_kana(text: str) -> bool:
    return bool(JAPANESE_KANA_REGEX.search(text))


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


def approved_fixes(
    items: list[dict[str, Any]],
    threshold: float = 0.9,
    *,
    autonomous: bool = False,
    current_translations: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    approved: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        item_id = str(item.get("id", ""))
        replacement = str(item.get("replacement", "") or item.get("approved_translation", "")).strip()
        is_new_contract = "category" in item or "replacement" in item
        mandatory_kana_cleanup = bool(
            current_translations
            and has_japanese_kana(str(current_translations.get(item_id, "")))
        )
        category = CATEGORY_ALIASES.get(str(item.get("category", "")), str(item.get("category", "")))
        if mandatory_kana_cleanup:
            category = "policy_violation"
            item["severity"] = "critical"
            item["auto_apply"] = True
        if is_new_contract or mandatory_kana_cleanup:
            item["category"] = category
        if is_new_contract and (
            category not in OBJECTIVE_CATEGORIES
            or str(item.get("severity", "")) not in OBJECTIVE_SEVERITIES
        ):
            continue
        # Quality Guardrail: Strictly reject any replacement that contains leftover Japanese kana
        if has_japanese_kana(replacement):
            continue
        conf = float(item.get("confidence", 0) or 0)
        is_consensus = bool(item.get("consensus"))
        # Category-based Dynamic Threshold: Objective categories qualify at 0.80+, consensus auto-qualifies
        req_threshold = 0.8 if (is_new_contract and category in OBJECTIVE_CATEGORIES) else threshold
        meets_approval_threshold = mandatory_kana_cleanup or is_consensus or (
            (autonomous or item.get("auto_apply") is True)
            and conf >= req_threshold
        )
        if meets_approval_threshold and replacement:
            item["approved_translation"] = replacement
            item["replacement"] = replacement
            approved.append(item)
    return approved


def missing_checked_ids(payload: dict[str, Any], expected_ids: set[str]) -> set[str]:
    checked = payload.get("checked_ids", []) if isinstance(payload, dict) else []
    return expected_ids - {str(item) for item in checked}


def validate_chapter_review_payload(payload: dict[str, Any], expected_ids: set[str]) -> dict[str, Any]:
    try:
        normalized = ChapterReviewOutput.model_validate(payload).model_dump(exclude_none=True)
    except Exception as exc:
        raise ValueError(f"章节审阅结果未通过完整 Schema 校验：{exc}") from exc
    raw_checked_ids = [str(item) for item in normalized["checked_ids"]]
    # Filter checked_ids to expected IDs and deduplicate preserving order
    seen = set()
    checked_ids = []
    for cid in raw_checked_ids:
        if cid in expected_ids and cid not in seen:
            seen.add(cid)
            checked_ids.append(cid)
    normalized["checked_ids"] = checked_ids
    
    # Filter fixes to only valid expected IDs and sanitize Japanese kana hallucinations
    sanitized_fixes = []
    for item in normalized["fixes"]:
        if not isinstance(item, dict) or str(item.get("id", "")) not in expected_ids:
            continue
        rep = str(item.get("replacement", "") or item.get("approved_translation", "")).strip()
        item["category"] = CATEGORY_ALIASES.get(str(item.get("category", "")), str(item.get("category", "")))
        if rep and has_japanese_kana(rep):
            item["auto_apply"] = False
            item["confidence"] = min(float(item.get("confidence", 0) or 0), 0.3)
            item["invalid_reason"] = "修正译文中残留未翻译日文假名，已拦截并禁用自动写回"
        sanitized_fixes.append(item)
    normalized["fixes"] = sanitized_fixes

    received = set(checked_ids)
    missing = sorted(expected_ids - received)
    if missing:
        raise ValueError(f"章节审阅结果段落不匹配；checked_ids 缺少 ID：{', '.join(missing)}")
    return ChapterReviewOutput.model_validate(normalized).model_dump(exclude_none=True)


def validate_global_consistency_payload(payload: dict[str, Any], expected_chapter_ids: set[str]) -> dict[str, Any]:
    try:
        normalized = GlobalReviewOutput.model_validate(payload).model_dump(exclude_none=True)
    except Exception as exc:
        raise ValueError(f"全书一致性结果未通过完整 Schema 校验：{exc}") from exc
    checked = [str(item) for item in normalized["checked_chapters"]]
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
    return normalized


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
    return [primary] + fallbacks


def check_reviewer(timeout: int = 60, *, backend: str | None = None) -> dict[str, Any]:
    selected = _selected_backend(backend)
    try:
        provider = get_provider(selected)
        return provider.health_check(timeout=timeout)
    except Exception as exc:
        return {"name": f"reviewer:{selected}", "status": "error", "error": str(exc)}


def _normalized_review(payload: dict[str, Any]) -> dict[str, Any]:
    return ChapterReviewOutput.model_validate(payload).model_dump(exclude_none=True)


def _merge_delta(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    identity_key: str,
    left_reporter: str | None = None,
    right_reporter: str | None = None,
    prefer_right: bool = False,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {"add": [], "update": [], "conflicts": []}
    for section in ("add", "update", "conflicts"):
        by_identity: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for items, reporter, is_right in (
            (left.get(section, []) or [], left_reporter, False),
            (right.get(section, []) or [], right_reporter, True),
        ):
            for raw in items:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                identity = str(item.get(identity_key) or item.get("key") or json.dumps(item, ensure_ascii=False, sort_keys=True)).strip()
                if not identity:
                    continue
                reporters = list(item.get("reporters", []) or [])
                if reporter and reporter not in reporters:
                    reporters.append(reporter)
                item["reporters"] = reporters
                if identity not in by_identity:
                    by_identity[identity] = item
                    order.append(identity)
                    continue
                existing = by_identity[identity]
                existing_reporters = list(existing.get("reporters", []) or [])
                for name in reporters:
                    if name not in existing_reporters:
                        existing_reporters.append(name)
                if (prefer_right and is_right) or float(item.get("confidence", 0) or 0) > float(existing.get("confidence", 0) or 0):
                    item["reporters"] = existing_reporters
                    by_identity[identity] = item
                else:
                    existing["reporters"] = existing_reporters
        result[section] = [by_identity[identity] for identity in order]
    return result


def _merge_chapter_state(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    summaries = [str(value.get("summary", "")).strip() for value in (left, right)]
    merged = {
        "summary": "\n".join(item for item in summaries if item),
        "important_changes": list(dict.fromkeys((left.get("important_changes", []) or []) + (right.get("important_changes", []) or []))),
        "active_entities": list(dict.fromkeys((left.get("active_entities", []) or []) + (right.get("active_entities", []) or []))),
        "location": str(right.get("location") or left.get("location") or ""),
        "timeline": list(dict.fromkeys((left.get("timeline", []) or []) + (right.get("timeline", []) or []))),
        "open_questions": list(dict.fromkeys((left.get("open_questions", []) or []) + (right.get("open_questions", []) or []))),
    }
    return merged


def edit_distance(s1: str, s2: str) -> int:
    """Calculate Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return edit_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]


def merge_chapter_reviews(
    primary_review: dict[str, Any],
    secondary_review: dict[str, Any],
    *,
    current_translations: dict[str, str] | None = None,
) -> dict[str, Any]:
    primary_review = _normalized_review(primary_review)
    secondary_review = _normalized_review(secondary_review)
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
            rep_a = str(in_a.get("replacement", "")).strip()
            rep_b = str(in_b.get("replacement", "")).strip()
            kana_a = has_japanese_kana(rep_a)
            kana_b = has_japanese_kana(rep_b)
            if kana_a and not kana_b:
                chosen = dict(in_b)
            elif kana_b and not kana_a:
                chosen = dict(in_a)
            else:
                # Least Invasive Priority: choose the fix that makes the least extraneous modifications to the original translation
                orig_text = str((current_translations or {}).get(fix_id, "")).strip()
                if orig_text and rep_a and rep_b:
                    dist_a = edit_distance(rep_a, orig_text)
                    dist_b = edit_distance(rep_b, orig_text)
                    if dist_a < dist_b:
                        chosen = dict(in_a)
                    elif dist_b < dist_a:
                        chosen = dict(in_b)
                    else:
                        chosen = dict(in_a if conf_a >= conf_b else in_b)
                else:
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
            item = dict(in_b or {})
            item["consensus"] = False
            item["reporters"] = ["secondary"]
            merged_fixes.append(item)

    merged_glossary_delta = _merge_delta(
        primary_review["glossary_delta"], secondary_review["glossary_delta"],
        identity_key="source", left_reporter="primary", right_reporter="secondary",
    )
    merged_memory = _merge_delta(
        primary_review["memory_delta"], secondary_review["memory_delta"],
        identity_key="key", left_reporter="primary", right_reporter="secondary",
    )

    state_a = primary_review.get("chapter_state", {}) or {}
    state_b = secondary_review.get("chapter_state", {}) or {}
    merged_state = _merge_chapter_state(state_a, state_b)

    return _normalized_review({
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
    })


REVIEW_CHUNK_MAX_PARAGRAPHS = 100


def dynamic_review_timeout(input_payload: dict[str, Any]) -> int:
    """Calculate dynamic timeout linearly based on source character volume."""
    items = input_payload.get("items", [])
    total_chars = sum(len(str(item.get("source", ""))) for item in items if isinstance(item, dict))
    return max(60, min(360, 45 + int(total_chars * 0.05)))


def _execute_review_with_fallbacks(
    kind: str,
    input_payload: dict[str, Any],
    schema_path: Path,
    autonomous: bool = False,
    backend: str | None = None,
    timeout: int | None = None,
    cancel_check: Callable[[], None] | None = None,
    role: str = "primary",
    on_reviewer_status: Callable[[dict[str, Any]], None] | None = None,
    chunk_index: int = 1,
    total_chunks: int = 1,
    split_depth: int = 0,
    split_path: str = "root",
    attempt_counters: dict[str, int] | None = None,
    attempt_lock: threading.Lock | None = None,
) -> dict[str, Any]:
    backends = _review_backends(backend)
    last_exc = None
    effective_timeout = timeout or dynamic_review_timeout(input_payload)
    for candidate_index, candidate in enumerate(backends, start=1):
        if cancel_check:
            cancel_check()
        if attempt_counters is not None:
            if attempt_lock is None:
                attempt_counters[role] = attempt_counters.get(role, 0) + 1
                attempt = attempt_counters[role]
            else:
                with attempt_lock:
                    attempt_counters[role] = attempt_counters.get(role, 0) + 1
                    attempt = attempt_counters[role]
        else:
            attempt = candidate_index
        status_base = {
            "role": role,
            "backend": candidate,
            "attempt": attempt,
            "candidate_index": candidate_index,
            "candidate_total": len(backends),
            "chunk_index": chunk_index,
            "total_chunks": total_chunks,
            "split_depth": split_depth,
            "split_path": split_path,
            "timeout_seconds": effective_timeout,
        }
        if on_reviewer_status:
            on_reviewer_status({**status_base, "status": "reviewing"})
        try:
            provider = get_provider(candidate)
            result = provider.review(kind, input_payload, schema_path, autonomous=autonomous, timeout=effective_timeout)
            if on_reviewer_status:
                on_reviewer_status({**status_base, "status": "completed"})
            return result
        except Exception as exc:
            last_exc = exc
            if on_reviewer_status:
                on_reviewer_status({**status_base, "status": "failed", "error": str(exc)})
            continue
    raise RuntimeError(f"所有审阅端均失败 (kind={kind}, primary={backend}): {last_exc}") from last_exc


def _update_rolling_payload(base_payload: dict[str, Any], chunk_review: dict[str, Any]) -> dict[str, Any]:
    """Forward newly extracted terms, character memory, and narrative state to subsequent chunks."""
    rolling = dict(base_payload)
    chunk_review = _normalized_review(chunk_review)

    # 1. Forward Glossary
    current_glossary = list(rolling.get("glossary", []))
    term_index = {str(term.get("source", "")).strip(): index for index, term in enumerate(current_glossary) if isinstance(term, dict)}
    for term in chunk_review["glossary_delta"]["add"] + chunk_review["glossary_delta"]["update"]:
        if isinstance(term, dict) and term.get("source"):
            src = str(term["source"]).strip()
            if src in term_index:
                current_glossary[term_index[src]] = {**current_glossary[term_index[src]], **term}
            elif src:
                term_index[src] = len(current_glossary)
                current_glossary.append(term)
    rolling["glossary"] = current_glossary

    # 2. Forward Book Memory
    current_memory = dict(rolling.get("book_memory", {}))
    mem_delta = chunk_review.get("memory_delta", {})
    if isinstance(mem_delta, dict):
        merged_entries = {entry.get("key"): entry for entry in current_memory.get("entries", []) if isinstance(entry, dict) and entry.get("key")}
        for entry in mem_delta.get("add", []) + mem_delta.get("update", []):
            if isinstance(entry, dict) and entry.get("key"):
                existing = merged_entries.get(entry["key"], {})
                merged_entries[entry["key"]] = {**existing, **entry}
        current_memory["entries"] = list(merged_entries.values())
        conflicts = list(current_memory.get("conflicts", []))
        for conflict in mem_delta.get("conflicts", []):
            if conflict not in conflicts:
                conflicts.append(conflict)
        current_memory["conflicts"] = conflicts
        rolling["book_memory"] = current_memory

    # 3. Forward Chapter State (Narrative summary of immediate preceding chunk)
    chunk_state = chunk_review.get("chapter_state")
    if isinstance(chunk_state, dict) and chunk_state:
        rolling["previous_chapter_state"] = chunk_state

    return rolling


def _combine_chunk_reviews(chunk_a: dict[str, Any], chunk_b: dict[str, Any]) -> dict[str, Any]:
    """Combine two sequential chunk reviews into a unified review structure."""
    chunk_a = _normalized_review(chunk_a)
    chunk_b = _normalized_review(chunk_b)
    # Checked IDs concatenated preserving order
    seen_ids = set()
    merged_checked = []
    for cid in (chunk_a.get("checked_ids", []) or []) + (chunk_b.get("checked_ids", []) or []):
        if cid not in seen_ids:
            seen_ids.add(cid)
            merged_checked.append(cid)

    # Fixes concatenated
    merged_fixes = (chunk_a.get("fixes", []) or []) + (chunk_b.get("fixes", []) or [])

    merged_glossary_delta = _merge_delta(chunk_a["glossary_delta"], chunk_b["glossary_delta"], identity_key="source", prefer_right=True)
    merged_memory = _merge_delta(chunk_a["memory_delta"], chunk_b["memory_delta"], identity_key="key", prefer_right=True)

    # Chapter State synthesis
    merged_state = _merge_chapter_state(chunk_a["chapter_state"], chunk_b["chapter_state"])

    return _normalized_review({
        "checked_ids": merged_checked,
        "fixes": merged_fixes,
        "glossary_delta": merged_glossary_delta,
        "memory_delta": merged_memory,
        "chapter_state": merged_state,
    })


def _execute_single_segment_review(
    input_payload: dict[str, Any],
    schema_path: Path,
    autonomous: bool = False,
    backend: str | None = None,
    secondary_backend: str | None = None,
    is_dual: bool = False,
    on_reviewer_status: Callable[[dict[str, Any]], None] | None = None,
    cancel_check: Callable[[], None] | None = None,
    timeout: int | None = None,
    chunk_index: int = 1,
    total_chunks: int = 1,
    split_depth: int = 0,
    split_path: str = "root",
    attempt_counters: dict[str, int] | None = None,
    attempt_lock: threading.Lock | None = None,
    role: str = "primary",
) -> dict[str, Any]:
    """Execute single segment review with dual review (if configured) and backend failover."""
    primary_cand = backend
    sec_cand = secondary_backend

    if not is_dual or not sec_cand or sec_cand == primary_cand:
        if cancel_check:
            cancel_check()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="Reviewer")
        future = executor.submit(
            _execute_review_with_fallbacks,
            "chapter",
            input_payload,
            schema_path,
            autonomous=autonomous,
            backend=primary_cand,
            cancel_check=cancel_check,
            timeout=timeout,
            role=role,
            on_reviewer_status=on_reviewer_status,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            split_depth=split_depth,
            split_path=split_path,
            attempt_counters=attempt_counters,
            attempt_lock=attempt_lock,
        )
        try:
            while not future.done():
                if cancel_check:
                    cancel_check()
                wait({future}, timeout=0.1)
            result = future.result()
        except Exception:
            executor.shutdown(wait=False, cancel_futures=True)
            if cancel_check:
                try:
                    cancel_check()
                except Exception:
                    if on_reviewer_status:
                        on_reviewer_status({
                            "role": role, "backend": primary_cand or "", "status": "cancelled",
                            "chunk_index": chunk_index, "total_chunks": total_chunks,
                            "split_depth": split_depth, "split_path": split_path,
                        })
                    raise
            raise
        executor.shutdown(wait=True)
        return result

    payloads: dict[str, dict[str, Any]] = {}
    errors = []
    reviewers = {"primary": primary_cand, "secondary": sec_cand}
    if cancel_check:
        cancel_check()
    executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="DualReviewer")
    futures: dict[Future[dict[str, Any]], tuple[str, str | None]] = {
        executor.submit(
            _execute_review_with_fallbacks,
            "chapter",
            input_payload,
            schema_path,
            autonomous=autonomous,
            backend=candidate,
            cancel_check=cancel_check,
            timeout=timeout,
            role=role,
            on_reviewer_status=on_reviewer_status,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            split_depth=split_depth,
            split_path=split_path,
            attempt_counters=attempt_counters,
            attempt_lock=attempt_lock,
        ): (role, candidate)
        for role, candidate in reviewers.items()
    }
    pending = set(futures)
    try:
        while pending:
            if cancel_check:
                cancel_check()
            done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                role, candidate = futures[future]
                try:
                    payloads[role] = future.result()
                except Exception as exc:
                    errors.append(f"{role} ({candidate}) error: {exc}")
    except Exception:
        for future in pending:
            role, candidate = futures[future]
            if on_reviewer_status:
                on_reviewer_status({"role": role, "backend": candidate or "", "status": "cancelled"})
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    executor.shutdown(wait=True)

    primary_payload = payloads.get("primary")
    secondary_payload = payloads.get("secondary")

    if primary_payload and secondary_payload:
        raw_items = input_payload.get("items", []) if isinstance(input_payload, dict) else []
        current_trans = {
            str(item.get("id", "")): str(item.get("translated", ""))
            for item in raw_items
            if isinstance(item, dict) and item.get("id")
        }
        return merge_chapter_reviews(primary_payload, secondary_payload, current_translations=current_trans)
    raise RuntimeError(f"双审阅未完整完成: {'; '.join(errors)}")


def _execute_segment_with_adaptive_split(
    base_payload: dict[str, Any],
    items: list[dict[str, Any]],
    schema_path: Path,
    autonomous: bool = False,
    backend: str | None = None,
    secondary_backend: str | None = None,
    is_dual: bool = False,
    on_reviewer_status: Callable[[dict[str, Any]], None] | None = None,
    cancel_check: Callable[[], None] | None = None,
    timeout: int | None = None,
    chunk_index: int = 1,
    total_chunks: int = 1,
    split_path: str = "root",
    attempt_counters: dict[str, int] | None = None,
    attempt_lock: threading.Lock | None = None,
    role: str = "primary",
    depth: int = 0,
    max_depth: int = 4,
) -> dict[str, Any]:
    """Execute review for items. If failure occurs and len(items) > 1, recursively binary split."""
    if not items:
        return _normalized_review({
            "checked_ids": [],
            "fixes": [],
            "glossary_delta": {"add": [], "update": [], "conflicts": []},
            "memory_delta": {"add": [], "update": [], "conflicts": []},
            "chapter_state": base_payload.get("previous_chapter_state") or {},
        })

    if is_dual and secondary_backend and secondary_backend != backend:
        payloads: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="DualReviewer")
        futures: dict[Future[dict[str, Any]], tuple[str, str | None]] = {
            executor.submit(
                _execute_segment_with_adaptive_split,
                base_payload,
                items,
                schema_path,
                autonomous=autonomous,
                backend=candidate,
                secondary_backend=None,
                is_dual=False,
                on_reviewer_status=on_reviewer_status,
                cancel_check=cancel_check,
                timeout=timeout,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                split_path=split_path,
                attempt_counters=attempt_counters,
                attempt_lock=attempt_lock,
                role=reviewer_role,
                depth=depth,
                max_depth=max_depth,
            ): (reviewer_role, candidate)
            for reviewer_role, candidate in {"primary": backend, "secondary": secondary_backend}.items()
        }
        pending = set(futures)
        try:
            while pending:
                if cancel_check:
                    cancel_check()
                done, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                for future in done:
                    reviewer_role, candidate = futures[future]
                    try:
                        payloads[reviewer_role] = future.result()
                    except Exception as exc:
                        errors.append(f"{reviewer_role} ({candidate}) error: {exc}")
        except Exception:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        executor.shutdown(wait=True)
        primary_payload = payloads.get("primary")
        secondary_payload = payloads.get("secondary")
        if primary_payload and secondary_payload:
            current_trans = {
                str(item.get("id", "")): str(item.get("translated", ""))
                for item in items
                if isinstance(item, dict) and item.get("id")
            }
            return merge_chapter_reviews(primary_payload, secondary_payload, current_translations=current_trans)
        if primary_payload:
            return primary_payload
        if secondary_payload:
            return secondary_payload
        raise RuntimeError(f"双审阅端均失败: {'; '.join(errors)}")

    if cancel_check:
        cancel_check()
    segment_payload = dict(base_payload)
    segment_payload["items"] = items
    expected_ids = {str(item.get("id", "")) for item in items if item.get("id")}

    try:
        res = _execute_single_segment_review(
            segment_payload,
            schema_path,
            autonomous=autonomous,
            backend=backend,
            secondary_backend=secondary_backend,
            is_dual=False,
            on_reviewer_status=on_reviewer_status,
            cancel_check=cancel_check,
            timeout=timeout,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            split_depth=depth,
            split_path=split_path,
            attempt_counters=attempt_counters,
            attempt_lock=attempt_lock,
            role=role,
        )
        res = validate_chapter_review_payload(res, expected_ids)
        return res
    except Exception as exc:
        if len(items) > 1 and depth < max_depth:
            # Recursive binary split on failure: Never skip review!
            midpoint = max(1, len(items) // 2)
            left_items = items[:midpoint]
            right_items = items[midpoint:]

            left_res = _execute_segment_with_adaptive_split(
                base_payload,
                left_items,
                schema_path,
                autonomous=autonomous,
                backend=backend,
                secondary_backend=secondary_backend,
                is_dual=is_dual,
                on_reviewer_status=on_reviewer_status,
                cancel_check=cancel_check,
                timeout=timeout,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                split_path=f"{split_path}.L",
                attempt_counters=attempt_counters,
                attempt_lock=attempt_lock,
                role=role,
                depth=depth + 1,
                max_depth=max_depth,
            )

            rolling_payload = _update_rolling_payload(base_payload, left_res)

            right_res = _execute_segment_with_adaptive_split(
                rolling_payload,
                right_items,
                schema_path,
                autonomous=autonomous,
                backend=backend,
                secondary_backend=secondary_backend,
                is_dual=is_dual,
                on_reviewer_status=on_reviewer_status,
                cancel_check=cancel_check,
                timeout=timeout,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                split_path=f"{split_path}.R",
                attempt_counters=attempt_counters,
                attempt_lock=attempt_lock,
                role=role,
                depth=depth + 1,
                max_depth=max_depth,
            )

            return _combine_chunk_reviews(left_res, right_res)
        else:
            raise exc


def run_chapter_review(
    input_path: Path,
    output_path: Path,
    autonomous: bool = False,
    *,
    backend: str | None = None,
    secondary_backend: str | None = None,
    dual_review: bool | None = None,
    chunk_size: int = REVIEW_CHUNK_MAX_PARAGRAPHS,
    on_reviewer_status: Callable[[dict[str, Any]], None] | None = None,
    cancel_check: Callable[[], None] | None = None,
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

    items = input_payload.get("items", [])
    expected_ids = {str(item["id"]) for item in items if isinstance(item, dict) and item.get("id")}

    chunks = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)] or [[]]
    rolling_payload = dict(input_payload)
    chunk_results = []
    attempt_counters = {"primary": 0, "secondary": 0}
    attempt_lock = threading.Lock()

    for chunk_index, chunk_items in enumerate(chunks, start=1):
        if cancel_check:
            cancel_check()
        timeout_payload = dict(rolling_payload)
        timeout_payload["items"] = chunk_items
        chunk_timeout = dynamic_review_timeout(timeout_payload)
        chunk_res = _execute_segment_with_adaptive_split(
            rolling_payload,
            chunk_items,
            CHAPTER_SCHEMA,
            autonomous=autonomous,
            backend=primary_cand,
            secondary_backend=sec_cand,
            is_dual=is_dual,
            on_reviewer_status=on_reviewer_status,
            cancel_check=cancel_check,
            timeout=chunk_timeout,
            chunk_index=chunk_index,
            total_chunks=len(chunks),
            attempt_counters=attempt_counters,
            attempt_lock=attempt_lock,
        )
        chunk_results.append(chunk_res)
        rolling_payload = _update_rolling_payload(rolling_payload, chunk_res)

    merged_payload = chunk_results[0]
    for next_res in chunk_results[1:]:
        merged_payload = _combine_chunk_reviews(merged_payload, next_res)

    if cancel_check:
        cancel_check()
    merged_payload = validate_chapter_review_payload(merged_payload, expected_ids)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_global_consistency_review(input_path: Path, output_path: Path, *, backend: str | None = None) -> None:
    try:
        input_payload = json.loads(input_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Reviewer input is invalid: {input_path}: {exc}") from exc
    payload = _execute_review_with_fallbacks("global", input_payload, GLOBAL_SCHEMA, autonomous=False, backend=backend)
    expected_chapters = {str(item.get("chapter_id", "")) for item in input_payload.get("chapters", []) if isinstance(item, dict) and item.get("chapter_id")}
    payload = validate_global_consistency_payload(payload, expected_chapters)
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
        previous_state: dict[str, Any] = {}
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
        review = validate_chapter_review_payload(review, expected)
        current_translations = {item["id"]: item["translated"] for item in items}
        fixes = approved_fixes(
            review["fixes"],
            autonomous=autonomous,
            current_translations=current_translations,
        )
        fixes_path = workspace.reviews_dir / f"{c_id}-consistency-fixes.json"
        write_json(fixes_path, {"book": book, "items": fixes})
        applied_fixes: Any = False
        if apply:
            if fixes:
                applied_fixes = call_novel_translator("apply-review-fixes", "--book", book, "--input", str(fixes_path))
            manifest_after_fixes = read_json(manifest_path(book))
            verify_applied_fixes(manifest_after_fixes, fixes)
            remaining_kana = [
                item_id
                for item_id, paragraph in paragraph_map(manifest_after_fixes).items()
                if item_id in expected and has_japanese_kana(str(paragraph.get("translated", "")))
            ]
            if remaining_kana:
                raise ValueError(f"章节 {c_id} 写回后仍残留日文假名：{', '.join(sorted(remaining_kana))}")
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
