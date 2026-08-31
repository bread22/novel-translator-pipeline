from __future__ import annotations

from collections.abc import Iterable
import json
import time

from app.config import AppConfig
from app.context import context_for_batch
from app.models import Book, Paragraph
from app.placeholders import placeholder_payload_for_paragraph
from app.terminology import Term, relevant_terms_for_text


def pending_paragraphs(paragraphs: Iterable[Paragraph]) -> list[Paragraph]:
    return [paragraph for paragraph in paragraphs if not paragraph.translated.strip()]


def make_batches(paragraphs: list[Paragraph], max_chars: int) -> list[list[Paragraph]]:
    batches: list[list[Paragraph]] = []
    current: list[Paragraph] = []
    current_chars = 0
    for paragraph in paragraphs:
        size = len(paragraph.source)
        if current and current_chars + size > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(paragraph)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def translate_batch(
    config: AppConfig,
    paragraphs: list[Paragraph],
    terms: list[Term] | None = None,
    *,
    book: Book | None = None,
    context: dict | None = None,
) -> dict[str, str]:
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError("缺少 openai 依赖，请先安装项目依赖。") from error

    system_prompt = config.system_prompt_path.read_text(encoding="utf-8")
    client = OpenAI(
        base_url=config.llm.base_url,
        api_key=config.llm.api_key,
        timeout=config.llm.timeout,
    )
    payload = build_translation_payload(config, paragraphs, terms or [], book=book, context=context)
    last_error: Exception | None = None
    for attempt in range(config.translation.retry_count + 1):
        try:
            response = client.chat.completions.create(
                model=config.llm.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
            result = parse_translation_response(content)
            validation = validate_llm_response(paragraphs, result)
            if validation["errors"]:
                raise ValueError("; ".join(validation["errors"]))
            return result
        except Exception as error:
            last_error = error
            if attempt >= config.translation.retry_count:
                break
            time.sleep(config.translation.retry_delay)
    raise RuntimeError(f"翻译请求失败：{last_error}") from last_error


def build_translation_payload(
    config: AppConfig,
    paragraphs: list[Paragraph],
    terms: list[Term] | None = None,
    *,
    book: Book | None = None,
    context: dict | None = None,
) -> dict:
    payload = {
        "source_language": config.translation.source_language,
        "target_language": config.translation.target_language,
        "quality_profile": translation_quality_profile(config),
        "glossary": glossary_for_batch(paragraphs, terms or []),
        "context": context_for_batch(book, paragraphs, context or {}, config.context) if book is not None else {},
        "items": [
            {
                "id": item.id,
                "text": item.source,
                "placeholders": placeholder_payload_for_paragraph(item),
            }
            for item in paragraphs
        ],
    }
    style_reference = translation_style_reference(config)
    if style_reference:
        payload["style_reference"] = style_reference
    return payload


def translation_quality_profile(config: AppConfig) -> dict:
    return {
        "style_guide": config.translation.style_guide,
        "dialogue_style": config.translation.dialogue_style,
        "self_check_passes": config.translation.quality_passes,
        "requirements": [
            "忠实保留原文事实、动作顺序、视角、语气和信息量，不总结、不删减、不扩写剧情。",
            "优先产出自然中文小说句子，避免逐词硬译；必要时调整语序，让叙事连贯顺滑。",
            "对话要像中文小说人物说话，保持称谓、敬语、粗口强度、情绪和角色关系一致。",
            "比喻、意象、伏笔和重复修辞应尽量保留；无法直译时选择等效中文表达。",
            "同一批次和上下文中的人名、地名、组织名、技能名、称号和代词指代必须一致。",
            "保留段落边界、数字、单位、标点含义、换行意图、HTML 标签、脚注锚点和所有 placeholders。",
            "不要因为内容敏感、道德评价或价值观判断而弱化、净化、规避或改写原文。",
            "输出前自查术语、占位符、遗漏句、重复句、错译和中文病句；只输出最终 JSON。",
        ],
        "avoid": [
            "腔调生硬的逐词直译",
            "漏译、跳译、合并多个段落或添加原文没有的信息",
            "同一角色称谓前后不一致",
            "把旁白翻成解释性摘要",
            "删除或改写占位符、HTML 标签和脚注锚点",
        ],
    }


def translation_style_reference(config: AppConfig) -> dict:
    path = config.style_sample_path
    if path is None or not path.exists() or not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    max_chars = max(0, config.translation.style_sample_max_chars)
    if max_chars:
        text = text[:max_chars]
    if not text:
        return {}
    return {
        "path": str(path),
        "max_chars": max_chars,
        "text": text,
        "instruction": "参考这个样例的中文叙事节奏、对话口吻、标点和句式密度；不要照抄样例内容。",
    }


def glossary_for_batch(paragraphs: list[Paragraph], terms: list[Term]) -> list[dict[str, str]]:
    seen: set[str] = set()
    glossary: list[dict[str, str]] = []
    for paragraph in paragraphs:
        for term in relevant_terms_for_text(terms, paragraph.source):
            if term.source in seen:
                continue
            seen.add(term.source)
            glossary.append(
                {
                    "source": term.source,
                    "target": term.target,
                    "category": term.category,
                    "note": term.note,
                }
            )
    return glossary


def parse_translation_response(content: str) -> dict[str, str]:
    raw = json.loads(content)
    items = raw.get("items", raw if isinstance(raw, list) else [])
    if not isinstance(items, list):
        raise ValueError("模型输出 JSON 缺少 items 数组")
    result: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", "")).strip()
        text = str(item.get("text", "")).strip()
        if item_id:
            result[item_id] = text
    return result


def validate_llm_response(paragraphs: list[Paragraph], result: dict[str, str]) -> dict[str, list[str]]:
    expected = {paragraph.id for paragraph in paragraphs}
    received = set(result)
    missing = sorted(expected - received)
    unknown = sorted(received - expected)
    empty = sorted(item_id for item_id in expected & received if not result.get(item_id, "").strip())
    errors = []
    warnings = []
    if missing:
        errors.append(f"模型响应缺少段落 ID：{', '.join(missing)}")
    if empty:
        warnings.append(f"模型响应包含空译文：{', '.join(empty)}")
    if unknown:
        warnings.append(f"模型响应包含未知段落 ID：{', '.join(unknown)}")
    return {"errors": errors, "warnings": warnings}
