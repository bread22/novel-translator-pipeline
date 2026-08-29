"""Deterministic, provider-neutral context selection for chapter review requests.

The objects in this module are request projections.  They never mutate the
authoritative glossary, memory, state, or policy objects supplied by callers.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from translator.providers.base import build_review_prompt


RequiredReason = Literal[
    "direct_term_match",
    "speaker_identity",
    "pronoun_resolution_support",
    "active_relationship",
    "relevant_conflict",
    "timeline_constraint",
    "targeted_evidence",
    "global_critical",
    "local_context_minimum",
]


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _chars(value: Any) -> int:
    return len(_canonical(value))


def _stable_id(prefix: str, item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = str(item.get(key, "")).strip()
        if value:
            return f"{prefix}:{value}"
    return f"{prefix}:{_hash(item)[7:23]}"


@dataclass(frozen=True)
class ReviewContextBudget:
    enabled: bool = False
    budget_version: str = "review-context-v2"
    selector_version: str = "review-selector-v1"
    operational_input_hard_limit_chars: int = 50_000
    background_soft_limit_chars: int = 30_000
    operational_headroom_chars: int = 2_000
    local_context_soft_chars: int = 8_000
    glossary_soft_chars: int = 8_000
    memory_soft_chars: int = 14_000
    state_soft_chars: int = 4_000
    book_summary_soft_chars: int = 1_500
    recent_evidence_chapters: int = 3
    entity_relation_hops: int = 1
    max_global_critical_facts: int = 8

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> "ReviewContextBudget":
        raw = raw if isinstance(raw, dict) else {}
        fields = cls.__dataclass_fields__
        return cls(**{key: raw[key] for key in fields if key in raw})


@dataclass(frozen=True)
class SelectedEntry:
    pool: str
    stable_id: str
    value: Any
    required: bool
    required_reason: RequiredReason | None = None
    priority: tuple[Any, ...] = field(default_factory=tuple)

    @property
    def content_hash(self) -> str:
        return _hash(self.value)


@dataclass(frozen=True)
class ReviewContextSnapshot:
    budget_version: str
    selector_version: str
    target: tuple[dict[str, Any], ...]
    context_before: tuple[dict[str, Any], ...]
    context_after: tuple[dict[str, Any], ...]
    glossary: tuple[dict[str, Any], ...]
    memory: dict[str, Any]
    previous_chapter_state: dict[str, Any]
    current_chapter_rolling_state: dict[str, Any]
    policy: str
    entries: tuple[SelectedEntry, ...]

    def semantic_value(self) -> dict[str, Any]:
        return {
            "budget_version": self.budget_version,
            "selector_version": self.selector_version,
            "target": self.target,
            "context_before": self.context_before,
            "context_after": self.context_after,
            "glossary": self.glossary,
            "memory": self.memory,
            "previous_chapter_state": self.previous_chapter_state,
            "current_chapter_rolling_state": self.current_chapter_rolling_state,
            "policy_hash": _hash(self.policy),
            "entries": [
                {
                    "pool": entry.pool,
                    "stable_id": entry.stable_id,
                    "content_hash": entry.content_hash,
                    "required": entry.required,
                    "required_reason": entry.required_reason,
                }
                for entry in self.entries
            ],
        }

    @property
    def context_snapshot_id(self) -> str:
        return _hash(self.semantic_value())


@dataclass(frozen=True)
class MemoryIndexFact:
    stable_id: str
    content_hash: str
    fact_type: str
    status: str
    aliases: tuple[str, ...]
    entity_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    value: dict[str, Any]


@dataclass(frozen=True)
class MemoryIndex:
    projection_version: str
    source_content_hash: str
    facts: tuple[MemoryIndexFact, ...]

    @property
    def content_hash(self) -> str:
        return _hash({
            "projection_version": self.projection_version,
            "source_content_hash": self.source_content_hash,
            "facts": [
                {"stable_id": fact.stable_id, "content_hash": fact.content_hash,
                 "aliases": fact.aliases, "entity_ids": fact.entity_ids}
                for fact in self.facts
            ],
        })


class ReviewContextOverflowError(ValueError):
    """A structural overflow which must not enter provider retry handling."""

    def __init__(self, *, reason: str = "required_context_overflow", diagnostics: dict[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.diagnostics = diagnostics or {}


class ReviewTargetSplitRequired(ReviewContextOverflowError):
    """Signals that the complete-paragraph target must be split and reselected."""

    def __init__(self, diagnostics: dict[str, Any]):
        super().__init__(reason="target_split_required", diagnostics=diagnostics)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return _canonical(value)


def _contains(haystack: str, needle: Any) -> bool:
    value = str(needle or "").strip()
    return bool(value and value.casefold() in haystack.casefold())


def _aliases(item: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ("source", "target", "canonical_name", "name", "key", "entity_id"):
        if item.get(key):
            result.append(str(item[key]))
    for key in ("aliases", "alias", "entities", "entity_ids", "people", "locations", "organizations"):
        raw = item.get(key, [])
        if isinstance(raw, str):
            result.append(raw)
        elif isinstance(raw, list):
            result.extend(str(value) for value in raw if value)
    return list(dict.fromkeys(result))


def _compact_entry(item: dict[str, Any], *, memory: bool = False) -> dict[str, Any]:
    keys = (
        ("fact_id", "id", "key", "value", "category", "status", "scope", "entities", "entity_ids",
         "aliases", "evidence_ids", "chapter_id", "first_seen_chapter", "last_seen_chapter", "confidence",
         "global_critical")
        if memory else
        ("term_id", "id", "source", "target", "canonical_name", "aliases", "category", "status",
         "manual", "locked", "evidence_ids", "first_seen_chapter", "last_seen_chapter", "confidence")
    )
    projected = {key: deepcopy(item[key]) for key in keys if key in item}
    return projected or deepcopy(item)


def _state_projection(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    order = ("active_entities", "open_questions", "important_changes", "location", "timeline", "summary")
    return {key: deepcopy(state[key]) for key in order if key in state and state[key] not in (None, "", [])}


def _memory_collections(memory: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Any]]:
    if not isinstance(memory, dict):
        return [], [], []
    entries = [item for item in memory.get("entries", []) if isinstance(item, dict)]
    # Legacy stores remain selectable without changing their authoritative form.
    for item in memory.get("characters", []) if isinstance(memory.get("characters"), list) else []:
        if isinstance(item, dict):
            entries.append({"key": item.get("name", ""), "value": item.get("summary", ""), "category": "character", **item})
    for item in memory.get("world_settings", []) if isinstance(memory.get("world_settings"), list) else []:
        if isinstance(item, dict):
            entries.append({"key": item.get("term", ""), "value": item.get("explanation", ""), "category": "fact", **item})
    conflicts = [item for item in memory.get("conflicts", []) if isinstance(item, dict)]
    timeline = list(memory.get("timeline", [])) if isinstance(memory.get("timeline"), list) else []
    return entries, conflicts, timeline


def build_memory_index(memory: dict[str, Any] | None, *, projection_version: str = "review-selector-v1") -> MemoryIndex:
    """Build a disposable deterministic index from authoritative Book Memory."""
    source = memory if isinstance(memory, dict) else {}
    entries, _conflicts, _timeline = _memory_collections(source)
    facts: list[MemoryIndexFact] = []
    for raw in entries:
        stable_id = _stable_id("fact", raw, ("fact_id", "id", "key"))
        entity_ids = raw.get("entity_ids", raw.get("entities", []))
        evidence_ids = raw.get("evidence_ids", [])
        facts.append(MemoryIndexFact(
            stable_id=stable_id,
            content_hash=_hash(raw),
            fact_type=str(raw.get("fact_type") or raw.get("category") or "fact"),
            status=str(raw.get("status") or "active"),
            aliases=tuple(_aliases(raw)),
            entity_ids=tuple(str(value) for value in entity_ids if value) if isinstance(entity_ids, list) else (),
            evidence_ids=tuple(str(value) for value in evidence_ids if value) if isinstance(evidence_ids, list) else (),
            value=deepcopy(raw),
        ))
    facts.sort(key=lambda fact: fact.stable_id)
    return MemoryIndex(projection_version, _hash(source), tuple(facts))


def _make_payload(base: dict[str, Any], snapshot: ReviewContextSnapshot) -> dict[str, Any]:
    payload = {
        key: deepcopy(value)
        for key, value in base.items()
        if key not in {"items", "context_before", "context_after", "glossary", "book_memory",
                       "previous_chapter_state", "current_chapter_rolling_state", "translation_policy"}
    }
    glossary_projection: Any = [deepcopy(item) for item in snapshot.glossary]
    if isinstance(base.get("glossary"), dict):
        glossary_projection = {"terms": glossary_projection}
    payload.update({
        "translation_policy": snapshot.policy,
        "book_memory": deepcopy(snapshot.memory),
        "previous_chapter_state": deepcopy(snapshot.previous_chapter_state),
        "current_chapter_rolling_state": deepcopy(snapshot.current_chapter_rolling_state),
        "glossary": glossary_projection,
        "items": [deepcopy(item) for item in snapshot.target],
        "context_before": [deepcopy(item) for item in snapshot.context_before],
        "context_after": [deepcopy(item) for item in snapshot.context_after],
    })
    return payload


def build_budgeted_review_context(
    authoritative_context: dict[str, Any],
    *,
    items: list[dict[str, Any]],
    context_before: list[dict[str, Any]] | None = None,
    context_after: list[dict[str, Any]] | None = None,
    trigger_evidence: list[dict[str, Any]] | None = None,
    budget: ReviewContextBudget | dict[str, Any] | None = None,
    schema_path: Path,
    autonomous: bool = False,
) -> tuple[ReviewContextSnapshot, dict[str, Any], dict[str, Any]]:
    """Select context, render the real prompt, and enforce the input hard limit."""
    cfg = budget if isinstance(budget, ReviewContextBudget) else ReviewContextBudget.from_mapping(budget)
    before = [deepcopy(item) for item in (context_before or []) if isinstance(item, dict)]
    after = [deepcopy(item) for item in (context_after or []) if isinstance(item, dict)]
    targets = [deepcopy(item) for item in items if isinstance(item, dict)]
    trigger_evidence = [item for item in (trigger_evidence or []) if isinstance(item, dict)]
    target_text = _text(targets)
    local_text = _text(before + after)
    evidence_ids = {
        str(value)
        for finding in trigger_evidence
        for value in ([finding.get("id")] + list(finding.get("evidence_ids", []) or []))
        if value
    }

    previous = _state_projection(authoritative_context.get("previous_chapter_state"))
    rolling = _state_projection(authoritative_context.get("current_chapter_rolling_state"))
    active_entities = list(dict.fromkeys(
        [str(value) for state in (rolling, previous) for value in state.get("active_entities", []) or [] if value]
    ))
    speaker_entities = list(dict.fromkeys(
        str(paragraph.get(key)).strip()
        for paragraph in targets + before + after
        for key in ("speaker_id", "speaker", "addressee_id", "addressee")
        if paragraph.get(key)
    ))
    # Active entities are already deterministic state metadata.  They remain
    # valid scene signals even when the language omits the subject's name.
    scene_entities = list(dict.fromkeys(active_entities + speaker_entities))

    selected: list[SelectedEntry] = []
    optional: list[SelectedEntry] = []

    # The closest complete paragraph on each side is protected.  More distant
    # paragraphs are optional and ordered by distance from the target.
    for side, paragraphs in (("before", before), ("after", after)):
        ordered = list(reversed(paragraphs)) if side == "before" else list(paragraphs)
        for distance, paragraph in enumerate(ordered, start=1):
            stable_id = _stable_id(f"local-{side}", paragraph, ("id",))
            paragraph_id = str(paragraph.get("id", ""))
            if paragraph_id and paragraph_id in evidence_ids:
                selected.append(SelectedEntry("local", stable_id, paragraph, True, "targeted_evidence"))
            elif distance == 1:
                selected.append(SelectedEntry("local", stable_id, paragraph, True, "local_context_minimum"))
            else:
                optional.append(SelectedEntry(f"local_{side}", stable_id, paragraph, False, priority=(0, distance)))

    raw_glossary = authoritative_context.get("glossary", [])
    if isinstance(raw_glossary, dict):
        raw_glossary = raw_glossary.get("terms", [])
    glossary = [item for item in raw_glossary if isinstance(item, dict)] if isinstance(raw_glossary, list) else []
    paragraph_ids = {
        str(paragraph.get("id", "")) for paragraph in targets + before + after if paragraph.get("id")
    }
    for raw in glossary:
        item = _compact_entry(raw)
        stable_id = _stable_id("term", raw, ("term_id", "id", "source"))
        direct = _contains(target_text, raw.get("source")) or _contains(target_text, raw.get("target"))
        local_match = _contains(local_text, raw.get("source")) or _contains(local_text, raw.get("target"))
        raw_evidence = raw.get("evidence_ids", raw.get("sample_ids", []))
        evidence_related = bool(
            paragraph_ids.intersection(str(value) for value in raw_evidence)
        ) if isinstance(raw_evidence, list) else False
        targeted = stable_id in evidence_ids or str(raw.get("term_id", "")) in evidence_ids or str(raw.get("source", "")) in evidence_ids
        active = str(raw.get("status", "active")).lower() in {"active", "approved", "locked"} or bool(raw.get("manual") or raw.get("locked"))
        entity_related = any(alias in scene_entities or any(_contains(alias, ent) or _contains(ent, alias) for ent in scene_entities) for alias in _aliases(raw))
        if targeted:
            selected.append(SelectedEntry("glossary", stable_id, item, True, "targeted_evidence", priority=(0, stable_id)))
        elif direct and active:
            selected.append(SelectedEntry("glossary", stable_id, item, True, "direct_term_match", priority=(0, stable_id)))
        elif direct or entity_related or local_match or evidence_related:
            optional.append(SelectedEntry(
                "glossary", stable_id, item, False,
                priority=(1, 0 if direct else 1 if entity_related else 2 if evidence_related else 3, stable_id),
            ))

    authoritative_memory = authoritative_context.get("book_memory")
    memory_index = build_memory_index(
        authoritative_memory if isinstance(authoritative_memory, dict) else {},
        projection_version=cfg.selector_version,
    )
    memory = [fact.value for fact in memory_index.facts]
    _entries, conflicts, timeline = _memory_collections(authoritative_memory)
    direct_memory_entities: set[str] = set(scene_entities)
    for raw in memory:
        aliases = _aliases(raw)
        if any(_contains(target_text + local_text, alias) for alias in aliases):
            direct_memory_entities.update(aliases)
    # One-hop expansion changes candidacy only.  A relationship candidate is
    # promoted later only when current deterministic evidence makes it necessary.
    for raw in memory:
        aliases = _aliases(raw)
        related = any(any(_contains(alias, entity) or _contains(entity, alias) for entity in direct_memory_entities) for alias in aliases)
        speaker_related = any(
            any(_contains(alias, entity) or _contains(entity, alias) for entity in speaker_entities)
            for alias in aliases
        )
        direct = any(_contains(target_text + local_text, alias) for alias in aliases)
        stable_id = _stable_id("fact", raw, ("fact_id", "id", "key"))
        targeted = stable_id in evidence_ids or str(raw.get("fact_id", "")) in evidence_ids or str(raw.get("key", "")) in evidence_ids
        critical = bool(raw.get("global_critical"))
        item = _compact_entry(raw, memory=True)
        if targeted:
            selected.append(SelectedEntry("memory", stable_id, item, True, "targeted_evidence", priority=(0, stable_id)))
        elif critical:
            selected.append(SelectedEntry("memory", stable_id, item, True, "global_critical", priority=(0, stable_id)))
        elif speaker_related:
            selected.append(SelectedEntry("memory", stable_id, item, True, "speaker_identity", priority=(0, stable_id)))
        elif direct:
            selected.append(SelectedEntry("memory", stable_id, item, True, "active_relationship", priority=(0, stable_id)))
        elif related:
            optional.append(SelectedEntry("memory", stable_id, item, False, priority=(2, stable_id)))

    # Bound global critical facts deterministically.
    critical_entries = sorted((entry for entry in selected if entry.required_reason == "global_critical"), key=lambda entry: entry.stable_id)
    for entry in critical_entries[cfg.max_global_critical_facts:]:
        selected.remove(entry)
        optional.append(SelectedEntry(entry.pool, entry.stable_id, entry.value, False, priority=(3, entry.stable_id)))

    for raw in conflicts:
        stable_id = _stable_id("conflict", raw, ("id", "key"))
        relevant = any(_contains(target_text + local_text, alias) for alias in _aliases(raw))
        targeted = stable_id in evidence_ids or str(raw.get("key", "")) in evidence_ids
        entry = SelectedEntry("memory_conflict", stable_id, _compact_entry(raw, memory=True), relevant or targeted,
                              "targeted_evidence" if targeted else ("relevant_conflict" if relevant else None), priority=(3, stable_id))
        (selected if entry.required else optional).append(entry)

    for index, raw in enumerate(timeline):
        value = deepcopy(raw)
        stable_id = f"timeline:{index}:{_hash(value)[7:15]}"
        relevant = any(_contains(target_text + local_text, entity) for entity in _aliases(raw)) if isinstance(raw, dict) else False
        entry = SelectedEntry("timeline", stable_id, value, relevant, "timeline_constraint" if relevant else None, priority=(4, index))
        (selected if entry.required else optional).append(entry)

    if isinstance(authoritative_memory, dict) and authoritative_memory.get("summary"):
        optional.append(SelectedEntry(
            "book_summary", "book-summary", deepcopy(authoritative_memory["summary"]), False,
            priority=(9, "book-summary"),
        ))

    for label, state in (("rolling", rolling), ("previous", previous)):
        if state:
            optional.append(SelectedEntry(f"state_{label}", f"state:{label}", state, False, priority=(1 if label == "rolling" else 2, label)))

    required_chars = sum(_chars(entry.value) for entry in selected)
    optional.sort(key=lambda entry: entry.priority)
    optional_selected: list[SelectedEntry] = []
    optional_chars = 0

    def quota_pool(pool: str) -> str:
        if pool.startswith("local"):
            return "local"
        if pool.startswith("memory") or pool == "timeline":
            return "memory"
        if pool.startswith("state"):
            return "state"
        return pool

    quotas = {
        "local": cfg.local_context_soft_chars,
        "glossary": cfg.glossary_soft_chars,
        "memory": cfg.memory_soft_chars,
        "state": cfg.state_soft_chars,
        "book_summary": cfg.book_summary_soft_chars,
    }
    pool_used = {name: 0 for name in quotas}
    for entry in selected:
        pool = quota_pool(entry.pool)
        pool_used[pool] = pool_used.get(pool, 0) + _chars(entry.value)
    deferred: list[SelectedEntry] = []
    for entry in optional:
        size = _chars(entry.value)
        pool = quota_pool(entry.pool)
        if (
            pool_used.get(pool, 0) + size <= quotas.get(pool, cfg.background_soft_limit_chars)
            and required_chars + optional_chars + size <= cfg.background_soft_limit_chars
        ):
            optional_selected.append(entry)
            optional_chars += size
            pool_used[pool] = pool_used.get(pool, 0) + size
        else:
            deferred.append(entry)
    # Empty capacity is shared across pools in stable global-priority order.
    borrowed_ids: set[str] = set()
    for entry in deferred:
        size = _chars(entry.value)
        if required_chars + optional_chars + size <= cfg.background_soft_limit_chars:
            optional_selected.append(entry)
            optional_chars += size
            borrowed_ids.add(entry.stable_id)

    policy = str(authoritative_context.get("translation_policy", ""))

    def assemble(entries: list[SelectedEntry]) -> tuple[ReviewContextSnapshot, dict[str, Any], str]:
        required_local_before = [entry.value for entry in selected if entry.pool == "local" and entry.stable_id.startswith("local-before:")]
        required_local_after = [entry.value for entry in selected if entry.pool == "local" and entry.stable_id.startswith("local-after:")]
        local_before = required_local_before + [entry.value for entry in entries if entry.pool == "local_before"]
        # Optional before entries were ranked nearest-first; restore document order.
        before_ids = {str(item.get("id", "")) for item in local_before}
        selected_before = [item for item in before if str(item.get("id", "")) in before_ids]
        after_ids = {str(item.get("id", "")) for item in required_local_after + [entry.value for entry in entries if entry.pool == "local_after"]}
        selected_after = [item for item in after if str(item.get("id", "")) in after_ids]
        terms = [entry.value for entry in selected + entries if entry.pool == "glossary"]
        facts = [entry.value for entry in selected + entries if entry.pool == "memory"]
        selected_conflicts = [entry.value for entry in selected + entries if entry.pool == "memory_conflict"]
        selected_timeline = [entry.value for entry in selected + entries if entry.pool == "timeline"]
        selected_summaries = [entry.value for entry in entries if entry.pool == "book_summary"]
        memory_projection = {
            "schema_version": str((authoritative_context.get("book_memory") or {}).get("schema_version", "2.0"))
            if isinstance(authoritative_context.get("book_memory"), dict) else "2.0",
            "entries": facts,
            "conflicts": selected_conflicts,
            "timeline": selected_timeline,
        }
        if selected_summaries:
            memory_projection["summary"] = selected_summaries[0]
        included = selected + entries
        included_pools = {entry.pool for entry in entries}
        selected_previous = previous if "state_previous" in included_pools else {}
        selected_rolling = rolling if "state_rolling" in included_pools else {}
        snapshot = ReviewContextSnapshot(
            cfg.budget_version, cfg.selector_version, tuple(targets), tuple(selected_before), tuple(selected_after),
            tuple(terms), memory_projection, selected_previous, selected_rolling, policy, tuple(included),
        )
        payload = _make_payload(authoritative_context, snapshot)
        prompt = build_review_prompt("chapter", payload, schema_path, autonomous)
        return snapshot, payload, prompt

    kept_optional = list(optional_selected)
    snapshot, payload, prompt = assemble(kept_optional)
    # Stable eviction: discard the lowest-priority complete OPTIONAL item first.
    selection_limit = max(1, cfg.operational_input_hard_limit_chars - cfg.operational_headroom_chars)
    while len(prompt) > selection_limit and kept_optional:
        kept_optional.pop()
        snapshot, payload, prompt = assemble(kept_optional)

    reason_counts: dict[str, int] = {}
    for entry in selected:
        if entry.required_reason:
            reason_counts[entry.required_reason] = reason_counts.get(entry.required_reason, 0) + 1
    pool_chars = {
        pool: sum(_chars(entry.value) for entry in selected + kept_optional if entry.pool.startswith(pool))
        for pool in ("local", "glossary", "memory", "state")
    }
    fixed_payload = deepcopy(payload)
    fixed_payload.update({
        "items": [], "context_before": [], "context_after": [], "glossary": [],
        "book_memory": {"schema_version": "2.0", "entries": [], "conflicts": [], "timeline": []},
        "previous_chapter_state": {}, "current_chapter_rolling_state": {},
    })
    fixed_chars = len(build_review_prompt("chapter", fixed_payload, schema_path, autonomous))
    oversized_entries = [
        entry.stable_id for entry in selected + optional
        if _chars(entry.value) > max(1, {
            "local": cfg.local_context_soft_chars,
            "glossary": cfg.glossary_soft_chars,
            "memory": cfg.memory_soft_chars,
            "state": cfg.state_soft_chars,
        }.get(entry.pool.split("_")[0], cfg.background_soft_limit_chars))
    ]
    diagnostics = {
        "budget_version": cfg.budget_version,
        "selector_version": cfg.selector_version,
        "context_snapshot_id": snapshot.context_snapshot_id,
        "prompt_chars": len(prompt),
        "prompt_utf8_bytes": len(prompt.encode("utf-8")),
        "estimated_prompt_tokens": (len(prompt.encode("utf-8")) + 3) // 4,
        "actual_input_tokens": None,
        "operational_input_hard_limit_chars": cfg.operational_input_hard_limit_chars,
        "budget_pressure": round(len(prompt) / max(1, cfg.operational_input_hard_limit_chars), 6),
        "fixed_chars": fixed_chars,
        "target_chars": _chars(targets),
        "required_chars": required_chars,
        "optional_chars": sum(_chars(entry.value) for entry in kept_optional),
        "local_context_chars": pool_chars["local"],
        "glossary_chars": pool_chars["glossary"],
        "memory_chars": pool_chars["memory"],
        "state_chars": pool_chars["state"],
        "excluded_optional_entries": len(optional) - len(kept_optional),
        "soft_quota_borrowed_chars": sum(
            _chars(entry.value) for entry in kept_optional if entry.stable_id in borrowed_ids
        ),
        "required_reason_counts": reason_counts,
        "scene_entities": scene_entities,
        "scene_entity_signal_insufficient": not bool(scene_entities),
        "oversized_entries": oversized_entries,
        "target_paragraph_ids": [str(item.get("id", "")) for item in targets],
        "context_paragraph_ids": [str(item.get("id", "")) for item in snapshot.context_before + snapshot.context_after],
        "selected_entries": [
            {"stable_id": entry.stable_id, "pool": entry.pool, "content_hash": entry.content_hash,
             "required": entry.required, "required_reason": entry.required_reason}
            for entry in snapshot.entries
        ],
        "policy_source_hash": _hash(policy),
        "glossary_projection_hash": _hash(snapshot.glossary),
        "memory_projection_hash": _hash(snapshot.memory),
        "memory_index_hash": memory_index.content_hash,
        "memory_source_hash": memory_index.source_content_hash,
        "state_projection_hash": _hash({"previous": snapshot.previous_chapter_state, "rolling": snapshot.current_chapter_rolling_state}),
        "rendered_prompt_hash": "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
    }
    if len(prompt) > cfg.operational_input_hard_limit_chars:
        diagnostics["overflow"] = {
            "reason": "required_context_overflow",
            "minimum_target_ids": diagnostics["target_paragraph_ids"],
            "required_entry_ids": [entry.stable_id for entry in selected],
        }
        if len(targets) > 1:
            raise ReviewTargetSplitRequired(diagnostics)
        raise ReviewContextOverflowError(diagnostics=diagnostics)
    return snapshot, diagnostics, payload
