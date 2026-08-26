from __future__ import annotations

from pathlib import Path

from translator.core.workspace import BookWorkspace, read_json, write_json
from translator.pipeline.chapter_pipeline import IterativePipeline


def test_preextractor_can_feed_first_translation_batch(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"chapters": [{"id": "c1", "paragraphs": [{"id": "p1", "source": "雨宮慶が来た", "translated": ""}]}]})
    workspace = BookWorkspace.at(tmp_path / "output", "book")

    seen: list[dict] = []
    def extractor(_chapter_id: str, items: list[dict], _glossary: dict) -> dict:
        return {"candidates": [{"source": "雨宮慶", "target": "雨宫庆", "category": "person", "confidence": 0.96, "evidence_ids": [items[0]["id"]]}]}

    def translate(_provider: str, _book: str, ids: list[str], **_kwargs: object) -> dict:
        glossary = read_json(workspace.glossary_path)
        seen.extend(glossary.get("terms", []))
        data = read_json(manifest)
        data["chapters"][0]["paragraphs"][0]["translated"] = "雨宫庆来了"
        write_json(manifest, data)
        return {"status": "ok"}

    def reviewer(input_path: Path, output_path: Path) -> None:
        payload = read_json(input_path)
        write_json(output_path, {"checked_ids": [item["id"] for item in payload["items"]], "fixes": [], "glossary_delta": {"add": [], "update": [], "conflicts": []}, "memory_delta": {"add": [], "update": [], "conflicts": []}, "chapter_state": {}})

    pipeline = IterativePipeline(book="book", workspace=workspace, manifest=manifest, targeted_translator=translate, chapter_reviewer=reviewer, glossary_extractor=extractor)
    pipeline.initialize()
    pipeline.run_chapter("c1", 1)
    assert any(term["status"] == "active" and term["target"] == "雨宫庆" for term in seen)
