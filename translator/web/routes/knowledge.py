from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, HTTPException

from translator.core.config import load_config
from translator.core.paths import PathResolver
from translator.core.workspace import BookWorkspace, json_file_lock, read_json, utc_now, write_json
from translator.glossary.lifecycle import stable_term_id
from translator.glossary.service import persist_glossary
from translator.glossary.taxonomy import canonical_category, category_tier, CategoryTier
from translator.pipeline.chapter_pipeline import manifest_path
from translator.review.models import normalize_review_for_display
from translator.review.reviewer import (
    OBJECTIVE_SEVERITIES,
    REVIEW_FIX_CATEGORIES,
    has_hangul,
    has_japanese_kana,
    has_masking_symbol,
)
from translator.web.models import (
    BookMemoryResponse,
    GlossaryCreateRequest,
    GlossaryItem,
    GlossaryResponse,
    PendingQueueResponse,
)


router = APIRouter(prefix="/knowledge", tags=["Knowledge"])
_CHAPTER_ID_RE = re.compile(r"^c[^-]+$")


def _chapter_files(directory: Path, suffix: str = "") -> dict[str, Path]:
    """Return files belonging to chapter IDs, excluding pipeline sidecars."""
    if not directory.exists():
        return {}
    marker = f"{suffix}.json"
    files: dict[str, Path] = {}
    for path in directory.glob("c*.json"):
        chapter_id = path.name[:-len(marker)] if suffix and path.name.endswith(marker) else path.stem if not suffix else ""
        if _CHAPTER_ID_RE.fullmatch(chapter_id):
            files[chapter_id] = path
    return files


def _not_applied_reason(fix: dict[str, Any], *, apply_disabled: bool) -> str:
    if fix.get("invalid_reason"):
        return f"审阅结果校验未通过：{fix['invalid_reason']}"
    if fix.get("operation") == "clear":
        return "清空重复段落需要双审一致、95% 置信度和自动应用标记"
    replacement = str(fix.get("replacement") or fix.get("approved_translation") or "").strip()
    if not replacement:
        return "审阅器没有提供可写回的修正译文"
    if has_japanese_kana(replacement):
        return "建议译文仍含日文假名，写回安全校验已拦截"
    if has_hangul(replacement):
        return "建议译文仍含韩文字符，写回安全校验已拦截"
    if has_masking_symbol(replacement):
        return "建议译文仍含伏字或遮掩符号，写回安全校验已拦截"
    category = str(fix.get("category", ""))
    severity = str(fix.get("severity", ""))
    if category not in REVIEW_FIX_CATEGORIES:
        return f"问题分类 {category or 'unknown'} 不在审阅修正白名单"
    if severity not in OBJECTIVE_SEVERITIES:
        return f"严重度 {severity or 'unknown'} 未达到 critical/major 自动修正门槛"
    confidence = float(fix.get("confidence", 0) or 0)
    if confidence < 0.9:
        return f"置信度 {round(confidence * 100)}% 低于 90% 自动修正门槛"
    if apply_disabled:
        return "本次流水线未启用自动写回"
    if fix.get("auto_apply") is not True:
        return "审阅器未将此建议标记为可自动应用"
    return "未进入最终批准或写回集合，需人工复核"


def _decorate_fixes_for_display(
    fixes: list[dict[str, Any]],
    *,
    applied_ids: set[str],
    apply_disabled: bool,
) -> list[dict[str, Any]]:
    decorated: list[dict[str, Any]] = []
    for raw in fixes:
        fix = dict(raw)
        apply_state = str(fix.get("apply_state", ""))
        applied = apply_state == "applied" if apply_state else str(fix.get("id", "")) in applied_ids
        fix["applied"] = applied
        fix["not_applied_reason"] = None if applied else (
            str(fix.get("apply_reason", "")) or _not_applied_reason(fix, apply_disabled=apply_disabled)
        )
        decorated.append(fix)
    return decorated


def get_workspace_for_book(book_id: str) -> BookWorkspace:
    manifest = read_json(manifest_path(book_id), default=None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"未找到书籍: {book_id}")
    config = load_config()
    output_root = PathResolver.for_config().output_root(config)
    title = manifest.get("title", book_id)
    return BookWorkspace.at(output_root, title)


