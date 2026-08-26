from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Any, Mapping

from translator.glossary.models import GlossaryCandidate
from translator.glossary.name_validation import NameCheckResult, check_person_name
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
    discarded_evidence: tuple[tuple[str, str], ...] = ()
    name_check: NameCheckResult | None = None

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
    """Validate shape, taxonomy, deterministic names and evidence without an LLM."""
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

    name_check = check_person_name(source, target, category)
    if name_check is not None:
        if name_check.status == "ambiguous":
            return ValidationResult(
                False,
                f"name_mapping_ambiguous:{name_check.reason}",
                tier,
                model,
                name_check=name_check,
            )
        # Glossary stores the bare name. Honorifics remain in paragraph text and
        # are deliberately excluded from the candidate mapping.
        model = model.model_copy(update={
            "source": name_check.name_source,
            "target": name_check.expected_target,
        })
        source = name_check.name_source
        target = name_check.expected_target

    if TARGET_FORBIDDEN_RE.search(target) or "\t" in target:
        return ValidationResult(False, "unclean_target", tier, model, name_check=name_check)
    if KANA_RE.search(target):
        return ValidationResult(False, "target_contains_japanese_kana", tier, model, name_check=name_check)
    if len(source) > 80 or SOURCE_SENTENCE_RE.search(source):
        return ValidationResult(False, "source_is_sentence_or_too_long", tier, model, name_check=name_check)
    if len(source.split()) > 8 or len(target) > 80:
        return ValidationResult(False, "term_shape_too_long", tier, model, name_check=name_check)
    note = str(model.note or "").strip()
    if len(note) > 120 or "\n" in note or "\r" in note:
        return ValidationResult(False, "note_too_long", tier, model, name_check=name_check)

    evidence_ids = tuple(dict.fromkeys(str(item).strip() for item in model.evidence_ids if str(item).strip()))
    known_ids = set(evidence_texts)
    if existing_evidence_ids:
        known_ids |= existing_evidence_ids
    if not evidence_ids:
        return ValidationResult(False, "missing_evidence", tier, model, name_check=name_check)
    valid_evidence_ids: list[str] = []
    discarded_evidence: list[tuple[str, str]] = []
    for evidence_id in evidence_ids:
        if evidence_id not in known_ids:
            discarded_evidence.append((evidence_id, "unknown_evidence_id"))
            continue
        text = unicodedata.normalize("NFKC", _evidence_text(evidence_texts.get(evidence_id, source)))
        if source not in text:
            discarded_evidence.append((evidence_id, "source_not_in_evidence"))
            continue
        valid_evidence_ids.append(evidence_id)

    if not valid_evidence_ids:
        reason = discarded_evidence[0][1] if discarded_evidence else "missing_evidence"
        if reason == "unknown_evidence_id":
            reason = "unknown_evidence_id:" + ",".join(sorted(item for item, _ in discarded_evidence))
        elif reason == "source_not_in_evidence":
            reason = "source_not_in_evidence:" + discarded_evidence[0][0]
        return ValidationResult(False, reason, tier, model, evidence_ids, tuple(discarded_evidence), name_check)
    normalized = model.model_copy(update={
        "source": source,
        "target": target,
        "category": category,
        "note": note,
        "evidence_ids": valid_evidence_ids,
    })
    return ValidationResult(True, "", tier, normalized, tuple(valid_evidence_ids), tuple(discarded_evidence), name_check)


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
