"""Deterministic chapter pre-scan used as input to the review context budgeter.

The pre-scan is deliberately read-only.  It reports occurrences of terms that
already exist in the authoritative glossary; it never creates, updates, or
activates a glossary entry.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import unicodedata
from typing import Any, Iterable, Mapping


def _fold(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def _term_aliases(term: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("source", "canonical_name", "name"):
        value = str(term.get(key, "")).strip()
        if value:
            values.append(value)
    for key in ("aliases", "alias"):
        raw = term.get(key, [])
        if isinstance(raw, str):
            values.append(raw.strip())
        elif isinstance(raw, list):
            values.extend(str(item).strip() for item in raw if str(item).strip())
    return list(dict.fromkeys(values))


def _stable_hit_id(term: Mapping[str, Any], paragraph_id: str, matched: str) -> str:
    identity = "|".join((str(term.get("term_id") or term.get("source") or ""), paragraph_id, matched))
    return "known-hit:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def deterministic_known_hit_scan(
    items: Iterable[Mapping[str, Any]],
    glossary: Mapping[str, Any] | Iterable[Mapping[str, Any]] | None = None,
    *,
    chapter_id: str = "",
) -> dict[str, Any]:
    """Return deterministic glossary occurrences for a translated chapter.

    Matching is case-insensitive after NFKC normalization and is performed on
    the source text.  Each hit keeps the source paragraph and the complete
    glossary entry so the budgeter can choose it without another model call.
    """
    paragraphs = [dict(item) for item in items if isinstance(item, Mapping) and str(item.get("id", "")).strip()]
    if isinstance(glossary, Mapping):
        raw_terms = glossary.get("terms", [])
    else:
        raw_terms = glossary or []
    terms = [dict(item) for item in raw_terms if isinstance(item, Mapping)]
    hits: list[dict[str, Any]] = []
    for term in terms:
        status = str(term.get("status", "active")).casefold()
        if status and status not in {"active", "approved", "locked"} and not term.get("locked"):
            continue
        source = str(term.get("source", "")).strip()
        aliases = _term_aliases(term)
        if not source or not aliases:
            continue
        for paragraph in paragraphs:
            paragraph_id = str(paragraph["id"])
            text = str(paragraph.get("source", ""))
            folded_text = _fold(text)
            matched = next((alias for alias in aliases if _fold(alias) and _fold(alias) in folded_text), "")
            if not matched:
                continue
            hit = {
                "hit_id": _stable_hit_id(term, paragraph_id, matched),
                "term_id": term.get("term_id") or term.get("id") or source,
                "source": source,
                "target": str(term.get("target", "")),
                "category": str(term.get("category", "unresolved")),
                "status": str(term.get("status", "candidate")),
                "matched": matched,
                "paragraph_id": paragraph_id,
                "paragraph_ids": [paragraph_id],
                "source_fragment": text,
            }
            # The budgeter only needs a compact immutable projection.
            hit["term"] = deepcopy({
                key: term[key]
                for key in ("term_id", "source", "target", "category", "status", "aliases", "locked")
                if key in term
            })
            hits.append(hit)
    hits.sort(key=lambda item: (str(item.get("paragraph_id", "")), str(item.get("source", "")), str(item.get("hit_id", ""))))
    return {
        "schema_version": "1.0",
        "chapter_id": chapter_id,
        "known_hits": hits,
        "hit_count": len(hits),
        "term_count": len({str(item.get("term_id", "")) for item in hits}),
    }


__all__ = ["deterministic_known_hit_scan"]
