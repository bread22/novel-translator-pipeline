from __future__ import annotations

import hashlib
from pathlib import Path
import re
import unicodedata


_IDENTIFIER = re.compile(r"^[\w][\w.-]{0,127}$", re.UNICODE)
_PROMPT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\.md$")


def _reject_control(value: str) -> None:
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("名称为空或包含控制字符")


def validate_prompt_filename(value: str) -> str:
    _reject_control(value)
    if value != Path(value).name or "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError("Prompt 必须是单层文件名")
    if not _PROMPT.fullmatch(value):
        raise ValueError("Prompt 文件名只允许字母、数字、点、下划线、连字符并以 .md 结尾")
    return value


def resolve_under(root: Path, candidate: str | Path) -> Path:
    root = root.expanduser().resolve()
    raw = str(candidate)
    _reject_control(raw)
    if Path(raw).is_absolute():
        raise ValueError("不接受绝对路径")
    resolved = (root / raw).resolve()
    if resolved == root or root not in resolved.parents:
        raise ValueError("路径超出允许目录")
    return resolved


def validate_book_id(value: str) -> str:
    _reject_control(value)
    if not _IDENTIFIER.fullmatch(value) or value in {".", ".."}:
        raise ValueError("书籍 ID 格式无效")
    return value


def book_id_from_title(title: str) -> str:
    """Create a valid, readable book ID from an uploaded file's title."""
    normalized = unicodedata.normalize("NFKC", title).strip()
    candidate = re.sub(r"[^\w.-]+", "-", normalized, flags=re.UNICODE).strip("._-")
    if not candidate:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]
        candidate = f"book-{digest}"
    elif len(candidate) > 128:
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:8]
        candidate = f"{candidate[:119].rstrip('._-')}-{digest}"
    return validate_book_id(candidate)


def validate_chapter_id(value: str) -> str:
    _reject_control(value)
    if not _IDENTIFIER.fullmatch(value) or value in {".", ".."}:
        raise ValueError("章节 ID 格式无效")
    return value
