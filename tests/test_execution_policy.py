from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, fields

import pytest

from arqsim.evaluation import (
    EvaluationPolicy,
    ExecutionPolicy,
    RuntimeInjectionMode,
)


def test_evaluation_policy_is_the_execution_policy_alias() -> None:
    assert EvaluationPolicy is ExecutionPolicy
    assert [field.name for field in fields(ExecutionPolicy)] == [
        "magic_state_batching",
        "injection_lowering_mode",
        "observation_level",
        "run_seed",
        "max_transitions",
    ]


def test_python_surface_uses_execution_names_with_read_only_legacy_views() -> None:
    policy = ExecutionPolicy(
        magic_state_batching="incremental",
        injection_lowering_mode=RuntimeInjectionMode.FINITE_STATE_INJECTION_V1,
        observation_level="summary",
        run_seed=7,
        max_transitions=123,
    )

    assert policy.magic_state_consumption == "incremental"
    assert (
        policy.runtime_injection_mode
        is RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
    )
    assert policy.trace_level == "summary"
    assert policy.seed == 7
    assert policy.max_events == 123
    assert policy.store_load_policy == "dependency_aware_overlap"
    assert policy.resource_fill_policy == "greedy_fill_to_capacity"
    assert policy.selected_layers == ()

    with pytest.raises(FrozenInstanceError):
        policy.trace_level = "full"  # type: ignore[misc]


def test_fixed_wire_semantics_are_not_declared_constructor_parameters() -> None:
    parameters = inspect.signature(ExecutionPolicy).parameters

    assert "store_load_policy" not in parameters
    assert "resource_fill_policy" not in parameters
    assert "selected_layers" not in parameters


def test_wire_codec_retains_the_frozen_eight_field_shape() -> None:
    policy = ExecutionPolicy(
        magic_state_batching="incremental",
        injection_lowering_mode="finite_state_injection_v1",
        observation_level="summary",
        run_seed=17,
        max_transitions=42,
    )
    document = {
        "magic_state_consumption": "incremental",
        "store_load_policy": "dependency_aware_overlap",
        "resource_fill_policy": "greedy_fill_to_capacity",
        "trace_level": "summary",
        "runtime_injection_mode": "finite_state_injection_v1",
        "selected_layers": [],
        "seed": 17,
        "max_events": 42,
    }

    assert policy.to_dict() == document
    assert ExecutionPolicy.from_dict(document) == policy


def test_wire_codec_rejects_noncanonical_fixed_semantics() -> None:
    with pytest.raises(ValueError, match="dependency_aware_overlap"):
        ExecutionPolicy.from_dict({"store_load_policy": "serial"})
    with pytest.raises(ValueError, match="greedy_fill_to_capacity"):
        ExecutionPolicy.from_dict({"resource_fill_policy": "fair"})
    with pytest.raises(ValueError, match="selected_layers"):
        ExecutionPolicy.from_dict({"selected_layers": [0]})


def test_active_legacy_constructor_names_remain_migration_compatible() -> None:
    policy = EvaluationPolicy(
        magic_state_consumption="incremental",
        runtime_injection_mode="finite_state_injection_v1",
        trace_level="summary",
        seed=3,
        max_events=99,
        store_load_policy="dependency_aware_overlap",
        resource_fill_policy="greedy_fill_to_capacity",
        selected_layers=(),
    )

    assert policy == ExecutionPolicy(
        magic_state_batching="incremental",
        injection_lowering_mode="finite_state_injection_v1",
        observation_level="summary",
        run_seed=3,
        max_transitions=99,
    )
