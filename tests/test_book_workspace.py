from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from translator.core.workspace import (
    BookWorkspace,
    empty_book_memory,
    merge_memory_delta,
    merge_term_updates,
    safe_book_name,
)


class BookWorkspaceTests(unittest.TestCase):
    def test_safe_book_name_keeps_chinese_and_replaces_path_chars(self) -> None:
        self.assertEqual(safe_book_name("人妻・十九歳"), "人妻・十九歳")
        self.assertEqual(safe_book_name("book/one:two"), "book_one_two")
        with self.assertRaises(ValueError):
            safe_book_name("..")

    def test_term_merge_adds_conflicts_without_overwriting(self) -> None:
        glossary = {
            "book": "b1",
            "terms": [
                {"source": "作品A", "target": "作品甲", "category": "work_title", "confidence": 0.95, "first_seen_chunk": "c1", "last_seen_chunk": "c1"}
            ],
            "conflicts": [],
        }
        updates = [
            {"source": "作品A", "target": "作品乙", "category": "work_title", "confidence": 0.95},
            {"source": "高志", "target": "高志", "confidence": 0.95},
            {"source": "低置信", "target": "无效", "confidence": 0.8},
        ]
        merged, summary = merge_term_updates(glossary, updates, chunk_id="c2")
        self.assertEqual(summary["added"], 1)
        self.assertEqual(summary["conflicted"], 1)
        self.assertEqual(summary["rejected"], 1)
        self.assertEqual(merged["terms"][0]["target"], "作品甲")
        self.assertEqual(merged["terms"][1]["target"], "高志")
        self.assertEqual(merged["conflicts"][0]["proposed_target"], "作品乙")

    def test_initialize_copies_and_unpacks_epub(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_epub = root / "source.epub"
            with zipfile.ZipFile(source_epub, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr("META-INF/container.xml", "<container/>")
            workspace = BookWorkspace.at(root / "output", "中文书名")
            workspace.initialize(source_epub, book_id="book-1")
            self.assertTrue(workspace.original_epub.exists())
            self.assertTrue((workspace.unpacked_dir / "mimetype").exists())
            self.assertTrue(workspace.glossary_path.exists())
            self.assertTrue(workspace.progress_path.exists())
            self.assertTrue(workspace.book_memory_path.exists())

    def test_memory_merge_preserves_conflicts(self) -> None:
        memory = empty_book_memory("b1")
        delta_1 = {
            "add": [{"key": "miki.job", "value": "银行职员", "category": "fact", "confidence": 0.95}],
            "update": [],
        }
        memory, summary = merge_memory_delta(memory, delta_1, chapter_id="c0001")
        self.assertEqual(summary["added"], 1)
        self.assertEqual(memory["entries"][0]["value"], "银行职员")

        delta_2 = {
            "add": [],
            "update": [{"key": "miki.job", "value": "教师", "confidence": 0.95}],
        }
        memory, summary = merge_memory_delta(memory, delta_2, chapter_id="c0002")
        self.assertEqual(summary["conflicted"], 1)
        self.assertEqual(memory["entries"][0]["value"], "银行职员")
        self.assertEqual(memory["conflicts"][0]["proposed_value"], "教师")


if __name__ == "__main__":
    unittest.main()
