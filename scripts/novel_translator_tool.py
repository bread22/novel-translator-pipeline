from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
NOVEL_TRANSLATOR_ROOT = Path(
    os.environ.get("NOVEL_TRANSLATOR_ROOT", str(Path.home() / "src" / "novel-translator"))
).expanduser().resolve()
NOVEL_TRANSLATOR_PYTHON = NOVEL_TRANSLATOR_ROOT / ".venv" / "bin" / "python"
if not NOVEL_TRANSLATOR_PYTHON.exists():
    NOVEL_TRANSLATOR_PYTHON = Path(os.environ.get("NOVEL_TRANSLATOR_PYTHON", "python3"))


def provider_failure_reason(result: dict[str, Any] | None) -> str:
    """Classify a provider result without treating every failure as a block."""
    if not isinstance(result, dict):
        return "unknown"
    text = json.dumps(result, ensure_ascii=False).casefold()
    if any(marker in text for marker in ("content_filter", "content policy", "sensitive words", "safety policy", "provider_blocked")):
        return "content_filter"
    if any(marker in text for marker in ("timeout", "timed out", "connection error", "connection refused")):
        return "network"
    if "json" in text and any(marker in text for marker in ("parse", "items", "schema")):
        return "output_format"
    return "provider_error"


def call_novel_translator(*args: str) -> dict[str, Any]:
    command = [
        str(NOVEL_TRANSLATOR_PYTHON),
        str(NOVEL_TRANSLATOR_ROOT / "main.py"),
        "--agent-mode",
        *args,
        "--json",
    ]
    result = subprocess.run(
        command,
        cwd=NOVEL_TRANSLATOR_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Novel Translator failed ({result.returncode}):\n{result.stderr}\n{result.stdout}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Novel Translator returned non-JSON output:\n{result.stdout}") from exc


def call_novel_translator_with_config(
    overrides: dict[str, str | int],
    *args: str,
) -> dict[str, Any]:
    """Run Novel Translator with temporary provider/config overrides."""
    setting = NOVEL_TRANSLATOR_ROOT / "setting.toml"
    content = setting.read_text(encoding="utf-8")
    for key, value in overrides.items():
        if isinstance(value, int):
            replacement = str(value)
        else:
            replacement = json.dumps(value, ensure_ascii=False)
        updated, count = re.subn(
            rf"(?m)^{re.escape(key)}\s*=\s*.*$",
            f"{key} = {replacement}",
            content,
            count=1,
        )
        if count != 1:
            raise RuntimeError(f"未找到配置项：{key}：{setting}")
        content = updated
    temporary = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".toml", delete=False)
    try:
        temporary.write(content)
        temporary.close()
        command = [
            str(NOVEL_TRANSLATOR_PYTHON),
            str(NOVEL_TRANSLATOR_ROOT / "main.py"),
            "--config",
            temporary.name,
            "--agent-mode",
            *args,
            "--json",
        ]
        result = subprocess.run(command, cwd=NOVEL_TRANSLATOR_ROOT, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Novel Translator provider failed ({result.returncode}):\n{result.stderr}\n{result.stdout}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Novel Translator provider returned non-JSON output:\n{result.stdout}") from exc
    finally:
        Path(temporary.name).unlink(missing_ok=True)


def call_novel_translator_targeted(
    provider: str,
    book: str,
    paragraph_ids: list[str],
    *,
    source_chars: int,
    max_tokens: int = 8192,
) -> dict[str, Any]:
    """Translate only a segment with the selected provider."""
    if not paragraph_ids:
        return {"status": "ok", "summary": {"translated": 0}}
    if provider == "gemini":
        overrides = {"batch_max_chars": max(1, source_chars + 1), "max_tokens": max_tokens}
    elif provider == "murasaki-local":
        overrides = {
            "base_url": os.environ.get("MURASAKI_BASE_URL", "http://127.0.0.1:1234/v1"),
            "model": os.environ.get("MURASAKI_MODEL", "murasaki-14b-v0.2"),
            "batch_max_chars": max(1, source_chars + 1),
            "max_tokens": max_tokens,
        }
    else:
        raise ValueError(f"未知翻译 provider：{provider}")
    return call_novel_translator_with_config(
        overrides,
        "translate",
        "--book",
        book,
        "--max-batches",
        "1",
        "--workers",
        "1",
        "--rpm",
        "10",
        "--target-ids",
        *paragraph_ids,
    )


def call_novel_translator_with_batch_limit(batch_max_chars: int, *args: str) -> dict[str, Any]:
    """Run a recovery command with a temporary smaller batch size."""
    if batch_max_chars < 1:
        raise ValueError("batch_max_chars 必须大于 0")
    setting = NOVEL_TRANSLATOR_ROOT / "setting.toml"
    content = setting.read_text(encoding="utf-8")
    updated, count = re.subn(
        r"(?m)^batch_max_chars\s*=\s*\d+\s*$",
        f"batch_max_chars = {batch_max_chars}",
        content,
        count=1,
    )
    if count != 1:
        raise RuntimeError(f"未找到 batch_max_chars 配置：{setting}")
    temporary = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".toml", delete=False)
    try:
        temporary.write(updated)
        temporary.close()
        command = [
            str(NOVEL_TRANSLATOR_PYTHON),
            str(NOVEL_TRANSLATOR_ROOT / "main.py"),
            "--config",
            temporary.name,
            "--agent-mode",
            *args,
            "--json",
        ]
        result = subprocess.run(command, cwd=NOVEL_TRANSLATOR_ROOT, text=True, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Novel Translator recovery failed ({result.returncode}):\n{result.stderr}\n{result.stdout}")
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Novel Translator recovery returned non-JSON output:\n{result.stdout}") from exc
    finally:
        Path(temporary.name).unlink(missing_ok=True)
