from __future__ import annotations

from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import zipfile

from app.analysis import analyze_book, translation_plan
from app.book_io import export_epub, export_txt, inspect_epub, load_epub_book, load_txt_book, validate_epub
from app.cli_main import build_parser, check, command_catalog, delivery_check, doctor, retry_failed, run_folder, secret_scan, self_test, version_report
from app.config import AppConfig, AutomationConfig, ContextConfig, EpubConfig, ExportConfig, LlmConfig, QualityConfig, ReviewConfig, TranslationConfig, load_config
from app.context import context_for_batch, context_status, summarize_context
from app.delivery import package_delivery, verify_delivery
from app.feedback import verify_feedback_text
from app.manual import export_pending_translations, import_manual_translations, reset_translations
from app.memory import export_memory, import_memory, load_memory, lookup_memory, remember_translation, terminology_hash
from app.models import Paragraph, load_book, save_book
from app.placeholders import extract_placeholders, placeholder_mismatches
from app.quality import quality_report
from app.review import apply_review_fixes, review_translations
from app.runs import failed_batches, latest_failed_paragraph_ids, record_batch, run_report
from app.snapshots import create_snapshot, list_snapshots, restore_snapshot
from app.task_control import clear_stop, request_stop, stop_requested, task_status
from app.terminology import Term, extract_term_candidates, validate_terms
from app.translator import build_translation_payload, validate_llm_response
from app.workspace import prepare_agent_workspace, validate_agent_workspace


