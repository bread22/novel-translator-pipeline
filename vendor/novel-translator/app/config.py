from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import ast
import os

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - depends on Python version
    tomllib = None


@dataclass(frozen=True)
class LlmConfig:
    base_url: str
    api_key: str
    model: str
    timeout: int = 600


@dataclass(frozen=True)
class TranslationConfig:
    target_language: str = "zh-Hans"
    source_language: str = "auto"
    system_prompt_file: str = "prompts/novel_translation_system.md"
    style_guide: str = "自然流畅的简体中文小说译文，忠实原意，避免生硬直译。"
    dialogue_style: str = "符合中文网文/出版小说阅读习惯，称谓、语气和人物关系保持连续。"
    style_sample_file: str = ""
    style_sample_max_chars: int = 1200
    batch_max_chars: int = 4000
    temperature: float = 0.3
    max_tokens: int = 4096
    retry_count: int = 3
    retry_delay: int = 2
    quality_passes: int = 2
    fail_on_placeholder_mismatch: bool = True
    fail_on_empty_translation: bool = True


@dataclass(frozen=True)
class ContextConfig:
    previous_paragraphs: int = 3
    next_paragraphs: int = 2
    chapter_summary_max_chars: int = 1200


@dataclass(frozen=True)
class ReviewConfig:
    mode: str = "risk"
    sample_ratio: float = 0.05
    max_items_per_run: int = 200
    severity_threshold: str = "medium"


@dataclass(frozen=True)
class AutomationConfig:
    workers: int = 200
    rpm: int = 200
    tpm: int = 0
    stop_on_warning: bool = False
    auto_retry_failed: bool = True
    folder_input_dir: str = "../原文"
    folder_output_dir: str = "../已翻译"
    work_records_dir: str = "../workspace/books"


@dataclass(frozen=True)
class ExportConfig:
    bilingual: bool = False


@dataclass(frozen=True)
class QualityConfig:
    source_residual_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class EpubConfig:
    parser: str = "auto"
    include_non_linear_spine: bool = False
    preserve_outer_markup: bool = True
    warn_on_ruby: bool = True
    warn_on_duplicate_source: bool = True
    translate_nav: bool = True
    translate_toc: bool = True
    preserve_inline_tags: bool = True
    inline_safe_tags: tuple[str, ...] = ("span", "strong", "em", "a")


@dataclass(frozen=True)
class AppConfig:
    root: Path
    llm: LlmConfig
    translation: TranslationConfig
    context: ContextConfig
    review: ReviewConfig
    automation: AutomationConfig
    export: ExportConfig
    quality: QualityConfig
    epub: EpubConfig

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def books_dir(self) -> Path:
        return self.data_dir / "books"

    @property
    def system_prompt_path(self) -> Path:
        configured = Path(self.translation.system_prompt_file)
        if configured.is_absolute():
            return configured
        return self.root / configured

    @property
    def style_sample_path(self) -> Path | None:
        configured = self.translation.style_sample_file.strip()
        if not configured:
            return None
        path = Path(configured)
        if path.is_absolute():
            return path
        return self.root / path


