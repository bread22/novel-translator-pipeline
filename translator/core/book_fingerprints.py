"""Conservative upload identity, independent of filenames and book IDs.

Body v1 hashes original paragraphs in manifest reading order, preserving chapter
and paragraph boundaries. Covers/TOCs and generated chapter titles are excluded.
No translations, punctuation removal, case folding, or NFKC folding participate.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import logging
from pathlib import Path
import unicodedata
from typing import Any

from translator.core.workspace import BookWorkspace, read_json, write_json

BODY_VERSION = "original-paragraphs-nfc-v1"
FINGERPRINT_FILE = "upload-fingerprints.json"


def body_sha256(manifest: dict[str, Any]) -> str | None:
    chapters = []
    for chapter in manifest.get("chapters", []):
        if chapter.get("role", "chapter") in {"cover", "toc"}:
            continue
        paragraphs = []
        for paragraph in chapter.get("paragraphs", []):
            source = paragraph.get("source", "")
            if not isinstance(source, str):
                continue
            normalized = " ".join(unicodedata.normalize("NFC", source).split())
            if normalized:
                paragraphs.append(normalized)
        if paragraphs:
            chapters.append(paragraphs)
    # Empty/image-only books must never all collide on the empty-body hash.
    if not chapters:
        return None
    canonical = json.dumps([BODY_VERSION, chapters], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@lru_cache(maxsize=1024)
def _file_sha256(path: Path, size: int, mtime_ns: int, ctime_ns: int) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    path = path.resolve()
    stat = path.stat()
    return _file_sha256(path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _signature(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()), "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns,
        "inode": stat.st_ino, "device": stat.st_dev,
    }


def _valid_hash(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _original_source(manifest_path: Path, manifest: dict[str, Any], output_root: Path) -> Path | None:
    source_type = manifest.get("source_type")
    candidates = [manifest_path.parent / f"source{suffix}" for suffix in (
        (f".{source_type}",) if source_type in {"epub", "txt"} else (".epub", ".txt")
    )]
    if source_type != "txt":
        try:
            candidates.append(BookWorkspace.at(output_root, manifest.get("title", manifest_path.parent.name)).original_epub)
        except ValueError:
            pass
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def existing_fingerprints(manifest_path: Path, manifest: dict[str, Any], output_root: Path) -> dict[str, Any]:
    """Lazily persist legacy identity, without modifying source or translations.

    The upload caller holds the catalog lock. Re-read the manifest rather than
    binding a potentially stale caller snapshot to a newer stat signature.
    Before/after checks prevent caching a hash against a concurrently changed
    input. This sidecar is a disposable cache, never a translation authority.
    """
    del manifest
    cache_path = manifest_path.parent / FINGERPRINT_FILE
    try:
        saved = read_json(cache_path, {})
    except (OSError, ValueError):
        saved = {}
    if not isinstance(saved, dict):
        saved = {}
    version_ok = saved.get("body_version") == BODY_VERSION
    body_valid = "body_sha256" in saved and (saved["body_sha256"] is None or _valid_hash(saved["body_sha256"]))
    for _ in range(3):
        try:
            manifest_stat = _signature(manifest_path)
            current = read_json(manifest_path)
            if not isinstance(current, dict):
                raise ValueError(f"Invalid book manifest: {manifest_path}")
            body = (saved["body_sha256"]
                    if version_ok and body_valid and saved.get("manifest_stat") == manifest_stat
                    else body_sha256(current))
            source = _original_source(manifest_path, current, output_root)
            source_stat = _signature(source) if source is not None else None
            source_hash = None
            if source is not None:
                source_hash = (saved["source_sha256"]
                               if version_ok and _valid_hash(saved.get("source_sha256"))
                               and saved.get("source_stat") == source_stat else file_sha256(source))
            elif version_ok and body_valid and saved["body_sha256"] == body and _valid_hash(saved.get("source_sha256")):
                source_hash = saved["source_sha256"]
            # Reject mixed snapshots, including disappearance/reappearance of
            # the preferred original while a fallback is being inspected.
            if _signature(manifest_path) != manifest_stat or _original_source(manifest_path, current, output_root) != source:
                continue
            if source is not None and _signature(source) != source_stat:
                continue
        except FileNotFoundError:
            continue
        record = {
            "source_sha256": source_hash, "body_sha256": body, "body_version": BODY_VERSION,
            "manifest_stat": manifest_stat, "source_stat": source_stat,
        }
        if record != saved:
            try:
                write_json(cache_path, record)
            except OSError:
                # Cache failure must not turn a detected duplicate into an import.
                logging.getLogger(__name__).warning("Fingerprint cache write failed: %s", cache_path, exc_info=True)
        return record
    raise OSError(f"Book changed repeatedly while computing fingerprints: {manifest_path}")
