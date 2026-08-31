from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from pathlib import Path
import json
import re

from app.context import load_context
from app.models import Book, book_dir
from app.placeholders import extract_placeholders
from app.quality import quality_report
from app.terminology import Term, extract_term_candidates


DIALOGUE_PATTERN = re.compile(r"[「『“\"]|(^|\n)\s*[-—]")


def analysis_path(root_books_dir: Path, book_id: str) -> Path:
    return book_dir(root_books_dir, book_id) / "analysis.json"


def analyze_book(root_books_dir: Path, book: Book, terms: list[Term]) -> dict:
    paragraphs = book.paragraphs
    duplicate_counter = Counter(paragraph.source for paragraph in paragraphs)
    duplicate_groups = [
        {"source": source, "count": count}
        for source, count in duplicate_counter.most_common(50)
        if count > 1
    ]
    candidates = extract_term_candidates(book)
    chapter_lengths = [
        {
            "id": chapter.id,
            "title": chapter.title,
            "paragraphs": len(chapter.paragraphs),
            "chars": sum(len(paragraph.source) for paragraph in chapter.paragraphs),
        }
        for chapter in book.chapters
    ]
    dialogue_count = sum(1 for paragraph in paragraphs if DIALOGUE_PATTERN.search(paragraph.source))
    placeholder_count = sum(len(extract_placeholders(paragraph.source)) for paragraph in paragraphs)
    epub_risk_count = sum(1 for paragraph in paragraphs if paragraph.metadata.get("epub", {}).get("risks"))
    filled_terms = [term for term in terms if term.source and term.target]
    payload = {
        "book": book.id,
        "title": book.title,
        "source_type": book.source_type,
        "chapter_lengths": chapter_lengths,
        "paragraphs": len(paragraphs),
        "chars": sum(len(paragraph.source) for paragraph in paragraphs),
        "dialogue_ratio": round(dialogue_count / len(paragraphs), 4) if paragraphs else 0,
        "duplicate_groups": duplicate_groups,
        "duplicate_group_count": len(duplicate_groups),
        "placeholder_count": placeholder_count,
        "epub_risk_count": epub_risk_count,
        "terminology": {
            "candidate_count": len(candidates),
            "current_count": len(terms),
            "filled_count": len(filled_terms),
            "density": round(len(candidates) / len(paragraphs), 4) if paragraphs else 0,
            "top_candidates": [asdict(term) for term in candidates[:50]],
        },
        "name_like_terms": [asdict(term) for term in candidates[:30]],
    }
    path = analysis_path(root_books_dir, book.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "ok",
        "warnings": [],
        "summary": {
            "book": book.id,
            "chapters": len(book.chapters),
            "paragraphs": len(paragraphs),
            "dialogue_ratio": payload["dialogue_ratio"],
            "duplicate_group_count": len(duplicate_groups),
            "epub_risk_count": epub_risk_count,
            "output": str(path),
        },
        "details": payload,
    }


def load_analysis(root_books_dir: Path, book_id: str) -> dict:
    path = analysis_path(root_books_dir, book_id)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def translation_plan(root_books_dir: Path, book: Book, terms: list[Term], quality_config) -> dict:
    analysis = load_analysis(root_books_dir, book.id)
    if not analysis:
        analysis = analyze_book(root_books_dir, book, terms)["details"]
    context = load_context(root_books_dir, book.id)
    missing_context = [chapter.id for chapter in book.chapters if chapter.id not in context.get("chapters", {})]
    quality = quality_report(book, quality_config, terms)
    actions = []
    warnings = []
    filled_terms = sum(1 for term in terms if term.target)
    if filled_terms == 0 and analysis.get("terminology", {}).get("candidate_count", 0):
        actions.append("先导出并导入术语表，至少确认主要人名、地名和组织名。")
        warnings.append("术语表尚未填写，直接翻译可能导致译名漂移。")
    if missing_context:
        actions.append("先运行 summarize-context，再用 context-status 验收章节摘要。")
    if analysis.get("epub_risk_count", 0):
        actions.append("EPUB 有复杂标记风险，导出前生成 EPUB 风险报告并人工抽查。")
    if analysis.get("duplicate_group_count", 0):
        actions.append("存在重复原文，翻译记忆可降低成本，EPUB 导出需依赖节点定位。")
    if quality["summary"].get("untranslated", 0):
        actions.append("先小批量 translate --max-batches 1，确认质量后再全量。")
    actions.append("翻译后运行 run-report、quality-report、review-translations --mode risk 和 validate-export。")
    recommended_batch = 2500 if analysis.get("dialogue_ratio", 0) > 0.35 else 4000
    if analysis.get("epub_risk_count", 0):
        recommended_batch = min(recommended_batch, 3000)
    return {
        "status": "warning" if warnings else "ok",
        "warnings": warnings,
        "summary": {
            "book": book.id,
            "recommended_batch_max_chars": recommended_batch,
            "needs_terminology": filled_terms == 0,
            "needs_context": bool(missing_context),
            "needs_review": True,
            "epub_risk_count": analysis.get("epub_risk_count", 0),
        },
        "details": {
            "actions": actions,
            "missing_context": missing_context,
            "analysis": analysis,
        },
    }
