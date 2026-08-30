from __future__ import annotations

import re
import unicodedata


_KANA_CHARS = r"\u3040-\u309f\u30a0-\u30fa\u30fc-\u30ff"
JAPANESE_KANA_REGEX = re.compile(rf"[{_KANA_CHARS}]")
JAPANESE_KANA_RUN_REGEX = re.compile(rf"[{_KANA_CHARS}]+")
HANGUL_REGEX = re.compile(r"[\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\uac00-\ud7af\ud7b0-\ud7ff]")
KANA_REFERENCE_CONTEXT_REGEX = re.compile(
    r"(?:原文|假名|平假名|片假名|变体假名|日文(?:字|文字)?|写着|写作|写成|写的是|读作|读音|发音|表记|书写|字形|字样|字般|文字|二字|三字|四字|字体|符号)"
)


def has_japanese_kana(text: str) -> bool:
    return bool(JAPANESE_KANA_REGEX.search(text))


def has_hangul(text: str) -> bool:
    return bool(HANGUL_REGEX.search(text))


def _is_kana_reference(text: str, source: str, start: int, end: int) -> bool:
    token = text[start:end]
    if token not in unicodedata.normalize("NFKC", source):
        return False
    window = text[max(0, start - 40):min(len(text), end + 40)]
    return bool(KANA_REFERENCE_CONTEXT_REGEX.search(window))


def has_illegal_japanese_kana(text: str, *, source: str | None = None) -> bool:
    """Return whether kana is residual rather than an explicitly discussed source-text object."""
    normalized_text = unicodedata.normalize("NFKC", text)
    matches = list(JAPANESE_KANA_RUN_REGEX.finditer(normalized_text))
    if not matches:
        return False
    if not source:
        return True
    return any(
        not _is_kana_reference(normalized_text, source, match.start(), match.end())
        for match in matches
    )


def has_target_script_residue(text: str, *, source: str | None = None) -> bool:
    """Return whether target text contains Hangul or unallowed Japanese kana."""
    return has_hangul(text) or has_illegal_japanese_kana(text, source=source)
