from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def resolve_novel_translator_root() -> Path:
    env_root = os.environ.get("NOVEL_TRANSLATOR_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    vendor_root = ROOT / "vendor" / "novel-translator"
    if (vendor_root / "main.py").exists():
        return vendor_root.resolve()
    default_home = Path.home() / "src" / "novel-translator"
    if (default_home / "main.py").exists():
        return default_home.resolve()
    return default_home.resolve()


def resolve_novel_translator_python(novel_root: Path | None = None) -> Path:
    env_python = os.environ.get("NOVEL_TRANSLATOR_PYTHON")
    if env_python:
        return Path(env_python).expanduser().resolve()
    if novel_root is None:
        novel_root = resolve_novel_translator_root()

    for candidate in (
        novel_root / ".venv" / "bin" / "python",
        novel_root / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ):
        if candidate.exists():
            return candidate.resolve()

    if sys.executable:
        return Path(sys.executable).resolve()
    return Path("python3")


NOVEL_TRANSLATOR_ROOT = resolve_novel_translator_root()
NOVEL_TRANSLATOR_PYTHON = resolve_novel_translator_python(NOVEL_TRANSLATOR_ROOT)


@dataclass
class ToolResult:
    status: str
    returncode: int | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class NovelToolError(RuntimeError):
    def __init__(self, message: str, result: ToolResult) -> None:
        super().__init__(message)
        self.result = result


def novel_translator_diagnostic(novel_root: Path | None = None, python_bin: Path | None = None) -> dict[str, Any]:
    root = (novel_root or resolve_novel_translator_root()).expanduser().resolve()
    py_bin = (python_bin or resolve_novel_translator_python(root)).expanduser().resolve()
    checks = {
        "root_exists": root.is_dir(),
        "main_py_exists": (root / "main.py").is_file(),
        "python_exists": py_bin.is_file(),
    }
    return {
        "status": "ok" if all(checks.values()) else "error",
        "root": str(root),
        "python": str(py_bin),
        "checks": checks,
        "setup": "设置 NOVEL_TRANSLATOR_ROOT 和 NOVEL_TRANSLATOR_PYTHON，或安装受支持的 external runtime。",
    }


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
    if any(marker in text for marker in (
        "content_filter",
        "content policy",
        "sensitive words",
        "safety policy",
        "provider_blocked",
        "explicit sexual content",
        "cannot help with this request",
        "can't help with this request",
        "i can't help with this",
        "i cannot help with this",
        "i can't help with",
        "i cannot help with",
        "i cannot fulfill this request",
        "i can't fulfill this request",
        "cannot translate explicit",
        "cannot reproduce that material",
        "won't produce this",
        "won't produce",
        "not able to produce or translate",
        "not able to translate",
        "as an ai language model",
        "falls under content i won't produce",
        "falls under content i cannot produce",
        "depicts a non-consensual",
        "non-consensual sexual",
        "regardless of the framing as fiction",
        "我无法翻译",
        "我不能翻译",
        "我无法协助",
        "我不能协助",
        "无法翻译该请求",
        "无法翻译这些内容",
        "不能翻译这批内容",
        "无法按您要求的json格式输出",
        "属于禁止生成",
        "禁止生成的范围",
        "禁止生成范畴",
        "安全政策",
        "违反安全政策",
        "未成年人的性",
        "未成年人的露骨性内容",
        "违背道德",
        "色情内容",
        "露骨色情",
        "涉及未成年人",
        "翻訳できません",
        "生成・翻訳には応じられない",
        "生成・翻訳",
        "応じられません",
        "翻訳には応じられ",
        "翻訳をお手伝いできません",
        "性的コンテンツ",
        "性的描写",
        "ポリシー",
        "ガイドライン",
    )):
        return "content_filter"
    if any(marker in text for marker in ("quota", "rate limit", "overloaded", "subscription to increase")):
        return "quota_reached"
    if any(marker in text for marker in ("timeout", "timed out", "connection error", "connection refused")):
        return "network"
    if "json" in text and any(marker in text for marker in ("parse", "items", "schema")):
        return "output_format"
    return "provider_error"


def call_novel_translator(
    *args: str,
    novel_root: Path | None = None,
    python_bin: Path | None = None,
    timeout: float = 600,
    output_limit: int = 16_000,
) -> dict[str, Any]:
    root = novel_root or resolve_novel_translator_root()
    py_bin = python_bin or resolve_novel_translator_python(root)
    diagnostic = novel_translator_diagnostic(root, py_bin)
    if diagnostic["status"] != "ok":
        result = ToolResult(status="error", errors=[{"code": "dependency_missing", "message": diagnostic["setup"]}], summary=diagnostic)
        raise NovelToolError("Novel Translator runtime 检查失败", result)
    command = [
        str(py_bin),
        str(root / "main.py"),
        "--agent-mode",
        *args,
        "--json",
    ]
    process = subprocess.Popen(
        command,
        cwd=root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=(os.name != "nt"),
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            stdout, stderr = process.communicate()
        result = ToolResult(
            status="timeout",
            returncode=process.returncode,
            errors=[{"code": "timeout", "message": f"外部工具超过 {timeout} 秒"}],
            stdout=stdout[-output_limit:],
            stderr=stderr[-output_limit:],
        )
        raise NovelToolError("Novel Translator 执行超时", result) from exc
    stdout = stdout[-output_limit:]
    stderr = stderr[-output_limit:]
    if process.returncode != 0:
        result = ToolResult(
            status="error",
            returncode=process.returncode,
            errors=[{"code": "nonzero_exit", "message": stderr or stdout or "外部工具返回非零状态"}],
            stdout=stdout,
            stderr=stderr,
        )
        raise NovelToolError(f"Novel Translator failed ({process.returncode})", result)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        result = ToolResult(
            status="error",
            returncode=process.returncode,
            errors=[{"code": "invalid_json", "message": "外部工具未返回 JSON"}],
            stdout=stdout,
            stderr=stderr,
        )
        raise NovelToolError("Novel Translator returned non-JSON output", result) from exc
    if not isinstance(payload, dict):
        payload = {"payload": payload}
    normalized = ToolResult(
        status=str(payload.get("status", "ok")),
        returncode=process.returncode,
        errors=list(payload.get("errors", []) or []),
        summary=dict(payload.get("summary", {}) or {}),
        stdout="",
        stderr=stderr,
    ).as_dict()
    normalized.update(payload)
    normalized["returncode"] = process.returncode
    return normalized
