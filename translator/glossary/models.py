from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from translator.glossary.taxonomy import Category


class GlossaryCandidate(BaseModel):
    """The only shape a reviewer/extractor may return for a glossary candidate."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    category: Category
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_ids: list[str]
    note: str = ""


class TermEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str
    paragraph_id: str
    reporter: str
    confidence: float = Field(ge=0.0, le=1.0)


class GlossaryTerm(BaseModel):
    """Typed view of a persisted v3 term; file readers may still preserve unknown legacy fields."""

    model_config = ConfigDict(extra="ignore")

    term_id: str
    source: str
    source_normalized: str
    target: str
    category: Category
    status: str
    confidence: float = Field(ge=0.0, le=1.0)
    canonical_term_id: str | None = None
    note: str = ""
    first_seen_chunk: str = ""
    last_seen_chunk: str = ""
    occurrences: int = 0
    chapter_count: int = 0
    sample_ids: list[str] = Field(default_factory=list)
    evidence: list[TermEvidence] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    retired_reason: str | None = None


class GlossaryV3(BaseModel):
    """Top-level document contract used at file/API boundaries."""

    model_config = ConfigDict(extra="allow")

    schema_version: Literal["3.0"] = "3.0"
    book: str = ""
    terms: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    revisions: list[dict[str, Any]] = Field(default_factory=list)


GlossaryDocument = GlossaryV3
