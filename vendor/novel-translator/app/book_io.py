from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree as ET
import hashlib
import html
import posixpath
import re
from urllib.parse import unquote
import zipfile

from app.config import EpubConfig
from app.models import Book, Chapter, Paragraph, slugify


CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
NCX_NS = "http://www.daisy.org/z3986/2005/ncx/"
TRANSLATABLE_TAGS = {"p", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "div"}
RISK_TAGS = {"ruby", "rt", "rp", "table", "pre", "code", "script", "style"}

ET.register_namespace("", XHTML_NS)
ET.register_namespace("epub", EPUB_NS)


@dataclass(frozen=True)
class SpineItem:
    item_id: str
    href: str
    path: str
    media_type: str
    linear: bool
    role: str = "chapter"


@dataclass(frozen=True)
class _EpubNode:
    text: str
    tag: str
    node_id: str
    node_class: str
    risks: list[str]


@dataclass(frozen=True)
class _ChapterMarker:
    node_index: int
    title: str
    number: int
    suffix: str
    tag: str


def load_source_book(path: Path, title: str | None = None, epub_config: EpubConfig | None = None) -> Book:
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return load_txt_book(path, title=title)
    if suffix == ".epub":
        return load_epub_book(path, title=title, epub_config=epub_config)
    raise ValueError("只支持 .txt 和 .epub 文件")


def load_txt_book(path: Path, title: str | None = None) -> Book:
    text = read_text_guessing_encoding(path)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks = [block.strip() for block in re.split(r"\n\s*\n+", normalized) if block.strip()]
    book_title = title or path.stem
    chapter = Chapter(id="c0001", title=book_title, index=1)
    for index, block in enumerate(blocks, start=1):
        chapter.paragraphs.append(
            Paragraph(
                id=f"c0001-p{index:05d}",
                chapter_id=chapter.id,
                index=index,
                source=block,
            )
        )
    return Book(
        id=slugify(book_title),
        title=book_title,
        source_type="txt",
        source_file=str(path),
        chapters=[chapter],
    )


