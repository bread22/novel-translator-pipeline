from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from app.models import Paragraph


@dataclass(frozen=True)
class Placeholder:
    value: str
    kind: str


PLACEHOLDER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("double_brace", re.compile(r"\{\{[^{}\n]{1,80}\}\}")),
    ("brace", re.compile(r"\{[^{}\n]{1,80}\}")),
    ("printf", re.compile(r"%(?:\d+\$)?[+#0\- ]?(?:\d+|\*)?(?:\.(?:\d+|\*))?[sdif]")),
    ("html_tag", re.compile(r"</?[A-Za-z][A-Za-z0-9:-]*(?:\s+[^<>]*)?>")),
    ("footnote_anchor", re.compile(r"\[#[-_A-Za-z0-9:.]+\]")),
)


def extract_placeholders(text: str) -> list[Placeholder]:
    found: list[Placeholder] = []
    seen: set[tuple[str, str]] = set()
    occupied: list[range] = []
    for kind, pattern in PLACEHOLDER_PATTERNS:
        for match in pattern.finditer(text):
            span = range(match.start(), match.end())
            if any(_ranges_overlap(span, item) for item in occupied):
                continue
            value = match.group(0)
            key = (kind, value)
            if key in seen:
                continue
            seen.add(key)
            occupied.append(span)
            found.append(Placeholder(value=value, kind=kind))
    return found


def placeholder_payload_for_paragraph(paragraph: Paragraph) -> list[dict[str, str]]:
    return [asdict(item) for item in extract_placeholders(paragraph.source)]


def placeholder_mismatches(paragraph: Paragraph) -> list[dict[str, str]]:
    if not paragraph.translated.strip():
        return []
    missing = []
    for placeholder in extract_placeholders(paragraph.source):
        if placeholder.value not in paragraph.translated:
            missing.append({"id": paragraph.id, "placeholder": placeholder.value, "kind": placeholder.kind})
    return missing


def _ranges_overlap(left: range, right: range) -> bool:
    return left.start < right.stop and right.start < left.stop

