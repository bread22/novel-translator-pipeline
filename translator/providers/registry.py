from __future__ import annotations

from typing import Any

from translator.core.config import load_config
from translator.providers.antigravity import AntigravityProvider
from translator.providers.base import BaseProvider
from translator.providers.codex import CodexProvider
from translator.providers.opencode import OpenCodeProvider
from translator.providers.openai_provider import OpenAIProvider


def get_provider(name: str, config: dict[str, Any] | None = None) -> BaseProvider:
    """Instantiate a provider adapter by its name in config.toml."""
    cfg = config or load_config()
    providers = cfg.get("providers", {})
    if name not in providers:
        matching = [p_name for p_name, p_val in providers.items() if isinstance(p_val, dict) and p_val.get("type") == name]
        if matching:
            p_config = dict(providers[matching[0]])
            name = matching[0]
        elif name in {"opencode", "antigravity", "codex", "online_api", "openai"}:
            p_config = {"type": "openai" if name in {"online_api", "openai"} else name}
        else:
            raise ValueError(f"未在 config.toml 的 [providers] 中找到 provider: '{name}'")
    else:
        p_config = dict(providers[name])
    p_type = str(p_config.get("type", "")).strip().casefold()

    if p_type in {"openai", "http"}:
        return OpenAIProvider(name, p_config)
    if p_type == "antigravity":
        return AntigravityProvider(name, p_config)
    if p_type == "opencode":
        return OpenCodeProvider(name, p_config)
    if p_type == "codex":
        return CodexProvider(name, p_config)

    # Auto-infer provider type if not explicitly set
    if "agy" in p_config or name == "antigravity":
        return AntigravityProvider(name, p_config)
    if "binary" in p_config:
        bin_name = str(p_config.get("binary", "")).casefold()
        if "opencode" in bin_name or name == "opencode":
            return OpenCodeProvider(name, p_config)
        if "codex" in bin_name or name == "codex":
            return CodexProvider(name, p_config)
    if "base_url" in p_config or name == "lmstudio":
        return OpenAIProvider(name, p_config)

    raise ValueError(f"无法确定 provider '{name}' 的类型，请在配置中指定 type = 'openai'|'antigravity'|'opencode'|'codex'")


create_provider = get_provider

__all__ = ["create_provider", "get_provider"]
