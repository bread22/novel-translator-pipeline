from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Callable, Mapping

from translator.review.reviewer import has_japanese_kana


@dataclass
class BackfillResult:
    baseline_target: str
    new_target: str
    affected: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline_target": self.baseline_target,
            "new_target": self.new_target,
            "affected": self.affected,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "failed": self.failed,
        }


def _paragraphs(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        paragraph for chapter in manifest.get("chapters", []) if isinstance(chapter, Mapping)
        for paragraph in chapter.get("paragraphs", []) if isinstance(paragraph, dict) and paragraph.get("id")
    ]


def affected_paragraph_ids(manifest: Mapping[str, Any], revision: Mapping[str, Any]) -> list[str]:
    source = str(revision.get("source", "")).strip()
    baseline = str(revision.get("baseline_target", "")).strip()
    ids: list[str] = []
    for paragraph in _paragraphs(manifest):
        original = str(paragraph.get("source", ""))
        translated = str(paragraph.get("translated", ""))
        if (source and source in original) or (baseline and baseline in translated):
            ids.append(str(paragraph["id"]))
    return sorted(dict.fromkeys(ids))


def validate_backfill_text(text: str, *, source: str, target: str, baseline: str = "", baseline_text: str = "") -> str | None:
    if not text.strip():
        return "empty_translation"
    if has_japanese_kana(text):
        return "japanese_kana"
    if source and source in text:
        return "source_left_untranslated"
    if "{{" in text and "}}" not in text:
        return "placeholder_corruption"
    if baseline_text:
        token_re = re.compile(r"\{\{[^{}]+\}\}|\[\[[^\]]+\]\]|<[^>]+>")
        if sorted(token_re.findall(baseline_text)) != sorted(token_re.findall(text)):
            return "placeholder_or_html_tag_corruption"
    # A changed target is expected when the old target was present; for a source-only
    # paragraph the provider may legitimately choose a grammatical inflection.
    if baseline and baseline in text and target and target not in text:
        return "old_target_remains"
    return None


def run_targeted_backfill(
    manifest: Mapping[str, Any],
    revision: Mapping[str, Any],
    *,
    rewrite: Callable[[str, dict[str, Any]], str] | None = None,
) -> BackfillResult:
    """Apply only affected paragraphs; callers persist the returned text atomically."""
    result = BackfillResult(str(revision.get("baseline_target", "")), str(revision.get("new_target", "")))
    result.affected = affected_paragraph_ids(manifest, revision)
    by_id = {str(item["id"]): item for item in _paragraphs(manifest)}
    for item_id in result.affected:
        paragraph = by_id[item_id]
        before = str(paragraph.get("translated", ""))
        try:
            after = rewrite(item_id, paragraph) if rewrite else before
        except Exception:
            result.failed.append(item_id)
            continue
        error = validate_backfill_text(
            after,
            source=str(paragraph.get("source", "")),
            target=result.new_target,
            baseline=result.baseline_target,
            baseline_text=before,
        )
        if error:
            result.failed.append(item_id)
        elif after == before:
            result.unchanged.append(item_id)
        else:
            paragraph["translated"] = after
            result.changed.append(item_id)
    return result
