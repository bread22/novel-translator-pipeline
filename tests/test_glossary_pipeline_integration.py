from __future__ import annotations

from pathlib import Path

from translator.core.workspace import BookWorkspace, read_json, write_json
from translator.pipeline.chapter_pipeline import IterativePipeline


def test_prescan_injects_known_terms_into_translation(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"chapters": [{"id": "c1", "paragraphs": [{"id": "p1", "source": "雨宮慶が来た", "translated": ""}]}]})
    workspace = BookWorkspace.at(tmp_path / "output", "book")
    workspace.initialize()
    write_json(workspace.glossary_path, {
        "schema_version": "3.0",
        "terms": [{"term_id": "t1", "source": "雨宮慶", "target": "雨宫庆", "category": "person", "status": "active"}],
    })

    seen: list[dict] = []
    def translate(_provider: str, _book: str, ids: list[str], **_kwargs: object) -> dict:
        glossary = read_json(workspace.glossary_path)
        seen.extend(glossary.get("terms", []))
        data = read_json(manifest)
        data["chapters"][0]["paragraphs"][0]["translated"] = "雨宫庆来了"
        write_json(manifest, data)
        return {"status": "ok"}

    def reviewer(input_path: Path, output_path: Path) -> None:
        payload = read_json(input_path)
        write_json(output_path, {"checked_ids": [item["id"] for item in payload["items"]], "fixes": []})

    pipeline = IterativePipeline(
        book="book",
        workspace=workspace,
        manifest=manifest,
        targeted_translator=translate,
        chapter_reviewer=reviewer,
        knowledge_extractor=lambda *args, **kwargs: {},
    )
    pipeline.initialize()
    pipeline.run_chapter("c1", 1)
    assert any(term["status"] == "active" and term["target"] == "雨宫庆" for term in seen)
    report = pipeline._prescan_reports.get("c1", {})
    assert report.get("known_hit_count", 0) >= 1


def test_prescan_skips_when_no_active_terms(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"chapters": [{"id": "c1", "paragraphs": [{"id": "p1", "source": "テスト", "translated": ""}]}]})
    workspace = BookWorkspace.at(tmp_path / "output", "book")

    def translate(_provider: str, _book: str, ids: list[str], **_kwargs: object) -> dict:
        data = read_json(manifest)
        data["chapters"][0]["paragraphs"][0]["translated"] = "测试"
        write_json(manifest, data)
        return {"status": "ok"}

    def reviewer(input_path: Path, output_path: Path) -> None:
        payload = read_json(input_path)
        write_json(output_path, {"checked_ids": [item["id"] for item in payload["items"]], "fixes": []})

    pipeline = IterativePipeline(
        book="book",
        workspace=workspace,
        manifest=manifest,
        targeted_translator=translate,
        chapter_reviewer=reviewer,
        knowledge_extractor=lambda *args, **kwargs: {},
    )
    pipeline.initialize()
    pipeline.run_chapter("c1", 1)
    report = pipeline._prescan_reports.get("c1", {})
    assert report.get("known_hit_count", 0) == 0

