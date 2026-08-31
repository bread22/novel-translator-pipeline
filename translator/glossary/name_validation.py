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


# This is intentionally a small, reviewed allowlist. Characters not listed here
# are preserved only when the generated target keeps the same character.
JA_TO_ZH_NAME_MAP: dict[str, str] = {
    "亞": "亚",
    "壓": "压",
    "奧": "奥",
    "會": "会",
    "學": "学",
    "宮": "宫",
    "廣": "广",
    "國": "国",
    "櫻": "樱",
    "慶": "庆",
    "澤": "泽",
    "濱": "滨",
    "灣": "湾",
    "爲": "为",
    "眞": "真",
    "經": "经",
    "緩": "缓",
    "緒": "绪",
    "縣": "县",
    "縱": "纵",
    "織": "织",
    "繪": "绘",
    "繼": "继",
    "續": "续",
    "總": "总",
    "綠": "绿",
    "羅": "罗",
    "臺": "台",
    "藝": "艺",
    "觀": "观",
    "邊": "边",
    "醫": "医",
    "鎌": "镰",
    "關": "关",
    "雜": "杂",
    "顯": "显",
    "髮": "发",
    "龍": "龙",
    # Common Japanese shinjitai forms.
    "竜": "龙",
    "沢": "泽",
    "辺": "边",
    "広": "广",
    "浜": "滨",
    "栄": "荣",
    "黒": "黑",
}

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


def check_person_name(source: str, target: str, category: object) -> NameCheckResult | None:
    """Check only the name portion of a person candidate.

    Deterministic character corrections are returned as ``corrected``. Cases
    that require an unlisted mapping remain ``ambiguous`` for the file queue.
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
    ) -> NameCheckResult:
        return NameCheckResult(
            status=status,
            expected_target=expected_target,
            reason=reason,
            mismatch_positions=mismatch_positions,
            **base,
        )

    if not name_source or not name_target:
        return result("ambiguous", reason="empty_name_after_honorific_split")
    # The first phase targets kanji names. If the source name contains no CJK kanji,
    # it is a transliterated/phonetic name (katakana/hiragana/latin) and does not use
    # 1:1 character alignment. Return None to let standard glossary validation proceed.
    if not _CJK_CHAR_RE.search(name_source):
        return None
    if not _is_cjk_name(name_source) or not _is_cjk_name(name_target):
        return result("ambiguous", reason="name_is_not_cjk_aligned")
    if len(name_source) != len(name_target):
        return result("ambiguous", reason="name_length_mismatch")

    expected_target = "".join(JA_TO_ZH_NAME_MAP.get(char, char) for char in name_source)
    mismatches = tuple(index for index, (actual, expected) in enumerate(zip(name_target, expected_target)) if actual != expected)
    if not mismatches:
        return result("pass", expected_target=expected_target)

    # A mismatch is auto-correctable only when the source character has an
    # explicit reviewed mapping. Unknown source characters go to the queue.
    unsafe = tuple(
        index
        for index in mismatches
        if name_source[index] not in JA_TO_ZH_NAME_MAP
    )
    if unsafe:
        return result(
            "ambiguous",
            expected_target=expected_target,
            reason="unmapped_character_mismatch",
            mismatch_positions=mismatches,
        )
    return result(
        "corrected",
        expected_target=expected_target,
        reason="deterministic_character_mapping",
        mismatch_positions=mismatches,
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
