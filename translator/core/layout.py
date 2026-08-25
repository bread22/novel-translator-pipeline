from __future__ import annotations

from pathlib import Path
import posixpath
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from typing import Any


CONTAINER_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NS = "http://www.idpf.org/2007/opf"
DC_NS = "http://purl.org/dc/elements/1.1/"
XHTML_NS = "http://www.w3.org/1999/xhtml"
XML_NS = "http://www.w3.org/XML/1998/namespace"

HORIZONTAL_CSS = """:root,
html,
body,
body * {
    writing-mode: horizontal-tb !important;
    -webkit-writing-mode: horizontal-tb !important;
    -epub-writing-mode: horizontal-tb !important;
    direction: ltr !important;
    text-orientation: mixed !important;
}

body * {
    text-combine-upright: none !important;
    -webkit-text-combine: none !important;
    -epub-text-combine: none !important;
}
"""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _zip_write_info(dst: zipfile.ZipFile, info: zipfile.ZipInfo, data: bytes, *, stored: bool = False) -> None:
    out = zipfile.ZipInfo(info.filename, date_time=info.date_time)
    out.comment = info.comment
    out.extra = info.extra
    out.internal_attr = info.internal_attr
    out.external_attr = info.external_attr
    out.create_system = info.create_system
    out.compress_type = zipfile.ZIP_STORED if stored else info.compress_type
    dst.writestr(out, data)


def _rootfile_path(src: zipfile.ZipFile) -> str:
    try:
        container = ET.fromstring(src.read("META-INF/container.xml"))
        rootfile = next(
            element for element in container.iter()
            if _local_name(element.tag) == "rootfile" and element.attrib.get("full-path")
        )
        return rootfile.attrib["full-path"]
    except (KeyError, ET.ParseError, StopIteration):
        opfs = [name for name in src.namelist() if name.casefold().endswith(".opf")]
        if not opfs:
            raise ValueError("EPUB 中未找到 OPF 文件")
        return opfs[0]


def _parse_opf(data: bytes) -> ET.Element:
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("OPF XML 无法解析，无法转换 EPUB 横排布局") from exc


def _href_path(opf_path: str, href: str) -> str:
    return posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), href.split("#", 1)[0]))


def _relative_href(source_path: str, target_path: str) -> str:
    value = posixpath.relpath(target_path, posixpath.dirname(source_path) or ".")
    return value.replace("\\", "/")


def _find_manifest(root: ET.Element) -> tuple[ET.Element, dict[str, ET.Element]]:
    manifest = next((element for element in root.iter() if _local_name(element.tag) == "manifest"), None)
    if manifest is None:
        raise ValueError("OPF 中未找到 manifest")
    items = {
        str(item.attrib.get("id", "")): item
        for item in manifest
        if _local_name(item.tag) == "item" and item.attrib.get("id")
    }
    return manifest, items


def _content_paths(root: ET.Element, opf_path: str) -> list[str]:
    _manifest, items = _find_manifest(root)
    spine = next((element for element in root.iter() if _local_name(element.tag) == "spine"), None)
    ids = [str(ref.attrib.get("idref", "")) for ref in (list(spine) if spine is not None else []) if _local_name(ref.tag) == "itemref"]
    paths: list[str] = []
    for item_id in ids:
        item = items.get(item_id)
        if item is None or "nav" in str(item.attrib.get("properties", "")).split():
            continue
        media_type = str(item.attrib.get("media-type", "")).casefold()
        if media_type in {"application/xhtml+xml", "text/html"}:
            paths.append(_href_path(opf_path, str(item.attrib.get("href", ""))))
    return paths


def _set_language(root: ET.Element, language: str) -> None:
    metadata = next((element for element in root.iter() if _local_name(element.tag) == "metadata"), None)
    if metadata is None:
        return
    language_element = next((element for element in metadata if _local_name(element.tag) == "language"), None)
    if language_element is None:
        language_element = ET.Element(f"{{{DC_NS}}}language")
        metadata.append(language_element)
    language_element.text = language
    language_element.set(f"{{{XML_NS}}}lang", language)


