"""Versioned access to book JSON authorities.

All transactions are short: model calls must run outside these contexts. Locks
serialize cooperating threads/processes; multi-file failures roll back within the
process. This is not a crash-atomic database/WAL transaction.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from translator.core.workspace import BookWorkspace, json_file_lock, read_json, write_json, utc_now


class VersionConflict(RuntimeError):
    """The caller's snapshot no longer matches the persisted authority."""


def revision(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    value: Any
    revision: str


def _restore_bytes(path: Path, value: bytes | None) -> None:
    if value is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def file_transaction(paths: list[Path]):
    """Common rollback boundary for legacy operations that write files directly."""
    ordered = sorted(set(paths), key=lambda p: str(p.resolve()))
    with ExitStack() as stack:
        for path in ordered:
            stack.enter_context(json_file_lock(path))
        originals = {path: path.read_bytes() if path.exists() else None for path in ordered}
        try:
            yield
        except BaseException:
            errors = []
            for path, content in originals.items():
                try:
                    _restore_bytes(path, content)
                except OSError as exc:
                    errors.append(exc)
            if errors:
                raise RuntimeError('书籍事务回滚失败；需要检查存储') from errors[0]
            raise


@contextmanager
def transaction(paths: list[Path], *, expected: Mapping[Path, str] | None = None) -> Iterator[dict[Path, Any]]:
    """Lock in canonical order and publish changed documents, or restore all.

    Missing documents are represented by None. Callers mutate the yielded map;
    deletion is explicit via None. Revisions do not change legacy JSON schemas.
    """
    ordered = sorted(set(paths), key=lambda p: str(p.resolve()))
    with file_transaction(ordered):
        original = {path: read_json(path) for path in ordered}
        for path, version in (expected or {}).items():
            if path not in original or revision(original[path]) != version:
                raise VersionConflict(f'书籍数据版本已变化：{path.name}')
        documents = deepcopy(original)
        yield documents
        for path in ordered:
            if documents[path] == original[path]:
                continue
            if documents[path] is None:
                path.unlink(missing_ok=True)
            else:
                write_json(path, documents[path])


@dataclass(frozen=True)
class JsonDocument:
    path: Path

    def snapshot(self, default: Any = None) -> Snapshot:
        with json_file_lock(self.path):
            raw = read_json(self.path)
            return Snapshot(deepcopy(default if raw is None else raw), revision(raw))

    def update(self, mutate: Callable[[Any], Any], *, default: Any = None, expected_revision: str | None = None) -> Snapshot:
        expected = {self.path: expected_revision} if expected_revision is not None else None
        with transaction([self.path], expected=expected) as documents:
            current = documents[self.path]
            documents[self.path] = mutate(deepcopy(default if current is None else current))
            result = deepcopy(documents[self.path])
        return Snapshot(result, revision(result))

    def replace(self, value: Any, *, expected_revision: str) -> Snapshot:
        return self.update(lambda _: value, expected_revision=expected_revision)

    def patch(self, fields: Mapping[str, Any]) -> Snapshot:
        return self.update(lambda current: {**current, **fields}, default={})


@dataclass(frozen=True)
class BookRepository:
    manifest_path: Path | None = None
    workspace: BookWorkspace | None = None

    @property
    def manifest(self) -> JsonDocument:
        if self.manifest_path is None:
            raise ValueError('Manifest path is required')
        return JsonDocument(self.manifest_path)

    @property
    def glossary(self) -> JsonDocument:
        if self.workspace is None:
            raise ValueError('Workspace is required')
        return JsonDocument(self.workspace.glossary_path)

    @property
    def progress(self) -> JsonDocument:
        if self.workspace is None:
            raise ValueError('Workspace is required')
        return JsonDocument(self.workspace.progress_path)

    def update_paragraphs(self, translations: Mapping[str, str], *, baseline: Mapping[str, Any] | None = None) -> Snapshot:
        """Merge selected paragraphs, rejecting stale selected records only."""
        before = self.paragraphs(baseline) if baseline is not None else None
        def merge(manifest: Any) -> Any:
            if not manifest:
                raise FileNotFoundError(self.manifest.path)
            current = self.paragraphs(manifest)
            for item_id in translations:
                if item_id not in current:
                    if before is not None:
                        raise VersionConflict(f'段落已移除：{item_id}')
                    raise KeyError(item_id)
                if before is not None and before.get(item_id) != current[item_id]:
                    raise VersionConflict(f'段落已修改：{item_id}')
            for item_id, text in translations.items():
                current[item_id]['translated'] = text
                current[item_id]['updated_at'] = utc_now()
            return manifest
        return self.manifest.update(merge)

    @staticmethod
    def paragraphs(manifest: Mapping[str, Any]) -> dict[str, Any]:
        return {str(p['id']): p for ch in manifest.get('chapters', []) for p in ch.get('paragraphs', [])}

    @contextmanager
    def glossary_transaction(self, *, expected_revision: str | None = None):
        if self.workspace is None:
            raise ValueError('Workspace is required')
        from translator.glossary.projection import build_translation_term_projection
        authority = self.workspace.glossary_path
        projection = self.workspace.novel_translator_terms_path
        expected = {authority: expected_revision} if expected_revision is not None else None
        with transaction([authority, projection], expected=expected) as documents:
            glossary = documents[authority]
            if glossary is None:
                glossary = {'schema_version': '3.0', 'terms': [], 'conflicts': [], 'revisions': []}
            yield glossary
            documents[authority] = glossary
            documents[projection] = build_translation_term_projection(glossary)

    def save_glossary(self, glossary: Mapping[str, Any], *, expected_revision: str | None = None) -> None:
        with self.glossary_transaction(expected_revision=expected_revision) as current:
            current.clear()
            current.update(deepcopy(glossary))
