from __future__ import annotations

import re

from app.config import QualityConfig
from app.models import Book
from app.placeholders import placeholder_mismatches
from app.persona import person_address_issues
from app.terminology import Term, relevant_terms_for_text


def quality_report(book: Book, config: QualityConfig, terms: list[Term] | None = None) -> dict:
    untranslated = []
    residual = []
    terminology_mismatch = []
    placeholder_mismatch = []
    epub_markup_risk = []
    style_inconsistency = []
    dialogue_punctuation = []
    over_literal_translation = []
    person_address_issue = []
    review_required = []
    patterns = [re.compile(pattern) for pattern in config.source_residual_patterns]
    glossary = terms or []
    for paragraph in book.paragraphs:
        if not paragraph.translated.strip():
            untranslated.append(paragraph.id)
            continue
        for term in relevant_terms_for_text(glossary, paragraph.source):
            if term.target not in paragraph.translated:
                terminology_mismatch.append(
                    {
                        "id": paragraph.id,
                        "source": term.source,
                        "expected": term.target,
                        "text": paragraph.translated,
                    }
                )
        placeholder_mismatch.extend(
            {**item, "text": paragraph.translated}
            for item in placeholder_mismatches(paragraph)
        )
        epub_meta = paragraph.metadata.get("epub", {})
        risks = epub_meta.get("risks", [])
        if risks:
            epub_markup_risk.append(
                {
                    "id": paragraph.id,
                    "chapter_path": epub_meta.get("chapter_path", ""),
                    "risks": list(risks),
                    "text": paragraph.translated,
                }
            )
        for pattern in patterns:
            if pattern.search(paragraph.translated):
                residual.append({"id": paragraph.id, "pattern": pattern.pattern, "text": paragraph.translated})
                break
        dialogue_reasons = _dialogue_punctuation_issues(paragraph.source, paragraph.translated)
        if dialogue_reasons:
            dialogue_punctuation.append({"id": paragraph.id, "reasons": dialogue_reasons, "text": paragraph.translated})
        literal_reasons = _over_literal_issues(paragraph.source, paragraph.translated)
        if literal_reasons:
            over_literal_translation.append({"id": paragraph.id, "reasons": literal_reasons, "text": paragraph.translated})
        address_issues = person_address_issues(paragraph.source, paragraph.translated)
        if address_issues:
            person_address_issue.append({"id": paragraph.id, "issues": address_issues, "text": paragraph.translated})
        style_reasons = _style_issues(paragraph.translated)
        if style_reasons:
            style_inconsistency.append({"id": paragraph.id, "reasons": style_reasons, "text": paragraph.translated})
    review_ids = set()
    for collection in (residual, terminology_mismatch, placeholder_mismatch, epub_markup_risk, style_inconsistency, dialogue_punctuation, over_literal_translation, person_address_issue):
        for item in collection:
            review_ids.add(item["id"])
    review_required = sorted(review_ids)
    status = "ok" if not untranslated and not residual and not terminology_mismatch and not placeholder_mismatch and not epub_markup_risk and not style_inconsistency and not dialogue_punctuation and not over_literal_translation and not person_address_issue else "warning"
    return {
        "status": status,
        "warnings": [],
        "summary": {
            "chapters": len(book.chapters),
            "paragraphs": len(book.paragraphs),
            "translated": sum(1 for item in book.paragraphs if item.translated.strip()),
            "untranslated": len(untranslated),
            "source_residual": len(residual),
            "terminology_mismatch": len(terminology_mismatch),
            "placeholder_mismatch": len(placeholder_mismatch),
            "epub_markup_risk": len(epub_markup_risk),
            "style_inconsistency": len(style_inconsistency),
            "dialogue_punctuation": len(dialogue_punctuation),
            "over_literal_translation": len(over_literal_translation),
            "person_address_issue": len(person_address_issue),
            "review_required": len(review_required),
        },
        "details": {
            "untranslated": untranslated[:100],
            "source_residual": residual[:100],
            "terminology_mismatch": terminology_mismatch[:100],
            "placeholder_mismatch": placeholder_mismatch[:100],
            "epub_markup_risk": epub_markup_risk[:100],
            "style_inconsistency": style_inconsistency[:100],
            "dialogue_punctuation": dialogue_punctuation[:100],
            "over_literal_translation": over_literal_translation[:100],
            "person_address_issue": person_address_issue[:100],
            "review_required": review_required[:100],
        },
    }


def _dialogue_punctuation_issues(source: str, translated: str) -> list[str]:
    issues = []
    has_dialogue = any(mark in source for mark in ('"', "“", "「", "『"))
    if not has_dialogue:
        return issues
    if '"' in translated:
        issues.append("western_quote_in_dialogue")
    if translated.count("“") != translated.count("”"):
        issues.append("unbalanced_chinese_quotes")
    if translated.count("‘") != translated.count("’"):
        issues.append("unbalanced_single_quotes")
    return issues


def _over_literal_issues(source: str, translated: str) -> list[str]:
    issues = []
    if not source or not translated:
        return issues
    if source.strip() == translated.strip():
        issues.append("identical_to_source")
    source_tokens = _latin_tokens(source)
    translated_tokens = _latin_tokens(translated)
    retained_tokens = sorted(source_tokens & translated_tokens)
    if len(retained_tokens) >= 3:
        issues.append("source_tokens_retained")
    ascii_letters = sum(1 for char in translated if ("A" <= char <= "Z") or ("a" <= char <= "z"))
    if len(translated) > 10 and ascii_letters / max(len(translated), 1) > 0.35:
        issues.append("high_latin_ratio")
    source_chars = len(source.strip())
    translated_chars = len(translated.strip())
    if source_chars >= 80 and translated_chars < max(20, int(source_chars * 0.18)):
        issues.append("suspiciously_short_translation")
    return issues


def _style_issues(translated: str) -> list[str]:
    issues = []
    checks = {
        "double_space": "  " in translated,
        "duplicated_full_stop": "。。" in translated,
        "duplicated_comma": "，，" in translated,
        "western_comma_or_period": "，" not in translated and re.search(r"[A-Za-z\u4e00-\u9fff],[A-Za-z\u4e00-\u9fff]", translated) is not None,
        "western_sentence_period": re.search(r"[\u4e00-\u9fff]\.", translated) is not None,
        "repeated_sentence": _has_repeated_sentence(translated),
    }
    for reason, matched in checks.items():
        if matched:
            issues.append(reason)
    return issues


def _latin_tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text)
        if token.lower() not in {"the", "and", "for", "you", "that", "this", "with", "his", "her"}
    }


def _has_repeated_sentence(text: str) -> bool:
    parts = [item.strip() for item in re.split(r"[。！？!?.]+", text) if item.strip()]
    seen = set()
    for part in parts:
        if len(part) < 4:
            continue
        if part in seen:
            return True
        seen.add(part)
    return False
