from __future__ import annotations

import re
from typing import Any


BAD_ADDRESS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"[\u4e00-\u9fffA-Za-z·・ー]{1,16}桑", "san_transliteration"),
    (r"[\u4e00-\u9fffA-Za-z·・ー]{1,16}酱", "chan_transliteration"),
    (r"[\u4e00-\u9fffA-Za-z·・ー]{1,16}碳", "tan_transliteration"),
    (r"[\u4e00-\u9fffA-Za-z·・ー]{1,16}君", "kun_transliteration"),
    (r"[\u4e00-\u9fffA-Za-z·・ー]{0,16}(?:さん|くん|ちゃん|さま)", "japanese_honorific_residual"),
)
IGNORED_KUN_MATCH_SUFFIXES = ("夫君", "郎君", "诸君", "主君")
IGNORED_CHAN_MATCH_SUFFIXES = ("辣酱", "甜酱", "果酱", "豆瓣酱", "肉酱", "调味酱")

SOURCE_HONORIFIC_PATTERN = re.compile(r"([\u30A1-\u30FA\u30FC一-龥A-Za-z·・ー]{1,24})(さん|くん|君|ちゃん|さま|様|殿|先生|先輩|後輩|氏)")
TARGET_BAD_ADDRESS_RE = re.compile("|".join(f"(?:{pattern})" for pattern, _ in BAD_ADDRESS_PATTERNS))


def person_address_issues(source: str, translated: str) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not translated:
        return issues
    for pattern, code in BAD_ADDRESS_PATTERNS:
        for match in re.finditer(pattern, translated):
            if _is_ignored_address_match(code, match.group(0), translated, match.end()):
                continue
            issues.append({"code": code, "match": match.group(0), "suggestion": suggestion_for_code(code)})
            break
    if SOURCE_HONORIFIC_PATTERN.search(source) and not issues:
        for value in ("桑", "酱", "さん", "くん", "ちゃん", "さま"):
            if value in translated:
                issues.append({"code": "honorific_residual", "match": value, "suggestion": "根据人物关系改为中文称呼或直接省略敬称。"})
                break
    return issues


def terminology_address_warnings(terms: list[Any]) -> list[str]:
    warnings: list[str] = []
    for term in terms:
        target_issue = _target_bad_address_match(term.target) if term.target else None
        if target_issue:
            warnings.append(f"术语 {term.source} 的译名 {term.target} 疑似残留日式敬称音译，请改为中文称呼或去掉敬称")
        if SOURCE_HONORIFIC_PATTERN.search(term.source) and term.target and term.source != term.target:
            if any(value in term.target for value in ("桑", "酱", "さん", "くん", "ちゃん", "さま")):
                warnings.append(f"术语 {term.source} 的译名 {term.target} 保留了日式敬称，请按人物关系处理称呼")
    return warnings


def _target_bad_address_match(target: str) -> tuple[str, str] | None:
    for pattern, code in BAD_ADDRESS_PATTERNS:
        for match in re.finditer(pattern, target):
            if _is_ignored_address_match(code, match.group(0), target, match.end()):
                continue
            return code, match.group(0)
    return None


def _is_ignored_address_match(code: str, value: str, full_text: str, match_end: int) -> bool:
    if code == "kun_transliteration" and value.endswith(IGNORED_KUN_MATCH_SUFFIXES):
        return True
    if code == "kun_transliteration" and full_text[match_end : match_end + 1] == "主":
        return True
    if code == "chan_transliteration" and value.endswith(IGNORED_CHAN_MATCH_SUFFIXES):
        return True
    if code == "tan_transliteration" and full_text[match_end : match_end + 1] == "酸":
        return True
    return False


def suggestion_for_code(code: str) -> str:
    if code == "san_transliteration":
        return "不要把 さん 直译为“桑”；按语境改为直呼姓名、先生、小姐、前辈、老师等中文称呼。"
    if code == "chan_transliteration":
        return "不要把 ちゃん 直译为“酱”；按亲密度改为昵称、小名、直呼姓名或省略。"
    if code == "tan_transliteration":
        return "不要保留 たん/碳 这类音译敬称；按中文亲昵称呼重写。"
    if code == "kun_transliteration":
        return "不要把 くん/君 机械保留为“君”；按语境改为直呼姓名、同伴称呼、前辈/后辈等中文表达。"
    if code == "japanese_honorific_residual":
        return "译文仍含日文敬称，按人物关系改为中文称呼或省略。"
    return "检查人物称呼是否符合中文语境和人物关系。"
