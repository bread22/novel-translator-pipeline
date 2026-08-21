from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from translator.providers.base import (
    BaseProvider,
    build_review_prompt,
    parse_translation_items,
    provider_block_reason,
    validate_translation_items,
)


ROOT = Path(__file__).resolve().parents[2]


class CodexProvider(BaseProvider):
    """Codex CLI universal provider for translation and review."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        self.binary = str(config.get("binary", "codex"))
        self.model = str(config.get("model", ""))
        self.reasoning_effort = str(config.get("reasoning_effort", ""))
        self.timeout = int(config.get("timeout", 600))

    def _executable(self) -> str:
        exe = shutil.which(self.binary)
        if not exe:
            raise RuntimeError(f"codex executable not found in PATH: {self.binary}")
        return exe

    def health_check(self, timeout: int = 60) -> dict[str, Any]:
        executable = shutil.which(self.binary)
        if not executable:
            return {"name": f"provider:{self.name}", "status": "error", "error": "codex executable not found in PATH"}
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        try:
            with tempfile.TemporaryDirectory(prefix="codex-health-") as temporary:
                root = Path(temporary)
                schema_path = root / "schema.json"
                output_path = root / "result.json"
                schema_path.write_text(json.dumps(schema), encoding="utf-8")
                command = [
                    executable,
                    "exec",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--sandbox",
                    "read-only",
                ]
                if self.model:
                    command.extend(["--model", self.model])
                if self.reasoning_effort:
                    command.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
                command.extend([
                    "--output-schema", str(schema_path),
                    "-o", str(output_path),
                    "-C", str(ROOT),
                    'Return exactly {"ok":true}. Do not include any other fields or text.',
                ])
                result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)
                if result.returncode != 0:
                    return {
                        "name": f"provider:{self.name}",
                        "status": "error",
                        "error": f"codex exited {result.returncode}: {(result.stderr or result.stdout)[-600:]}",
                    }
                try:
                    payload = json.loads(output_path.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError) as exc:
                    return {"name": f"provider:{self.name}", "status": "error", "error": f"invalid health response: {exc}"}
                if not isinstance(payload, dict) or payload.get("ok") is not True:
                    return {"name": f"provider:{self.name}", "status": "error", "error": f"unexpected health response: {payload!r}"}
        except subprocess.TimeoutExpired:
            return {"name": f"provider:{self.name}", "status": "error", "error": f"codex health check timed out after {timeout}s"}
        except OSError as exc:
            return {"name": f"provider:{self.name}", "status": "error", "error": str(exc)}
        return {"name": f"provider:{self.name}", "status": "ok", "model": self.model or "(default)"}

    def translate(
        self,
        payload: dict[str, Any],
        system_prompt: str,
        max_tokens: int,
        timeout: int | None = None,
    ) -> tuple[list[dict[str, str]], dict[str, Any]]:
        translation_schema = {
            "type": "object",
            "required": ["items"],
            "additionalProperties": False,
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["id", "text"],
                        "properties": {
                            "id": {"type": "string"},
                            "text": {"type": "string"},
                        },
                    },
                },
            },
        }
        prompt = (
            "你是日译中小说翻译专家。严格将输入的 JSON payload 中每个 source 翻译为中文。\n"
            f"翻译要求：\n{system_prompt}\n\n"
            f"JSON payload：\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            "必须覆盖 payload.items 中的每个 id。"
        )
        try:
            with tempfile.TemporaryDirectory(prefix="codex-trans-") as temp_dir:
                root = Path(temp_dir)
                schema_path = root / "schema.json"
                output_path = root / "result.json"
                schema_path.write_text(json.dumps(translation_schema), encoding="utf-8")
                command = [
                    self._executable(),
                    "exec",
                    "--ephemeral",
                    "--skip-git-repo-check",
                    "--sandbox",
                    "read-only",
                ]
                if self.model:
                    command.extend(["--model", self.model])
                if self.reasoning_effort:
                    command.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
                command.extend([
                    "--output-schema", str(schema_path),
                    "-o", str(output_path),
                    "-C", str(ROOT),
                    prompt,
                ])
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    timeout=timeout or self.timeout,
                    check=False,
                )
                if result.returncode != 0:
                    combined = f"{result.stderr}\n{result.stdout}"
                    block = provider_block_reason(combined)
                    reason = "content_filter" if block else "process"
                    return [], {
                        "status": "blocked" if reason == "content_filter" else "error",
                        "provider": self.name,
                        "reason": reason,
                        "error": combined[-1000:],
                    }
                content = output_path.read_text(encoding="utf-8")
        except Exception as exc:
            return [], {"status": "error", "provider": self.name, "reason": "process", "error": str(exc)}

        common = {"provider": self.name, "raw_response": content[:4000]}
        try:
            items = parse_translation_items(content)
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
        with tempfile.TemporaryDirectory(prefix="codex-rev-") as temp_dir:
            root = Path(temp_dir)
            output_path = root / "review_output.json"
            command = [
                self._executable(),
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
            ]
            if self.model:
                command.extend(["--model", self.model])
            if self.reasoning_effort:
                command.extend(["-c", f'model_reasoning_effort="{self.reasoning_effort}"'])
            command.extend([
                "--output-schema", str(schema_path),
                "-o", str(output_path),
                "-C", str(ROOT),
                prompt,
            ])
            result = subprocess.run(
                command,
                cwd=ROOT,
                text=True,
                capture_output=True,
                timeout=timeout or self.timeout,
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Codex review failed ({result.returncode}):\n{result.stderr}\n{result.stdout}")
            try:
                return json.loads(output_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"Codex review produced invalid output: {exc}") from exc
