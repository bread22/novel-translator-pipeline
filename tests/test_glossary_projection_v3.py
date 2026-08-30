from __future__ import annotations

import json
from pathlib import Path

from translator.core.workspace import BookWorkspace, write_json
from translator.providers.translator import ProviderTranslator


def test_provider_payload_reads_workspace_authority_and_selects_related_terms(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"chapters": [{"id": "c1", "paragraphs": [
        {"id": "p1", "source": "雨宮慶が止血鉗を使う", "translated": ""},
        {"id": "p2", "source": "別の段落", "translated": ""},
    ]}]})
    workspace = BookWorkspace.at(tmp_path / "output", "book")
    workspace.initialize(book_id="book")
    write_json(workspace.glossary_path, {"schema_version": "3.0", "terms": [
        {"source": "雨宮慶", "target": "雨宫庆", "category": "person", "status": "active", "manual": True, "evidence": [{"paragraph_id": "p1"}], "note": "secret"},
        {"source": "止血鉗", "target": "止血钳", "category": "medical_device", "status": "active", "manual": True, "evidence": [{"paragraph_id": "p1"}]},
        {"source": "手", "target": "手", "category": "body_part", "status": "retired", "evidence": [{"paragraph_id": "p1"}]},
        {"source": "無関係な場所", "target": "无关地点", "category": "location", "status": "active", "manual": True, "evidence": [{"paragraph_id": "p9"}]},
    ], "conflicts": [], "revisions": []})
    translator = ProviderTranslator(novel_root=tmp_path, manifest=manifest, glossary_path=workspace.glossary_path)
    payload, _ = translator._payload("book", ["p1"])
    assert payload["glossary"] == [
        {"source": "止血鉗", "target": "止血钳", "category": "medical_device"},
        {"source": "雨宮慶", "target": "雨宫庆", "category": "person"},
    ]
    serialized = json.dumps(payload["glossary"], ensure_ascii=False)
    assert "secret" not in serialized
    assert "confidence" not in serialized
    assert "evidence" not in serialized
