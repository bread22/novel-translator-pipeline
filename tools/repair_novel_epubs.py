from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import asdict
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "novel-translator"
BOOKS = VENDOR / "data" / "books"
OPS = "http://www.idpf.org/2007/ops"
NCX = "http://www.daisy.org/z3986/2005/ncx/"
XHTML = "http://www.w3.org/1999/xhtml"
ET.register_namespace("", XHTML)
ET.register_namespace("epub", OPS)


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def is_chapter_label(label: str) -> bool:
    return bool(re.match(r"^\s*(?:第\s*[0-9０-９一二三四五六七八九十百千]+\s*[章話節部]|(?:Lesson|Chapter|Episode|Act|Scene|Case|Track|Part|Stage)\s*[0-9０-９一二三四五六七八九十百千]+)", label, re.IGNORECASE))


def first_chapter_id(data: bytes) -> str:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return ""
    ids = [element.get("id", "") for element in root.iter() if element.get("id", "").startswith("CR")]
    if ids:
        return ids[0]
    for element in root.iter():
        text = " ".join("".join(element.itertext()).split())
        if is_chapter_label(text) and element.get("id"):
            return element.get("id", "")
    return ""
def transform_br_to_paragraphs(root: ET.Element) -> bool:
    body = next((element for element in root.iter() if local(element.tag) == "body"), None)
    if body is None:
        return False
    head = next((element for element in root.iter() if local(element.tag) == "head"), None)

    has_p = any(local(el.tag) == "p" for el in body.iter())
    br_count = sum(1 for el in body.iter() if local(el.tag) == "br")
    if has_p or br_count < 3:
        return False

    def collect_tokens(container):
        tokens = []
        if container.text and container.text.strip():
            tokens.append(("text", container.text.strip()))
        for child in list(container):
            tag = local(child.tag)
            if tag == "br":
                tokens.append(("break",))
            elif tag in {"span", "div"} and any(local(c.tag) == "br" for c in child.iter()):
                tokens.extend(collect_tokens(child))
            elif tag == "div" and not any(child.itertext()) and not list(child):
                tokens.append(("break",))
            else:
                tokens.append(("elem", child))
            if child.tail and child.tail.strip():
                tokens.append(("text", child.tail.strip()))
        return tokens

    tokens = collect_tokens(body)
    paragraphs = []
    current_para = []
    for tok in tokens:
        if tok[0] == "break":
            if current_para:
                paragraphs.append(current_para)
                current_para = []
        else:
            current_para.append(tok)
    if current_para:
        paragraphs.append(current_para)

    new_p_elems = []
    for para_tokens in paragraphs:
        p = ET.Element(f"{{{XHTML}}}p")
        last_elem = None
        for tok in para_tokens:
            if tok[0] == "text":
                if last_elem is None:
                    p.text = (p.text or "") + tok[1]
                else:
                    last_elem.tail = (last_elem.tail or "") + tok[1]
            elif tok[0] == "elem":
                child = tok[1]
                child.tail = None
                p.append(child)
                last_elem = child
        if ("".join(p.itertext())).strip():
            new_p_elems.append(p)
    if not new_p_elems:
        return False
    body.clear()
    for p in new_p_elems:
        body.append(p)

    all_text = " ".join("".join(p.itertext()).split())
    if len(re.findall(r"第[一二三四五六七八九十百千0-9０-９]+章", all_text)) >= 3 and len(all_text) < 5000:
        body.set(f"{{{OPS}}}type", "toc")
        if head is not None:
            title_el = next((el for el in head.iter() if local(el.tag) == "title"), None)
            if title_el is None:
                title_el = ET.Element(f"{{{XHTML}}}title")
                head.insert(0, title_el)
            title_el.text = "目录"
    elif head is not None:
        title_el = next((el for el in head.iter() if local(el.tag) == "title"), None)
        if title_el is None or not (title_el.text or "").strip():
            first_text = ("".join(new_p_elems[0].itertext())).strip()
            if first_text and len(first_text) < 100:
                if title_el is None:
                    title_el = ET.Element(f"{{{XHTML}}}title")
                    head.insert(0, title_el)
                title_el.text = first_text

    return True



