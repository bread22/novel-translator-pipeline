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
<item id="id_1" href="text00000.html" media-type="application/xhtml+xml"/>
<item id="id_2" href="text00001.html" media-type="application/xhtml+xml"/>
<item id="id_3" href="text00002.html" media-type="application/xhtml+xml"/>
<item id="id_4" href="text00003.html" media-type="application/xhtml+xml"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
</manifest>
<spine toc="ncx"><itemref idref="id_1"/><itemref idref="id_2"/><itemref idref="id_3"/><itemref idref="id_4"/></spine>
</package>""",
        )
        archive.writestr("OEBPS/text00000.html", "<html xmlns='http://www.w3.org/1999/xhtml'><body><p>封面</p></body></html>")
        archive.writestr(
            "OEBPS/text00001.html",
            "<html xmlns='http://www.w3.org/1999/xhtml'><body><p>第一章 序幕</p><p>第二章 秘密</p></body></html>",
        )
        archive.writestr("OEBPS/text00002.html", body)
        archive.writestr("OEBPS/text00003.html", "<html xmlns='http://www.w3.org/1999/xhtml'><body><p>版权页</p></body></html>")
        archive.writestr(
            "OEBPS/toc.ncx",
            """<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>
<navPoint><navLabel><text>第一章 序幕</text></navLabel><content src="text00002.html#missing-1"/></navPoint>
<navPoint><navLabel><text>第二章 秘密</text></navLabel><content src="text00002.html#missing-2"/></navPoint>
<navPoint><navLabel><text>第三章 转折</text></navLabel><content src="text00002.html#missing-3"/></navPoint>
<navPoint><navLabel><text>第四章 终章</text></navLabel><content src="text00002.html#missing-4"/></navPoint>
</navMap></ncx>""",
        )


def write_decorated_split_epub(path: Path) -> None:
    numbers = ("一", "二", "三", "四", "五", "六", "七", "八", "九", "十")
    body_one = [
        f'<p id="chapter-{index}">【第{number}章 标题{index}】</p><p>第{number}章正文。</p>'
        for index, number in enumerate(numbers[:6], start=1)
    ]
    body_two = [
        "<p>上一逻辑章节在物理切分点后的续文。</p>",
        *[
            f'<p id="chapter-{index}">【第{number}章 标题{index}】</p><p>第{number}章正文。</p>'
            for index, number in enumerate(numbers[6:], start=7)
        ],
    ]
    body_two_text = "".join(body_two)
    nav_points = "".join(
        f'<navPoint><navLabel><text>第{number}章 标题{index}</text></navLabel>'
        f'<content src="text/part0002_split_{0 if index <= 6 else 1:03d}.html#chapter-{index}"/></navPoint>'
        for index, number in enumerate(numbers, start=1)
    )
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
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Decorated Split Fixture</dc:title></metadata>
<manifest>
<item id="cover" href="cover.html" media-type="application/xhtml+xml"/>
<item id="toc-page" href="toc-page.html" media-type="application/xhtml+xml"/>
<item id="body-one" href="text/part0002_split_000.html" media-type="application/xhtml+xml"/>
<item id="body-two" href="text/part0002_split_001.html" media-type="application/xhtml+xml"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
</manifest>
<spine toc="ncx"><itemref idref="cover"/><itemref idref="toc-page"/><itemref idref="body-one"/><itemref idref="body-two"/></spine>
</package>""",
        )
        archive.writestr("OEBPS/cover.html", "<html xmlns='http://www.w3.org/1999/xhtml'><body><p>封面内容。</p></body></html>")
        archive.writestr(
            "OEBPS/toc-page.html",
            "<html xmlns='http://www.w3.org/1999/xhtml'><body>"
            + "".join(f"<p>第{number}章 标题{index}</p>" for index, number in enumerate(numbers, start=1))
            + "</body></html>",
        )
        archive.writestr(
            "OEBPS/text/part0002_split_000.html",
            "<html xmlns='http://www.w3.org/1999/xhtml'><body>" + "".join(body_one) + "</body></html>",
        )
        archive.writestr(
            "OEBPS/text/part0002_split_001.html",
            "<html xmlns='http://www.w3.org/1999/xhtml'><body>" + body_two_text + "</body></html>",
        )
        archive.writestr(
            "OEBPS/toc.ncx",
            '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>'
            + nav_points
            + "</navMap></ncx>",
        )


