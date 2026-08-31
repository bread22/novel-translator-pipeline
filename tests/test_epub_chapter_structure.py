from __future__ import annotations

import json
from pathlib import Path
import tempfile
from xml.etree import ElementTree as ET
import zipfile

from translator.core.novel_tool import call_novel_translator


VENDOR_ROOT = Path(__file__).resolve().parents[1] / "vendor" / "novel-translator"


def write_monolithic_epub(path: Path) -> None:
    body = """<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Fixture</title></head><body>
<p>第一章 序幕</p><p>第一段。</p>
<p>第二章 秘密</p><p>第二段。</p>
<p>第三章 转折</p><p>第三段。</p><p>第二章</p><p>第三章后续。</p>
<p>第四章 终章</p><p>第四段。</p>
</body></html>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<package xmlns="http://www.idpf.org/2007/opf" version="2.0">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Monolithic Fixture</dc:title></metadata>
<manifest>
<item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>
<item id="toc-page" href="toc.xhtml" media-type="application/xhtml+xml"/>
<item id="body" href="body.xhtml" media-type="application/xhtml+xml"/>
<item id="colophon" href="colophon.xhtml" media-type="application/xhtml+xml"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
</manifest>
<spine toc="ncx"><itemref idref="cover"/><itemref idref="toc-page"/><itemref idref="body"/><itemref idref="colophon"/></spine>
</package>""",
        )
        archive.writestr("OEBPS/cover.xhtml", "<html xmlns='http://www.w3.org/1999/xhtml'><body><p>封面</p></body></html>")
        archive.writestr(
            "OEBPS/toc.xhtml",
            "<html xmlns='http://www.w3.org/1999/xhtml'><body><p>第一章 序幕</p><p>第二章 秘密</p></body></html>",
        )
        archive.writestr("OEBPS/body.xhtml", body)
        archive.writestr("OEBPS/colophon.xhtml", "<html xmlns='http://www.w3.org/1999/xhtml'><body><p>尾页</p></body></html>")
        archive.writestr(
            "OEBPS/toc.ncx",
            """<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>
<navPoint><navLabel><text>第一章 序幕</text></navLabel><content src="body.xhtml#missing-1"/></navPoint>
<navPoint><navLabel><text>第二章 秘密</text></navLabel><content src="body.xhtml#missing-2"/></navPoint>
<navPoint><navLabel><text>第三章 转折</text></navLabel><content src="body.xhtml#missing-3"/></navPoint>
<navPoint><navLabel><text>第四章 终章</text></navLabel><content src="body.xhtml#missing-4"/></navPoint>
</navMap></ncx>""",
        )


def _api_root(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "app").symlink_to(VENDOR_ROOT / "app", target_is_directory=True)
    return runtime


def test_monolithic_epub_uses_ordered_markers_and_skips_spine_frontmatter(tmp_path: Path) -> None:
    epub = tmp_path / "monolithic.epub"
    write_monolithic_epub(epub)
    runtime = _api_root(tmp_path)

    result = call_novel_translator(
        "add-book",
        "--path",
        str(epub),
        "--title",
        "Monolithic Fixture",
        "--id",
        "monolithic-fixture",
        novel_root=runtime,
    )

    assert result["summary"]["chapters"] == 4
    manifest = json.loads(
        (runtime / "data" / "books" / "monolithic-fixture" / "manifest.json").read_text(encoding="utf-8")
    )
    assert [chapter["title"] for chapter in manifest["chapters"]] == [
        "第一章 序幕",
        "第二章 秘密",
        "第三章 转折",
        "第四章 终章",
    ]
    assert all(chapter["source_path"] == "OEBPS/body.xhtml" for chapter in manifest["chapters"])
    assert "第二章" not in [paragraph["source"] for paragraph in manifest["chapters"][2]["paragraphs"]]
    assert manifest["metadata"]["epub"]["ignored_nodes"] == {"OEBPS/body.xhtml": [6]}


def test_monolithic_epub_validates_missing_fragments_and_exports_all_same_file_chapters(tmp_path: Path) -> None:
    epub = tmp_path / "monolithic.epub"
    write_monolithic_epub(epub)
    runtime = _api_root(tmp_path)
    call_novel_translator(
        "add-book",
        "--path",
        str(epub),
        "--title",
        "Monolithic Fixture",
        "--id",
        "monolithic-fixture",
        novel_root=runtime,
    )

    validation = call_novel_translator("validate-epub", "--path", str(epub), novel_root=runtime)
    assert validation["summary"]["toc_broken_links"] == 4
    assert all(error["code"] == "epub_toc_broken_link" for error in validation["errors"])

    manifest_path = runtime / "data" / "books" / "monolithic-fixture" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["chapters"][1]["paragraphs"][1]["translated"] = "第二段译文。"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    output = tmp_path / "translated.epub"
    exported = call_novel_translator(
        "export",
        "--book",
        "monolithic-fixture",
        "--format",
        "epub",
        "--output",
        str(output),
        "--monolingual",
        novel_root=runtime,
    )
    assert exported["summary"]["format"] == "epub"

    with zipfile.ZipFile(output) as archive:
        root = ET.fromstring(archive.read("OEBPS/body.xhtml"))
        toc = archive.read("OEBPS/toc.ncx").decode("utf-8")
    texts = ["".join(element.itertext()).strip() for element in root.iter() if element.tag.endswith("p")]
    assert "第二段译文。" in texts
    assert "第二章" not in texts
    assert len([element for element in root.iter() if element.get("id", "").startswith("chapter-")]) == 4
    assert "body.xhtml#chapter-0001" in toc
    assert "body.xhtml#chapter-0004" in toc

    output_validation = call_novel_translator("validate-epub", "--path", str(output), novel_root=runtime)
    assert output_validation["summary"]["toc_broken_links"] == 0
    assert output_validation["errors"] == []
