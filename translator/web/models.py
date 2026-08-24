from __future__ import annotations

from typing import Any
from pydantic import BaseModel, ConfigDict, Field, model_validator


class BookSummary(BaseModel):
    id: str
    name: str
    source_type: str = "epub"
    total_chapters: int = 0
    translated_chapters: int = 0
    total_paragraphs: int = 0
    translated_paragraphs: int = 0
    progress_percentage: float = 0.0
    status: str = "pending"  # pending, translating, reviewing, completed, paused, error
    has_output_epub: bool = False
    epub_download_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ChapterSummary(BaseModel):
    id: str
    index: int
    title: str
    total_paragraphs: int = 0
    translated_paragraphs: int = 0
    status: str = "pending"  # pending, translated, reviewed
    auto_fixed_count: int = 0


class ParagraphItem(BaseModel):
    id: str
    index: int
    chapter_id: str
    source: str
    translated: str = ""
    status: str = "pending"  # pending, translated, fallback_recovered, review_fixed, manually_edited
    provider: str | None = None
    fallback_from: str | None = None
    fallback_reason: str | None = None
    duration_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChapterDetail(BaseModel):
    id: str
    index: int
    title: str
    total_paragraphs: int
    translated_paragraphs: int
    status: str
    paragraphs: list[ParagraphItem]
    chapter_summary: str = ""
    auto_fixed_count: int = 0


class ParagraphUpdateRequest(BaseModel):
    translated: str


class PipelineStartRequest(BaseModel):
    book_id: str
    apply: bool = True
    autonomous: bool = True
    finalize: bool = True
    layout: str = "horizontal"  # horizontal or preserve
    primary_translator: str | None = None
    fallback_translators: list[str] | None = None
    reviewer: str | None = None
    translation_policy: str | None = None
    max_cycles: int = 1000


class RetranslateParagraphRequest(BaseModel):
    book_id: str
    chapter_id: str
    paragraph_id: str
    provider: str | None = None


class TaskStatusResponse(BaseModel):
    task_id: str
    book_id: str
    status: str  # idle, running, paused, completed, failed, stopped
    phase: str = "idle"  # queued, initializing, translating, reviewing, finalizing, idle
    reviewer_states: dict[str, str] = Field(default_factory=dict)
    overall_progress: float = 0.0
    current_chapter: str = ""
    current_chapter_index: int = 0
    total_chapters: int = 0
    current_batch: int = 0
    total_batches: int = 0
    recovered_paragraphs: int = 0
    message: str = ""
    error_detail: str | None = None
    started_at: str | None = None
    updated_at: str | None = None


class GlossaryItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str
    target: str
    category: str = "general"  # character, location, skill, organization, general
    confidence: float = 1.0
    note: str = ""
    first_seen_chunk: str | None = None
    last_seen_chunk: str | None = None
    occurrences: int = 0
    sample_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def read_legacy_aliases(cls, raw: Any) -> Any:
        if isinstance(raw, dict):
            value = dict(raw)
            value.setdefault("note", value.get("notes", ""))
            value.setdefault("first_seen_chunk", value.get("first_chapter"))
            return value
        return raw


class GlossaryUpsert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str
    target: str
    category: str = "general"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    note: str = ""

    @model_validator(mode="before")
    @classmethod
    def read_legacy_note(cls, raw: Any) -> Any:
        if isinstance(raw, dict):
            value = dict(raw)
            value.setdefault("note", value.get("notes", ""))
            return value
        return raw


class GlossaryResponse(BaseModel):
    book_id: str
    terms: list[GlossaryItem]
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    updated_at: str | None = None


class GlossaryCreateRequest(BaseModel):
    terms: list[GlossaryUpsert]


class BookMemoryResponse(BaseModel):
    book_id: str
    characters: list[dict[str, Any]] = Field(default_factory=list)
    world_settings: list[dict[str, Any]] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)
    chapter_states: list[dict[str, Any]] = Field(default_factory=list)


class PreflightProviderResult(BaseModel):
    provider: str
    type: str
    role: str = ""
    status: str  # ok, failed, warning
    latency_ms: float = 0.0
    model: str = ""
    message: str = ""


class PreflightResponse(BaseModel):
    all_passed: bool
    results: list[PreflightProviderResult]


class QueueItem(BaseModel):
    id: str
    book_id: str
    book_name: str
    source_type: str = "epub"
    options: PipelineStartRequest
    status: str = "pending"  # pending, running, paused, completed, failed, cancelled
    phase: str = "queued"  # queued, initializing, translating, reviewing, finalizing, idle
    reviewer_states: dict[str, str] = Field(default_factory=dict)
    order_index: int = 0
    priority: int = 0
    overall_progress: float = 0.0
    current_chapter: str = ""
    current_chapter_index: int = 0
    total_chapters: int = 0
    message: str = "等待队列调度..."
    error_detail: str | None = None
    enqueued_at: str
    started_at: str | None = None
    completed_at: str | None = None
    updated_at: str | None = None
    retry_count: int = 0
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    process_id: str | None = None
    recovery_reason: str | None = None


class QueueStatusResponse(BaseModel):
    is_paused: bool = False
    concurrency: int = 1
    total_items: int = 0
    running_count: int = 0
    pending_count: int = 0
    completed_count: int = 0
    failed_count: int = 0
    items: list[QueueItem] = Field(default_factory=list)


class EnqueueRequest(BaseModel):
    book_ids: list[str]
    options: PipelineStartRequest | None = None
    insert_front: bool = False


class QueueItemMoveRequest(BaseModel):
    direction: str  # up, down, top


class QueueReorderRequest(BaseModel):
    item_ids: list[str]


class QueueClearRequest(BaseModel):
    scope: str = "completed"  # completed, failed, all_finished


class QueueConfigUpdateRequest(BaseModel):
    concurrency: int | None = None
    stop_on_error: bool | None = None
