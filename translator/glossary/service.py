from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from translator.glossary.lifecycle import merge_term_candidates
from translator.glossary.projection import build_translation_term_projection
from translator.core.book_store import BookRepository


def apply_glossary_delta(
    glossary: Mapping[str, Any],
    updates: Iterable[Mapping[str, Any]],
    *,
    chapter_id: str,
    reporter: str,
    evidence_texts: Mapping[str, Any],
    name_mapping_queue_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, int]]:
    return merge_term_candidates(
        glossary,
        updates,
        chapter_id=chapter_id,
        reporter=reporter,
        evidence_texts=evidence_texts,
        name_mapping_queue_path=name_mapping_queue_path,
    )


def persist_glossary(workspace: Any, glossary: Mapping[str, Any]) -> None:
    """Publish authority and projection with one rollback boundary."""
    BookRepository(workspace=workspace).save_glossary(glossary)


def glossary_from_path(path: Path) -> dict[str, Any]:
    import json
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"schema_version": "3.0", "terms": [], "conflicts": [], "revisions": []}
    return value if isinstance(value, dict) else {"schema_version": "3.0", "terms": [], "conflicts": [], "revisions": []}
