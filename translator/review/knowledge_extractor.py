"""Window and chapter-final knowledge extraction.

This module owns the model-facing knowledge contract.  It never writes the
authoritative glossary or memory while extracting; ``apply_knowledge_delta``
is the only persistence entry point and is called by the pipeline orchestrator.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable, Literal, Mapping, Sequence
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from translator.core.config import load_config
from translator.core.workspace import BookWorkspace, empty_book_memory, read_json, utc_now, write_json
from translator.glossary.service import apply_glossary_delta, persist_glossary
from translator.glossary.taxonomy import CategoryTier, canonical_category, category_tier
from translator.glossary.validation import validate_term_candidate
from translator.providers.base import build_review_prompt
from translator.providers.registry import get_provider


ROOT = Path(__file__).resolve().parents[2]
WINDOW_SCHEMA = ROOT / "schemas" / "knowledge-extractor-window.schema.json"
FINALIZE_SCHEMA = ROOT / "schemas" / "knowledge-extractor-finalize.schema.json"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RollingContextDelta(_Strict):
    adopted_terms: list[str] = Field(default_factory=list)
    active_entities: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    important_states: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class KnowledgeCandidate(_Strict):
    candidate_id: str = Field(min_length=1)
    kind: Literal["glossary", "memory"]
    source: str = ""
    target: str = ""
    category: str = ""
    source_scope: Literal["body", "title", "author", "cover", "front_matter"] = "body"
    key: str = ""
    value: str = ""
    note: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_window: str = Field(min_length=1)
    source_paragraph_ids: list[str] = Field(default_factory=list, min_length=1)
    evidence_ids: list[str] = Field(default_factory=list, min_length=1)
    source_fragment: str = Field(min_length=1)
    target_fragment: str = Field(min_length=1)
    referenced_glossary_ids: list[str] = Field(default_factory=list)
    referenced_memory_keys: list[str] = Field(default_factory=list)
    alias_candidate_ids: list[str] = Field(default_factory=list)


class KnowledgeConflict(_Strict):
    conflict_id: str = Field(min_length=1)
    kind: Literal["glossary", "memory"]
    candidate_id: str = ""
    key: str = ""
    existing_value: str = ""
    proposed_value: str = ""
    note: str = Field(min_length=1)
    source_window: str = Field(min_length=1)
    source_paragraph_ids: list[str] = Field(min_length=1)
    source_fragment: str = Field(min_length=1)
    target_fragment: str = Field(min_length=1)


class WindowKnowledgeOutput(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    rolling_context_delta: RollingContextDelta = Field(default_factory=RollingContextDelta)
    knowledge_candidates: list[KnowledgeCandidate] = Field(default_factory=list)
    conflicts: list[KnowledgeConflict] = Field(default_factory=list)


class KnowledgeDecision(_Strict):
    candidate_id: str = Field(min_length=1)
    action: Literal["active", "candidate", "conflict", "discard"]
    reason: str = ""
    conflict_id: str = ""


class FinalKnowledgeOutput(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    decisions: list[KnowledgeDecision] = Field(default_factory=list)


def empty_window_output() -> dict[str, Any]:
    return WindowKnowledgeOutput().model_dump()


def _candidate_id(raw: Mapping[str, Any], window_id: str, index: int) -> str:
    supplied = str(raw.get("candidate_id", "")).strip()
    if supplied:
        if window_id and window_id not in supplied:
            window_tag = window_id.split(":")[-1] if ":" in window_id else window_id
            if window_tag not in supplied:
                return f"{window_id}:{supplied}"
        return supplied
    material = json.dumps({"window": window_id, "index": index, "candidate": dict(raw)}, ensure_ascii=False, sort_keys=True)
    return "candidate:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def normalize_window_output(
    payload: Mapping[str, Any] | None,
    *,
    window_id: str,
    items: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate and bind candidates to the actual window evidence."""
    raw = dict(payload or {})
    raw.setdefault("schema_version", "1.0")
    raw.setdefault("rolling_context_delta", {})
    raw.setdefault("knowledge_candidates", [])
    raw.setdefault("conflicts", [])
    candidates: list[dict[str, Any]] = []
    valid_ids = {str(item.get("id", "")) for item in items if item.get("id")}
    for index, item in enumerate(raw.get("knowledge_candidates", []) if isinstance(raw.get("knowledge_candidates"), list) else []):
        if not isinstance(item, Mapping):
            continue
        candidate = dict(item)
        supplied_id = str(candidate.get("candidate_id", "")).strip()
        candidate["candidate_id"] = _candidate_id(candidate, window_id, index)
        if supplied_id and supplied_id != candidate["candidate_id"]:
            candidate["alias_candidate_ids"] = [supplied_id]
        candidate["source_window"] = window_id
        if candidate.get("kind") == "glossary" and candidate.get("category"):
            candidate["category"] = canonical_category(candidate["category"])
        source_ids = [str(value) for value in candidate.get("source_paragraph_ids", []) if str(value) in valid_ids]
        evidence_ids = [str(value) for value in candidate.get("evidence_ids", []) if str(value) in valid_ids]
        candidate["source_paragraph_ids"] = list(dict.fromkeys(source_ids or evidence_ids))
        candidate["evidence_ids"] = list(dict.fromkeys(evidence_ids or source_ids))
        if not candidate.get("source_fragment") and candidate["evidence_ids"]:
            lookup = {str(item.get("id")): str(item.get("source", "")) for item in items}
            candidate["source_fragment"] = lookup.get(candidate["evidence_ids"][0], "")
        if not candidate.get("target_fragment") and candidate["evidence_ids"]:
            lookup = {str(item.get("id")): str(item.get("translated", "")) for item in items}
            candidate["target_fragment"] = lookup.get(candidate["evidence_ids"][0], "")
        try:
            validated = KnowledgeCandidate.model_validate(candidate)
        except Exception:
            continue
        if validated.kind not in {"glossary", "memory"} or not validated.evidence_ids:
            continue
        candidates.append(validated.model_dump())

    conflicts: list[dict[str, Any]] = []
    for index, item in enumerate(raw.get("conflicts", []) if isinstance(raw.get("conflicts"), list) else []):
        if not isinstance(item, Mapping):
            continue
        conflict = dict(item)
        conflict.setdefault("conflict_id", f"conflict:{window_id}:{index}")
        conflict["source_window"] = window_id
        conflict["source_paragraph_ids"] = [str(value) for value in conflict.get("source_paragraph_ids", []) if str(value) in valid_ids]
        if not conflict["source_paragraph_ids"] and conflict.get("evidence_ids"):
            conflict["source_paragraph_ids"] = [str(value) for value in conflict["evidence_ids"] if str(value) in valid_ids]
        if conflict["source_paragraph_ids"]:
            paragraph_id = conflict["source_paragraph_ids"][0]
            lookup_source = {str(value.get("id")): str(value.get("source", "")) for value in items}
            lookup_target = {str(value.get("id")): str(value.get("translated", "")) for value in items}
            if not conflict.get("source_fragment"):
                conflict["source_fragment"] = lookup_source.get(paragraph_id, "")
            if not conflict.get("target_fragment"):
                conflict["target_fragment"] = lookup_target.get(paragraph_id, "")
        try:
            conflicts.append(KnowledgeConflict.model_validate(conflict).model_dump())
        except Exception:
            continue
    result = WindowKnowledgeOutput.model_validate({
        "schema_version": "1.0",
        "rolling_context_delta": raw.get("rolling_context_delta", {}),
        "knowledge_candidates": candidates,
        "conflicts": conflicts,
    }).model_dump()
    return result


