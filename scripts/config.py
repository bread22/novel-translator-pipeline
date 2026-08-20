from __future__ import annotations

import os
import json
from pathlib import Path
import re
import tomllib
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
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
        for key, child in properties.items():
            if key in value:
                errors.extend(_schema_errors(value[key], child, root, f"{path}.{key}"))
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
    for role, provider in value["roles"].items():
        if provider not in providers:
            raise ValueError(f"roles.{role} 引用了未定义 provider：{provider}")
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
