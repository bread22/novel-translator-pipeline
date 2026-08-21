from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from translator.core.config import load_config


REPORT_SCHEMA_VERSION = 1


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _yaml(value: Any, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.extend(_yaml(item, indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(item)}")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}-")
                lines.extend(_yaml(item, indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        return lines
    return [f"{prefix}{_yaml_scalar(value)}"]


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return str(value)
    return _quote(str(value))


def dump_fixed_yaml(payload: dict[str, Any]) -> str:
    return "\n".join(_yaml(payload)) + "\n"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _model(provider: str, _role: str, _novel_root: Path | None = None) -> str:
    config = load_config()
    providers = config.get("providers", {})
    if provider in providers:
        model = providers[provider].get("model", "")
        if model:
            return model
    return "(configured default)"


def _provider_counts(provenance: dict[str, Any], providers: list[str]) -> tuple[dict[str, int], dict[str, str]]:
    counts = Counter()
    origins: dict[str, str] = {}
    for item_id, item in provenance.get("items", {}).items():
        provider = str(item.get("provider", "unknown"))
        counts[provider] += 1
        origins[str(item_id)] = provider
    result = {p: counts[p] for p in providers}
    for p, c in counts.items():
        if p not in result:
            result[p] = c
    return result, origins


def _diagnostic_summary(diagnostics: dict[str, Any], primary: str, _fallback: str) -> dict[str, Any]:
    reasons: dict[str, dict[str, Any]] = {}
    for attempt in diagnostics.get("attempts", []):
        if str(attempt.get("provider", "")) != primary:
            continue
        reason = str(attempt.get("reason", "provider_error"))
        if reason == "ok":
            continue
        entry = reasons.setdefault(reason, {"attempts": 0, "paragraphs": 0, "paragraph_ids": []})
        ids = [str(item) for item in attempt.get("ids", [])]
        entry["attempts"] += 1
        entry["paragraphs"] += len(ids)
        entry["paragraph_ids"] = sorted(set(entry["paragraph_ids"]) | set(ids))
    for entry in reasons.values():
        entry["unique_paragraphs"] = len(entry["paragraph_ids"])
        del entry["paragraph_ids"]
    return {key: reasons[key] for key in sorted(reasons)}


def _review_summary(workspace: Path, origins: dict[str, str], providers: list[str]) -> dict[str, Any]:
    reported = 0
    applied = 0
    reported_by_origin = Counter()
    applied_by_origin = Counter()
    categories = Counter()
    categories_by_origin: dict[str, Counter] = {p: Counter() for p in providers}
    categories_by_origin["unknown"] = Counter()
    chapters = 0
    paragraphs = 0
    for output in sorted(workspace.glob("reviews/c????-output.json")):
        payload = _read_json(output, {})
        chapter_id = output.stem.removesuffix("-output")
        chapters += 1
        checked = payload.get("checked_ids", [])
        paragraphs += len(checked) if isinstance(checked, list) else len(payload.get("items", []))
        fixes = payload.get("fixes", payload.get("issues", []))
        if not isinstance(fixes, list):
            fixes = []
        reported += len(fixes)
        for fix in fixes:
            item_id = str(fix.get("id", ""))
            category = str(fix.get("category", "unknown"))
            origin = origins.get(item_id, "unknown")
            if origin not in categories_by_origin:
                origin = "unknown"
            categories[category] += 1
            categories_by_origin[origin][category] += 1
            reported_by_origin[origin] += 1
        approved = _read_json(workspace / "reviews" / f"{chapter_id}-approved-fixes.json", {})
        items = approved.get("items", []) if isinstance(approved, dict) else []
        if isinstance(items, list):
            applied += len(items)
            for fix in items:
                applied_by_origin[origins.get(str(fix.get("id", "")), "unknown")] += 1

    all_keys = list(providers)
    if reported_by_origin.get("unknown", 0) > 0 or applied_by_origin.get("unknown", 0) > 0:
        all_keys.append("unknown")

    fallback_categories = Counter()
    for p in providers[1:]:
        fallback_categories.update(categories_by_origin[p])

    primary_name = providers[0] if providers else "primary"
    fix_categories: dict[str, Any] = {
        "total": {key: categories[key] for key in sorted(categories)},
    }
    if categories_by_origin.get(primary_name):
        fix_categories["primary"] = {key: categories_by_origin[primary_name][key] for key in sorted(categories_by_origin[primary_name])}
    if fallback_categories:
        fix_categories["fallback"] = {key: fallback_categories[key] for key in sorted(fallback_categories)}
    for p in providers[1:]:
        if categories_by_origin.get(p):
            fix_categories[p] = {key: categories_by_origin[p][key] for key in sorted(categories_by_origin[p])}
    if categories_by_origin.get("unknown"):
        fix_categories["unknown"] = {key: categories_by_origin["unknown"][key] for key in sorted(categories_by_origin["unknown"])}

    return {
        "reviewer_chapters": chapters,
        "reviewed_paragraphs": paragraphs,
        "fixes_reported": reported,
        "fixes_applied": applied,
        "fixes_reported_by_translation_provider": {key: reported_by_origin[key] for key in all_keys},
        "fixes_applied_by_translation_provider": {key: applied_by_origin[key] for key in all_keys},
        "fix_categories_reported": fix_categories,
    }


def generate_work_report(
    *,
    workspace: Path,
    book: str,
    primary_translator: str,
    fallback_translator: str = "",
    fallback_translators: list[str] | None = None,
    reviewer: str,
    novel_root: Path,
    manifest: dict[str, Any],
    layout: str = "preserve",
) -> Path:
    fb_list: list[str] = []
    if fallback_translators:
        fb_list = list(fallback_translators)
    elif fallback_translator:
        fb_list = [fallback_translator]
    else:
        fb_list = ["opencode", "lmstudio"]

    all_providers = [primary_translator] + [p for p in fb_list if p != primary_translator]

    provenance = _read_json(workspace / "data" / "translation-provenance.json", {"items": {}})
    diagnostics = _read_json(workspace / "data" / "provider-diagnostics.json", {"attempts": []})
    counts, _origins = _provider_counts(provenance, all_providers)
    total = sum(counts.values())
    review = _review_summary(workspace, _origins, all_providers)

    fallbacks_payload = []
    for fb in fb_list:
        fb_count = counts.get(fb, 0)
        fallbacks_payload.append({
            "provider": fb,
            "model": _model(fb, "translator", novel_root),
            "paragraphs": fb_count,
            "percentage": round(fb_count * 100 / total, 2) if total else 0,
        })

    first_fb = fb_list[0] if fb_list else "opencode"
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_type": "translation_work_report",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "book": {
            "id": book,
            "title": str(manifest.get("title", book)),
            "chapters": len(manifest.get("chapters", [])),
            "paragraphs": sum(len(c.get("paragraphs", [])) for c in manifest.get("chapters", [])),
        },
        "translation": {
            "total_translated": total,
            "primary": {
                "provider": primary_translator,
                "model": _model(primary_translator, "translator", novel_root),
                "paragraphs": counts.get(primary_translator, 0),
                "percentage": round(counts.get(primary_translator, 0) * 100 / total, 2) if total else 0,
            },
            "fallbacks": fallbacks_payload,
            "fallback": {
                "provider": first_fb,
                "model": _model(first_fb, "translator", novel_root),
                "paragraphs": counts.get(first_fb, 0),
                "percentage": round(counts.get(first_fb, 0) * 100 / total, 2) if total else 0,
            },
            "fallback_reasons": _diagnostic_summary(diagnostics, primary_translator, first_fb),
        },
        "review": {
            "provider": reviewer,
            "model": _model(reviewer, "reviewer", novel_root),
            **review,
        },
        "quality": {
            "pending_paragraphs": sum(1 for c in manifest.get("chapters", []) for p in c.get("paragraphs", []) if not str(p.get("translated", "")).strip()),
            "provider_attempts": len(diagnostics.get("attempts", [])),
        },
        "export": {
            "layout": layout,
            "postprocess": layout == "horizontal",
        },
        "artifacts": {
            "workspace": str(workspace.resolve()),
            "provenance": str((workspace / "data" / "translation-provenance.json").resolve()),
            "diagnostics": str((workspace / "data" / "provider-diagnostics.json").resolve()),
            "report": str((workspace / "reports" / "work-report.yaml").resolve()),
        },
    }
    path = workspace / "reports" / "work-report.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_fixed_yaml(report), encoding="utf-8")
    return path
