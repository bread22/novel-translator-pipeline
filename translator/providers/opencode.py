from __future__ import annotations

import json
import math
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import time
from typing import Any, Callable

from translator.core.config import load_config, setting
from translator.providers.base import (
    BaseProvider,
    build_review_prompt,
    extract_json_object,
    parse_translation_items,
    provider_block_reason,
    validate_translation_items,
)


ROOT = Path(__file__).resolve().parents[2]

_CONTENT_FILTER_MARKERS = (
    "content policy",
    "sensitive words",
    "prohibited use policy",
    "content_filter",
    "provider_blocked",
)
_RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate_limit",
    "rate-limit",
    "too many requests",
    "quota exceeded",
    "quota_exceeded",
    "quota reached",
    "quota_reached",
    "quota",
    "free limit",
    "free_limit",
    "free tier limit",
    "free usage",
    "usage limit",
    "usage_limit",
    "resource exhausted",
    "resource_exhausted",
    "insufficient quota",
    "insufficient_quota",
)


class OpenCodeError(RuntimeError):
    def __init__(self, message: str, *, reason: str = "provider_error") -> None:
        super().__init__(message)
        self.reason = reason


def executable() -> str | None:
    config = load_config()
    try:
        bin_path = setting(config, "providers.opencode.binary", "OPENCODE_BIN")
    except KeyError:
        bin_path = os.environ.get("OPENCODE_BIN", "opencode")
    return shutil.which(bin_path) if bin_path else shutil.which("opencode")


def model_for(role: str) -> str:
    role_key = role.upper().replace("-", "_")
    env_name = f"OPENCODE_{role_key}_MODEL"
    if env_name in os.environ:
        return os.environ[env_name].strip()
    config = load_config()
    try:
        return str(setting(config, "providers.opencode.model", "OPENCODE_MODEL")).strip()
    except KeyError:
        return os.environ.get("OPENCODE_MODEL", "").strip()


def _agent_for(role: str) -> str:
    role_key = role.upper().replace("-", "_")
    env_name = f"OPENCODE_{role_key}_AGENT"
    if env_name in os.environ:
        return os.environ[env_name].strip()
    config = load_config()
    try:
        return str(setting(config, "providers.opencode.agent", "OPENCODE_AGENT")).strip()
    except KeyError:
        return os.environ.get("OPENCODE_AGENT", "").strip()


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


