from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
import re
import stat
import tempfile
from datetime import datetime, timezone
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]
from typing import Any

from dotenv import dotenv_values, set_key


ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(dotenv_path: Path = ROOT / ".env", override: bool = True) -> None:
    if not dotenv_path.exists():
        return
    try:
        for key, value in dotenv_values(dotenv_path).items():
            if key and value is not None and (override or key not in os.environ):
                os.environ[key] = value
    except Exception:
        pass


def read_env_keys(dotenv_path: Path = ROOT / ".env") -> dict[str, str]:
    """Read all key-values from .env file."""
    if not dotenv_path.exists():
        return {}
    res: dict[str, str] = {}
    try:
        res = {key: value for key, value in dotenv_values(dotenv_path).items() if key and value is not None}
    except Exception:
        pass
    return res


ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def write_env_keys(updates: dict[str, str], dotenv_path: Path = ROOT / ".env") -> None:
    """Atomically apply dotenv updates with correct quoting and mode 0600."""
    dotenv_path = dotenv_path.expanduser().resolve()
    dotenv_path.parent.mkdir(parents=True, exist_ok=True)
    normalized: dict[str, str] = {}
    for raw_key, raw_value in updates.items():
        key = raw_key.strip()
        if not ENV_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"无效环境变量名：{raw_key!r}")
        normalized[key] = str(raw_value)

    fd, temporary_name = tempfile.mkstemp(prefix=f".{dotenv_path.name}.", dir=dotenv_path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            if dotenv_path.exists():
                stream.write(dotenv_path.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        for key, value in normalized.items():
            set_key(str(temporary), key, value, quote_mode="always")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, dotenv_path)
        os.chmod(dotenv_path, stat.S_IRUSR | stat.S_IWUSR)
        for key, value in normalized.items():
            os.environ[key] = value
    finally:
        temporary.unlink(missing_ok=True)


def write_env_key(key: str, value: str, dotenv_path: Path = ROOT / ".env") -> None:
    write_env_keys({key: value}, dotenv_path)


_load_dotenv()

CONFIG_PATH = Path(os.environ.get("TRANSLATOR_CONFIG", ROOT / "config.toml"))
CONFIG_SCHEMA_PATH = ROOT / "schemas" / "config.schema.json"


def _resolve_ref(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ValueError(f"不支持的 schema 引用：{reference}")
    value: Any = root
    for part in reference[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    if not isinstance(value, dict):
        raise ValueError(f"schema 引用不是对象：{reference}")
    return value


def _schema_errors(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "<root>") -> list[str]:
    errors: list[str] = []
    if "$ref" in schema:
        return _schema_errors(value, _resolve_ref(root, str(schema["$ref"])), root, path)
    if "oneOf" in schema:
        candidates = [_schema_errors(value, candidate, root, path) for candidate in schema["oneOf"]]
        matches = [candidate for candidate in candidates if not candidate]
        if len(matches) != 1:
            return [f"{path}: 必须且只能匹配一个 schema 分支"]
        return []
    for item in schema.get("allOf", []):
        errors.extend(_schema_errors(value, item, root, path))
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} 不在 {schema['enum']!r} 中")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: 必须为 {schema['const']!r}")
    expected = str(schema.get("type", ""))
    type_ok = {
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "array": isinstance(value, list),
    }.get(expected, True)
    if not type_ok:
        return errors + [f"{path}: 类型应为 {expected}"]
    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key}: 缺少必需字段")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties:
                    errors.append(f"{path}.{key}: schema 未定义此字段")
        elif isinstance(schema.get("additionalProperties"), dict):
            for key, child_val in value.items():
                if key not in properties:
                    errors.extend(_schema_errors(child_val, schema["additionalProperties"], root, f"{path}.{key}"))
        for key, child in properties.items():
            if key in value:
                errors.extend(_schema_errors(value[key], child, root, f"{path}.{key}"))
    if isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            errors.append(f"{path}: 元素数量小于最小值 {schema['minItems']}")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            errors.append(f"{path}: 元素数量大于最大值 {schema['maxItems']}")
        if "items" in schema:
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, schema["items"], root, f"{path}[{index}]"))
    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            errors.append(f"{path}: 字符串过短")
        if schema.get("pattern") and not re.search(str(schema["pattern"]), value):
            errors.append(f"{path}: 不匹配 {schema['pattern']}")
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: 小于最小值 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: 大于最大值 {schema['maximum']}")
    return errors


