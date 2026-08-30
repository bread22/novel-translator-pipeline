from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import unicodedata
from typing import Any, Iterable, Mapping, Sequence

from translator.glossary.models import GlossaryCandidate
from translator.glossary.name_validation import append_name_mapping_review
from translator.glossary.resolution import resolve_term_conflict
from translator.glossary.taxonomy import BODY_SOURCE_SCOPE, CategoryTier, canonical_category, category_tier, has_independent_support
from translator.glossary.validation import ValidationResult, validate_term_candidate


CANDIDATE_FIELDS = frozenset({"source", "target", "category", "confidence", "evidence_ids", "note", "source_scope"})


def _candidate_reporters(raw: Mapping[str, Any], fallback: str) -> tuple[str, ...]:
    values = raw.get("reporters", [])
    if isinstance(values, str):
        values = [values]
    reporters = tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip())) if isinstance(values, Sequence) else ()
    return reporters or ((fallback,) if fallback else ())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_term_id(source_normalized: str) -> str:
    return hashlib.sha256(source_normalized.encode("utf-8")).hexdigest()[:24]


def _evidence_record(chapter_id: str, paragraph_id: str, reporter: str, confidence: float) -> dict[str, Any]:
    return {
        "chapter_id": str(chapter_id),
        "paragraph_id": str(paragraph_id),
        "reporter": str(reporter),
        "confidence": float(confidence),
    }


def _evidence_key(item: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(item.get("chapter_id", "")), str(item.get("paragraph_id", "")), str(item.get("reporter", "")))


def _normalize_existing(raw: Mapping[str, Any], *, book: str = "") -> dict[str, Any]:
    term = dict(raw)
    source = str(term.get("source", "")).strip()
    source_normalized = str(term.get("source_normalized") or unicodedata.normalize("NFKC", source)).strip()
    term["source"] = source
    term["source_normalized"] = source_normalized
    term["term_id"] = str(term.get("term_id") or stable_term_id(source_normalized))
    term["category"] = canonical_category(term.get("category", "unresolved"))
    term["source_scope"] = str(term.get("source_scope", BODY_SOURCE_SCOPE)).strip().casefold() or BODY_SOURCE_SCOPE
    term["status"] = str(term.get("status", "candidate"))
    term["evidence"] = [dict(item) for item in term.get("evidence", []) if isinstance(item, Mapping)]
    term["provenance"] = list(dict.fromkeys(str(item) for item in term.get("provenance", []) if str(item)))
    term["sample_ids"] = list(dict.fromkeys(str(item) for item in term.get("sample_ids", []) if str(item)))
    term["occurrences"] = len({_evidence_key(item) for item in term["evidence"]})
    term["chapter_count"] = len({str(item.get("chapter_id", "")) for item in term["evidence"] if item.get("chapter_id")})
    if term["status"] == "active" and not term["evidence"]:
        term["status"] = "candidate"
    term.setdefault("confidence", 0.0)
    term.setdefault("canonical_term_id", None)
    term.setdefault("note", "")
    term.setdefault("first_seen_chunk", "")
    term.setdefault("last_seen_chunk", "")
    term.setdefault("created_at", _now())
    term.setdefault("updated_at", _now())
    term.setdefault("retired_reason", None)
    return term


def _proposal_evidence(
    candidate: GlossaryCandidate,
    *,
    chapter_id: str,
    reporters: Sequence[str],
) -> list[dict[str, Any]]:
    return [
        _evidence_record(chapter_id, evidence_id, reporter, candidate.confidence)
        for evidence_id in candidate.evidence_ids
        for reporter in reporters
    ]


def _gate_met(term: Mapping[str, Any], tier: CategoryTier | None) -> bool:
    evidence = [item for item in term.get("evidence", []) if isinstance(item, Mapping)]
    if not evidence or tier is None:
        return False
    if tier not in {CategoryTier.DIRECT_ALLOWED, CategoryTier.GATED_ALLOWED}:
        return False
    return has_independent_support(dict(term)) and float(term.get("confidence", 0) or 0) >= 0.92


def _add_evidence(term: dict[str, Any], evidence: Sequence[Mapping[str, Any]]) -> int:
    current = {_evidence_key(item) for item in term.get("evidence", []) if isinstance(item, Mapping)}
    added = 0
    term.setdefault("evidence", [])
    for item in evidence:
        key = _evidence_key(item)
        if key in current or not key[1]:
            continue
        term["evidence"].append(dict(item))
        current.add(key)
        added += 1
        paragraph_id = key[1]
        if paragraph_id not in term.setdefault("sample_ids", []):
            term["sample_ids"].append(paragraph_id)
    term["occurrences"] = len({_evidence_key(item) for item in term["evidence"]})
    term["chapter_count"] = len({str(item.get("chapter_id", "")) for item in term["evidence"] if item.get("chapter_id")})
    return added


