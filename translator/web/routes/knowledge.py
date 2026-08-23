from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from translator.core.config import load_config
from translator.core.workspace import BookWorkspace, read_json, write_json
from translator.pipeline.chapter_pipeline import manifest_path
from translator.web.models import (
    BookMemoryResponse,
    GlossaryCreateRequest,
    GlossaryItem,
    GlossaryResponse,
)


router = APIRouter(prefix="/knowledge", tags=["Knowledge"])


def get_workspace_for_book(book_id: str) -> BookWorkspace:
    manifest = read_json(manifest_path(book_id), default=None)
    if not manifest:
        raise HTTPException(status_code=404, detail=f"未找到书籍: {book_id}")
    config = load_config()
    output_root = Path(config["paths"]["output_root"]).resolve()
    title = manifest.get("title", book_id)
    return BookWorkspace.at(output_root, title)


@router.get("/{book_id}/glossary", response_model=GlossaryResponse)
def get_glossary(book_id: str) -> GlossaryResponse:
    workspace = get_workspace_for_book(book_id)
    glossary_data = read_json(workspace.glossary_path, default={"terms": [], "conflicts": []})

    items = []
    for t in glossary_data.get("terms", []):
        items.append(
            GlossaryItem(
                source=t.get("source", ""),
                target=t.get("target", ""),
                category=t.get("category", "general"),
                confidence=float(t.get("confidence", 1.0)),
                notes=t.get("notes", ""),
                first_chapter=t.get("first_chapter"),
            )
        )

    return GlossaryResponse(
        book_id=book_id,
        terms=items,
        conflicts=glossary_data.get("conflicts", []),
        updated_at=glossary_data.get("updated_at"),
    )


@router.post("/{book_id}/glossary", response_model=GlossaryResponse)
def update_glossary(book_id: str, request: GlossaryCreateRequest) -> GlossaryResponse:
    workspace = get_workspace_for_book(book_id)
    glossary_data = read_json(workspace.glossary_path, default={"terms": [], "conflicts": []})

    existing_terms = {t.get("source"): t for t in glossary_data.get("terms", [])}
    for item in request.terms:
        existing_terms[item.source] = item.model_dump()

    glossary_data["terms"] = list(existing_terms.values())
    write_json(workspace.glossary_path, glossary_data)

    return get_glossary(book_id)


@router.get("/{book_id}/memory", response_model=BookMemoryResponse)
def get_book_memory(book_id: str) -> BookMemoryResponse:
    workspace = get_workspace_for_book(book_id)
    memory_data = read_json(workspace.book_memory_path, default={})

    # Collect all chapter states
    chapter_states = []
    if workspace.chapter_states_dir.exists():
        for state_file in sorted(workspace.chapter_states_dir.glob("*.json")):
            s = read_json(state_file, default=None)
            if s:
                chapter_states.append(s)

    return BookMemoryResponse(
        book_id=book_id,
        characters=memory_data.get("characters", []),
        world_settings=memory_data.get("world_settings", []),
        timeline=memory_data.get("timeline", []),
        chapter_states=chapter_states,
    )


@router.get("/{book_id}/reviews/{chapter_id}")
def get_chapter_review(book_id: str, chapter_id: str) -> dict[str, Any]:
    workspace = get_workspace_for_book(book_id)
    review_output_file = workspace.reviews_dir / f"{chapter_id}-output.json"
    review_fixes_file = workspace.reviews_dir / f"{chapter_id}-fixes.json"

    output_data = read_json(review_output_file, default=None)
    fixes_data = read_json(review_fixes_file, default=None)

    if not output_data and not fixes_data:
        return {
            "status": "not_found",
            "message": f"章节 {chapter_id} 尚未执行审阅或无审阅记录",
            "chapter_id": chapter_id,
            "auto_applied_fixes": [],
            "candidates": [],
        }

    return {
        "status": "ok",
        "chapter_id": chapter_id,
        "review_output": output_data,
        "applied_fixes": fixes_data,
    }