def normalize_finalize_output(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(payload or {})
    raw.setdefault("schema_version", "1.0")
    decisions: list[dict[str, Any]] = []
    for item in raw.get("decisions", []) if isinstance(raw.get("decisions"), list) else []:
        if not isinstance(item, Mapping):
            continue
        decision = dict(item)
        if str(decision.get("action", "")) not in {"active", "candidate", "conflict", "discard"}:
            continue
        try:
            decisions.append(KnowledgeDecision.model_validate(decision).model_dump())
        except Exception:
            continue
    return FinalKnowledgeOutput.model_validate({"schema_version": "1.0", "decisions": decisions}).model_dump()


def _settings(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source = config if isinstance(config, Mapping) else load_config()
    if "knowledge_extractor" in source:
        return dict(source.get("knowledge_extractor", {}) or {})
    return dict(source)


def knowledge_extractor_enabled(config: Mapping[str, Any] | None = None) -> bool:
    return bool(_settings(config).get("enabled", False))


def _provider(name: str, config: Mapping[str, Any], settings: Mapping[str, Any]) -> Any:
    full_config = config if isinstance(config, Mapping) and "providers" in config else load_config()
    provider_cfg = deepcopy(dict(full_config))
    providers = provider_cfg.setdefault("providers", {})
    if name not in providers:
        raise ValueError(f"Knowledge Extractor provider 未配置：{name}")
    selected = dict(providers[name])
    if settings.get("model"):
        selected["model"] = str(settings["model"])
    if settings.get("credential_ref"):
        selected["api_key"] = str(settings["credential_ref"])
    if "temperature" in settings:
        selected["temperature"] = float(settings["temperature"])
    if "max_output_tokens" in settings:
        selected["max_output_tokens"] = int(settings["max_output_tokens"])
    providers[name] = selected
    return get_provider(name, provider_cfg)


def run_knowledge_extractor_window(
    payload: Mapping[str, Any],
    *,
    output_path: Path | None = None,
    provider_factory: Callable[[str], Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    settings = _settings(config)
    if not knowledge_extractor_enabled(config):
        result = {**empty_window_output(), "status": "skipped", "reason": "disabled"}
        if output_path:
            write_json(output_path, result)
        return result
    provider_name = str(settings.get("provider", "")).strip()
    provider = provider_factory(provider_name) if provider_factory else _provider(provider_name, config or load_config(), settings)
    result = provider.review(
        "knowledge_window", dict(payload), WINDOW_SCHEMA,
        autonomous=False, timeout=int(settings.get("request_timeout", 300)),
    )
    normalized = normalize_window_output(
        result if isinstance(result, Mapping) else {},
        window_id=str(payload.get("window_id", "window-unknown")),
        items=[item for item in payload.get("items", []) if isinstance(item, Mapping)],
    )
    normalized["status"] = "completed"
    if output_path:
        write_json(output_path, normalized)
    return normalized


def aggregate_candidates(
    candidates: Sequence[Mapping[str, Any]],
    historical_candidates: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Merge candidates sharing entity identities across windows/chapters into consolidated evidence."""
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for raw in candidates:
        if not isinstance(raw, Mapping):
            continue
        c = dict(raw)
        kind = str(c.get("kind", "glossary")).strip().lower()
        if kind == "glossary":
            src = unicodedata.normalize("NFKC", str(c.get("source", "")).strip())
            tgt = unicodedata.normalize("NFKC", str(c.get("target", "")).strip())
            cat = canonical_category(c.get("category", ""))
            key = ("glossary", cat, src.casefold(), tgt.casefold())
        else:
            k = unicodedata.normalize("NFKC", str(c.get("key", "")).strip())
            val = unicodedata.normalize("NFKC", str(c.get("value", "")).strip())
            cat = canonical_category(c.get("category", ""))
            key = ("memory", cat, k.casefold(), val.casefold())

        cid = str(c.get("candidate_id", "")).strip()
        pids = [str(p).strip() for p in c.get("source_paragraph_ids", []) if str(p).strip()]
        eids = [str(e).strip() for e in c.get("evidence_ids", []) if str(e).strip()]

        if key not in grouped:
            item = dict(c)
            if kind == "glossary":
                item["category"] = cat
                if src:
                    item["source"] = src
                if tgt:
                    item["target"] = tgt
            item["source_paragraph_ids"] = list(dict.fromkeys(pids or eids))
            item["evidence_ids"] = list(dict.fromkeys(eids or pids))
            item["alias_candidate_ids"] = [cid] if cid else []
            grouped[key] = item
        else:
            existing = grouped[key]
            existing["source_paragraph_ids"] = list(dict.fromkeys(existing.get("source_paragraph_ids", []) + (pids or eids)))
            existing["evidence_ids"] = list(dict.fromkeys(existing.get("evidence_ids", []) + (eids or pids)))
            if cid and cid not in existing.get("alias_candidate_ids", []):
                existing.setdefault("alias_candidate_ids", []).append(cid)
            try:
                if float(c.get("confidence", 0) or 0) > float(existing.get("confidence", 0) or 0):
                    existing["confidence"] = float(c.get("confidence", 0) or 0)
            except (ValueError, TypeError):
                pass
            c_note = str(c.get("note", "")).strip()
            ex_note = str(existing.get("note", "")).strip()
            if c_note and c_note not in ex_note:
                existing["note"] = f"{ex_note}; {c_note}".strip("; ")
            cur_windows = [w.strip() for w in existing.get("source_window", "").split(",") if w.strip()]
            new_window = str(c.get("source_window", "")).strip()
            if new_window and new_window not in cur_windows:
                cur_windows.append(new_window)
            existing["source_window"] = ", ".join(cur_windows)
            if not existing.get("source_fragment") and c.get("source_fragment"):
                existing["source_fragment"] = c.get("source_fragment")
            if not existing.get("target_fragment") and c.get("target_fragment"):
                existing["target_fragment"] = c.get("target_fragment")

    if historical_candidates:
        for raw in historical_candidates:
            if not isinstance(raw, Mapping):
                continue
            h = dict(raw)
            h_kind = str(h.get("kind", "glossary")).strip().lower()
            if h_kind == "glossary":
                h_src = unicodedata.normalize("NFKC", str(h.get("source", "")).strip())
                h_tgt = unicodedata.normalize("NFKC", str(h.get("target", "")).strip())
                h_cat = canonical_category(h.get("category", ""))
                h_key = ("glossary", h_cat, h_src.casefold(), h_tgt.casefold())
            else:
                h_k = unicodedata.normalize("NFKC", str(h.get("key", "")).strip())
                h_val = unicodedata.normalize("NFKC", str(h.get("value", "")).strip())
                h_cat = canonical_category(h.get("category", ""))
                h_key = ("memory", h_cat, h_k.casefold(), h_val.casefold())

            if h_key in grouped:
                existing = grouped[h_key]
                h_pids = [str(p).strip() for p in h.get("source_paragraph_ids", []) if str(p).strip()]
                h_eids = [str(e).strip() for e in h.get("evidence_ids", []) if str(e).strip()]
                existing["source_paragraph_ids"] = list(dict.fromkeys(existing.get("source_paragraph_ids", []) + (h_pids or h_eids)))
                existing["evidence_ids"] = list(dict.fromkeys(existing.get("evidence_ids", []) + (h_eids or h_pids)))

    return list(grouped.values())


def build_finalization_payload(
    candidates: Sequence[Mapping[str, Any]],
    conflicts: Sequence[Mapping[str, Any]],
    glossary: Mapping[str, Any],
    memory: Mapping[str, Any],
    *,
    candidate_store: Mapping[str, Any] | None = None,
    max_chars: int = 30_000,
) -> dict[str, Any]:
    """Build a bounded finalization request without chapter source/translation."""
    def serialized(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)

    def references(candidate: Mapping[str, Any]) -> set[str]:
        values: set[str] = set()
        for key in (
            "candidate_id", "source", "target", "key", "value", "category",
            "referenced_glossary_ids", "referenced_memory_keys",
        ):
            raw = candidate.get(key, "")
            if isinstance(raw, list):
                values.update(str(item).strip().casefold() for item in raw if str(item).strip())
            elif raw:
                values.add(str(raw).strip().casefold())
        return values

    def is_relevant(record: Mapping[str, Any], refs: set[str], *, memory_record: bool = False) -> bool:
        record_id_fields = ("term_id", "id", "source", "target", "canonical_name", "key")
        if memory_record:
            record_id_fields = ("fact_id", "id", "key", "value", "name", "term")
        values = {str(record.get(key, "")).strip().casefold() for key in record_id_fields if record.get(key)}
        if values.intersection(refs):
            return True
        haystack = serialized(record).casefold()
        return any(len(ref) >= 2 and ref in haystack for ref in refs)

    historical_items = candidate_store.get("items", []) if isinstance(candidate_store, Mapping) else []
    aggregated_candidates = aggregate_candidates(candidates, historical_candidates=historical_items)

    active_terms_all = [
        dict(item) for item in glossary.get("terms", []) if isinstance(item, Mapping)
        and (
            str(item.get("status", "active")).lower() in {"active", "locked", "approved"}
            or bool(item.get("locked"))
        )
    ]
    memory_entries_all: list[dict[str, Any]] = [
        dict(item) for item in memory.get("entries", []) if isinstance(item, Mapping)
    ]
    # Book Memory v2 may still expose its read-only legacy projections.  Turn
    # them into finalization records without changing the authoritative file.
    for item in memory.get("characters", []) if isinstance(memory.get("characters"), list) else []:
        if isinstance(item, Mapping):
            memory_entries_all.append({
                "key": item.get("name", ""),
                "value": item.get("summary", ""),
                "category": "character",
                "status": "active",
                **dict(item),
            })
    for item in memory.get("world_settings", []) if isinstance(memory.get("world_settings"), list) else []:
        if isinstance(item, Mapping):
            memory_entries_all.append({
                "key": item.get("term", ""),
                "value": item.get("explanation", ""),
                "category": "fact",
                "status": "active",
                **dict(item),
            })
    memory_entries_all = [
        item for item in memory_entries_all
        if str(item.get("status", "active")).lower() in {"active", "locked", "approved", ""}
        or bool(item.get("locked"))
    ]
    # Finalization sees only state that can explain one of this chapter's
    # candidates.  It does not receive the full book stores.
    candidate_refs = [references(item) for item in aggregated_candidates if isinstance(item, Mapping)]
    active_terms = [item for item in active_terms_all if any(is_relevant(item, refs) for refs in candidate_refs)]
    memory_entries = [item for item in memory_entries_all if any(is_relevant(item, refs, memory_record=True) for refs in candidate_refs)]

    compact_candidates = [
        {
            key: candidate[key] for key in (
                "candidate_id", "kind", "source", "target", "category", "key", "value", "note",
                "confidence", "source_window", "source_paragraph_ids", "evidence_ids",
                "source_fragment", "target_fragment", "referenced_glossary_ids", "referenced_memory_keys",
            ) if key in candidate
        }
        for candidate in aggregated_candidates if isinstance(candidate, Mapping)
    ]
    compact_conflicts = [dict(item) for item in conflicts if isinstance(item, Mapping)]

    def document(selected_candidates: list[dict[str, Any]], selected_conflicts: list[dict[str, Any]],
                 terms: list[dict[str, Any]], facts: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "candidates": selected_candidates,
            "conflicts": selected_conflicts,
            "active_glossary": terms,
            "related_memory": facts,
        }

    # Keep candidate evidence first, then conflicts and related authoritative
    # context. Every addition is tested against the hard request limit.
    selected_candidates: list[dict[str, Any]] = []
    selected_conflicts: list[dict[str, Any]] = []
    selected_terms: list[dict[str, Any]] = []
    selected_memory: list[dict[str, Any]] = []
    for item in compact_candidates:
        trial = document(selected_candidates + [item], selected_conflicts, selected_terms, selected_memory)
        if len(serialized(trial)) <= max_chars:
            selected_candidates.append(item)
    for item in compact_conflicts:
        trial = document(selected_candidates, selected_conflicts + [item], selected_terms, selected_memory)
        if len(serialized(trial)) <= max_chars:
            selected_conflicts.append(item)
    for item in active_terms:
        trial = document(selected_candidates, selected_conflicts, selected_terms + [item], selected_memory)
        if len(serialized(trial)) <= max_chars:
            selected_terms.append(item)
    for item in memory_entries:
        trial = document(selected_candidates, selected_conflicts, selected_terms, selected_memory + [item])
        if len(serialized(trial)) <= max_chars:
            selected_memory.append(item)

    selected_ids = {str(item.get("candidate_id", "")) for item in selected_candidates}
    result = document(selected_candidates, selected_conflicts, selected_terms, selected_memory)
    result["omitted_candidate_ids"] = [
        str(item.get("candidate_id", "")) for item in aggregated_candidates
        if str(item.get("candidate_id", "")) not in selected_ids
    ]
    # The omission audit is useful but must not break the same hard limit.
    while len(serialized(result)) > max_chars and result["omitted_candidate_ids"]:
        result["omitted_candidate_ids"].pop()
    return result


def finalization_prompt_chars(payload: Mapping[str, Any]) -> int:
    """Return the size of the exact fixed finalization prompt sent to a provider."""
    return len(build_review_prompt("knowledge_finalize", dict(payload), FINALIZE_SCHEMA, False))


def run_knowledge_finalization(
    payload: Mapping[str, Any],
    *,
    output_path: Path | None = None,
    provider_factory: Callable[[str], Any] | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    settings = _settings(config)
    if not knowledge_extractor_enabled(config):
        result = {"schema_version": "1.0", "decisions": [], "status": "skipped", "reason": "disabled"}
        if output_path:
            write_json(output_path, result)
        return result
    provider_name = str(settings.get("provider", "")).strip()
    provider = provider_factory(provider_name) if provider_factory else _provider(provider_name, config or load_config(), settings)
    result = provider.review(
        "knowledge_finalize", dict(payload), FINALIZE_SCHEMA,
        autonomous=False, timeout=int(settings.get("request_timeout", 300)),
    )
    normalized = normalize_finalize_output(result if isinstance(result, Mapping) else {})
    normalized["status"] = "completed"
    if output_path:
        write_json(output_path, normalized)
    return normalized


def knowledge_extractor_connection_test(config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    settings = _settings(config)
    provider_name = str(settings.get("provider", "")).strip()
    if not provider_name:
        return {"status": "error", "error": "未配置 Knowledge Extractor provider"}
    started = time.monotonic()
    try:
        result = _provider(provider_name, config or load_config(), settings).health_check(
            timeout=int(settings.get("request_timeout", 300))
        )
        return {"status": "ok" if isinstance(result, Mapping) and result.get("status") == "ok" else "error", "provider": provider_name, "latency_ms": round((time.monotonic() - started) * 1000, 1), "result": result}
    except Exception as exc:
        return {"status": "error", "provider": provider_name, "latency_ms": round((time.monotonic() - started) * 1000, 1), "error": str(exc)}


def apply_knowledge_delta(
    workspace: BookWorkspace,
    chapter_id: str,
    candidates: Sequence[Mapping[str, Any]],
    decisions: Mapping[str, Mapping[str, Any]] | list[Mapping[str, Any]] | None,
    conflicts: Sequence[Mapping[str, Any]] | None = None,
    *,
    evidence_texts: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist final knowledge actions while preserving old active values."""
    if isinstance(decisions, list):
        decision_by_id = {str(item.get("candidate_id", "")): dict(item) for item in decisions if isinstance(item, Mapping)}
    else:
        decision_by_id = {str(key): dict(value) for key, value in (decisions or {}).items() if isinstance(value, Mapping)}

    aggregated_candidates = aggregate_candidates(candidates)

    decision_by_term: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for d_cid, d_item in decision_by_id.items():
        for raw in aggregated_candidates:
            if not isinstance(raw, Mapping):
                continue
            c_cid = str(raw.get("candidate_id", "")).strip()
            aliases = [str(a).strip() for a in raw.get("alias_candidate_ids", []) if str(a).strip()]
            if d_cid == c_cid or d_cid in aliases or c_cid.endswith(f":{d_cid}") or c_cid.endswith(f"-{d_cid}"):
                kind = str(raw.get("kind", "glossary")).strip().lower()
                if kind == "glossary":
                    src = unicodedata.normalize("NFKC", str(raw.get("source", "")).strip())
                    tgt = unicodedata.normalize("NFKC", str(raw.get("target", "")).strip())
                    cat = canonical_category(raw.get("category", ""))
                    decision_by_term[(kind, cat, src.casefold(), tgt.casefold())] = d_item
                else:
                    k = unicodedata.normalize("NFKC", str(raw.get("key", "")).strip())
                    val = unicodedata.normalize("NFKC", str(raw.get("value", "")).strip())
                    cat = canonical_category(raw.get("category", ""))
                    decision_by_term[(kind, cat, k.casefold(), val.casefold())] = d_item

    candidate_store_path = workspace.knowledge_candidates_path
    conflict_store_path = workspace.knowledge_conflicts_path
    glossary_path = workspace.glossary_path
    memory_path = workspace.book_memory_path
    paths = [candidate_store_path, conflict_store_path, glossary_path, memory_path, workspace.novel_translator_terms_path]
    originals = {path: path.read_bytes() if path.exists() else None for path in paths}
    evidence = dict(evidence_texts or {})
    active_glossary_raw: list[dict[str, Any]] = []
    active_memory: list[dict[str, Any]] = []
    stored_candidates: list[dict[str, Any]] = []
    stored_conflicts = [
        {**dict(item), "chapter_id": chapter_id}
        for item in (conflicts or []) if isinstance(item, Mapping)
    ]
    summary = {"active": 0, "candidate": 0, "conflict": len(stored_conflicts), "discard": 0, "omitted": 0}

    def _get_decision(candidate: dict[str, Any]) -> dict[str, Any] | None:
        cid = str(candidate.get("candidate_id", "")).strip()
        if cid in decision_by_id:
            return decision_by_id[cid]
        for alias in candidate.get("alias_candidate_ids", []):
            if str(alias) in decision_by_id:
                return decision_by_id[str(alias)]
        for d_k, d_v in decision_by_id.items():
            if cid.endswith(f":{d_k}") or cid.endswith(f"-{d_k}"):
                return d_v
        kind = str(candidate.get("kind", "glossary")).strip().lower()
        if kind == "glossary":
            src = unicodedata.normalize("NFKC", str(candidate.get("source", "")).strip())
            tgt = unicodedata.normalize("NFKC", str(candidate.get("target", "")).strip())
            cat = canonical_category(candidate.get("category", ""))
            return decision_by_term.get((kind, cat, src.casefold(), tgt.casefold()))
        else:
            k = unicodedata.normalize("NFKC", str(candidate.get("key", "")).strip())
            val = unicodedata.normalize("NFKC", str(candidate.get("value", "")).strip())
            cat = canonical_category(candidate.get("category", ""))
            return decision_by_term.get((kind, cat, k.casefold(), val.casefold()))

    try:
        for raw in aggregated_candidates:
            candidate = dict(raw)
            decision = _get_decision(candidate)
            action = str((decision or {}).get("action", "candidate"))
            if action not in {"active", "candidate", "conflict", "discard"}:
                action = "candidate"
            if decision is None:
                summary["omitted"] += 1
            if action == "active":
                if candidate.get("kind") == "glossary":
                    category = canonical_category(candidate.get("category", ""))
                    candidate["category"] = category
                    if (
                        str(candidate.get("source", "")).strip()
                        and str(candidate.get("target", "")).strip()
                        and category_tier(category) in {CategoryTier.DIRECT_ALLOWED, CategoryTier.GATED_ALLOWED}
                    ):
                        validation = validate_term_candidate(
                            {key: candidate[key] for key in ("source", "target", "category", "confidence", "evidence_ids", "note", "source_scope") if key in candidate},
                            evidence_texts=evidence,
                        )
                        if validation.valid and validation.candidate is not None:
                            candidate.update(validation.candidate.model_dump())
                            active_glossary_raw.append({key: candidate[key] for key in ("source", "target", "category", "confidence", "evidence_ids", "note", "source_scope") if key in candidate})
                        else:
                            action = "candidate"
                    else:
                        action = "candidate"
                elif candidate.get("kind") == "memory":
                    if str(candidate.get("key", "")).strip() and str(candidate.get("value", "")).strip() and float(candidate.get("confidence", 0) or 0) >= 0.9:
                        active_memory.append({key: candidate[key] for key in ("key", "value", "category", "confidence", "note") if key in candidate})
                    else:
                        action = "candidate"
                else:
                    action = "candidate"
            record = {**candidate, "chapter_id": chapter_id, "final_action": action, "final_reason": str((decision or {}).get("reason", ""))}
            if action == "candidate":
                stored_candidates.append(record)
            elif action == "conflict":
                stored_conflicts.append({**record, "conflict_id": str((decision or {}).get("conflict_id", ""))})
            summary[action] = int(summary.get(action, 0)) + 1

        active_glossary_map: dict[tuple[str, str, str], dict[str, Any]] = {}
        for term in active_glossary_raw:
            g_key = (term["source"], term["target"], term["category"])
            if g_key not in active_glossary_map:
                active_glossary_map[g_key] = dict(term)
            else:
                existing = active_glossary_map[g_key]
                existing["evidence_ids"] = list(dict.fromkeys(existing.get("evidence_ids", []) + term.get("evidence_ids", [])))
                if float(term.get("confidence", 0) or 0) > float(existing.get("confidence", 0) or 0):
                    existing["confidence"] = term.get("confidence", 0)
        active_glossary = list(active_glossary_map.values())

        glossary = read_json(glossary_path, {"schema_version": "3.0", "terms": [], "conflicts": [], "revisions": []})
        if active_glossary:
            glossary, glossary_summary = apply_glossary_delta(
                glossary, active_glossary, chapter_id=chapter_id, reporter="knowledge_extractor",
                evidence_texts=evidence, name_mapping_queue_path=workspace.name_mapping_review_path,
            )
            persist_glossary(workspace, glossary)
        memory = read_json(memory_path, empty_book_memory(str(glossary.get("book", ""))))
        if active_memory:
            from translator.core.workspace import merge_memory_delta
            memory, memory_summary = merge_memory_delta(memory, {"add": active_memory, "update": [], "conflicts": []}, chapter_id)
            write_json(memory_path, memory)
        store = read_json(candidate_store_path, {"schema_version": "1.0", "items": []})
        store["items"] = [*store.get("items", []), *stored_candidates]
        store["updated_at"] = utc_now()
        write_json(candidate_store_path, store)
        conflict_store = read_json(conflict_store_path, {"schema_version": "1.0", "items": []})
        conflict_store["items"] = [*conflict_store.get("items", []), *stored_conflicts]
        conflict_store["updated_at"] = utc_now()
        write_json(conflict_store_path, conflict_store)
    except Exception:
        for path, content in originals.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        raise
    return summary


__all__ = [
    "WindowKnowledgeOutput", "FinalKnowledgeOutput", "aggregate_candidates",
    "build_finalization_payload", "finalization_prompt_chars",
    "run_knowledge_extractor_window", "run_knowledge_finalization",
    "knowledge_extractor_enabled", "knowledge_extractor_connection_test", "apply_knowledge_delta",
    "normalize_window_output", "normalize_finalize_output",
]
