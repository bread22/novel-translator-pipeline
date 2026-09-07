"""Explicit, per-execution configuration and book locations (no shared adapters)."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from translator.core.config import CONFIG_PATH, load_config
from translator.core.paths import PathResolver
from translator.core.workspace import BookWorkspace


@dataclass(frozen=True)
class BookExecutionContext:
    book_id: str
    manifest: Path
    workspace: BookWorkspace
    novel_root: Path
    config_path: Path
    translation_policy: Path
    _config: dict[str, Any]

    @classmethod
    def create(
        cls, *, book_id: str, manifest: Path, workspace: BookWorkspace, novel_root: Path,
        config: dict[str, Any] | None = None, config_path: Path | None = None,
        translation_policy: Path | None = None,
    ) -> 'BookExecutionContext':
        path = (config_path or CONFIG_PATH).expanduser().resolve()
        config = deepcopy(config if config is not None else load_config(path))
        paths = PathResolver.for_config(path)
        policy = paths.resolve(translation_policy) if translation_policy is not None else paths.translation_policy(config)
        return cls(book_id, manifest.resolve(), workspace, novel_root.resolve(), path, policy, config)

    @property
    def config(self) -> dict[str, Any]:
        # Adapters get independent snapshots; one provider cannot alter its peers.
        return deepcopy(self._config)

    def translator(self, *, factory: Callable[..., Any] | None = None, timeout: int = 600):
        if factory is None:
            from translator.providers.translator import ProviderTranslator
            factory = ProviderTranslator
        return factory(
            novel_root=self.novel_root, manifest=self.manifest, glossary_path=self.workspace.glossary_path,
            timeout=timeout, config=self.config, config_path=self.config_path, translation_policy=self.translation_policy,
        )

    def review(self, input_path: Path, output_path: Path, *, factory: Callable[..., Any] | None = None, **options: Any) -> None:
        if factory is None:
            from translator.review.reviewer import run_chapter_review
            factory = run_chapter_review
        factory(input_path, output_path, config=self.config, **options)
