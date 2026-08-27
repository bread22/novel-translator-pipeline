from __future__ import annotations

from abc import ABC, abstractmethod
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

from translator.glossary.taxonomy import BLOCKED, CATEGORY_VALUES, DIRECT_ALLOWED, GATED_ALLOWED


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
            if isinstance(item, dict) and bool(re.search(r"[\u3040-\u309f\u30a0-\u30ff]", str(item.get("translated", ""))))
        ]
        kana_warning = ""
        if flagged_kana_ids:
            kana_warning = f"\n  * 【系统预检警报 - 检测到以下段落译文残留日文假名，必须逐一在 fixes 中输出 policy_violation 修复并提供纯正中文 replacement】：\n    {', '.join(flagged_kana_ids)}"

        instructions = f"""
这是章节级一致性审阅。
- 顶层必须输出一个 JSON 对象，结构必须包含：{{"schema_version":"2.0", "checked_ids": [...], "fixes": [...], "glossary_delta": {{"add":[], "update":[], "conflicts":[]}}, "memory_delta": {{"add":[], "update":[], "conflicts":[]}}, "chapter_state": {{"summary": "...", "important_changes":[], "active_entities":[], "location":"", "timeline":[]}}}}。
- 【语言规范与严禁残留日文】：{kana_warning}
  * 对 items 中每一条 translated 字段逐字扫描平假名和片假名；这是对已有译文的硬性检查，不得只检查 fixes.replacement！
  * translated 中只要残留一个日文假名（包括拟声词、语气词、敬称、助词或片假名词的一部分），就必须为该段输出一条 policy_violation fix。
  * source 字段本身是日文原文，source 中的假名不算译文残留；检查对象是 translated 字段。
  * 发现残留时，reason 必须指出具体残留文本，replacement 必须给出该段完整、无任何假名的简体中文译文，不能只替换一个词或返回空字符串。
  * 所有 fixes 里的 replacement（修正译文）必须是纯正、地道、通顺的完整简体中文段落！
  * 绝对严禁在 replacement 中残留任何日文假名（包括平假名、片假名，如 すぐそば、カタカナ、の、に 等）或未翻译的生造日文词汇！
  * 若原文中出现片假名概念词（如「カタカナ職業」），必须意译为其对应中文含义（如“时尚新潮职业”/“白领职业”），严禁直接复制日文假名！
  * glossary_delta 的 target（中文译名）、memory_delta 中的各字段值、chapter_state 中的 summary，全部必须使用规范简体中文。
- 【日文汉字、伏字与遮掩符号】：
  * 只有 Unicode 平假名和片假名才能称为“日文假名”。日文汉字、旧字体、异体字、中文标点以及○、●、×、＊、※、□等符号不是假名；不得在 reason 中把它们称为“假名”、“平假名”、“片假名”或 kana。
  * 人名、地名或术语中的日文汉字/旧字体/异体字与已有中文译名不一致时，使用 category=terminology、severity=major；reason 写明“与既定中文译名不一致”，不得误报为假名残留。
  * 原文词语中的○、●、×、＊、※、□等可能是出版审查使用的伏字/遮掩符号。必须结合词形、上下文、动作、术语库及同书已出现的完整写法还原；还原结果唯一时，译文必须直接使用术语库指定的完整中文词，不得保留伏字、日文原词或含糊代称。
  * 译文仍保留伏字时，使用 category=policy_violation、severity=critical；reason 必须写“原文伏字/遮掩符号未还原”，replacement 必须是可直接覆盖当前段落的完整简体中文，且不得再含遮掩符号。
  * 只有当伏字的原词和中文译名可唯一确定时才可设置 auto_apply=true 且 confidence>=0.95。若存在两个以上合理解释，必须设置 auto_apply=false、replacement=""，并在 reason 中写明 ambiguous_source；不得猜测或编造原词。
  * 日文风格词或不规范译名只有在与已激活术语表明确冲突时才能输出 terminology fix；纯粹风格偏好、同义词替换或“可以更好”的润色不得输出 fix。
  * 若某段是与相邻段完全重复的多余译文，只有双审一致、category=addition 或 omission、severity=major/critical、confidence>=0.95 时，才可输出 operation=clear、replacement=""、auto_apply=true。不得用 clear 删除任何含有独立原文信息的段落。
- 审阅与严重度分级原则：
  * 客观错误（包括实词误译如名词/材质/动作词错译、成语/惯用句误译、人名术语不一致、主客体颠倒、漏译、假名残留）必须积极报告并给出修复。
  * 严重度分级：
    - critical：严重破坏情节、因果颠倒、整句漏译、残留日文假名。
    - major：明确的词义误译（如面料/物品错译）、术语/人名冲突、动作混淆、惯用语明显生硬。
    - minor：微观语境精度纠偏、动词动作层次精度修正、地道口语化纠错（置信度 >= 0.8 时亦会自动采纳）。
  * 纯属两可的主观风格偏好或无实质改善的润色，不要输出 fix（fixes 数组保持精炼，只解决真正的问题）。
- 必须检查 items 中的每个段落，并把全部 ID 且不重复地写入 checked_ids（必须覆盖 items 中的全部段落 ID）。
- fixes 只输出确实存在的问题；replacement 必须是完整段落译文。除了符合上述严格条件的 operation=clear 重复段落，不得返回空 replacement。
- fixes.category 只能使用：mistranslation、subject_object、pronoun_reference、omission、addition、terminology、factual_conflict、context_conflict、policy_violation；译文残留日文假名必须使用 policy_violation。
- 【术语库收录规范（glossary_delta）】：
  * 只能提交封闭 taxonomy 中的 DIRECT_ALLOWED/GATED_ALLOWED 实体或专名；不要要求模型判断它是否贯穿全书，只提交当前证据。
  * BLOCKED 类别（身体、状态、动作、修辞、俚语、普通物品、普通职业/称谓等）不得进入 glossary，但仍可在 fixes 中修复翻译错误。
  * target 必须是单一、确定、干净的简体中文译名，绝对严禁包含备选项斜杠、括号解释、词典释义或日文假名。
  * 每个候选必须提供 evidence_ids；程序会根据原文定位、类别和独立证据决定 candidate/active 状态。
- memory_delta 只收录会影响后续章节翻译的人物、关系、别名、重要事实和持续状态。
- chapter_state 只保存本章摘要和会影响后续理解的重要变化。
""".strip()
    elif kind == "glossary_extract":
        instructions = """
这是章节翻译前的轻量实体预提取，只提交术语候选，不提交人物经历、关系、外貌、剧情或描写性短语。
- 顶层必须输出 {"schema_version":"3.0", "checked_ids":[...], "candidates":[...]}。
- candidates 只能包含 source、target、category、confidence、evidence_ids、note；不得输出 status、term_id、occurrences、chapter_count 或时间戳。
- category 必须使用封闭 taxonomy：DIRECT_ALLOWED 可在 confidence >= 0.92 且有证据时激活；GATED_ALLOWED 仅保留候选，等待两个段落/章节或两个独立 reporter；BLOCKED 仍可作为正常翻译审阅 fix，但永远不能作为 glossary candidate。
- BLOCKED 包括 anatomy/body_part/body_fluid/body_state/mental_state/action/generic_technique/onomatopoeia、修辞、俚语、普通物品、普通医学词、职业和通用称谓等。
- 每个 evidence_ids 必须是本次输入 items 的 paragraph ID；target 必须是单一、干净的简体中文译名，不得有斜杠、括号、候选说明或日文假名。
- checked_ids 必须覆盖本次输入的所有段落 ID。
""".strip()
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

    taxonomy_instruction = (
        f"\n- taxonomy DIRECT_ALLOWED（{','.join(sorted(DIRECT_ALLOWED))}）；"
        f"GATED_ALLOWED（{','.join(sorted(GATED_ALLOWED))}）；"
        f"BLOCKED（{','.join(sorted(BLOCKED))}）。"
        f"category 只能从封闭集合 {','.join(CATEGORY_VALUES)} 中选择。"
    )
    if kind in {"chapter", "glossary_extract"}:
        instructions += taxonomy_instruction

    auto_rule = (
        "全自动模式下，所有置信度 >= 0.8 且有明确修复的项目设置 auto_apply=true。"
        if autonomous
        else "涉及语义取舍、风格偏好或不确定改写时，auto_apply=false 且 replacement 为空。"
    )
    schema = schema_path.read_text(encoding="utf-8")
    return f"""
你是资深日译中小说审阅专家。只分析输入 JSON，不修改文件，不调用外部工具。
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
