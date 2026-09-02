from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Callable


REPAIR_RULE_VERSION = "1.0"


@dataclass(frozen=True)
class TranslationRepair:
    rule_id: str
    source_match: str
    target_pattern: str
    replacement: str
    count: int
    target_match: str = ""
    target_start: int = -1
    target_end: int = -1


@dataclass(frozen=True)
class _RepairRule:
    rule_id: str
    source_pattern: re.Pattern[str]
    target_pattern: re.Pattern[str]
    replacement: str | Callable[[re.Match[str]], str]


_QUOTE = r"(?:‘の’|“の”|「の」|『の』|'の'|\"の\"|＂の＂|﹁の﹂|﹃の﹄|〝の〞)"


def _idiom_replacement(match: re.Match[str]) -> str:
    lead = str(match.group("lead") or "")
    return f"{lead}画圈"


REPAIR_RULES: tuple[_RepairRule, ...] = (
    _RepairRule(
        "jp_idiom_nonliteral_001",
        re.compile(r"(?:のの字|「の」の字)\s*を\s*(?:書|描|えが|画)く"),
        re.compile(
            rf"(?P<lead>像|仿佛|如同)?\s*(?:写|画|描)(?:着|了|成|作|出|地)?\s*{_QUOTE}(?:字形?|字样?|文字?)"
        ),
        _idiom_replacement,
    ),
    # Former review-time visual-shape micro-repairs, now source-triggered and
    # shared by translation and review recovery.
    _RepairRule(
        "jp_shape_kono_001",
        re.compile(r"コの字(?:形)?"),
        re.compile(r"[“\"「]?コ[”\"」]?の?字形?"),
        "“凹”字形",
    ),
    _RepairRule(
        "jp_shape_ro_001",
        re.compile(r"ロの字(?:形)?"),
        re.compile(r"[“\"「]?ロ[”\"」]?の?字形?"),
        "“回”字形",
    ),
    _RepairRule(
        "jp_shape_kuno_001",
        re.compile(r"くの字(?:形)?"),
        re.compile(r"[“\"「]?く[”\"」]?の?字形?"),
        "“折线”字形",
    ),
    _RepairRule(
        "jp_shape_he_001",
        re.compile(r"ヘの字(?:形)?"),
        re.compile(r"[“\"「]?ヘ[”\"」]?の?字形?"),
        "“倒V”字形",
    ),
    _RepairRule(
        "jp_shape_hachi_001",
        re.compile(r"八の字(?:形)?"),
        re.compile(r"[“\"「]?八[”\"」]?の?字形?"),
        "“八”字形",
    ),
    _RepairRule(
        "jp_shape_tei_001",
        re.compile(r"丁の字(?:形)?"),
        re.compile(r"[“\"「]?丁[”\"」]?の?字形?"),
        "“丁”字形",
    ),
)


def apply_deterministic_repairs(
    *, source: str, translated: str
) -> tuple[str, tuple[TranslationRepair, ...]]:
    """Apply source-triggered, narrowly matched, idempotent repairs."""
    if not source or not translated:
        return translated, ()

    normalized_source = unicodedata.normalize("NFKC", source)
    current = translated
    records: list[TranslationRepair] = []
    for rule in REPAIR_RULES:
        source_match = rule.source_pattern.search(normalized_source)
        if source_match is None:
            continue

        if callable(rule.replacement):
            replacement_function = rule.replacement

            def replace(match: re.Match[str]) -> str:
                return replacement_function(match)

            record_replacement = "画圈"
        else:
            record_replacement = rule.replacement

            def replace(match: re.Match[str]) -> str:
                return record_replacement

        first_target_match = rule.target_pattern.search(current)
        updated, count = rule.target_pattern.subn(replace, current)
        if not count or updated == current:
            continue
        if not record_replacement:
            record_replacement = updated
        records.append(
            TranslationRepair(
                rule_id=rule.rule_id,
                source_match=source_match.group(0),
                target_pattern=rule.target_pattern.pattern,
                replacement=record_replacement,
                count=count,
                target_match=first_target_match.group(0) if first_target_match else "",
                target_start=first_target_match.start() if first_target_match else -1,
                target_end=first_target_match.end() if first_target_match else -1,
            )
        )
        current = updated
    return current, tuple(records)


__all__ = [
    "REPAIR_RULES",
    "REPAIR_RULE_VERSION",
    "TranslationRepair",
    "apply_deterministic_repairs",
]
