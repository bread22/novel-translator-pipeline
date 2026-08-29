from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from translator.providers.base import (
    BaseProvider,
    build_review_prompt,
    extract_json_object,
    parse_translation_items,
    provider_block_reason,
    validate_translation_items,
)
from translator.providers.errors import (
    ProviderConnectionError,
    ProviderHTTPError,
    ProviderResponseError,
    ProviderTimeoutError,
)


def _response_excerpt(raw: str, limit: int = 1000) -> str:
    excerpt = raw[:limit]
    excerpt = re.sub(r"(?i)bearer\s+[a-z0-9._~+/=-]+", "Bearer [REDACTED]", excerpt)
    excerpt = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "sk-[REDACTED]", excerpt)
    return re.sub(
        r'(?i)(["\']?(?:api[_-]?key|token|secret|password)["\']?\s*[:=]\s*["\']?)[^"\'\s,}]+',
        r"\1[REDACTED]",
        excerpt,
    )


def _retry_after_seconds(headers: Any) -> float | None:
    value = headers.get("Retry-After") if headers is not None and hasattr(headers, "get") else None
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(str(value))
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _load_json_from_text(text: str) -> dict[str, Any]:
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        return {}


def _plain_single_translation(content: str, requested: list[dict[str, Any]]) -> list[dict[str, str]] | None:
    if len(requested) != 1:
        return None
    text = str(content).strip()
    if not text or text.startswith("{") or text.startswith("["):
        return None
    source = str(requested[0].get("text", ""))
    if len(text) > max(512, len(source) * 6 + 256):
        return None
    return [{"id": str(requested[0].get("id", "")), "text": text}]


def _estimate_input_tokens(system_prompt: str, payload: dict[str, Any]) -> int:
    user_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return len(system_prompt) + len(user_json) + 256


