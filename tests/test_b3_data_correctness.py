from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from translator.core.workspace import BookWorkspace, read_json, write_json
from translator.pipeline.chapter_pipeline import IterativePipeline
from translator.web.models import GlossaryCreateRequest
from translator.web.routes.knowledge import get_glossary, update_glossary
from scripts.migrate_glossary_v2 import migrate


class B3DataCorrectnessTests(unittest.TestCase):
    def test_partial_exception_fallback_only_sends_remaining_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest_path = root / "manifest.json"
            write_json(manifest_path, {"chapters": [{"id": "c1", "paragraphs": [
                {"id": "p1", "source": "one", "translated": ""},
                {"id": "p2", "source": "two", "translated": ""},
            ]}]})
            workspace = BookWorkspace.at(root / "output", "book")
            workspace.initialize(book_id="book")
            calls: list[tuple[str, list[str]]] = []

            def targeted(provider: str, _book: str, ids: list[str], **_kwargs) -> dict:
                calls.append((provider, list(ids)))
                data = read_json(manifest_path)
                if provider == "fb1":
                    data["chapters"][0]["paragraphs"][0]["translated"] = "first"
                    write_json(manifest_path, data)
                    raise RuntimeError("partial fixture")
                if provider == "fb2":
                    data["chapters"][0]["paragraphs"][1]["translated"] = "second"
                    write_json(manifest_path, data)
                    return {"status": "ok"}
                return {"status": "error", "reason": "network"}

            pipeline = IterativePipeline(
                book="book", workspace=workspace, manifest=manifest_path,
                targeted_translator=targeted, primary_translator="primary", fallback_translators=["fb1", "fb2"],
            )
            pipeline._translate_segment_with_recovery("c1", read_json(manifest_path)["chapters"][0]["paragraphs"], [])
            self.assertEqual(calls, [("primary", ["p1", "p2"]), ("fb1", ["p1", "p2"]), ("fb2", ["p2"])])
            provenance = read_json(workspace.data_dir / "translation-provenance.json")["items"]
            self.assertEqual(provenance["p1"]["provider"], "fb1")
            self.assertEqual(provenance["p2"]["provider"], "fb2")
            self.assertTrue(provenance["p1"]["source_hash"])
            self.assertTrue(provenance["p2"]["translation_hash"])

    def test_glossary_increment_preserves_server_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = BookWorkspace.at(Path(temporary), "book")
            workspace.initialize(book_id="book")
            write_json(workspace.glossary_path, {"terms": [{
                "source": "name", "target": "旧译", "category": "character", "confidence": 0.8,
                "note": "old", "first_seen_chunk": "c1", "last_seen_chunk": "c3", "occurrences": 9,
                "sample_ids": ["p1", "p7"],
            }], "conflicts": []})
            request = GlossaryCreateRequest.model_validate({"terms": [{"source": "name", "target": "新译", "note": "manual"}]})
            with patch("translator.web.routes.knowledge.get_workspace_for_book", return_value=workspace):
                response = update_glossary("book", request)
                round_trip = get_glossary("book")
            item = response.terms[0]
            self.assertEqual(item.target, "新译")
            self.assertEqual(item.occurrences, 9)
            self.assertEqual(item.sample_ids, ["p1", "p7"])
            self.assertEqual(round_trip.terms[0].first_seen_chunk, "c1")

    def test_glossary_migration_defaults_to_dry_run_and_backs_up_on_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "glossary.json"
            write_json(path, {"terms": [{"source": "x", "target": "y", "notes": "legacy", "first_chapter": "c1"}]})
            original = path.read_bytes()
            dry_run = migrate(path)
            self.assertEqual(dry_run["mode"], "dry-run")
            self.assertEqual(path.read_bytes(), original)
            applied = migrate(path, apply=True)
            self.assertTrue(Path(applied["backup"]).is_file())
            migrated = read_json(path)
            self.assertEqual(migrated["terms"][0]["note"], "legacy")
            self.assertEqual(migrated["terms"][0]["first_seen_chunk"], "c1")


if __name__ == "__main__":
    unittest.main()
