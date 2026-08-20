from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from scripts.config import load_config, setting


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


def _model(provider: str, role: str, novel_root: Path) -> str:
    config = load_config()
    if provider == "antigravity":
        return setting(config, "providers.antigravity.model", "PRIMARY_MODEL")
    if provider == "lmstudio":
        novel_setting = novel_root / "setting.toml"
        if novel_setting.exists():
            for line in novel_setting.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("model ="):
                    return line.split("=", 1)[1].strip().strip('"\'')
        return setting(config, "providers.lmstudio.model", "MURASAKI_MODEL")
    role_env = "OPENCODE_REVIEWER_MODEL" if role == "reviewer" else "OPENCODE_TRANSLATOR_MODEL"
    if role_env in os.environ:
        return os.environ[role_env]
    model = setting(config, f"providers.{provider}.model", "OPENCODE_MODEL" if provider == "opencode" else "CODEX_MODEL")
    return model or "(configured default)"


def _provider_counts(provenance: dict[str, Any], primary: str, fallback: str) -> tuple[dict[str, int], dict[str, str]]:
    counts = Counter()
    origins: dict[str, str] = {}
    for item_id, item in provenance.get("items", {}).items():
        provider = str(item.get("provider", "unknown"))
        counts[provider] += 1
        origins[str(item_id)] = provider
    return {primary: counts[primary], fallback: counts[fallback]}, origins


def _diagnostic_summary(diagnostics: dict[str, Any], primary: str, fallback: str) -> dict[str, Any]:
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


def _review_summary(workspace: Path, origins: dict[str, str], primary: str, fallback: str) -> dict[str, Any]:
    reported = 0
    applied = 0
    reported_by_origin = Counter()
    applied_by_origin = Counter()
    categories = Counter()
    categories_by_origin = {
        primary: Counter(),
        fallback: Counter(),
        "unknown": Counter(),
    }
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
    return {
        "reviewer_chapters": chapters,
        "reviewed_paragraphs": paragraphs,
        "fixes_reported": reported,
        "fixes_applied": applied,
        "fixes_reported_by_translation_provider": {key: reported_by_origin[key] for key in (primary, fallback, "unknown")},
        "fixes_applied_by_translation_provider": {key: applied_by_origin[key] for key in (primary, fallback, "unknown")},
        "fix_categories_reported": {
            "total": {key: categories[key] for key in sorted(categories)},
            "primary": {key: categories_by_origin[primary][key] for key in sorted(categories_by_origin[primary])},
            "fallback": {key: categories_by_origin[fallback][key] for key in sorted(categories_by_origin[fallback])},
            "unknown": {key: categories_by_origin["unknown"][key] for key in sorted(categories_by_origin["unknown"])},
        },
    }


def generate_work_report(
    *,
    workspace: Path,
    book: str,
    primary_translator: str,
    fallback_translator: str,
    reviewer: str,
    novel_root: Path,
    manifest: dict[str, Any],
    layout: str = "preserve",
) -> Path:
    provenance = _read_json(workspace / "data" / "translation-provenance.json", {"items": {}})
    diagnostics = _read_json(workspace / "data" / "provider-diagnostics.json", {"attempts": []})
    counts, _origins = _provider_counts(provenance, primary_translator, fallback_translator)
    total = sum(counts.values())
    review = _review_summary(workspace, _origins, primary_translator, fallback_translator)
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
            "primary": {"provider": primary_translator, "model": _model(primary_translator, "translator", novel_root), "paragraphs": counts[primary_translator], "percentage": round(counts[primary_translator] * 100 / total, 2) if total else 0},
            "fallback": {"provider": fallback_translator, "model": _model(fallback_translator, "translator", novel_root), "paragraphs": counts[fallback_translator], "percentage": round(counts[fallback_translator] * 100 / total, 2) if total else 0},
            "fallback_reasons": _diagnostic_summary(diagnostics, primary_translator, fallback_translator),
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
