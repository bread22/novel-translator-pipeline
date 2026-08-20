from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

from scripts.epub_layout import apply_horizontal_layout


class EpubLayoutTests(unittest.TestCase):
    def test_horizontal_layout_adds_override_and_updates_package(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            epub = Path(temporary) / "book.epub"
            with zipfile.ZipFile(epub, "w") as archive:
                archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
                archive.writestr(
                    "META-INF/container.xml",
                    '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
                )
                archive.writestr(
                    "OEBPS/content.opf",
                    '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0"><metadata><dc:language>ja</dc:language></metadata><manifest><item id="cover-page" href="titlepage.xhtml" media-type="application/xhtml+xml"/><item id="c1" href="text/chapter.xhtml" media-type="application/xhtml+xml"/><item id="css" href="style.css" media-type="text/css"/></manifest><spine page-progression-direction="rtl"><itemref idref="cover-page"/><itemref idref="c1"/></spine></package>',
                )
                archive.writestr("OEBPS/style.css", "body { writing-mode: vertical-rl; }")
                archive.writestr(
                    "OEBPS/titlepage.xhtml",
                    '<html xmlns="http://www.w3.org/1999/xhtml"><head><meta name="calibre:cover" content="true"/></head><body><svg xmlns="http://www.w3.org/2000/svg"><image href="cover.jpeg"/></svg></body></html>',
                )
                archive.writestr(
                    "OEBPS/text/chapter.xhtml",
                    '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>章节</title></head><body>译文</body></html>',
                )

            result = apply_horizontal_layout(epub)

            self.assertEqual(result["layout"], "horizontal")
            self.assertEqual(result["content_documents"], 2)
            with zipfile.ZipFile(epub) as archive:
                css = archive.read("OEBPS/Styles/horizontal-zh.css").decode("utf-8")
                opf = archive.read("OEBPS/content.opf").decode("utf-8")
                chapter = archive.read("OEBPS/text/chapter.xhtml").decode("utf-8")
            self.assertIn("writing-mode: horizontal-tb", css)
            self.assertIn("horizontal-zh.css", opf)
            self.assertIn('page-progression-direction="ltr"', opf)
            self.assertIn("zh-CN", opf)
            self.assertIn("../Styles/horizontal-zh.css", chapter)
            self.assertIn("译文", chapter)
            with zipfile.ZipFile(epub) as archive:
                titlepage = archive.read("OEBPS/titlepage.xhtml").decode("utf-8")
            self.assertNotIn("horizontal-zh.css", titlepage)
            self.assertIn("xmlns=\"http://www.w3.org/2000/svg\"", titlepage)


if __name__ == "__main__":
    unittest.main()
