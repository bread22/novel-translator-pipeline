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
