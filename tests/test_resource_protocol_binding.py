"""Acceptance tests for one authoritative resource-protocol binding."""

from __future__ import annotations

import pytest

from arqsim.api import (
    CANONICAL_FIDELITY_PRESET,
    EvaluationConfig,
    run_evaluation,
    validate_evaluation_report_document,
)
from arqsim.operation_profiles import (
    ArrivalDistribution,
    OperationLatencyProfile,
    ResolvedResourceProtocolBindings,
    effective_resource_protocol_bindings,
    resolve_resource_protocol_bindings,
    with_effective_arrivals,
)
from arqsim.program import FTCircuit, LogicalLayer, LogicalOperation
from arqsim.qec import (
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
)
from arqsim.schema import normalize_json, semantic_hash
from arqsim.specification import build_architecture_specification


def _t_circuit(num_qubits: int = 1) -> FTCircuit:
    return FTCircuit(
        representation="clifford_t",
        num_qubits=num_qubits,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "t", qubits=(0,)),),
            ),
        ),
    )


def test_default_11_magic_binding_uses_catalog_timing_quality_and_space() -> None:
    specification = build_architecture_specification(_t_circuit(), "1.1")
    bindings = resolve_resource_protocol_bindings(
        specification,
        OperationLatencyProfile(),
    )
    magic = bindings.magic_state
    catalog = get_magic_state_factory_profile("cultivation-d5-d15-p1e3")

    assert magic is not None
    assert bindings.logical_bell_pair is None
    assert magic.requested_protocol_id == catalog.id
    assert magic.protocol_id == catalog.id
    assert magic.protocol_profile_hash == catalog.profile_hash
    assert magic.copies == 1
    assert magic.outputs_per_copy_per_batch == catalog.outputs_per_batch
    assert magic.base_arrival_distribution.kind == catalog.arrival_model
    assert magic.base_arrival_distribution.mean_interval_s == pytest.approx(
        catalog.cycles_per_batch * 1e-3
    )
    assert magic.effective_arrival_distribution == magic.base_arrival_distribution
    assert magic.arrival_source == "protocol_profile"
    assert magic.output_error_probability == pytest.approx(
        catalog.output_error_probability
    )
    assert magic.output_fidelity == pytest.approx(
        1.0 - catalog.output_error_probability
    )
    assert magic.physical_footprint["physical_qubits_per_copy"] == (
        catalog.physical_qubits_per_copy
    )

    restored = ResolvedResourceProtocolBindings.from_dict(bindings.to_dict())
    assert restored.bindings_hash == bindings.bindings_hash
    assert restored.to_dict() == bindings.to_dict()


def test_multinode_bell_binding_shares_physical_rate_across_copies() -> None:
    copies = 3
    specification = build_architecture_specification(
        _t_circuit(3),
        "1.3",
        policy_overrides={
            "protocols.entanglement_distillation.copies": copies,
        },
    )
    bell = resolve_resource_protocol_bindings(
        specification,
        OperationLatencyProfile(),
    ).logical_bell_pair
    catalog = get_entanglement_distillation_profile(
        "boosting-dbell9-ds19-pbell1e2"
    )

    assert bell is not None
    shared_rate = catalog.reference_physical_bell_pair_rate_per_s
    local_interval = catalog.qec_cycles_per_batch * catalog.qec_cycle_time_s
    supply_interval = (
        catalog.raw_bell_pairs_per_output
        * catalog.outputs_per_batch
        / (shared_rate / copies)
    )
    expected_interval = max(local_interval, supply_interval)
    expected_cold_start_extra = min(local_interval, supply_interval)

    assert bell.protocol_id == catalog.id
    assert bell.protocol_profile_hash == catalog.profile_hash
    assert bell.copies == copies
    assert bell.base_arrival_distribution.mean_interval_s == pytest.approx(
        expected_interval
    )
    assert bell.base_arrival_distribution.initial_delay_s == pytest.approx(
        expected_cold_start_extra
    )
    assert bell.base_arrival_distribution.initial_delay_samples == copies
    assert bell.operating_point["shared_physical_bell_pair_rate_per_s"] == (
        shared_rate
    )
    assert bell.operating_point["per_lane_physical_bell_pair_rate_per_s"] == (
        pytest.approx(shared_rate / copies)
    )
    assert bell.operating_point["aggregate_mean_output_rate_per_s"] == pytest.approx(
        shared_rate / catalog.raw_bell_pairs_per_output
    )
    assert bell.operating_point["cold_start_extra_per_copy_s"] == pytest.approx(
        expected_cold_start_extra
    )
    assert bell.operating_point["cold_start_delayed_samples"] == copies
    assert bell.operating_point["expected_first_batch_latency_s"] == pytest.approx(
        expected_interval + expected_cold_start_extra
    )
    assert bell.output_error_probability == pytest.approx(
        catalog.output_error_probability
    )
    assert bell.output_fidelity == pytest.approx(catalog.output_fidelity)


