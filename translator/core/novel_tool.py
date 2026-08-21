from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
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
    status = str(result.get("status", "")).casefold()
    explicit_reason = str(result.get("reason", "")).casefold()
    if status in {"ok", "success"}:
        return "ok"
    if explicit_reason in {"content_filter", "output_format", "network"}:
        return explicit_reason
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