def read_text_guessing_encoding(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def load_epub_book(path: Path, title: str | None = None, epub_config: EpubConfig | None = None) -> Book:
    config = epub_config or EpubConfig()
    inspection = inspect_epub(path, config)
    with zipfile.ZipFile(path) as archive:
        opf_path = str(inspection["details"]["opf_path"])
        opf = _read_xml(archive, opf_path)
        navigation = _navigation_chapters(archive, opf, opf_path)
        navigation_paths = set(navigation)
        manifest = _opf_manifest_items(opf)
        nav_path = _find_nav_path(manifest, opf_path)
        toc_path = _find_toc_path(opf, manifest, opf_path)
        book_title = title or _metadata_title(opf) or path.stem
        chapters: list[Chapter] = []
        ignored_nodes: dict[str, list[int]] = {}
        spine_documents: list[dict[str, Any]] = []
        chapter_index = 1
        for spine_item in _spine_items(opf, opf_path, config):
            if spine_item.path not in archive.namelist():
                continue
            data = archive.read(spine_item.path)
            role = _classify_spine_item(
                spine_item,
                data,
                nav_path=nav_path,
                toc_path=toc_path,
                navigation_paths=navigation_paths,
            )
            spine_documents.append(
                {
                    "item_id": spine_item.item_id,
                    "path": spine_item.path,
                    "linear": spine_item.linear,
                    "role": role,
                }
            )
            parsed, warning_count, ignored = _parse_epub_chapters(
                data,
                chapter_index,
                spine_item.path,
                config,
                navigation_titles=navigation.get(spine_item.path, ()),
                document_role=role,
            )
            if ignored:
                ignored_nodes[spine_item.path] = ignored
            for chapter in parsed:
                if chapter.paragraphs:
                    chapters.append(chapter)
                    chapter_index += 1
            inspection["summary"]["warning_count"] += warning_count
        inspection["details"]["ignored_nodes"] = ignored_nodes
        inspection["details"]["spine_documents"] = spine_documents
    return Book(
        id=slugify(book_title),
        title=book_title,
        source_type="epub",
        source_file=str(path),
        chapters=chapters,
        metadata={
            "epub": {
                "parser_mode": inspection["summary"]["parser_mode"],
                "opf_path": inspection["details"]["opf_path"],
                "nav_path": inspection["details"]["nav_path"],
                "toc_path": inspection["details"]["toc_path"],
                "warning_count": inspection["summary"]["warning_count"],
                "warnings": inspection["warnings"],
                "ignored_nodes": ignored_nodes,
                "spine_documents": inspection["details"].get("spine_documents", []),
            }
        },
    )


def inspect_epub(path: Path, epub_config: EpubConfig | None = None) -> dict:
    config = epub_config or EpubConfig()
    warnings: list[str] = []
    details: dict[str, Any] = {}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        opf_path = _find_opf_path(archive)
        opf = _read_xml(archive, opf_path)
        manifest = _opf_manifest_items(opf)
        spine_items = _spine_items(opf, opf_path, config)
        all_spine_items = _spine_items(opf, opf_path, EpubConfig(include_non_linear_spine=True))
        nav_path = _find_nav_path(manifest, opf_path)
        toc_path = _find_toc_path(opf, manifest, opf_path)
        navigation = _navigation_chapters(archive, opf, opf_path)
        navigation_paths = set(navigation)
        html_files = [
            _join_zip_path(str(Path(opf_path).parent), item.get("href", ""))
            for item in manifest.values()
            if _is_html_item(item)
        ]
        image_count = sum(1 for item in manifest.values() if str(item.get("media-type", "")).startswith("image/"))
        css_count = sum(1 for item in manifest.values() if item.get("media-type") == "text/css")
        image_alt_title_count = 0
        chapter_stats = []
        duplicate_counter: Counter[str] = Counter()
        parser_mode = _select_parser_mode(config)
        warning_count = 0
        spine_documents: list[dict[str, Any]] = []
        for spine_item in spine_items:
            if spine_item.path not in names:
                warnings.append(f"spine 文件不存在：{spine_item.path}")
                warning_count += 1
                continue
            data = archive.read(spine_item.path)
            role = _classify_spine_item(
                spine_item,
                data,
                nav_path=nav_path,
                toc_path=toc_path,
                navigation_paths=navigation_paths,
            )
            spine_documents.append(
                {
                    "item_id": spine_item.item_id,
                    "path": spine_item.path,
                    "linear": spine_item.linear,
                    "role": role,
                }
            )
            stats = _inspect_chapter_bytes(
                data,
                spine_item.path,
                config,
                navigation_titles=navigation.get(spine_item.path, ()),
                document_role=role,
            )
            chapter_stats.append(stats)
            duplicate_counter.update(stats["texts"])
            image_alt_title_count += int(stats.get("image_alt_title_count", 0))
            warning_count += len(stats["warnings"])
        duplicate_text_count = sum(1 for _, count in duplicate_counter.items() if count > 1)
        if duplicate_text_count and config.warn_on_duplicate_source:
            warnings.append(f"存在 {duplicate_text_count} 组重复原文，导出将依赖节点定位回写")
        if any(stats["used_fallback_parser"] for stats in chapter_stats):
            warnings.append("部分章节需要增强解析器处理；未安装 beautifulsoup4/lxml 时只能报告风险")
        if any(int(stats.get("marker_warning_count", 0)) for stats in chapter_stats):
            warnings.append("检测到顺序异常或重复的章节标记；已按导航和章节编号抑制误分章")
        # Every translatable spine document is a logical unit.  Navigation
        # targets only guide chapter splitting; they do not filter translation
        # input, so cover/TOC/front/back matter remain available to the user.
        logical_stats = chapter_stats
        details = {
            "opf_path": opf_path,
            "nav_path": nav_path,
            "toc_path": toc_path,
            "manifest_count": len(manifest),
            "spine_count": len(all_spine_items),
            "linear_spine_count": len([item for item in all_spine_items if item.linear]),
            "non_linear_spine_count": len([item for item in all_spine_items if not item.linear]),
            "html_files": html_files,
            "nav_rewrite_supported": bool(nav_path),
            "toc_rewrite_supported": bool(toc_path),
            "chapter_stats": [
                {key: value for key, value in stats.items() if key != "texts"}
                for stats in chapter_stats
            ],
            "navigation_chapter_paths": sorted(navigation_paths),
            "spine_documents": spine_documents,
        }
    status = "warning" if warnings else "ok"
    return {
        "status": status,
        "warnings": warnings,
        "summary": {
            "path": str(path),
            "epub_version": _opf_version(opf),
            "parser_mode": parser_mode,
            "has_nav": bool(nav_path),
            "has_toc": bool(toc_path),
            "html_file_count": len(html_files),
            "image_count": image_count,
            "css_count": css_count,
            "chapter_count": sum(int(stats.get("detected_chapter_count", 1)) for stats in logical_stats),
            "paragraph_count": sum(int(stats.get("logical_paragraph_count", stats["paragraph_count"])) for stats in logical_stats),
            "ruby_count": sum(int(stats["ruby_count"]) for stats in logical_stats),
            "link_count": sum(int(stats["link_count"]) for stats in logical_stats),
            "footnote_link_count": sum(int(stats.get("footnote_link_count", 0)) for stats in logical_stats),
            "inline_complexity": sum(int(stats.get("inline_complexity", 0)) for stats in logical_stats),
            "image_alt_title_count": image_alt_title_count,
            "nav_rewrite_supported": bool(nav_path),
            "toc_rewrite_supported": bool(toc_path),
            "duplicate_text_count": duplicate_text_count,
            "warning_count": warning_count + len(warnings),
        },
        "details": details,
    }


def validate_epub(path: Path, epub_config: EpubConfig | None = None) -> dict:
    config = epub_config or EpubConfig()
    errors: list[dict[str, str]] = []
    warnings: list[str] = []
    details: dict[str, Any] = {}
    summary: dict[str, Any] = {
        "path": str(path),
        "valid_for_local_open": False,
        "mimetype_first": False,
        "mimetype_uncompressed": False,
        "manifest_missing": 0,
        "spine_missing": 0,
        "nav_broken_links": 0,
        "nav_empty_anchors": 0,
        "nav_linear_spine_count": 0,
        "toc_broken_links": 0,
        "toc_prefixed_namespace": False,
        "metadata_description_source_residual": False,
    }
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = set(archive.namelist())
            if not infos:
                errors.append({"code": "epub_empty_zip", "message": "EPUB 压缩包为空"})
                return _epub_validation_result(errors, warnings, summary, details)
            mimetype_info = next((info for info in infos if info.filename == "mimetype"), None)
            summary["mimetype_first"] = bool(infos and infos[0].filename == "mimetype")
            summary["mimetype_uncompressed"] = bool(mimetype_info and mimetype_info.compress_type == zipfile.ZIP_STORED)
            if mimetype_info is None:
                errors.append({"code": "epub_missing_mimetype", "message": "缺少 mimetype 文件"})
            else:
                mimetype = archive.read("mimetype").decode("ascii", errors="replace")
                summary["mimetype"] = mimetype
                if mimetype != "application/epub+zip":
                    errors.append({"code": "epub_bad_mimetype", "message": f"mimetype 应为 application/epub+zip，当前为 {mimetype}"})
                if not summary["mimetype_first"]:
                    warnings.append("mimetype 不是压缩包第一项，部分阅读器可能拒绝打开")
                if not summary["mimetype_uncompressed"]:
                    warnings.append("mimetype 被压缩，部分阅读器可能拒绝打开")
            if "META-INF/container.xml" not in names:
                errors.append({"code": "epub_missing_container", "message": "缺少 META-INF/container.xml"})
                return _epub_validation_result(errors, warnings, summary, details)
            opf_path = _find_opf_path(archive)
            if opf_path not in names:
                errors.append({"code": "epub_missing_opf", "message": f"OPF 文件不存在：{opf_path}"})
                return _epub_validation_result(errors, warnings, summary, details)
            opf = _read_xml(archive, opf_path)
            manifest = _opf_manifest_items(opf)
            metadata_texts = _opf_metadata_texts(opf)
            all_spine_items = _spine_items(opf, opf_path, EpubConfig(include_non_linear_spine=True))
            spine_items = _spine_items(opf, opf_path, config)
            nav_path = _find_nav_path(manifest, opf_path)
            toc_path = _find_toc_path(opf, manifest, opf_path)
            manifest_missing = _missing_manifest_items(manifest, opf_path, names)
            spine_missing = [item.path for item in all_spine_items if item.path not in names]
            nav_linear_spine = [item.path for item in all_spine_items if nav_path and item.path == nav_path and item.linear]
            nav_links = _validate_link_file(archive, nav_path, names, "href") if nav_path else {"count": 0, "broken": [], "empty_anchors": 0}
            toc_links = _validate_link_file(archive, toc_path, names, "src") if toc_path else {"count": 0, "broken": [], "empty_anchors": 0}
            toc_prefixed_namespace = _has_prefixed_root(archive, toc_path, "ncx") if toc_path else False
            summary.update(
                {
                    "epub_version": _opf_version(opf),
                    "opf_path": opf_path,
                    "has_nav": bool(nav_path),
                    "has_toc": bool(toc_path),
                    "nav_path": nav_path,
                    "toc_path": toc_path,
                    "manifest_count": len(manifest),
                    "spine_count": len(all_spine_items),
                    "linear_spine_count": len([item for item in all_spine_items if item.linear]),
                    "chapter_count": len(spine_items),
                    "manifest_missing": len(manifest_missing),
                    "spine_missing": len(spine_missing),
                    "nav_link_count": nav_links["count"],
                    "nav_broken_links": len(nav_links["broken"]),
                    "nav_empty_anchors": nav_links["empty_anchors"],
                    "nav_linear_spine_count": len(nav_linear_spine),
                    "toc_link_count": toc_links["count"],
                    "toc_broken_links": len(toc_links["broken"]),
                    "toc_prefixed_namespace": toc_prefixed_namespace,
                    "metadata_title": metadata_texts.get("title", ""),
                    "metadata_language": metadata_texts.get("language", ""),
                    "metadata_description_source_residual": _contains_japanese_kana(metadata_texts.get("description", "")),
                }
            )
            if not nav_path:
                warnings.append("未找到 EPUB3 nav 目录")
            if not toc_path:
                warnings.append("未找到 NCX toc 目录")
            for item_id, href, full_path in manifest_missing[:20]:
                errors.append({"code": "epub_manifest_missing", "message": f"manifest 项不存在：{item_id} {href} -> {full_path}"})
            for missing in spine_missing[:20]:
                errors.append({"code": "epub_spine_missing", "message": f"spine 章节文件不存在：{missing}"})
            for href, target in nav_links["broken"][:20]:
                errors.append({"code": "epub_nav_broken_link", "message": f"nav 链接不存在：{href} -> {target}"})
            if nav_links["empty_anchors"]:
                errors.append({"code": "epub_nav_empty_anchor", "message": f"nav 中存在 {nav_links['empty_anchors']} 个空链接文本"})
            if nav_linear_spine:
                errors.append({"code": "epub_nav_linear_spine", "message": "nav.xhtml 位于线性阅读顺序中，部分手机阅读器会把目录页当作正文第一章"})
            for src, target in toc_links["broken"][:20]:
                errors.append({"code": "epub_toc_broken_link", "message": f"toc 链接不存在：{src} -> {target}"})
            if toc_prefixed_namespace:
                errors.append({"code": "epub_toc_prefixed_namespace", "message": "toc.ncx 使用了带前缀的 ncx 根标签，部分 Android 阅读器会加载目录失败"})
            if summary["metadata_description_source_residual"]:
                warnings.append("OPF 简介中仍检测到日文假名，手机书籍详情页可能显示未翻译简介")
            details = {
                "manifest_missing": manifest_missing,
                "spine_missing": spine_missing,
                "nav_linear_spine": nav_linear_spine,
                "nav_broken_links": nav_links["broken"],
                "toc_broken_links": toc_links["broken"],
            }
    except zipfile.BadZipFile:
        errors.append({"code": "epub_bad_zip", "message": "文件不是有效 ZIP/EPUB"})
    except ET.ParseError as error:
        errors.append({"code": "epub_xml_parse_error", "message": f"EPUB XML 解析失败：{error}"})
    except KeyError as error:
        errors.append({"code": "epub_missing_file", "message": f"EPUB 缺少必要文件：{error}"})
    summary["valid_for_local_open"] = not errors
    return _epub_validation_result(errors, warnings, summary, details)


def _epub_validation_result(errors: list[dict[str, str]], warnings: list[str], summary: dict[str, Any], details: dict[str, Any]) -> dict:
    summary["error_count"] = len(errors)
    summary["warning_count"] = len(warnings)
    return {
        "status": "error" if errors else ("warning" if warnings else "ok"),
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
        "details": details,
    }


def _missing_manifest_items(manifest: dict[str, dict[str, str]], opf_path: str, names: set[str]) -> list[tuple[str, str, str]]:
    opf_dir = posixpath.dirname(opf_path)
    missing = []
    for item_id, item in manifest.items():
        href = item.get("href", "")
        if not href:
            continue
        full_path = _norm_zip_path(posixpath.join(opf_dir, href))
        if full_path not in names:
            missing.append((item_id, href, full_path))
    return missing


def _opf_metadata_texts(opf: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for element in opf.iter():
        local = _local_name(element.tag)
        if local in {"title", "description", "language"}:
            result[local] = _element_text(element)
    return result


def _contains_japanese_kana(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff]", text))


def _validate_link_file(archive: zipfile.ZipFile, path: str, names: set[str], attr: str) -> dict[str, Any]:
    if not path or path not in names:
        return {"count": 0, "broken": [], "empty_anchors": 0}
    data = archive.read(path).decode("utf-8", errors="replace")
    values = _attribute_values(data, attr)
    base = posixpath.dirname(path)
    broken = []
    anchor_cache: dict[str, set[str]] = {}
    for value in values:
        value = html.unescape(value)
        if _external_link(value):
            continue
        path_part, separator, fragment = value.partition("#")
        link_path = _link_path(path_part)
        target = path if not link_path else _norm_zip_path(posixpath.join(base, link_path))
        if target and target not in names:
            broken.append((value, target))
            continue
        if separator and fragment:
            if target not in anchor_cache:
                target_data = archive.read(target).decode("utf-8", errors="replace")
                anchor_cache[target] = set(
                    re.findall(r"\b(?:id|name)\s*=\s*['\"]([^'\"]+)['\"]", target_data, flags=re.I)
                )
            if fragment not in anchor_cache[target]:
                broken.append((value, f"{target}#{fragment}"))
    empty_anchors = 0
    if attr == "href":
        empty_anchors = len(re.findall(r"<(?:\w+:)?a\b[^>]*href\s*=\s*['\"][^'\"]+['\"][^>]*/>", data, flags=re.I))
        empty_anchors += len(re.findall(r"<(?:\w+:)?a\b[^>]*href\s*=\s*['\"][^'\"]+['\"][^>]*>\s*</(?:\w+:)?a>", data, flags=re.I | re.S))
    return {"count": len(values), "broken": broken, "empty_anchors": empty_anchors}


def _has_prefixed_root(archive: zipfile.ZipFile, path: str, local_name: str) -> bool:
    if not path or path not in archive.namelist():
        return False
    data = archive.read(path).decode("utf-8", errors="replace")
    match = re.search(r"<([A-Za-z_][\w.-]*):" + re.escape(local_name) + r"\b", data)
    return bool(match)


def _attribute_values(text: str, attr: str) -> list[str]:
    return re.findall(r"\b" + re.escape(attr) + r"\s*=\s*['\"]([^'\"]+)['\"]", text, flags=re.I)


def _external_or_fragment_link(value: str) -> bool:
    return value.startswith("#") or _external_link(value)


def _external_link(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(("http:", "https:", "mailto:", "data:", "javascript:"))


def _link_path(value: str) -> str:
    return value.split("#", 1)[0].split("?", 1)[0]


def _norm_zip_path(path: str) -> str:
    return posixpath.normpath(path).replace("\\", "/")


def _find_opf_path(archive: zipfile.ZipFile) -> str:
    container = ET.fromstring(archive.read("META-INF/container.xml"))
    rootfile = _first_by_local_name(container, "rootfile")
    if rootfile is None:
        raise ValueError("EPUB 缺少 rootfile")
    full_path = rootfile.attrib.get("full-path")
    if not full_path:
        raise ValueError("EPUB rootfile 缺少 full-path")
    return full_path


def _read_xml(archive: zipfile.ZipFile, path: str) -> ET.Element:
    return ET.fromstring(archive.read(path))


def _metadata_title(opf: ET.Element) -> str:
    for element in opf.iter():
        if _local_name(element.tag) == "title" and element.text:
            return element.text.strip()
    return ""


def _opf_version(opf: ET.Element) -> str:
    return opf.attrib.get("version", "")


def _opf_manifest_items(opf: ET.Element) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for item in opf.iter():
        if _local_name(item.tag) != "item":
            continue
        item_id = item.attrib.get("id")
        if item_id:
            result[item_id] = dict(item.attrib)
    return result


_DOCUMENT_ROLE_LABELS = {
    "cover": "封面",
    "toc": "目录",
    "frontmatter": "前置内容",
    "backmatter": "后置内容",
    "colophon": "版权信息",
}
_DOCUMENT_ROLE_HINTS = {
    "cover": ("cover", "titlepage", "title-page", "表紙", "封面"),
    "toc": ("toc", "tableofcontents", "table-of-contents", "contents", "目次", "目录"),
    "colophon": ("colophon", "copyright", "奥付", "版权"),
    "frontmatter": ("frontmatter", "front-matter", "preface", "foreword", "prologue", "序言", "前言", "序章", "扉页"),
    "backmatter": ("backmatter", "back-matter", "afterword", "epilogue", "appendix", "后记", "附录", "尾页"),
}


def _semantic_tokens(data: bytes) -> set[str]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return set()
    tokens: set[str] = set()
    for element in root.iter():
        for key, value in element.attrib.items():
            local_key = _local_name(key)
            if local_key not in {"type", "role"}:
                continue
            tokens.update(re.split(r"[\s,]+", value.casefold().strip()))
    return {token for token in tokens if token}


def _classify_spine_item(
    spine_item: SpineItem,
    data: bytes,
    *,
    nav_path: str,
    toc_path: str,
    navigation_paths: set[str],
) -> str:
    """Classify a spine XHTML without excluding it from translation.

    Navigation targets answer *where chapter boundaries are*; they do not
    answer *which files are translatable*.  The latter is the complete spine,
    with EPUB semantic hints and conservative filename hints used only to
    decide whether a document should be split into ordinary chapters.
    """
    if spine_item.path == nav_path or spine_item.path == toc_path:
        return "toc"

    semantic = _semantic_tokens(data)
    for role, values in _DOCUMENT_ROLE_HINTS.items():
        if semantic.intersection(values):
            return role

    identifier = unquote(f"{spine_item.item_id} {spine_item.href} {spine_item.path}").casefold()
    for role, values in _DOCUMENT_ROLE_HINTS.items():
        if any(value.casefold() in identifier for value in values):
            return role

    if spine_item.path in navigation_paths:
        return "chapter"
    return "chapter"


def _spine_items(opf: ET.Element, opf_path: str, config: EpubConfig) -> list[SpineItem]:
    manifest = _opf_manifest_items(opf)
    opf_dir = str(Path(opf_path).parent)
    items: list[SpineItem] = []
    in_spine = False
    for element in opf.iter():
        local = _local_name(element.tag)
        if local == "spine":
            in_spine = True
            continue
        if in_spine and local != "itemref":
            continue
        if not in_spine or local != "itemref":
            continue
        item_id = element.attrib.get("idref", "")
        manifest_item = manifest.get(item_id, {})
        href = manifest_item.get("href", "")
        linear = element.attrib.get("linear", "yes") != "no"
        if not linear and not config.include_non_linear_spine:
            continue
        if not href or not _is_html_item(manifest_item):
            continue
        items.append(
            SpineItem(
                item_id=item_id,
                href=href,
                path=_join_zip_path(opf_dir, href),
                media_type=manifest_item.get("media-type", ""),
                linear=linear,
            )
        )
    return items


def _find_nav_path(manifest: dict[str, dict[str, str]], opf_path: str) -> str:
    opf_dir = str(Path(opf_path).parent)
    for item in manifest.values():
        if "nav" in item.get("properties", "").split():
            return _join_zip_path(opf_dir, item.get("href", ""))
    return ""


def _find_toc_path(opf: ET.Element, manifest: dict[str, dict[str, str]], opf_path: str) -> str:
    opf_dir = str(Path(opf_path).parent)
    spine_toc = ""
    for element in opf.iter():
        if _local_name(element.tag) == "spine":
            spine_toc = element.attrib.get("toc", "")
            break
    if spine_toc and spine_toc in manifest:
        return _join_zip_path(opf_dir, manifest[spine_toc].get("href", ""))
    for item in manifest.values():
        if item.get("media-type") == "application/x-dtbncx+xml":
            return _join_zip_path(opf_dir, item.get("href", ""))
    return ""


def _navigation_chapters(
    archive: zipfile.ZipFile,
    opf: ET.Element,
    opf_path: str,
) -> dict[str, list[str]]:
    """Return navigation labels grouped by their target XHTML path.

    A number of EPUB producers emit a perfectly usable NCX whose fragment
    targets were never written into the XHTML.  The path portion is still
    useful for selecting the real chapter document, while the labels provide
    an additional signal for distinguishing headings from stray body text.
    """
    manifest = _opf_manifest_items(opf)
    candidates = [_find_toc_path(opf, manifest, opf_path)]
    candidates.append(_find_nav_path(manifest, opf_path))
    result: dict[str, list[str]] = {}
    for navigation_path in dict.fromkeys(path for path in candidates if path):
        if navigation_path not in archive.namelist():
            continue
        try:
            root = _read_xml(archive, navigation_path)
        except (ET.ParseError, KeyError):
            continue
        base = posixpath.dirname(navigation_path)
        if _local_name(root.tag) == "ncx":
            for point in root.iter():
                if _local_name(point.tag) != "navpoint":
                    continue
                label = next(
                    (_element_text(child) for child in point.iter() if _local_name(child.tag) == "text"),
                    "",
                )
                content = next(
                    (child.attrib.get("src", "") for child in point.iter() if _local_name(child.tag) == "content"),
                    "",
                )
                if label and content:
                    target = _norm_zip_path(posixpath.join(base, _link_path(html.unescape(content))))
                    result.setdefault(target, []).append(label)
        else:
            for anchor in root.iter():
                if _local_name(anchor.tag) != "a":
                    continue
                href = anchor.attrib.get("href", "")
                label = _element_text(anchor)
                if href and label and not _external_link(href):
                    target = _norm_zip_path(posixpath.join(base, _link_path(html.unescape(href))))
                    result.setdefault(target, []).append(label)
        if result:
            # Prefer the first valid navigation document (normally NCX) so a
            # generic EPUB3 nav cannot add unrelated links to the chapter map.
            break
    return result


def _is_html_item(item: dict[str, str]) -> bool:
    href = item.get("href", "").lower()
    media_type = item.get("media-type", "").lower()
    return (
        "xhtml" in media_type
        or "html" in media_type
        or href.endswith((".xhtml", ".html", ".htm"))
    )


def _join_zip_path(base: str, href: str) -> str:
    if not base or base == ".":
        return href
    return str(Path(base) / href).replace("\\", "/")


_CHAPTER_MARKER_RE = re.compile(
    r"^\s*第\s*(?P<number>[0-9０-９一二三四五六七八九十百千]+)\s*"
    r"(?P<unit>章|話|節|部)(?P<suffix>.*)$"
)


def _parse_chapter_number(value: str) -> int | None:
    value = value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    units = {"十": 10, "百": 100, "千": 1000}
    if not value or any(char not in digits and char not in units for char in value):
        return None
    total = 0
    current = 0
    for char in value:
        if char in units:
            total += (current or 1) * units[char]
            current = 0
        else:
            current = digits[char]
    return total + current


def _chapter_marker(text: str, tag: str, node_index: int) -> _ChapterMarker | None:
    normalized = _normalize_text(text)
    match = _CHAPTER_MARKER_RE.match(normalized)
    if not match:
        return None
    number = _parse_chapter_number(match.group("number"))
    if number is None:
        return None
    suffix = _normalize_text(match.group("suffix")).lstrip(" :：、.-—–")
    return _ChapterMarker(node_index, normalized, number, suffix, tag)


def _normalized_label(value: str) -> str:
    return re.sub(r"\s+", "", _normalize_text(value)).casefold()


def _marker_matches_navigation(marker: _ChapterMarker, navigation_titles: Sequence[str]) -> bool:
    if not navigation_titles:
        return True
    same_number: list[_ChapterMarker] = []
    for index, title in enumerate(navigation_titles):
        nav_marker = _chapter_marker(title, "nav", index)
        if nav_marker is not None and nav_marker.number == marker.number:
            same_number.append(nav_marker)
    if not same_number:
        return True
    if not marker.suffix:
        # A bare, out-of-place "第二章" is a common conversion artefact.  Do
        # not treat it as a chapter when the NCX has a titled entry for it.
        return any(not nav_marker.suffix for nav_marker in same_number)
    marker_label = _normalized_label(marker.title)
    return any(
        marker_label == _normalized_label(nav_marker.title)
        or bool(nav_marker.suffix and marker.suffix)
        for nav_marker in same_number
    )


def _select_chapter_markers(
    nodes: Sequence[_EpubNode],
    navigation_titles: Sequence[str] = (),
) -> tuple[list[_ChapterMarker], set[int], int]:
    candidates = [
        marker
        for index, node in enumerate(nodes)
        if (marker := _chapter_marker(node.text, node.tag, index)) is not None
    ]
    accepted: list[_ChapterMarker] = []
    ignored: set[int] = set()
    warnings = 0
    for marker in candidates:
        if not _marker_matches_navigation(marker, navigation_titles):
            warnings += 1
            if not marker.suffix and marker.tag not in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                ignored.add(marker.node_index)
            continue
        if accepted and marker.number <= accepted[-1].number:
            warnings += 1
            if not marker.suffix and marker.tag not in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                ignored.add(marker.node_index)
            continue
        accepted.append(marker)
    return accepted, ignored, warnings


def _build_epub_chapters(
    nodes: Sequence[_EpubNode],
    index: int,
    source_path: str,
    default_title: str,
    parser_name: str,
    navigation_titles: Sequence[str] = (),
    document_role: str = "chapter",
) -> tuple[list[Chapter], int, list[int]]:
    split_chapters = document_role == "chapter"
    if split_chapters:
        markers, ignored, warning_count = _select_chapter_markers(nodes, navigation_titles)
    else:
        # Front matter, cover, TOC, and colophon are translatable documents,
        # but chapter markers inside them are labels or links rather than
        # body chapter boundaries.
        markers, ignored, warning_count = [], set(), 0
    if len(markers) >= 2:
        starts = [0] + [marker.node_index for marker in markers[1:]]
        titles = [marker.title for marker in markers]
    else:
        starts = [0]
        titles = [markers[0].title if markers else (default_title or _DOCUMENT_ROLE_LABELS.get(document_role, "Chapter"))]

    chapters: list[Chapter] = []
    for offset, start in enumerate(starts):
        end = starts[offset + 1] if offset + 1 < len(starts) else len(nodes)
        chapter = Chapter(
            id=f"c{index + offset:04d}",
            title=titles[offset] or f"Chapter {index + offset}",
            index=index + offset,
            source_path=source_path,
            role=document_role,
        )
        paragraph_index = 1
        for node_index in range(start, end):
            if node_index in ignored:
                continue
            node = nodes[node_index]
            warning_count += 1 if node.risks else 0
            chapter.paragraphs.append(
                Paragraph(
                    id=f"{chapter.id}-p{paragraph_index:05d}",
                    chapter_id=chapter.id,
                    index=paragraph_index,
                    source=node.text,
                    metadata={
                        "epub": {
                            "chapter_path": source_path,
                            "document_role": document_role,
                            "node_index": node_index,
                            "node_tag": node.tag,
                            "node_id": node.node_id,
                            "node_class": node.node_class,
                            "text_hash": _text_hash(node.text),
                            "risks": node.risks,
                            "parser": parser_name,
                        }
                    },
                )
            )
            paragraph_index += 1
        chapters.append(chapter)
    return chapters, warning_count, sorted(ignored)


def _parse_epub_chapters(
    data: bytes,
    index: int,
    source_path: str,
    config: EpubConfig,
    navigation_titles: Sequence[str] = (),
    document_role: str = "chapter",
) -> tuple[list[Chapter], int, list[int]]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        soup = _soup(data)
        if soup is not None:
            nodes = [
                _EpubNode(
                    text=_normalize_text(node.get_text(" ")),
                    tag=str(getattr(node, "name", "")),
                    node_id=str(node.attrs.get("id", "")),
                    node_class=" ".join(node.attrs.get("class", [])) if isinstance(node.attrs.get("class"), list) else str(node.attrs.get("class", "")),
                    risks=_soup_node_risks(node),
                )
                for node in _soup_translatable_nodes(soup, document_role=document_role)
            ]
            title_node = soup.find(["h1", "h2"])
            title = _normalize_text(title_node.get_text(" ")) if title_node else _DOCUMENT_ROLE_LABELS.get(document_role, f"Chapter {index}")
            chapters, warning_count, ignored = _build_epub_chapters(
                nodes,
                index,
                source_path,
                title,
                "soup",
                navigation_titles,
                document_role,
            )
            return chapters, warning_count + 1, ignored
        raise

    nodes = [
        _EpubNode(
            text=_element_text(element),
            tag=_local_name(element.tag),
            node_id=element.attrib.get("id", ""),
            node_class=element.attrib.get("class", ""),
            risks=_element_risks(element),
        )
        for element in _translatable_elements(root, document_role=document_role)
    ]
    return _build_epub_chapters(
        nodes,
        index,
        source_path,
        _document_title(root, document_role, index),
        "stdlib",
        navigation_titles,
        document_role,
    )


def _parse_epub_chapter(
    data: bytes,
    index: int,
    source_path: str,
    config: EpubConfig,
    document_role: str = "chapter",
) -> tuple[Chapter, int]:
    chapters, warning_count, _ = _parse_epub_chapters(
        data,
        index,
        source_path,
        config,
        document_role=document_role,
    )
    if not chapters:
        return Chapter(id=f"c{index:04d}", title=f"Chapter {index}", index=index, source_path=source_path), warning_count
    return chapters[0], warning_count


def _parse_epub_chapter_with_soup(
    data: bytes,
    index: int,
    source_path: str,
    document_role: str = "chapter",
) -> tuple[Chapter, int] | None:
    soup = _soup(data)
    if soup is None:
        return None
    title_node = soup.find(["h1", "h2"])
    title = _normalize_text(title_node.get_text(" ")) if title_node else _DOCUMENT_ROLE_LABELS.get(document_role, f"Chapter {index}")
    chapter = Chapter(
        id=f"c{index:04d}",
        title=title or f"Chapter {index}",
        index=index,
        source_path=source_path,
        role=document_role,
    )
    warning_count = 1
    nodes = _soup_translatable_nodes(soup, document_role=document_role)
    for paragraph_index, node in enumerate(nodes, start=1):
        text = _normalize_text(node.get_text(" "))
        if not text:
            continue
        risks = _soup_node_risks(node)
        warning_count += 1 if risks else 0
        chapter.paragraphs.append(
            Paragraph(
                id=f"{chapter.id}-p{paragraph_index:05d}",
                chapter_id=chapter.id,
                index=paragraph_index,
                source=text,
                metadata={
                    "epub": {
                        "chapter_path": source_path,
                        "document_role": document_role,
                        "node_index": paragraph_index - 1,
                        "node_tag": str(getattr(node, "name", "")),
                        "node_id": str(node.attrs.get("id", "")),
                        "node_class": " ".join(node.attrs.get("class", [])) if isinstance(node.attrs.get("class"), list) else str(node.attrs.get("class", "")),
                        "text_hash": _text_hash(text),
                        "risks": risks,
                        "parser": "soup",
                    }
                },
            )
        )
    return chapter, warning_count


def _chapter_title(root: ET.Element) -> str:
    for element in root.iter():
        local_name = _local_name(element.tag)
        if local_name in {"h1", "h2", "title"}:
            text = _element_text(element)
            if text:
                return text
    return ""


def _document_title(root: ET.Element, document_role: str, index: int) -> str:
    if document_role == "chapter":
        return _chapter_title(root) or f"Chapter {index}"
    for element in root.iter():
        if _local_name(element.tag) in {"h1", "h2"}:
            text = _element_text(element)
            if text:
                return text
    return _DOCUMENT_ROLE_LABELS.get(document_role, f"Document {index}")


def _translatable_elements(root: ET.Element, *, document_role: str = "chapter") -> list[ET.Element]:
    elements = []
    for element in root.iter():
        local_name = _local_name(element.tag)
        allowed_tags = TRANSLATABLE_TAGS | ({"a"} if document_role == "toc" else set())
        if local_name not in allowed_tags:
            continue
        if local_name == "div" and _has_block_children(element):
            continue
        if document_role == "toc" and local_name in TRANSLATABLE_TAGS and any(
            _local_name(child.tag) == "a" for child in element.iter() if child is not element
        ):
            continue
        text = _element_text(element)
        if text:
            elements.append(element)
    return elements


def _soup_translatable_nodes(soup, *, document_role: str = "chapter") -> list:
    nodes = []
    allowed_tags = TRANSLATABLE_TAGS | ({"a"} if document_role == "toc" else set())
    for node in soup.find_all(list(allowed_tags)):
        if node.name == "div" and node.find(list(TRANSLATABLE_TAGS - {"div"})):
            continue
        if document_role == "toc" and node.name in TRANSLATABLE_TAGS and node.find("a"):
            continue
        text = _normalize_text(node.get_text(" "))
        if text:
            nodes.append(node)
    return nodes


def _has_block_children(element: ET.Element) -> bool:
    return any(child is not element and _local_name(child.tag) in TRANSLATABLE_TAGS - {"div"} for child in element.iter())


def _element_text(element: ET.Element) -> str:
    text = "".join(element.itertext())
    return _normalize_text(html.unescape(text))


def _normalize_text(text: str) -> str:
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def _element_risks(element: ET.Element) -> list[str]:
    risks: list[str] = []
    seen: set[str] = set()
    for child in element.iter():
        local = _local_name(child.tag)
        if local in RISK_TAGS and local not in seen:
            risks.append(local)
            seen.add(local)
        if local == "a" and "link" not in seen:
            risks.append("link")
            seen.add("link")
        if local == "img" and "image" not in seen:
            risks.append("image")
            seen.add("image")
    return risks


def _soup_node_risks(node) -> list[str]:
    risks: list[str] = []
    for name in sorted(RISK_TAGS | {"a", "img"}):
        if node.find(name):
            risks.append({"a": "link", "img": "image"}.get(name, name))
    return risks


def export_txt(book: Book, output: Path, bilingual: bool = False) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    chunks: list[str] = [book.title, ""]
    for chapter in book.chapters:
        chunks.extend([chapter.title, ""])
        for paragraph in chapter.paragraphs:
            text = paragraph.translated or paragraph.source
            if bilingual and paragraph.translated:
                chunks.append(paragraph.source)
            chunks.append(text)
            chunks.append("")
    output.write_text("\n".join(chunks).rstrip() + "\n", encoding="utf-8")


def _chapter_anchor_map(chapters_by_path: Mapping[str, Sequence[Chapter]]) -> dict[str, list[tuple[int, str]]]:
    result: dict[str, list[tuple[int, str]]] = {}
    for source_path, chapters in chapters_by_path.items():
        body_chapter_index = 0
        for chapter in chapters:
            if chapter.role != "chapter" or not chapter.paragraphs:
                continue
            body_chapter_index += 1
            marker = next(
                (paragraph for paragraph in chapter.paragraphs if _normalize_text(paragraph.source) == _normalize_text(chapter.title)),
                chapter.paragraphs[0],
            )
            locator = marker.metadata.get("epub", {})
            if "node_index" not in locator:
                continue
            try:
                node_index = int(locator["node_index"])
            except (TypeError, ValueError):
                continue
            result.setdefault(source_path, []).append((node_index, f"chapter-{body_chapter_index:04d}"))
    return result


def _repair_navigation_targets(
    data: bytes,
    navigation_path: str,
    anchor_map_by_path: Mapping[str, Sequence[tuple[int, str]]],
) -> bytes:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return data
    base = posixpath.dirname(navigation_path)
    used: Counter[str] = Counter()
    for element in root.iter():
        local = _local_name(element.tag)
        attribute = "src" if local == "content" else "href" if local == "a" else ""
        if not attribute:
            continue
        value = element.attrib.get(attribute, "")
        if not value or _external_link(value):
            continue
        path_part = _link_path(html.unescape(value))
        target = navigation_path if not path_part else _norm_zip_path(posixpath.join(base, path_part))
        anchors = anchor_map_by_path.get(target, ())
        ordinal = used[target]
        if not anchors or ordinal >= len(anchors):
            continue
        element.set(attribute, f"{path_part}#{anchors[ordinal][1]}")
        used[target] += 1
    return _serialize_xml(root)


def export_epub(book: Book, output: Path, epub_config: EpubConfig | None = None, *, bilingual: bool = False) -> dict:
    config = epub_config or EpubConfig()
    if book.source_type != "epub":
        raise ValueError("当前书籍不是 EPUB，无法导出 EPUB")
    output.parent.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    chapters_by_path: dict[str, list[Chapter]] = {}
    for chapter in book.chapters:
        chapters_by_path.setdefault(chapter.source_path, []).append(chapter)
    ignored_nodes_by_path = book.metadata.get("epub", {}).get("ignored_nodes", {})
    anchor_map_by_path = _chapter_anchor_map(chapters_by_path)
    title_translations = _chapter_title_translations(book)
    source = Path(book.source_file)
    with zipfile.ZipFile(source, "r") as src, zipfile.ZipFile(output, "w") as dst:
        opf_path = book.metadata.get("epub", {}).get("opf_path", "")
        nav_path = book.metadata.get("epub", {}).get("nav_path", "")
        toc_path = book.metadata.get("epub", {}).get("toc_path", "")
        infos = src.infolist()
        mimetype_info = next((info for info in infos if info.filename == "mimetype"), None)
        if mimetype_info is not None:
            _write_epub_member(dst, mimetype_info, src.read(mimetype_info.filename), force_stored=True)
        for info in infos:
            if info.filename == "mimetype":
                continue
            data = src.read(info.filename)
            is_nav = config.translate_nav and info.filename == nav_path
            is_toc = config.translate_toc and info.filename == toc_path
            if info.filename == opf_path:
                data = _update_opf_for_export(data, book, title_translations)
            elif is_nav or is_toc:
                if info.filename == nav_path or info.filename == toc_path:
                    data = _repair_navigation_targets(data, info.filename, anchor_map_by_path)
                chapters = chapters_by_path.get(info.filename)
                if chapters:
                    document_role = chapters[0].role
                    data, chapter_warnings = _replace_chapters_by_locator(
                        data,
                        chapters,
                        config,
                        bilingual=bilingual,
                        document_role=document_role,
                    )
                    warnings.extend(f"{info.filename}: {message}" for message in chapter_warnings)
                if title_translations:
                    data, nav_warnings = _replace_navigation_text(data, title_translations)
                    warnings.extend(f"{info.filename}: {message}" for message in nav_warnings)
            else:
                chapters = chapters_by_path.get(info.filename)
                if chapters:
                    document_role = chapters[0].role
                    data, chapter_warnings = _replace_chapters_by_locator(
                        data,
                        chapters,
                        config,
                        bilingual=bilingual,
                        ignored_nodes=ignored_nodes_by_path.get(info.filename, ()),
                        chapter_anchor_ids=dict(anchor_map_by_path.get(info.filename, ())),
                        document_role=document_role,
                    )
                    warnings.extend(f"{info.filename}: {message}" for message in chapter_warnings)
            _write_epub_member(dst, info, data)
    return {"warnings": warnings, "warning_count": len(warnings)}


def _update_opf_for_export(data: bytes, book: Book, title_translations: dict[str, str]) -> bytes:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return data
    changed = _replace_opf_metadata(root, book, title_translations)
    nav_ids = {
        item.attrib.get("id", "")
        for item in root.iter()
        if _local_name(item.tag) == "item" and "nav" in item.attrib.get("properties", "").split()
    }
    if not nav_ids:
        return _serialize_xml(root) if changed else data
    changed = _mark_nav_spine_non_linear(root, nav_ids) or changed
    return _serialize_xml(root) if changed else data


def _replace_opf_metadata(root: ET.Element, book: Book, title_translations: dict[str, str]) -> bool:
    metadata_translations = book.metadata.get("epub", {}).get("metadata_translations", {})
    title = str(metadata_translations.get("title") or title_translations.get(book.title, "") or "").strip()
    description = str(metadata_translations.get("description") or "").strip()
    language = str(metadata_translations.get("language") or "").strip()
    changed = False
    for element in root.iter():
        local = _local_name(element.tag)
        if local == "title" and title:
            changed = _set_plain_text_if_changed(element, title) or changed
        elif local == "description" and description:
            changed = _set_plain_text_if_changed(element, description) or changed
        elif local == "language" and language:
            changed = _set_plain_text_if_changed(element, language) or changed
    return changed


def _mark_nav_spine_non_linear(root: ET.Element, nav_ids: set[str]) -> bool:
    changed = False
    for itemref in root.iter():
        if _local_name(itemref.tag) == "itemref" and itemref.attrib.get("idref", "") in nav_ids:
            if itemref.attrib.get("linear") != "no":
                itemref.set("linear", "no")
                changed = True
    return changed


def _set_plain_text_if_changed(element: ET.Element, value: str) -> bool:
    if _element_text(element) == value and not list(element):
        return False
    element.clear()
    element.text = value
    return True


def _write_epub_member(dst: zipfile.ZipFile, info: zipfile.ZipInfo, data: bytes, *, force_stored: bool = False) -> None:
    out_info = zipfile.ZipInfo(info.filename, date_time=info.date_time)
    out_info.comment = info.comment
    out_info.extra = info.extra
    out_info.internal_attr = info.internal_attr
    out_info.external_attr = info.external_attr
    out_info.create_system = info.create_system
    out_info.compress_type = zipfile.ZIP_STORED if force_stored else info.compress_type
    dst.writestr(out_info, data)


def _replace_chapter_by_locator(
    data: bytes,
    chapter: Chapter,
    config: EpubConfig,
    *,
    bilingual: bool = False,
) -> tuple[bytes, list[str]]:
    return _replace_chapters_by_locator(
        data,
        [chapter],
        config,
        bilingual=bilingual,
        document_role=chapter.role,
    )


def _replace_chapters_by_locator(
    data: bytes,
    chapters: Sequence[Chapter],
    config: EpubConfig,
    *,
    bilingual: bool = False,
    ignored_nodes: Sequence[int] = (),
    chapter_anchor_ids: Mapping[int, str] | None = None,
    document_role: str = "chapter",
) -> tuple[bytes, list[str]]:
    warnings: list[str] = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        soup_result = _replace_chapters_by_locator_with_soup(
            data,
            chapters,
            config,
            bilingual=bilingual,
            ignored_nodes=ignored_nodes,
            document_role=document_role,
        )
        if soup_result is not None:
            return soup_result
        return data, ["章节 XML 无法解析，且增强解析器不可用，已保留原文"]
    nodes = _translatable_elements(root, document_role=document_role)
    paragraphs = [paragraph for chapter in chapters for paragraph in chapter.paragraphs]
    for paragraph in paragraphs:
        if not paragraph.translated:
            continue
        locator = paragraph.metadata.get("epub", {})
        node_index = int(locator.get("node_index", paragraph.index - 1))
        if node_index < 0 or node_index >= len(nodes):
            warnings.append(f"{paragraph.id} 节点定位失效，已保留原文")
            continue
        element = nodes[node_index]
        source_text = _element_text(element)
        expected_hash = locator.get("text_hash")
        if expected_hash and _text_hash(source_text) != expected_hash:
            warnings.append(f"{paragraph.id} 节点原文 hash 不一致，已保留原文")
            continue
        _set_element_text(element, _export_text(paragraph.source, paragraph.translated, bilingual=bilingual), config=config)
    for node_index, anchor_id in (chapter_anchor_ids or {}).items():
        if 0 <= int(node_index) < len(nodes):
            nodes[int(node_index)].set("id", str(anchor_id))
    for node_index in ignored_nodes:
        if 0 <= int(node_index) < len(nodes):
            nodes[int(node_index)].clear()
    return _serialize_xml(root), warnings


def _replace_chapter_by_locator_with_soup(data: bytes, chapter: Chapter, config: EpubConfig, *, bilingual: bool = False) -> tuple[bytes, list[str]] | None:
    return _replace_chapters_by_locator_with_soup(
        data,
        [chapter],
        config,
        bilingual=bilingual,
        document_role=chapter.role,
    )


def _replace_chapters_by_locator_with_soup(
    data: bytes,
    chapters: Sequence[Chapter],
    config: EpubConfig,
    *,
    bilingual: bool = False,
    ignored_nodes: Sequence[int] = (),
    chapter_anchor_ids: Mapping[int, str] | None = None,
    document_role: str = "chapter",
) -> tuple[bytes, list[str]] | None:
    soup = _soup(data)
    if soup is None:
        return None
    warnings: list[str] = []
    nodes = _soup_translatable_nodes(soup, document_role=document_role)
    paragraphs = [paragraph for chapter in chapters for paragraph in chapter.paragraphs]
    for paragraph in paragraphs:
        if not paragraph.translated:
            continue
        locator = paragraph.metadata.get("epub", {})
        node_index = int(locator.get("node_index", paragraph.index - 1))
        if node_index < 0 or node_index >= len(nodes):
            warnings.append(f"{paragraph.id} 节点定位失效，已保留原文")
            continue
        node = nodes[node_index]
        source_text = _normalize_text(node.get_text(" "))
        expected_hash = locator.get("text_hash")
        if expected_hash and _text_hash(source_text) != expected_hash:
            warnings.append(f"{paragraph.id} 节点原文 hash 不一致，已保留原文")
            continue
        text = _export_text(paragraph.source, paragraph.translated, bilingual=bilingual)
        if getattr(config, "preserve_inline_tags", False) and _soup_node_inline_safe(node, set(config.inline_safe_tags)):
            for child in node.find_all(True):
                child.string = ""
            node.insert(0, text)
        else:
            node.clear()
            node.string = text
    for node_index, anchor_id in (chapter_anchor_ids or {}).items():
        if 0 <= int(node_index) < len(nodes):
            nodes[int(node_index)]["id"] = str(anchor_id)
    for node_index in ignored_nodes:
        if 0 <= int(node_index) < len(nodes):
            nodes[int(node_index)].clear()
    return str(soup).encode("utf-8"), warnings


def _export_text(source: str, translated: str, *, bilingual: bool) -> str:
    if not bilingual:
        return translated
    return f"{source}\n\n{translated}"


def _set_element_text(element: ET.Element, text: str, *, config: EpubConfig) -> None:
    attrib = dict(element.attrib) if config.preserve_outer_markup else {}
    children = list(element)
    if config.preserve_inline_tags and children and _inline_children_safe(element, set(config.inline_safe_tags)):
        element.text = text
        element.attrib.clear()
        element.attrib.update(attrib)
        for child in children:
            child.text = ""
            child.tail = ""
        return
    element.clear()
    element.attrib.update(attrib)
    element.text = text


def _inspect_chapter_bytes(
    data: bytes,
    path: str,
    config: EpubConfig,
    navigation_titles: Sequence[str] = (),
    document_role: str = "chapter",
) -> dict:
    warnings: list[str] = []
    used_fallback_parser = False
    try:
        root = ET.fromstring(data)
        nodes = _translatable_elements(root, document_role=document_role)
        risks = [_element_risks(node) for node in nodes]
        texts = [_element_text(node) for node in nodes]
        link_count = sum(1 for element in root.iter() if _local_name(element.tag) == "a")
        ruby_count = sum(1 for element in root.iter() if _local_name(element.tag) == "ruby")
        footnote_link_count = sum(1 for element in root.iter() if _local_name(element.tag) == "a" and _looks_like_footnote(element.attrib.get("href", "")))
        inline_complexity = sum(len([child for child in node.iter() if child is not node]) for node in nodes)
        image_alt_title_count = sum(1 for element in root.iter() if _local_name(element.tag) == "img" and (element.attrib.get("alt") or element.attrib.get("title")))
    except ET.ParseError as error:
        soup = _soup(data)
        if soup is None:
            return {
                "path": path,
                "paragraph_count": 0,
                "logical_paragraph_count": 0,
                "detected_chapter_count": 0,
                "ignored_structural_node_count": 0,
                "marker_warning_count": 0,
                "ruby_count": 0,
                "link_count": 0,
                "risk_count": 1,
                "empty": True,
                "used_fallback_parser": False,
                "warnings": [f"章节无法用标准库解析：{error}，增强解析器不可用"],
                "footnote_link_count": 0,
                "inline_complexity": 0,
                "image_alt_title_count": 0,
                "texts": [],
            }
        used_fallback_parser = True
        nodes = _soup_translatable_nodes(soup, document_role=document_role)
        risks = [_soup_node_risks(node) for node in nodes]
        texts = [_normalize_text(node.get_text(" ")) for node in nodes]
        link_count = len(soup.find_all("a"))
        ruby_count = len(soup.find_all("ruby"))
        footnote_link_count = len([node for node in soup.find_all("a") if _looks_like_footnote(str(node.attrs.get("href", "")))])
        inline_complexity = sum(len(node.find_all(True)) for node in nodes)
        image_alt_title_count = len([node for node in soup.find_all("img") if node.attrs.get("alt") or node.attrs.get("title")])
        warnings.append("章节使用增强解析器处理")
    records = [
        _EpubNode(
            text=text,
            tag=(
                _local_name(node.tag)
                if hasattr(node, "tag") and isinstance(node.tag, str)
                else str(getattr(node, "name", ""))
            ),
            node_id=(
                node.attrib.get("id", "")
                if hasattr(node, "attrib")
                else str(getattr(node, "attrs", {}).get("id", ""))
            ),
            node_class=(
                node.attrib.get("class", "")
                if hasattr(node, "attrib")
                else " ".join(getattr(node, "attrs", {}).get("class", []))
                if isinstance(getattr(node, "attrs", {}).get("class", []), list)
                else str(getattr(node, "attrs", {}).get("class", ""))
            ),
            risks=risks[index],
        )
        for index, (node, text) in enumerate(zip(nodes, texts))
        if text
    ]
    if document_role == "chapter":
        markers, ignored, marker_warning_count = _select_chapter_markers(records, navigation_titles)
        detected_chapter_count = len(markers)
        if detected_chapter_count < 2:
            detected_chapter_count = 1
    else:
        markers, ignored, marker_warning_count = [], set(), 0
        detected_chapter_count = 1 if texts else 0
    risk_count = sum(1 for item in risks if item)
    if ruby_count and config.warn_on_ruby:
        warnings.append(f"包含 {ruby_count} 个 ruby 节点，导出后建议人工复核")
    return {
        "path": path,
        "document_role": document_role,
        "paragraph_count": len([text for text in texts if text]),
        "logical_paragraph_count": len([text for text in texts if text]) - len(ignored),
        "detected_chapter_count": detected_chapter_count,
        "ignored_structural_node_count": len(ignored),
        "marker_warning_count": marker_warning_count,
        "ruby_count": ruby_count,
        "link_count": link_count,
        "footnote_link_count": footnote_link_count,
        "inline_complexity": inline_complexity,
        "image_alt_title_count": image_alt_title_count,
        "risk_count": risk_count,
        "empty": not any(texts),
        "used_fallback_parser": used_fallback_parser,
        "warnings": warnings,
        "texts": [text for text in texts if text],
    }


def _select_parser_mode(config: EpubConfig) -> str:
    requested = config.parser.lower()
    if requested in {"stdlib", "xml"}:
        return "stdlib"
    if requested in {"bs4", "beautifulsoup", "soup", "lxml"} and _soup_available():
        return "enhanced"
    if requested in {"bs4", "beautifulsoup", "soup", "lxml"}:
        return "stdlib-no-enhanced-dependency"
    return "auto-enhanced" if _soup_available() else "auto-stdlib"


def _soup_available() -> bool:
    try:
        import bs4  # noqa: F401

        return True
    except Exception:
        return False


def _soup(data: bytes):
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return None
    parser = "lxml" if _lxml_available() else "html.parser"
    return BeautifulSoup(data, parser)


def _lxml_available() -> bool:
    try:
        import lxml  # noqa: F401

        return True
    except Exception:
        return False


def _first_by_local_name(root: ET.Element, name: str) -> ET.Element | None:
    for element in root.iter():
        if _local_name(element.tag) == name:
            return element
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chapter_title_translations(book: Book) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for chapter in book.chapters:
        if chapter.paragraphs and chapter.paragraphs[0].translated:
            first = chapter.paragraphs[0]
            # EPUB TOC entries often use the file-level chapter title while the
            # visible translated heading is the first paragraph in the file.
            # Map both forms so navigation text follows the translated heading.
            mapping[chapter.title] = first.translated
            mapping[first.source] = first.translated
        for paragraph in chapter.paragraphs:
            epub = paragraph.metadata.get("epub", {})
            if epub.get("node_tag") in {"h1", "h2", "h3", "h4", "h5", "h6"} and paragraph.translated:
                mapping[paragraph.source] = paragraph.translated
    return mapping


def _replace_navigation_text(data: bytes, title_translations: dict[str, str]) -> tuple[bytes, list[str]]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        soup = _soup(data)
        if soup is None:
            return data, ["导航文件无法解析，已保留原文"]
        changed = 0
        for node in soup.find_all(["a", "span", "text"]):
            text = _normalize_text(node.get_text(" "))
            if text in title_translations:
                node.clear()
                node.string = title_translations[text]
                changed += 1
        return str(soup).encode("utf-8"), [] if changed else []
    changed = 0
    for element in root.iter():
        text = _element_text(element)
        if text in title_translations and not list(element):
            element.text = title_translations[text]
            changed += 1
    return _serialize_xml(root), [] if changed else []


def _serialize_xml(root: ET.Element) -> bytes:
    namespace = _namespace_uri(root.tag)
    if namespace:
        ET.register_namespace("", namespace)
    ET.register_namespace("epub", EPUB_NS)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _namespace_uri(tag: str) -> str:
    if tag.startswith("{"):
        return tag[1:].split("}", 1)[0]
    return ""


def _inline_children_safe(element: ET.Element, safe_tags: set[str]) -> bool:
    for child in element.iter():
        if child is element:
            continue
        if _local_name(child.tag) not in safe_tags:
            return False
    return True


def _soup_node_inline_safe(node, safe_tags: set[str]) -> bool:
    return all(getattr(child, "name", "") in safe_tags for child in node.find_all(True))


def _looks_like_footnote(href: str) -> bool:
    value = href.lower()
    return "note" in value or "foot" in value or value.startswith("#fn") or value.startswith("#note")