def _pending_queue(workspace: BookWorkspace) -> tuple[list[dict[str, Any]], dict[str, int]]:
    data = read_json(workspace.knowledge_candidates_path, default={})
    raw_items = data.get("items", []) if isinstance(data, dict) else []
    items = [dict(item) for item in raw_items if isinstance(item, dict)]
    reason_counts: dict[str, int] = {}
    for item in items:
        reason = str(item.get("queue_reason") or item.get("final_reason") or "pending_review").strip()
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return items, dict(sorted(reason_counts.items()))


@router.get("/{book_id}/glossary", response_model=GlossaryResponse)
def get_glossary(book_id: str) -> GlossaryResponse:
    workspace = get_workspace_for_book(book_id)
    glossary_data = read_json(workspace.glossary_path, default={"terms": [], "conflicts": []})
    pending_items, pending_reason_counts = _pending_queue(workspace)

    items = []
    for t in glossary_data.get("terms", []):
        items.append(GlossaryItem.model_validate(t))

    return GlossaryResponse(
        book_id=book_id,
        terms=items,
        conflicts=glossary_data.get("conflicts", []),
        updated_at=glossary_data.get("updated_at"),
        pending_items=pending_items,
        pending_count=len(pending_items),
        pending_reason_counts=pending_reason_counts,
    )


@router.get("/{book_id}/pending", response_model=PendingQueueResponse)
def get_pending_queue(book_id: str) -> PendingQueueResponse:
    workspace = get_workspace_for_book(book_id)
    items, reason_counts = _pending_queue(workspace)
    return PendingQueueResponse(
        book_id=book_id,
        items=items,
        count=len(items),
        reason_counts=reason_counts,
    )


@router.post("/{book_id}/glossary", response_model=GlossaryResponse)
def update_glossary(book_id: str, request: GlossaryCreateRequest) -> GlossaryResponse:
    workspace = get_workspace_for_book(book_id)
    with json_file_lock(workspace.glossary_path):
        glossary_data = read_json(workspace.glossary_path, default={"terms": [], "conflicts": []})

        existing_terms = {str(t.get("source", "")): dict(t) for t in glossary_data.get("terms", []) if isinstance(t, dict) and t.get("source")}
        for item in request.terms:
            current = existing_terms.get(item.source, {})
            incoming = {key: value for key, value in item.model_dump().items() if value is not None}
            supplied_fields = set(item.model_fields_set)
            for field in ("category", "confidence", "note"):
                if field not in supplied_fields and field in current:
                    incoming[field] = current[field]
            incoming["source"] = item.source.strip()
            incoming["source_normalized"] = incoming.get("source_normalized") or item.source.strip()
            incoming["category"] = canonical_category(incoming.get("category", "unresolved"))
            incoming["term_id"] = current.get("term_id") or incoming.get("term_id") or stable_term_id(str(incoming["source_normalized"]))
            merged = {**current, **incoming}
            if not merged.get("evidence"):
                merged["evidence"] = list(current.get("evidence", []) or [])
            if not merged.get("provenance"):
                merged["provenance"] = list(current.get("provenance", []) or ["api"])
            if merged.get("status") == "active" and not merged.get("evidence"):
                merged["status"] = "candidate"
            if category_tier(merged.get("category")) is CategoryTier.BLOCKED:
                merged["status"] = "retired"
                merged["retired_reason"] = "blocked_category"
            existing_terms[item.source] = merged

        glossary_data["terms"] = list(existing_terms.values())
        glossary_data["schema_version"] = "3.0"
        glossary_data.setdefault("revisions", [])
        glossary_data["updated_at"] = utc_now()
        # The glossary is authoritative; always rebuild the disposable
        # translator projection from it rather than editing the projection.
        persist_glossary(workspace, glossary_data)

    return get_glossary(book_id)


@router.delete("/{book_id}/glossary/{source}", response_model=GlossaryResponse)
def delete_glossary_term(book_id: str, source: str) -> GlossaryResponse:
    workspace = get_workspace_for_book(book_id)
    with json_file_lock(workspace.glossary_path):
        glossary_data = read_json(workspace.glossary_path, default={"terms": [], "conflicts": []})
        before = len(glossary_data.get("terms", []))
        glossary_data["terms"] = [term for term in glossary_data.get("terms", []) if str(term.get("source", "")) != source]
        if len(glossary_data["terms"]) == before:
            raise HTTPException(status_code=404, detail=f"未找到术语: {source}")
        glossary_data["updated_at"] = utc_now()
        persist_glossary(workspace, glossary_data)
    return get_glossary(book_id)


