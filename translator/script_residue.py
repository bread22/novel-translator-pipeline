from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Literal


_KANA_CHARS = r"\u3040-\u309f\u30a0-\u30fa\u30fc-\u30ff"
JAPANESE_KANA_REGEX = re.compile(rf"[{_KANA_CHARS}]")
JAPANESE_KANA_RUN_REGEX = re.compile(rf"[{_KANA_CHARS}]+")
HANGUL_REGEX = re.compile(r"[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7af\ud7b0-\ud7ff]")
HANGUL_RUN_REGEX = re.compile(r"[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7af\ud7b0-\ud7ff]+")

# Compatibility constant.  The inspector below uses narrower quote-aware
# checks, so adding a word here does not create a global allow-list.
KANA_REFERENCE_CONTEXT_REGEX = re.compile(
    r"(?:原文|假名|平假名|片假名|变体假名|日文(?:字|文字)?|写着|写作|写成|写的是|读作|读音|发音|表记|书写|字形|字样|字般|文字|二字|三字|四字|字体|符号|"
    r"词|这个词|此词|一词|词语|词汇|称作|称为|叫做|叫作|意思|含义)"
)

ResidueClassification = Literal[
    "target_script_residue",
    "explicit_source_reference",
    "target_hangul",
    "source_copy",
    "ambiguous",
]


@dataclass(frozen=True)
class ScriptResidueFinding:
    """One target-script finding with replayable, normalized offsets."""

    token: str
    start: int
    end: int
    classification: ResidueClassification
    source_match: bool = False
    context_match: str | None = None
    source_context_match: str | None = None
    target_context_match: str | None = None
    preserve_policy_enabled: bool = True


_QUOTE_PAIRS = (
    ("「", "」"),
    ("『", "』"),
    ("“", "”"),
    ("‘", "’"),
    ('"', '"'),
    ("'", "'"),
    ("（", "）"),
    ("(", ")"),
    ("【", "】"),
)
_SOURCE_CONTEXT_TERMS = re.compile(
    r"(?:原文|原字|文字|字形|字样|字体|假名|平假名|片假名|变体假名|日文(?:字|文字)?|表记|书写|書|描|えが|写|"
    r"言葉|ことば|コトバ|語(?:源|彙|義)?|用語|熟語|呼称|呼(?:ぶ|ばれ|ばれる|んだ|び|名)|"
    r"言(?:う|い|った|われる|われ)|という|といった|名付|名づけ|命名|由来|意味|意義|定義|"
    r"古語|俗語|隠語|方言|スラング|符丁|俚語|いわゆる|所謂)"
)
_TARGET_CONTEXT_TERMS = re.compile(
    r"(?:原文|原字|文字|字形|字样|字体|字|假名|平假名|片假名|变体假名|日文(?:字|文字)?|日语|表记|书写|写着|写作|写成|写的是|读作|读音|发音|符号|"
    r"词|这个词|此词|一词|词语|词汇|单词|词条|词义|词源|"
    r"称作|称为|叫做|叫作|称呼|被称为|被称作|俗称|统称|简称|名称|"
    r"古语|俗语|俚语|隐语|行话|暗语|黑话|方言|成语|惯用语|语源|语义|语意|"
    r"意思|含义|涵义|指代|指的是|意指|所谓|释义|说法|俗说|"
    r"翻译|译作|译为|译成|译意)"
)
_SHAPE_CONTEXT_TERMS = re.compile(r"(?:字形|字样|字体|文字|[一二三四五六七八九十百千]字|个字|这个字|此字)")
_WORD_CONTEXT_TERMS = re.compile(
    r"(?:言葉|ことば|コトバ|語|用語|熟語|呼称|呼(?:ぶ|ばれ|ばれる|んだ)|言(?:う|い|った|われる|われ)|という|といった|"
    r"词|这个词|此词|一词|词语|词汇|单词|词条|词义|词源|称作|称为|叫做|叫作|称呼|俗称|古语|俗语|俚语|隐语|行话|暗语|黑话|方言|意思|含义|涵义|说法)"
)


def has_japanese_kana(text: str) -> bool:
    return bool(JAPANESE_KANA_REGEX.search(text))


def has_hangul(text: str) -> bool:
    return bool(HANGUL_REGEX.search(text))


