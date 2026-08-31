from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import re

from app.models import Book, book_dir
from app.persona import terminology_address_warnings


@dataclass
class Term:
    source: str
    target: str = ""
    category: str = "other"
    note: str = ""
    occurrences: int = 0
    sample_ids: list[str] | None = None


ENGLISH_NAME_PATTERN = re.compile(r"\b[A-Z][a-zA-Z]{2,}(?:[ '\-][A-Z][a-zA-Z]{2,}){0,2}\b")
KATAKANA_PATTERN = re.compile(r"[\u30A1-\u30FA\u30FC]{2,}")
MARKED_NAME_PATTERN = re.compile(r"[「『“\"]([^」』”\"]{1,24})[」』”\"]")


def terms_path(root_books_dir: Path, book_id: str) -> Path:
    return book_dir(root_books_dir, book_id) / "terms.json"


def load_terms(root_books_dir: Path, book_id: str) -> list[Term]:
    path = terms_path(root_books_dir, book_id)
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get("terms", raw if isinstance(raw, list) else [])
    return [term_from_dict(item) for item in items if isinstance(item, dict)]


def save_terms(root_books_dir: Path, book_id: str, terms: list[Term]) -> Path:
    path = terms_path(root_books_dir, book_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"terms": [asdict(term) for term in terms]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def term_from_dict(raw: dict) -> Term:
    sample_ids = raw.get("sample_ids")
    if sample_ids is not None and not isinstance(sample_ids, list):
        sample_ids = []
    return Term(
        source=str(raw.get("source", "")).strip(),
        target=str(raw.get("target", "")).strip(),
        category=str(raw.get("category", "other")).strip() or "other",
        note=str(raw.get("note", "")).strip(),
        occurrences=int(raw.get("occurrences", 0) or 0),
        sample_ids=[str(item) for item in sample_ids] if sample_ids is not None else [],
    )


def extract_term_candidates(book: Book, *, minimum_occurrences: int = 2) -> list[Term]:
    counter: Counter[str] = Counter()
    samples: dict[str, list[str]] = defaultdict(list)
    for paragraph in book.paragraphs:
        for candidate in paragraph_candidates(paragraph.source):
            counter[candidate] += 1
            if len(samples[candidate]) < 5:
                samples[candidate].append(paragraph.id)
    terms = [
        Term(
            source=source,
            category=guess_category(source),
            occurrences=count,
            sample_ids=samples[source],
        )
        for source, count in counter.items()
        if count >= minimum_occurrences
    ]
    return sorted(terms, key=lambda item: (-item.occurrences, item.source.casefold()))


def paragraph_candidates(text: str) -> set[str]:
    candidates: set[str] = set()
    candidates.update(match.group(0).strip() for match in ENGLISH_NAME_PATTERN.finditer(text))
    candidates.update(match.group(0).strip() for match in KATAKANA_PATTERN.finditer(text))
    for match in MARKED_NAME_PATTERN.finditer(text):
        value = match.group(1).strip()
        if 1 < len(value) <= 24 and not re.search(r"[。！？!?，,；;：:]", value):
            candidates.add(value)
    return {item for item in candidates if len(item) >= 2}


def guess_category(source: str) -> str:
    if KATAKANA_PATTERN.fullmatch(source) or ENGLISH_NAME_PATTERN.fullmatch(source):
        return "name"
    return "other"


def export_terminology_workspace(book: Book, output_dir: Path, existing_terms: list[Term]) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    terminology_dir = output_dir / "terminology"
    contexts_dir = terminology_dir / "contexts"
    contexts_dir.mkdir(parents=True, exist_ok=True)

    candidates = merge_existing_terms(extract_term_candidates(book), existing_terms)
    glossary_path = terminology_dir / "glossary.json"
    glossary_path.write_text(
        json.dumps({"terms": [asdict(term) for term in candidates]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    context_records = []
    paragraph_by_id = {paragraph.id: paragraph for paragraph in book.paragraphs}
    for term in candidates:
        samples = []
        for paragraph_id in term.sample_ids or []:
            paragraph = paragraph_by_id.get(paragraph_id)
            if paragraph is not None:
                samples.append(
                    {
                        "paragraph_id": paragraph.id,
                        "chapter_id": paragraph.chapter_id,
                        "source": paragraph.source,
                    }
                )
        context_records.append({"source": term.source, "samples": samples})
    contexts_path = contexts_dir / "term-contexts.json"
    contexts_path.write_text(json.dumps(context_records, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = {
        "book": book.id,
        "title": book.title,
        "workflow": "terminology",
        "files": {
            "glossary": str(glossary_path),
            "contexts": str(contexts_path),
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "manifest": str(manifest_path),
        "glossary": str(glossary_path),
        "contexts": str(contexts_path),
        "candidate_count": len(candidates),
        "filled_count": sum(1 for term in candidates if term.target),
    }


def merge_existing_terms(candidates: list[Term], existing_terms: list[Term]) -> list[Term]:
    existing_by_source = {term.source: term for term in existing_terms if term.source}
    merged: list[Term] = []
    seen: set[str] = set()
    for candidate in candidates:
        existing = existing_by_source.get(candidate.source)
        if existing is not None:
            candidate.target = existing.target
            candidate.category = existing.category
            candidate.note = existing.note
        merged.append(candidate)
        seen.add(candidate.source)
    for existing in existing_terms:
        if existing.source and existing.source not in seen:
            merged.append(existing)
    return merged


def validate_terms(terms: list[Term]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    by_source: dict[str, list[Term]] = defaultdict(list)
    by_target: dict[str, list[Term]] = defaultdict(list)
    for term in terms:
        if not term.source:
            errors.append("存在空 source 术语")
            continue
        by_source[term.source].append(term)
        if not term.target:
            warnings.append(f"术语 {term.source} 尚未填写 target")
        else:
            by_target[term.target].append(term)
    for source, items in by_source.items():
        targets = {item.target for item in items}
        if len(items) > 1 and len(targets) > 1:
            errors.append(f"术语 {source} 存在多个译名：{', '.join(sorted(targets))}")
        elif len(items) > 1:
            warnings.append(f"术语 {source} 重复出现")
    for target, items in by_target.items():
        sources = {item.source for item in items}
        if target and len(sources) > 1:
            warnings.append(f"译名 {target} 对应多个原文：{', '.join(sorted(sources))}")
    warnings.extend(terminology_address_warnings(terms))
    return errors, warnings


def relevant_terms_for_text(terms: list[Term], text: str) -> list[Term]:
    return [term for term in terms if term.source and term.target and term.source in text]