@router.get("/{book_id}/memory", response_model=BookMemoryResponse)
def get_book_memory(book_id: str) -> BookMemoryResponse:
    workspace = get_workspace_for_book(book_id)
    memory_data = read_json(workspace.book_memory_path, default={})

    # Collect all chapter states
    chapter_states = []
    if workspace.chapter_states_dir.exists():
        for state_file in sorted(workspace.chapter_states_dir.glob("*.json")):
            s = read_json(state_file, default=None)
            if s:
                chapter_states.append(s)

    characters = list(memory_data.get("characters", []))
    world_settings = list(memory_data.get("world_settings", []))
    timeline = list(memory_data.get("timeline", []))

    # Parse and map structured entries into characters and world settings
    for entry in memory_data.get("entries", []):
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key", "")).strip()
        val = str(entry.get("value", "")).strip()
        cat = str(entry.get("category", "")).strip().lower()
        note = str(entry.get("note", "")).strip()
        first_chapter = str(entry.get("first_seen_chapter", "")).strip()

        if not key or not val:
            continue

        # Reviewers historically emitted both the canonical v2 categories
        # (``character``/``relationship``) and the descriptive categories
        # (``character_profile``/``relationship_graph``).  Treat both forms
        # as character records so the Knowledge Hub does not hide them under
        # world settings.
        if cat in ("character", "character_profile", "relationship", "relationship_graph", "person", "role"):
            if not any(c.get("name") == key for c in characters):
                role_tag = "角色档案" if cat in ("character", "character_profile", "person", "role") else "人物关系"
                summary_text = val
                if note:
                    summary_text = f"{val}\n\n【出处与备注】: {note}"
                characters.append({
                    "name": key,
                    "role": role_tag,
                    "summary": summary_text,
                    "first_seen": first_chapter or None,
                })
        else:
            if not any(w.get("term") == key for w in world_settings):
                expl_text = val
                if note:
                    expl_text = f"{val} ({note})"
                world_settings.append({
                    "term": key,
                    "explanation": expl_text,
                    "category": cat or "fact",
                })

    # Project active glossary terms into characters & world settings if not already present
    glossary_data = read_json(workspace.glossary_path, default={})
    for term in glossary_data.get("terms", []):
        if not isinstance(term, dict):
            continue
        source = str(term.get("source", "")).strip()
        target = str(term.get("target", "")).strip()
        cat = str(term.get("category", "")).strip().lower()
        note = str(term.get("note", "")).strip()
        first_seen = str(term.get("first_seen_chunk", "")).strip()
        display_name = target or source
        if not display_name:
            continue

        if cat in ("person", "character", "role"):
            if not any(c.get("name") == display_name or c.get("name") == source for c in characters):
                summary_text = note if note else f"译名: {target} (原文: {source})"
                characters.append({
                    "name": display_name,
                    "role": "角色档案",
                    "summary": summary_text,
                    "first_seen": first_seen or None,
                })
        elif cat in ("location", "organization", "item", "skill", "lore", "terminology", "unresolved"):
            if not any(w.get("term") == display_name or w.get("term") == source for w in world_settings):
                expl_text = f"原文: {source} → 译文: {target}"
                if note:
                    expl_text = f"{expl_text} ({note})"
                world_settings.append({
                    "term": display_name,
                    "explanation": expl_text,
                    "category": cat,
                })

    return BookMemoryResponse(
        book_id=book_id,
        characters=characters,
        world_settings=world_settings,
        timeline=timeline,
        chapter_states=chapter_states,
    )