def test_explicit_arrival_override_preserves_base_protocol_and_quality() -> None:
    specification = build_architecture_specification(_t_circuit(), "1.3")
    base = resolve_resource_protocol_bindings(
        specification,
        OperationLatencyProfile(),
    )
    override = ArrivalDistribution(kind="deterministic", mean_interval_s=1e-3)
    requested = OperationLatencyProfile(magic_state_arrival=override)
    effective = effective_resource_protocol_bindings(requested, base)

    base_magic = base.magic_state
    effective_magic = effective.magic_state
    base_bell = base.logical_bell_pair
    effective_bell = effective.logical_bell_pair
    assert base_magic is not None and effective_magic is not None
    assert base_bell is not None and effective_bell is not None

    assert effective_magic.base_arrival_distribution == (
        base_magic.base_arrival_distribution
    )
    assert effective_magic.effective_arrival_distribution == override
    assert effective_magic.arrival_source == "explicit_arrival_override"
    assert effective_magic.protocol_id == base_magic.protocol_id
    assert effective_magic.output_error_probability == (
        base_magic.output_error_probability
    )
    assert effective_magic.provenance["arrival_resolution"]["source"] == (
        "explicit_arrival_override"
    )

    assert effective_bell.effective_arrival_distribution == (
        base_bell.base_arrival_distribution
    )
    assert effective_bell.arrival_source == "protocol_profile"
    assert effective.bindings_hash != base.bindings_hash

    runtime_profile = with_effective_arrivals(requested, base)
    assert runtime_profile.magic_state_arrival == override
    assert runtime_profile.bell_pair_arrival == base_bell.base_arrival_distribution
    assert runtime_profile.provenance["resource_protocol_bindings_hash"] == (
        effective.bindings_hash
    )


def test_arrival_kind_override_changes_sampling_law_not_protocol_interval() -> None:
    specification = build_architecture_specification(_t_circuit(), "1.3")
    base = resolve_resource_protocol_bindings(
        specification,
        OperationLatencyProfile(),
    )
    effective = effective_resource_protocol_bindings(
        OperationLatencyProfile(derived_arrival_kind="deterministic"),
        base,
    )

    for kind, base_binding in base.bindings.items():
        resolved = effective[kind]
        assert resolved.arrival_source == "derived_kind_override"
        assert resolved.effective_arrival_distribution.kind == "deterministic"
        assert resolved.effective_arrival_distribution.mean_interval_s == pytest.approx(
            base_binding.base_arrival_distribution.mean_interval_s
        )
        assert resolved.output_error_probability == (
            base_binding.output_error_probability
        )

    base_bell = base.logical_bell_pair
    effective_bell = effective.logical_bell_pair
    assert base_bell is not None and effective_bell is not None
    assert effective_bell.effective_arrival_distribution.initial_delay_s == (
        base_bell.base_arrival_distribution.initial_delay_s
    )
    assert effective_bell.effective_arrival_distribution.initial_delay_samples == (
        base_bell.base_arrival_distribution.initial_delay_samples
    )


def test_legacy_magic_alias_is_canonicalized_before_resource_binding() -> None:
    specification = build_architecture_specification(
        _t_circuit(),
        "1.1",
        policy_overrides={"protocols.magic_state.id": "cultivation"},
    )
    magic = resolve_resource_protocol_bindings(
        specification,
        OperationLatencyProfile(),
    ).magic_state

    assert magic is not None
    assert magic.requested_protocol_id == "cultivation-d5-d15-p1e3"
    assert magic.protocol_id == "cultivation-d5-d15-p1e3"
    assert "requested_protocol_alias" not in magic.provenance


def test_binding_round_trip_rejects_tampered_catalog_quality() -> None:
    specification = build_architecture_specification(_t_circuit(), "1.1")
    bindings = resolve_resource_protocol_bindings(
        specification,
        OperationLatencyProfile(),
    )
    payload = bindings.to_dict()
    payload["bindings"]["magic_state"]["output_error_probability"] *= 10
    payload["bindings"]["magic_state"]["output_fidelity"] = (
        1.0
        - payload["bindings"]["magic_state"]["output_error_probability"]
    )

    with pytest.raises(ValueError, match="hash does not match"):
        ResolvedResourceProtocolBindings.from_dict(payload)


