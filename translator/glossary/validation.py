from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Mapping

from translator.glossary.models import GlossaryCandidate
from translator.glossary.taxonomy import CategoryTier, canonical_category, category_tier


KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
TARGET_FORBIDDEN_RE = re.compile(r"[/|\\\n\r（）()【】\[\]]")
SOURCE_SENTENCE_RE = re.compile(r"[。！？!?\n\r]")


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str = ""
    category_tier: CategoryTier | None = None
    candidate: GlossaryCandidate | None = None
    evidence_ids: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.valid


def _evidence_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return str(value.get("source") or value.get("text") or value.get("original") or "")
    return ""


def validate_term_candidate(
    candidate: GlossaryCandidate | Mapping[str, Any],
    *,
    evidence_texts: Mapping[str, Any],
    existing_evidence_ids: set[str] | None = None,
) -> ValidationResult:
    """Validate shape, taxonomy and evidence without consulting a language model."""
    raw = candidate.model_dump() if isinstance(candidate, GlossaryCandidate) else dict(candidate)
    if "confidence" not in raw:
        return ValidationResult(False, "missing_confidence")
    # Review compatibility metadata is not part of the candidate contract.
    raw = {key: value for key, value in raw.items() if key in {"source", "target", "category", "confidence", "evidence_ids", "note"}}
    try:
        model = GlossaryCandidate.model_validate(raw)
    except Exception as exc:
        return ValidationResult(False, f"schema:{exc}")

    source = unicodedata.normalize("NFKC", model.source).strip()
    target = unicodedata.normalize("NFKC", model.target).strip()
    category = canonical_category(model.category)
    tier = category_tier(category)
    if tier is None:
        return ValidationResult(False, "unknown_category")
    if tier is CategoryTier.BLOCKED:
        return ValidationResult(False, "blocked_category", tier, model)
    if not source or not target:
        return ValidationResult(False, "empty_source_or_target", tier, model)
    if TARGET_FORBIDDEN_RE.search(target) or "\t" in target:
        return ValidationResult(False, "unclean_target", tier, model)
    if KANA_RE.search(target):
        return ValidationResult(False, "target_contains_japanese_kana", tier, model)
    if len(source) > 80 or SOURCE_SENTENCE_RE.search(source):
        return ValidationResult(False, "source_is_sentence_or_too_long", tier, model)
    if len(source.split()) > 8 or len(target) > 80:
        return ValidationResult(False, "term_shape_too_long", tier, model)
    note = str(model.note or "").strip()
    if len(note) > 120 or "\n" in note or "\r" in note:
        return ValidationResult(False, "note_too_long", tier, model)

    evidence_ids = tuple(dict.fromkeys(str(item).strip() for item in model.evidence_ids if str(item).strip()))
    known_ids = set(evidence_texts)
    if existing_evidence_ids:
        known_ids |= existing_evidence_ids
    if not evidence_ids:
        return ValidationResult(False, "missing_evidence", tier, model)
    missing = [item for item in evidence_ids if item not in known_ids]
    if missing:
        return ValidationResult(False, "unknown_evidence_id:" + ",".join(sorted(missing)), tier, model, evidence_ids)
    for evidence_id in evidence_ids:
        text = unicodedata.normalize("NFKC", _evidence_text(evidence_texts.get(evidence_id, source)))
        if source not in text:
            return ValidationResult(False, f"source_not_in_evidence:{evidence_id}", tier, model, evidence_ids)
    normalized = model.model_copy(update={"source": source, "target": target, "category": category, "note": note, "evidence_ids": list(evidence_ids)})
    return ValidationResult(True, "", tier, normalized, evidence_ids)


def validate_glossary_document(document: Mapping[str, Any]) -> list[str]:
    """Return deterministic v3 document errors; an empty list means it reopens cleanly."""
    errors: list[str] = []
    if document.get("schema_version") != "3.0":
        errors.append("schema_version")
    if not isinstance(document.get("terms", []), list):
        return ["terms_not_list"]
    seen: dict[str, str] = {}
    for index, raw in enumerate(document.get("terms", [])):
        if not isinstance(raw, Mapping):
            errors.append(f"term[{index}]:not_object")
            continue
        source = str(raw.get("source", "")).strip()
        normalized = str(raw.get("source_normalized") or unicodedata.normalize("NFKC", source)).strip()
        status = str(raw.get("status", ""))
        if not source or not normalized:
            errors.append(f"term[{index}]:empty_source")
        if status not in {"candidate", "active", "disputed", "revised", "retired"}:
            errors.append(f"term[{index}]:status")
        term_tier = category_tier(raw.get("category"))
        if term_tier is None:
            errors.append(f"term[{index}]:category")
        evidence = raw.get("evidence", [])
        if status == "active" and not isinstance(evidence, list) or status == "active" and not evidence:
            errors.append(f"term[{index}]:active_without_evidence")
        if status == "active" and term_tier is CategoryTier.BLOCKED:
            errors.append(f"term[{index}]:active_blocked_category")
        if normalized in seen and status == "active" and seen[normalized] != str(raw.get("target", "")):
            errors.append(f"term[{index}]:duplicate_active_source")
        if status == "active":
            seen[normalized] = str(raw.get("target", ""))
    return errors