def _api_root(tmp_path: Path) -> Path:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "app").symlink_to(VENDOR_ROOT / "app", target_is_directory=True)
    return runtime


def test_monolithic_epub_keeps_translatable_spine_frontmatter_and_splits_body(tmp_path: Path) -> None:
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

    assert result["summary"]["chapters"] == 7
    manifest = json.loads(
        (runtime / "data" / "books" / "monolithic-fixture" / "manifest.json").read_text(encoding="utf-8")
    )
    assert [chapter["role"] for chapter in manifest["chapters"]] == [
        "cover",
        "toc",
        "chapter",
        "chapter",
        "chapter",
        "chapter",
        "colophon",
    ]
    assert [chapter["title"] for chapter in manifest["chapters"]] == [
        "封面",
        "目录",
        "第一章 序幕",
        "第二章 秘密",
        "第三章 转折",
        "第四章 终章",
        "版权信息",
    ]
    assert [chapter["source_path"] for chapter in manifest["chapters"]] == [
        "OEBPS/text00000.html",
        "OEBPS/text00001.html",
        "OEBPS/text00002.html",
        "OEBPS/text00002.html",
        "OEBPS/text00002.html",
        "OEBPS/text00002.html",
        "OEBPS/text00003.html",
    ]
    assert "第二章" not in [paragraph["source"] for paragraph in manifest["chapters"][4]["paragraphs"]]
    assert manifest["metadata"]["epub"]["ignored_nodes"] == {"OEBPS/text00002.html": [6]}


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
    manifest["chapters"][0]["paragraphs"][0]["translated"] = "封面译文"
    manifest["chapters"][1]["paragraphs"][0]["translated"] = "第一章 序幕译"
    manifest["chapters"][2]["paragraphs"][0]["translated"] = "第一章 序幕译"
    manifest["chapters"][3]["paragraphs"][1]["translated"] = "第二段译文。"
    manifest["chapters"][6]["paragraphs"][0]["translated"] = "尾页译文"
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
        root = ET.fromstring(archive.read("OEBPS/text00002.html"))
        cover = ET.fromstring(archive.read("OEBPS/text00000.html"))
        toc_page = ET.fromstring(archive.read("OEBPS/text00001.html"))
        colophon = ET.fromstring(archive.read("OEBPS/text00003.html"))
        toc = archive.read("OEBPS/toc.ncx").decode("utf-8")
    texts = ["".join(element.itertext()).strip() for element in root.iter() if element.tag.endswith("p")]
    cover_texts = ["".join(element.itertext()).strip() for element in cover.iter() if element.tag.endswith("p")]
    toc_page_texts = ["".join(element.itertext()).strip() for element in toc_page.iter() if element.tag.endswith("p")]
    colophon_texts = ["".join(element.itertext()).strip() for element in colophon.iter() if element.tag.endswith("p")]
    assert "封面译文" in cover_texts
    assert "第一章 序幕译" in toc_page_texts
    assert "尾页译文" in colophon_texts
    assert "第二段译文。" in texts
    assert "第二章" not in texts
    assert len([element for element in root.iter() if element.get("id", "").startswith("chapter-")]) == 4
    assert "text00002.html#chapter-0001" in toc
    assert "text00002.html#chapter-0004" in toc

    output_validation = call_novel_translator("validate-epub", "--path", str(output), novel_root=runtime)
    assert output_validation["summary"]["toc_broken_links"] == 0
    assert output_validation["errors"] == []


