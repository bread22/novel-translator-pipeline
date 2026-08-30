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
    protocol_keys = {
        "items", "checked_ids", "fixes", "checked_chapters", "ok",
        "structured_output", "response", "content", "output",
        "schema_version", "knowledge_candidates", "rolling_context_delta",
        "decisions", "conflicts", "candidates", "active_glossary", "related_memory",
        "title_zh", "title_ja", "description",
    }
    for candidate in candidates:
        try:
            val = json.loads(candidate)
            if isinstance(val, dict):
                found.append(val)
        except json.JSONDecodeError:
            pass
        idx = 0
        while idx < len(candidate):
            if candidate[idx] != "{":
                idx += 1
                continue
            try:
                val, end_offset = decoder.raw_decode(candidate[idx:])
                if isinstance(val, dict):
                    found.append(val)
                idx += max(1, end_offset)
            except json.JSONDecodeError:
                idx += 1
                continue
    if found:
        # Prioritize candidate with expected top-level keys
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
            if isinstance(nested, dict) and any(k in nested for k in protocol_keys):
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
这是章节级语义审阅，知识提取由独立的 Knowledge Extractor 负责。
- 顶层只输出 {{"schema_version":"2.0", "checked_ids":[...], "fixes":[...], "context_findings":[...]}}。
- 知识库、记忆库和章节状态字段全部交给独立提取器；本角色只返回语义审阅与润色结果。
- {kana_warning}
- 证据边界：只依据输入中的 source、translated、context、translation_policy 和 glossary 判断。不得把模型记忆、未经提供的出版社惯例、年代惯例、文库惯例或所谓中文出版惯例当作规则；除非该规则明确写在 translation_policy 或 glossary 中，否则不得据此要求修改。
- 检查错译、漏译、增译、主客体、指代、否定、条件、因果、时间、关系、专名和 replacement 完整性。
- 必须检查 items 中每条译文并把全部 ID 且不重复地写入 checked_ids。
- 每个目标先决定 PASS、REPORT_ONLY 或 FIX_REQUIRED。语义正确且中文自然时必须 PASS；只是同义词、文学质感或个人措辞偏好也必须 PASS，且不得输出 finding 或 replacement。
- 在开始润色前必须先回答：译文中是否存在“字面看似中文、实际是日语词法直搬”的表达？重点检查亲属称谓、职务、学校制度、日语汉字词，以及汉字相同但中文不自然的词。此类问题优先于标题修辞和局部措辞优化。
- `兄嫁（あによめ）` 的概念是哥哥的妻子，不是正常中文亲属称谓；根据叙述视角译为 `嫂子`、`兄嫂` 或 `大嫂`。不得因为它在多个标题中重复出现，就默认它是系统术语或为了文库风格原样保留；`義弟` 同样要结合关系译为 `小叔子`、`妻弟` 等自然称谓。
- 只有 source 与 translated 存在可明确指出的客观矛盾时才输出 decision=FIX_REQUIRED，并给出单一完整段落 replacement。多种解释合理、人物关系或上下文证据不足时输出 decision=REPORT_ONLY，不提供自动写回许可。
- 90 年代、港台文库或其他文学风格只作为 advisory，不得单独触发 finding。`序章/序言`、`酒店/饭店`、`兴奋/狂喜` 均是固定 PASS 示例，不得为这些同义表达生成 replacement。
- severity=major 只表示会造成实质意义错误或关键事实错误，例如否定/主体客体/指代/动作/关系/关键术语被改变；纯润色使用 `style` 且通常为 `minor`，不得把润色包装成 major。
- 置信度是基于证据的记录，不是校准后的正确率，也不是自动写回许可。不得仅凭 confidence=0.8、0.9 或更高创建或升级 finding；客观错误必须指出 source 与 translated 的具体语义矛盾，style 润色必须指出具体的中文表达问题及其 translation_policy 依据。
- **透明的外来语不得默认音译。** 对标题和片假名按意义优先：`レイプ` → 强暴/强奸，`ホテル` → 酒店，`ナイフ` → 刀，`セックス` → 性爱；不要机械写成“雷普”“厚泰鲁”“奈夫”“塞库斯”。只有人名、品牌、虚构专名、无法自然意译的名称，或 Glossary 已明确指定音译时，才考虑音译。书名也一样；片假名书名若是有明确意义的普通英语词组合，默认优先传达标题意义，而不是机械保留声音。
- `terminology` 只能用于 source、translation_policy 或 glossary 能直接证明的术语错误，不能用来包装润色；译文中确实残留未翻译的日文/韩文字符仍按 policy_violation 处理，但 source 中的片假名本身不是译文错误。
- fixes.category 只能使用 style、mistranslation、subject_object、pronoun_reference、omission、addition、terminology、factual_conflict、context_conflict、policy_violation。
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
        "全自动模式下，只有 decision=FIX_REQUIRED 的明确客观错误才设置 auto_apply=true；style 永远不得自动写回。replacement 必须是单一完整中文段落，不得含多个答案、编辑说明、Markdown、日文、韩文或遮掩符号。confidence 只能作为辅助记录，绝不能单独触发 auto_apply。"
        if autonomous
        else "只报告明确客观错误；普通润色和同义表达必须 PASS。证据不足时使用 REPORT_ONLY 且 auto_apply=false。"
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
