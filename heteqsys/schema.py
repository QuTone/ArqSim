"""Small, dependency-free helpers for canonical JSON contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any


def normalize_json(value: Any) -> Any:
    """Return a deterministic JSON-compatible representation."""

    # Exact JSON scalars dominate large execution ledgers.  Returning them
    # before the generic dataclass/ABC checks avoids millions of expensive
    # runtime protocol checks without changing subclass handling (notably
    # Enum and Path values still follow their dedicated branches below).
    value_type = type(value)
    if (
        value is None
        or value_type is str
        or value_type is int
        or value_type is float
        or value_type is bool
    ):
        return value
    if is_dataclass(value):
        return normalize_json(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): normalize_json(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [normalize_json(item) for item in value]
    if isinstance(value, set):
        return [normalize_json(item) for item in sorted(value, key=str)]
    return value


def _freeze_normalized_json(value: Any) -> Any:
    """Freeze a value already returned by :func:`normalize_json`."""

    if type(value) is dict:
        return MappingProxyType(
            {
                str(key): _freeze_normalized_json(child)
                for key, child in value.items()
            }
        )
    if type(value) is list:
        return tuple(_freeze_normalized_json(child) for child in value)
    return value


def deep_freeze_json(value: Any) -> Any:
    """Return a detached, recursively immutable JSON-shaped value."""

    normalized = normalize_json(value)
    try:
        json.dumps(
            normalized,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "deep_freeze_json requires finite JSON-shaped data"
        ) from exc
    return _freeze_normalized_json(normalized)


def canonical_json(value: Any) -> str:
    return json.dumps(
        normalize_json(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def strict_json(value: Any, *, label: str = "value") -> Any:
    """Return normalized finite JSON data or raise a useful contract error.

    Python's :mod:`json` encoder accepts ``NaN`` and infinities by default even
    though they are not valid JSON.  Public configuration and report contracts
    use this helper before they are hashed or sent to another process.
    """

    normalized = normalize_json(value)
    try:
        json.dumps(
            normalized,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain finite JSON data") from exc
    return normalized


def semantic_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("ascii")).hexdigest()


def hash_and_freeze_json_document(
    value: Any,
    *,
    hash_key: str,
    label: str = "document",
) -> Mapping[str, Any]:
    """Normalize, validate, self-hash, and freeze one JSON object.

    This is the single-pass equivalent of ``strict_json(value)``, adding a
    ``semantic_hash`` of that unsigned normalized object, and then calling
    ``deep_freeze_json``.  It is intended for large immutable public documents
    whose hash field is not part of its own digest.
    """

    if type(hash_key) is not str or not hash_key:
        raise ValueError("hash_key must be a non-empty string")
    normalized = normalize_json(value)
    if type(normalized) is not dict:
        raise ValueError(f"{label} must be a JSON object")
    if hash_key in normalized:
        raise ValueError(f"{label} already contains hash field {hash_key!r}")
    try:
        canonical = json.dumps(
            normalized,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain finite JSON data") from exc
    normalized[hash_key] = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    frozen = _freeze_normalized_json(normalized)
    assert isinstance(frozen, Mapping)
    return frozen
