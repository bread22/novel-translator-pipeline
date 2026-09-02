from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no advisory flock.
    fcntl = None  # type: ignore[assignment]
import hashlib
import json
from pathlib import Path
import re
import threading
import unicodedata
from typing import Literal, Sequence

from translator.glossary.name_normalizer import NameNormalization, normalize_japanese_name


JP_HONORIFICS: tuple[str, ...] = (
    "先生",
    "先輩",
    "さん",
    "様",
    "さま",
    "ちゃん",
    "くん",
    "君",
    "氏",
    "殿",
)
ZH_HONORIFICS: tuple[str, ...] = (
    "先生",
    "老师",
    "女士",
    "小姐",
    "同学",
    "学长",
    "学姐",
    "前辈",
    "君",
    # A translated candidate may still carry the source-language suffix.
    "先輩",
    "さん",
    "様",
    "さま",
    "ちゃん",
    "くん",
    "氏",
    "殿",
)
NAME_CATEGORIES = frozenset({"person", "person_alias", "author"})
_CJK_RE = re.compile(r"^[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+$")
_CJK_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


@dataclass(frozen=True)
class NameCheckResult:
    status: Literal["not_applicable", "pass", "corrected", "ambiguous"]
    original_source: str
    original_target: str
    name_source: str
    name_target: str
    expected_target: str
    source_honorific: str = ""
    target_honorific: str = ""
    reason: str = ""
    mismatch_positions: tuple[int, ...] = ()
    normalization_method: str = ""
    normalization_version: str = ""
    normalized_candidates: tuple[str, ...] = ()
    selected_candidate: str = ""
    normalization_diagnostics: tuple[str, ...] = ()
    normalization_warning: str = ""


def _split_honorific(value: str, suffixes: Sequence[str]) -> tuple[str, str]:
    value = unicodedata.normalize("NFKC", value).strip()
    for suffix in sorted(suffixes, key=len, reverse=True):
        if not value.endswith(suffix) or len(value) <= len(suffix):
            continue
        prefix = value[: -len(suffix)].strip()
        # 君 is also a regular character in some names. Keep one-character
        # prefixes ambiguous instead of stripping it automatically.
        if suffix == "君" and len(prefix) < 2:
            continue
        return prefix, suffix
    return value, ""


def _is_cjk_name(value: str) -> bool:
    return bool(_CJK_RE.fullmatch(value))


def _normalization_warning(normalization: NameNormalization) -> str:
    warning_codes = (
        "opencc_backend_error",
        "opencc_unmapped",
        "candidate_overflow",
        "opencc_version_mismatch",
    )
    return next((code for code in warning_codes if code in normalization.diagnostics), "")