def _inject_metadata_to_opf(root: ET.Element, meta: dict[str, Any]) -> None:
    metadata_el = next((element for element in root.iter() if _local_name(element.tag) == "metadata"), None)
    if metadata_el is None:
        return

    title_zh = str(meta.get("title_zh", "")).strip()
    title_ja = str(meta.get("title_ja", "")).strip()
    author_zh = str(meta.get("author_zh", "")).strip()
    author_ja = str(meta.get("author_ja", "")).strip()
    description = str(meta.get("description", "")).strip()

    # 1. Main title and Japanese original title / subtitle
    if title_zh:
        title_el = next((element for element in metadata_el if _local_name(element.tag) == "title"), None)
        if title_el is None:
            title_el = ET.Element(f"{{{DC_NS}}}title")
            metadata_el.append(title_el)
        title_el.text = title_zh
        title_el.set("id", "title-main")

        # Subtitle / Japanese original title if different
        if title_ja and title_ja != title_zh:
            sub_el = next((element for element in metadata_el if _local_name(element.tag) == "title" and element != title_el), None)
            if sub_el is None:
                sub_el = ET.Element(f"{{{DC_NS}}}title")
                metadata_el.append(sub_el)
            sub_el.text = title_ja
            sub_el.set("id", "title-orig")

            # EPUB 3 subtitle refine
            sub_refine = next((el for el in metadata_el if _local_name(el.tag) == "meta" and el.attrib.get("refines") == "#title-orig"), None)
            if sub_refine is None:
                sub_refine = ET.Element(f"{{{OPF_NS}}}meta")
                metadata_el.append(sub_refine)
            sub_refine.set("refines", "#title-orig")
            sub_refine.set("property", "title-type")
            sub_refine.text = "subtitle"

            # Legacy original-title meta for older readers/Calibre
            orig_meta = next((el for el in metadata_el if _local_name(el.tag) == "meta" and el.attrib.get("name") == "original-title"), None)
            if orig_meta is None:
                orig_meta = ET.Element(f"{{{OPF_NS}}}meta")
                metadata_el.append(orig_meta)
            orig_meta.set("name", "original-title")
            orig_meta.set("content", title_ja)

        # EPUB 3 main title refine
        main_refine = next((el for el in metadata_el if _local_name(el.tag) == "meta" and el.attrib.get("refines") == "#title-main"), None)
        if main_refine is None:
            main_refine = ET.Element(f"{{{OPF_NS}}}meta")
            metadata_el.append(main_refine)
        main_refine.set("refines", "#title-main")
        main_refine.set("property", "title-type")
        main_refine.text = "main"

        # Calibre sort title
        sort_meta = next((el for el in metadata_el if _local_name(el.tag) == "meta" and el.attrib.get("name") == "calibre:title_sort"), None)
        if sort_meta is None:
            sort_meta = ET.Element(f"{{{OPF_NS}}}meta")
            metadata_el.append(sort_meta)
        sort_meta.set("name", "calibre:title_sort")
        sort_meta.set("content", title_zh)

    # 2. Author / Creator
    if author_zh:
        creator_el = next((element for element in metadata_el if _local_name(element.tag) == "creator"), None)
        if creator_el is None:
            creator_el = ET.Element(f"{{{DC_NS}}}creator")
            metadata_el.append(creator_el)
        creator_el.text = author_zh
        creator_el.set("id", "creator")
        if author_ja:
            creator_el.set(f"{{{OPF_NS}}}file-as", author_ja)

    # 3. Synopsis / Description
    if description:
        desc_el = next((element for element in metadata_el if _local_name(element.tag) == "description"), None)
        if desc_el is None:
            desc_el = ET.Element(f"{{{DC_NS}}}description")
            metadata_el.append(desc_el)
        desc_el.text = description

    # 4. Language & Contributor
    _set_language(root, "zh-CN")

    contributor_el = next((el for el in metadata_el if _local_name(el.tag) == "contributor" and el.attrib.get("id") == "translator"), None)
    if contributor_el is None:
        contributor_el = ET.Element(f"{{{DC_NS}}}contributor", {"id": "translator"})
        metadata_el.append(contributor_el)
    contributor_el.text = "Novel Translator Studio"


def _update_opf(root: ET.Element, opf_path: str, css_path: str, metadata: dict[str, Any] | None = None) -> None:
    ET.register_namespace("", OPF_NS)
    ET.register_namespace("dc", DC_NS)
    manifest, items = _find_manifest(root)
    css_href = _relative_href(opf_path, css_path)
    css_item = next(
        (item for item in items.values() if _href_path(opf_path, str(item.attrib.get("href", ""))) == css_path),
        None,
    )
    if css_item is None:
        css_id = "horizontal-zh-layout"
        used_ids = set(items)
        suffix = 2
        while css_id in used_ids:
            css_id = f"horizontal-zh-layout-{suffix}"
            suffix += 1
        css_item = ET.SubElement(manifest, f"{{{OPF_NS}}}item")
        css_item.set("id", css_id)
    css_item.set("href", css_href)
    css_item.set("media-type", "text/css")
    root_spine = next((element for element in root.iter() if _local_name(element.tag) == "spine"), None)
    if root_spine is not None:
        root_spine.set("page-progression-direction", "ltr")
    _set_language(root, "zh-CN")
    if metadata:
        _inject_metadata_to_opf(root, metadata)