@router.get("/{book_id}/reports")
def list_chapter_reports(book_id: str) -> list[dict[str, Any]]:
    workspace = get_workspace_for_book(book_id)
    reports: list[dict[str, Any]] = []

    if not workspace.reports_dir.exists() and not workspace.reviews_dir.exists():
        return []

    # Find all chapter report files
    report_files = _chapter_files(workspace.reports_dir)
    review_output_files = _chapter_files(workspace.reviews_dir, "-output")
    approved_fix_files = _chapter_files(workspace.reviews_dir, "-approved-fixes")
    state_files = _chapter_files(workspace.chapter_states_dir)

    all_ch_ids = sorted(set(report_files.keys()) | set(review_output_files.keys()) | set(state_files.keys()))

    for ch_id in all_ch_ids:
        rep = read_json(report_files.get(ch_id, Path("nonexistent")), default={})
        raw_review = read_json(review_output_files.get(ch_id, Path("nonexistent")), default={})
        rev, migration_warning = normalize_review_for_display(raw_review)
        approved = read_json(approved_fix_files.get(ch_id, Path("nonexistent")), default={})
        st = read_json(state_files.get(ch_id, Path("nonexistent")), default={})

        fixes = rep.get("fixes", []) if isinstance(rep.get("fixes"), list) else rev.get("fixes", [])
        if not fixes and isinstance(rep.get("approved_fixes"), list):
            fixes = rep.get("approved_fixes", [])
        if not fixes and approved.get("items"):
            fixes = approved.get("items", [])

        approved_items = rep.get("approved_fixes")
        if not isinstance(approved_items, list):
            approved_items = approved.get("items", []) if isinstance(approved.get("items"), list) else []
        applied_count = int(rep.get("applied_fixes", 0) or 0)
        applied_ids = {
            str(item.get("id", ""))
            for item in approved_items
            if isinstance(item, dict) and item.get("id")
        } if applied_count > 0 else set()
        fixes = _decorate_fixes_for_display(
            fixes,
            applied_ids=applied_ids,
            apply_disabled=rep.get("applied") is False,
        )

        reviewed_at = rep.get("reviewed_at")
        if not reviewed_at:
            evidence_path = report_files.get(ch_id) or review_output_files.get(ch_id) or state_files.get(ch_id)
            reviewed_at = datetime.fromtimestamp(evidence_path.stat().st_mtime, timezone.utc).isoformat() if evidence_path and evidence_path.exists() else None
        reports.append({
            "schema_version": "2.0",
            "chapter_id": ch_id,
            "reviewed_at": reviewed_at,
            "checked_paragraphs": rep.get("checked_paragraphs") or len(rev.get("checked_ids", [])),
            "reported_issues": int(rep.get("reported_issues", 0) or 0) if "reported_issues" in rep else sum(
                1 for item in fixes if item.get("decision", "FIX_REQUIRED") == "FIX_REQUIRED"
            ),
            "applied_fixes": applied_count,
            "reviewed": int(rep.get("reviewed", rep.get("checked_paragraphs", 0)) or 0),
            "pass": int(rep.get("pass", 0) or 0),
            "fix_required": int(rep.get("fix_required", rep.get("reported_issues", 0)) or 0),
            "suggestions": int(rep.get("suggestions", 0) or 0),
            "applied": int(rep.get("applied", applied_count) or 0) if isinstance(rep.get("applied", applied_count), (int, bool)) else applied_count,
            "blocked": int(rep.get("blocked", 0) or 0),
            "fixes": fixes,
            "knowledge": rep.get("knowledge", {}),
            "pre_scan": rep.get("pre_scan", {}),
            "review_diagnostics": rev.get("review_diagnostics", {}),
            "dual_review": rev.get("dual_review", {}),
            "migration_warning": migration_warning,
        })

    return reports


@router.get("/{book_id}/reviews/{chapter_id}")
def get_chapter_review(book_id: str, chapter_id: str) -> dict[str, Any]:
    workspace = get_workspace_for_book(book_id)
    review_output_file = workspace.reviews_dir / f"{chapter_id}-output.json"
    approved_fixes_file = workspace.reviews_dir / f"{chapter_id}-approved-fixes.json"
    report_file = workspace.reports_dir / f"{chapter_id}.json"
    state_file = workspace.chapter_states_dir / f"{chapter_id}.json"

    raw_output_data = read_json(review_output_file, default=None)
    output_data, migration_warning = normalize_review_for_display(raw_output_data) if raw_output_data is not None else (None, None)
    approved_data = read_json(approved_fixes_file, default=None)
    report_data = read_json(report_file, default=None)
    state_data = read_json(state_file, default=None)

    if not output_data and not report_data and not state_data:
        return {
            "status": "not_found",
            "message": f"章节 {chapter_id} 尚未执行审阅或无审阅记录",
            "chapter_id": chapter_id,
            "fixes": [],
        }

    fixes = (output_data.get("fixes") if output_data else []) or (approved_data.get("items") if approved_data else [])

    return {
        "status": "ok",
        "chapter_id": chapter_id,
        "report": report_data,
        "review_output": output_data,
        "fixes": fixes,
        "knowledge": report_data.get("knowledge", {}) if isinstance(report_data, dict) else {},
        "migration_warning": migration_warning,
    }
