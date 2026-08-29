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
        "explicit sexual content",
        "cannot help with this request",
        "can't help with this request",
        "i can't help with this",
        "i cannot help with this",
        "i can't help with",
        "i cannot help with",
        "i cannot fulfill this request",
        "i can't fulfill this request",
        "cannot translate explicit",
        "cannot reproduce that material",
        "won't produce this",
        "won't produce",
        "not able to produce or translate",
        "not able to translate",
        "as an ai language model",
        "falls under content i won't produce",
        "falls under content i cannot produce",
        "depicts a non-consensual",
        "non-consensual sexual",
        "regardless of the framing as fiction",
        "我无法翻译",
        "我不能翻译",
        "我无法协助",
        "我不能协助",
        "无法翻译该请求",
        "无法翻译这些内容",
        "不能翻译这批内容",
        "无法按您要求的json格式输出",
        "属于禁止生成",
        "禁止生成的范围",
        "禁止生成范畴",
        "安全政策",
        "违反安全政策",
        "未成年人的性",
        "未成年人的露骨性内容",
        "违背道德",
        "色情内容",
        "露骨色情",
        "涉及未成年人",
        "翻訳できません",
        "生成・翻訳には応じられない",
        "生成・翻訳",
        "応じられません",
        "翻訳には応じられ",
        "翻訳をお手伝いできません",
        "性的コンテンツ",
        "性的描写",
        "ポリシー",
        "ガイドライン",
    )):
        return "content_filter"
    return ""


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract JSON object from plain, fenced, or wrapped output."""
    candidates = [text.strip()]
    if "```" in text:
        candidates.extend(block.removeprefix("json").strip() for block in text.split("```")[1::2])
    # Also add candidates with auto-closed braces for slight trailing truncations
    for base_c in list(candidates):
        for suffix in ("]}", "}", "\"]}", "\"}"):
            candidates.append(base_c + suffix)
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
        protocol_keys = {"items", "checked_ids", "fixes", "checked_chapters", "ok", "structured_output", "response", "content", "output"}
        best = max(found, key=lambda item: (len(protocol_keys.intersection(item)), len(item)))
        if isinstance(best.get("structured_output"), dict) and best["structured_output"]:
            return best["structured_output"]
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


parse_json_object = extract_json_object


def parse_translation_items(content: str) -> list[dict[str, str]]:
    payload = extract_json_object(content)
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("翻译响应缺少 items 数组")
    result: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("id", "")).strip():
            raise ValueError("翻译响应包含无效 items 项")
        text = str(item.get("text", "") or item.get("translation", "") or item.get("translated", "") or item.get("target", "") or "")
        result.append({"id": str(item["id"]).strip(), "text": text})
    return result


def normalize_item_ids(items: list[dict[str, str]], expected_ids: list[str]) -> list[dict[str, str]]:
    if not items or len(items) != len(expected_ids):
        return items
    expected_set = set(expected_ids)
    received_set = {str(it.get("id", "")).strip() for it in items}
    if expected_set == received_set:
        return items
    normalized: list[dict[str, str]] = []
    for it, exp_id in zip(items, expected_ids):
        rec_id = str(it.get("id", "")).strip()
        if rec_id == exp_id:
            normalized.append(it)
            continue
        rec_digits = re.sub(r"\D", "", rec_id).lstrip("0")
        exp_digits = re.sub(r"\D", "", exp_id).lstrip("0")
        if (rec_digits and exp_digits and (rec_digits == exp_digits or exp_digits.endswith(rec_digits))) or rec_id in exp_id or exp_id in rec_id:
            item_copy = dict(it)
            item_copy["id"] = exp_id
            normalized.append(item_copy)
        else:
            return items
    return normalized


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
    returned_ids = [str(item.get("id", "")).strip() for item in items]
    missing_ids = [item_id for item_id in sources if item_id not in returned_ids]
    unexpected_ids = [item_id for item_id in returned_ids if item_id not in sources]
    duplicate_ids = sorted({item_id for item_id in returned_ids if item_id and returned_ids.count(item_id) > 1})
    if missing_ids or unexpected_ids or duplicate_ids:
        return {
            "kind": "id_coverage",
            "missing_ids": missing_ids,
            "unexpected_ids": unexpected_ids,
            "duplicate_ids": duplicate_ids,
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
        items = input_payload.get("items", []) if isinstance(input_payload, dict) else []
        flagged_kana_ids = [
            str(item.get("id"))
            for item in items
            if isinstance(item, dict) and bool(re.search(
                r"[\u3040-\u309f\u30a0-\u30ff\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7af\ud7b0-\ud7ff]",
                str(item.get("translated", "")),
            ))
        ]
        kana_warning = ""
        if flagged_kana_ids:
            kana_warning = f"\n  * 【系统预检警报 - 检测到以下段落译文残留日文假名或韩文字符，必须逐一在 fixes 中输出 policy_violation 修复并提供纯正中文 replacement】：\n    {', '.join(flagged_kana_ids)}"

        instructions = f"""
