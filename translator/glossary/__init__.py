"""Deterministic v3 glossary taxonomy, validation, lifecycle and projection."""

from translator.glossary.lifecycle import merge_term_candidates
from translator.glossary.models import GlossaryCandidate, GlossaryDocument, GlossaryTerm, GlossaryV3
from translator.glossary.name_normalizer import NameNormalization, normalize_japanese_name
from translator.glossary.projection import (
    build_translation_term_projection,
    select_relevant_terms,
)
from translator.glossary.taxonomy import (
    BLOCKED,
    DIRECT_ALLOWED,
    GATED_ALLOWED,
    CATEGORY_VALUES,
    canonical_category,
)
from translator.glossary.validation import ValidationResult, validate_term_candidate

__all__ = [
    "BLOCKED",
    "DIRECT_ALLOWED",
    "GATED_ALLOWED",
    "CATEGORY_VALUES",
    "GlossaryCandidate",
    "GlossaryDocument",
    "GlossaryTerm",
    "GlossaryV3",
    "NameNormalization",
    "ValidationResult",
    "build_translation_term_projection",
    "canonical_category",
    "merge_term_candidates",
    "normalize_japanese_name",
    "select_relevant_terms",
    "validate_term_candidate",
]