def check_person_name(source: str, target: str, category: object) -> NameCheckResult | None:
    """Check only the name portion of a person candidate.

    OpenCC candidate ambiguity is diagnostic-only. Structural name failures
    remain ``ambiguous`` for the legacy queue boundary; backend/data failures
    preserve the model target as an accepted, warning-bearing result.
    """
    canonical_category = str(category or "").strip().casefold()
    if canonical_category not in NAME_CATEGORIES:
        return None

    original_source = unicodedata.normalize("NFKC", str(source)).strip()
    original_target = unicodedata.normalize("NFKC", str(target)).strip()
    name_source, source_honorific = _split_honorific(original_source, JP_HONORIFICS)
    name_target, target_honorific = _split_honorific(original_target, ZH_HONORIFICS)

    base = {
        "original_source": original_source,
        "original_target": original_target,
        "name_source": name_source,
        "name_target": name_target,
        "source_honorific": source_honorific,
        "target_honorific": target_honorific,
    }

    def result(
        status: Literal["not_applicable", "pass", "corrected", "ambiguous"],
        *,
        expected_target: str = "",
        reason: str = "",
        mismatch_positions: tuple[int, ...] = (),
        normalization: NameNormalization | None = None,
        selected_candidate: str = "",
    ) -> NameCheckResult:
        warning = _normalization_warning(normalization) if normalization is not None else ""
        return NameCheckResult(
            status=status,
            expected_target=expected_target,
            reason=reason,
            mismatch_positions=mismatch_positions,
            normalization_method=normalization.method if normalization is not None else "",
            normalization_version=normalization.data_version if normalization is not None else "",
            normalized_candidates=normalization.candidates if normalization is not None else (),
            selected_candidate=selected_candidate or (normalization.preferred if normalization is not None else ""),
            normalization_diagnostics=normalization.diagnostics if normalization is not None else (),
            normalization_warning=warning,
            **base,
        )

    if not name_source or not name_target:
        return result("ambiguous", reason="empty_name_after_honorific_split")
    # The first phase targets kanji names. If the source name contains no CJK kanji,
    # it is a transliterated/phonetic name (katakana/hiragana/latin) and does not use
    # 1:1 character alignment. Return None to let standard glossary validation proceed.
    if not _CJK_CHAR_RE.search(name_source) or re.search(r"[\u3040-\u309f\u30a0-\u30ff]", name_source):
        return None
    # Let the common validator report target script/format errors instead of
    # turning a malformed target into a mapping-review queue item.
    if re.search(r"[\u3040-\u309f\u30a0-\u30ff]", name_target):
        return None
    if not _is_cjk_name(name_source) or not _is_cjk_name(name_target):
        return result("ambiguous", reason="name_is_not_cjk_aligned")
    if len(name_source) != len(name_target):
        return result("ambiguous", reason="name_length_mismatch")

    normalization = normalize_japanese_name(name_source)
    expected_target = normalization.preferred
    mismatches = tuple(
        index
        for index, (actual, expected) in enumerate(zip(name_target, expected_target))
        if actual != expected
    )
    if name_target in normalization.candidates:
        reason = "opencc_nonpreferred_candidate" if mismatches else ""
        return result(
            "pass",
            expected_target=expected_target,
            reason=reason,
            mismatch_positions=mismatches,
            normalization=normalization,
        )

    # If an unmatched position has no OpenCC data, the backend has no basis to
    # rewrite the model output. Preserve it and continue with a visible warning
    # rather than creating a blocking review item.
    unmapped_mismatch = tuple(index for index in mismatches if index in normalization.unmapped_positions)
    warning = _normalization_warning(normalization)
    if unmapped_mismatch or warning in {"opencc_backend_error", "candidate_overflow"}:
        return result(
            "pass",
            expected_target=name_target,
            reason=warning or "opencc_unmapped",
            mismatch_positions=mismatches,
            normalization=normalization,
            selected_candidate=name_target,
        )
    return result(
        "corrected",
        expected_target=expected_target,
        reason="opencc_preferred_mapping",
        mismatch_positions=mismatches,
        normalization=normalization,
    )


_QUEUE_LOCKS: dict[Path, threading.RLock] = {}
_QUEUE_LOCKS_GUARD = threading.Lock()


def append_name_mapping_review(
    path: Path,
    check: NameCheckResult,
    *,
    chapter_id: str,
    reporter: str,
    evidence_ids: Sequence[str],
) -> bool:
    """Append one deduplicated pending name mapping to the JSONL review queue."""
    if check.status != "ambiguous":
        return False
    path = path.expanduser().resolve()
    key_payload = {
        "name_source": check.name_source,
        "name_target": check.name_target,
        "expected_target": check.expected_target,
        "reason": check.reason,
    }
    item_id = hashlib.sha256(json.dumps(key_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    record = {
        "id": item_id,
        "status": "pending",
        "source": check.original_source,
        "target": check.original_target,
        "name_source": check.name_source,
        "name_target": check.name_target,
        "expected_target": check.expected_target,
        "source_honorific": check.source_honorific,
        "target_honorific": check.target_honorific,
        "reason": check.reason,
        "mismatch_positions": list(check.mismatch_positions),
        "normalization_method": check.normalization_method,
        "normalization_version": check.normalization_version,
        "normalized_candidates": list(check.normalized_candidates),
        "selected_candidate": check.selected_candidate,
        "normalization_diagnostics": list(check.normalization_diagnostics),
        "normalization_warning": check.normalization_warning,
        "chapter_id": str(chapter_id),
        "reporter": str(reporter),
        "evidence_ids": [str(value) for value in evidence_ids],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    with _QUEUE_LOCKS_GUARD:
        thread_lock = _QUEUE_LOCKS.setdefault(path, threading.RLock())
    with thread_lock:
        lock_path = path.with_name(f".{path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                if path.exists():
                    for line in path.read_text(encoding="utf-8").splitlines():
                        try:
                            if json.loads(line).get("id") == item_id:
                                return False
                        except (TypeError, json.JSONDecodeError):
                            continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as output:
                    output.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    output.flush()
                    os.fsync(output.fileno())
                return True
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
