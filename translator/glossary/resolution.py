from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from translator.glossary.models import GlossaryCandidate


@dataclass(frozen=True)
class ConflictResolution:
    status: str
    chosen_target: str
    score_existing: float
    score_proposed: float
    reason: str
    revision: dict[str, Any] | None = None


def _support(records: list[Mapping[str, Any]]) -> tuple[int, int, int]:
    reporters = {str(item.get("reporter", "")) for item in records if item.get("reporter")}
    chapters = {str(item.get("chapter_id", "")) for item in records if item.get("chapter_id")}
    paragraphs = {str(item.get("paragraph_id", "")) for item in records if item.get("paragraph_id")}
    return len(reporters), len(chapters), len(paragraphs)


def _score(*, confidence: float, evidence_count: int, reporters: int, chapters: int, canonical: bool = False) -> float:
    return (
        confidence * 10.0
        + min(evidence_count, 10) * 1.5
        + reporters * 2.0
        + chapters * 1.5
        + (2.0 if canonical else 0.0)
    )


def resolve_term_conflict(
    existing: Mapping[str, Any],
    proposal: GlossaryCandidate | Mapping[str, Any],
    *,
    proposal_evidence: list[Mapping[str, Any]] | None = None,
) -> ConflictResolution:
    """Resolve only when the new spelling has independent, explainable support."""
    raw = proposal.model_dump() if isinstance(proposal, GlossaryCandidate) else dict(proposal)
    evidence = proposal_evidence or []
    existing_evidence = [item for item in existing.get("evidence", []) if isinstance(item, Mapping)]
    proposed_reporters, proposed_chapters, proposed_paragraphs = _support(evidence)
    existing_reporters, existing_chapters, _ = _support(existing_evidence)
    proposed_score = _score(
        confidence=float(raw.get("confidence", 0) or 0),
        evidence_count=len(evidence), reporters=proposed_reporters,
        chapters=proposed_chapters, canonical=bool(raw.get("canonical_term_id")),
    )
    existing_score = _score(
        confidence=float(existing.get("confidence", 0) or 0),
        evidence_count=len(existing_evidence), reporters=existing_reporters,
        chapters=existing_chapters, canonical=bool(existing.get("canonical_term_id")),
    )
    independent = proposed_reporters >= 2 or proposed_chapters >= 2 or proposed_paragraphs >= 2
    if independent and proposed_score > existing_score:
        revision = {
            "source": existing.get("source", raw.get("source", "")),
            "source_normalized": existing.get("source_normalized", ""),
            "baseline_target": existing.get("target", ""),
            "new_target": raw.get("target", ""),
            "reason": "independent evidence score exceeded previous target",
        }
        return ConflictResolution("revised", str(raw.get("target", "")), existing_score, proposed_score, "independent_strong_evidence", revision)
    if abs(proposed_score - existing_score) < 2.0 or not independent:
        return ConflictResolution("disputed", str(existing.get("target", "")), existing_score, proposed_score, "evidence_is_not_independent_or_scores_are_close")
    return ConflictResolution("disputed", str(existing.get("target", "")), existing_score, proposed_score, "stable_target_not_overridden_by_one_report")