class OpenAIProvider(BaseProvider):
    """Universal OpenAI-compatible HTTP provider for online APIs and local servers (LM Studio, Ollama, DeepSeek, etc.)."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        self.base_url = str(config.get("base_url", "http://127.0.0.1:1234/v1")).rstrip("/")
        self.model = str(config.get("model", ""))
        self.raw_key = str(config.get("api_key", "")).strip()
        if self.raw_key.startswith("$"):
            env_var = self.raw_key[1:]
            self.api_key = os.environ.get(env_var, "")
            if not self.api_key:
                from translator.core.config import _load_dotenv
                _load_dotenv(override=True)
                self.api_key = os.environ.get(env_var, "")
        elif self.raw_key and self.raw_key not in {"sk-...", "lm-studio"}:
            self.api_key = self.raw_key
        else:
            provider_env = f"{name.upper()}_API_KEY"
            if provider_env in os.environ:
                self.api_key = os.environ[provider_env]
            elif "gemini" in name.lower() or "googleapis.com" in self.base_url:
                self.api_key = os.environ.get("GEMINI_API_KEY", self.raw_key or "lm-studio")
            elif "nvidia" in name.lower() or "nvidia.com" in self.base_url:
                self.api_key = os.environ.get("NVIDIA_API_KEY", self.raw_key or "lm-studio")
            elif "deepseek" in name.lower() or "deepseek.com" in self.base_url:
                self.api_key = os.environ.get("DEEPSEEK_API_KEY", self.raw_key or "lm-studio")
            else:
                self.api_key = os.environ.get("OPENAI_API_KEY", self.raw_key or "lm-studio")
        self.context_tokens = int(config.get("context_tokens", 8192))
        self.timeout = int(config.get("timeout", 600))
        self.headers = dict(config.get("headers", {}))
        self.chat_template_kwargs = dict(config.get("chat_template_kwargs", {}))
        self.extra_body = dict(config.get("extra_body", {}))
        if "thinking" in config:
            is_thinking = bool(config["thinking"])
            self.chat_template_kwargs["thinking"] = is_thinking
            self.chat_template_kwargs["enable_thinking"] = is_thinking

    def _make_headers(self) -> dict[str, str]:
        # Always check fresh os.environ if env var is configured
        if self.raw_key and self.raw_key.startswith("$"):
            self.api_key = os.environ.get(self.raw_key[1:], self.api_key)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        headers.update(self.headers)
        return headers

    def _post_chat(self, body_data: dict[str, Any], timeout: int) -> tuple[str, int, dict[str, Any] | None]:
        body = json.dumps(body_data, ensure_ascii=False).encode("utf-8")
        req = Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers=self._make_headers(),
            method="POST",
        )
        with urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
        return raw, status, None

    def health_check(self, timeout: int = 60) -> dict[str, Any]:
        if self.raw_key and self.raw_key.startswith("$"):
            self.api_key = os.environ.get(self.raw_key[1:], "")
        is_local = "127.0.0.1" in self.base_url or "localhost" in self.base_url
        if not self.api_key and not is_local:
            return {
                "name": f"provider:{self.name}",
                "status": "error",
                "base_url": self.base_url,
                "model": self.model,
                "error": "未配置有效 API Key (请在下方填入密钥并点击保存)",
            }
        payload = {
            "items": [{"id": "__healthcheck__", "text": "テスト"}],
        }
        system_prompt = (
            "你是专业日译中小说翻译。"
            "只输出合规 JSON 对象，格式为 {\"items\":[{\"id\":\"__healthcheck__\",\"text\":\"译文\"}]}。"
            "不要输出 Markdown、解释或 JSON 之外的文字。"
        )
        effective_timeout = max(timeout, 120) if not is_local else timeout
        try:
            items, result = self.translate(payload, system_prompt, max_tokens=512, timeout=effective_timeout)
            if result.get("status") != "ok" or len(items) != 1 or items[0].get("id") != "__healthcheck__":
                http_status = result.get("http_status")
                raw_resp = result.get("raw_response", "")
                if http_status:
                    err = f"HTTP {http_status}: {raw_resp[:200]}"
                else:
                    err = result.get("error") or result.get("reason") or "healthcheck response invalid"
                return {
                    "name": f"provider:{self.name}",
                    "status": "error",
                    "base_url": self.base_url,
                    "model": self.model,
                    "error": str(err)[:800],
                }
            return {
                "name": f"provider:{self.name}",
                "status": "ok",
                "base_url": self.base_url,
                "model": self.model,
            }
        except Exception as exc:
            return {
                "name": f"provider:{self.name}",
                "status": "error",
                "base_url": self.base_url,
                "model": self.model,
                "error": str(exc)[:800],
            }

    def translate(
        self,
        payload: dict[str, Any],
        system_prompt: str,
        max_tokens: int,
        timeout: int | None = None,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        effective_timeout = timeout or self.timeout
        requested = payload.get("items", []) if isinstance(payload, dict) else []
        source_chars = sum(len(str(item.get("source", "") or item.get("text", ""))) for item in requested if isinstance(item, dict))
        if self.context_tokens < 16384:
            effective_max_tokens = min(max_tokens, max(512, source_chars * 4 + 256))
        else:
            effective_max_tokens = min(max_tokens, max(2048, source_chars * 6 + 1024))

        # Check local context window limit if bounded
        if self.context_tokens < 65536:
            estimated_input = _estimate_input_tokens(system_prompt, payload)
            available_output = self.context_tokens - estimated_input - 128
            if available_output < 256:
                return [], {
                    "status": "error",
                    "provider": self.name,
                    "reason": "context_overflow",
                    "error": f"输入预估 token ({estimated_input}) 接近上限 ({self.context_tokens})",
                }
            effective_max_tokens = min(effective_max_tokens, available_output)

        request_payload: dict[str, Any] = payload
        if len(requested) == 1 and self.context_tokens < 16384:
            request_payload = {
                "source_language": "auto",
                "target_language": "zh-Hans",
                "instructions": ["只翻译下面这一项 source；不要翻译上下文，不要添加标题、注释或说明。"],
                "items": requested,
            }

        system_instruction = (
            "你是 Novel Translator 的专业日译中翻译后端。\n"
            "只输出合规的单个 JSON 对象，格式严格为：{\"items\":[{\"id\":\"段落ID\",\"text\":\"译文\"}]}。\n"
            "不要输出任何 Markdown、解释、说明、标题或 JSON 之外的文字。\n"
            f"翻译系统规范：\n{system_prompt}\n\n"
            "必须覆盖输入 payload items 中的全部 ID，并严格保持对应顺序。"
        )

        body_data: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": json.dumps(request_payload, ensure_ascii=False)},
            ],
            "temperature": 0.3,
            "max_tokens": effective_max_tokens,
            "response_format": {"type": "json_object"},
        }
        if self.chat_template_kwargs:
            body_data["chat_template_kwargs"] = self.chat_template_kwargs
        if self.extra_body:
            body_data.update(self.extra_body)

        try:
            raw, status, _ = self._post_chat(body_data, effective_timeout)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            block_marker = provider_block_reason(raw)
            reason = block_marker or "http_error"
            return [], {
                "status": "blocked" if reason == "content_filter" else "error",
                "provider": self.name,
                "reason": reason,
                "http_status": exc.code,
                "raw_response": raw[:4000],
            }
        except (URLError, TimeoutError, OSError) as exc:
            return [], {
                "status": "error",
                "provider": self.name,
                "reason": "network",
                "error": str(exc),
            }

        response = _load_json_from_text(raw)
        choices = response.get("choices", []) if isinstance(response, dict) else []
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        finish_reason = str(choice.get("finish_reason", "")).casefold() or None
        content = choice.get("message", {}).get("content", "") if isinstance(choice.get("message"), dict) else ""
        if not content and isinstance(choice.get("message"), dict):
            content = str(choice.get("message", {}).get("reasoning_content", "") or "")
        if isinstance(content, dict):
            content = json.dumps(content, ensure_ascii=False)

        common = {
            "provider": self.name,
            "http_status": status,
            "finish_reason": finish_reason,
            "raw_response": raw[:4000],
        }

        if finish_reason == "length":
            return [], {**common, "status": "error", "reason": "output_format", "error": "翻译响应达到 max_tokens 截断"}
        if finish_reason == "content_filter" or provider_block_reason(str(content)):
            return [], {**common, "status": "blocked", "reason": "content_filter"}

        # Try parsing structured translation items
        try:
            items = parse_translation_items(content)
        except (ValueError, TypeError, json.JSONDecodeError):
            fallback_items = _plain_single_translation(content, requested)
            if fallback_items:
                return fallback_items, {**common, "status": "ok", "format": "single_plain_text"}
            return [], {**common, "status": "error", "reason": "output_format", "error": "翻译响应未包含有效 items 数组"}

        validation = validate_translation_items(items, payload)
        if validation:
            return [], {
                **common,
                "status": "error",
                "reason": "output_format",
                "error": "翻译响应未通过完整性校验",
                "validation": validation,
            }

        return items, {**common, "status": "ok"}

    def review(
        self,
        kind: str,
        input_payload: dict[str, Any],
        schema_path: Path,
        autonomous: bool = False,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        effective_timeout = timeout or self.timeout
        prompt = build_review_prompt(kind, input_payload, schema_path, autonomous)
        system_content = (
            "你是本书 Knowledge Extractor。严格按输入的固定提示词和 JSON Schema 提取临时上下文、长期候选或最终动作；不要输出 Markdown 或额外文字。"
            if kind in {"knowledge_window", "knowledge_finalize"}
            else "你是资深日译中小说语义审阅专家。只检查译文相对于原文的客观错误，并严格输出本次 kind 指定的 JSON 对象；不要输出知识库、记忆或章节状态字段，不要输出 Markdown 或额外文字。"
        )
        body_data: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_content,
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": float(self.config.get("temperature", 0.2)),
        }
        if self.config.get("max_output_tokens"):
            body_data["max_tokens"] = int(self.config["max_output_tokens"])
        if self.chat_template_kwargs:
            body_data["chat_template_kwargs"] = self.chat_template_kwargs
        if self.extra_body:
            body_data.update(self.extra_body)
        try:
            raw, status, _ = self._post_chat(body_data, effective_timeout)
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            excerpt = _response_excerpt(raw)
            raise ProviderHTTPError(
                f"{self.name} review HTTP error {exc.code}: {excerpt}",
                provider=self.name,
                status_code=int(exc.code),
                response_excerpt=excerpt,
                retry_after_seconds=_retry_after_seconds(exc.headers),
            ) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ProviderTimeoutError(
                f"{self.name} review request timed out after {effective_timeout}s: {exc}",
                provider=self.name,
                timeout_seconds=effective_timeout,
            ) from exc
        except URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise ProviderTimeoutError(
                    f"{self.name} review request timed out after {effective_timeout}s: {exc.reason}",
                    provider=self.name,
                    timeout_seconds=effective_timeout,
                ) from exc
            raise ProviderConnectionError(
                f"{self.name} review connection failed: {exc.reason}", provider=self.name
            ) from exc
        except (ConnectionError, OSError) as exc:
            raise ProviderConnectionError(
                f"{self.name} review connection failed: {exc}", provider=self.name
            ) from exc

        response = _load_json_from_text(raw)
        choices = response.get("choices", []) if isinstance(response, dict) else []
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        content = choice.get("message", {}).get("content", "") if isinstance(choice.get("message"), dict) else ""
        if isinstance(content, dict):
            return content
        try:
            return extract_json_object(content or raw)
        except ValueError as exc:
            raise ProviderResponseError(f"{self.name} review response is invalid: {exc}", provider=self.name) from exc