def test_run_report_binds_catalog_timing_and_quality_once_end_to_end() -> None:
    report = run_evaluation(
        _t_circuit(),
        EvaluationConfig(
            profile_id="1.3",
            fidelity_profile=CANONICAL_FIDELITY_PRESET,
        ),
    )
    bindings = report.resource_protocol_bindings
    magic = bindings.magic_state
    bell = bindings.logical_bell_pair
    assert magic is not None and bell is not None

    latency = report.effective_configuration.latency_profile
    assert latency.magic_state_arrival == magic.effective_arrival_distribution
    assert latency.bell_pair_arrival == bell.effective_arrival_distribution
    assert latency.provenance["resource_protocol_bindings_hash"] == (
        bindings.bindings_hash
    )

    magic_process = next(
        process
        for process in report.execution_plan.resource_dag.processes
        if process.opcode.value == "PREPARE_MAGIC_STATE"
    )
    bell_process = next(
        process
        for process in report.execution_plan.resource_dag.processes
        if process.opcode.value == "PREPARE_LOGICAL_BELL"
    )
    assert magic_process.protocol == magic.protocol_id
    assert magic_process.arrival_distribution == (
        magic.effective_arrival_distribution
    )
    assert bell_process.protocol == bell.protocol_id
    assert bell_process.arrival_distribution == bell.effective_arrival_distribution

    plan_provenance = report.execution_plan.provenance
    assert plan_provenance["resource_protocol_bindings_hash"] == (
        bindings.bindings_hash
    )
    assert normalize_json(plan_provenance["resource_protocol_bindings"]) == (
        bindings.to_dict()
    )

    assert report.fidelity_profile is not None
    magic_quality = report.fidelity_profile.provenance["magic_state_operations"]
    bell_quality = report.fidelity_profile.provenance["logical_bell_operations"]
    assert magic_quality["factory_profile"] == magic.protocol_id
    assert magic_quality["protocol_profile_hash"] == magic.protocol_profile_hash
    assert magic_quality["factory_output_failure_probability"] == pytest.approx(
        magic.output_error_probability
    )
    assert bell_quality["factory_profile"] == bell.protocol_id
    assert bell_quality["protocol_profile_hash"] == bell.protocol_profile_hash
    assert bell_quality["factory_output_failure_probability"] == pytest.approx(
        bell.output_error_probability
    )

    validated = validate_evaluation_report_document(report.to_dict())
    assert normalize_json(validated) == report.to_dict()


def test_run_report_explicit_arrival_override_does_not_override_quality() -> None:
    explicit_magic = ArrivalDistribution(
        kind="deterministic",
        mean_interval_s=1e-3,
    )
    report = run_evaluation(
        _t_circuit(),
        EvaluationConfig(
            profile_id="1.3",
            latency_profile=OperationLatencyProfile(
                magic_state_arrival=explicit_magic,
                provenance={
                    "magic_state_factory": {
                        "output_error_probability": 0.25,
                    }
                },
            ),
            fidelity_profile=CANONICAL_FIDELITY_PRESET,
        ),
    )
    magic = report.resource_protocol_bindings.magic_state
    bell = report.resource_protocol_bindings.logical_bell_pair
    assert magic is not None and bell is not None

    catalog = get_magic_state_factory_profile(magic.protocol_id)
    assert magic.arrival_source == "explicit_arrival_override"
    assert magic.effective_arrival_distribution == explicit_magic
    assert magic.base_arrival_distribution != explicit_magic
    assert bell.arrival_source == "protocol_profile"
    assert bell.effective_arrival_distribution == bell.base_arrival_distribution

    magic_process = next(
        process
        for process in report.execution_plan.resource_dag.processes
        if process.opcode.value == "PREPARE_MAGIC_STATE"
    )
    assert magic_process.arrival_distribution == explicit_magic
    assert report.fidelity_profile is not None
    fidelity_receipt = report.fidelity_profile.provenance["magic_state_operations"]
    assert fidelity_receipt["factory_output_failure_probability"] == pytest.approx(
        catalog.output_error_probability
    )
    assert fidelity_receipt["factory_output_failure_probability"] != 0.25
    validate_evaluation_report_document(report.to_dict())


def test_report_validator_rejects_resigned_resource_quality_tampering() -> None:
    report = run_evaluation(
        _t_circuit(),
        EvaluationConfig(
            profile_id="1.3",
            fidelity_profile=CANONICAL_FIDELITY_PRESET,
        ),
    )
    payload = report.to_dict()
    plan = payload["artifacts"]["execution_plan"]
    bindings = plan["provenance"]["resource_protocol_bindings"]
    magic = bindings["bindings"]["magic_state"]

    magic["output_error_probability"] *= 10
    magic["output_fidelity"] = 1.0 - magic["output_error_probability"]
    magic_unsigned = {
        key: value for key, value in magic.items() if key != "binding_hash"
    }
    magic["binding_hash"] = semantic_hash(magic_unsigned)
    bindings_unsigned = {
        key: value for key, value in bindings.items() if key != "bindings_hash"
    }
    bindings["bindings_hash"] = semantic_hash(bindings_unsigned)
    plan["provenance"]["resource_protocol_bindings_hash"] = bindings[
        "bindings_hash"
    ]
    plan["plan_hash"] = semantic_hash(
        {key: value for key, value in plan.items() if key != "plan_hash"}
    )
    trace = payload["artifacts"]["execution_trace"]
    trace["plan_hash"] = plan["plan_hash"]
    trace["trace_hash"] = semantic_hash(
        {key: value for key, value in trace.items() if key != "trace_hash"}
    )
    report_unsigned = {
        key: value for key, value in payload.items() if key != "report_hash"
    }
    payload["report_hash"] = semantic_hash(report_unsigned)

    with pytest.raises(
        ValueError,
        match="resource-protocol bindings do not match the request",
    ):
        validate_evaluation_report_document(payload)