def repair_epub(path: Path) -> None:
    book_name = path.parent.name
    backup = path.with_name(path.name + ".original")
    if not backup.exists():
        shutil.copy2(path, backup)

    with zipfile.ZipFile(path) as source:
        files = {name: source.read(name) for name in source.namelist()}

    opf_name = next(name for name in files if name.endswith(".opf"))
    opf = ET.fromstring(files[opf_name])
    base = Path(opf_name).parent.as_posix()

    manifest: dict[str, str] = {}
    for item in opf.iter():
        if local(item.tag) == "item" and item.get("id") and item.get("href"):
            manifest[item.get("id", "")] = str(Path(base, item.get("href", "")).as_posix())

    # Restore missing chapter anchors in either NCX or EPUB3 nav.xhtml.
    # These books often have the chapter boundary in the navigation file but
    # omit the fragment from every first chapter target.
    for nav_name in [name for name in files if name.endswith((".ncx", "nav.xhtml", "navigation-documents.xhtml"))]:
        nav = ET.fromstring(files[nav_name])
        for point in nav.iter():
            tag = local(point.tag)
            if tag == "navPoint":
                label = next((child.text or "" for child in point.iter() if local(child.tag) == "text"), "")
                link = next((child for child in point.iter() if local(child.tag) == "content"), None)
                attr = "src"
            elif tag == "a":
                label = " ".join("".join(point.itertext()).split())
                link = point
                attr = "href"
            else:
                continue
            if link is None or not is_chapter_label(label):
                continue
            src = link.get(attr, "")
            target, _, fragment = src.partition("#")
            if fragment:
                continue
            target_path = str(Path(Path(nav_name).parent, target).as_posix())
            data = files.get(target_path)
            marker_id = first_chapter_id(data) if data else ""
            if marker_id:
                link.set(attr, f"{target}#{marker_id}")
        if nav_name.endswith(".ncx"):
            ET.register_namespace("", NCX)
        files[nav_name] = ET.tostring(nav, encoding="utf-8", xml_declaration=True)
        ET.register_namespace("", XHTML)

    # Make title, contents, front matter, and colophon semantically explicit.
    # This prevents the contents page's visible chapter list from being
    # interpreted as actual chapter boundaries by EPUB consumers.
    chapter_target_paths: set[str] = set()
    toc_target_paths: set[str] = set()
    for nav_name, data in list(files.items()):
        if not nav_name.endswith((".ncx", "nav.xhtml", "navigation-documents.xhtml")):
            continue
        try:
            nav_root = ET.fromstring(data)
        except ET.ParseError:
            continue
        for element in nav_root.iter():
            if local(element.tag) == "content":
                src = element.get("src", "")
                label = ""
                parent_label = next((" ".join("".join(parent.itertext()).split()) for parent in nav_root.iter() if element in list(parent)), "")
            elif local(element.tag) == "a":
                src = element.get("href", "")
                label = " ".join("".join(element.itertext()).split())
                parent_label = ""
            else:
                continue
            target_path = str(Path(Path(nav_name).parent, src.partition("#")[0]).as_posix())
            if is_chapter_label(label) or is_chapter_label(parent_label):
                chapter_target_paths.add(target_path)
            elif any(token in (label + " " + parent_label) for token in ("目次", "目录", "Contents", "Table of")):
                toc_target_paths.add(target_path)

    for name, data in list(files.items()):
        if not name.lower().endswith((".html", ".xhtml")):
            continue
        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue
        transform_br_to_paragraphs(root)
        role = ""
        text = " ".join("".join(root.itertext()).split())
        lower_name = name.casefold()
        body = next((element for element in root.iter() if local(element.tag) == "body"), root)
        body_class = str(body.get("class", "")).casefold()
        for element in root.iter():
            if element.get("id", "") == "CR0000":
                marker_text = " ".join("".join(element.itertext()).split())
                if marker_text:
                    for title_element in root.iter():
                        if local(title_element.tag) == "title" and (title_element.text or "").strip().casefold() in {"unknown", "不明"}:
                            title_element.text = marker_text
                break
        # This title has a title page followed by a separate contents page;
        # both were otherwise exposed as ordinary chapter files.
        if "トー-クン三部作" in book_name:
            part_match = re.search(r"/text(\d{5})\.html$", name)
            part = int(part_match.group(1)) if part_match else -1
            if part in {0, 1, 2, 3, 8, 13}:
                role = "cover"
            elif part in {4, 9, 14}:
                role = "toc"
            elif part == 18:
                role = "colophon"
            elif part in {5, 6, 7, 10, 11, 12, 15, 16, 17}:
                role = "chapter"
        elif "女教師-魔淫の教壇" in book_name:
            part_match = re.search(r"/part(\d{4})\.html$", name)
            part = int(part_match.group(1)) if part_match else -1
            if part in {0, 1, 2, 8, 13}:
                role = "cover"
            elif 3 <= part <= 7 or 9 <= part <= 12 or part == 14:
                role = "chapter"
            elif part == 15:
                role = "colophon"
        elif "姦禁性裁" in book_name:
            part_match = re.search(r"/p-(\d{3})\.xhtml$", name)
            part = int(part_match.group(1)) if part_match else -1
            if part in {5, 13, 22}:
                role = "cover"
            elif 6 <= part <= 12 or 14 <= part <= 21:
                role = "chapter"
        elif "みだらな肉筆" in book_name:
            part_match = re.search(r"/p-(\d{3})\.xhtml$", name)
            part = int(part_match.group(1)) if part_match else -1
            if 1 <= part <= 9:
                role = "chapter"
        elif "奴隷女教師-嬲る" in book_name and name.endswith("/part0001.html"):
            role = "cover"
        elif "奴隷女教師-嬲る" in book_name and name.endswith("/part0002.html"):
            role = "toc"
        elif "奴隷女教師-嬲る" in book_name and any(name.endswith(f"/part{i:04d}.html") for i in (0, 15, 16, 17)):
            role = "colophon"
        elif "奴隷女教師-嬲る" in book_name and 3 <= next((int(m.group(1)) for m in [re.search(r"/part(\d{4})\.html$", name)] if m), -1) <= 14:
            role = "chapter"
        elif any(token in lower_name for token in ("titlepage", "cover_page", "p-cover")) or any(token in body_class for token in ("p-titlepage", "p-cover", "p-tobira", "p-fmatter")) or name.endswith("part0000.html"):
            role = "cover"
        elif name in toc_target_paths or "toc" in lower_name or "p-toc" in body_class or (name not in chapter_target_paths and len(re.findall(r"第[一二三四五六七八九十百千0-9０-９]+章", text)) >= 3 and len(text) < 5000):
            role = "toc"
        elif "colophon" in lower_name or "p-credit" in body_class or "p-colophon" in body_class or "奥付" in text or ("発行" in text and len(text) < 2000):
            role = "colophon"
        elif "fmatter" in lower_name or "主な登場人物" in text or "ストーリー" in text and not is_chapter_label(text):
            role = "frontmatter"
        if role == "chapter":
            title_element = next((element for element in root.iter() if local(element.tag) == "title"), None)
            first_paragraph = next((element for element in root.iter() if local(element.tag) == "p" and " ".join("".join(element.itertext()).split())), None)
            candidate = " ".join("".join(first_paragraph.itertext()).split()) if first_paragraph is not None else ""
            if title_element is not None and candidate and len(candidate) <= 200:
                title_element.text = candidate
        if role:
            body.set(f"{{{OPS}}}type", role)
        files[name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

    temporary = Path(tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)[1])
    try:
        with zipfile.ZipFile(temporary, "w") as target:
            for name, data in files.items():
                info = zipfile.ZipInfo(name)
                info.compress_type = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
                target.writestr(info, data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def refresh_manifest(book_dir: Path) -> tuple[int, int]:
    sys.path.insert(0, str(VENDOR))
    from app.book_io import load_source_book

    manifest_path = book_dir / "manifest.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8"))
    old_translations: dict[str, list[str]] = {}
    for chapter in old.get("chapters", []):
        for paragraph in chapter.get("paragraphs", []):
            old_translations.setdefault(str(paragraph.get("source", "")), []).append(str(paragraph.get("translated", "")))

    book = load_source_book(book_dir / "source.epub")
    for chapter in book.chapters:
        for paragraph in chapter.paragraphs:
            candidates = old_translations.get(paragraph.source, [])
            if candidates:
                paragraph.translated = candidates.pop(0)
    manifest_path.write_text(json.dumps(asdict(book), ensure_ascii=False, indent=2), encoding="utf-8")
    return len(book.chapters), len(book.paragraphs)


def main() -> None:
    prefixes = (
        "新-凌辱女子学園[123]-",
        "姦禁性裁-",
        "トー-クン三部作-",
        "みだらな肉筆-",
        "女教師-魔淫の教壇-",
        "美熟女の休日-",
        "彼女-の美母-",
    )
    if len(sys.argv) > 1:
        targets = [
            BOOKS / arg if (BOOKS / arg).is_dir() else Path(arg)
            for arg in sys.argv[1:]
        ]
    else:
        targets = [p for p in BOOKS.iterdir() if p.is_dir() and p.name.startswith(prefixes)]

    for book_dir in sorted(targets):
        if not book_dir.is_dir() or not (book_dir / "source.epub").exists():
            continue
        repair_epub(book_dir / "source.epub")
        chapters, paragraphs = refresh_manifest(book_dir)
        print(book_dir.name, chapters, paragraphs)


if __name__ == "__main__":
    main()
