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
    parse_json_object,
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
        self.reasoning_effort = str(config.get("reasoning_effort", "low") or "low").strip() or "low"
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
        try:
            with tempfile.TemporaryDirectory(prefix="codex-health-") as temporary:
                root = Path(temporary)
                output_path = root / "result.json"
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
                    payload = parse_json_object(output_path.read_text(encoding="utf-8"))
                except (FileNotFoundError, ValueError) as exc:
                    return {"name": f"provider:{self.name}", "status": "error", "error": f"invalid health response: {exc}"}
                if payload.get("ok") is not True or set(payload) != {"ok"}:
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
        prompt = (
            "你是日译中小说翻译专家。严格将输入的 JSON payload 中每个 source 翻译为中文。\n"
            "只输出一个 JSON 对象，格式为 {\"items\":[{\"id\":\"段落ID\",\"text\":\"译文\"}]}。\n"
            "每个 items 项只能包含 id 和 text，二者都必须是字符串；不要输出 Markdown、解释或 JSON 之外的文字。\n"
            f"翻译要求：\n{system_prompt}\n\n"
            f"JSON payload：\n{json.dumps(payload, ensure_ascii=False)}\n\n"
            f"最多输出约 {max_tokens} 个 token；必须按原顺序覆盖 payload.items 中的每个 id，id 原样保留，不得遗漏、重复或添加其他 id。"
        )
        try:
            with tempfile.TemporaryDirectory(prefix="codex-trans-") as temp_dir:
                root = Path(temp_dir)
                output_path = root / "result.json"
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
                return parse_json_object(output_path.read_text(encoding="utf-8"))
            except (FileNotFoundError, ValueError) as exc:
                raise RuntimeError(f"Codex review produced invalid output: {exc}") from exc
