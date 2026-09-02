"""Deterministic OpenCC-backed normalization for Japanese kanji names.

The OpenCC converter is deliberately kept behind this module.  Name checking
needs character candidates rather than phrase conversion: a phrase dictionary
can change an entity or its length, while a name mapping must remain
position-preserving and replayable.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
from itertools import product
from typing import Any, Iterable, Mapping
import unicodedata


NORMALIZATION_METHOD = "opencc"
JP2T_CONFIG = "jp2t"
T2S_CONFIG = "t2s"
MAX_NAME_CANDIDATES = 256
MAX_NAME_LENGTH = 80


@dataclass(frozen=True)
class NameNormalization:
    """The complete, stable result of one name normalization attempt."""

    source: str
    candidates: tuple[str, ...]
    preferred: str
    method: str
    ambiguous_positions: tuple[int, ...] = ()
    diagnostics: tuple[str, ...] = ()
    data_version: str = ""
    unmapped_positions: tuple[int, ...] = ()


@dataclass(frozen=True)
class _OpenCCBackend:
    jp2t: Any
    t2s: Any
    japanese_candidates: Mapping[str, tuple[str, ...]]
    simplified_candidates: Mapping[str, tuple[str, ...]]
    version: str
    data_version: str


_BACKEND: _OpenCCBackend | None = None
_BACKEND_ERROR: bool = False


def _package_version(module: Any, distribution: str) -> str:
    value = str(getattr(module, "__version__", "") or "").strip()
    if value:
        return value
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return ""


def _dictionary_values(resource: Any) -> dict[str, tuple[str, ...]]:
    """Read a one-character OpenCC dictionary without relying on internals."""
    values: dict[str, tuple[str, ...]] = {}
    try:
        text = resource.read_text(encoding="utf-8")
    except TypeError:
        # Some Traversable implementations expose read_text without a keyword.
        text = resource.read_text()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 2 or len(fields[0]) != 1:
            continue
        key = fields[0]
        values[key] = tuple(dict.fromkeys(value for value in fields[1:] if value))
    return values


def _merge_dictionaries(*dictionaries: Mapping[str, Iterable[str]]) -> dict[str, tuple[str, ...]]:
    merged: dict[str, list[str]] = {}
    for dictionary in dictionaries:
        for key, raw_values in dictionary.items():
            target = merged.setdefault(key, [])
            for value in raw_values:
                if value not in target:
                    target.append(value)
    return {key: tuple(values) for key, values in merged.items()}


def _build_backend() -> _OpenCCBackend:
    import opencc
    import opencc_data

    jp2t = opencc.OpenCC(JP2T_CONFIG)
    t2s = opencc.OpenCC(T2S_CONFIG)
    japanese_candidates = _dictionary_values(opencc_data.data_path("JPShinjitaiCharacters.txt"))
    simplified_candidates = _merge_dictionaries(
        _dictionary_values(opencc_data.data_path("TSCharactersExt.txt")),
        _dictionary_values(opencc_data.data_path("TSCharacters.txt")),
    )
    version = _package_version(opencc, "opencc-py")
    data_version = _package_version(opencc_data, "opencc-data")
    return _OpenCCBackend(
        jp2t=jp2t,
        t2s=t2s,
        japanese_candidates=japanese_candidates,
        simplified_candidates=simplified_candidates,
        version=version,
        data_version=data_version,
    )


def _get_backend() -> _OpenCCBackend | None:
    global _BACKEND, _BACKEND_ERROR
    if _BACKEND is not None:
        return _BACKEND
    if _BACKEND_ERROR:
        return None
    try:
        _BACKEND = _build_backend()
    except Exception:
        # A missing or damaged optional data package must not stop glossary
        # ingestion.  The caller records the stable diagnostic below.
        _BACKEND_ERROR = True
        return None
    return _BACKEND


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def _chain_convert(backend: _OpenCCBackend, value: str) -> str:
    # Convert one character at a time.  Calling either converter on a complete
    # name would enable phrase dictionaries and violate name alignment.
    return backend.t2s.convert(backend.jp2t.convert(value))


def _character_candidates(backend: _OpenCCBackend, value: str) -> tuple[tuple[str, ...], str, bool]:
    source_values = _unique((value, *backend.japanese_candidates.get(value, ())))
    chain_preferred = _chain_convert(backend, value)
    direct_simplified = backend.t2s.convert(value)
    converted_values: list[str] = []
    for source_value in source_values:
        converted_values.append(backend.t2s.convert(source_value))
        for simplified in backend.simplified_candidates.get(source_value, ()):
            converted_values.append(_chain_convert(backend, simplified))

    # The first candidate is the two-stage OpenCC result.  A source character
    # such as 戸 has an identity first entry in jp2t (戸 戶); in that case the
    # first non-identity data candidate is the deterministic Chinese choice.
    preferred = chain_preferred
    # A few Japanese old-form outputs (for example 緖) are not repeated in
    # the standard t2s character table.  The direct simplified candidate is
    # still an OpenCC data-backed result and is a better target than that
    # intermediate old form.
    if direct_simplified != value and direct_simplified != chain_preferred:
        preferred = direct_simplified
    if preferred == value:
        preferred = next((item for item in converted_values if item != value), preferred)
    candidates = _unique((preferred, value, *converted_values))
    supported = bool(
        backend.japanese_candidates.get(value)
        or backend.simplified_candidates.get(value)
        or any(backend.simplified_candidates.get(item) for item in source_values)
        or chain_preferred != value
    )
    return candidates or (value,), preferred or value, supported


def _version_value(backend: _OpenCCBackend) -> str:
    if backend.version and backend.version == backend.data_version:
        return backend.version
    if backend.version or backend.data_version:
        return f"{backend.version}/{backend.data_version}"
    return ""


def normalize_japanese_name(value: str) -> NameNormalization:
    """Generate deterministic simplified candidates for a kanji name.

    The result is useful even when the backend is unavailable: its identity
    candidate lets the validation layer preserve the model's target while
    recording why automatic normalization was skipped.
    """
    source = unicodedata.normalize("NFKC", str(value)).strip()
    backend = _get_backend()
    if backend is None:
        return NameNormalization(
            source=source,
            candidates=(source,) if source else (),
            preferred=source,
            method=NORMALIZATION_METHOD,
            diagnostics=("opencc_backend_error",),
            unmapped_positions=tuple(range(len(source))),
        )

    try:
        position_options: list[tuple[str, ...]] = []
        preferred_parts: list[str] = []
        unmapped: list[int] = []
        for index, character in enumerate(source):
            options, preferred, supported = _character_candidates(backend, character)
            position_options.append(options)
            preferred_parts.append(preferred)
            if not supported:
                unmapped.append(index)
        preferred_value = "".join(preferred_parts)
    except Exception:
        return NameNormalization(
            source=source,
            candidates=(source,) if source else (),
            preferred=source,
            method=NORMALIZATION_METHOD,
            diagnostics=("opencc_backend_error",),
            data_version=_version_value(backend),
            unmapped_positions=tuple(range(len(source))),
        )

    diagnostics: list[str] = []
    ambiguous_positions = tuple(index for index, options in enumerate(position_options) if len(options) > 1)
    if ambiguous_positions:
        diagnostics.append("opencc_ambiguous")
    if unmapped:
        diagnostics.append("opencc_unmapped")

    candidate_count = 1
    for options in position_options:
        candidate_count *= max(1, len(options))
    candidates: tuple[str, ...]
    if len(source) > MAX_NAME_LENGTH or candidate_count > MAX_NAME_CANDIDATES:
        diagnostics.extend(("candidate_overflow", f"candidate_count:{candidate_count}"))
        if len(source) > MAX_NAME_LENGTH:
            diagnostics.append(f"candidate_length:{len(source)}")
        candidates = (preferred_value,)
    else:
        candidates = _unique("".join(parts) for parts in product(*position_options)) if source else ()
        if preferred_value and preferred_value in candidates:
            candidates = (preferred_value, *tuple(item for item in candidates if item != preferred_value))

    if backend.version and backend.data_version and backend.version != backend.data_version:
        diagnostics.append("opencc_version_mismatch")
    return NameNormalization(
        source=source,
        candidates=candidates,
        preferred=preferred_value,
        method=NORMALIZATION_METHOD,
        ambiguous_positions=ambiguous_positions,
        diagnostics=tuple(dict.fromkeys(diagnostics)),
        data_version=_version_value(backend),
        unmapped_positions=tuple(unmapped),
    )


def normalization_metadata() -> dict[str, Any]:
    """Return backend/data information for smoke tests and replay reports."""
    backend = _get_backend()
    if backend is None:
        return {
            "method": NORMALIZATION_METHOD,
            "configs": [JP2T_CONFIG, T2S_CONFIG],
            "version": "",
            "data_version": "",
            "status": "error",
            "diagnostics": ["opencc_backend_error"],
        }
    diagnostics = ["opencc_version_mismatch"] if backend.version and backend.data_version and backend.version != backend.data_version else []
    return {
        "method": NORMALIZATION_METHOD,
        "configs": [JP2T_CONFIG, T2S_CONFIG],
        "version": backend.version,
        "data_version": backend.data_version,
        "status": "ok",
        "diagnostics": diagnostics,
    }


def reset_backend_for_tests() -> None:
    """Clear the lazy singleton for isolated backend failure tests."""
    global _BACKEND, _BACKEND_ERROR
    _BACKEND = None
    _BACKEND_ERROR = False


__all__ = [
    "MAX_NAME_CANDIDATES",
    "MAX_NAME_LENGTH",
    "NameNormalization",
    "NORMALIZATION_METHOD",
    "normalization_metadata",
    "normalize_japanese_name",
    "reset_backend_for_tests",
]
