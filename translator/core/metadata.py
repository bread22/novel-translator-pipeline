from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
from typing import Any

from translator.core.config import load_config
from translator.core.workspace import BookWorkspace, read_json, write_json
from translator.providers.registry import get_provider
from translator.review.reviewer import has_japanese_kana

logger = logging.getLogger("translator.metadata")

ROOT = Path(__file__).resolve().parents[2]
METADATA_SCHEMA = ROOT / "schemas" / "book-metadata.schema.json"

INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
PUBLISHER_KEYWORDS = (
    "文庫", "書院", "ノベル", "comics", "出版", "ブックス",
    "社", "レーベル", "特別編集", "全集", "コミック", "magazine", "文库",
)


def sanitize_epub_filename(title_zh: str, author_zh: str = "", max_length: int = 120) -> str:
    """Generate a clean, cross-platform filesystem safe filename in <Title> - <Author>.epub format."""
    clean_title = INVALID_FILENAME_CHARS.sub(" ", title_zh).strip()
    clean_author = INVALID_FILENAME_CHARS.sub(" ", author_zh).strip()

    # Normalize whitespace
    clean_title = re.sub(r"\s+", " ", clean_title)
    clean_author = re.sub(r"\s+", " ", clean_author)

    if clean_author and clean_author != "佚名":
        base_name = f"{clean_title} - {clean_author}"
    else:
        base_name = clean_title or "未命名作品"

    if len(base_name) > max_length:
        base_name = base_name[:max_length].rstrip()

    return f"{base_name}.epub"


def heuristic_extract_metadata(raw_title: str, sample_text: str = "") -> dict[str, str]:
    """Fallback rule-based extractor to cleanly parse title and author from Japanese raw strings."""
    text = re.sub(
        r"\s*[\(\[](?:z-library|1lib|z-lib|annas-archive|epub|txt)[^\)\]]*[\)\]]",
        "",
        raw_title,
        flags=re.IGNORECASE,
    ).strip()

    groups = re.findall(r"[\(\[]([^\)\]]+)[\)\]]", text)
    author = ""
    for g in reversed(groups):
        g_clean = g.strip()
        if not any(pk in g_clean.lower() for pk in PUBLISHER_KEYWORDS):
            # Strip inner array notation like [竜也, 高]
            clean_g = re.sub(r"\[.*?\]", "", g_clean).strip().strip(",")
            if clean_g:
                author = clean_g
                break

    # Strip parenthesized tokens to leave main title
    main_title = re.sub(r"\s*[\(\[][^\)\]]+[\)\]]", "", text).strip()
    if not main_title:
        main_title = text

    clean_author = author or "佚名"
    return {
        "title_zh": main_title,
        "title_ja": main_title,
        "author_zh": clean_author,
        "author_ja": author,
        "description": f"《{main_title}》是作者 {clean_author} 创作的作品，由 Novel Translator Studio 自动翻译与排版编排。",
    }


def extract_book_metadata(
    book_id: str,
    manifest: dict[str, Any],
    workspace: BookWorkspace,
    *,
    primary_provider: str | None = None,
    fallback_providers: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Extract and generate high-quality publication metadata and synopsis using LLM with heuristic fallbacks."""
    # 1. Check existing cached metadata
    if workspace.book_metadata_path.exists():
        cached = read_json(workspace.book_metadata_path, default=None)
        if isinstance(cached, dict) and cached.get("title_zh") and cached.get("description"):
            return cached

    cfg = config or load_config()
    roles = cfg.get("roles", {})
    p_name = primary_provider or roles.get("primary_translator")
    f_names = fallback_providers or roles.get("fallback_translators", [])

    candidates: list[str] = []
    if p_name:
        candidates.append(p_name)
    if isinstance(f_names, list):
        for fn in f_names:
            if fn and fn not in candidates:
                candidates.append(fn)

    # 2. Gather context
    raw_title = str(manifest.get("title", book_id))
    sample_paragraphs: list[str] = []
    for ch in manifest.get("chapters", [])[:2]:
        for p in ch.get("paragraphs", [])[:8]:
            tr = str(p.get("translated", "")).strip()
            src = str(p.get("source", "")).strip()
            if tr:
                sample_paragraphs.append(tr)
            elif src:
                sample_paragraphs.append(src)

    book_memory = read_json(workspace.book_memory_path, default={})
    summaries: list[str] = []
    if workspace.chapter_states_dir.exists():
        for sf in sorted(workspace.chapter_states_dir.glob("*.json"))[:5]:
            st = read_json(sf, default={})
            if isinstance(st, dict) and st.get("summary"):
                summaries.append(str(st["summary"]))

    characters = [c.get("name", "") for c in book_memory.get("characters", []) if isinstance(c, dict) and c.get("name")][:6]
    world_settings = [w.get("term", "") for w in book_memory.get("world_settings", []) if isinstance(w, dict) and w.get("term")][:6]

    input_payload = {
        "raw_title": raw_title,
        "sample_paragraphs": sample_paragraphs[:6],
        "chapter_summaries": summaries,
        "characters": characters,
        "world_settings": world_settings,
    }

    # 3. Try LLM extraction
    for candidate_name in candidates:
        try:
            adapter = get_provider(candidate_name, cfg)
            result = adapter.review(
                kind="metadata",
                input_payload=input_payload,
                schema_path=METADATA_SCHEMA,
                autonomous=True,
                timeout=60,
            )
            if isinstance(result, dict) and result.get("title_zh") and result.get("description"):
                title_zh = str(result["title_zh"]).strip()
                desc = str(result["description"]).strip()
                author_zh = str(result.get("author_zh", "")).strip() or "佚名"
                author_ja = str(result.get("author_ja", "")).strip()
                title_ja = str(result.get("title_ja", "")).strip() or raw_title

                # Quality checks: Ensure title and description do not contain unhandled Japanese kana
                if not has_japanese_kana(title_zh) and not has_japanese_kana(desc):
                    meta_result = {
                        "title_zh": title_zh,
                        "title_ja": title_ja,
                        "author_zh": author_zh,
                        "author_ja": author_ja,
                        "description": desc,
                    }
                    write_json(workspace.book_metadata_path, meta_result)
                    return meta_result
        except Exception as exc:
            logger.warning("LLM provider %s metadata extraction failed: %s", candidate_name, exc)

    # 4. Fallback to heuristic parsing
    logger.info("Falling back to heuristic metadata extraction for %s", raw_title)
    meta_result = heuristic_extract_metadata(raw_title, sample_text="\n".join(sample_paragraphs[:3]))
    write_json(workspace.book_metadata_path, meta_result)
    return meta_result
