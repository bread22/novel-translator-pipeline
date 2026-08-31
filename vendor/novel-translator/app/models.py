from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json
import re
import shutil


@dataclass
class Paragraph:
    id: str
    chapter_id: str
    index: int
    source: str
    translated: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chapter:
    id: str
    title: str
    index: int
    source_path: str = ""
    paragraphs: list[Paragraph] = field(default_factory=list)


@dataclass
class Book:
    id: str
    title: str
    source_type: str
    source_file: str
    chapters: list[Chapter] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def paragraphs(self) -> list[Paragraph]:
        return [paragraph for chapter in self.chapters for paragraph in chapter.paragraphs]


def slugify(value: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "-", value.strip()).strip("-._")
    return slug or "book"


def book_dir(root_books_dir: Path, book_id: str) -> Path:
    return root_books_dir / book_id


def save_book(root_books_dir: Path, book: Book, source_path: Path) -> Path:
    target_dir = book_dir(root_books_dir, book.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    source_copy = target_dir / f"source{source_path.suffix.lower()}"
    if source_path.resolve() != source_copy.resolve():
        shutil.copy2(source_path, source_copy)
    book.source_file = str(source_copy)
    (target_dir / "manifest.json").write_text(
        json.dumps(asdict(book), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target_dir


def load_book(root_books_dir: Path, book_id: str) -> Book:
    manifest = book_dir(root_books_dir, book_id) / "manifest.json"
    if not manifest.exists():
        raise FileNotFoundError(f"未找到书籍：{book_id}")
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    return book_from_dict(raw)


def book_from_dict(raw: dict[str, Any]) -> Book:
    chapters: list[Chapter] = []
    for chapter_raw in raw.get("chapters", []):
        paragraphs = [
            Paragraph(
                id=str(item["id"]),
                chapter_id=str(item["chapter_id"]),
                index=int(item["index"]),
                source=str(item["source"]),
                translated=str(item.get("translated", "")),
                metadata=dict(item.get("metadata", {})),
            )
            for item in chapter_raw.get("paragraphs", [])
        ]
        chapters.append(
            Chapter(
                id=str(chapter_raw["id"]),
                title=str(chapter_raw.get("title", "")),
                index=int(chapter_raw["index"]),
                source_path=str(chapter_raw.get("source_path", "")),
                paragraphs=paragraphs,
            )
        )
    return Book(
        id=str(raw["id"]),
        title=str(raw.get("title", raw["id"])),
        source_type=str(raw["source_type"]),
        source_file=str(raw.get("source_file", "")),
        chapters=chapters,
        metadata=dict(raw.get("metadata", {})),
    )


def persist_book(root_books_dir: Path, book: Book) -> None:
    target_dir = book_dir(root_books_dir, book.id)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "manifest.json").write_text(
        json.dumps(asdict(book), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
