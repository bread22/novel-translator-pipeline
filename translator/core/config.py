from __future__ import annotations

import json
import os
from pathlib import Path
import re
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]
from typing import Any


ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(dotenv_path: Path = ROOT / ".env") -> None:
    if not dotenv_path.exists():
        return
    try:
        content = dotenv_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


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
        errors.extend(_schema_errors(value, _resolve_ref(root, str(schema["$ref"])), root, path))
    for item in schema.get("allOf", []):
        errors.extend(_schema_errors(value, item, root, path))
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} 不在 {schema['enum']!r} 中")
    expected = schema.get("type")
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


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("rb") as stream:
        value = tomllib.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"参数文件必须是 TOML 对象：{path}")
    schema = json.loads(CONFIG_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = _schema_errors(value, schema, schema)
    if errors:
        raise ValueError(f"参数文件未通过 {CONFIG_SCHEMA_PATH.name}：{'; '.join(errors)}")
    providers = value["providers"]
    roles = value["roles"]
    
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
    if "fallback_translator" in roles and roles["fallback_translator"] not in providers:
        raise ValueError(f"roles.fallback_translator 引用了未定义 provider：{roles['fallback_translator']}")
    if "secondary_fallback_translator" in roles and roles["secondary_fallback_translator"] not in providers:
        raise ValueError(f"roles.secondary_fallback_translator 引用了未定义 provider：{roles['secondary_fallback_translator']}")
        
    return value


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


def reviewer_name(config: dict[str, Any]) -> str:
    return str(setting(config, "roles.reviewer", "REVIEWER")).strip()


def secondary_reviewer_name(config: dict[str, Any]) -> str:
    roles = config.get("roles", {})
    return str(roles.get("secondary_reviewer", "") or os.environ.get("SECONDARY_REVIEWER", "")).strip()


def dual_review_enabled(config: dict[str, Any]) -> bool:
    roles = config.get("roles", {})
    if "DUAL_REVIEW" in os.environ:
        return os.environ["DUAL_REVIEW"].lower() in {"1", "true", "yes", "on"}
    return bool(roles.get("dual_review", False))


def reviewer_name(config: dict[str, Any]) -> str:
    return str(setting(config, "roles.reviewer", "REVIEWER")).strip()
