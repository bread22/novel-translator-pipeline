from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import json

from app.config import AppConfig, ReviewConfig
from app.models import Book, Paragraph, book_dir, persist_book
from app.placeholders import placeholder_mismatches
from app.quality import quality_report
from app.terminology import Term


def reviews_dir(root_books_dir: Path, book_id: str) -> Path:
    return book_dir(root_books_dir, book_id) / "reviews"


def new_review_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]


def review_path(root_books_dir: Path, book_id: str, review_id: str) -> Path:
    return reviews_dir(root_books_dir, book_id) / f"{review_id}.json"


def review_translations(config: AppConfig, book: Book, terms: list[Term], mode: str | None = None) -> dict:
    selected_mode = mode or config.review.mode
    quality = quality_report(book, config.quality, terms)
    paragraphs = _select_review_paragraphs(book, quality, selected_mode, config.review)
    paragraphs = paragraphs[: config.review.max_items_per_run]
    review_id = new_review_id()
    items = [_review_item(paragraph, book, quality, terms) for paragraph in paragraphs]
    warnings = []
    if items and config.llm.api_key:
        try:
            items = _llm_review_items(config, items)
        except Exception as error:
            warnings.append(f"LLM 审校失败，已保留规则审校结果：{error}")
    payload = {
        "book": book.id,
        "review_id": review_id,
        "mode": selected_mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    path = review_path(config.books_dir, book.id, review_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "status": "ok" if not items else "warning",
        "warnings": ([f"审校发现 {len(items)} 个需要复核的段落"] if items else []) + warnings,
        "summary": {"book": book.id, "review_id": review_id, "mode": selected_mode, "items": len(items), "output": str(path)},
        "details": payload,
    }


def apply_review_fixes(root_books_dir: Path, book: Book, input_path: Path) -> dict:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    items = raw.get("items", raw if isinstance(raw, list) else [])
    by_id = {paragraph.id: paragraph for paragraph in book.paragraphs}
    errors = []
    applied = 0
    for item in items:
        if not isinstance(item, dict) or not item.get("approved_translation"):
            continue
        paragraph_id = str(item.get("id", ""))
        translated = str(item.get("approved_translation", "")).strip()
        paragraph = by_id.get(paragraph_id)
        if paragraph is None:
            errors.append({"code": "review_fix_invalid", "message": f"未知段落 ID：{paragraph_id}"})
            continue
        if not translated:
            errors.append({"code": "review_fix_invalid", "message": f"{paragraph_id} 的 approved_translation 为空"})
            continue
        original = paragraph.translated
        paragraph.translated = translated
        missing = placeholder_mismatches(paragraph)
        if missing:
            paragraph.translated = original
            errors.append({"code": "review_fix_invalid", "message": f"{paragraph_id} 缺少占位符"})
            continue
        applied += 1
    if errors:
        return {"status": "error", "errors": errors, "warnings": [], "summary": {"book": book.id, "applied": 0}, "details": {}}
    if applied:
        persist_book(root_books_dir, book)
    return {"status": "ok", "warnings": [], "summary": {"book": book.id, "input": str(input_path), "applied": applied}, "details": {}}


def export_review_report(root_books_dir: Path, book_id: str, review_id: str, output: Path) -> dict:
    path = review_path(root_books_dir, book_id, review_id)
    if not path.exists():
        raise FileNotFoundError(f"未找到审校报告：{review_id}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    lines = [f"# Review Report: {book_id}", "", f"- Review ID: `{review_id}`", f"- Mode: `{raw.get('mode', '')}`", f"- Items: {len(raw.get('items', []))}", ""]
    for item in raw.get("items", []):
        lines.extend(
            [
                f"## {item.get('id', '')} - {item.get('severity', '')}",
                "",
                f"- Chapter: {item.get('chapter_title', '')}",
                f"- Issues: {', '.join(item.get('issues', []))}",
                "",
                "**Source**",
                "",
                str(item.get("source", "")),
                "",
                "**Translated**",
                "",
                str(item.get("translated", "")),
                "",
                "**Suggestion**",
                "",
                str(item.get("suggestion", "")),
                "",
            ]
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    return {"status": "ok", "warnings": [], "summary": {"book": book_id, "review_id": review_id, "output": str(output)}, "details": {}}


def _select_review_paragraphs(book: Book, quality: dict, mode: str, config: ReviewConfig) -> list[Paragraph]:
    translated = [paragraph for paragraph in book.paragraphs if paragraph.translated.strip()]
    if mode == "all":
        return translated
    if mode == "sample":
        selected = []
        step = max(1, int(1 / max(config.sample_ratio, 0.01)))
        for chapter in book.chapters:
            chapter_translated = [paragraph for paragraph in chapter.paragraphs if paragraph.translated.strip()]
            selected.extend(chapter_translated[::step] or chapter_translated[:1])
        return selected
    ids = set()
    for key in ("source_residual", "terminology_mismatch", "placeholder_mismatch", "epub_markup_risk", "style_inconsistency", "dialogue_punctuation", "over_literal_translation", "person_address_issue", "review_required"):
        for item in quality.get("details", {}).get(key, []):
            if isinstance(item, str):
                ids.add(item)
            elif isinstance(item, dict):
                ids.add(str(item.get("id", "")))
    return [paragraph for paragraph in translated if paragraph.id in ids]


def _review_item(paragraph: Paragraph, book: Book, quality: dict, terms: list[Term]) -> dict:
    issues = _issues_for_paragraph(paragraph.id, quality)
    if not issues:
        issues = ["manual_review"]
    chapter = next((item for item in book.chapters if item.id == paragraph.chapter_id), None)
    severity = "high" if {"placeholder_mismatch", "terminology_mismatch"} & set(issues) else "medium"
    suggestion = _suggestion_for_issues(issues)
    return {
        "id": paragraph.id,
        "chapter_title": chapter.title if chapter else "",
        "source": paragraph.source,
        "translated": paragraph.translated,
        "issues": issues,
        "suggestion": suggestion,
        "approved_translation": "",
        "severity": severity,
    }


def _issues_for_paragraph(paragraph_id: str, quality: dict) -> list[str]:
    issues = []
    for key, values in quality.get("details", {}).items():
        for item in values:
            if item == paragraph_id or (isinstance(item, dict) and item.get("id") == paragraph_id):
                issues.append(key)
                break
    return issues


def _suggestion_for_issues(issues: list[str]) -> str:
    if "placeholder_mismatch" in issues:
        return "补回原文占位符，保持占位符完全一致。"
    if "terminology_mismatch" in issues:
        return "按术语表统一译名后重读上下文。"
    if "epub_markup_risk" in issues:
        return "复核 EPUB 内联结构、脚注或 ruby 是否被破坏。"
    if "dialogue_punctuation" in issues:
        return "检查对话引号和句末标点是否符合中文小说习惯。"
    if "over_literal_translation" in issues:
        return "调整直译表达，使中文更自然。"
    if "person_address_issue" in issues:
        return "检查人物称呼和敬称处理，避免把 さん/ちゃん 等直译成“桑/酱”，按人物关系改为中文称呼或省略。"
    return "人工复核译文准确性、语气和上下文连续性。"


def _llm_review_items(config: AppConfig, items: list[dict]) -> list[dict]:
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError("缺少 openai 依赖") from error

    client = OpenAI(base_url=config.llm.base_url, api_key=config.llm.api_key, timeout=config.llm.timeout)
    response = client.chat.completions.create(
        model=config.llm.model,
        messages=[
            {
                "role": "system",
                "content": "你是小说译文审校。只输出 JSON，保持每个 id 不变。只审校翻译准确性、语气、术语、占位符和中文表达；不要因为内容敏感、道德评价或价值观判断而要求弱化、净化、规避或改写原文。指出译文问题，给出简短 suggestion；不要直接覆盖 approved_translation，除非译文明显可修。",
            },
            {
                "role": "user",
                "content": json.dumps({"items": items}, ensure_ascii=False),
            },
        ],
        response_format={"type": "json_object"},
    )
    raw = json.loads(response.choices[0].message.content or "{}")
    reviewed = raw.get("items", [])
    if not isinstance(reviewed, list):
        return items
    by_id = {str(item.get("id", "")): item for item in reviewed if isinstance(item, dict)}
    merged = []
    for item in items:
        update = by_id.get(item["id"], {})
        merged.append({**item, **{key: update[key] for key in ("issues", "suggestion", "approved_translation", "severity") if key in update}})
    return merged