def validate_config_data(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("参数文件必须是 TOML 对象")
    schema = json.loads(CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = _schema_errors(value, schema, schema)
    if errors:
        raise ValueError(f"参数文件未通过 {CONFIG_SCHEMA_PATH.name}：{'; '.join(errors)}")
    providers = value["providers"]
    roles = value["roles"]
    knowledge = value.get("knowledge_extractor", {})
    if isinstance(knowledge, dict):
        knowledge_provider = str(knowledge.get("provider", "")).strip()
        if knowledge_provider and knowledge_provider not in providers:
            raise ValueError(
                f"knowledge_extractor.provider 引用了未定义 provider：{knowledge_provider}"
            )
        knowledge_fallbacks = knowledge.get("fallback_providers", [])
        if isinstance(knowledge_fallbacks, list):
            for fallback_provider in knowledge_fallbacks:
                if fallback_provider not in providers:
                    raise ValueError(
                        f"knowledge_extractor.fallback_providers 引用了未定义 provider：{fallback_provider}"
                    )
    
    # Validate primary translator
    primary = roles.get("primary_translator")
    if primary and primary not in providers:
        raise ValueError(f"roles.primary_translator 引用了未定义 provider：{primary}")
        
    # Validate reviewer
    reviewer = roles.get("reviewer")
    if reviewer and reviewer not in providers:
        raise ValueError(f"roles.reviewer 引用了未定义 provider：{reviewer}")

    # Validate secondary reviewer
    secondary_rev = roles.get("secondary_reviewer")
    if secondary_rev and secondary_rev not in providers:
        raise ValueError(f"roles.secondary_reviewer 引用了未定义 provider：{secondary_rev}")
        
    # Validate fallbacks
    fallbacks = roles.get("fallback_translators")
    if isinstance(fallbacks, list):
        for fb in fallbacks:
            if fb not in providers:
                raise ValueError(f"roles.fallback_translators 引用了未定义 provider：{fb}")
    if roles.get("fallback_translator") and roles["fallback_translator"] not in providers:
        raise ValueError(f"roles.fallback_translator 引用了未定义 provider：{roles['fallback_translator']}")
    if roles.get("secondary_fallback_translator") and roles["secondary_fallback_translator"] not in providers:
        raise ValueError(f"roles.secondary_fallback_translator 引用了未定义 provider：{roles['secondary_fallback_translator']}")
    for fallback_reviewer in roles.get("fallback_reviewers", []):
        if fallback_reviewer not in providers:
            raise ValueError(f"roles.fallback_reviewers 引用了未定义 provider：{fallback_reviewer}")
    return value


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.expanduser().resolve().open("rb") as stream:
        value = tomllib.load(stream)
    return validate_config_data(value)


def config_sha256(path: Path = CONFIG_PATH) -> str:
    import hashlib

    return hashlib.sha256(path.expanduser().resolve().read_bytes()).hexdigest()


def create_config_backup(path: Path = CONFIG_PATH) -> Path:
    """Create an immutable, timestamped sibling backup of a valid config."""
    source = path.expanduser().resolve()
    load_config(source)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = source.with_name(f"{source.name}.bak.{stamp}")
    shutil.copy2(source, backup)
    return backup


def list_config_backups(path: Path = CONFIG_PATH) -> list[Path]:
    target = path.expanduser().resolve()
    return sorted(target.parent.glob(f"{target.name}.bak.*"), reverse=True)


def restore_config_backup(backup: Path, path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Validate a sibling timestamped backup and atomically restore it."""
    target = path.expanduser().resolve()
    source = backup.expanduser().resolve()
    expected_prefix = f"{target.name}.bak."
    if source.parent != target.parent or not source.name.startswith(expected_prefix):
        raise ValueError(f"备份必须是 {target.parent / (expected_prefix + '<timestamp>')}")
    restored = load_config(source)
    if target.exists():
        create_config_backup(target)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.restore.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        load_config(temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return restored


def config_value(config: dict[str, Any], dotted: str) -> Any:
    value: Any = config
    for key in dotted.split("."):
        if not isinstance(value, dict) or key not in value:
            raise KeyError(f"参数文件缺少：{dotted}")
        value = value[key]
    return value


def setting(config: dict[str, Any], dotted: str, env_name: str | None = None) -> Any:
    """Read the parameter file; environment variables remain explicit overrides."""
    if env_name and env_name in os.environ:
        return os.environ[env_name]
    return config_value(config, dotted)


def primary_translator_name(config: dict[str, Any]) -> str:
    return str(setting(config, "roles.primary_translator", "PRIMARY_TRANSLATOR")).strip()


def fallback_translators_names(config: dict[str, Any]) -> list[str]:
    env_override = os.environ.get("FALLBACK_TRANSLATORS") or os.environ.get("FALLBACK_TRANSLATOR")
    if env_override:
        return [item.strip() for item in env_override.split(",") if item.strip()]
    roles = config.get("roles", {})
    if "fallback_translators" in roles and isinstance(roles["fallback_translators"], list):
        return [str(item).strip() for item in roles["fallback_translators"] if str(item).strip()]
    result: list[str] = []
    if "fallback_translator" in roles:
        result.append(str(roles["fallback_translator"]).strip())
    if "secondary_fallback_translator" in roles:
        sec = str(roles["secondary_fallback_translator"]).strip()
        if sec and sec not in result:
            result.append(sec)
    return result or ["lmstudio"]




def secondary_reviewer_name(config: dict[str, Any]) -> str:
    roles = config.get("roles", {})
    return str(roles.get("secondary_reviewer", "") or os.environ.get("SECONDARY_REVIEWER", "")).strip()


def fallback_reviewers_names(config: dict[str, Any]) -> list[str]:
    env_override = os.environ.get("FALLBACK_REVIEWERS")
    if env_override:
        return [item.strip() for item in env_override.split(",") if item.strip()]
    roles = config.get("roles", {})
    result: list[str] = []
    secondary = str(roles.get("secondary_reviewer", "") or "").strip()
    if secondary:
        result.append(secondary)
    configured = roles.get("fallback_reviewers", [])
    if isinstance(configured, list):
        for item in configured:
            value = str(item).strip()
            if value and value not in result:
                result.append(value)
    return result


def dual_review_enabled(config: dict[str, Any]) -> bool:
    roles = config.get("roles", {})
    if "DUAL_REVIEW" in os.environ:
        return os.environ["DUAL_REVIEW"].lower() in {"1", "true", "yes", "on"}
    return bool(roles.get("dual_review", False))


def reviewer_name(config: dict[str, Any]) -> str:
    return str(setting(config, "roles.reviewer", "REVIEWER")).strip()