def load_config(root: Path, config_path: Path | None = None) -> AppConfig:
    path = config_path or root / "setting.toml"
    if not path.exists():
        example = root / "setting.example.toml"
        raise FileNotFoundError(f"配置文件不存在：{path}。请复制 {example.name} 为 setting.toml 后填写。")
    raw = load_toml(path)
    llm_raw = raw.get("llm", {})
    translation_raw = raw.get("translation", {})
    context_raw = raw.get("context", {})
    review_raw = raw.get("review", {})
    automation_raw = raw.get("automation", {})
    export_raw = raw.get("export", {})
    quality_raw = raw.get("quality", {})
    epub_raw = raw.get("epub", {})
    llm = LlmConfig(
        base_url=resolve_config_value(
            llm_raw.get("base_url", ""),
            ("NOVEL_TRANSLATOR_BASE_URL", "OPENAI_BASE_URL"),
        ).rstrip("/"),
        api_key=resolve_config_value(
            llm_raw.get("api_key", ""),
            ("NOVEL_TRANSLATOR_API_KEY", "OPENAI_API_KEY"),
        ),
        model=resolve_config_value(
            llm_raw.get("model", ""),
            ("NOVEL_TRANSLATOR_MODEL", "OPENAI_MODEL"),
        ),
        timeout=int(llm_raw.get("timeout", 600)),
    )
    translation = TranslationConfig(
        target_language=str(translation_raw.get("target_language", "zh-Hans")),
        source_language=str(translation_raw.get("source_language", "auto")),
        system_prompt_file=str(
            translation_raw.get("system_prompt_file", "prompts/novel_translation_system.md")
        ),
        style_guide=str(
            translation_raw.get(
                "style_guide",
                "自然流畅的简体中文小说译文，忠实原意，避免生硬直译。",
            )
        ),
        dialogue_style=str(
            translation_raw.get(
                "dialogue_style",
                "符合中文网文/出版小说阅读习惯，称谓、语气和人物关系保持连续。",
            )
        ),
        style_sample_file=str(translation_raw.get("style_sample_file", "")),
        style_sample_max_chars=int(translation_raw.get("style_sample_max_chars", 1200)),
        batch_max_chars=int(translation_raw.get("batch_max_chars", 4000)),
        temperature=float(translation_raw.get("temperature", 0.3)),
        max_tokens=int(translation_raw.get("max_tokens", 4096)),
        retry_count=int(translation_raw.get("retry_count", 3)),
        retry_delay=int(translation_raw.get("retry_delay", 2)),
        quality_passes=int(translation_raw.get("quality_passes", 2)),
        fail_on_placeholder_mismatch=bool(translation_raw.get("fail_on_placeholder_mismatch", True)),
        fail_on_empty_translation=bool(translation_raw.get("fail_on_empty_translation", True)),
    )
    quality = QualityConfig(
        source_residual_patterns=tuple(str(item) for item in quality_raw.get("source_residual_patterns", []))
    )
    context = ContextConfig(
        previous_paragraphs=int(context_raw.get("previous_paragraphs", 3)),
        next_paragraphs=int(context_raw.get("next_paragraphs", 2)),
        chapter_summary_max_chars=int(context_raw.get("chapter_summary_max_chars", 1200)),
    )
    review = ReviewConfig(
        mode=str(review_raw.get("mode", "risk")),
        sample_ratio=float(review_raw.get("sample_ratio", 0.05)),
        max_items_per_run=int(review_raw.get("max_items_per_run", 200)),
        severity_threshold=str(review_raw.get("severity_threshold", "medium")),
    )
    automation = AutomationConfig(
        workers=int(automation_raw.get("workers", 200)),
        rpm=int(automation_raw.get("rpm", 200)),
        tpm=int(automation_raw.get("tpm", 0)),
        stop_on_warning=bool(automation_raw.get("stop_on_warning", False)),
        auto_retry_failed=bool(automation_raw.get("auto_retry_failed", True)),
        folder_input_dir=str(automation_raw.get("folder_input_dir", "../原文")),
        folder_output_dir=str(automation_raw.get("folder_output_dir", "../已翻译")),
        work_records_dir=str(automation_raw.get("work_records_dir", "../workspace/books")),
    )
    export = ExportConfig(
        bilingual=bool(export_raw.get("bilingual", False)),
    )
    epub = EpubConfig(
        parser=str(epub_raw.get("parser", "auto")),
        include_non_linear_spine=bool(epub_raw.get("include_non_linear_spine", False)),
        preserve_outer_markup=bool(epub_raw.get("preserve_outer_markup", True)),
        warn_on_ruby=bool(epub_raw.get("warn_on_ruby", True)),
        warn_on_duplicate_source=bool(epub_raw.get("warn_on_duplicate_source", True)),
        translate_nav=bool(epub_raw.get("translate_nav", True)),
        translate_toc=bool(epub_raw.get("translate_toc", True)),
        preserve_inline_tags=bool(epub_raw.get("preserve_inline_tags", True)),
        inline_safe_tags=tuple(str(item) for item in epub_raw.get("inline_safe_tags", ["span", "strong", "em", "a"])),
    )
    return AppConfig(root=root, llm=llm, translation=translation, context=context, review=review, automation=automation, export=export, quality=quality, epub=epub)


def resolve_config_value(value: object, env_names: tuple[str, ...], default: str = "") -> str:
    configured = str(value or "").strip()
    env_name = _explicit_env_name(configured)
    if env_name:
        return os.environ.get(env_name, default).strip()
    if configured and configured not in {"YOUR_API_KEY", "<API Key>", "<模型服务地址>", "<模型名>"}:
        return configured
    for candidate in env_names:
        env_value = os.environ.get(candidate, "").strip()
        if env_value:
            return env_value
    return "" if configured in {"YOUR_API_KEY", "<API Key>", "<模型服务地址>", "<模型名>"} else configured or default


def _explicit_env_name(value: str) -> str:
    if value.startswith("env:"):
        return value[4:].strip()
    if value.startswith("${") and value.endswith("}"):
        return value[2:-1].strip()
    if value.startswith("$") and len(value) > 1:
        return value[1:].strip()
    return ""


def load_toml(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if tomllib is not None:
        try:
            return tomllib.loads(text)
        except tomllib.TOMLDecodeError:
            return tomllib.loads(_escape_windows_paths_in_basic_strings(text))
    try:
        import tomli

        try:
            return tomli.loads(text)
        except tomli.TOMLDecodeError:
            return tomli.loads(_escape_windows_paths_in_basic_strings(text))
    except ModuleNotFoundError:
        return parse_simple_toml(text)


def _escape_windows_paths_in_basic_strings(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        prefix, separator, suffix = raw_line.partition("=")
        value = suffix.strip()
        if separator and len(value) >= 3 and value[0] == '"' and value[-1] == '"' and ":\\" in value:
            quote_start = suffix.index('"')
            quote_end = suffix.rfind('"')
            inner = suffix[quote_start + 1 : quote_end]
            escaped = inner.replace("\\", "\\\\")
            suffix = f"{suffix[: quote_start + 1]}{escaped}{suffix[quote_end:]}"
            raw_line = prefix + separator + suffix
        lines.append(raw_line)
    return "\n".join(lines)


def parse_simple_toml(text: str) -> dict:
    """Parse the small TOML subset used by setting.example.toml."""
    result: dict[str, dict] = {}
    current: dict | None = None
    pending_key = ""
    pending_value_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if pending_key:
            pending_value_lines.append(line)
            if line.endswith("]"):
                current[pending_key] = ast.literal_eval(" ".join(pending_value_lines)) if current is not None else []
                pending_key = ""
                pending_value_lines = []
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            current = result.setdefault(section, {})
            continue
        if current is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value == "[":
            pending_key = key
            pending_value_lines = [value]
            continue
        if value.lower() in {"true", "false"}:
            current[key] = value.lower() == "true"
            continue
        try:
            current[key] = ast.literal_eval(value)
        except Exception:
            current[key] = value.strip('"')
    return result
