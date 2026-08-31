from __future__ import annotations

from pathlib import Path
import json

from app.config import ContextConfig, LlmConfig
from app.models import Book, Paragraph, book_dir


def context_path(root_books_dir: Path, book_id: str) -> Path:
    return book_dir(root_books_dir, book_id) / "context.json"


def load_context(root_books_dir: Path, book_id: str) -> dict:
    path = context_path(root_books_dir, book_id)
    if not path.exists():
        return {"book": book_id, "chapters": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_context(root_books_dir: Path, book_id: str, context: dict) -> Path:
    path = context_path(root_books_dir, book_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def summarize_context(root_books_dir: Path, book: Book, config: ContextConfig, llm: LlmConfig | None = None) -> dict:
    chapters = {}
    warnings = []
    summary_mode = "llm" if llm is not None and llm.api_key else "extractive"
    for chapter in book.chapters:
        text = "\n".join(paragraph.source for paragraph in chapter.paragraphs)
        try:
            if summary_mode == "llm":
                summary = _summarize_chapter_with_llm(chapter.title, text, config, llm)
            else:
                summary = _extractive_summary(text, config)
        except Exception as error:
            summary_mode = "extractive"
            warnings.append(f"章节 {chapter.id} 模型摘要失败，已回落到抽取式摘要：{error}")
            summary = _extractive_summary(text, config)
        chapters[chapter.id] = {
            "title": chapter.title,
            "summary": summary,
            "paragraph_count": len(chapter.paragraphs),
        }
    if summary_mode == "extractive" and not warnings:
        warnings.append("当前上下文使用抽取式章节摘要；配置可用模型后会自动尝试模型摘要。")
    payload = {"book": book.id, "summary_mode": summary_mode, "chapters": chapters}
    path = save_context(root_books_dir, book.id, payload)
    return {
        "status": "ok",
        "warnings": warnings,
        "summary": {"book": book.id, "chapters": len(chapters), "summary_mode": summary_mode, "output": str(path)},
        "details": {},
    }


def context_status(root_books_dir: Path, book: Book) -> dict:
    context = load_context(root_books_dir, book.id)
    chapters = context.get("chapters", {})
    missing = [chapter.id for chapter in book.chapters if chapter.id not in chapters]
    return {
        "status": "ok" if not missing else "warning",
        "warnings": [f"缺少 {len(missing)} 个章节摘要"] if missing else [],
        "summary": {"book": book.id, "chapters": len(book.chapters), "summarized": len(chapters), "missing": len(missing)},
        "details": {"missing": missing},
    }


def context_for_batch(book: Book, paragraphs: list[Paragraph], context: dict, config: ContextConfig) -> dict:
    if not paragraphs:
        return {}
    all_paragraphs = book.paragraphs
    first = paragraphs[0]
    first_index = next((index for index, paragraph in enumerate(all_paragraphs) if paragraph.id == first.id), 0)
    previous_items = all_paragraphs[max(0, first_index - config.previous_paragraphs):first_index]
    next_start = first_index + len(paragraphs)
    next_items = all_paragraphs[next_start:next_start + config.next_paragraphs]
    chapter = next((item for item in book.chapters if item.id == first.chapter_id), None)
    chapter_context = context.get("chapters", {}).get(first.chapter_id, {})
    return {
        "chapter_id": first.chapter_id,
        "chapter_title": chapter.title if chapter else "",
        "chapter_summary": chapter_context.get("summary", ""),
        "previous": [
            {"id": item.id, "source": item.source, "translated": item.translated}
            for item in previous_items
        ],
        "next": [
            {"id": item.id, "source": item.source}
            for item in next_items
        ],
    }


def _extractive_summary(text: str, config: ContextConfig) -> str:
    return text[: config.chapter_summary_max_chars].strip()


def _summarize_chapter_with_llm(title: str, text: str, config: ContextConfig, llm: LlmConfig) -> str:
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError("缺少 openai 依赖") from error

    client = OpenAI(base_url=llm.base_url, api_key=llm.api_key, timeout=llm.timeout)
    excerpt = text[: max(config.chapter_summary_max_chars * 8, 4000)]
    response = client.chat.completions.create(
        model=llm.model,
        messages=[
            {
                "role": "system",
                "content": "你是小说翻译助手。请为章节生成简洁中文上下文摘要，保留人物、地点、冲突、情绪和关键伏笔，不添加原文没有的信息。",
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "chapter_title": title,
                        "max_chars": config.chapter_summary_max_chars,
                        "source": excerpt,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    )
    summary = (response.choices[0].message.content or "").strip()
    return summary[: config.chapter_summary_max_chars].strip()