def test_decorated_split_epub_uses_toc_fragments_and_attaches_leading_continuation(tmp_path: Path) -> None:
    epub = tmp_path / "decorated-split.epub"
    write_decorated_split_epub(epub)
    runtime = _api_root(tmp_path)

    result = call_novel_translator(
        "add-book",
        "--path",
        str(epub),
        "--title",
        "Decorated Split Fixture",
        "--id",
        "decorated-split-fixture",
        novel_root=runtime,
    )

    assert result["summary"]["chapters"] == 12  # cover, TOC, ten logical chapters
    manifest_path = runtime / "data" / "books" / "decorated-split-fixture" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    body = [chapter for chapter in manifest["chapters"] if chapter["role"] == "chapter"]
    assert len(body) == 10
    assert [chapter["title"] for chapter in body] == [f"【第{number}章 标题{index}】" for index, number in enumerate(("一", "二", "三", "四", "五", "六", "七", "八", "九", "十"), start=1)]
    assert {paragraph["source"] for paragraph in body[5]["paragraphs"]} >= {"上一逻辑章节在物理切分点后的续文。"}
    assert "上一逻辑章节在物理切分点后的续文。" not in {paragraph["source"] for paragraph in body[6]["paragraphs"]}
    assert {
        item["metadata"]["epub"]["chapter_path"]
        for item in body[5]["paragraphs"][-1:]
    } == {"OEBPS/text/part0002_split_001.html"}
    assert manifest["metadata"]["epub"]["logical_chapter_paths"][body[5]["id"]] == [
        "OEBPS/text/part0002_split_000.html",
        "OEBPS/text/part0002_split_001.html",
    ]
    split_diagnostics = manifest["metadata"]["epub"]["chapter_split_diagnostics"]
    second = next(item for item in split_diagnostics if item["path"] == "OEBPS/text/part0002_split_001.html")
    assert second["toc_used"] is True
    assert second["fragment_resolved_count"] == 4
    assert second["continued_into"] == body[5]["id"]


def test_cross_file_decorated_chapters_export_by_paragraph_path_and_repair_ncx(tmp_path: Path) -> None:
    epub = tmp_path / "decorated-split.epub"
    write_decorated_split_epub(epub)
    runtime = _api_root(tmp_path)
    call_novel_translator(
        "add-book",
        "--path",
        str(epub),
        "--title",
        "Decorated Split Fixture",
        "--id",
        "decorated-split-fixture",
        novel_root=runtime,
    )
    manifest_path = runtime / "data" / "books" / "decorated-split-fixture" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    body = [chapter for chapter in manifest["chapters"] if chapter["role"] == "chapter"]
    body[5]["paragraphs"][0]["translated"] = "第六章译文"
    continuation = next(
        paragraph for paragraph in body[5]["paragraphs"] if paragraph["source"] == "上一逻辑章节在物理切分点后的续文。"
    )
    continuation["translated"] = "续文译文。"
    body[6]["paragraphs"][0]["translated"] = "第七章译文"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    output = tmp_path / "decorated-translated.epub"
    exported = call_novel_translator(
        "export",
        "--book",
        "decorated-split-fixture",
        "--format",
        "epub",
        "--output",
        str(output),
        "--monolingual",
        novel_root=runtime,
    )
    assert exported["summary"]["format"] == "epub"
    with zipfile.ZipFile(output) as archive:
        first = archive.read("OEBPS/text/part0002_split_000.html").decode("utf-8")
        second = archive.read("OEBPS/text/part0002_split_001.html").decode("utf-8")
        toc = archive.read("OEBPS/toc.ncx").decode("utf-8")
    assert "第六章译文" in first
    assert "续文译文。" in second
    assert "第七章译文" in second
    assert "id=\"chapter-0006\"" in first
    assert "id=\"chapter-0007\"" in second
    assert "text/part0002_split_000.html#chapter-0006" in toc
    assert "text/part0002_split_001.html#chapter-0007" in toc
    validation = call_novel_translator("validate-epub", "--path", str(output), novel_root=runtime)
    assert validation["summary"]["toc_broken_links"] == 0
    assert validation["errors"] == []
