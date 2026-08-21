from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from translator.core.layout import apply_horizontal_layout


class EpubLayoutTests(unittest.TestCase):
    def test_horizontal_layout_adds_override_and_updates_package(self) -> None:
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

            result = apply_horizontal_layout(epub_path)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["spine_direction"], "ltr")

            with zipfile.ZipFile(epub_path, "r") as archive:
                self.assertIn("OEBPS/Styles/horizontal-zh.css", archive.namelist())
                opf = archive.read("OEBPS/content.opf").decode("utf-8")
                self.assertIn('page-progression-direction="ltr"', opf)
                self.assertIn("zh-CN", opf)
                xhtml = archive.read("OEBPS/Text/c1.xhtml").decode("utf-8")
                self.assertIn("horizontal-zh.css", xhtml)
                css = archive.read("OEBPS/Styles/horizontal-zh.css").decode("utf-8")
                self.assertIn("writing-mode: horizontal-tb !important;", css)


if __name__ == "__main__":
    unittest.main()
