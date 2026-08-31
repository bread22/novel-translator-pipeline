from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from translator.core.novel_tool import call_novel_translator


VENDOR_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "novel-translator"


def write_epub(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>EPUB Fixture</dc:title><dc:language>ja</dc:language></metadata>
  <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="chapter"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/chapter.xhtml",
            """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>第一章</h1><p>本文。</p></body></html>""",
        )


class NovelToolPythonApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        (self.runtime / "app").symlink_to(VENDOR_ROOT / "app", target_is_directory=True)
        (self.runtime / "main.py").symlink_to(VENDOR_ROOT / "main.py")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def call(self, *args: str) -> dict:
        result = call_novel_translator(*args, novel_root=self.runtime)
        self.assertEqual(result["returncode"], 0)
        self.assertIn("status", result)
        self.assertIn("summary", result)
        self.assertIn("warnings", result)
        self.assertIn("errors", result)
        return result

    def test_six_operations_use_cached_python_api_and_preserve_result_shape(self) -> None:
        source = self.root / "source.txt"
        source.write_text("第一段。\n\n第二段。\n", encoding="utf-8")
        epub_source = self.root / "source.epub"
        write_epub(epub_source)

        with patch("translator.core.novel_tool.subprocess.Popen", side_effect=AssertionError("CLI fallback was used")):
            registered = self.call(
                "add-book",
                "--path",
                str(source),
                "--title",
                "Fixture Book",
                "--id",
                "fixture-book",
            )
            self.assertEqual(registered["summary"]["book"], "fixture-book")

            snapshot = self.call("snapshot", "--book", "fixture-book", "--name", "before-fix")
            self.assertEqual(snapshot["summary"]["book"], "fixture-book")
            self.assertTrue(list((self.runtime / "data" / "books" / "fixture-book" / "snapshots").glob("*/manifest.json")))

            fixes = self.root / "fixes.json"
            fixes.write_text(
                json.dumps({"items": [{"id": "c0001-p00001", "approved_translation": "第一段（译文）。"}]}),
                encoding="utf-8",
            )
            applied = self.call("apply-review-fixes", "--book", "fixture-book", "--input", str(fixes))
            self.assertEqual(applied["summary"]["applied"], 1)

            txt_output = self.root / "translated.txt"
            exported_txt = self.call(
                "export",
                "--book",
                "fixture-book",
                "--format",
                "txt",
                "--output",
                str(txt_output),
                "--monolingual",
            )
            self.assertEqual(exported_txt["summary"]["format"], "txt")
            self.assertIn("第一段（译文）。", txt_output.read_text(encoding="utf-8"))

            registered_epub = self.call(
                "add-book",
                "--path",
                str(epub_source),
                "--title",
                "EPUB Fixture",
                "--id",
                "epub-fixture",
            )
            self.assertEqual(registered_epub["summary"]["book"], "epub-fixture")
            epub_output = self.root / "translated.epub"
            exported_epub = self.call(
                "export",
                "--book",
                "epub-fixture",
                "--format",
                "epub",
                "--output",
                str(epub_output),
                "--monolingual",
            )
            self.assertEqual(exported_epub["summary"]["format"], "epub")
            self.assertTrue(zipfile.is_zipfile(epub_output))

            validated = self.call("validate-epub", "--path", str(epub_output))
            self.assertIn(validated["status"], {"ok", "warning"})
            self.assertEqual(validated["errors"], [])

            reset = self.call("reset-translations", "--book", "fixture-book", "--all")
            self.assertEqual(reset["summary"]["reset"], 1)
            manifest = json.loads(
                (self.runtime / "data" / "books" / "fixture-book" / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertTrue(all(not paragraph["translated"] for chapter in manifest["chapters"] for paragraph in chapter["paragraphs"]))

        from translator.core import novel_tool

        self.assertIn(self.runtime.resolve(), novel_tool._VENDOR_API_CACHE)
        self.assertIs(
            novel_tool._VENDOR_API_CACHE[self.runtime.resolve()],
            novel_tool._vendor_api(self.runtime),
        )


if __name__ == "__main__":
    unittest.main()
