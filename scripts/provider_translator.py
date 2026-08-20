from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from scripts.antigravity_backend import extract_json_object
from scripts.config import load_config, setting
from scripts.opencode_backend import OpenCodeError, model_for, run_prompt


def _toml_value(root: Path, section: str, key: str, default: str) -> str:
    path = root / "setting.toml"
    if not path.exists():
        return default
    current = ""
    pattern = re.compile(rf"^{re.escape(key)}\s*=\s*[\"'](.*?)[\"']\s*$")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            continue
        if current == section:
            match = pattern.match(line)
            if match:
                return match.group(1)
    return default


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _extract_placeholders(text: str) -> list[str]:
    return sorted(set(re.findall(r"\{\{[^{}]+\}\}|\[\[[^\]]+\]\]", text)))


def _terms(root: Path, book: str) -> list[dict[str, Any]]:
    raw = _load_json(root / "data" / "books" / book / "terms.json", {})
    items = raw.get("items", raw.get("terms", raw if isinstance(raw, list) else [])) if isinstance(raw, (dict, list)) else []
    return [item for item in items if isinstance(item, dict)]


def _parse_translation(content: str) -> list[dict[str, str]]:
    payload = extract_json_object(content)
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("翻译响应缺少 items 数组")
    result: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("id", "")).strip():
            raise ValueError("翻译响应包含无效 items 项")
        result.append({"id": str(item["id"]).strip(), "text": str(item.get("text", ""))})
    return result


def _normalized_text(text: str) -> str:
    """Normalize line wrapping while keeping content and punctuation intact."""
    return re.sub(r"\s+", "", text.replace("\\n", "\n"))


def _repeated_content(text: str) -> dict[str, Any] | None:
    """Return a diagnostic when a response repeats a substantial line."""
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in text.replace("\\n", "\n").splitlines()
        if line.strip()
    ]
    counts = Counter(line for line in lines if len(line) >= 24)
    for line, count in counts.items():
        if count >= 2:
            return {"kind": "repeated_line", "count": count, "sample": line[:160]}
    return None


def _previous_context_overlap(text: str, payload: dict[str, Any], item_id: str) -> dict[str, Any] | None:
    """Reject a single-item response that copies a whole previous translation."""
    haystack = _normalized_text(text)
    context = payload.get("context", {}) if isinstance(payload, dict) else {}
    previous = context.get("previous", []) if isinstance(context, dict) else []
    for item in previous:
        if not isinstance(item, dict) or str(item.get("id", "")) == item_id:
            continue
        translated = str(item.get("translated", ""))
        candidate = _normalized_text(translated)
        if len(candidate) >= 48 and candidate in haystack:
            return {
                "kind": "previous_context_overlap",
                "source_id": str(item.get("id", "")),
                "sample": translated[:160],
            }
    return None


def _validate_translation_items(items: list[dict[str, str]], payload: dict[str, Any]) -> dict[str, Any] | None:
    """Guard the manifest against truncation, repetition, and context feedback."""
    requested = payload.get("items", []) if isinstance(payload, dict) else []
    sources = {
        str(item.get("id", "")): str(item.get("text", ""))
        for item in requested
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }
    for item in items:
        item_id = str(item.get("id", "")).strip()
        text = str(item.get("text", "")).strip()
        if not text:
            return {"kind": "empty_translation", "id": item_id}
        source = sources.get(item_id, "")
        # A single translated paragraph may expand, but a many-fold expansion
        # with a fixed floor is a strong signal that adjacent context leaked in.
        max_chars = max(512, len(source) * 6 + 256)
        if len(text) > max_chars:
            return {
                "kind": "output_too_long",
                "id": item_id,
                "text_chars": len(text),
                "source_chars": len(source),
                "max_chars": max_chars,
            }
        repeated = _repeated_content(text)
        if repeated:
            return {"id": item_id, **repeated}
        overlap = _previous_context_overlap(text, payload, item_id)
        if overlap:
            return {"id": item_id, **overlap}
    return None


