from __future__ import annotations

import json
from pathlib import Path
import tempfile

from translator.core.workspace import BookWorkspace
from translator.pipeline.chapter_pipeline import IterativePipeline
from translator.script_residue import has_target_script_residue, inspect_target_script
from translator.translation_repairs import apply_deterministic_repairs


def test_idiom_variants_share_one_source_aware_repair_rule() -> None:
    cases = [
        ("のの字を書く", "像写「の」字一样扭动身体"),
        ("「の」の字をえがくように", "像画“の”字一样扭动起来"),
        ("双臀をのの字を描くようにうねらせた。", "双臀画着‘の’字形扭动。"),
    ]
    for source, target in cases:
        repaired, records = apply_deterministic_repairs(source=source, translated=target)
        assert repaired in {"像画圈一样扭动身体", "像画圈一样扭动起来", "双臀画圈扭动。"}
        assert [item.rule_id for item in records] == ["jp_idiom_nonliteral_001"]
        assert records[0].count == 1
        assert not has_target_script_residue(repaired, source=source)
        assert apply_deterministic_repairs(source=source, translated=repaired) == (repaired, ())


def test_repair_requires_source_trigger_and_strict_target_shape() -> None:
    target = "像画“の”字一样扭动身体"
    unchanged, records = apply_deterministic_repairs(source="彼女は笑った。", translated=target)
    assert unchanged == target
    assert records == ()

    near_miss, records = apply_deterministic_repairs(
        source="のの字を書く", translated="她保留了“の”这个字。"
    )
    assert near_miss == "她保留了“の”这个字。"
    assert records == ()


def test_structured_residue_requires_source_and_target_character_evidence() -> None:
    source = "变体仮名で「くじり」と書いてあった。"
    explicit = inspect_target_script("用变体假名写着“くじり”二字。", source=source)
    assert len(explicit) == 1
    assert explicit[0].classification == "explicit_source_reference"
    assert explicit[0].token == "くじり"
    assert explicit[0].source_match is True
    assert explicit[0].context_match == "shape_reference"
    assert explicit[0].start < explicit[0].end
    assert not has_target_script_residue("用变体假名写着“くじり”二字。", source=source)

    missing_source_context = inspect_target_script(
        "她“ヒクッヒクッ”地抽搐。", source="ヒクッヒクッと痉挛了。"
    )
    assert missing_source_context[0].classification == "target_script_residue"
    assert missing_source_context[0].source_match is True
    assert has_target_script_residue("她“ヒクッヒクッ”地抽搐。", source="ヒクッヒクッと痉挛了。")

    second_residue = inspect_target_script(
        "用变体假名写着“くじり”二字，并出现かな。", source=source
    )
    assert [item.classification for item in second_residue] == [
        "explicit_source_reference", "target_script_residue",
    ]
    assert has_target_script_residue("用变体假名写着“くじり”二字，并出现かな。", source=source)


def test_primary_idiom_repair_stops_fallback_and_records_recovery() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps({
            "chapters": [{
                "id": "c1",
                "paragraphs": [{"id": "p1", "source": "のの字を書く", "translated": ""}],
            }],
        }, ensure_ascii=False), encoding="utf-8")
        workspace = BookWorkspace.at(root / "output", "book")
        calls: list[str] = []

        def targeted(provider: str, _book: str, ids: list[str], **_kwargs: object) -> dict:
            calls.append(provider)
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["chapters"][0]["paragraphs"][0]["translated"] = "像画“の”字一样扭动身体"
            manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return {"status": "ok", "summary": {"translated": len(ids)}}

        pipeline = IterativePipeline(
            book="book", workspace=workspace, manifest=manifest_path,
            tool_call=lambda *_args: {"status": "ok"}, targeted_translator=targeted,
            primary_translator="primary", fallback_translators=["fallback"],
        )
        pipeline.initialize()
        result = pipeline._translate_chapter("c1", 1)

        assert calls == ["primary"]
        assert result["translated"] == 1
        assert json.loads(manifest_path.read_text(encoding="utf-8"))["chapters"][0]["paragraphs"][0]["translated"] == "像画圈一样扭动身体"
        attempt = json.loads((workspace.data_dir / "provider-diagnostics.json").read_text(encoding="utf-8"))["attempts"][0]
        assert attempt["failure_class"] == "deterministic_repair_recovered"
        assert attempt["repair_rule_ids"] == ["jp_idiom_nonliteral_001"]
        assert attempt["repair_attempts"][0]["before_hash"] != attempt["repair_attempts"][0]["after_hash"]
        provenance = json.loads((workspace.data_dir / "translation-provenance.json").read_text(encoding="utf-8"))
        assert provenance["items"]["p1"]["reason"] == "deterministic_repair_recovered"


def test_residue_routes_to_fallback_with_structured_failure_class() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps({
            "chapters": [{
                "id": "c1",
                "paragraphs": [{"id": "p1", "source": "原文", "translated": ""}],
            }],
        }, ensure_ascii=False), encoding="utf-8")
        workspace = BookWorkspace.at(root / "output", "book")
        calls: list[str] = []

        def targeted(provider: str, _book: str, _ids: list[str], **_kwargs: object) -> dict:
            calls.append(provider)
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["chapters"][0]["paragraphs"][0]["translated"] = "残留かな" if provider == "primary" else "译文"
            manifest_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return {"status": "ok"}

        pipeline = IterativePipeline(
            book="book", workspace=workspace, manifest=manifest_path,
            tool_call=lambda *_args: {"status": "ok"}, targeted_translator=targeted,
            primary_translator="primary", fallback_translators=["fallback"],
        )
        pipeline.initialize()
        pipeline._translate_chapter("c1", 1)

        assert calls == ["primary", "fallback"]
        attempts = json.loads((workspace.data_dir / "provider-diagnostics.json").read_text(encoding="utf-8"))["attempts"]
        assert attempts[0]["failure_class"] == "target_script_residue"
        assert attempts[0]["residue_tokens"] == {"p1": ["かな"]}
        assert attempts[1]["recovered_ids"] == ["p1"]

