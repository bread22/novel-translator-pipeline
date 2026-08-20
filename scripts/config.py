from __future__ import annotations

import os
from pathlib import Path
import tomllib
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(os.environ.get("TRANSLATOR_CONFIG", ROOT / "config.toml"))


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("rb") as stream:
        value = tomllib.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"参数文件必须是 TOML 对象：{path}")
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
