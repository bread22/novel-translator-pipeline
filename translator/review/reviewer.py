from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable

from translator.core.config import load_config, setting
from translator.core.job_control import JobCancelled
from translator.core.novel_tool import call_novel_translator
from translator.core.workspace import BookWorkspace, empty_book_memory, read_json, utc_now, write_json
from translator.providers.registry import get_provider
from translator.providers.errors import (
    ProviderConnectionError,
    ProviderHTTPError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from translator.review.models import ChapterReviewOutput, GlobalReviewOutput
from translator.review.context_budget import (
    ReviewContextBudget,
    ReviewContextOverflowError,
    ReviewTargetSplitRequired,
    build_budgeted_review_context,
)


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
REVIEW_FIX_CATEGORIES = OBJECTIVE_CATEGORIES | {"style"}
OBJECTIVE_SEVERITIES = {"critical", "major", "minor"}
CATEGORY_ALIASES = {
    "translation_error": "mistranslation",
}


class ReviewValidationError(ValueError):
    """A shape/content error for which reducing the target segment can help."""


import re
import hashlib

JAPANESE_KANA_REGEX = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
HANGUL_REGEX = re.compile(r"[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7af\ud7b0-\ud7ff]")
MASKING_SYMBOL_REGEX = re.compile(r"[○●×＊※□]")
ASCII_WORD_REGEX = re.compile(r"(?<![A-Za-z])[A-Za-z]{2,}(?![A-Za-z])")
REVIEW_META_REGEX = re.compile(
    r"(?:译者注|审阅|修改(?:为|说明|建议)|可改为|建议译为|replacement|option\s*[a-z]?|答案[一二12]|以下(?:是|为))",
    re.IGNORECASE,
)
MARKDOWN_REGEX = re.compile(r"(?:^|\n)\s*(?:#{1,6}\s|[-*+]\s|```|>\s)|\[[^\]]+\]\([^)]+\)")
MULTIPLE_ANSWER_REGEX = re.compile(r"(?:^|\n)\s*(?:[A-Da-d][.)、]|[12一二][.)、])\s*")


def has_japanese_kana(text: str) -> bool:
    return bool(JAPANESE_KANA_REGEX.search(text))


def has_hangul(text: str) -> bool:
    return bool(HANGUL_REGEX.search(text))


def has_target_script_residue(text: str) -> bool:
    """Return whether a Simplified-Chinese translation still contains Japanese or Korean script."""
    return has_japanese_kana(text) or has_hangul(text)


def has_masking_symbol(text: str) -> bool:
    return bool(MASKING_SYMBOL_REGEX.search(text))


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


def translation_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def replacement_validation_errors(replacement: str) -> list[str]:
    """Return deterministic format/script errors; semantic quality stays with reviewers."""
    errors: list[str] = []
    if not replacement.strip():
        errors.append("empty_replacement")
    if has_target_script_residue(replacement):
        errors.append("target_script_residue")
    if has_masking_symbol(replacement):
        errors.append("masking_symbol")
    if ASCII_WORD_REGEX.search(replacement):
        errors.append("latin_word")
    if REVIEW_META_REGEX.search(replacement):
        errors.append("review_meta_text")
    if MARKDOWN_REGEX.search(replacement):
        errors.append("markdown")
    if MULTIPLE_ANSWER_REGEX.search(replacement) or re.search(r"(?:或者|或译为|二选一|/\s*或\s*/)", replacement):
        errors.append("multiple_answers")
    if "\x00" in replacement or "\r" in replacement:
        errors.append("illegal_format")
    return list(dict.fromkeys(errors))


def evaluate_apply_gate(
    items: list[dict[str, Any]],
    threshold: float = 0.9,
    *,
    autonomous: bool = False,
    current_translations: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate every proposal and retain its final report-facing gate state."""
    evaluated: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        item_id = str(item.get("id", ""))
        operation = str(item.get("operation", "replace") or "replace")
        is_clear = operation == "clear"
        replacement = str(item.get("replacement", "") or item.get("approved_translation", "")).strip()
        is_new_contract = "category" in item or "replacement" in item
        current_text = str(current_translations.get(item_id, "")) if current_translations else ""
        item.setdefault("decision", "FIX_REQUIRED")
        item["apply_state"] = "not_applied"
        item["apply_reason"] = ""
        item["validation_errors"] = []
        if current_translations is not None and item_id not in current_translations:
            item["apply_reason"] = "unknown_target"
            evaluated.append(item)
            continue
        if str(item.get("decision")) != "FIX_REQUIRED":
            item["apply_reason"] = "pass" if item.get("decision") == "PASS" else "report_only"
            evaluated.append(item)
            continue
        if current_translations and not is_clear and replacement == current_text:
            item["decision"] = "PASS"
            item["replacement"] = ""
            item["approved_translation"] = ""
            item["apply_reason"] = "no_op"
            evaluated.append(item)
            continue
        mandatory_script_cleanup = bool(
            current_translations
            and has_target_script_residue(current_text)
        )
        category = CATEGORY_ALIASES.get(str(item.get("category", "")), str(item.get("category", "")))
        reason = str(item.get("reason", "")).lower()
        masking_reason = any(k in reason for k in ("伏字", "遮掩", "未还原", "○", "●", "×", "＊", "※", "□"))
        mandatory_masking_cleanup = bool(
            current_translations
            and has_masking_symbol(current_text)
            and masking_reason
            and not is_clear
        )

        # Quality Guardrail: Reject hallucinated kana violations when current text has no Japanese kana
        if current_translations and category == "policy_violation":
            is_kana_reason = any(k in reason for k in ("假名", "片假名", "平假名", "kana", "日文假名"))
            if is_kana_reason and not mandatory_script_cleanup and not mandatory_masking_cleanup:
                item["apply_reason"] = "unsubstantiated_policy_violation"
                evaluated.append(item)
                continue

        if mandatory_script_cleanup or mandatory_masking_cleanup:
            category = "policy_violation"
            item["severity"] = "critical"
            item["auto_apply"] = True
        if is_new_contract or mandatory_script_cleanup or mandatory_masking_cleanup:
            item["category"] = category
        if is_new_contract and (
            category not in OBJECTIVE_CATEGORIES
            or str(item.get("severity", "")) not in OBJECTIVE_SEVERITIES
        ):
            item["decision"] = "PASS" if category == "style" else "REPORT_ONLY"
            item["apply_reason"] = "style_not_auto_applied" if category == "style" else "ineligible_category"
            evaluated.append(item)
            continue
        if is_clear:
            item["apply_reason"] = "clear_disabled"
            evaluated.append(item)
            continue
        errors = replacement_validation_errors(replacement)
        if errors:
            item["apply_state"] = "blocked"
            item["apply_reason"] = "replacement_validation_failed"
            item["validation_errors"] = errors
            item["invalid_reason"] = ", ".join(errors)
            evaluated.append(item)
            continue
        conf = float(item.get("confidence", 0) or 0)
        if not (mandatory_script_cleanup or mandatory_masking_cleanup) and conf < threshold:
            item["apply_reason"] = "below_threshold"
            evaluated.append(item)
            continue
        if item.get("consensus") is False and len(item.get("reporters", [])) > 1:
            item["decision"] = "REPORT_ONLY"
            item["apply_reason"] = "replacement_disagreement"
            evaluated.append(item)
            continue
        if not (autonomous or item.get("auto_apply") is True or mandatory_script_cleanup or mandatory_masking_cleanup):
            item["apply_reason"] = "auto_apply_disabled"
            evaluated.append(item)
            continue
        base_hash = str(item.get("base_translation_hash", ""))
        if base_hash and current_translations is not None and base_hash != translation_hash(current_text):
            item["apply_reason"] = "stale_base_translation"
            evaluated.append(item)
            continue
        item["operation"] = operation
        item["approved_translation"] = replacement
        item["replacement"] = replacement
        item["base_translation_hash"] = base_hash or translation_hash(current_text)
        item["apply_reason"] = "gate_passed"
        evaluated.append(item)
    return evaluated


def approved_fixes(
    items: list[dict[str, Any]], threshold: float = 0.9, *, autonomous: bool = False,
    current_translations: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    return [
        item for item in evaluate_apply_gate(
            items, threshold, autonomous=autonomous, current_translations=current_translations
        )
        if item.get("apply_reason") == "gate_passed"
    ]


def missing_checked_ids(payload: dict[str, Any], expected_ids: set[str]) -> set[str]:
    checked = payload.get("checked_ids", []) if isinstance(payload, dict) else []
    return expected_ids - {str(item) for item in checked}


def validate_chapter_review_payload(
    payload: dict[str, Any],
    expected_ids: set[str],
    *,
    context_before_ids: set[str] | None = None,
) -> dict[str, Any]:
    try:
        normalized = ChapterReviewOutput.model_validate(payload).model_dump(exclude_none=True)
    except Exception as exc:
        raise ReviewValidationError(f"章节审阅结果未通过完整 Schema 校验：{exc}") from exc
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
        # PASS is represented by the checked ID alone. Legacy style findings are
        # ordinary acceptable wording differences and normalize to PASS.
        if item.get("decision") == "PASS" or item["category"] == "style":
            continue
        errors = replacement_validation_errors(rep) if item.get("decision") == "FIX_REQUIRED" else []
        if errors:
            item["auto_apply"] = False
            item["apply_state"] = "blocked"
            item["apply_reason"] = "replacement_validation_failed"
            item["validation_errors"] = errors
            item["invalid_reason"] = ", ".join(errors)
        sanitized_fixes.append(item)
    normalized["fixes"] = sanitized_fixes

    # Context findings are advisory signals for a bounded targeted re-review.
    # They may only point backwards into the read-only context window.
    allowed_context_ids = context_before_ids or set()
    sanitized_context_findings = []
    for raw_finding in normalized.get("context_findings", []):
        if not isinstance(raw_finding, dict) or str(raw_finding.get("id", "")) not in allowed_context_ids:
            continue
        finding = dict(raw_finding)
        finding["category"] = CATEGORY_ALIASES.get(
            str(finding.get("category", "")), str(finding.get("category", ""))
        )
        if (
            finding["category"] not in OBJECTIVE_CATEGORIES
            or str(finding.get("severity", "")) not in OBJECTIVE_SEVERITIES
        ):
            continue
        finding["evidence_ids"] = [
            str(evidence_id)
            for evidence_id in finding.get("evidence_ids", [])
            if str(evidence_id) in expected_ids
        ]
        sanitized_context_findings.append(finding)
    normalized["context_findings"] = sanitized_context_findings

    received = set(checked_ids)
    missing = sorted(expected_ids - received)
    if missing:
        raise ReviewValidationError(f"章节审阅结果段落不匹配；checked_ids 缺少 ID：{', '.join(missing)}")
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
        is_clear = str(fix.get("operation", "replace")) == "clear"
        expected = str(fix.get("approved_translation", "") or fix.get("replacement", "")).strip()
        actual = str(paragraphs.get(item_id, {}).get("translated", "")).strip()
        if not item_id or (is_clear and actual) or (not is_clear and (not expected or actual != expected)):
            mismatches.append(item_id or "<empty>")
    if mismatches:
        raise ValueError(f"应用修复后 manifest 未验证通过：{', '.join(mismatches)}")


def finalize_writeback_states(
    gate_results: list[dict[str, Any]],
    manifest: dict[str, Any],
    *,
    execution_error: Exception | None = None,
) -> list[dict[str, Any]]:
    """Promote gate-passed records only after exact manifest verification."""
    paragraphs = paragraph_map(manifest)
    finalized: list[dict[str, Any]] = []
    for raw in gate_results:
        item = dict(raw)
        if item.get("apply_reason") != "gate_passed":
            finalized.append(item)
            continue
        if execution_error is not None:
            item["apply_state"] = "failed"
            item["apply_reason"] = "write_failed"
            item["validation_errors"] = [str(execution_error)]
            finalized.append(item)
            continue
        item_id = str(item.get("id", ""))
        expected = str(item.get("replacement", ""))
        actual = str(paragraphs.get(item_id, {}).get("translated", ""))
        if item_id in paragraphs and expected and actual == expected:
            item["apply_state"] = "applied"
            item["apply_reason"] = "manifest_verified"
        else:
            item["apply_state"] = "failed"
            item["apply_reason"] = "manifest_verification_failed"
            item["validation_errors"] = ["manifest_mismatch"]
        finalized.append(item)
    return finalized


def review_report_counts(checked: int, records: list[dict[str, Any]]) -> dict[str, int]:
    fix_required = sum(1 for item in records if item.get("decision") == "FIX_REQUIRED")
    suggestions = sum(1 for item in records if item.get("decision") == "REPORT_ONLY")
    return {
        "reviewed": checked,
        "pass": max(0, checked - fix_required - suggestions),
        "fix_required": fix_required,
        "suggestions": suggestions,
        "applied": sum(1 for item in records if item.get("apply_state") == "applied"),
        "blocked": sum(1 for item in records if item.get("apply_state") == "blocked"),
    }


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
            same_fix = (
                str(in_a.get("category", "")) == str(in_b.get("category", ""))
                and str(in_a.get("operation", "replace") or "replace")
                == str(in_b.get("operation", "replace") or "replace")
                and rep_a == rep_b
                and bool(rep_a or str(in_a.get("operation", "replace")) == "clear")
            )
            if same_fix:
                chosen = dict(in_a if conf_a >= conf_b else in_b)
                chosen["decision"] = "FIX_REQUIRED"
                chosen["apply_reason"] = ""
            else:
                # Different answers are evidence of ambiguity, not a signal to choose
                # one by confidence or edit distance.
                chosen = dict(in_a if conf_a >= conf_b else in_b)
                chosen["decision"] = "REPORT_ONLY"
                chosen["replacement"] = ""
                chosen["approved_translation"] = ""
                chosen["apply_state"] = "not_applied"
                chosen["apply_reason"] = "replacement_disagreement"
            chosen["confidence"] = max(conf_a, conf_b)
            chosen["consensus"] = same_fix
            chosen["reporters"] = ["primary", "secondary"]
            merged_fixes.append(chosen)
        elif in_a:
            item = dict(in_a)
            item["decision"] = "REPORT_ONLY"
            item["apply_state"] = "not_applied"
            item["apply_reason"] = "single_reviewer_only"
            item["consensus"] = False
            item["reporters"] = ["primary"]
            merged_fixes.append(item)
        else:
            item = dict(in_b or {})
            item["decision"] = "REPORT_ONLY"
            item["apply_state"] = "not_applied"
            item["apply_reason"] = "single_reviewer_only"
            item["consensus"] = False
            item["reporters"] = ["secondary"]
            merged_fixes.append(item)

    findings_a = primary_review.get("context_findings", []) or []
    findings_b = secondary_review.get("context_findings", []) or []
    by_finding_a = {
        str(item.get("id", "")): item
        for item in findings_a
        if isinstance(item, dict) and item.get("id")
    }
    by_finding_b = {
        str(item.get("id", "")): item
        for item in findings_b
        if isinstance(item, dict) and item.get("id")
    }
    merged_context_findings: list[dict[str, Any]] = []
    for finding_id in sorted(set(by_finding_a) | set(by_finding_b)):
        finding_a = by_finding_a.get(finding_id)
        finding_b = by_finding_b.get(finding_id)
        if finding_a and finding_b:
            chosen = dict(finding_a)
            if len(str(finding_b.get("reason", ""))) > len(str(finding_a.get("reason", ""))):
                chosen = dict(finding_b)
            chosen["confidence"] = max(
                float(finding_a.get("confidence", 0) or 0),
                float(finding_b.get("confidence", 0) or 0),
            )
            chosen["consensus"] = True
            chosen["reporters"] = ["primary", "secondary"]
            chosen["evidence_ids"] = list(dict.fromkeys(
                [str(value) for value in finding_a.get("evidence_ids", [])]
                + [str(value) for value in finding_b.get("evidence_ids", [])]
            ))
        else:
            chosen = dict(finding_a or finding_b or {})
            chosen["consensus"] = False
            chosen["reporters"] = ["primary" if finding_a else "secondary"]
        merged_context_findings.append(chosen)

    return _normalized_review({
        "checked_ids": merged_checked,
        "fixes": merged_fixes,
        "context_findings": merged_context_findings,
        "dual_review": {
            "enabled": True,
            "primary_fixes_count": len(fixes_a),
            "secondary_fixes_count": len(fixes_b),
            "consensus_fixes_count": sum(1 for f in merged_fixes if f.get("consensus")),
            "merged_fixes_count": len(merged_fixes),
        },
    })


REVIEW_CHUNK_MAX_PARAGRAPHS = 100  # Legacy explicit paragraph-count override.
REVIEW_CHUNK_MIN_CHARS = 1000
REVIEW_CHUNK_MAX_CHARS = 1500
REVIEW_CONTEXT_BEFORE = 3
REVIEW_CONTEXT_AFTER = 3


def chunk_items_by_source_chars(
    items: list[dict[str, Any]],
    *,
    min_chars: int = REVIEW_CHUNK_MIN_CHARS,
    max_chars: int = REVIEW_CHUNK_MAX_CHARS,
) -> list[list[dict[str, Any]]]:
    """Split between complete paragraphs using a source-character budget."""
    if min_chars < 1 or max_chars < min_chars:
        raise ValueError("review chunk character bounds are invalid")
    if not items:
        return [[]]

    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for item in items:
        paragraph_chars = len(str(item.get("source", "")))
        if current and current_chars >= min_chars and current_chars + paragraph_chars > max_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += paragraph_chars

    if current:
        if chunks and current_chars < min_chars:
            previous_chars = sum(len(str(item.get("source", ""))) for item in chunks[-1])
            if previous_chars + current_chars <= max_chars:
                chunks[-1].extend(current)
            else:
                chunks.append(current)
        else:
            chunks.append(current)
    return chunks


def build_review_window(
    items: list[dict[str, Any]],
    start: int,
    end: int,
    *,
    context_before: int = REVIEW_CONTEXT_BEFORE,
    context_after: int = REVIEW_CONTEXT_AFTER,
) -> dict[str, list[dict[str, Any]]]:
    """Return target items plus bilingual, read-only neighboring paragraphs."""
    return {
        "items": items[start:end],
        "context_before": items[max(0, start - context_before):start],
        "context_after": items[end:end + context_after],
    }


def dynamic_review_timeout(input_payload: dict[str, Any]) -> int:
    """Calculate timeout from the complete serialized review payload size."""
    payload_chars = len(json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"), default=str))
    return max(120, min(720, 45 + int(payload_chars * 0.05)))


def _coerce_provider_request_error(exc: Exception, provider: str, timeout: int) -> Exception:
    if isinstance(exc, (ProviderRequestError, ProviderResponseError)):
        return exc
    if isinstance(exc, TimeoutError):
        return ProviderTimeoutError(
            f"{provider} review request timed out after {timeout}s: {exc}",
            provider=provider,
            timeout_seconds=timeout,
        )
    if isinstance(exc, ConnectionError):
        return ProviderConnectionError(f"{provider} review connection failed: {exc}", provider=provider)
    return exc


def should_adaptively_split(exc: Exception) -> bool:
    """Only shrink input for response/validation failures and exhausted read timeouts."""
    if isinstance(exc, ReviewTargetSplitRequired):
        return True
    if isinstance(exc, ReviewContextOverflowError):
        return False
    if isinstance(exc, ProviderTimeoutError):
        return exc.retries_exhausted
    if isinstance(exc, (ProviderHTTPError, ProviderConnectionError, ProviderRequestError)):
        return False
    return isinstance(exc, (ReviewValidationError, ProviderResponseError, ValueError))


def _interruptible_retry_wait(
    delay: float,
    *,
    cancel_check: Callable[[], None] | None,
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> None:
    deadline = monotonic() + max(0.0, delay)
    while True:
        if cancel_check:
            cancel_check()
        remaining = deadline - monotonic()
        if remaining <= 0:
            return
        sleeper(min(0.25, remaining))


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
    retry_config: dict[str, Any] | None = None,
    random_uniform: Callable[[float, float], float] = random.uniform,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    wall_clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    backends = _review_backends(backend)
    last_exc: Exception | None = None
    effective_timeout = timeout or dynamic_review_timeout(input_payload)
    pipeline_config = retry_config if retry_config is not None else load_config().get("pipeline", {})
    transient_retries = max(0, int(pipeline_config.get("transient_http_retries", 3)))
    timeout_retries = max(0, int(pipeline_config.get("timeout_retries", 1)))
    connection_retries = max(0, int(pipeline_config.get("connection_retries", 2)))
    backoff_min = max(0.0, float(pipeline_config.get("transient_backoff_min_seconds", 10)))
    backoff_max = max(backoff_min, float(pipeline_config.get("transient_backoff_max_seconds", 20)))
    backoff_multiplier = max(1.0, float(pipeline_config.get("transient_backoff_multiplier", 2)))
    backoff_cap = max(0.0, float(pipeline_config.get("transient_backoff_cap_seconds", 80)))

    def next_attempt() -> int:
        if attempt_counters is None:
            return 0
        if attempt_lock is None:
            attempt_counters[role] = attempt_counters.get(role, 0) + 1
            return attempt_counters[role]
        with attempt_lock:
            attempt_counters[role] = attempt_counters.get(role, 0) + 1
            return attempt_counters[role]

    local_attempt = 0
    for candidate_index, candidate in enumerate(backends, start=1):
        retry_index = 0
        provider = None
        while True:
            if cancel_check:
                cancel_check()
            local_attempt += 1
            attempt = next_attempt() if attempt_counters is not None else local_attempt
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
                "retry_index": retry_index,
            }
            if on_reviewer_status:
                on_reviewer_status({**status_base, "status": "reviewing" if retry_index == 0 else "retrying"})
            try:
                if provider is None:
                    provider = get_provider(candidate)
                result = provider.review(
                    kind, input_payload, schema_path, autonomous=autonomous, timeout=effective_timeout
                )
                if on_reviewer_status:
                    on_reviewer_status({**status_base, "status": "completed"})
                return result
            except JobCancelled:
                if on_reviewer_status:
                    on_reviewer_status({**status_base, "status": "cancelled"})
                raise
            except Exception as raw_exc:
                exc = (
                    ProviderRequestError(
                        f"{candidate} reviewer initialization failed: {raw_exc}",
                        provider=candidate,
                        retryable=False,
                    )
                    if provider is None
                    else _coerce_provider_request_error(raw_exc, candidate, effective_timeout)
                )
                last_exc = exc
                retry_total = 0
                reason = ""
                if isinstance(exc, ProviderHTTPError) and exc.retryable:
                    retry_total = transient_retries
                    reason = f"http_{exc.status_code}"
                elif isinstance(exc, ProviderTimeoutError):
                    retry_total = timeout_retries
                    reason = "read_timeout"
                elif isinstance(exc, ProviderConnectionError):
                    retry_total = connection_retries
                    reason = "connection_error"

                if retry_index < retry_total:
                    if isinstance(exc, ProviderHTTPError) and exc.retry_after_seconds is not None:
                        delay = min(backoff_cap, exc.retry_after_seconds)
                    elif isinstance(exc, ProviderConnectionError):
                        delay = min(5.0, random_uniform(1.0, 2.0) * (2 ** retry_index))
                    elif isinstance(exc, ProviderTimeoutError):
                        delay = 0.25
                    else:
                        low = min(backoff_cap, backoff_min * (backoff_multiplier ** retry_index))
                        high = min(backoff_cap, backoff_max * (backoff_multiplier ** retry_index))
                        delay = random_uniform(low, high)
                    wait_status = {
                        **status_base,
                        "status": "retry_wait",
                        "retry_reason": reason,
                        "retry_index": retry_index + 1,
                        "retry_total": retry_total,
                        "retry_delay_seconds": round(delay, 3),
                        "retry_resume_monotonic": monotonic() + delay,
                        "retry_resume_at": datetime.fromtimestamp(
                            wall_clock() + delay, tz=timezone.utc
                        ).isoformat(),
                    }
                    if isinstance(exc, ProviderHTTPError):
                        wait_status["http_status"] = exc.status_code
                    if on_reviewer_status:
                        on_reviewer_status(wait_status)
                    try:
                        _interruptible_retry_wait(
                            delay, cancel_check=cancel_check, monotonic=monotonic, sleeper=sleeper
                        )
                    except JobCancelled:
                        if on_reviewer_status:
                            on_reviewer_status({**wait_status, "status": "cancelled"})
                        raise
                    retry_index += 1
                    continue

                if isinstance(exc, ProviderRequestError):
                    exc.retries_exhausted = isinstance(exc, ProviderTimeoutError) or retry_total > 0
                retries_exhausted = isinstance(exc, ProviderTimeoutError) or retry_total > 0
                if on_reviewer_status:
                    on_reviewer_status({
                        **status_base,
                        "status": "failed",
                        "error": str(exc),
                        "retry_reason": reason or None,
                        "retry_total": retry_total,
                        "retries_exhausted": retries_exhausted,
                    })
                break
    if last_exc is None:
        raise RuntimeError(f"没有可用审阅端 (kind={kind}, primary={backend})")
    raise last_exc


def _update_rolling_payload(base_payload: dict[str, Any], knowledge: dict[str, Any]) -> dict[str, Any]:
    """Forward only temporary window context; formal knowledge stays untouched."""
    rolling = dict(base_payload)
    delta = knowledge.get("rolling_context_delta", knowledge) if isinstance(knowledge, dict) else {}
    if not isinstance(delta, dict):
        return rolling
    current = dict(rolling.get("current_chapter_review_context", {}) or {})
    for key in ("adopted_terms", "active_entities", "locations", "relationships", "important_states", "notes"):
        values = delta.get(key, [])
        if isinstance(values, list):
            existing = current.get(key, [])
            if not isinstance(existing, list):
                existing = []
            current[key] = list(dict.fromkeys(existing + values))
    if current:
        rolling["current_chapter_review_context"] = current
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

    merged_findings: list[dict[str, Any]] = []
    finding_index: dict[str, int] = {}
    for raw_finding in list(chunk_a.get("context_findings", []) or []) + list(chunk_b.get("context_findings", []) or []):
        if not isinstance(raw_finding, dict) or not raw_finding.get("id"):
            continue
        finding = dict(raw_finding)
        finding_id = str(finding["id"])
        if finding_id in finding_index:
            existing = merged_findings[finding_index[finding_id]]
            if len(str(finding.get("reason", ""))) > len(str(existing.get("reason", ""))):
                merged_findings[finding_index[finding_id]] = finding
        else:
            finding_index[finding_id] = len(merged_findings)
            merged_findings.append(finding)

    context_snapshots = list((chunk_a.get("review_diagnostics") or {}).get("context_snapshots", []) or [])
    context_snapshots.extend((chunk_b.get("review_diagnostics") or {}).get("context_snapshots", []) or [])
    return _normalized_review({
        "checked_ids": merged_checked,
        "fixes": merged_fixes,
        "context_findings": merged_findings,
        "review_diagnostics": {"context_snapshots": context_snapshots} if context_snapshots else None,
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
    context_before_size: int = 0,
    context_after_size: int = 0,
) -> dict[str, Any]:
    """Execute review and split only failures whose classification can benefit from smaller input."""
    if not items:
        return _normalized_review({
            "checked_ids": [],
            "fixes": [],
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
                context_before_size=context_before_size,
                context_after_size=context_after_size,
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
            merged = merge_chapter_reviews(primary_payload, secondary_payload, current_translations=current_trans)
            primary_snapshots = list((primary_payload.get("review_diagnostics") or {}).get("context_snapshots", []) or [])
            secondary_snapshots = list((secondary_payload.get("review_diagnostics") or {}).get("context_snapshots", []) or [])
            if primary_snapshots or secondary_snapshots:
                primary_ids = [item.get("context_snapshot_id") for item in primary_snapshots]
                secondary_ids = [item.get("context_snapshot_id") for item in secondary_snapshots]
                merged["review_diagnostics"] = {
                    "context_snapshots": primary_snapshots,
                    "dual_snapshot_match": primary_ids == secondary_ids,
                }
            return merged
        raise RuntimeError(f"双审阅未完整完成: {'; '.join(errors)}")

    if cancel_check:
        cancel_check()
    segment_payload = dict(base_payload)
    segment_payload["items"] = items
    context_diagnostics: dict[str, Any] | None = None
    pipeline_config = load_config().get("pipeline", {})
    context_config = ReviewContextBudget.from_mapping(pipeline_config.get("review_context"))
    if context_config.enabled:
        _snapshot, context_diagnostics, segment_payload = build_budgeted_review_context(
            base_payload,
            items=items,
            context_before=list(base_payload.get("context_before", []) or []),
            context_after=list(base_payload.get("context_after", []) or []),
            trigger_evidence=list(base_payload.get("trigger_findings", []) or []),
            budget=context_config,
            schema_path=schema_path,
            autonomous=autonomous,
        )
    expected_ids = {str(item.get("id", "")) for item in items if item.get("id")}
    context_before_ids = {
        str(item.get("id", ""))
        for item in segment_payload.get("context_before", [])
        if isinstance(item, dict) and item.get("id")
    }

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
        res = validate_chapter_review_payload(
            res,
            expected_ids,
            context_before_ids=context_before_ids,
        )
        if context_diagnostics is not None:
            res["review_diagnostics"] = {"context_snapshots": [context_diagnostics]}
        return res
    except Exception as exc:
        if (
            should_adaptively_split(exc)
            and len(items) > 1
            and (depth < max_depth or isinstance(exc, ReviewTargetSplitRequired))
        ):
            # Content and exhausted read-timeout failures may benefit from a smaller target.
            midpoint = max(1, len(items) // 2)
            left_items = items[:midpoint]
            right_items = items[midpoint:]

            left_payload = dict(base_payload)
            left_payload["context_after"] = (
                list(right_items) + list(base_payload.get("context_after", []) or [])
            )[:context_after_size]
            right_payload = dict(base_payload)
            right_payload["context_before"] = (
                list(base_payload.get("context_before", []) or []) + list(left_items)
            )[-context_before_size:] if context_before_size else []

            left_res = _execute_segment_with_adaptive_split(
                left_payload,
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
                context_before_size=context_before_size,
                context_after_size=context_after_size,
            )

            rolling_payload = _update_rolling_payload(base_payload, left_res)

            right_res = _execute_segment_with_adaptive_split(
                {
                    **rolling_payload,
                    "context_before": right_payload.get("context_before", []),
                    "context_after": right_payload.get("context_after", []),
                },
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
                context_before_size=context_before_size,
                context_after_size=context_after_size,
            )

            return _combine_chunk_reviews(left_res, right_res)
        else:
            raise exc


def _eligible_context_findings(
    review: dict[str, Any],
    *,
    allowed_ids: set[str],
    minimum_confidence: float,
    already_rechecked: set[str],
) -> list[dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    for raw in review.get("context_findings", []) or []:
        if not isinstance(raw, dict):
            continue
        finding_id = str(raw.get("id", ""))
        confidence = float(raw.get("confidence", 0) or 0)
        if (
            finding_id in allowed_ids
            and finding_id not in already_rechecked
            and (bool(raw.get("consensus")) or confidence >= minimum_confidence)
        ):
            eligible.append(dict(raw))
    return eligible


def _replace_targeted_review(
    aggregate: dict[str, Any],
    targeted: dict[str, Any],
    targeted_ids: set[str],
) -> dict[str, Any]:
    """Make a targeted re-review authoritative for its paragraph IDs."""
    aggregate = _normalized_review(aggregate)
    targeted = _normalized_review(targeted)
    fixes = [
        item for item in aggregate.get("fixes", []) or []
        if str(item.get("id", "")) not in targeted_ids
    ]
    fixes.extend(targeted.get("fixes", []) or [])
    context_snapshots = list((aggregate.get("review_diagnostics") or {}).get("context_snapshots", []) or [])
    context_snapshots.extend((targeted.get("review_diagnostics") or {}).get("context_snapshots", []) or [])
    return _normalized_review({
        **aggregate,
        "checked_ids": list(dict.fromkeys(
            list(aggregate.get("checked_ids", []) or [])
            + list(targeted.get("checked_ids", []) or [])
        )),
        "fixes": fixes,
        "review_diagnostics": {"context_snapshots": context_snapshots} if context_snapshots else None,
    })


def run_chapter_review(
    input_path: Path,
    output_path: Path,
    autonomous: bool = False,
    *,
    backend: str | None = None,
    secondary_backend: str | None = None,
    dual_review: bool | None = None,
    chunk_size: int | None = None,
    chunk_min_chars: int | None = None,
    chunk_max_chars: int | None = None,
    context_before: int | None = None,
    context_after: int | None = None,
    backtrack_enabled: bool | None = None,
    backtrack_min_confidence: float | None = None,
    on_reviewer_status: Callable[[dict[str, Any]], None] | None = None,
    cancel_check: Callable[[], None] | None = None,
    on_window_completed: Callable[[dict[str, Any], dict[str, list[dict[str, Any]]], int, int], dict[str, Any] | None] | None = None,
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
    pipeline_config = config.get("pipeline", {})
    effective_min_chars = int(
        chunk_min_chars if chunk_min_chars is not None
        else pipeline_config.get("review_chunk_min_chars", REVIEW_CHUNK_MIN_CHARS)
    )
    effective_max_chars = int(
        chunk_max_chars if chunk_max_chars is not None
        else pipeline_config.get("review_chunk_max_chars", REVIEW_CHUNK_MAX_CHARS)
    )
    effective_context_before = max(0, int(
        context_before if context_before is not None
        else pipeline_config.get("review_context_before", REVIEW_CONTEXT_BEFORE)
    ))
    effective_context_after = max(0, int(
        context_after if context_after is not None
        else pipeline_config.get("review_context_after", REVIEW_CONTEXT_AFTER)
    ))
    effective_backtrack = bool(
        backtrack_enabled if backtrack_enabled is not None
        else pipeline_config.get("review_backtrack_enabled", True)
    )
    effective_backtrack_threshold = float(
        backtrack_min_confidence if backtrack_min_confidence is not None
        else pipeline_config.get("review_backtrack_min_confidence", 0.8)
    )

    # Keep the old paragraph-count argument for callers/tests that explicitly
    # use it; the production default is source-character chunking.
    if chunk_size is not None:
        if chunk_size < 1:
            raise ValueError("chunk_size must be greater than zero")
        spans = [(start, min(len(items), start + chunk_size)) for start in range(0, len(items), chunk_size)]
    else:
        chunks = chunk_items_by_source_chars(
            items,
            min_chars=effective_min_chars,
            max_chars=effective_max_chars,
        )
        spans = []
        cursor = 0
        for chunk in chunks:
            spans.append((cursor, cursor + len(chunk)))
            cursor += len(chunk)
    if not spans:
        spans = [(0, 0)]

    rolling_payload = dict(input_payload)
    aggregate: dict[str, Any] | None = None
    attempt_counters = {"primary": 0, "secondary": 0}
    attempt_lock = threading.Lock()
    already_rechecked: set[str] = set()
    backtrack_diagnostics: list[dict[str, Any]] = []
    window_diagnostics: list[dict[str, Any]] = []

    for chunk_index, (start, end) in enumerate(spans, start=1):
        if cancel_check:
            cancel_check()
        window = build_review_window(
            items,
            start,
            end,
            context_before=effective_context_before,
            context_after=effective_context_after,
        )
        chunk_payload = {**rolling_payload, **window, "review_mode": "chapter_chunk"}
        chunk_timeout = dynamic_review_timeout(chunk_payload)
        chunk_res = _execute_segment_with_adaptive_split(
            chunk_payload,
            window["items"],
            CHAPTER_SCHEMA,
            autonomous=autonomous,
            backend=primary_cand,
            secondary_backend=sec_cand,
            is_dual=is_dual,
            on_reviewer_status=on_reviewer_status,
            cancel_check=cancel_check,
            timeout=chunk_timeout,
            chunk_index=chunk_index,
            total_chunks=len(spans),
            attempt_counters=attempt_counters,
            attempt_lock=attempt_lock,
            context_before_size=effective_context_before,
            context_after_size=effective_context_after,
        )
        if aggregate is None:
            aggregate = chunk_res
        else:
            aggregate = _combine_chunk_reviews(aggregate, chunk_res)
        if on_window_completed is not None:
            try:
                window_knowledge = on_window_completed(chunk_res, window, chunk_index, len(spans))
                if isinstance(window_knowledge, dict):
                    rolling_payload = _update_rolling_payload(rolling_payload, window_knowledge)
                    window_diagnostics.append({
                        "window_index": chunk_index,
                        "status": window_knowledge.get("status", "completed"),
                        "candidate_count": len(window_knowledge.get("knowledge_candidates", []) or []),
                        "conflict_count": len(window_knowledge.get("conflicts", []) or []),
                        "rolling_context_fields": sorted(
                            key for key, value in (window_knowledge.get("rolling_context_delta", {}) or {}).items()
                            if isinstance(value, list) and value
                        ),
                    })
            except JobCancelled:
                raise
            except Exception as exc:  # knowledge extraction is advisory to review
                window_diagnostics.append({"window_index": chunk_index, "status": "failed", "error": str(exc)})

        if effective_backtrack:
            context_ids = {
                str(item.get("id", ""))
                for item in window["context_before"]
                if isinstance(item, dict) and item.get("id")
            }
            findings = _eligible_context_findings(
                chunk_res,
                allowed_ids=context_ids,
                minimum_confidence=effective_backtrack_threshold,
                already_rechecked=already_rechecked,
            )
            for finding in findings:
                finding_id = str(finding["id"])
                already_rechecked.add(finding_id)
                candidate_index = next(
                    (index for index, item in enumerate(items) if str(item.get("id", "")) == finding_id),
                    None,
                )
                if candidate_index is None:
                    continue
                candidate_window = build_review_window(
                    items,
                    candidate_index,
                    candidate_index + 1,
                    context_before=effective_context_before,
                    context_after=effective_context_after,
                )
                targeted_payload = {
                    **rolling_payload,
                    **candidate_window,
                    "review_mode": "targeted_context_recheck",
                    "trigger_findings": [finding],
                }
                try:
                    targeted_res = _execute_segment_with_adaptive_split(
                        targeted_payload,
                        candidate_window["items"],
                        CHAPTER_SCHEMA,
                        autonomous=autonomous,
                        backend=primary_cand,
                        secondary_backend=sec_cand,
                        is_dual=is_dual,
                        on_reviewer_status=on_reviewer_status,
                        cancel_check=cancel_check,
                        timeout=dynamic_review_timeout(targeted_payload),
                        chunk_index=chunk_index,
                        total_chunks=len(spans),
                        split_path=f"backtrack.{finding_id}",
                        attempt_counters=attempt_counters,
                        attempt_lock=attempt_lock,
                        context_before_size=effective_context_before,
                        context_after_size=effective_context_after,
                    )
                    aggregate = _replace_targeted_review(aggregate, targeted_res, {finding_id})
                    backtrack_diagnostics.append({
                        "id": finding_id,
                        "status": "completed",
                        "fixes": len(targeted_res.get("fixes", []) or []),
                    })
                except Exception as exc:
                    backtrack_diagnostics.append({
                        "id": finding_id,
                        "status": "failed",
                        "error": str(exc),
                    })

    merged_payload = aggregate or _normalized_review({})

    if cancel_check:
        cancel_check()
    merged_payload = validate_chapter_review_payload(
        merged_payload,
        expected_ids,
        context_before_ids=expected_ids,
    )
    context_snapshots = list((merged_payload.get("review_diagnostics") or {}).get("context_snapshots", []) or [])
    merged_payload["review_diagnostics"] = {
        "chunking": {
            "mode": "paragraph_count" if chunk_size is not None else "source_chars",
            "min_chars": effective_min_chars if chunk_size is None else None,
            "max_chars": effective_max_chars if chunk_size is None else None,
            "chunk_count": len(spans),
            "context_before": effective_context_before,
            "context_after": effective_context_after,
        },
        "backtrack": {
            "enabled": effective_backtrack,
            "candidate_count": len(merged_payload.get("context_findings", []) or []),
            "rechecks": backtrack_diagnostics,
        },
        "context_snapshots": context_snapshots,
        "window_knowledge": window_diagnostics,
    }
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
    review_apply_cfg = dict(load_config().get("pipeline", {}).get("review_apply", {}) or {})
    apply = bool(apply and review_apply_cfg.get("mode", "report_only") == "hard_fix")
    gate_threshold = max(0.9, float(review_apply_cfg.get("minimum_confidence", 0.9)))
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
    glossary = read_json(workspace.glossary_path, {"schema_version": "3.0", "book": book, "terms": [], "conflicts": [], "revisions": []})
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
        review = validate_chapter_review_payload(
            review,
            expected,
            context_before_ids=set(expected),
        )
        current_translations = {item["id"]: item["translated"] for item in items}
        gate_results = evaluate_apply_gate(
            review["fixes"],
            threshold=gate_threshold,
            autonomous=autonomous,
            current_translations=current_translations,
        )
        fresh_paragraphs = paragraph_map(read_json(manifest_path(book)))
        gate_results = evaluate_apply_gate(
            gate_results,
            threshold=gate_threshold,
            autonomous=autonomous,
            current_translations={
                item_id: str(fresh_paragraphs.get(item_id, {}).get("translated", ""))
                for item_id in expected
            },
        )
        if not apply:
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
        fixes_path = workspace.reviews_dir / f"{c_id}-consistency-fixes.json"
        write_json(fixes_path, {"book": book, "items": fixes})
        applied_fixes: Any = False
        if apply:
            write_error: Exception | None = None
            if fixes:
                try:
                    applied_fixes = call_novel_translator("apply-review-fixes", "--book", book, "--input", str(fixes_path))
                except Exception as exc:
                    write_error = exc
                    applied_fixes = {"status": "error", "error": str(exc)}
            manifest_after_fixes = read_json(manifest_path(book))
            gate_results = finalize_writeback_states(gate_results, manifest_after_fixes, execution_error=write_error)
            review["fixes"] = gate_results
            fixes = [item for item in gate_results if item.get("apply_state") == "applied"]
            remaining_kana = [
                item_id
                for item_id, paragraph in paragraph_map(manifest_after_fixes).items()
                if item_id in expected and has_target_script_residue(str(paragraph.get("translated", "")))
            ]
            if remaining_kana:
                raise ValueError(f"章节 {c_id} 写回后仍残留日文假名或韩文字符：{', '.join(sorted(remaining_kana))}")
        # The standalone reviewer is deliberately read-only for knowledge.
        # ChapterPipeline owns window extraction, final decisions, and the
        # single apply_knowledge_delta persistence boundary.
        knowledge_summary = {
            "status": "not_run",
            "reason": "knowledge extraction is orchestrated by ChapterPipeline",
            "candidates": 0,
            "active": 0,
        }
        report_path = workspace.reports_dir / f"{c_id}.json"
        counts = review_report_counts(len(expected), review["fixes"])
        write_json(report_path, {
            "book": book,
            "chapter_id": c_id,
            "reviewed_at": utc_now(),
            "checked_paragraphs": len(expected),
            "reported_issues": counts["fix_required"],
            **counts,
            "applied_fixes": counts["applied"],
            "approved_fixes": [item for item in review["fixes"] if item.get("apply_state") == "applied"],
            "fixes": review["fixes"],
            "term_summary": knowledge_summary,
            "glossary": knowledge_summary,
            "memory_summary": knowledge_summary,
            "knowledge": knowledge_summary,
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
