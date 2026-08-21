from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from threading import BoundedSemaphore
from typing import Any

from translator.providers.base import (
    BaseProvider,
    build_review_prompt,
    extract_json_object,
    parse_json_object,
    parse_translation_items,
    provider_block_reason,
    validate_translation_items,
)


def build_prompt(messages: list[dict[str, Any]]) -> str:
    parts = [
        "你是 Novel Translator 的翻译后端。严格遵守 system message 和 user message 中的要求。",
        "只输出 user message 要求的 JSON，不要 Markdown、解释、前后缀或剧情摘要。",
    ]
    for message in messages:
        role = str(message.get("role", "user")).upper()
        content = message.get("content", "")
        if isinstance(content, list):
            content = "\n".join(str(item.get("text", item)) for item in content if isinstance(item, dict))
        parts.append(f"\n--- {role} ---\n{content}")
    return "\n".join(parts)


class AntigravityProvider(BaseProvider):
    """Antigravity (AGY CLI / Gemini) universal provider for translation and review."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        self.agy = str(config.get("agy", "agy"))
        self.model = str(config.get("model", "gemini-3.7-flash"))
        self.effort = str(config.get("effort", "low"))
        self.timeout = int(config.get("timeout", 600))
        concurrency = int(config.get("concurrency", 1))
        self.slots = BoundedSemaphore(max(1, concurrency))

    def _run_agy(self, prompt: str, timeout: int | None = None, schema_path: Path | None = None) -> str:
        eff_timeout = timeout or self.timeout
        executable = shutil.which(self.agy)
        if not executable:
            raise RuntimeError(f"agy executable not found in PATH: {self.agy}")
        output_format = "json" if (schema_path and schema_path.exists()) else "text"
        command = [
            executable,
            "--model",
            self.model,
            "--effort",
            self.effort or "low",
            "--output-format",
            output_format,
            "--print-timeout",
            f"{eff_timeout}s",
            "--input-format",
            "text",
            "--dangerously-skip-permissions",
            "--disable-slash-commands",
        ]
        if schema_path and schema_path.exists():
            command.extend(["--json-schema", str(schema_path)])
        acquired = self.slots.acquire(timeout=eff_timeout)
        if not acquired:
            raise TimeoutError("等待 Antigravity 槽位超时")
        try:
            result = subprocess.run(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=eff_timeout,
                check=False,
            )
        finally:
            self.slots.release()
        if result.returncode != 0:
            raise RuntimeError(f"agy execution failed ({result.returncode}): {result.stderr[-2000:]}")
        return result.stdout

    def health_check(self, timeout: int = 60) -> dict[str, Any]:
        executable = shutil.which(self.agy)
        if not executable:
            return {"name": f"provider:{self.name}", "status": "error", "error": f"agy '{self.agy}' not found in PATH"}
        try:
            output = self._run_agy('只输出严格的 JSON: {"ok": true}', timeout=timeout)
            block = provider_block_reason(output)
            if block:
                return {"name": f"provider:{self.name}", "status": "error", "error": f"blocked: {block}"}
            obj = extract_json_object(output)
            if obj.get("ok") is not True:
                return {"name": f"provider:{self.name}", "status": "error", "error": f"unexpected ping output: {output[:200]}"}
            return {
                "name": f"provider:{self.name}",
                "status": "ok",
                "model": self.model,
            }
        except Exception as exc:
            return {
                "name": f"provider:{self.name}",
                "status": "error",
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
        prompt = (
            "你是 Novel Translator 的日译中翻译后端。\n"
            "严格遵守下面的翻译系统要求和 JSON payload。\n"
            "只输出一个 JSON 对象，格式为 {\"items\":[{\"id\":\"段落ID\",\"text\":\"译文\"}]}。\n"
            "不要输出 Markdown、解释、推理、标题、编号或 JSON 之外的文字。\n"
            f"翻译系统要求：\n{system_prompt}\n\n"
            "JSON payload：\n"
            f"{json.dumps(payload, ensure_ascii=False)}\n\n"
            f"最多输出约 {max_tokens} 个 token；必须覆盖 payload.items 中的全部 ID，保持顺序。"
        )
        try:
            raw = self._run_agy(prompt, timeout=timeout)
        except Exception as exc:
            reason = "content_filter" if "content_filter" in str(exc) else "process"
            return [], {"status": "error", "provider": self.name, "reason": reason, "error": str(exc)}

        block = provider_block_reason(raw)
        if block:
            return [], {"status": "blocked", "provider": self.name, "reason": "content_filter", "raw_response": raw[:2000]}

        common = {"provider": self.name, "raw_response": raw[:4000]}
        try:
            items = parse_translation_items(raw)
        except Exception as exc:
            return [], {**common, "status": "error", "reason": "output_format", "error": str(exc)}

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
        prompt = build_review_prompt(kind, input_payload, schema_path, autonomous)
        raw = self._run_agy(prompt, timeout=timeout, schema_path=schema_path)
        block = provider_block_reason(raw)
        if block:
            raise RuntimeError(f"Antigravity review blocked by content filter: {raw[:1000]}")
        return parse_json_object(raw)
