from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import tempfile
from typing import Any

from translator.core.config import load_config, setting
from translator.providers.base import (
    extract_json_object,
    normalize_item_ids,
    normalized_text,
    parse_translation_items,
    previous_context_overlap,
    provider_block_reason,
    repeated_content,
    validate_translation_items,
)
from translator.providers.registry import get_provider


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _extract_placeholders(text: str) -> list[str]:
    return sorted(set(re.findall(r"\{\{[^{}]+\}\}|\[\[[^\]]+\]\]", text)))


def _terms(root: Path, book: str) -> list[dict[str, Any]]:
    raw = _load_json(root / "data" / "books" / book / "terms.json", {})
    if isinstance(raw, dict):
        items = raw.get("items", raw.get("terms", []))
    elif isinstance(raw, list):
        items = raw
    else:
        items = []
    if not isinstance(items, list):
        items = []
    return [item for item in items if isinstance(item, dict)]


class ProviderTranslator:
    """Direct provider adapter orchestrator; Novel Translator remains a storage/export tool."""

    def __init__(self, *, novel_root: Path, manifest: Path, timeout: int = 600) -> None:
        self.novel_root = novel_root
        self.manifest = manifest
        self.timeout = timeout
        self.config = load_config()

    def health_check(self, provider: str, timeout: int = 60) -> dict[str, Any]:
        try:
            adapter = get_provider(provider, self.config)
            return adapter.health_check(timeout=timeout)
        except Exception as exc:
            return {"name": f"provider:{provider}", "status": "error", "error": str(exc)[:800]}

    def _system_prompt(self, provider: str) -> str:
        p_cfg = self.config.get("providers", {}).get(provider, {})
        p_type = p_cfg.get("type", provider)
        if provider == "lmstudio" or p_type == "openai" and int(p_cfg.get("context_tokens", 65536)) < 16384:
            return (
                "你是备用日中小说翻译器。把用户 payload 中每个 source 翻译成自然、忠实的简体中文。"
                "严格只输出一个 JSON 对象，格式为 {\"items\":[{\"id\":\"段落ID\",\"text\":\"译文\"}]}。"
                "不要输出分析、推理、解释、编号或 JSON 之外的文字；保留段落顺序。"
            )
        policy_rel = self.config.get("paths", {}).get("translation_policy", "docs/prompts/translation-policy.md")
        path = self.novel_root / policy_rel
        if path.exists():
            return path.read_text(encoding="utf-8")
        fallback_path = self.novel_root / "prompts" / "novel_translation_system.md"
        if fallback_path.exists():
            return fallback_path.read_text(encoding="utf-8")
        return "你是专业小说译者。严格忠实翻译输入内容，只输出 JSON。"

    def _payload(self, book: str, ids: list[str]) -> tuple[dict[str, Any], dict[str, str]]:
        manifest = _load_json(self.manifest, {})
        paragraphs = [p for chapter in manifest.get("chapters", []) for p in chapter.get("paragraphs", [])]
        by_id = {str(item.get("id")): item for item in paragraphs}
        selected = [by_id[item_id] for item_id in ids if item_id in by_id]
        if len(selected) != len(ids):
            missing = sorted(set(ids) - set(by_id))
            raise ValueError(f"manifest 缺少段落：{', '.join(missing)}")
        first_index = next(index for index, item in enumerate(paragraphs) if str(item.get("id")) == ids[0])
        chapter: dict[str, Any] = next(
            (item for item in manifest.get("chapters", []) if any(str(p.get("id")) == ids[0] for p in item.get("paragraphs", []))),
            {},
        )
        previous = paragraphs[max(0, first_index - 3):first_index]
        next_index = first_index + len(selected)
        following = paragraphs[next_index:next_index + 2]
        payload = {
            "source_language": "auto",
            "target_language": "zh-Hans",
            "quality_profile": {
                "style_guide": "自然流畅的简体中文小说译文，忠实原意，避免生硬直译。",
                "dialogue_style": "符合中文小说阅读习惯，称谓、语气和人物关系保持连续。",
                "self_check_passes": 2,
                "requirements": [
                    "忠实保留原文事实、动作顺序、视角、语气和信息量，不总结、不删减、不扩写剧情。",
                    "保留段落边界、数字、标点含义、换行意图、HTML 标签、脚注锚点和所有 placeholders。",
                    "同一批次和上下文中的人名、地名、组织名、技能名、称号和代词指代保持一致。",
                    "只输出最终 JSON，不要解释。",
                ],
            },
            "glossary": _terms(self.novel_root, book),
            "context": {
                "chapter_id": str(chapter.get("id", "")),
                "chapter_title": str(chapter.get("title", "")),
                "previous": [{"id": str(item.get("id")), "source": str(item.get("source", "")), "translated": str(item.get("translated", ""))} for item in previous],
                "next": [{"id": str(item.get("id")), "source": str(item.get("source", ""))} for item in following],
            },
            "items": [
                {"id": str(item["id"]), "text": str(item.get("source", "")), "placeholders": _extract_placeholders(str(item.get("source", "")))}
                for item in selected
            ],
        }
        return payload, {str(item["id"]): str(item.get("source", "")) for item in selected}

    def _request(self, provider: str, payload: dict[str, Any], max_tokens: int, timeout: int | None = None) -> tuple[list[dict[str, str]], dict[str, Any]]:
        adapter = get_provider(provider, self.config)
        system_prompt = self._system_prompt(provider)
        items, result = adapter.translate(payload, system_prompt, max_tokens, timeout=timeout or self.timeout)
        if result.get("status") == "error" and result.get("reason") == "context_overflow":
            return self._request_local_split(provider, payload, max_tokens, timeout)
        return items, result

    def _request_local_split(
        self,
        provider: str,
        payload: dict[str, Any],
        max_tokens: int,
        timeout: int | None,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if not isinstance(items, list) or not items:
            return [], {"status": "error", "provider": provider, "reason": "input_too_long", "error": "请求内容超过限制且没有可分割项目"}

        if len(items) == 1:
            item = items[0]
            source = str(item.get("text", "")) if isinstance(item, dict) else ""
            if len(source) < 2:
                return [], {"status": "error", "provider": provider, "reason": "input_too_long", "error": "单个段落无法继续切分"}
            midpoint = len(source) // 2
            left, right = source[:midpoint], source[midpoint:]
            item_id = str(item.get("id", "item"))
            parts = []
            for suffix, text in (("a", left), ("b", right)):
                part = dict(item)
                part["id"] = f"{item_id}__split_{suffix}"
                part["text"] = text
                parts.append(part)
            first = dict(payload, items=[parts[0]])
            second = dict(payload, items=[parts[1]])
            first_items, first_result = self._request(provider, first, max_tokens, timeout)
            if first_result.get("status") != "ok":
                return [], {**first_result, "split": "first_half"}
            second_items, second_result = self._request(provider, second, max_tokens, timeout)
            if second_result.get("status") != "ok":
                return [], {**second_result, "split": "second_half"}
            combined = {
                "id": item_id,
                "text": "".join([str(first_items[0]["text"]), str(second_items[0]["text"])])
                if first_items and second_items else "",
            }
            return [combined], {"status": "ok", "provider": provider, "split": "single_item_halves"}

        midpoint = max(1, len(items) // 2)
        results: list[dict[str, str]] = []
        for label, subset in (("first_half", items[:midpoint]), ("second_half", items[midpoint:])):
            part_payload = dict(payload, items=subset)
            part_items, part_result = self._request(provider, part_payload, max_tokens, timeout)
            if part_result.get("status") != "ok":
                return [], {**part_result, "split": label}
            results.extend(part_items)
        return results, {"status": "ok", "provider": provider, "split": "item_halves", "parts": 2}

    def __call__(self, provider: str, book: str, ids: list[str], *, source_chars: int, max_tokens: int) -> dict[str, Any]:
        if not ids:
            return {"status": "ok", "provider": provider, "summary": {"translated": 0}}
        payload, sources = self._payload(book, ids)
        items, result = self._request(provider, payload, max_tokens)
        if result.get("status") != "ok":
            return result
        items = normalize_item_ids(items, ids)
        expected = set(ids)
        received_ids = [item["id"] for item in items]
        received = set(received_ids)
        missing = sorted(expected - received)
        unknown = sorted(received - expected)
        duplicate_ids = sorted({item_id for item_id in received_ids if received_ids.count(item_id) > 1})
        if missing or unknown or duplicate_ids:
            return {
                "status": "error",
                "provider": provider,
                "reason": "output_format",
                "missing": missing,
                "unknown": unknown,
                "duplicate_ids": duplicate_ids,
                "raw_response": result.get("raw_response", ""),
            }
        manifest = _load_json(self.manifest, {})
        by_id = {str(item.get("id")): item for chapter in manifest.get("chapters", []) for item in chapter.get("paragraphs", [])}
        for item in items:
            text = item["text"].strip()
            if not text:
                return {"status": "error", "provider": provider, "reason": "empty_translation", "id": item["id"]}
            by_id[item["id"]]["translated"] = text
        self._atomic_write(manifest)
        result["summary"] = {"translated": len(items), "source_chars": sum(len(sources[item_id]) for item_id in ids)}
        return result

    def _atomic_write(self, manifest: dict[str, Any]) -> None:
        temporary = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", dir=self.manifest.parent, delete=False)
        try:
            temporary.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            temporary.close()
            Path(temporary.name).replace(self.manifest)
        finally:
            Path(temporary.name).unlink(missing_ok=True)
