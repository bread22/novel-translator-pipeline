from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from translator.glossary.taxonomy import BLOCKED, Category, canonical_category


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GlossaryEntry(StrictModel):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    category: Category
    note: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str]
    reporters: list[str] = Field(default_factory=list)

    @field_validator("category")
    @classmethod
    def reject_blocked_category(cls, value: str) -> str:
        if value in BLOCKED:
            raise ValueError("BLOCKED taxonomy categories are not glossary candidates")
        return value


class DeltaConflict(StrictModel):
    key: str = Field(min_length=1)
    existing_value: str = ""
    proposed_value: str = ""
    note: str = ""
    reporters: list[str] = Field(default_factory=list)


class GlossaryDelta(StrictModel):
    add: list[GlossaryEntry] = Field(default_factory=list)
    update: list[GlossaryEntry] = Field(default_factory=list)
    conflicts: list[DeltaConflict] = Field(default_factory=list)


class MemoryEntry(StrictModel):
    key: str = Field(min_length=1)
    value: str = Field(min_length=1)
    category: str = "fact"
    note: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reporters: list[str] = Field(default_factory=list)


class MemoryDelta(StrictModel):
    add: list[MemoryEntry] = Field(default_factory=list)
    update: list[MemoryEntry] = Field(default_factory=list)
    conflicts: list[DeltaConflict] = Field(default_factory=list)


class ChapterState(StrictModel):
    summary: str = ""
    important_changes: list[str] = Field(default_factory=list)
    active_entities: list[str] = Field(default_factory=list)
    location: str = ""
    timeline: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class ChapterFix(StrictModel):
    id: str = Field(min_length=1)
    decision: Literal["PASS", "REPORT_ONLY", "FIX_REQUIRED"] = "FIX_REQUIRED"
    category: str = "context_conflict"
    severity: str = "major"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    replacement: str = ""
    operation: Literal["replace", "clear"] = "replace"
    auto_apply: bool = False
    approved_translation: str | None = None
    consensus: bool | None = None
    reporters: list[str] = Field(default_factory=list)
    invalid_reason: str | None = None
    base_translation_hash: str = ""
    apply_state: Literal["not_applied", "blocked", "applied", "failed"] = "not_applied"
    apply_reason: str = ""
    validation_errors: list[str] = Field(default_factory=list)


class ContextFinding(StrictModel):
    id: str = Field(min_length=1)
    category: str = "context_conflict"
    severity: str = "major"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    consensus: bool | None = None
    reporters: list[str] = Field(default_factory=list)


class ChapterReviewOutput(StrictModel):
    schema_version: str = "2.0"
    checked_ids: list[str] = Field(default_factory=list)
    fixes: list[ChapterFix] = Field(default_factory=list)
    context_findings: list[ContextFinding] = Field(default_factory=list)
    dual_review: dict[str, Any] | None = None
    review_diagnostics: dict[str, Any] | None = None

    @model_validator(mode="after")
    def unique_checked_ids(self) -> "ChapterReviewOutput":
        self.checked_ids = list(dict.fromkeys(self.checked_ids))
        return self


class GlobalReviewOutput(StrictModel):
    schema_version: str = "2.0"
    checked_chapters: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any] | str] = Field(default_factory=list)


def normalize_review_for_display(payload: Any) -> tuple[Any, str | None]:
    """Upgrade legacy review data in memory while retaining invalid input for display."""
    try:
        normalized = ChapterReviewOutput.model_validate(payload).model_dump()
    except Exception as exc:
        error_count = exc.error_count() if hasattr(exc, "error_count") else 1
        return payload, f"review schema migration warning: {error_count} validation error(s)"
    return normalized, None
