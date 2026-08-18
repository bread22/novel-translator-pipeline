from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.book_workspace import BookWorkspace, merge_term_updates, novel_translator_terms, safe_book_name


class BookWorkspaceTests(unittest.TestCase):
    def test_safe_book_name_keeps_chinese_and_replaces_path_chars(self) -> None:
        self.assertEqual(safe_book_name("女银行职员/第一部"), "女银行职员_第一部")
        with self.assertRaises(ValueError):
            safe_book_name("..")

    def test_initialize_copies_and_unpacks_epub(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.epub"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("OEBPS/chapter.xhtml", "<p>本文</p>")
            workspace = BookWorkspace.at(root / "output", "女银行职员")
            workspace.initialize(source, book_id="book-id")
            self.assertEqual(workspace.original_epub.read_bytes(), source.read_bytes())
            self.assertTrue((workspace.unpacked_dir / "OEBPS" / "chapter.xhtml").exists())
            self.assertEqual(json.loads(workspace.progress_path.read_text())["state"], "initialized")

    def test_term_merge_adds_conflicts_without_overwriting(self) -> None:
        glossary = {"terms": [{"source": "銀行員", "target": "银行职员", "confidence": 0.95}], "conflicts": []}
        merged, summary = merge_term_updates(
            glossary,
            [
                {"source": "美樹", "target": "美树", "category": "name", "note": "人名", "confidence": 0.99},
                {"source": "銀行員", "target": "银行员", "category": "title", "note": "冲突", "confidence": 0.98},
                {"source": "支店", "target": "分行", "category": "place", "note": "", "confidence": 0.5},
            ],
            chunk_id="chunk-00001",
        )
        by_source = {item["source"]: item for item in merged["terms"]}
        self.assertEqual(by_source["銀行員"]["target"], "银行职员")
        self.assertEqual(by_source["美樹"]["target"], "美树")
        self.assertEqual(summary, {"added": 1, "confirmed": 0, "rejected": 1, "conflicted": 1})
        exported = novel_translator_terms(merged)
        self.assertNotIn("confidence", exported["terms"][0])


if __name__ == "__main__":
    unittest.main()
