"""Conservative upload identity, independent of filenames and book IDs.

Body v1 hashes original paragraphs in manifest reading order, preserving chapter
and paragraph boundaries. Covers/TOCs and generated chapter titles are excluded.
No translations, punctuation removal, case folding, or NFKC folding participate.
"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path
import unicodedata
from typing import Any

from translator.core.workspace import BookWorkspace, read_json

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


def existing_fingerprints(manifest_path: Path, manifest: dict[str, Any], output_root: Path) -> dict[str, Any]:
    """Read legacy books without rewriting manifests or translation progress.

    A saved source is preferred to stored hashes, so externally replaced files
    do not keep an obsolete identity. If sources are missing, original manifest
    paragraphs still support body matching. Never hash exported translations.
    """
    body = body_sha256(manifest)
    source_type = manifest.get("source_type")
    candidates = [manifest_path.parent / f"source{suffix}" for suffix in (
        (f".{source_type}",) if source_type in {"epub", "txt"} else (".epub", ".txt")
    )]
    if source_type != "txt":
        try:
            candidates.append(BookWorkspace.at(output_root, manifest.get("title", manifest_path.parent.name)).original_epub)
        except ValueError:
            pass
    source_hash = None
    for candidate in candidates:
        if candidate.is_file():
            source_hash = file_sha256(candidate)
            break
    if source_hash is None:
        saved = read_json(manifest_path.parent / FINGERPRINT_FILE, {})
        if isinstance(saved, dict) and saved.get("body_version") == BODY_VERSION and saved.get("body_sha256") == body:
            source_hash = saved.get("source_sha256")
    return {"source_sha256": source_hash, "body_sha256": body, "body_version": BODY_VERSION}