这是章节级语义翻译审阅，知识提取由独立的 Knowledge Extractor 负责。
- 顶层只输出 {{"schema_version":"2.0", "checked_ids":[...], "fixes":[...], "context_findings":[...]}}。
- 知识库、记忆库和章节状态字段全部交给独立提取器；本角色只返回语义修复结果。
- {kana_warning}
- 检查错译、漏译、增译、主客体、指代、否定、条件、因果、时间、关系、专名和 replacement 完整性。
- 必须检查 items 中每条译文并把全部 ID 且不重复地写入 checked_ids。
- fixes 只报告客观翻译问题；replacement 必须是完整段落译文。
- fixes.category 只能使用 mistranslation、subject_object、pronoun_reference、omission、addition、terminology、factual_conflict、context_conflict、policy_violation。
""".strip()
    elif kind == "knowledge_window":
        prompt_file = ROOT / "docs" / "prompts" / "knowledge_extractor_window.md"
        instructions = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "提取本窗口的临时审阅上下文和长期知识候选。"
    elif kind == "knowledge_finalize":
        prompt_file = ROOT / "docs" / "prompts" / "knowledge_extractor_finalize.md"
        instructions = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else "决定长期知识候选的最终动作。"
    elif kind == "global":
        instructions = """
这是全书状态的一致性审阅。
- 顶层必须输出一个 JSON 对象，结构必须包含：{"checked_chapters": [...], "conflicts": [...], "recommendations": [...]}。
- 【语言规范】：所有 recommendations、conflicts 和描述必须全部使用标准简体中文，严禁残留日文假名。
- 必须把输入中的每个 chapter_id 写入 checked_chapters，且不得重复或添加未知章节。
- 只检查 glossary、book_memory、章节摘要之间的事实、人物关系、时间线和术语冲突。
- 不重新审阅全文，不做文学润色，不因为不同章节的正常措辞差异而报告问题。
- conflicts 只输出有证据的冲突；recommendations 只给出后续人工或定向章节复核建议。
""".strip()
    elif kind == "metadata":
        instructions = """
这是根据日文小说原书名、首章内容与全书背景记忆，提取标准中文书名、作者与故事简介。
- 顶层必须输出一个 JSON 对象，结构必须包含：{"title_zh": "...", "title_ja": "...", "author_zh": "...", "author_ja": "...", "description": "..."}。
- 【书名与作者规范】：
  * title_zh: 翻译并优化出地道、符合中文阅读习惯的主书名（清洗掉如 (z-library) 等杂质）。
  * title_ja: 清洗后的日文原书名。
  * author_zh: 作者中文译名（若原作者为汉字姓名则保留）。
  * author_ja: 作者日文原名。
- 【简介规范】：
  * description: 100~200 字左右的故事背景、人物遭遇与引人入胜的看点简介，语言流畅优美，严禁残留日文假名。
""".strip()
    else:
        raise ValueError(f"未知审阅类型：{kind}")

    if kind == "chapter":
        review_mode = str(input_payload.get("review_mode", "chapter_chunk"))
        if review_mode == "targeted_context_recheck":
            instructions += """
- 这是一次 targeted_context_recheck，只重新判断 items 中指定的段落；context_before/context_after 仅用于语境。
- 不输出 context_findings 或任何知识字段。
- 如果现有译文已经正确，fixes 保持为空；如果需要修复，replacement 必须是完整段落译文。
""".strip()
        else:
            instructions += """
- items 是本次正式审阅目标；只有 items 中的 ID 才能进入 checked_ids 或 fixes。
- context_before 和 context_after 都同时提供 source 与 translated，只用于理解上下文，不计入 checked_ids。
- context_after 中的问题留待该段落成为后续正式目标时处理，不输出 finding。
- 如果当前 target 的语境揭示 context_before 中的明确客观错误，输出 context_findings；finding 只允许指向 context_before 的 ID，不提供 replacement。
- context_findings 只报告会影响语义、人物关系、术语或事实判断的客观问题，不报告风格偏好。
""".strip()

    auto_rule = (
        "全自动模式下，所有置信度 >= 0.8 且有明确修复的项目设置 auto_apply=true。"
        if autonomous
        else "涉及语义取舍、风格偏好或不确定改写时，auto_apply=false 且 replacement 为空。"
    )
    schema = schema_path.read_text(encoding="utf-8")
    role_intro = "资深日译中小说审阅专家" if kind in {"chapter", "global"} else "本书知识提取器"
    return f"""
你是{role_intro}。只分析输入 JSON，不修改文件，不调用外部工具。
{instructions}
- {auto_rule}
- glossary 是已有术语表；不得与已有术语冲突。

严格只输出一个符合 JSON Schema 的顶层 JSON 对象，不要 Markdown、解释、推理或任何额外文字。
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