def _event_usage(stdout: str, *, model: str | None, duration_ms: float) -> dict[str, Any]:
    """Summarize usage reported by OpenCode's step_finish JSON events."""
    totals: dict[str, int | float] = {}
    reported: set[str] = set()
    finish_steps = 0
    cost_total = 0.0
    cost_reported = False
    session_id: str | None = None

    def add_number(name: str, value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        if not math.isfinite(float(value)) or value < 0:
            return
        totals[name] = totals.get(name, 0) + value
        reported.add(name)

    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if session_id is None and isinstance(event.get("sessionID"), str):
            session_id = event["sessionID"]
        if event.get("type") != "step_finish":
            continue
        part = event.get("part")
        if not isinstance(part, dict):
            continue
        tokens = part.get("tokens")
        if isinstance(tokens, dict):
            finish_steps += 1
            for source_key, target_key in (
                ("input", "input_tokens"),
                ("output", "output_tokens"),
                ("reasoning", "reasoning_tokens"),
                ("total", "total_tokens"),
            ):
                if source_key in tokens:
                    add_number(target_key, tokens[source_key])
            cache = tokens.get("cache")
            if isinstance(cache, dict):
                for source_key, target_key in (
                    ("read", "cache_read_tokens"),
                    ("write", "cache_write_tokens"),
                ):
                    if source_key in cache:
                        add_number(target_key, cache[source_key])
        cost = part.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(float(cost)) and cost >= 0:
            cost_total += float(cost)
            cost_reported = True

    usage: dict[str, Any] = {
        "source": "opencode_json_step_finish",
        "available": finish_steps > 0,
        "model": model or None,
        "steps": finish_steps,
        "request_duration_ms": round(duration_ms, 3),
    }
    usage.update({key: totals[key] for key in sorted(reported)})
    if cost_reported:
        usage["cost_usd"] = round(cost_total, 10)
    if "cache_read_tokens" in reported:
        usage["cache_hit"] = totals["cache_read_tokens"] > 0
    if session_id:
        usage["session_id"] = session_id
    return usage


def _session_export_usage(
    session_id: str,
    *,
    model: str | None,
    binary: str,
    request_started: float,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    """Recover final usage when `opencode run --format json` omitted step_finish."""
    try:
        result = subprocess.run(
            [binary, "session", "export", session_id, "--sanitize"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {**fallback, "session_export_status": "error"}
    if result.returncode != 0:
        return {**fallback, "session_export_status": "error"}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {**fallback, "session_export_status": "invalid_json"}
    info = payload.get("info") if isinstance(payload, dict) else None
    tokens = info.get("tokens") if isinstance(info, dict) else None
    if not isinstance(tokens, dict):
        return {**fallback, "session_export_status": "no_usage"}

    totals: dict[str, int | float] = {}
    reported: set[str] = set()
    for source_key, target_key in (
        ("input", "input_tokens"),
        ("output", "output_tokens"),
        ("reasoning", "reasoning_tokens"),
        ("total", "total_tokens"),
    ):
        value = tokens.get(source_key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and value >= 0
        ):
            totals[target_key] = value
            reported.add(target_key)
    cache = tokens.get("cache")
    if isinstance(cache, dict):
        for source_key, target_key in (("read", "cache_read_tokens"), ("write", "cache_write_tokens")):
            value = cache.get(source_key)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and value >= 0
            ):
                totals[target_key] = value
                reported.add(target_key)
    cost = info.get("cost") if isinstance(info, dict) else None
    cost_reported = (
        isinstance(cost, (int, float))
        and not isinstance(cost, bool)
        and math.isfinite(float(cost))
        and cost >= 0
    )
    if not reported and not cost_reported:
        return {**fallback, "session_export_status": "no_usage"}

    usage: dict[str, Any] = {
        "source": "opencode_session_export",
        "available": bool(reported),
        "model": model or None,
        "request_duration_ms": round((time.monotonic() - request_started) * 1000, 3),
        "session_id": session_id,
        "session_export_status": "ok",
    }
    usage.update({key: totals[key] for key in sorted(reported)})
    if cost_reported:
        usage["cost_usd"] = round(float(cost), 10)
    if "cache_read_tokens" in reported:
        usage["cache_hit"] = totals["cache_read_tokens"] > 0
    return usage


def _notify_usage(callback: Callable[[dict[str, Any]], None] | None, usage: dict[str, Any]) -> None:
    if callback is None:
        return
    try:
        callback(usage)
    except Exception:
        # Usage reporting must never change provider behavior.
        pass


def _event_error_text(stdout: str) -> str:
    """Extract errors from OpenCode's JSON event stream without scanning model text."""
    errors: list[str] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type", ""))
        error: Any = None
        if event_type in {"session.error", "error"}:
            properties = event.get("properties")
            error = properties.get("error") if isinstance(properties, dict) else event.get("error")
        elif event_type == "message.updated":
            properties = event.get("properties")
            info = properties.get("info") if isinstance(properties, dict) else None
            error = info.get("error") if isinstance(info, dict) else None
        if error is not None:
            errors.append(json.dumps(error, ensure_ascii=False) if not isinstance(error, str) else error)
    return "\n".join(errors)


def _failure_reason(stdout: str, stderr: str, *, include_plain_stdout: bool = False) -> str | None:
    material = "\n".join(
        part
        for part in (
            stderr,
            _event_error_text(stdout),
            stdout if include_plain_stdout else "",
        )
        if part
    ).casefold()
    if any(marker in material for marker in _CONTENT_FILTER_MARKERS):
        return "content_filter"
    if any(marker in material for marker in _RATE_LIMIT_MARKERS):
        return "rate_limit"
    return None


def _as_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _stop_process(process: subprocess.Popen[Any]) -> None:
    """Stop the OpenCode process and any children it launched."""
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass


def _run_command(command: list[str], prompt: str, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run OpenCode while continuously streaming stdin/stdout/stderr without deadlock."""
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    prompt_bytes = prompt.encode("utf-8")
    stdin_offset = 0
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    deadline = time.monotonic() + timeout

    if process.stdin:
        os.set_blocking(process.stdin.fileno(), False)
    if process.stdout:
        os.set_blocking(process.stdout.fileno(), False)
    if process.stderr:
        os.set_blocking(process.stderr.fileno(), False)

    try:
        with selectors.DefaultSelector() as selector:
            if process.stdin and prompt_bytes:
                selector.register(process.stdin, selectors.EVENT_WRITE)
            elif process.stdin:
                process.stdin.close()

            if process.stdout:
                selector.register(process.stdout, selectors.EVENT_READ)
            if process.stderr:
                selector.register(process.stderr, selectors.EVENT_READ)

            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _stop_process(process)
                    stdout_str = b"".join(stdout_chunks).decode("utf-8", errors="replace")
                    stderr_str = b"".join(stderr_chunks).decode("utf-8", errors="replace")
                    raise subprocess.TimeoutExpired(command, timeout, output=stdout_str, stderr=stderr_str)

                events = selector.select(timeout=min(0.5, remaining))
                for key, _ in events:
                    if key.fileobj is process.stdin:
                        chunk = prompt_bytes[stdin_offset : stdin_offset + 65536]
                        try:
                            written = os.write(process.stdin.fileno(), chunk)
                            stdin_offset += written
                            if stdin_offset >= len(prompt_bytes):
                                selector.unregister(process.stdin)
                                process.stdin.close()
                        except (BrokenPipeError, OSError):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                    elif key.fileobj is process.stdout:
                        try:
                            data = os.read(process.stdout.fileno(), 65536)
                        except (BlockingIOError, InterruptedError):
                            continue
                        except OSError:
                            data = b""
                        if not data:
                            selector.unregister(process.stdout)
                            process.stdout.close()
                        else:
                            stdout_chunks.append(data)
                    elif key.fileobj is process.stderr:
                        try:
                            data = os.read(process.stderr.fileno(), 65536)
                        except (BlockingIOError, InterruptedError):
                            continue
                        except OSError:
                            data = b""
                        if not data:
                            selector.unregister(process.stderr)
                            process.stderr.close()
                        else:
                            stderr_chunks.append(data)

                if process.poll() is not None and process.stdin and not process.stdin.closed:
                    try:
                        selector.unregister(process.stdin)
                    except Exception:
                        pass
                    process.stdin.close()

                stdout_str = b"".join(stdout_chunks).decode("utf-8", errors="replace")
                stderr_str = b"".join(stderr_chunks).decode("utf-8", errors="replace")
                reason = _failure_reason(stdout_str, stderr_str)
                if reason is not None:
                    _stop_process(process)
                    combined = "\n".join(part for part in (stdout_str, stderr_str) if part)
                    label = "blocked" if reason == "content_filter" else "rate limit"
                    raise OpenCodeError(f"opencode {label}: {combined[-2000:]}", reason=reason)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _stop_process(process)
            stdout_str = b"".join(stdout_chunks).decode("utf-8", errors="replace")
            stderr_str = b"".join(stderr_chunks).decode("utf-8", errors="replace")
            raise subprocess.TimeoutExpired(command, timeout, output=stdout_str, stderr=stderr_str)

        try:
            process.wait(timeout=max(0.0, remaining))
        except subprocess.TimeoutExpired:
            _stop_process(process)
            stdout_str = b"".join(stdout_chunks).decode("utf-8", errors="replace")
            stderr_str = b"".join(stderr_chunks).decode("utf-8", errors="replace")
            raise subprocess.TimeoutExpired(command, timeout, output=stdout_str, stderr=stderr_str)

        stdout_str = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        stderr_str = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(command, process.returncode, stdout_str, stderr_str)
    except BaseException:
        _stop_process(process)
        raise


def run_prompt(
    prompt: str,
    *,
    role: str = "translator",
    timeout: int = 600,
    model: str | None = None,
    binary: str | None = None,
    agent: str | None = None,
    variant: str | None = None,
    max_retries: int = 1,
    on_usage: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    if timeout <= 0:
        raise ValueError("OpenCode timeout 必须大于 0")
    command_executable = binary or executable()
    if not command_executable:
        raise OpenCodeError("opencode executable not found in PATH", reason="executable")
    command = [
        command_executable,
        "run",
        "--format",
        "json",
        "--print-logs",
        "--log-level",
        "error",
    ]
    chosen_model = model if model is not None else model_for(role)
    chosen_variant = (variant or "").strip()
    if chosen_model and chosen_variant:
        # OpenCode v2 encodes a model variant in the model identifier instead
        # of accepting the v1 `--variant` flag.
        chosen_model = f"{chosen_model.partition('#')[0]}#{chosen_variant}"
    if chosen_model:
        command.extend(["--model", chosen_model])
    chosen_agent = agent if agent is not None else _agent_for(role)
    if chosen_agent:
        command.extend(["--agent", chosen_agent])
    last_error: Exception | None = None
    for attempt in range(max_retries):
        request_started = time.monotonic()
        try:
            result = _run_command(command, prompt, timeout)
        except subprocess.TimeoutExpired as exc:
            elapsed_ms = (time.monotonic() - request_started) * 1000
            partial_stdout = _as_text(exc.output)
            usage = _event_usage(partial_stdout, model=chosen_model or None, duration_ms=elapsed_ms)
            session_id = usage.get("session_id")
            if not usage["available"] and isinstance(session_id, str):
                usage = _session_export_usage(
                    session_id,
                    model=chosen_model or None,
                    binary=command_executable,
                    request_started=request_started,
                    fallback=usage,
                )
            _notify_usage(on_usage, usage)
            last_error = OpenCodeError(f"opencode timed out after {timeout}s", reason="timeout")
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise last_error from exc
        except OSError as exc:
            elapsed_ms = (time.monotonic() - request_started) * 1000
            _notify_usage(on_usage, _event_usage("", model=chosen_model or None, duration_ms=elapsed_ms))
            last_error = OpenCodeError(str(exc), reason="network")
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise last_error from exc
        usage = _event_usage(
            result.stdout,
            model=chosen_model or None,
            duration_ms=(time.monotonic() - request_started) * 1000,
        )
        session_id = usage.get("session_id")
        if not usage["available"] and isinstance(session_id, str):
            usage = _session_export_usage(
                session_id,
                model=chosen_model or None,
                binary=command_executable,
                request_started=request_started,
                fallback=usage,
            )
        _notify_usage(on_usage, usage)
        combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
        failure_reason = _failure_reason(result.stdout, result.stderr, include_plain_stdout=result.returncode != 0)
        if failure_reason == "content_filter":
            raise OpenCodeError(f"opencode blocked: {combined[-2000:]}", reason="content_filter")
        if failure_reason == "rate_limit":
            raise OpenCodeError(f"opencode rate limit: {combined[-2000:]}", reason="rate_limit")
        if result.returncode != 0:
            last_error = OpenCodeError(f"opencode exited {result.returncode}: {combined[-2000:]}", reason="process")
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise last_error
        output = _event_text(result.stdout)
        if not output:
            last_error = OpenCodeError(f"opencode returned no assistant text: {combined[-1000:]}", reason="output_format")
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise last_error
        return output
    if last_error:
        raise last_error
    raise OpenCodeError("opencode failed", reason="process")


parse_json_object = extract_json_object


def run_json(
    prompt: str,
    *,
    role: str = "reviewer",
    timeout: int = 600,
    on_usage: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    return parse_json_object(run_prompt(prompt, role=role, timeout=timeout, on_usage=on_usage))


def check(timeout: int = 60, *, role: str = "reviewer") -> dict[str, Any]:
    usage: dict[str, Any] | None = None

    def capture_usage(details: dict[str, Any]) -> None:
        nonlocal usage
        usage = details

    try:
        payload = run_json(
            'Return exactly {"ok":true}. Do not include Markdown, explanations, tools, or any other fields.',
            role=role,
            timeout=timeout,
            on_usage=capture_usage,
        )
    except (OpenCodeError, ValueError) as exc:
        result = {
            "name": f"provider:opencode:{role}",
            "status": "error",
            "model": model_for(role) or "(configured default)",
            "error": str(exc)[:800],
        }
        if usage is not None:
            result["usage"] = usage
        return result
    if payload.get("ok") is not True or set(payload) != {"ok"}:
        result = {
            "name": f"provider:opencode:{role}",
            "status": "error",
            "model": model_for(role) or "(configured default)",
            "error": f"unexpected health response: {payload!r}",
        }
        if usage is not None:
            result["usage"] = usage
        return result
    result = {
        "name": f"provider:opencode:{role}",
        "status": "ok",
        "model": model_for(role) or "(configured default)",
    }
    if usage is not None:
        result["usage"] = usage
    return result


class OpenCodeProvider(BaseProvider):
    """OpenCode CLI universal provider for translation and review."""

    def __init__(self, name: str, config: dict[str, Any]) -> None:
        super().__init__(name, config)
        self.binary = str(config.get("binary", "opencode"))
        self.model = str(config.get("model", ""))
        self.agent = str(config.get("agent", ""))
        self.variant = str(config.get("variant", "low") or "low").strip() or "low"
        self.timeout = int(config.get("timeout", 600))
        self.last_usage: dict[str, Any] | None = None

    def health_check(self, timeout: int = 10) -> dict[str, Any]:
        eff_model = self.model or model_for("reviewer") or "(configured default)"
        self.last_usage = None

        def capture_usage(usage: dict[str, Any]) -> None:
            self.last_usage = usage

        try:
            raw = run_prompt(
                'Return exactly {"ok":true}. Do not include Markdown, explanations, tools, or any other fields.',
                role="reviewer",
                timeout=timeout,
                model=self.model or None,
                binary=self.binary or None,
                agent=self.agent,
                variant=self.variant or None,
                max_retries=1,
                on_usage=capture_usage,
            )
            payload = parse_json_object(raw)
            if payload.get("ok") is not True or set(payload) != {"ok"}:
                result = {
                    "name": f"provider:{self.name}",
                    "status": "error",
                    "model": eff_model,
                    "error": f"unexpected health response: {payload!r}",
                }
                if self.last_usage is not None:
                    result["usage"] = self.last_usage
                return result
            result = {
                "name": f"provider:{self.name}",
                "status": "ok",
                "model": eff_model,
            }
            if self.last_usage is not None:
                result["usage"] = self.last_usage
            return result
        except Exception as exc:
            result = {
                "name": f"provider:{self.name}",
                "status": "error",
                "model": eff_model,
                "error": str(exc)[:800],
            }
            if self.last_usage is not None:
                result["usage"] = self.last_usage
            return result

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
        self.last_usage = None

        def capture_usage(usage: dict[str, Any]) -> None:
            self.last_usage = usage

        try:
            content = run_prompt(
                prompt,
                role="translator",
                timeout=timeout or self.timeout,
                model=self.model or None,
                binary=self.binary or None,
                agent=self.agent,
                variant=self.variant or None,
                max_retries=1,
                on_usage=capture_usage,
            )
        except OpenCodeError as exc:
            result = {
                "status": "blocked" if exc.reason == "content_filter" else "error",
                "provider": self.name,
                "reason": exc.reason,
                "error": str(exc),
            }
            if self.last_usage is not None:
                result["usage"] = self.last_usage
            return [], result
        common = {"provider": self.name, "raw_response": content[:4000]}
        if self.last_usage is not None:
            common["usage"] = self.last_usage
        block = provider_block_reason(content)
        if block:
            return [], {**common, "status": "blocked", "reason": "content_filter"}
        try:
            items = parse_translation_items(content)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
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
        self.last_usage = None

        def capture_usage(usage: dict[str, Any]) -> None:
            self.last_usage = usage

        content = run_prompt(
            prompt,
            role="reviewer",
            timeout=timeout or self.timeout,
            model=self.model or None,
            binary=self.binary or None,
            agent=self.agent,
            variant=self.variant or None,
            max_retries=1,
            on_usage=capture_usage,
        )
        return parse_json_object(content)