def _plain_single_translation(content: str, requested: list[dict[str, Any]]) -> list[dict[str, str]] | None:
    """Accept a bounded plain-text answer from LM Studio for one fallback item.

    Some local models follow the translation instruction but omit the JSON
    wrapper on sensitive single-paragraph retries.  The caller still runs the
    normal length/repetition/context validation before writing the result.
    """
    if len(requested) != 1:
        return None
    text = str(content).strip()
    if not text or text.startswith("{") or text.startswith("["):
        return None
    source = str(requested[0].get("text", ""))
    if len(text) > max(512, len(source) * 6 + 256):
        return None
    return [{"id": str(requested[0].get("id", "")), "text": text}]


def _estimate_local_input_tokens(system_prompt: str, payload: dict[str, Any]) -> int:
    """Conservative preflight estimate for an LM Studio chat request.

    Japanese text can approach one token per character.  Counting characters
    rather than relying on a provider-specific tokenizer prevents an 8192-token
    rejection before the HTTP request is sent.
    """
    user_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return len(system_prompt) + len(user_json) + 256


class ProviderTranslator:
    """Direct provider adapter; Novel Translator remains a storage/export tool."""

    def __init__(self, *, novel_root: Path, manifest: Path, timeout: int = 600) -> None:
        self.novel_root = novel_root
        self.manifest = manifest
        self.timeout = timeout
        self.config = load_config()

    def _provider_config(self, provider: str) -> tuple[str, str, str]:
        if provider == "antigravity":
            base_url = setting(self.config, "providers.antigravity.base_url", "ANTIGRAVITY_BASE_URL")
            model = setting(self.config, "providers.antigravity.model", "ANTIGRAVITY_MODEL")
            api_key = setting(self.config, "providers.antigravity.api_key", "ANTIGRAVITY_API_KEY")
        elif provider == "lmstudio":
            base_url = setting(self.config, "providers.lmstudio.base_url", "LMSTUDIO_BASE_URL")
            model = setting(self.config, "providers.lmstudio.model", "LMSTUDIO_MODEL")
            api_key = setting(self.config, "providers.lmstudio.api_key", "LMSTUDIO_API_KEY")
        else:
            raise ValueError(f"未知翻译 provider：{provider}")
        return base_url.rstrip("/"), model, api_key

    @staticmethod
    def _health_error(result: dict[str, Any]) -> str:
        parts = [str(result.get(key, "")) for key in ("reason", "error", "validation") if result.get(key)]
        return "; ".join(parts)[:800] or "provider returned an unusable response"

    def health_check(self, provider: str, timeout: int = 60) -> dict[str, Any]:
        """Verify endpoint, configured model, and one real translation-shaped request."""
        if provider == "opencode":
            payload = {
                "source_language": "ja",
                "target_language": "zh-Hans",
                "quality_profile": {"requirements": ["只输出 JSON，不要解释。"]},
                "items": [{"id": "__healthcheck__", "text": "テスト"}],
            }
            items, result = self._request(provider, payload, max_tokens=512, timeout=timeout)
            if result.get("status") != "ok" or len(items) != 1 or items[0].get("id") != "__healthcheck__":
                return {
                    "name": "translator:opencode",
                    "status": "error",
                    "model": model_for("translator") or "(configured default)",
                    "error": self._health_error(result),
                }
            return {
                "name": "translator:opencode",
                "status": "ok",
                "model": model_for("translator") or "(configured default)",
            }
        try:
            base_url, model, api_key = self._provider_config(provider)
            request = Request(
                f"{base_url}/models",
                headers={"Authorization": f"Bearer {api_key}"},
                method="GET",
            )
            with urlopen(request, timeout=timeout) as response:
                models_payload = _load_json_from_text(response.read().decode("utf-8", errors="replace"))
            model_ids = {
                str(item.get("id"))
                for item in models_payload.get("data", [])
                if isinstance(item, dict) and item.get("id")
            }
            if model not in model_ids:
                return {
                    "name": f"translator:{provider}",
                    "status": "error",
                    "base_url": base_url,
                    "model": model,
                    "error": f"configured model not listed; available={sorted(model_ids)}",
                }
            payload = {
                "source_language": "ja",
                "target_language": "zh-Hans",
                "quality_profile": {"requirements": ["只输出 JSON，不要解释。"]},
                "items": [{"id": "__healthcheck__", "text": "テスト"}],
            }
            items, result = self._request(provider, payload, max_tokens=512, timeout=timeout)
            if result.get("status") != "ok" or len(items) != 1 or items[0].get("id") != "__healthcheck__":
                return {
                    "name": f"translator:{provider}",
                    "status": "error",
                    "base_url": base_url,
                    "model": model,
                    "error": self._health_error(result),
                }
            return {
                "name": f"translator:{provider}",
                "status": "ok",
                "base_url": base_url,
                "model": model,
            }
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError) as exc:
            return {"name": f"translator:{provider}", "status": "error", "error": str(exc)[:800]}

    def _system_prompt(self, provider: str) -> str:
        if provider == "lmstudio":
            return (
                "你是备用日中小说翻译器。把用户 payload 中每个 source 翻译成自然、忠实的简体中文。"
                "严格只输出一个 JSON 对象，格式为 {\"items\":[{\"id\":\"段落ID\",\"text\":\"译文\"}]}。"
                "不要输出分析、推理、解释、编号或 JSON 之外的文字；保留段落顺序。"
            )
        path = self.novel_root / "prompts" / "novel_translation_system.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
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
        chapter = next((item for item in manifest.get("chapters", []) if any(str(p.get("id")) == ids[0] for p in item.get("paragraphs", []))), {})
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

    def _request_opencode(
        self,
        payload: dict[str, Any],
        max_tokens: int,
        timeout: int | None = None,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        prompt = (
            "你是 Novel Translator 的日译中翻译后端。\n"
            "严格遵守下面的翻译系统要求和 JSON payload。\n"
            "只输出一个 JSON 对象，格式为 {\"items\":[{\"id\":\"段落ID\",\"text\":\"译文\"}]}。\n"
            "不要输出 Markdown、解释、推理、标题、编号或 JSON 之外的文字。\n"
            f"翻译系统要求：\n{self._system_prompt('opencode')}\n\n"
            "JSON payload：\n"
            f"{json.dumps(payload, ensure_ascii=False)}\n\n"
            f"最多输出约 {max_tokens} 个 token；必须覆盖 payload.items 中的全部 ID，保持顺序。"
        )
        try:
            content = run_prompt(prompt, role="translator", timeout=timeout or self.timeout)
        except OpenCodeError as exc:
            return [], {
                "status": "blocked" if exc.reason == "content_filter" else "error",
                "provider": "opencode",
                "reason": exc.reason,
                "error": str(exc),
            }
        common = {"provider": "opencode", "raw_response": content[:4000]}
        try:
            items = _parse_translation(content)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return [], {**common, "status": "error", "reason": "output_format", "error": str(exc)}
        validation = _validate_translation_items(items, payload)
        if validation:
            return [], {
                **common,
                "status": "error",
                "reason": "output_format",
                "error": "翻译响应未通过完整性校验",
                "validation": validation,
            }
        return items, {**common, "status": "ok"}

    def _request(self, provider: str, payload: dict[str, Any], max_tokens: int, timeout: int | None = None) -> tuple[list[dict[str, str]], dict[str, Any]]:
        if provider == "opencode":
            return self._request_opencode(payload, max_tokens, timeout)
        base_url, model, api_key = self._provider_config(provider)
        requested = payload.get("items", []) if isinstance(payload, dict) else []
        source_chars = sum(
            len(str(item.get("text", "")))
            for item in requested
            if isinstance(item, dict)
        )
        effective_max_tokens = max_tokens
        if provider == "lmstudio":
            # Keep a malformed local response from consuming the whole context
            # window.  Longer source windows still receive a proportional cap.
            effective_max_tokens = min(max_tokens, max(512, source_chars * 4 + 256))
        request_payload: dict[str, Any] = payload
        if provider == "lmstudio" and len(requested) == 1:
            request_payload = {
                "source_language": "auto",
                "target_language": "zh-Hans",
                "instructions": ["只翻译下面这一项 source；不要翻译上下文，不要添加标题、注释或说明。"],
                "items": requested,
            }
        if provider == "lmstudio":
            # LM Studio rejects the request before generation when prompt plus
            # requested output exceeds the model context.  Check the exact
            # message payload before opening the connection and recursively
            # split it into two requests when necessary.
            context_limit = int(setting(self.config, "providers.lmstudio.context_tokens", "LMSTUDIO_CONTEXT_TOKENS"))
            estimated_input = _estimate_local_input_tokens(self._system_prompt(provider), request_payload)
            available_output = context_limit - estimated_input - 128
            if available_output < 512:
                return self._request_local_split(payload, max_tokens, timeout)
            effective_max_tokens = min(effective_max_tokens, available_output)
        body_data: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._system_prompt(provider)},
                {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False)},
            ],
            "temperature": 0.3,
            "max_tokens": effective_max_tokens,
        }
        body = json.dumps(body_data, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = int(response.status)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            text = raw.casefold()
            reason = "content_filter" if any(marker in text for marker in ("provider_blocked", "sensitive words", "content policy", "prohibited use policy")) else "http_error"
            return [], {"status": "blocked" if reason == "content_filter" else "error", "provider": provider, "reason": reason, "http_status": exc.code, "raw_response": raw[:4000]}
        except (URLError, TimeoutError, OSError) as exc:
            return [], {"status": "error", "provider": provider, "reason": "network", "error": str(exc)}
        response = _load_json_from_text(raw)
        choices = response.get("choices", []) if isinstance(response, dict) else []
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        finish_reason = str(choice.get("finish_reason", "")).casefold() or None
        content = choice.get("message", {}).get("content", "") if isinstance(choice.get("message"), dict) else ""
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False)
        common = {
            "provider": provider,
            "http_status": status,
            "finish_reason": finish_reason,
            "raw_response": raw[:4000],
        }
        if finish_reason == "length":
            return [], {
                **common,
                "status": "error",
                "reason": "output_format",
                "error": "翻译响应达到 max_tokens，未接受截断结果",
            }
        if finish_reason == "content_filter":
            return [], {**common, "status": "blocked", "reason": "content_filter"}
        try:
            items = _parse_translation(str(content))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            plain_items = _plain_single_translation(str(content), requested)
            if plain_items:
                validation = _validate_translation_items(plain_items, payload)
                if validation is None:
                    return plain_items, {**common, "status": "ok", "raw_response": str(content)[:1000], "format": "plain_single_item"}
            return [], {**common, "status": "error", "reason": "output_format", "error": str(exc)}
        validation = _validate_translation_items(items, payload)
        if validation:
            return [], {
                **common,
                "status": "error",
                "reason": "output_format",
                "error": "翻译响应未通过完整性校验",
                "validation": validation,
            }
        return items, {**common, "status": "ok", "raw_response": str(content)[:1000]}

    def _request_local_split(
        self,
        payload: dict[str, Any],
        max_tokens: int,
        timeout: int | None,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if not isinstance(items, list) or not items:
            return [], {"status": "error", "provider": "lmstudio", "reason": "input_too_long", "error": "请求内容超过上下文限制且没有可分割项目"}

        if len(items) == 1:
            item = items[0]
            source = str(item.get("text", "")) if isinstance(item, dict) else ""
            if len(source) < 2:
                return [], {"status": "error", "provider": "lmstudio", "reason": "input_too_long", "error": "单个段落无法继续切分"}
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
            first_items, first_result = self._request("lmstudio", first, max_tokens, timeout)
            if first_result.get("status") != "ok":
                return [], {**first_result, "split": "first_half"}
            second_items, second_result = self._request("lmstudio", second, max_tokens, timeout)
            if second_result.get("status") != "ok":
                return [], {**second_result, "split": "second_half"}
            combined = {
                "id": item_id,
                "text": "".join([str(first_items[0]["text"]), str(second_items[0]["text"])])
                if first_items and second_items else "",
            }
            return [combined], {"status": "ok", "provider": "lmstudio", "split": "single_item_halves"}

        midpoint = max(1, len(items) // 2)
        results: list[dict[str, str]] = []
        for label, subset in (("first_half", items[:midpoint]), ("second_half", items[midpoint:])):
            part_payload = dict(payload, items=subset)
            part_items, part_result = self._request("lmstudio", part_payload, max_tokens, timeout)
            if part_result.get("status") != "ok":
                return [], {**part_result, "split": label}
            results.extend(part_items)
        return results, {"status": "ok", "provider": "lmstudio", "split": "item_halves", "parts": 2}

    def __call__(self, provider: str, book: str, ids: list[str], *, source_chars: int, max_tokens: int) -> dict[str, Any]:
        if not ids:
            return {"status": "ok", "provider": provider, "summary": {"translated": 0}}
        payload, sources = self._payload(book, ids)
        items, result = self._request(provider, payload, max_tokens)
        if result.get("status") != "ok":
            return result
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


def _load_json_from_text(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}
