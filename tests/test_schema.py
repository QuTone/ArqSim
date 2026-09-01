from __future__ import annotations

import math

import pytest

from heteqsys.schema import (
    canonical_float_sum,
    hash_and_freeze_json_document,
    normalize_json,
    semantic_hash,
    strict_json,
)


def test_canonical_float_sum_preserves_frozen_ordered_contract_arithmetic() -> None:
    values = (0.005013, 0.005026, 0.005026)

    assert canonical_float_sum(values) == 0.015064999999999999


def test_hash_and_freeze_document_matches_composed_contract() -> None:
    source = {
        "z": (3, 2, 1),
        "nested": {"enabled": True, "value": 1.25},
    }
    expected = strict_json(source, label="test document")
    expected["document_hash"] = semantic_hash(expected)

    frozen = hash_and_freeze_json_document(
        source,
        hash_key="document_hash",
        label="test document",
    )

    assert normalize_json(frozen) == expected
    with pytest.raises(TypeError):
        frozen["nested"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        frozen["nested"]["enabled"] = False  # type: ignore[index]


def test_hash_and_freeze_document_rejects_invalid_or_prehashed_input() -> None:
    with pytest.raises(ValueError, match="finite JSON"):
        hash_and_freeze_json_document(
            {"value": math.nan},
            hash_key="document_hash",
        )
    with pytest.raises(ValueError, match="already contains"):
        hash_and_freeze_json_document(
            {"document_hash": "forged"},
            hash_key="document_hash",
        )
