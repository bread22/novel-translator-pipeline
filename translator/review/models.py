from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GlossaryEntry(StrictModel):
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    category: str = "other"
    note: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reporters: list[str] = Field(default_factory=list)


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
    category: str = "context_conflict"
    severity: str = "major"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    replacement: str = ""
    auto_apply: bool = False
    approved_translation: str | None = None
    consensus: bool | None = None
    reporters: list[str] = Field(default_factory=list)
    invalid_reason: str | None = None


class ChapterReviewOutput(StrictModel):
    schema_version: str = "2.0"
    checked_ids: list[str] = Field(default_factory=list)
    fixes: list[ChapterFix] = Field(default_factory=list)
    glossary_delta: GlossaryDelta = Field(default_factory=GlossaryDelta)
    memory_delta: MemoryDelta = Field(default_factory=MemoryDelta)
    chapter_state: ChapterState = Field(default_factory=ChapterState)
    dual_review: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_shapes(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        value = dict(raw)
        glossary = value.get("glossary_delta") if isinstance(value.get("glossary_delta"), dict) else {}
        value["glossary_delta"] = {
            "add": list(glossary.get("add", []) or []),
            "update": list(glossary.get("update", []) or []),
            "conflicts": list(glossary.get("conflicts", []) or []),
        }

        memory = value.get("memory_delta") if isinstance(value.get("memory_delta"), dict) else {}
        if any(key in memory for key in ("add", "update", "conflicts")):
            normalized_memory = {
                "add": list(memory.get("add", []) or []),
                "update": list(memory.get("update", []) or []),
                "conflicts": list(memory.get("conflicts", []) or []),
            }
        else:
            legacy_entries: list[dict[str, Any]] = []
            for collection, category in (("characters", "character"), ("world_settings", "fact"), ("entries", "fact")):
                for item in memory.get(collection, []) if isinstance(memory.get(collection), list) else []:
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get("key") or item.get("name") or item.get("term") or "").strip()
                    item_value = str(item.get("value") or item.get("summary") or item.get("explanation") or key).strip()
                    if key and item_value:
                        legacy_entries.append({"key": key, "value": item_value, "category": category})
            for key, item_value in memory.items():
                if key not in {"characters", "world_settings", "entries", "plot_hints"} and isinstance(item_value, str):
                    legacy_entries.append({"key": key, "value": item_value})
            normalized_memory = {"add": legacy_entries, "update": [], "conflicts": []}
        value["memory_delta"] = normalized_memory

        state = value.get("chapter_state") if isinstance(value.get("chapter_state"), dict) else {}
        state = dict(state)
        if "important_changes" not in state and "significant_changes" in state:
            state["important_changes"] = state.pop("significant_changes")
        value["chapter_state"] = state
        return value

    @model_validator(mode="after")
    def unique_checked_ids(self) -> "ChapterReviewOutput":
        self.checked_ids = list(dict.fromkeys(self.checked_ids))
        return self


class GlobalReviewOutput(StrictModel):
    schema_version: str = "2.0"
    checked_chapters: list[str] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    recommendations: list[dict[str, Any] | str] = Field(default_factory=list)
