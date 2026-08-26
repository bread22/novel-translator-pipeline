from __future__ import annotations

from typing import Any, Iterable, Mapping

from translator.glossary.taxonomy import category_tier
from translator.glossary.validation import TARGET_FORBIDDEN_RE, KANA_RE


def _active_terms(glossary: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    raw = glossary.get("terms", []) if isinstance(glossary, Mapping) else glossary
    result: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status", "")) != "active":
            continue
        source = str(item.get("source", "")).strip()
        target = str(item.get("target", "")).strip()
        tier = category_tier(item.get("category"))
        if not source or not target or tier is None or tier.value == "blocked":
            continue
        if not item.get("evidence"):
            continue
        if TARGET_FORBIDDEN_RE.search(target) or KANA_RE.search(target):
            continue
        payload = {"source": source, "target": target, "category": str(item.get("category"))}
        canonical_target = item.get("canonical_target")
        if canonical_target:
            payload["canonical_target"] = str(canonical_target)
        result.append(payload)
    return sorted(result, key=lambda item: (item["source"], item["target"], item["category"]))


def build_translation_term_projection(glossary: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {"terms": _active_terms(glossary)}


def select_relevant_terms(
    glossary: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    *,
    items: list[dict[str, str]],
    previous: list[dict[str, str]] | None = None,
    following: list[dict[str, str]] | None = None,
    context: dict[str, list[dict[str, str]]] | None = None,
    active_entities: list[str] | None = None,
    max_terms: int = 64,
    max_chars: int = 4000,
) -> list[dict[str, Any]]:
    previous = previous if previous is not None else (context or {}).get("previous", [])
    following = following if following is not None else (context or {}).get("following", (context or {}).get("next", []))
    current_text = "\n".join(str(item.get("text") or item.get("source") or "") for item in items)
    context_text = "\n".join(
        str(item.get("text") or item.get("source") or "")
        for item in [*(previous or []), *(following or [])]
    )
    entities = {str(item).strip() for item in (active_entities or []) if str(item).strip()}
    ranked: list[tuple[int, dict[str, Any]]] = []
    for term in _active_terms(glossary):
        source = term["source"]
        if source in current_text:
            priority = 0
        elif source in entities or term.get("target") in entities or term.get("canonical_target") in entities:
            priority = 1
        elif source in context_text:
            priority = 2
        else:
            continue
        ranked.append((priority, term))
    ranked.sort(key=lambda pair: (pair[0], pair[1]["source"], pair[1]["target"], pair[1]["category"]))
    selected: list[dict[str, Any]] = []
    chars = 0
    for _priority, term in ranked:
        if len(selected) >= max_terms:
            break
        cost = len(term["source"]) + len(term["target"]) + len(term["category"]) + 4
        if selected and chars + cost > max_chars:
            continue
        if not selected and cost > max_chars:
            continue
        selected.append(term)
        chars += cost
    return selected
