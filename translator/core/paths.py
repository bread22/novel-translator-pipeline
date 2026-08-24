from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from translator.core.config import CONFIG_PATH


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class PathResolver:
    base: Path

    @classmethod
    def for_config(cls, config_path: Path = CONFIG_PATH) -> "PathResolver":
        return cls(config_path.expanduser().resolve().parent)

    def resolve(self, value: str | Path) -> Path:
        path = Path(value).expanduser()
        return path.resolve() if path.is_absolute() else (self.base / path).resolve()

    def output_root(self, config: dict[str, Any]) -> Path:
        return self.resolve(config.get("paths", {}).get("output_root", "output"))

    def translation_policy(self, config: dict[str, Any]) -> Path:
        return self.resolve(config.get("paths", {}).get("translation_policy", "docs/prompts/translation-policy.md"))

    def source_root(self, config: dict[str, Any]) -> Path:
        return self.resolve(config.get("queue", {}).get("source_root", "source"))

    def translated_root(self, config: dict[str, Any]) -> Path:
        return self.resolve(config.get("queue", {}).get("translated_root", "translated"))

    @property
    def prompts_root(self) -> Path:
        return (self.base / "docs" / "prompts").resolve()

    @property
    def frontend_dist(self) -> Path:
        return (PROJECT_ROOT / "frontend" / "dist").resolve()
