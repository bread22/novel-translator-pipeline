from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EpubConfig:
    """EPUB options consumed by the pipeline's direct book operations."""

    parser: str = "auto"
    include_non_linear_spine: bool = False
    preserve_outer_markup: bool = True
    warn_on_ruby: bool = True
    warn_on_duplicate_source: bool = True
    translate_nav: bool = True
    translate_toc: bool = True
    preserve_inline_tags: bool = True
    inline_safe_tags: tuple[str, ...] = ("span", "strong", "em", "a")
