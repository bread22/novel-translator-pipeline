from __future__ import annotations

from enum import Enum
from typing import Final, Literal


class CategoryTier(str, Enum):
    DIRECT_ALLOWED = "direct_allowed"
    GATED_ALLOWED = "gated_allowed"
    BLOCKED = "blocked"


DIRECT_ALLOWED: Final[frozenset[str]] = frozenset({
    "person", "author", "named_nonhuman", "work_title", "document_title",
    "location", "facility", "organization", "company", "government_body",
    "group", "brand", "product_model", "vehicle_name", "named_event",
})

GATED_ALLOWED: Final[frozenset[str]] = frozenset({
    "person_alias", "entity_alias", "fixed_person_title", "official_rank",
    "medical_device", "drug_name", "diagnosis_name", "medical_procedure",
    "domain_device", "fictional_species", "fictional_faction", "ability_name",
    "artifact_name", "system_term", "currency_unit", "era_calendar",
})

BLOCKED: Final[frozenset[str]] = frozenset({
    "anatomy", "body_part", "body_fluid", "body_state", "mental_state", "action",
    "generic_technique", "onomatopoeia", "interjection", "adjective", "adverb",
    "descriptive_phrase", "metaphor", "euphemism", "slang", "dialogue_phrase",
    "honorific_generic", "occupation_generic", "kinship_generic", "common_object",
    "clothing_generic", "food_generic", "material_generic", "general_noun",
    "general_medical", "cultural_explanation", "grammar", "pronoun", "number_datetime",
    "translation_variant", "plot_fact", "ocr_uncertain", "unresolved",
})

CATEGORY_VALUES: Final[tuple[str, ...]] = tuple(sorted(DIRECT_ALLOWED | GATED_ALLOWED | BLOCKED))
Category = Literal[
    "person", "author", "named_nonhuman", "work_title", "document_title", "location",
    "facility", "organization", "company", "government_body", "group", "brand",
    "product_model", "vehicle_name", "named_event", "person_alias", "entity_alias",
    "fixed_person_title", "official_rank", "medical_device", "drug_name", "diagnosis_name",
    "medical_procedure", "domain_device", "fictional_species", "fictional_faction",
    "ability_name", "artifact_name", "system_term", "currency_unit", "era_calendar",
    "anatomy", "body_part", "body_fluid", "body_state", "mental_state", "action",
    "generic_technique", "onomatopoeia", "interjection", "adjective", "adverb",
    "descriptive_phrase", "metaphor", "euphemism", "slang", "dialogue_phrase",
    "honorific_generic", "occupation_generic", "kinship_generic", "common_object",
    "clothing_generic", "food_generic", "material_generic", "general_noun",
    "general_medical", "cultural_explanation", "grammar", "pronoun", "number_datetime",
    "translation_variant", "plot_fact", "ocr_uncertain", "unresolved",
]

SOURCE_SCOPES: Final[frozenset[str]] = frozenset({"body", "title", "author", "cover", "front_matter"})
BODY_SOURCE_SCOPE: Final[str] = "body"

# Legacy values are accepted only at the compatibility boundary and are immediately
# rewritten.  They are not part of the v3 category set.
LEGACY_CATEGORY_ALIASES: Final[dict[str, str]] = {
    "character": "person",
    "place": "location",
    "title": "work_title",
    "family_name": "person_alias",
    "medical": "unresolved",
    "item": "unresolved",
    "occupation": "occupation_generic",
    "honorific": "honorific_generic",
    "anatomy": "anatomy",
    "body": "body_part",
    "technique": "generic_technique",
    "term": "unresolved",
    "terminology": "unresolved",
    "other": "unresolved",
    "general": "unresolved",
}


def canonical_category(value: object) -> str:
    category = str(value or "").strip().casefold()
    return LEGACY_CATEGORY_ALIASES.get(category, category)


def category_tier(category: object) -> CategoryTier | None:
    canonical = canonical_category(category)
    if canonical in DIRECT_ALLOWED:
        return CategoryTier.DIRECT_ALLOWED
    if canonical in GATED_ALLOWED:
        return CategoryTier.GATED_ALLOWED
    if canonical in BLOCKED:
        return CategoryTier.BLOCKED
    return None


def is_known_category(category: object) -> bool:
    return category_tier(category) is not None


def has_independent_support(term: object) -> bool:
    """Require recurrence across paragraphs or chapters before activation."""
    if not isinstance(term, dict):
        return False
    evidence = [item for item in term.get("evidence", []) if isinstance(item, dict)]
    paragraphs = {str(item.get("paragraph_id", "")).strip() for item in evidence if item.get("paragraph_id")}
    chapters = {str(item.get("chapter_id", "")).strip() for item in evidence if item.get("chapter_id")}
    return len(paragraphs) >= 2 or len(chapters) >= 2
