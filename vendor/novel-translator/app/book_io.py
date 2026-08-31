from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
import hashlib
import html
import posixpath
import re
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
        book_title = title or _metadata_title(opf) or path.stem
        chapters: list[Chapter] = []
        chapter_index = 1
        for spine_item in _spine_items(opf, opf_path, config):
            if spine_item.path not in archive.namelist():
                continue
            chapter, warning_count = _parse_epub_chapter(
                archive.read(spine_item.path),
                chapter_index,
                spine_item.path,
                config,
            )
            if chapter.paragraphs:
                chapters.append(chapter)
                chapter_index += 1
            inspection["summary"]["warning_count"] += warning_count
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
        for spine_item in spine_items:
            if spine_item.path not in names:
                warnings.append(f"spine 文件不存在：{spine_item.path}")
                warning_count += 1
                continue
            stats = _inspect_chapter_bytes(archive.read(spine_item.path), spine_item.path, config)
            chapter_stats.append(stats)
            duplicate_counter.update(stats["texts"])
            image_alt_title_count += int(stats.get("image_alt_title_count", 0))
            warning_count += len(stats["warnings"])
        duplicate_text_count = sum(1 for _, count in duplicate_counter.items() if count > 1)
        if duplicate_text_count and config.warn_on_duplicate_source:
            warnings.append(f"存在 {duplicate_text_count} 组重复原文，导出将依赖节点定位回写")
        if any(stats["used_fallback_parser"] for stats in chapter_stats):
            warnings.append("部分章节需要增强解析器处理；未安装 beautifulsoup4/lxml 时只能报告风险")
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
            "chapter_count": len(chapter_stats),
            "paragraph_count": sum(int(stats["paragraph_count"]) for stats in chapter_stats),
            "ruby_count": sum(int(stats["ruby_count"]) for stats in chapter_stats),
            "link_count": sum(int(stats["link_count"]) for stats in chapter_stats),
            "footnote_link_count": sum(int(stats.get("footnote_link_count", 0)) for stats in chapter_stats),
            "inline_complexity": sum(int(stats.get("inline_complexity", 0)) for stats in chapter_stats),
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
    for value in values:
        if _external_or_fragment_link(value):
            continue
        target = _norm_zip_path(posixpath.join(base, _link_path(html.unescape(value))))
        if target and target not in names:
            broken.append((value, target))
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
    lowered = value.lower()
    return lowered.startswith(("#", "http:", "https:", "mailto:", "data:"))


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


def _parse_epub_chapter(data: bytes, index: int, source_path: str, config: EpubConfig) -> tuple[Chapter, int]:
    warning_count = 0
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        soup_result = _parse_epub_chapter_with_soup(data, index, source_path)
        if soup_result is not None:
            return soup_result, 1
        raise
    title = _chapter_title(root) or f"Chapter {index}"
    chapter = Chapter(id=f"c{index:04d}", title=title, index=index, source_path=source_path)
    nodes = _translatable_elements(root)
    for paragraph_index, element in enumerate(nodes, start=1):
        text = _element_text(element)
        if not text:
            continue
        risks = _element_risks(element)
        if risks:
            warning_count += 1
        chapter.paragraphs.append(
            Paragraph(
                id=f"{chapter.id}-p{paragraph_index:05d}",
                chapter_id=chapter.id,
                index=paragraph_index,
                source=text,
                metadata={
                    "epub": {
                        "chapter_path": source_path,
                        "node_index": paragraph_index - 1,
                        "node_tag": _local_name(element.tag),
                        "node_id": element.attrib.get("id", ""),
                        "node_class": element.attrib.get("class", ""),
                        "text_hash": _text_hash(text),
                        "risks": risks,
                        "parser": "stdlib",
                    }
                },
            )
        )
    return chapter, warning_count


def _parse_epub_chapter_with_soup(data: bytes, index: int, source_path: str) -> tuple[Chapter, int] | None:
    soup = _soup(data)
    if soup is None:
        return None
    title_node = soup.find(["h1", "h2", "title"])
    title = _normalize_text(title_node.get_text(" ")) if title_node else f"Chapter {index}"
    chapter = Chapter(id=f"c{index:04d}", title=title or f"Chapter {index}", index=index, source_path=source_path)
    warning_count = 1
    nodes = _soup_translatable_nodes(soup)
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


