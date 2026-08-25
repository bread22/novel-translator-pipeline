from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from translator.core.layout import apply_horizontal_layout, inject_epub_metadata
from translator.core.metadata import (
    extract_book_metadata,
    heuristic_extract_metadata,
    sanitize_epub_filename,
)
from translator.core.workspace import BookWorkspace, write_json


class MetadataTests(unittest.TestCase):
    def test_sanitize_epub_filename_formatting(self) -> None:
        name = sanitize_epub_filename("超 凌辱法【上】", "绮罗光")
        self.assertEqual(name, "超 凌辱法【上】 - 绮罗光.epub")

        # Test illegal characters
        name_illegal = sanitize_epub_filename("书名:带有/非法*字符?<>|", "作者/名")
        self.assertEqual(name_illegal, "书名 带有 非法 字符 - 作者 名.epub")

        # Test missing author / anonymous
        name_no_author = sanitize_epub_filename("某本小说", "")
        self.assertEqual(name_no_author, "某本小说.epub")

        name_anon = sanitize_epub_filename("某本小说", "佚名")
        self.assertEqual(name_anon, "某本小说.epub")

    def test_heuristic_extract_metadata(self) -> None:
        raw = "「超」凌辱法【上】生贄は酷く輪姦せ！ (フランス書院文庫) (綺羅光) (z-library.sk, 1lib.sk, z-lib.sk)"
        meta = heuristic_extract_metadata(raw)
        self.assertEqual(meta["title_zh"], "「超」凌辱法【上】生贄は酷く輪姦せ！")
        self.assertEqual(meta["author_zh"], "綺羅光")
        self.assertIn("《「超」凌辱法【上】生贄は酷く輪姦せ！》", meta["description"])

    def test_epub_metadata_injection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            epub_path = Path(temporary) / "book.epub"
            with zipfile.ZipFile(epub_path, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip")
                archive.writestr(
                    "META-INF/container.xml",
                    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
                    "</container>",
                )
                archive.writestr(
                    "OEBPS/content.opf",
                    '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">'
                    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:language>ja</dc:language></metadata>'
                    '<manifest><item id="c1" href="Text/c1.xhtml" media-type="application/xhtml+xml"/></manifest>'
                    '<spine page-progression-direction="rtl"><itemref idref="c1"/></spine>'
                    "</package>",
                )
                archive.writestr(
                    "OEBPS/Text/c1.xhtml",
                    '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c1</title></head><body><p>本文</p></body></html>',
                )

            metadata = {
                "title_zh": "测试中文书名",
                "title_ja": "テスト日本語タイトル",
                "author_zh": "测试作者",
                "author_ja": "テスト著者",
                "description": "这是一段测试用的小说故事背景与看点简介。",
            }

            result = apply_horizontal_layout(epub_path, metadata=metadata)
            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["metadata_injected"])

            with zipfile.ZipFile(epub_path, "r") as archive:
                opf = archive.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn("测试中文书名", opf)
                self.assertIn("テスト日本語タイトル", opf)
                self.assertIn("测试作者", opf)
                self.assertIn("这是一段测试用的小说故事背景与看点简介。", opf)
                self.assertIn("title-type", opf)
                self.assertIn("subtitle", opf)
                self.assertIn("zh-CN", opf)

    def test_extract_book_metadata_caching(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = BookWorkspace.at(root, "test-book")
            workspace.initialize()
            
            cached_meta = {
                "title_zh": "已缓存中文书名",
                "title_ja": "キャッシュ済み日文原名",
                "author_zh": "已缓存作者",
                "author_ja": "キャッシュ済み著者",
                "description": "已缓存的简介内容。",
            }
            write_json(workspace.book_metadata_path, cached_meta)

            manifest = {"title": "Raw Title", "chapters": []}
            result = extract_book_metadata("test-book", manifest, workspace)
            self.assertEqual(result["title_zh"], "已缓存中文书名")
            self.assertEqual(result["description"], "已缓存的简介内容。")


if __name__ == "__main__":
    unittest.main()
