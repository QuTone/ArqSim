"""Focused tests for the human-facing one-shot evaluation configuration."""

from __future__ import annotations

import json

import pytest

from arqsim.api import (
    CANONICAL_FIDELITY_PRESET,
    DEFAULT_FOOTPRINT_PRESET,
    EvaluationConfig,
    EvaluationRunError,
    REFERENCE_END_TO_END_FIDELITY_P1E3_PRESET,
    REFERENCE_PHYSICAL_QUBIT_FOOTPRINT_PRESET,
    run_evaluation,
)
from arqsim.evaluation import EvaluationPolicy, ExecutionPolicy
from arqsim.operation_profiles import ArrivalDistribution, OperationLatencyProfile
from arqsim.program import FTCircuit, LogicalLayer, LogicalOperation


def _empty_circuit() -> FTCircuit:
    return FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(),
    )


def _rz_circuit() -> FTCircuit:
    return FTCircuit(
        representation="gate",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (
                    LogicalOperation(
                        "gate", "rz", qubits=(0,), parameters=(0.125,)
                    ),
                ),
            ),
        ),
    )


def test_config_uses_human_names_without_changing_v1_wire_names() -> None:
    config = EvaluationConfig(
        profile_id="2.3",
        run_label="demo",
        architecture_overrides={"protocols.magic_state.buffer_capacity": 2},
    )

    assert config.run_label == "demo"
    assert config.architecture_overrides == {
        "protocols.magic_state.buffer_capacity": 2
    }
    # Read-only compatibility aliases keep report-v2 validation code and
    # callers inspecting older request documents working.
    assert config.workflow_id == config.run_label
    assert config.layout_policy_overrides == config.architecture_overrides
    assert config.to_dict()["workflow_id"] == "demo"
    assert config.to_dict()["layout_policy_overrides"] == {
        "protocols.magic_state.buffer_capacity": 2
    }


def test_config_is_keyword_only_and_reserves_runtime_receipt() -> None:
    with pytest.raises(TypeError):
        EvaluationConfig("2.3")  # type: ignore[misc]
    with pytest.raises(TypeError):
        EvaluationConfig(runtime_components=None)  # type: ignore[call-arg]


def test_reference_models_are_explicit_defaults() -> None:
    config = EvaluationConfig()

    assert config.fidelity_profile == CANONICAL_FIDELITY_PRESET
    assert config.footprint_model == DEFAULT_FOOTPRINT_PRESET
    assert (
        REFERENCE_END_TO_END_FIDELITY_P1E3_PRESET
        == CANONICAL_FIDELITY_PRESET
    )
    assert (
        REFERENCE_PHYSICAL_QUBIT_FOOTPRINT_PRESET
        == DEFAULT_FOOTPRINT_PRESET
    )
    assert config.to_dict()["fidelity_profile"] == {
        "preset": CANONICAL_FIDELITY_PRESET
    }
    assert config.to_dict()["footprint_model"] == {
        "preset": DEFAULT_FOOTPRINT_PRESET
    }
    assert EvaluationConfig(fidelity_profile=None).fidelity_profile is None
    with pytest.raises(TypeError, match="named preset"):
        EvaluationConfig(footprint_model=True)  # type: ignore[arg-type]


def test_policy_default_is_full_everywhere() -> None:
    config = EvaluationConfig()
    assert ExecutionPolicy().observation_level == "full"
    assert ExecutionPolicy.from_dict({}).observation_level == "full"
    assert config.execution_policy.observation_level == "full"
    assert EvaluationPolicy is ExecutionPolicy
    assert config.evaluation_policy is config.execution_policy


def test_config_exposes_execution_policy_but_preserves_v1_wire_key() -> None:
    policy = ExecutionPolicy(
        magic_state_batching="incremental",
        observation_level="summary",
        run_seed=11,
        max_transitions=123,
    )
    config = EvaluationConfig(execution_policy=policy)

    assert config.execution_policy is policy
    assert config.to_dict()["evaluation_policy"] == policy.to_dict()
    assert EvaluationConfig.from_dict(config.to_dict()).execution_policy == policy
    with pytest.raises(TypeError):
        EvaluationConfig(evaluation_policy=policy)  # type: ignore[call-arg]


