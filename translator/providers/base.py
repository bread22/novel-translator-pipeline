from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CHAPTER_SCHEMA = ROOT / "schemas" / "chapter-review-output.schema.json"
GLOBAL_SCHEMA = ROOT / "schemas" / "global-consistency-output.schema.json"


def provider_block_reason(text: str) -> str:
    lowered = text.casefold()
    if any(marker in lowered for marker in (
        "sensitive words",
        "prohibited use policy",
        "content policy",
        "content_filter",
        "provider_blocked",
        "safety policy",
        "violated safety guidelines",
    )):
        return "content_filter"
    return ""


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract JSON object from plain, fenced, or wrapped output."""
    candidates = [text.strip()]
    if "```" in text:
        candidates.extend(block.removeprefix("json").strip() for block in text.split("```")[1::2])
    decoder = json.JSONDecoder()
    found: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            val = json.loads(candidate)
            if isinstance(val, dict):
                found.append(val)
        except json.JSONDecodeError:
            pass
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                val, _ = decoder.raw_decode(candidate[index:])
                if isinstance(val, dict):
                    found.append(val)
            except json.JSONDecodeError:
                continue
    if found:
        # Prioritize candidate with expected top-level keys
        protocol_keys = {"items", "checked_ids", "fixes", "checked_chapters", "ok", "response", "content", "output"}
        best = max(found, key=lambda item: (len(protocol_keys.intersection(item)), len(item)))
        # Check if items/payload is wrapped in a string or nested object
        for key in ("response", "text", "content", "output"):
            nested = best.get(key)
            if isinstance(nested, str) and "{" in nested:
                try:
                    return extract_json_object(nested)
                except ValueError:
                    pass
            if isinstance(nested, dict) and any(k in nested for k in ("items", "checked_ids", "fixes", "ok")):
                return nested
        return best
    raise ValueError("输出中没有找到有效的 JSON 对象")


def parse_translation_items(content: str) -> list[dict[str, str]]:
    payload = extract_json_object(content)
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("翻译响应缺少 items 数组")
    result: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("id", "")).strip():
            raise ValueError("翻译响应包含无效 items 项")
        result.append({"id": str(item["id"]).strip(), "text": str(item.get("text", ""))})
    return result


def normalized_text(text: str) -> str:
    return re.sub(r"\s+", "", text.replace("\\n", "\n"))


def repeated_content(text: str) -> dict[str, Any] | None:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.replace("\\n", "\n").splitlines()
        if line.strip()
    ]
    counts = Counter(line for line in lines if len(line) >= 24)
    for line, count in counts.items():
        if count >= 2:
            return {"kind": "repeated_line", "count": count, "sample": line[:160]}
    return None


def previous_context_overlap(text: str, payload: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    haystack = normalized_text(text)
    context = payload.get("context", {}) if isinstance(payload, dict) else {}
    previous = context.get("previous", []) if isinstance(context, dict) else []
    for item in previous:
        if not isinstance(item, dict) or str(item.get("id", "")) == item_id:
            continue
        translated = str(item.get("translated", ""))
        candidate = normalized_text(translated)
        if len(candidate) >= 48 and candidate in haystack:
            return {
                "kind": "previous_context_overlap",
                "source_id": str(item.get("id", "")),
                "sample": translated[:160],
            }
    return None


def validate_translation_items(items: list[dict[str, str]], payload: dict[str, Any]) -> dict[str, Any] | None:
    requested = payload.get("items", []) if isinstance(payload, dict) else []
    sources = {
        str(item.get("id", "")): str(item.get("text", ""))
        for item in requested
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }
    for item in items:
        item_id = str(item.get("id", "")).strip()
        text = str(item.get("text", "")).strip()
        if not text:
            return {"kind": "empty_translation", "id": item_id}
        source = sources.get(item_id, "")
        max_chars = max(512, len(source) * 6 + 256)
        if len(text) > max_chars:
            return {
                "kind": "output_too_long",
                "id": item_id,
                "text_chars": len(text),
                "source_chars": len(source),
                "max_chars": max_chars,
            }
        repeated = repeated_content(text)
        if repeated:
            return {"id": item_id, **repeated}
        overlap = previous_context_overlap(text, payload, item_id)
        if overlap:
            return {"id": item_id, **overlap}
    return None


def build_review_prompt(kind: str, input_payload: dict[str, Any], schema_path: Path, autonomous: bool) -> str:
    if kind == "chapter":
        instructions = """
这是章节级一致性审阅。
- 只报告会导致读者误解原文的实质错误，不做文学润色。
- 不报告纯风格偏好、轻微措辞差异、可接受的自然化、标点偏好或普通敬称差异。
- 必须检查 items 中的每个段落，并把全部 ID 且不重复地写入 checked_ids。
- 重点检查人物身份和关系、主客体、代词指代、漏译、擅自添加、术语固定译法、事实冲突、时间顺序、跨段落动作关系和明显改变的强度。
- 当无法确定问题是否改变原意时，不要输出 fix。
- fixes 只输出确实存在且属于 critical 或 major 的问题；replacement 必须是完整段落译文。
- glossary_delta 只收录后文仍有价值的人名、别名、组织、地点、特殊术语和固定称谓。
- memory_delta 只收录会影响后续章节翻译的人物、关系、别名、重要事实和持续状态。
- chapter_state 只保存本章摘要和会影响后续理解的重要变化。
""".strip()
    elif kind == "global":
        instructions = """
这是全书状态的一致性审阅。
- 必须把输入中的每个 chapter_id 写入 checked_chapters，且不得重复或添加未知章节。
- 只检查 glossary、book_memory、章节摘要之间的事实、人物关系、时间线和术语冲突。
- 不重新审阅全文，不做文学润色，不因为不同章节的正常措辞差异而报告问题。
- conflicts 只输出有证据的冲突；recommendations 只给出后续人工或定向章节复核建议。
""".strip()
    else:
        raise ValueError(f"未知审阅类型：{kind}")

    auto_rule = (
        "全自动模式下，所有置信度 >= 0.9 且有明确修复的项目设置 auto_apply=true。"
        if autonomous
        else "涉及语义取舍、风格偏好或不确定改写时，auto_apply=false 且 replacement 为空。"
    )
    schema = schema_path.read_text(encoding="utf-8")
    return f"""
你是日译中小说译文审阅者。只分析输入 JSON，不修改文件，不调用外部工具。
{instructions}
- {auto_rule}
- glossary 是已有术语表；不得与已有术语冲突。

严格只输出一个 JSON 对象，不要 Markdown、解释、推理或前后缀。
JSON Schema：
{schema}

输入 JSON：
{json.dumps(input_payload, ensure_ascii=False)}
""".strip()


class BaseProvider(ABC):
    """Abstract base class for all translation and review backends."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        self.name = name
        self.config = config

    @abstractmethod
    def health_check(self, timeout: int = 60) -> dict[str, Any]:
        """Perform a ping / lightweight validation of the provider."""
        raise NotImplementedError

    @abstractmethod
    def translate(
        self,
        payload: dict[str, Any],
        system_prompt: str,
        max_tokens: int,
        timeout: int | None = None,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        """Translate a batch of paragraphs payload into list of {id, text} items."""
        raise NotImplementedError

    @abstractmethod
    def review(
        self,
        kind: str,
        input_payload: dict[str, Any],
        schema_path: Path,
        autonomous: bool = False,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """Review chapter or global consistency payload and return JSON output."""
        raise NotImplementedError
