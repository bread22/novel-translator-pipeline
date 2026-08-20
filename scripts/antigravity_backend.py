#!/usr/bin/env python3
"""OpenAI-compatible bridge from Novel Translator to Antigravity/agy."""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import BoundedSemaphore
import subprocess
import time
from typing import Any


def provider_block_reason(text: str) -> str:
    lowered = text.casefold()
    if "sensitive words" in lowered or "prohibited use policy" in lowered or "content policy" in lowered:
        return "content_filter"
    return ""


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the translation JSON from plain, fenced, or agy-wrapped output."""
    candidates = [text.strip()]
    if "```" in text:
        for block in text.split("```")[1::2]:
            candidates.append(block.removeprefix("json").strip())
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            for key in ("items", "response", "text", "content", "output"):
                nested = value.get(key)
                if key == "items" and isinstance(nested, list):
                    return value
                if isinstance(nested, str):
                    try:
                        parsed = json.loads(nested)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, dict):
                        return parsed
                if isinstance(nested, dict) and "items" in nested:
                    return nested
    start = text.find("{")
    while start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start:index + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(value, dict):
                        if isinstance(value.get("items"), list):
                            return value
                        for key in ("response", "text", "content", "output"):
                            nested = value.get(key)
                            if isinstance(nested, str):
                                return extract_json_object(nested)
                    break
        start = text.find("{", start + 1)
    raise ValueError("agy 输出中没有找到包含 items 的翻译 JSON")


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


class AntigravityBridge:
    def __init__(self, *, agy: str, model: str, effort: str, timeout: int, concurrency: int) -> None:
        self.agy = agy
        self.model = model
        self.effort = effort
        self.timeout = timeout
        self.slots = BoundedSemaphore(max(1, concurrency))

    def complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        prompt = build_prompt(messages)
        command = [
            self.agy,
            "--model",
            self.model,
            "--effort",
            self.effort,
            "--output-format",
            "text",
            "--print-timeout",
            f"{self.timeout}s",
            "--print",
            prompt,
        ]
        acquired = self.slots.acquire(timeout=self.timeout)
        if not acquired:
            raise TimeoutError("等待 Antigravity 并发槽位超时")
        try:
            result = subprocess.run(command, text=True, capture_output=True, timeout=self.timeout, check=False)
        finally:
            self.slots.release()
        if result.returncode != 0:
            raise RuntimeError(f"agy failed ({result.returncode}): {result.stderr[-2000:]}")
        block_reason = provider_block_reason(result.stdout)
        if block_reason:
            excerpt = result.stdout.strip()[:1000]
            raise ValueError(f"provider_blocked: {block_reason}; raw_response={excerpt}")
        payload = extract_json_object(result.stdout)
        if not isinstance(payload.get("items"), list):
            raise ValueError("翻译后端 JSON 缺少 items 数组")
        return payload


def make_handler(bridge: AntigravityBridge):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/v1/models":
                self._send(200, {"object": "list", "data": [{"id": bridge.model, "object": "model", "owned_by": "antigravity"}]})
                return
            self._send(404, {"error": {"message": "not found"}})

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/v1/chat/completions":
                self._send(404, {"error": {"message": "not found"}})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                request = json.loads(self.rfile.read(length))
                response = bridge.complete(request.get("messages", []))
                content = json.dumps(response, ensure_ascii=False)
                self._send(200, {
                    "id": f"antigravity-{int(time.time() * 1000)}",
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": request.get("model", bridge.model),
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                })
            except Exception as exc:  # bridge must return an OpenAI-shaped error
                self._send(502, {"error": {"message": str(exc), "type": type(exc).__name__}})

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Expose agy Gemini as an OpenAI-compatible Novel Translator backend")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1235)
    parser.add_argument("--agy", default=os.environ.get("AGY_BIN", "agy"))
    parser.add_argument("--model", default=os.environ.get("ANTIGRAVITY_MODEL", "gemini-3.7-flash"))
    parser.add_argument("--effort", choices=("low", "medium", "high"), default=os.environ.get("ANTIGRAVITY_EFFORT", "low"))
    parser.add_argument("--timeout", type=int, default=int(os.environ.get("ANTIGRAVITY_TIMEOUT", "600")))
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(AntigravityBridge(agy=args.agy, model=args.model, effort=args.effort, timeout=args.timeout, concurrency=args.concurrency)))
    print(f"Antigravity bridge listening on http://{args.host}:{args.port}/v1", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
