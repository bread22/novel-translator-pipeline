from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

from scripts.config import load_config, setting

ROOT = Path(__file__).resolve().parents[1]


class OpenCodeError(RuntimeError):
    def __init__(self, message: str, *, reason: str = "provider_error") -> None:
        super().__init__(message)
        self.reason = reason


def executable() -> str | None:
    config = load_config()
    return shutil.which(setting(config, "providers.opencode.binary", "OPENCODE_BIN"))


def model_for(role: str) -> str:
    role_key = role.upper().replace("-", "_")
    config = load_config()
    env_name = f"OPENCODE_{role_key}_MODEL"
    if env_name in os.environ:
        return os.environ[env_name].strip()
    return setting(config, "providers.opencode.model", "OPENCODE_MODEL").strip()


def _agent_for(role: str) -> str:
    role_key = role.upper().replace("-", "_")
    config = load_config()
    env_name = f"OPENCODE_{role_key}_AGENT"
    if env_name in os.environ:
        return os.environ[env_name].strip()
    return setting(config, "providers.opencode.agent", "OPENCODE_AGENT").strip()


def _event_text(stdout: str) -> str:
    chunks: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type", ""))
        if event_type in {"text", "message.part"}:
            part = event.get("part")
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
            elif isinstance(event.get("text"), str):
                chunks.append(event["text"])
        elif event_type in {"message", "assistant"}:
            content = event.get("content")
            if isinstance(content, str):
                chunks.append(content)
            elif isinstance(content, list):
                chunks.extend(
                    str(item.get("text", ""))
                    for item in content
                    if isinstance(item, dict) and isinstance(item.get("text"), str)
                )
    return "".join(chunks).strip()


def run_prompt(prompt: str, *, role: str, timeout: int = 600) -> str:
    if timeout <= 0:
        raise ValueError("OpenCode timeout 必须大于 0")
    command_executable = executable()
    if not command_executable:
        raise OpenCodeError("opencode executable not found in PATH", reason="executable")
    command = [command_executable, "run", "--format", "json", "--dir", str(ROOT)]
    model = model_for(role)
    if model:
        command.extend(["--model", model])
    agent = _agent_for(role)
    if agent:
        command.extend(["--agent", agent])
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OpenCodeError(f"opencode timed out after {timeout}s", reason="timeout") from exc
    except OSError as exc:
        raise OpenCodeError(str(exc), reason="network") from exc
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    lowered = combined.casefold()
    if any(marker in lowered for marker in ("content policy", "sensitive words", "prohibited use policy", "content_filter", "provider_blocked")):
        reason = "content_filter"
    else:
        reason = "process"
    if result.returncode != 0:
        raise OpenCodeError(f"opencode exited {result.returncode}: {combined[-2000:]}", reason=reason)
    output = _event_text(result.stdout)
    if not output:
        output = result.stdout.strip()
    if not output:
        raise OpenCodeError("opencode returned no assistant text", reason="output_format")
    return output


def parse_json_object(text: str) -> dict[str, Any]:
    candidates = [text.strip()]
    if "```" in text:
        candidates.extend(block.removeprefix("json").strip() for block in text.split("```")[1::2])
    decoder = json.JSONDecoder()
    found: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            value = None
        if isinstance(value, dict):
            found.append(value)
        for index, char in enumerate(candidate):
            if char != "{":
                continue
            try:
                value, _ = decoder.raw_decode(candidate[index:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                found.append(value)
    if found:
        # Prefer the object that looks like the requested protocol payload when
        # OpenCode adds a short explanatory prefix before the final JSON.
        protocol_keys = {"items", "checked_ids", "fixes", "checked_chapters", "ok"}
        return max(found, key=lambda item: (len(protocol_keys.intersection(item)), len(item)))
    raise ValueError("OpenCode 输出中没有找到 JSON 对象")


def run_json(prompt: str, *, role: str, timeout: int = 600) -> dict[str, Any]:
    return parse_json_object(run_prompt(prompt, role=role, timeout=timeout))


def check(timeout: int = 60, *, role: str = "reviewer") -> dict[str, Any]:
    try:
        payload = run_json(
            'Return exactly {"ok":true}. Do not include Markdown, explanations, tools, or any other fields.',
            role=role,
            timeout=timeout,
        )
    except (OpenCodeError, ValueError) as exc:
        return {
            "name": f"opencode:{role}",
            "status": "error",
            "model": model_for(role) or "(configured default)",
            "error": str(exc)[:800],
        }
    if payload.get("ok") is not True or set(payload) != {"ok"}:
        return {
            "name": f"opencode:{role}",
            "status": "error",
            "model": model_for(role) or "(configured default)",
            "error": f"unexpected health response: {payload!r}",
        }
    return {
        "name": f"opencode:{role}",
        "status": "ok",
        "model": model_for(role) or "(configured default)",
    }