def _inject_stylesheet(data: bytes, href: str) -> bytes:
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError("正文 XHTML XML 无法解析，无法注入横排 CSS") from exc
    root_ns = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else XHTML_NS
    namespace = root_ns or XHTML_NS
    ET.register_namespace("", namespace)
    head = next((element for element in root.iter() if _local_name(element.tag) == "head"), None)
    if head is None:
        head = ET.Element(f"{{{namespace}}}head")
        body = next((element for element in root if _local_name(element.tag) == "body"), None)
        if body is None:
            root.append(head)
        else:
            root.insert(list(root).index(body), head)
    existing = {
        str(element.attrib.get("href", ""))
        for element in head
        if _local_name(element.tag) == "link"
    }
    if href not in existing:
        link = ET.Element(f"{{{namespace}}}link", {"rel": "stylesheet", "type": "text/css", "href": href})
        head.append(link)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _is_cover_document(data: bytes) -> bool:
    text = data.decode("utf-8", errors="ignore").casefold()
    return "calibre:cover" in text or 'name="cover"' in text and 'content="true"' in text


def apply_horizontal_layout(epub: Path, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add a horizontal Chinese layout layer and inject rich metadata without changing translated text."""
    epub = epub.expanduser().resolve()
    if not epub.exists():
        raise FileNotFoundError(epub)
    temporary = Path(tempfile.mkstemp(prefix=f".{epub.name}.", suffix=".tmp", dir=epub.parent)[1])
    try:
        with zipfile.ZipFile(epub, "r") as src:
            opf_path = _rootfile_path(src)
            root = _parse_opf(src.read(opf_path))
            opf_dir = posixpath.dirname(opf_path)
            css_path = posixpath.join(opf_dir, "Styles", "horizontal-zh.css")
            content_paths = _content_paths(root, opf_path)
            _update_opf(root, opf_path, css_path, metadata=metadata)
            css_href_by_content = {
                path: _relative_href(path, css_path)
                for path in content_paths
            }
            updates: dict[str, bytes] = {
                opf_path: ET.tostring(root, encoding="utf-8", xml_declaration=True),
                css_path: HORIZONTAL_CSS.encode("utf-8"),
            }
            for path, href in css_href_by_content.items():
                data = src.read(path)
                if not _is_cover_document(data):
                    updates[path] = _inject_stylesheet(data, href)
            with zipfile.ZipFile(temporary, "w") as dst:
                for info in src.infolist():
                    data = updates.get(info.filename, src.read(info.filename))
                    _zip_write_info(dst, info, data, stored=info.filename == "mimetype")
                if css_path not in src.namelist():
                    info = zipfile.ZipInfo(css_path)
                    info.compress_type = zipfile.ZIP_DEFLATED
                    _zip_write_info(dst, info, updates[css_path])
        temporary.replace(epub)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": "ok",
        "layout": "horizontal",
        "css": css_path,
        "content_documents": len(content_paths),
        "spine_direction": "ltr",
        "language": "zh-CN",
        "metadata_injected": bool(metadata),
    }


def inject_epub_metadata(epub: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    """Inject rich metadata into an EPUB without applying layout transformation."""
    epub = epub.expanduser().resolve()
    if not epub.exists():
        raise FileNotFoundError(epub)
    temporary = Path(tempfile.mkstemp(prefix=f".{epub.name}.", suffix=".tmp", dir=epub.parent)[1])
    try:
        with zipfile.ZipFile(epub, "r") as src:
            try:
                opf_path = _rootfile_path(src)
                root = _parse_opf(src.read(opf_path))
            except (ValueError, KeyError, ET.ParseError):
                return {"status": "ok", "metadata_injected": False, "metadata": metadata}
            ET.register_namespace("", OPF_NS)
            ET.register_namespace("dc", DC_NS)
            _inject_metadata_to_opf(root, metadata)
            updates: dict[str, bytes] = {
                opf_path: ET.tostring(root, encoding="utf-8", xml_declaration=True),
            }
            with zipfile.ZipFile(temporary, "w") as dst:
                for info in src.infolist():
                    data = updates.get(info.filename, src.read(info.filename))
                    _zip_write_info(dst, info, data, stored=info.filename == "mimetype")
        temporary.replace(epub)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": "ok",
        "metadata_injected": True,
        "metadata": metadata,
    }