def test_config_json_rejects_duplicate_keys_and_nonfinite_constants() -> None:
    payload = EvaluationConfig().to_dict()
    encoded = json.dumps(payload)
    duplicate = encoded[:-1] + ', "profile_id": "2.3"}'

    with pytest.raises(ValueError, match="Duplicate JSON object key"):
        EvaluationConfig.from_json(duplicate)
    with pytest.raises(ValueError, match="Non-finite JSON constant"):
        EvaluationConfig.from_json(
            '{"schema_version":"arqsim.evaluation-config.v1","x":NaN}'
        )


def test_config_v1_rejects_the_pre_refreeze_runtime_manifest_v3() -> None:
    payload = EvaluationConfig().to_dict()
    payload["runtime_components"]["schema_version"] = (
        "arqsim.runtime-manifest.v3"
    )

    with pytest.raises(ValueError, match="Unsupported runtime-manifest"):
        EvaluationConfig.from_dict(payload)


def test_run_evaluation_wraps_pipeline_failures_with_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_architecture(*_args: object, **_kwargs: object) -> None:
        raise ValueError("synthetic architecture failure")

    monkeypatch.setattr(
        "arqsim.api.build_architecture_specification", fail_architecture
    )

    with pytest.raises(EvaluationRunError) as caught:
        run_evaluation(_empty_circuit())

    assert caught.value.stage == "architecture_resolution"
    assert caught.value.code == "architecture_resolution_failed"
    assert caught.value.details["cause_type"] == "ValueError"
    assert isinstance(caught.value.__cause__, ValueError)


def test_run_evaluation_wraps_artifact_assembly_as_report_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_layout(*_args: object, **_kwargs: object) -> None:
        raise ValueError("synthetic report assembly failure")

    monkeypatch.setattr("arqsim.api.materialize_compute_layout", fail_layout)

    with pytest.raises(EvaluationRunError) as caught:
        run_evaluation(_empty_circuit())

    assert caught.value.stage == "report_rendering"
    assert caught.value.code == "report_rendering_failed"
    assert caught.value.details["cause_type"] == "ValueError"
    assert isinstance(caught.value.__cause__, ValueError)


def test_one_shot_evaluation_fails_closed_on_incomplete_fidelity() -> None:
    with pytest.raises(EvaluationRunError) as caught:
        run_evaluation(_rz_circuit())

    assert caught.value.stage == "result_analysis"
    assert caught.value.code == "fidelity_coverage_incomplete"
    assert caught.value.details["coverage_gaps"][
        "unprofiled_logical_operation_counts"
    ] == {"rz": 1}


def test_fidelity_can_be_explicitly_disabled_for_unmodeled_operations() -> None:
    report = run_evaluation(
        _rz_circuit(),
        EvaluationConfig(fidelity_profile=None),
    )

    assert report.fidelity is None
    assert report.summary.success_probability is None
    assert report.summary.fidelity_complete_coverage is None


def test_one_shot_evaluation_fails_closed_on_missing_required_latency() -> None:
    incomplete = OperationLatencyProfile(
        gate_duration_s={"superconducting": 1e-6},
        magic_state_arrival=ArrivalDistribution.from_rate(1_000.0),
        bell_pair_arrival=ArrivalDistribution.from_rate(1_000.0),
    )

    with pytest.raises(EvaluationRunError) as caught:
        run_evaluation(
            _rz_circuit(),
            EvaluationConfig(
                latency_profile=incomplete,
                fidelity_profile=None,
            ),
        )

    assert caught.value.stage == "resource_protocol_resolution"
    assert "No QEC cycle time is available for modality neutral_atom" in str(
        caught.value
    )