def _quoted_context(text: str, token: str, *, source: bool) -> str | None:
    """Return the semantic category around a quoted character token."""
    terms = _SOURCE_CONTEXT_TERMS if source else _TARGET_CONTEXT_TERMS
    for opening, closing in _QUOTE_PAIRS:
        pattern = re.compile(re.escape(opening) + re.escape(token) + re.escape(closing))
        for match in pattern.finditer(text):
            window = text[max(0, match.start() - 25):min(len(text), match.end() + 25)]
            if not terms.search(window):
                continue
            if _SHAPE_CONTEXT_TERMS.search(window):
                return "shape_reference"
            if _WORD_CONTEXT_TERMS.search(window):
                return "word_reference"
            return "character_reference"
    return None


def _source_character_context(token: str, source: str) -> str | None:
    """Require explicit source character context, not a bare occurrence."""
    return _quoted_context(source, token, source=True)


def _target_character_context(token: str, text: str) -> str | None:
    """Require quoted retention plus a nearby explanation of that character."""
    return _quoted_context(text, token, source=False)


def _is_kana_reference(text: str, source: str, start: int, end: int) -> bool:
    """Compatibility predicate for the former private helper."""
    token = text[start:end]
    normalized_source = unicodedata.normalize("NFKC", source)
    return (
        token in normalized_source
        and _source_character_context(token, normalized_source) is not None
        and _target_character_context(token, text) is not None
    )


def inspect_target_script(
    text: str,
    *,
    source: str = "",
    preserve_policy_enabled: bool = True,
) -> tuple[ScriptResidueFinding, ...]:
    """Inspect every Japanese kana/Hangul run in target text.

    A source-character exception needs source match, explicit source context,
    quoted target context, and the preservation policy.  Any other run is a
    residue finding; callers must not infer approval from source occurrence.
    """
    normalized_text = unicodedata.normalize("NFKC", text)
    normalized_source = unicodedata.normalize("NFKC", source or "")
    if not normalized_text:
        return ()

    if (
        normalized_source
        and normalized_text.strip() == normalized_source.strip()
        and (has_japanese_kana(normalized_source) or has_hangul(normalized_source))
    ):
        return (
            ScriptResidueFinding(
                token=normalized_text,
                start=0,
                end=len(normalized_text),
                classification="source_copy",
                source_match=True,
                context_match="source_copy",
                preserve_policy_enabled=preserve_policy_enabled,
            ),
        )

    findings: list[ScriptResidueFinding] = []
    for match in JAPANESE_KANA_RUN_REGEX.finditer(normalized_text):
        token = match.group(0)
        source_match = bool(token and token in normalized_source)
        source_context = _source_character_context(token, normalized_source) if source_match else None
        target_context = _target_character_context(token, normalized_text)
        explicit = bool(source_match and source_context and target_context and preserve_policy_enabled)
        findings.append(
            ScriptResidueFinding(
                token=token,
                start=match.start(),
                end=match.end(),
                classification="explicit_source_reference" if explicit else "target_script_residue",
                source_match=source_match,
                context_match=target_context,
                source_context_match=source_context,
                target_context_match=target_context,
                preserve_policy_enabled=preserve_policy_enabled,
            )
        )

    for match in HANGUL_RUN_REGEX.finditer(normalized_text):
        findings.append(
            ScriptResidueFinding(
                token=match.group(0),
                start=match.start(),
                end=match.end(),
                classification="target_hangul",
                source_match=match.group(0) in normalized_source,
                preserve_policy_enabled=preserve_policy_enabled,
            )
        )

    return tuple(sorted(findings, key=lambda item: (item.start, item.end, item.token)))


def has_illegal_japanese_kana(text: str, *, source: str | None = None) -> bool:
    """Return whether kana is residual rather than an explicit source object."""
    findings = inspect_target_script(text, source=source or "")
    return any(
        has_japanese_kana(item.token)
        and item.classification in {"target_script_residue", "source_copy", "ambiguous"}
        for item in findings
    )


def has_target_script_residue(text: str, *, source: str | None = None) -> bool:
    """Return whether target text contains Hangul or unallowed Japanese kana."""
    return any(
        item.classification in {"target_script_residue", "target_hangul", "source_copy", "ambiguous"}
        for item in inspect_target_script(text, source=source or "")
    )


__all__ = [
    "HANGUL_REGEX",
    "HANGUL_RUN_REGEX",
    "JAPANESE_KANA_REGEX",
    "JAPANESE_KANA_RUN_REGEX",
    "KANA_REFERENCE_CONTEXT_REGEX",
    "ResidueClassification",
    "ScriptResidueFinding",
    "has_hangul",
    "has_illegal_japanese_kana",
    "has_japanese_kana",
    "has_target_script_residue",
    "inspect_target_script",
]
