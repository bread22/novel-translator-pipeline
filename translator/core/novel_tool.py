from __future__ import annotations

import json
from importlib import import_module
import os
from pathlib import Path
import sys
from dataclasses import asdict, dataclass, field
from threading import RLock
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]


def resolve_novel_translator_root() -> Path:
    env_root = os.environ.get("NOVEL_TRANSLATOR_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()
    vendor_root = ROOT / "vendor" / "novel-translator"
    if (vendor_root / "app").is_dir():
        return vendor_root.resolve()
    default_home = Path.home() / "src" / "novel-translator"
    if (default_home / "app").is_dir():
        return default_home.resolve()
    return vendor_root.resolve()


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


@dataclass(frozen=True)
class _VendorApi:
    """The small upstream API surface used by this project."""

    EpubConfig: type[Any]
    export_epub: Callable[..., dict[str, Any]]
    export_txt: Callable[..., None]
    load_source_book: Callable[..., Any]
    validate_epub: Callable[..., dict[str, Any]]
    reset_translations: Callable[..., dict[str, Any]]
    load_book: Callable[..., Any]
    save_book: Callable[..., Path]
    slugify: Callable[..., str]
    create_snapshot: Callable[..., dict[str, Any]]
    apply_review_fixes: Callable[..., dict[str, Any]]


_VENDOR_IMPORT_LOCK = RLock()
_VENDOR_OPERATION_LOCK = RLock()
_VENDOR_API_CACHE: dict[Path, _VendorApi] = {}
_PYTHON_API_COMMANDS = {
    "add-book",
    "snapshot",
    "apply-review-fixes",
    "export",
    "validate-epub",
    "reset-translations",
}


def _purge_app_modules() -> None:
    for module_name in list(sys.modules):
        if module_name == "app" or module_name.startswith("app."):
            del sys.modules[module_name]


def _vendor_api(root: Path) -> _VendorApi:
    """Import the vendored top-level ``app`` package once per runtime root."""
    root = root.expanduser().resolve()
    with _VENDOR_IMPORT_LOCK:
        cached = _VENDOR_API_CACHE.get(root)
        if cached is not None:
            return cached

        root_string = str(root)
        if root_string not in sys.path:
            sys.path.insert(0, root_string)

        loaded_app = sys.modules.get("app")
        loaded_paths = getattr(loaded_app, "__path__", ()) if loaded_app is not None else ()
        if loaded_app is not None and not any(Path(path).resolve() == root / "app" for path in loaded_paths):
            _purge_app_modules()

        book_io = import_module("app.book_io")
        config = import_module("app.config")
        manual = import_module("app.manual")
        models = import_module("app.models")
        review = import_module("app.review")
        snapshots = import_module("app.snapshots")
        api = _VendorApi(
            EpubConfig=config.EpubConfig,
            export_epub=book_io.export_epub,
            export_txt=book_io.export_txt,
            load_source_book=book_io.load_source_book,
            validate_epub=book_io.validate_epub,
            reset_translations=manual.reset_translations,
            load_book=models.load_book,
            save_book=models.save_book,
            slugify=models.slugify,
            create_snapshot=snapshots.create_snapshot,
            apply_review_fixes=review.apply_review_fixes,
        )
        _VENDOR_API_CACHE[root] = api
        return api


def _flag_value(args: tuple[str, ...], flag: str, *, required: bool = True) -> str | None:
    try:
        index = args.index(flag)
    except ValueError:
        if required:
            raise ValueError(f"缺少参数：{flag}")
        return None
    value_index = index + 1
    if value_index >= len(args) or args[value_index].startswith("--"):
        raise ValueError(f"参数 {flag} 缺少值")
    return args[value_index]


def _has_flag(args: tuple[str, ...], flag: str) -> bool:
    return flag in args


def _books_dir(root: Path) -> Path:
    return root / "data" / "books"


def _epub_config(api: _VendorApi) -> Any:
    """Use only the upstream EPUB contract; pipeline config has another shape."""
    return api.EpubConfig()


def _python_api_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {"payload": payload}
    errors = payload.get("errors", []) or []
    if not isinstance(errors, list):
        errors = [{"code": "python_api_error", "message": str(errors)}]
    summary = payload.get("summary", {}) or {}
    if not isinstance(summary, dict):
        summary = {"value": summary}
    normalized: dict[str, Any] = {
        "status": str(payload.get("status", "ok")),
        "returncode": 0,
        "errors": errors,
        "summary": summary,
        "stdout": "",
        "stderr": "",
    }
    normalized.update(payload)
    normalized["returncode"] = 0
    normalized.setdefault("errors", errors)
    normalized.setdefault("summary", summary)
    normalized.setdefault("stdout", "")
    normalized.setdefault("stderr", "")
    return normalized


def _call_python_api(root: Path, args: tuple[str, ...]) -> dict[str, Any]:
    command = args[0] if args else ""
    api = _vendor_api(root)
    books_dir = _books_dir(root)

    with _VENDOR_OPERATION_LOCK:
        if command == "add-book":
            source_path = Path(_flag_value(args, "--path") or "").expanduser().resolve()
            if not source_path.exists():
                raise FileNotFoundError(f"文件不存在：{source_path}")
            book = api.load_source_book(
                source_path,
                title=_flag_value(args, "--title", required=False),
                epub_config=_epub_config(api),
            )
            requested_id = _flag_value(args, "--id", required=False)
            if requested_id:
                book.id = api.slugify(requested_id)
            target_dir = api.save_book(books_dir, book, source_path)
            warnings: list[str] = []
            summary: dict[str, Any] = {
                "book": book.id,
                "title": book.title,
                "type": book.source_type,
                "chapters": len(book.chapters),
                "paragraphs": len(book.paragraphs),
                "data_dir": str(target_dir),
            }
            if book.source_type == "epub":
                epub_meta = book.metadata.get("epub", {})
                warnings = list(epub_meta.get("warnings", []))
                summary.update(
                    {
                        "parser_mode": epub_meta.get("parser_mode", ""),
                        "nav_path": epub_meta.get("nav_path", ""),
                        "toc_path": epub_meta.get("toc_path", ""),
                        "warning_count": epub_meta.get("warning_count", 0),
                    }
                )
            return _python_api_result(
                {
                    "status": "warning" if warnings else "ok",
                    "warnings": warnings,
                    "summary": summary,
                    "details": {},
                }
            )

        if command == "snapshot":
            book_id = _flag_value(args, "--book") or ""
            book = api.load_book(books_dir, book_id)
            return _python_api_result(api.create_snapshot(books_dir, book, _flag_value(args, "--name") or ""))

        if command == "apply-review-fixes":
            book_id = _flag_value(args, "--book") or ""
            book = api.load_book(books_dir, book_id)
            input_path = Path(_flag_value(args, "--input") or "").expanduser().resolve()
            return _python_api_result(api.apply_review_fixes(books_dir, book, input_path))

        if command == "export":
            book_id = _flag_value(args, "--book") or ""
            output = Path(_flag_value(args, "--output") or "").expanduser().resolve()
            export_format = (_flag_value(args, "--format") or "").casefold()
            book = api.load_book(books_dir, book_id)
            bilingual = _has_flag(args, "--bilingual") and not _has_flag(args, "--monolingual")
            if export_format == "txt":
                api.export_txt(book, output, bilingual=bilingual)
                warnings = []
            elif export_format == "epub":
                export_result = api.export_epub(book, output, _epub_config(api), bilingual=bilingual)
                warnings = list(export_result.get("warnings", []))
            else:
                raise ValueError(f"不支持导出格式：{export_format}")
            return _python_api_result(
                {
                    "status": "warning" if warnings else "ok",
                    "warnings": warnings,
                    "summary": {
                        "book": book.id,
                        "output": str(output),
                        "format": export_format,
                        "bilingual": bilingual,
                        "warning_count": len(warnings),
                    },
                    "details": {},
                }
            )

        if command == "validate-epub":
            path = Path(_flag_value(args, "--path") or "").expanduser().resolve()
            return _python_api_result(api.validate_epub(path, _epub_config(api)))

        if command == "reset-translations":
            book_id = _flag_value(args, "--book") or ""
            book = api.load_book(books_dir, book_id)
            input_value = _flag_value(args, "--input", required=False)
            return _python_api_result(
                api.reset_translations(
                    books_dir,
                    book,
                    input_path=Path(input_value).expanduser().resolve() if input_value else None,
                    reset_all=_has_flag(args, "--all"),
                )
            )

    raise ValueError(f"不支持的 Python API 命令：{command}")


def novel_translator_diagnostic(novel_root: Path | None = None, python_bin: Path | None = None) -> dict[str, Any]:
    root = (novel_root or resolve_novel_translator_root()).expanduser().resolve()
    py_bin = (python_bin or resolve_novel_translator_python(root)).expanduser().resolve()
    checks = {
        "root_exists": root.is_dir(),
        "main_py_exists": (root / "main.py").is_file(),
        "app_package_exists": (root / "app").is_dir(),
        "python_exists": py_bin.is_file(),
    }
    return {
        "status": "ok" if checks["root_exists"] and checks["app_package_exists"] else "error",
        "root": str(root),
        "python": str(py_bin),
        "checks": checks,
        "setup": "设置 NOVEL_TRANSLATOR_ROOT，或使用仓库内 vendor/novel-translator 运行时。",
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
    """Run one of the six book operations through the vendored Python API.

    ``timeout``, ``python_bin`` and ``output_limit`` remain compatibility
    parameters for callers that still pass them. These file operations run in
    process and do not use subprocess termination for timeouts.
    """
    del python_bin, timeout, output_limit
    root = (novel_root or resolve_novel_translator_root()).expanduser().resolve()
    command = args[0] if args else ""
    if command not in _PYTHON_API_COMMANDS:
        result = ToolResult(
            status="error",
            returncode=1,
            errors=[{"code": "unsupported_operation", "message": f"不支持的 Novel Translator 操作：{command}"}],
            summary={"operation": command},
        )
        raise NovelToolError("Novel Translator 操作不受支持", result)
    try:
        return _call_python_api(root, tuple(args))
    except NovelToolError:
        raise
    except Exception as exc:
        result = ToolResult(
            status="error",
            returncode=1,
            errors=[{"code": type(exc).__name__, "message": str(exc)}],
            summary={"operation": command},
        )
        raise NovelToolError("Novel Translator Python API 执行失败", result) from exc