def _translatable_elements(root: ET.Element) -> list[ET.Element]:
    elements = []
    for element in root.iter():
        local_name = _local_name(element.tag)
        if local_name not in TRANSLATABLE_TAGS:
            continue
        if local_name == "div" and _has_block_children(element):
            continue
        text = _element_text(element)
        if text:
            elements.append(element)
    return elements


def _soup_translatable_nodes(soup) -> list:
    nodes = []
    for node in soup.find_all(list(TRANSLATABLE_TAGS)):
        if node.name == "div" and node.find(list(TRANSLATABLE_TAGS - {"div"})):
            continue
        text = _normalize_text(node.get_text(" "))
        if text:
            nodes.append(node)
    return nodes


def _has_block_children(element: ET.Element) -> bool:
    return any(_local_name(child.tag) in TRANSLATABLE_TAGS - {"div"} for child in list(element))


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


def export_epub(book: Book, output: Path, epub_config: EpubConfig | None = None, *, bilingual: bool = False) -> dict:
    config = epub_config or EpubConfig()
    if book.source_type != "epub":
        raise ValueError("当前书籍不是 EPUB，无法导出 EPUB")
    output.parent.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    by_chapter = {chapter.source_path: chapter for chapter in book.chapters}
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
            elif title_translations and (is_nav or is_toc):
                data, nav_warnings = _replace_navigation_text(data, title_translations)
                warnings.extend(f"{info.filename}: {message}" for message in nav_warnings)
            else:
                chapter = by_chapter.get(info.filename)
                if chapter is not None:
                    data, chapter_warnings = _replace_chapter_by_locator(data, chapter, config, bilingual=bilingual)
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


def _replace_chapter_by_locator(data: bytes, chapter: Chapter, config: EpubConfig, *, bilingual: bool = False) -> tuple[bytes, list[str]]:
    warnings: list[str] = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        soup_result = _replace_chapter_by_locator_with_soup(data, chapter, config, bilingual=bilingual)
        if soup_result is not None:
            return soup_result
        return data, ["章节 XML 无法解析，且增强解析器不可用，已保留原文"]
    nodes = _translatable_elements(root)
    for paragraph in chapter.paragraphs:
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
    return _serialize_xml(root), warnings


def _replace_chapter_by_locator_with_soup(data: bytes, chapter: Chapter, config: EpubConfig, *, bilingual: bool = False) -> tuple[bytes, list[str]] | None:
    soup = _soup(data)
    if soup is None:
        return None
    warnings: list[str] = []
    nodes = _soup_translatable_nodes(soup)
    for paragraph in chapter.paragraphs:
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


def _inspect_chapter_bytes(data: bytes, path: str, config: EpubConfig) -> dict:
    warnings: list[str] = []
    used_fallback_parser = False
    try:
        root = ET.fromstring(data)
        nodes = _translatable_elements(root)
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
        nodes = _soup_translatable_nodes(soup)
        risks = [_soup_node_risks(node) for node in nodes]
        texts = [_normalize_text(node.get_text(" ")) for node in nodes]
        link_count = len(soup.find_all("a"))
        ruby_count = len(soup.find_all("ruby"))
        footnote_link_count = len([node for node in soup.find_all("a") if _looks_like_footnote(str(node.attrs.get("href", "")))])
        inline_complexity = sum(len(node.find_all(True)) for node in nodes)
        image_alt_title_count = len([node for node in soup.find_all("img") if node.attrs.get("alt") or node.attrs.get("title")])
        warnings.append("章节使用增强解析器处理")
    risk_count = sum(1 for item in risks if item)
    if ruby_count and config.warn_on_ruby:
        warnings.append(f"包含 {ruby_count} 个 ruby 节点，导出后建议人工复核")
    return {
        "path": path,
        "paragraph_count": len([text for text in texts if text]),
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
        if chapter.paragraphs and chapter.paragraphs[0].translated and chapter.paragraphs[0].source == chapter.title:
            mapping[chapter.title] = chapter.paragraphs[0].translated
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