def merge_term_candidates(
    glossary: Mapping[str, Any],
    candidates: Iterable[GlossaryCandidate | Mapping[str, Any]],
    *,
    chapter_id: str = "",
    reporter: str = "chapter_reviewer",
    evidence_texts: Mapping[str, Any] | None = None,
    name_mapping_queue_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Merge v3 candidates idempotently and calculate lifecycle state in code."""
    current = dict(glossary or {})
    terms = [_normalize_existing(item) for item in current.get("terms", []) if isinstance(item, Mapping)]
    conflicts = [dict(item) for item in current.get("conflicts", []) if isinstance(item, Mapping)]
    revisions = [dict(item) for item in current.get("revisions", []) if isinstance(item, Mapping)]
    by_source = {str(item.get("source_normalized")): item for item in terms if item.get("source_normalized")}
    # Reconstruct pending support from durable conflict records so a retry or a
    # later chapter can complete the same resolution without a private counter.
    for conflict in conflicts:
        source_key = str(conflict.get("source_normalized") or "")
        term = by_source.get(source_key)
        proposed_target = str(conflict.get("proposed_target") or "")
        if term is None or not proposed_target:
            continue
        support = term.setdefault("conflict_support", {}).setdefault(proposed_target, [])
        for evidence_id in conflict.get("evidence_ids", []) if isinstance(conflict.get("evidence_ids", []), list) else []:
            record = _evidence_record(
                str(conflict.get("chapter_id", "")), str(evidence_id), str(conflict.get("reporter", "")), float(conflict.get("confidence", 0) or 0)
            )
            if _evidence_key(record) not in {_evidence_key(item) for item in support}:
                support.append(record)
    caller_supplied_evidence = evidence_texts is not None
    evidence_texts = dict(evidence_texts or {})
    summary = {
        "reported": 0, "accepted_candidates": 0, "added": 0, "activated": 0, "confirmed": 0,
        "rejected": 0, "blocked_by_category": 0, "blocked_by_shape": 0, "blocked_by_evidence": 0,
        "evidence_total": 0, "evidence_valid": 0, "evidence_discarded": 0,
        "conflicted": 0, "disputed": 0, "revised": 0, "retired": 0,
        "name_normalized": 0, "blocked_by_name": 0, "name_review_queued": 0,
        "blocked_by_recurrence": 0,
    }
    for raw in candidates:
        summary["reported"] += 1
        raw_dict = raw.model_dump() if isinstance(raw, GlossaryCandidate) else dict(raw) if isinstance(raw, Mapping) else {}
        reporters = _candidate_reporters(raw_dict, reporter)
        candidate_payload = {key: raw_dict[key] for key in CANDIDATE_FIELDS if key in raw_dict}
        try:
            category = canonical_category(candidate_payload.get("category", ""))
            if category:
                candidate_payload["category"] = category
            candidate = GlossaryCandidate.model_validate(candidate_payload)
        except Exception:
            summary["rejected"] += 1
            summary["blocked_by_shape"] += 1
            continue
        # A unit caller may not have a manifest text map; actual pipeline callers always do.
        validation_texts = evidence_texts if caller_supplied_evidence else {
            str(item): candidate.source for item in candidate.evidence_ids
        }
        validation: ValidationResult = validate_term_candidate(candidate, evidence_texts=validation_texts)
        if not validation.valid:
            summary["rejected"] += 1
            summary["evidence_total"] += len(validation.evidence_ids) + len(validation.discarded_evidence)
            summary["evidence_valid"] += len(validation.evidence_ids)
            summary["evidence_discarded"] += len(validation.discarded_evidence)
            if validation.reason.startswith("name_mapping_"):
                summary["blocked_by_name"] += 1
                if validation.name_check is not None and name_mapping_queue_path is not None:
                    if append_name_mapping_review(
                        name_mapping_queue_path,
                        validation.name_check,
                        chapter_id=chapter_id,
                        reporter=reporter,
                        evidence_ids=validation.evidence_ids or tuple(candidate.evidence_ids),
                    ):
                        summary["name_review_queued"] += 1
            elif validation.reason == "blocked_category":
                summary["blocked_by_category"] += 1
            elif validation.reason == "metadata_source":
                summary["blocked_by_shape"] += 1
            elif "evidence" in validation.reason:
                summary["blocked_by_evidence"] += 1
            else:
                summary["blocked_by_shape"] += 1
            continue
        candidate = validation.candidate or candidate
        summary["evidence_total"] += len(validation.evidence_ids) + len(validation.discarded_evidence)
        summary["evidence_valid"] += len(validation.evidence_ids)
        summary["evidence_discarded"] += len(validation.discarded_evidence)
        summary["accepted_candidates"] += 1
        if validation.name_check is not None and validation.name_check.status == "corrected":
            summary["name_normalized"] += 1
        source_normalized = unicodedata.normalize("NFKC", candidate.source).strip()
        evidence = _proposal_evidence(candidate, chapter_id=chapter_id, reporters=reporters)
        existing = by_source.get(source_normalized)
        if existing is None:
            now = _now()
            existing = {
                "term_id": stable_term_id(source_normalized),
                "source": candidate.source,
                "source_normalized": source_normalized,
                "target": candidate.target,
                "category": candidate.category,
                "source_scope": getattr(candidate, "source_scope", BODY_SOURCE_SCOPE),
                "status": "candidate",
                "confidence": candidate.confidence,
                "canonical_term_id": None,
                "note": candidate.note,
                "first_seen_chunk": chapter_id,
                "last_seen_chunk": chapter_id,
                "occurrences": 0,
                "chapter_count": 0,
                "sample_ids": [],
                "evidence": [],
                "provenance": [],
                "created_at": now,
                "updated_at": now,
                "retired_reason": None,
            }
            terms.append(existing)
            by_source[source_normalized] = existing
            summary["added"] += 1
        existing.setdefault("conflict_support", {})
        if str(existing.get("target", "")).strip() != candidate.target:
            summary["conflicted"] += 1
            conflict_support = existing["conflict_support"].setdefault(candidate.target, [])
            for item in evidence:
                if _evidence_key(item) not in {_evidence_key(old) for old in conflict_support}:
                    conflict_support.append(item)
            resolution = resolve_term_conflict(existing, candidate, proposal_evidence=conflict_support)
            conflict = {
                "source": existing.get("source", candidate.source),
                "source_normalized": source_normalized,
                "existing_target": existing.get("target", ""),
                "proposed_target": candidate.target,
                "confidence": candidate.confidence,
                "chapter_id": chapter_id,
                "reporter": reporter,
                "evidence_ids": list(candidate.evidence_ids),
                "resolution": resolution.status,
                "created_at": _now(),
            }
            duplicate_conflict = any(
                str(old.get("source_normalized", "")) == source_normalized
                and str(old.get("proposed_target", "")) == candidate.target
                and str(old.get("chapter_id", "")) == chapter_id
                and str(old.get("reporter", "")) == reporter
                and list(old.get("evidence_ids", []) or []) == list(candidate.evidence_ids)
                for old in conflicts
                if isinstance(old, Mapping)
            )
            if not duplicate_conflict:
                conflicts.append(conflict)
            if resolution.status == "revised":
                # Conflict support is real evidence for the revised target. Keep it
                # in the durable evidence set so recurrence gating is evaluated on
                # the same records that explain the revision.
                _add_evidence(existing, conflict_support)
                revision = dict(resolution.revision or {})
                revision.update({
                    "term_id": existing.get("term_id"),
                    "chapter_id": chapter_id,
                    "reporter": reporter,
                    "evidence": list(conflict_support),
                    "created_at": _now(),
                })
                revisions.append(revision)
                existing["target"] = candidate.target
                existing["status"] = "active"
                existing["confidence"] = max(float(existing.get("confidence", 0) or 0), candidate.confidence)
                summary["revised"] += 1
            else:
                existing["status"] = "disputed"
                summary["disputed"] += 1
            existing["updated_at"] = _now()
            continue

        added_evidence = _add_evidence(existing, evidence)
        existing["confidence"] = max(float(existing.get("confidence", 0) or 0), candidate.confidence)
        existing["last_seen_chunk"] = chapter_id or existing.get("last_seen_chunk", "")
        if candidate.note:
            existing["note"] = candidate.note
        for provenance in reporters:
            if provenance and provenance not in existing.setdefault("provenance", []):
                existing["provenance"].append(provenance)
        tier = category_tier(existing.get("category"))
        was_active = existing.get("status") == "active"
        if existing.get("status") not in {"retired", "revised"} and _gate_met(existing, tier):
            existing["status"] = "active"
        elif existing.get("status") not in {"retired", "revised", "disputed"}:
            existing["status"] = "candidate"
        if added_evidence:
            summary["confirmed"] += 1
            if existing.get("status") == "candidate" and not _gate_met(existing, tier):
                summary["blocked_by_recurrence"] += 1
        if not was_active and existing.get("status") == "active":
            summary["activated"] += 1
        existing["updated_at"] = _now()

    for term in terms:
        term.pop("conflict_support", None)
        term["sample_ids"] = list(dict.fromkeys(term.get("sample_ids", [])))
        term["evidence"] = sorted(term.get("evidence", []), key=lambda item: (_evidence_key(item), str(item.get("confidence", ""))))
        term["occurrences"] = len({_evidence_key(item) for item in term.get("evidence", [])})
        term["chapter_count"] = len({str(item.get("chapter_id", "")) for item in term.get("evidence", []) if item.get("chapter_id")})
        if term.get("status") == "active" and not _gate_met(term, category_tier(term.get("category"))):
            term["status"] = "candidate"
    current.update({
        "schema_version": "3.0",
        "terms": sorted(terms, key=lambda item: str(item.get("source_normalized", ""))),
        "conflicts": conflicts,
        "revisions": revisions,
        "updated_at": _now(),
    })
    return current, summary