def test_load_txt_book_splits_blank_line_paragraphs(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("第一段。\n\n第二段。\n第三行。", encoding="utf-8")

    book = load_txt_book(source)

    assert book.source_type == "txt"
    assert book.title == "novel"
    assert len(book.chapters) == 1
    assert [item.source for item in book.paragraphs] == ["第一段。", "第二段。\n第三行。"]


def test_export_txt_uses_translation_when_available(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Hello.\n\nWorld.", encoding="utf-8")
    book = load_txt_book(source)
    book.paragraphs[0].translated = "你好。"

    output = tmp_path / "out.txt"
    export_txt(book, output)

    text = output.read_text(encoding="utf-8")
    assert "你好。" in text
    assert "World." in text


def test_export_txt_bilingual_writes_source_then_translation(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Hello.\n\nWorld.", encoding="utf-8")
    book = load_txt_book(source)
    book.paragraphs[0].translated = "你好。"

    output = tmp_path / "out.txt"
    export_txt(book, output, bilingual=True)

    text = output.read_text(encoding="utf-8")
    assert "Hello.\n你好。" in text


def test_load_epub_book_reads_spine_xhtml(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试书</dc:title></metadata>
  <manifest><item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c1"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/chapter1.xhtml",
            """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>第一章</title></head>
<body><h1>第一章</h1><p>她推开门。</p><p>风从走廊尽头吹来。</p></body></html>""",
        )

    book = load_epub_book(epub)

    assert book.title == "测试书"
    assert book.source_type == "epub"
    assert len(book.chapters) == 1
    assert [item.source for item in book.paragraphs] == ["第一章", "她推开门。", "风从走廊尽头吹来。"]
    assert book.paragraphs[0].metadata["epub"]["chapter_path"] == "OEBPS/chapter1.xhtml"
    assert book.paragraphs[0].metadata["epub"]["node_index"] == 0


def test_inspect_epub_reports_nav_toc_and_risks(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub(
        epub,
        chapter_name="chapter1.html",
        chapter_body='<body><nav><ol><li><a href="chapter1.html">第一章</a></li></ol></nav><p><ruby>漢<rt>かん</rt></ruby><a href="#n1">注</a></p></body>',
        extra_manifest='<item id="nav" href="chapter1.html" media-type="application/xhtml+xml" properties="nav"/><item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        spine_attrs=' toc="toc"',
    )

    report = inspect_epub(epub)

    assert report["summary"]["has_nav"] is True
    assert report["summary"]["has_toc"] is True
    assert report["summary"]["html_file_count"] >= 1
    assert report["summary"]["ruby_count"] == 1
    assert report["summary"]["link_count"] >= 1


def test_epub_export_uses_node_locator_for_duplicate_text(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub(epub, chapter_body="<body><p>Repeat.</p><p>Repeat.</p></body>")
    book = load_epub_book(epub)
    book.paragraphs[0].translated = "第一个。"
    book.paragraphs[1].translated = "第二个。"
    book.source_file = str(epub)

    output = tmp_path / "translated.epub"
    result = export_epub(book, output)
    with zipfile.ZipFile(output) as archive:
        xhtml = archive.read("OEBPS/chapter1.xhtml").decode("utf-8")

    assert result["warning_count"] == 0
    assert "第一个。" in xhtml
    assert "第二个。" in xhtml
    assert xhtml.index("第一个。") < xhtml.index("第二个。")


def test_epub_export_preserves_outer_attributes(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub(epub, chapter_body='<body><p id="p1" class="lead">Hello.</p></body>')
    book = load_epub_book(epub)
    book.paragraphs[0].translated = "你好。"
    book.source_file = str(epub)

    output = tmp_path / "translated.epub"
    export_epub(book, output)
    with zipfile.ZipFile(output) as archive:
        xhtml = archive.read("OEBPS/chapter1.xhtml").decode("utf-8")

    assert 'id="p1"' in xhtml
    assert 'class="lead"' in xhtml
    assert "你好。" in xhtml


def test_epub_export_bilingual_uses_node_locator(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub(epub, chapter_body="<body><p>Repeat.</p><p>Repeat.</p></body>")
    book = load_epub_book(epub)
    book.paragraphs[0].translated = "第一处。"
    book.paragraphs[1].translated = "第二处。"
    book.source_file = str(epub)

    output = tmp_path / "translated.epub"
    export_epub(book, output, bilingual=True)
    with zipfile.ZipFile(output) as archive:
        xhtml = archive.read("OEBPS/chapter1.xhtml").decode("utf-8")

    assert "Repeat." in xhtml
    assert "第一处。" in xhtml
    assert "第二处。" in xhtml
    assert xhtml.index("第一处。") < xhtml.index("第二处。")


def test_epub_export_translates_spine_nav_without_breaking_links(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试书</dc:title></metadata>
  <manifest>
    <item id="nav" href="Text/nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="Text/chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="nav"/><itemref idref="c1"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/Text/nav.xhtml",
            """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<body><nav epub:type="toc"><h2>测试书</h2><ol><li><a href="chapter1.xhtml">第一章</a></li></ol></nav></body></html>""",
        )
        archive.writestr(
            "OEBPS/Text/chapter1.xhtml",
            """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>第一章</h1><p>Hello.</p></body></html>""",
        )
    book = load_epub_book(epub)
    for paragraph in book.paragraphs:
        if paragraph.source == "测试书":
            paragraph.translated = "测试书译名"
        elif paragraph.source == "第一章":
            paragraph.translated = "第一章译名"
        elif paragraph.source == "Hello.":
            paragraph.translated = "你好。"
    book.source_file = str(epub)

    output = tmp_path / "translated.epub"
    export_epub(book, output)
    with zipfile.ZipFile(output) as archive:
        nav = archive.read("OEBPS/Text/nav.xhtml").decode("utf-8")
        chapter = archive.read("OEBPS/Text/chapter1.xhtml").decode("utf-8")
        opf = archive.read("OEBPS/content.opf").decode("utf-8")

    assert 'href="chapter1.xhtml"' in nav
    assert ">第一章译名</a>" in nav
    assert "<a href=\"chapter1.xhtml\" />" not in nav
    assert "你好。" in chapter
    assert '<itemref idref="nav" linear="no"' in opf


def test_epub_export_writes_mimetype_first_and_uncompressed(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_DEFLATED)
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试书</dc:title></metadata>
  <manifest><item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c1"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/chapter1.xhtml",
            """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Hello.</p></body></html>""",
        )
    book = load_epub_book(epub)
    book.paragraphs[0].translated = "你好。"
    book.source_file = str(epub)

    output = tmp_path / "translated.epub"
    export_epub(book, output)
    with zipfile.ZipFile(output) as archive:
        infos = archive.infolist()

    assert infos[0].filename == "mimetype"
    assert infos[0].compress_type == zipfile.ZIP_STORED


def test_epub_export_translates_opf_metadata(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub_with_metadata(epub)
    book = load_epub_book(epub)
    book.paragraphs[0].translated = "你好。"
    book.metadata.setdefault("epub", {})["metadata_translations"] = {
        "title": "中文书名",
        "description": "中文简介。",
        "language": "zh-CN",
    }
    book.source_file = str(epub)

    output = tmp_path / "translated.epub"
    export_epub(book, output)
    with zipfile.ZipFile(output) as archive:
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
    report = validate_epub(output)

    assert "中文书名" in opf
    assert "中文简介。" in opf
    assert "zh-CN" in opf
    assert report["summary"]["metadata_description_source_residual"] is False


def test_validate_epub_warns_on_untranslated_opf_description(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub_with_metadata(epub)

    report = validate_epub(epub)

    assert report["status"] == "warning"
    assert report["summary"]["metadata_description_source_residual"] is True
    assert any("OPF 简介" in warning for warning in report["warnings"])


def test_validate_epub_accepts_exported_local_open_package(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub(epub, chapter_body="<body><p>Hello.</p></body>")
    book = load_epub_book(epub)
    book.paragraphs[0].translated = "你好。"
    book.source_file = str(epub)

    output = tmp_path / "translated.epub"
    export_epub(book, output)
    report = validate_epub(output)

    assert report["status"] != "error"
    assert report["summary"]["valid_for_local_open"] is True
    assert report["summary"]["mimetype_first"] is True
    assert report["summary"]["mimetype_uncompressed"] is True
    assert report["summary"]["spine_missing"] == 0


def test_validate_epub_reports_empty_nav_anchor(tmp_path: Path) -> None:
    epub = tmp_path / "broken.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试书</dc:title></metadata>
  <manifest>
    <item id="nav" href="Text/nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="Text/chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="nav"/><itemref idref="c1"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/Text/nav.xhtml",
            """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><nav><ol><li>第一章<a href="chapter1.xhtml" /></li></ol></nav></body></html>""",
        )
        archive.writestr(
            "OEBPS/Text/chapter1.xhtml",
            """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Hello.</p></body></html>""",
        )

    report = validate_epub(epub)

    assert report["status"] == "error"
    assert report["summary"]["nav_empty_anchors"] == 1
    assert any(error["code"] == "epub_nav_empty_anchor" for error in report["errors"])


def test_validate_epub_reports_linear_nav_spine(tmp_path: Path) -> None:
    epub = tmp_path / "linear-nav.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试书</dc:title></metadata>
  <manifest>
    <item id="nav" href="Text/nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="c1" href="Text/chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="nav"/><itemref idref="c1"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/Text/nav.xhtml",
            """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><nav><ol><li><a href="chapter1.xhtml">第一章</a></li></ol></nav></body></html>""",
        )
        archive.writestr(
            "OEBPS/Text/chapter1.xhtml",
            """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Hello.</p></body></html>""",
        )

    report = validate_epub(epub)

    assert report["status"] == "error"
    assert report["summary"]["nav_linear_spine_count"] == 1
    assert any(error["code"] == "epub_nav_linear_spine" for error in report["errors"])


def test_epub_export_keeps_ncx_default_namespace_for_mobile_readers(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试书</dc:title></metadata>
  <manifest>
    <item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="Text/chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="toc"><itemref idref="c1"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/toc.ncx",
            """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<head><meta name="dtb:uid" content="test"/></head>
<docTitle><text>测试书</text></docTitle>
<navMap><navPoint id="nav-1"><navLabel><text>第一章</text></navLabel><content src="Text/chapter1.xhtml"/></navPoint></navMap>
</ncx>""",
        )
        archive.writestr(
            "OEBPS/Text/chapter1.xhtml",
            """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>第一章</h1><p>Hello.</p></body></html>""",
        )
    book = load_epub_book(epub)
    for paragraph in book.paragraphs:
        if paragraph.source == "第一章":
            paragraph.translated = "第一章译名"
        elif paragraph.source == "Hello.":
            paragraph.translated = "你好。"
    book.source_file = str(epub)

    output = tmp_path / "translated.epub"
    export_epub(book, output)
    with zipfile.ZipFile(output) as archive:
        toc = archive.read("OEBPS/toc.ncx").decode("utf-8")

    assert "<ncx " in toc
    assert "<ns0:ncx" not in toc
    assert "第一章译名" in toc


def test_validate_epub_reports_prefixed_ncx_namespace(tmp_path: Path) -> None:
    epub = tmp_path / "prefixed.epub"
    with zipfile.ZipFile(epub, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试书</dc:title></metadata>
  <manifest>
    <item id="toc" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="Text/chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="toc"><itemref idref="c1"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/toc.ncx",
            """<?xml version="1.0" encoding="UTF-8"?>
<ns0:ncx xmlns:ns0="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
<ns0:navMap><ns0:navPoint id="nav-1"><ns0:navLabel><ns0:text>第一章</ns0:text></ns0:navLabel><ns0:content src="Text/chapter1.xhtml"/></ns0:navPoint></ns0:navMap>
</ns0:ncx>""",
        )
        archive.writestr(
            "OEBPS/Text/chapter1.xhtml",
            """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Hello.</p></body></html>""",
        )

    report = validate_epub(epub)

    assert report["status"] == "error"
    assert report["summary"]["toc_prefixed_namespace"] is True
    assert any(error["code"] == "epub_toc_prefixed_namespace" for error in report["errors"])


def test_extract_term_candidates_counts_repeated_names(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Alice opened the door.\n\nAlice saw Bob.\n\nBob smiled at Alice.", encoding="utf-8")
    book = load_txt_book(source)

    terms = extract_term_candidates(book)

    by_source = {term.source: term for term in terms}
    assert by_source["Alice"].occurrences == 3
    assert by_source["Bob"].occurrences == 2


def test_workspace_export_and_validate(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Alice opened the door.\n\nBob smiled.", encoding="utf-8")
    book = load_txt_book(source, title="Workspace")
    books_dir = tmp_path / "books"
    save_book(books_dir, book, source)

    workspace = tmp_path / "workspace"
    report = prepare_agent_workspace(books_dir, book, [], _quality_config(), workspace)
    validation = validate_agent_workspace(book, workspace)

    assert report["status"] == "ok"
    assert (workspace / "manifest.json").exists()
    assert (workspace / "book-summary.json").exists()
    assert (workspace / "text-scope.json").exists()
    assert validation["status"] == "ok"


def test_workspace_validate_reports_missing_file(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Alice opened the door.", encoding="utf-8")
    book = load_txt_book(source, title="Workspace")
    workspace = tmp_path / "workspace"
    prepare_agent_workspace(tmp_path / "books", book, [], _quality_config(), workspace)
    (workspace / "book-summary.json").unlink()

    validation = validate_agent_workspace(book, workspace)

    assert validation["status"] == "error"
    assert validation["errors"][0]["code"] == "workspace_file_missing"


def test_pending_manual_import_and_reset(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("One.\n\nTwo.", encoding="utf-8")
    book = load_txt_book(source, title="Manual")
    books_dir = tmp_path / "books"
    save_book(books_dir, book, source)
    book.paragraphs[0].translated = "一。"

    pending_path = tmp_path / "pending.json"
    pending = export_pending_translations(book, [], pending_path)
    manual_path = tmp_path / "manual.json"
    manual_path.write_text(
        '{"items":[{"id":"c0001-p00002","translated":"二。"}]}',
        encoding="utf-8",
    )
    imported = import_manual_translations(books_dir, book, manual_path)
    reset_path = tmp_path / "reset.json"
    reset_path.write_text('["c0001-p00001"]', encoding="utf-8")
    reset = reset_translations(books_dir, book, input_path=reset_path)

    assert pending["summary"]["count"] == 1
    assert imported["summary"]["imported"] == 1
    assert book.paragraphs[1].translated == "二。"
    assert reset["summary"]["reset"] == 1
    assert book.paragraphs[0].translated == ""


def test_manual_import_rejects_unknown_id(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("One.", encoding="utf-8")
    book = load_txt_book(source, title="Manual")
    path = tmp_path / "manual.json"
    path.write_text('{"items":[{"id":"missing","translated":"x"}]}', encoding="utf-8")

    imported = import_manual_translations(tmp_path / "books", book, path)

    assert imported["status"] == "error"
    assert imported["errors"][0]["code"] == "manual_translation_invalid"


def test_feedback_matches_source_translation_and_not_found(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Alice opened the door.\n\nBob smiled.", encoding="utf-8")
    book = load_txt_book(source, title="Feedback")
    book.paragraphs[0].translated = "爱丽丝推开门。"
    feedback = tmp_path / "feedback.txt"
    feedback.write_text("Alice opened\n爱丽丝推开\nmissing text\n", encoding="utf-8")

    report = verify_feedback_text(book, feedback)

    assert report["summary"]["matched_source"] == 1
    assert report["summary"]["matched_translation"] == 1
    assert report["summary"]["not_found"] == 1


def test_placeholder_detection_and_mismatch() -> None:
    source = "Hello {name}, score=%d <ruby>word</ruby> [#note-1]"
    placeholders = extract_placeholders(source)
    paragraph = Paragraph(
        id="p1",
        chapter_id="c1",
        index=1,
        source=source,
        translated="你好{name}，分数是%d word",
    )

    missing = placeholder_mismatches(paragraph)

    assert "{name}" in [item.value for item in placeholders]
    assert "%d" in [item.value for item in placeholders]
    assert "<ruby>" in [item.value for item in placeholders]
    assert {"</ruby>", "[#note-1]"}.issubset({item["placeholder"] for item in missing})


def test_translation_memory_respects_terminology_hash(tmp_path: Path) -> None:
    books_dir = tmp_path / "books"
    entries = []
    terms = [Term(source="Alice", target="爱丽丝")]
    term_hash = terminology_hash(terms)
    remember_translation(entries, source="Alice opened the door.", translated="爱丽丝推开门。", term_hash=term_hash, model="test-model")

    assert lookup_memory(entries, "Alice opened the door.", term_hash).translated == "爱丽丝推开门。"
    assert lookup_memory(entries, "Alice opened the door.", terminology_hash([Term(source="Alice", target="艾丽丝")])) is None

    from app.memory import save_memory

    save_memory(books_dir, "book", entries)
    exported = tmp_path / "memory.json"
    export_memory(books_dir, "book", exported)
    imported_books = tmp_path / "imported"
    report = import_memory(imported_books, "book", exported)

    assert report["summary"]["imported"] == 1
    assert len(load_memory(imported_books, "book")) == 1


def test_context_summary_and_batch_payload(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("One.\n\nTwo.\n\nThree.\n\nFour.", encoding="utf-8")
    book = load_txt_book(source, title="Context")
    book.paragraphs[0].translated = "一。"
    config = ContextConfig(previous_paragraphs=1, next_paragraphs=1, chapter_summary_max_chars=8)

    report = summarize_context(tmp_path / "books", book, config)
    status = context_status(tmp_path / "books", book)
    context = context_for_batch(book, [book.paragraphs[1]], report_context(tmp_path / "books", book.id), config)

    assert report["summary"]["chapters"] == 1
    assert status["status"] == "ok"
    assert context["chapter_title"] == "Context"
    assert context["previous"][0]["translated"] == "一。"
    assert context["next"][0]["source"] == "Three."
    assert context["chapter_summary"]


def test_validate_llm_response_detects_batch_problems() -> None:
    paragraphs = [
        Paragraph(id="p1", chapter_id="c1", index=1, source="One."),
        Paragraph(id="p2", chapter_id="c1", index=2, source="Two."),
    ]

    missing = validate_llm_response(paragraphs, {"p1": "一。"})
    warnings = validate_llm_response(paragraphs, {"p1": "", "p2": "二。", "extra": "x"})

    assert missing["errors"]
    assert any("空译文" in item for item in warnings["warnings"])
    assert any("未知段落 ID" in item for item in warnings["warnings"])


def test_translation_payload_includes_quality_profile_and_context(tmp_path: Path) -> None:
    source = tmp_path / "payload.txt"
    source.write_text("Alice said, \"Run!\"\n\n{name} stayed.", encoding="utf-8")
    book = load_txt_book(source, title="Payload")
    book.paragraphs[0].translated = "爱丽丝说：“快跑！”"
    config = _app_config(Path("/tmp"))
    term = Term(source="Alice", target="爱丽丝", category="name", note="主角")
    context = {
        "chapters": {
            book.chapters[0].id: {
                "title": "Payload",
                "summary": "爱丽丝正在催促同伴离开。",
            }
        }
    }

    payload = build_translation_payload(config, [book.paragraphs[1]], [term], book=book, context=context)
    quality_profile = payload["quality_profile"]

    assert quality_profile["style_guide"]
    assert quality_profile["dialogue_style"]
    assert quality_profile["self_check_passes"] == config.translation.quality_passes
    assert any("避免逐词硬译" in item for item in quality_profile["requirements"])
    assert any("价值观判断" in item for item in quality_profile["requirements"])
    assert any("逐词直译" in item for item in quality_profile["avoid"])
    assert payload["context"]["chapter_summary"] == "爱丽丝正在催促同伴离开。"
    assert payload["items"][0]["placeholders"][0]["value"] == "{name}"


def test_translation_payload_includes_style_reference_when_configured(tmp_path: Path) -> None:
    sample = tmp_path / "style.md"
    sample.write_text("她停在雨里，轻声说：今晚不回头。\n第二行会被截断。", encoding="utf-8")
    source = tmp_path / "payload.txt"
    source.write_text("She stopped in the rain.", encoding="utf-8")
    book = load_txt_book(source, title="StyleReference")
    config = AppConfig(
        root=tmp_path,
        llm=LlmConfig(base_url="", api_key="", model="test-model"),
        translation=TranslationConfig(
            system_prompt_file="prompt.md",
            style_sample_file="style.md",
            style_sample_max_chars=12,
        ),
        context=ContextConfig(),
        review=ReviewConfig(),
        automation=AutomationConfig(),
        export=ExportConfig(),
        quality=QualityConfig(source_residual_patterns=()),
        epub=EpubConfig(),
    )

    payload = build_translation_payload(config, [book.paragraphs[0]], [], book=book, context={})

    assert payload["style_reference"]["text"] == "她停在雨里，轻声说：今晚"
    assert payload["style_reference"]["path"] == str(sample)
    assert "不要照抄" in payload["style_reference"]["instruction"]


def test_run_report_and_failed_ids(tmp_path: Path) -> None:
    books_dir = tmp_path / "books"
    record_batch(
        books_dir,
        "book",
        "run-1",
        {
            "batch_id": "run-1-b0001",
            "status": "failed",
            "paragraph_ids": ["p1", "p2"],
            "model": "test-model",
            "duration_seconds": 0.1,
            "retry_count": 0,
            "error": "missing p2",
            "warnings": [],
        },
    )

    report = run_report(books_dir, "book")
    failed = failed_batches(books_dir, "book")

    assert report["summary"]["failed"] == 1
    assert failed["summary"]["failed"] == 1
    assert latest_failed_paragraph_ids(books_dir, "book") == ["p1", "p2"]


def test_failed_batches_can_ignore_resolved_paragraphs(tmp_path: Path) -> None:
    books_dir = tmp_path / "books"
    record_batch(
        books_dir,
        "book",
        "run-1",
        {
            "batch_id": "run-1-b0001",
            "status": "failed",
            "paragraph_ids": ["p1", "p2"],
            "model": "test-model",
            "duration_seconds": 0.1,
            "retry_count": 0,
            "error": "temporary",
            "warnings": [],
        },
    )

    report = run_report(books_dir, "book", pending_ids={"p2"})
    failed = failed_batches(books_dir, "book", pending_ids={"p2"})

    assert report["summary"]["failed"] == 1
    assert report["summary"]["historical_failed"] == 1
    assert failed["details"]["batches"][0]["paragraph_ids"] == ["p2"]
    assert latest_failed_paragraph_ids(books_dir, "book", pending_ids={"p2"}) == ["p2"]


def test_task_control_stop_request_round_trip(tmp_path: Path) -> None:
    books_dir = tmp_path / "books"

    requested = request_stop(books_dir, "book", "pause")
    status = task_status(books_dir, "book")

    assert requested["summary"]["stop_requested"] is True
    assert stop_requested(books_dir, "book") is True
    assert status["summary"]["stop_requested"] is True
    cleared = clear_stop(books_dir, "book")
    assert cleared["summary"]["cleared"] is True
    assert stop_requested(books_dir, "book") is False


def test_retry_failed_dry_run_does_not_write_source_as_translation(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Hello.\n\nWorld.", encoding="utf-8")
    book = load_txt_book(source, title="Retry Dry Run")
    config = _app_config(tmp_path)
    books_dir = config.books_dir
    save_book(books_dir, book, source)
    record_batch(
        books_dir,
        book.id,
        "run-1",
        {
            "batch_id": "run-1-b0001",
            "status": "failed",
            "paragraph_ids": [book.paragraphs[0].id],
            "model": "test-model",
            "duration_seconds": 0.1,
            "retry_count": 0,
            "error": "temporary",
            "warnings": [],
        },
    )
    report = retry_failed(config, argparse.Namespace(book=book.id, run_id="retry-1", dry_run=True))
    reloaded = load_book(books_dir, book.id)

    assert report["summary"]["dry_run"] is True
    assert report["summary"]["retried"] == 1
    assert reloaded.paragraphs[0].translated == ""


def test_analysis_and_translation_plan_report_risks(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text('"Alice opened the door."\n\n"Alice opened the door."\n\nBob smiled.', encoding="utf-8")
    book = load_txt_book(source, title="Analysis")
    books_dir = tmp_path / "books"
    save_book(books_dir, book, source)

    analysis = analyze_book(books_dir, book, [])
    plan = translation_plan(books_dir, book, [], _quality_config())

    assert analysis["summary"]["duplicate_group_count"] == 1
    assert analysis["details"]["dialogue_ratio"] > 0
    assert plan["summary"]["needs_terminology"] is True


def test_quality_report_flags_common_translation_quality_issues(tmp_path: Path) -> None:
    source = tmp_path / "quality.txt"
    source.write_text(
        '"Alice opened the heavy wooden door and stepped into the moonlit corridor."\n\n'
        "The traveler crossed the silent valley, remembered the old promise, and kept walking until dawn.\n\n"
        "Bob looked back.",
        encoding="utf-8",
    )
    book = load_txt_book(source, title="Quality")
    book.paragraphs[0].translated = '"Alice opened the heavy wooden door and stepped into the moonlit corridor."'
    book.paragraphs[1].translated = "他走了。"
    book.paragraphs[2].translated = "鲍勃回头. 鲍勃回头."

    report = quality_report(book, _quality_config(), [])
    dialogue = report["details"]["dialogue_punctuation"][0]
    literal_reasons = {
        reason
        for item in report["details"]["over_literal_translation"]
        for reason in item["reasons"]
    }
    style_reasons = {
        reason
        for item in report["details"]["style_inconsistency"]
        for reason in item["reasons"]
    }

    assert report["status"] == "warning"
    assert dialogue["reasons"] == ["western_quote_in_dialogue"]
    assert {"source_tokens_retained", "high_latin_ratio", "suspiciously_short_translation"} <= literal_reasons
    assert {"western_sentence_period", "repeated_sentence"} <= style_reasons
    assert report["summary"]["review_required"] == 3


def test_validate_terms_warns_on_honorific_transliteration() -> None:
    terms = [Term(source="ミーリスさん", target="米莉丝桑", category="name")]

    errors, warnings = validate_terms(terms)

    assert errors == []
    assert any("日式敬称" in warning or "敬称音译" in warning for warning in warnings)


def test_quality_report_flags_person_address_issues(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("ミーリスさんは笑った。\n\nレンカちゃんも頷いた。", encoding="utf-8")
    book = load_txt_book(source, title="Persona")
    book.paragraphs[0].translated = "米莉丝桑笑了。"
    book.paragraphs[1].translated = "莲华酱也点了点头。"

    report = quality_report(book, _quality_config(), [])

    assert report["summary"]["person_address_issue"] == 2
    assert report["summary"]["review_required"] == 2
    assert {item["id"] for item in report["details"]["person_address_issue"]} == {book.paragraphs[0].id, book.paragraphs[1].id}


def test_quality_report_allows_native_chinese_spouse_address(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("旦那様は振り返った。", encoding="utf-8")
    book = load_txt_book(source, title="Persona")
    book.paragraphs[0].translated = "夫君回过头。"

    report = quality_report(book, _quality_config(), [])
    errors, warnings = validate_terms([Term(source="旦那様", target="夫君", category="address")])

    assert report["summary"]["person_address_issue"] == 0
    assert errors == []
    assert warnings == []


def test_quality_report_allows_native_chinese_lord_address(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("主君への忠誠を誓った。", encoding="utf-8")
    book = load_txt_book(source, title="Persona")
    book.paragraphs[0].translated = "她向主君宣誓忠诚。"

    report = quality_report(book, _quality_config(), [])

    assert report["summary"]["person_address_issue"] == 0


def test_quality_report_allows_chinese_monarch_word(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("国主は頭を下げた。", encoding="utf-8")
    book = load_txt_book(source, title="Persona")
    book.paragraphs[0].translated = "一国之主不会轻易低头，对同为君主的人尚且如此。"

    report = quality_report(book, _quality_config(), [])

    assert report["summary"]["person_address_issue"] == 0


def test_quality_report_allows_common_chinese_sauce_and_carbonated_words(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("炭酸入り果実水を飲む。甘辛のタレで味付けした。", encoding="utf-8")
    book = load_txt_book(source, title="Persona")
    book.paragraphs[0].translated = "她喝下碳酸果汁水，又用甜辣酱调味。"

    report = quality_report(book, _quality_config(), [])

    assert report["summary"]["person_address_issue"] == 0


def test_review_translations_and_apply_fixes(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text('Hello {name}.\n\n"Hi," Alice said.', encoding="utf-8")
    book = load_txt_book(source, title="Review")
    books_dir = tmp_path / "books"
    save_book(books_dir, book, source)
    book.paragraphs[0].translated = "你好。"
    book.paragraphs[1].translated = '"你好," Alice said.'
    config = _app_config(tmp_path)

    review = review_translations(config, book, [], "risk")
    review_file = Path(review["summary"]["output"])
    raw = review_file.read_text(encoding="utf-8")
    raw = raw.replace('"approved_translation": ""', '"approved_translation": "你好{name}。"')
    review_file.write_text(raw, encoding="utf-8")
    applied = apply_review_fixes(books_dir, book, review_file)

    assert review["summary"]["items"] >= 1
    assert applied["summary"]["applied"] >= 1
    assert book.paragraphs[0].translated == "你好{name}。"


def test_snapshot_restore_and_delivery_package(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("One.\n\nTwo.", encoding="utf-8")
    book = load_txt_book(source, title="Delivery")
    books_dir = tmp_path / "books"
    save_book(books_dir, book, source)
    book.paragraphs[0].translated = "一。"
    from app.models import persist_book

    persist_book(books_dir, book)
    snapshot = create_snapshot(books_dir, book, "before")
    book.paragraphs[0].translated = "坏译文"
    persist_book(books_dir, book)
    restore = restore_snapshot(books_dir, book.id, snapshot["summary"]["snapshot_id"])
    packaged_book = load_book(books_dir, book.id)
    package = package_delivery(books_dir, packaged_book, [], _quality_config(), EpubConfig(), tmp_path / "delivery")
    manifest = json.loads((tmp_path / "delivery" / "delivery-manifest.json").read_text(encoding="utf-8"))

    assert list_snapshots(books_dir, book.id)["summary"]["count"] == 1
    assert restore["summary"]["snapshot"] == snapshot["summary"]["snapshot_id"]
    assert (tmp_path / "delivery" / "delivery-manifest.json").exists()
    assert (tmp_path / "delivery" / "reports" / "delivery-check.json").exists()
    assert package["summary"]["output_dir"].endswith("delivery")
    assert package["summary"]["format"] == "txt"
    assert package["summary"]["ready"] is False
    assert package["status"] == "error"
    assert {item["code"] for item in package["errors"]} == {"pending_translations"}
    assert package["details"]["delivery_check"].endswith("delivery-check.json")
    assert manifest["status"] == "error"
    assert manifest["ready"] is False
    assert manifest["errors"][0]["code"] == "pending_translations"
    assert manifest["delivery_check_summary"]["pending"] == 1
    assert manifest["generated_at"]
    assert manifest["files"]["translated"]["sha256"] == hashlib.sha256(Path(manifest["translated"]).read_bytes()).hexdigest()
    assert manifest["files"]["delivery_check"]["bytes"] > 0
    verification = verify_delivery(tmp_path / "delivery" / "delivery-manifest.json")
    assert verification["status"] == "ok"
    assert verification["summary"]["verified"] == len(manifest["files"])


def test_package_delivery_accepts_explicit_txt_format_for_epub_source(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub(epub, chapter_body="<body><p>Hello.</p></body>")
    book = load_epub_book(epub, title="EpubPackage")
    config = _app_config(tmp_path)
    save_book(config.books_dir, book, epub)
    for paragraph in book.paragraphs:
        paragraph.translated = "你好。"
    from app.models import persist_book

    persist_book(config.books_dir, book)
    package = package_delivery(
        config.books_dir,
        book,
        [],
        _quality_config(),
        EpubConfig(),
        tmp_path / "delivery-txt",
        export_format="txt",
    )

    assert package["status"] == "ok"
    assert package["summary"]["ready"] is True
    assert package["summary"]["format"] == "txt"
    assert package["summary"]["translated"].endswith(".txt")
    assert (tmp_path / "delivery-txt" / "translated" / f"{book.id}.txt").exists()
    manifest = json.loads((tmp_path / "delivery-txt" / "delivery-manifest.json").read_text(encoding="utf-8"))
    delivery_report = json.loads((tmp_path / "delivery-txt" / "reports" / "delivery-check.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "ok"
    assert manifest["ready"] is True
    assert manifest["errors"] == []
    assert manifest["files"]["translated"]["path"].endswith(f"{book.id}.txt")
    assert manifest["files"]["translated"]["relative_path"] == f"translated/{book.id}.txt"
    assert len(manifest["files"]["translated"]["sha256"]) == 64
    assert delivery_report["summary"]["ready"] is True


def test_verify_delivery_uses_relative_paths_after_moving_package(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("One.", encoding="utf-8")
    book = load_txt_book(source, title="PortableDelivery")
    config = _app_config(tmp_path)
    save_book(config.books_dir, book, source)
    book.paragraphs[0].translated = "一。"
    from app.models import persist_book

    persist_book(config.books_dir, book)
    original = tmp_path / "portable-original"
    moved = tmp_path / "portable-moved"
    package_delivery(config.books_dir, book, [], _quality_config(), EpubConfig(), original)
    shutil.copytree(original, moved)
    shutil.rmtree(original)
    verification = verify_delivery(moved / "delivery-manifest.json")

    assert verification["status"] == "ok"
    assert verification["summary"]["verified"] > 0
    assert {item["path_source"] for item in verification["details"]["verified"]} == {"relative_path"}
    assert all(str(moved) in item["path"] for item in verification["details"]["verified"])


def test_verify_delivery_supports_legacy_absolute_manifest_paths(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("One.", encoding="utf-8")
    book = load_txt_book(source, title="LegacyDelivery")
    config = _app_config(tmp_path)
    save_book(config.books_dir, book, source)
    book.paragraphs[0].translated = "一。"
    from app.models import persist_book

    persist_book(config.books_dir, book)
    package = package_delivery(config.books_dir, book, [], _quality_config(), EpubConfig(), tmp_path / "legacy-delivery")
    manifest_path = Path(package["summary"]["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for record in manifest["files"].values():
        record.pop("relative_path", None)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    verification = verify_delivery(manifest_path)

    assert verification["status"] == "ok"
    assert {item["path_source"] for item in verification["details"]["verified"]} == {"path"}


def test_verify_delivery_rejects_relative_paths_outside_package(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("One.", encoding="utf-8")
    book = load_txt_book(source, title="UnsafeDelivery")
    config = _app_config(tmp_path)
    save_book(config.books_dir, book, source)
    book.paragraphs[0].translated = "一。"
    from app.models import persist_book

    persist_book(config.books_dir, book)
    package = package_delivery(config.books_dir, book, [], _quality_config(), EpubConfig(), tmp_path / "unsafe-delivery")
    manifest_path = Path(package["summary"]["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["translated"]["relative_path"] = "../outside.txt"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    verification = verify_delivery(manifest_path)

    assert verification["status"] == "error"
    assert "unsafe_relative_path" in {item["code"] for item in verification["errors"]}


def test_verify_delivery_returns_structured_error_for_invalid_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "delivery-manifest.json"
    manifest_path.write_text("{bad json", encoding="utf-8")
    verification = verify_delivery(manifest_path)

    assert verification["status"] == "error"
    assert verification["summary"]["errors"] == 1
    assert verification["errors"][0]["code"] == "manifest_invalid_json"


def test_verify_delivery_reports_invalid_file_records(tmp_path: Path) -> None:
    manifest_path = tmp_path / "delivery-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "book": "bad-records",
                "ready": True,
                "files": {
                    "translated": {"sha256": "x", "bytes": 1},
                    "quality_report": "not-object",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    verification = verify_delivery(manifest_path)
    codes = {item["code"] for item in verification["errors"]}

    assert verification["status"] == "error"
    assert {"file_path_missing", "file_record_invalid"} <= codes


def test_verify_delivery_detects_file_tampering(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("One.", encoding="utf-8")
    book = load_txt_book(source, title="VerifyDelivery")
    config = _app_config(tmp_path)
    save_book(config.books_dir, book, source)
    book.paragraphs[0].translated = "一。"
    from app.models import persist_book

    persist_book(config.books_dir, book)
    package = package_delivery(config.books_dir, book, [], _quality_config(), EpubConfig(), tmp_path / "verify-delivery")
    manifest_path = Path(package["summary"]["manifest"])
    Path(package["summary"]["translated"]).write_text("tampered", encoding="utf-8")
    verification = verify_delivery(manifest_path)

    assert verification["status"] == "error"
    assert "sha256_mismatch" in {item["code"] for item in verification["errors"]}


def test_package_delivery_surfaces_placeholder_blocker(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Hello {name}.", encoding="utf-8")
    book = load_txt_book(source, title="PackageBlocked")
    config = _app_config(tmp_path)
    save_book(config.books_dir, book, source)
    book.paragraphs[0].translated = "你好。"
    from app.models import persist_book

    persist_book(config.books_dir, book)
    package = package_delivery(config.books_dir, book, [], _quality_config(), EpubConfig(), tmp_path / "blocked-delivery")

    assert package["status"] == "error"
    assert package["summary"]["ready"] is False
    assert {item["code"] for item in package["errors"]} == {"placeholder_mismatch"}


def test_delivery_check_reports_ready_book(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("One.\n\nTwo.", encoding="utf-8")
    book = load_txt_book(source, title="Ready")
    config = _app_config(tmp_path)
    books_dir = config.books_dir
    save_book(books_dir, book, source)
    for paragraph in book.paragraphs:
        paragraph.translated = f"译文{paragraph.id}"
    from app.models import persist_book

    persist_book(books_dir, book)
    report = delivery_check(config, argparse.Namespace(book=book.id, format="txt"))

    assert report["status"] == "ok"
    assert report["summary"]["ready"] is True
    assert report["summary"]["pending"] == 0
    assert report["summary"]["failed_batches"] == 0


def test_delivery_check_blocks_pending_and_placeholder_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "novel.txt"
    source.write_text("Hello {name}.\n\nTwo.", encoding="utf-8")
    book = load_txt_book(source, title="Blocked")
    config = _app_config(tmp_path)
    books_dir = config.books_dir
    save_book(books_dir, book, source)
    book.paragraphs[0].translated = "你好。"
    from app.models import persist_book

    persist_book(books_dir, book)
    report = delivery_check(config, argparse.Namespace(book=book.id, format="txt"))
    codes = {item["code"] for item in report["errors"]}

    assert report["status"] == "error"
    assert report["summary"]["ready"] is False
    assert {"pending_translations", "placeholder_mismatch"} <= codes


def test_run_folder_dry_run_scans_without_registering(tmp_path: Path) -> None:
    input_dir = tmp_path / "原文"
    output_dir = tmp_path / "已翻译"
    input_dir.mkdir()
    source = input_dir / "book.txt"
    source.write_text("Hello.\n\nWorld.", encoding="utf-8")
    config = _app_config(tmp_path)

    report = run_folder(
        config,
        argparse.Namespace(
            input_dir=input_dir,
            output_dir=output_dir,
            format="txt",
            dry_run=True,
            max_batches=None,
            bilingual=True,
            monolingual=False,
        ),
    )

    assert report["status"] == "ok"
    assert report["summary"]["files"] == 1
    assert report["summary"]["bilingual"] is True
    assert not (tmp_path / "data").exists()


def test_load_config_uses_high_throughput_automation_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "setting.toml"
    config_path.write_text(
        """
[llm]
base_url = "https://api.example.com/v1"
api_key = "key"
model = "model"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(tmp_path, config_path)

    assert config.automation.workers == 200
    assert config.automation.rpm == 200
    assert config.automation.tpm == 0
    assert "生硬直译" in config.translation.style_guide
    assert "称谓" in config.translation.dialogue_style
    assert config.translation.style_sample_file == ""
    assert config.translation.style_sample_max_chars == 1200


def test_load_config_resolves_llm_from_environment(tmp_path: Path) -> None:
    config_path = tmp_path / "setting.toml"
    config_path.write_text(
        """
[llm]
base_url = "$OPENAI_BASE_URL"
api_key = "$OPENAI_API_KEY"
model = "$OPENAI_MODEL"
""".strip(),
        encoding="utf-8",
    )
    old_values = {key: os.environ.get(key) for key in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL")}
    try:
        os.environ["OPENAI_BASE_URL"] = "https://api.example.com/v1/"
        os.environ["OPENAI_API_KEY"] = "env-key"
        os.environ["OPENAI_MODEL"] = "env-model"
        config = load_config(tmp_path, config_path)
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert config.llm.base_url == "https://api.example.com/v1"
    assert config.llm.api_key == "env-key"
    assert config.llm.model == "env-model"


def test_example_config_uses_high_throughput_automation_defaults() -> None:
    root = Path(__file__).resolve().parents[1]
    config = load_config(root, root / "setting.example.toml")

    assert config.automation.workers == 200
    assert config.automation.rpm == 200
    assert config.automation.tpm == 0
    assert "生硬直译" in config.translation.style_guide
    assert "称谓" in config.translation.dialogue_style
    assert config.translation.style_sample_file == ""
    assert config.translation.style_sample_max_chars == 1200


def test_project_metadata_is_publishable() -> None:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    root = Path(__file__).resolve().parents[1]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert metadata["readme"] == "README.md"
    assert metadata["license"] == "MIT"
    assert metadata["urls"]["Repository"] == "https://github.com/OYcedar/novel-translator"
    assert (root / "LICENSE").exists()


def test_command_catalog_lists_parser_commands() -> None:
    parser_commands = _parser_command_names()
    report = command_catalog()
    catalog_commands = {item["name"] for item in report["details"]["commands"]}

    assert report["status"] == "ok"
    assert report["summary"]["commands"] == len(parser_commands)
    assert catalog_commands == parser_commands
    assert {"version", "doctor", "check", "commands", "translate", "quality-report", "package-delivery"} <= catalog_commands


def test_version_report_includes_metadata() -> None:
    report = version_report()

    assert report["status"] == "ok"
    assert report["summary"]["name"] == "novel-translator"
    assert report["summary"]["version"]
    assert report["summary"]["commands"] == len(_parser_command_names())
    assert report["details"]["repository"] == "https://github.com/OYcedar/novel-translator"


def test_check_runs_project_quality_gates() -> None:
    report = check(argparse.Namespace(config=Path(__file__).resolve().parents[1] / "setting.example.toml", strict=False))
    steps = {item["step"]: item for item in report["details"]["steps"]}

    assert report["status"] in {"ok", "warning"}
    assert report["summary"]["errors"] == 0
    assert report["summary"]["strict"] is False
    assert {"version", "doctor", "commands", "self-test", "secret-scan"} <= set(steps)
    assert steps["commands"]["summary"]["commands"] == len(_parser_command_names())


def test_check_strict_promotes_warnings_to_errors(tmp_path: Path) -> None:
    report = check(argparse.Namespace(config=tmp_path / "missing.toml", strict=True))

    assert report["status"] == "error"
    assert report["summary"]["strict"] is True
    assert report["summary"]["errors"] == len(report["warnings"])
    assert {item["code"] for item in report["errors"]} == {"warning_as_error"}


def test_doctor_reports_project_health_for_valid_config(tmp_path: Path) -> None:
    config_path = tmp_path / "setting.toml"
    style_sample = tmp_path / "style.md"
    style_sample.write_text("样例译文。", encoding="utf-8")
    config_path.write_text(
        """
[llm]
base_url = "https://api.example.com/v1"
api_key = "real-key"
model = "model"
[translation]
style_sample_file = "%s"
""".strip()
        % str(style_sample),
        encoding="utf-8",
    )

    report = doctor(argparse.Namespace(config=config_path))

    assert report["status"] in {"ok", "warning"}
    assert report["summary"]["config_exists"] is True
    assert report["summary"]["config_loadable"] is True
    assert report["summary"]["llm_configured"] is True
    assert report["summary"]["commands"] == len(_parser_command_names())
    assert report["summary"]["ci_configured"] is True
    assert report["summary"]["style_sample"]["exists"] is True
    assert report["details"]["required_files"]["readme"] is True
    assert report["details"]["required_files"]["license"] is True
    assert "api_key" not in str(report)


def test_doctor_warns_for_missing_config(tmp_path: Path) -> None:
    report = doctor(argparse.Namespace(config=tmp_path / "missing.toml"))

    assert report["status"] == "warning"
    assert report["summary"]["config_exists"] is False
    assert report["summary"]["config_loadable"] is False
    assert any("setting.toml 不存在" in warning for warning in report["warnings"])


def test_doctor_warns_for_missing_style_sample(tmp_path: Path) -> None:
    config_path = tmp_path / "setting.toml"
    config_path.write_text(
        """
[llm]
base_url = "https://api.example.com/v1"
api_key = "real-key"
model = "model"
[translation]
style_sample_file = "missing-style.md"
""".strip(),
        encoding="utf-8",
    )

    report = doctor(argparse.Namespace(config=config_path))

    assert report["status"] == "warning"
    assert report["summary"]["style_sample"]["configured"] is True
    assert report["summary"]["style_sample"]["exists"] is False
    assert any("style_sample_file" in warning for warning in report["warnings"])


def test_self_test_runs_txt_and_epub_smoke_checks() -> None:
    report = self_test()

    assert report["status"] == "ok"
    assert report["summary"]["errors"] == 0
    assert {"txt-import-export", "epub-inspect", "epub-export", "epub-validate", "epub-content-check"} <= {
        step["step"] for step in report["details"]["steps"]
    }


def test_secret_scan_passes_tracked_repository_files() -> None:
    report = secret_scan()

    assert report["status"] == "ok"
    assert report["summary"]["findings"] == 0


def test_documented_agent_commands_exist_in_parser() -> None:
    root = Path(__file__).resolve().parents[1]
    parser_commands = _parser_command_names()
    documented = set()

    readme = (root / "README.md").read_text(encoding="utf-8")
    documented.update(re.findall(r"--agent-mode\s+([a-z0-9-]+)", readme))

    skill_files = [
        root / "skills/novel-translator/SKILL.md",
        root / "skills/novel-translator/references/cli-command-contract.md",
        root / "skills/novel-translator/references/quality-and-recovery.md",
        root / "skills/novel-translator/references/terminology-workflow.md",
    ]
    for skill_file in skill_files:
        text = skill_file.read_text(encoding="utf-8")
        for line in text.splitlines():
            if not line.startswith("| `"):
                continue
            match = re.match(r"\| `([a-z0-9-]+)(?:\s|`)", line)
            if match:
                documented.add(match.group(1))

    assert documented
    assert documented <= parser_commands


def _parser_command_names() -> set[str]:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("parser has no subcommands")


def report_context(books_dir: Path, book_id: str) -> dict:
    from app.context import load_context

    return load_context(books_dir, book_id)


def _quality_config():
    from app.config import QualityConfig

    return QualityConfig(source_residual_patterns=())


def _app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        root=tmp_path,
        llm=LlmConfig(base_url="", api_key="", model="test-model"),
        translation=TranslationConfig(system_prompt_file="prompt.md"),
        context=ContextConfig(),
        review=ReviewConfig(),
        automation=AutomationConfig(),
        export=ExportConfig(),
        quality=QualityConfig(source_residual_patterns=()),
        epub=EpubConfig(),
    )


def _write_epub(
    path: Path,
    *,
    chapter_name: str = "chapter1.xhtml",
    chapter_body: str,
    extra_manifest: str = "",
    spine_attrs: str = "",
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>测试书</dc:title></metadata>
  <manifest><item id="c1" href="{chapter_name}" media-type="application/xhtml+xml"/>{extra_manifest}</manifest>
  <spine{spine_attrs}><itemref idref="c1"/></spine>
</package>""",
        )
        archive.writestr(
            f"OEBPS/{chapter_name}",
            f"""<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml">{chapter_body}</html>""",
        )
        if "toc.ncx" in extra_manifest:
            archive.writestr("OEBPS/toc.ncx", "<ncx><navMap></navMap></ncx>")


def _write_epub_with_metadata(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
        )
        archive.writestr(
            "OEBPS/content.opf",
            """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>日本語タイトル</dc:title>
    <dc:language>ja</dc:language>
    <dc:description>これは日本語の紹介文です。</dc:description>
  </metadata>
  <manifest><item id="c1" href="chapter1.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c1"/></spine>
</package>""",
        )
        archive.writestr(
            "OEBPS/chapter1.xhtml",
            """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml"><body><p>Hello.</p></body></html>""",
        )
