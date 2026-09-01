"""Tests for selectable QEC protocol profiles."""

from __future__ import annotations

import pytest

from heteqsys.operation_profiles import (
    ArrivalDistribution,
    OperationLatencyProfile,
    ResolvedResourceProtocolBinding,
    ResolvedResourceProtocolBindings,
    apply_arrival_overrides,
    resolve_resource_protocol_bindings,
)
from heteqsys.program import FTCircuit, LogicalLayer, LogicalOperation
from heteqsys.qec import (
    entanglement_distillation_profiles,
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
    magic_state_factory_profiles,
)
from heteqsys.schema import semantic_hash
from heteqsys.specification import build_architecture_specification


def _one_t_circuit() -> FTCircuit:
    return FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "t", qubits=(0,)),),
            ),
        ),
        provenance={"benchmark": "resource-binding"},
    )


def test_magic_state_factory_catalog_exposes_three_reference_protocols() -> None:
    profiles = magic_state_factory_profiles()
    assert set(profiles) == {
        "cultivation-d5-d15-p1e3",
        "litinski-15to1-17-7-7-p1e3",
        "litinski-15to1x20to4-13-5-5-23-11-13-p1e3",
    }


def test_litinski_multi_output_profile_drives_layout_and_timing() -> None:
    profile = get_magic_state_factory_profile(
        "litinski-15to1x20to4-13-5-5-23-11-13-p1e3"
    )
    assert profile.outputs_per_batch == 4
    assert profile.physical_qubits_per_copy == 43_300
    assert profile.output_error_probability == pytest.approx(1.4e-10)
    assert profile.mean_batch_interval_s(1e-6) == pytest.approx(130e-6)
    assert profile.layout_policy_overrides() == {
        "protocols.magic_state.id": profile.id,
        "protocols.magic_state.outputs_per_copy_per_batch": 4,
        "protocols.magic_state.physical_qubits_per_copy": 43_300,
        "protocols.magic_state.qec_cycles_per_batch": 130.0,
    }


def test_cultivation_profile_keeps_attempts_separate_from_rounds() -> None:
    profile = get_magic_state_factory_profile("cultivation-d5-d15-p1e3")
    assert profile.cycles_per_attempt == 20
    assert profile.expected_attempts_per_batch == pytest.approx(109.38404649818159)
    assert profile.cycles_per_batch == pytest.approx(2187.680929963632)
    assert profile.output_error_probability == pytest.approx(
        1.9689128369672686e-9
    )
    assert "cutoff 108" in profile.assumptions["target_regime"]


def test_entanglement_catalog_exposes_two_constant_rate_points_and_boosting() -> None:
    profiles = entanglement_distillation_profiles()
    assert set(profiles) == {
        "constant-rate-buffer10-pbell1e2",
        "constant-rate-buffer30-pbell1e2",
        "boosting-dbell9-ds19-pbell1e2",
    }
    b10 = profiles["constant-rate-buffer10-pbell1e2"]
    b30 = profiles["constant-rate-buffer30-pbell1e2"]
    assert b10.raw_bell_pairs_per_output == pytest.approx(22.44)
    assert b30.raw_bell_pairs_per_output == pytest.approx(7.32)
    assert b10.logical_qubits_per_copy_per_endpoint == 10
    assert b30.logical_qubits_per_copy_per_endpoint == 29
    assert b10.physical_qubits_per_copy_total is None
    assert b30.physical_qubits_per_copy_total is None
    assert b10.outputs_per_batch == 4
    assert b10.qec_cycles_per_batch == pytest.approx(9)
    assert b30.outputs_per_batch == 18
    assert b30.qec_cycles_per_batch == pytest.approx(64)
    assert b30.qec_cycles_per_output == pytest.approx(64 / 18)
    copies = 10
    raw_link_rate = 10_000
    per_copy_interval = b30.mean_batch_interval_s(raw_link_rate / copies, 1e-3)
    assert per_copy_interval == pytest.approx((7.32 * 18) / 1_000)
    assert copies * b30.outputs_per_batch / per_copy_interval == pytest.approx(
        raw_link_rate / 7.32
    )


def test_boosting_profile_uses_uniform_two_bottleneck_rate_model() -> None:
    profile = get_entanglement_distillation_profile(
        "boosting-dbell9-ds19-pbell1e2"
    )
    assert profile.raw_bell_pairs_per_output == pytest.approx(85.86)
    assert profile.physical_qubits_per_copy_per_endpoint == 721
    assert profile.physical_qubits_per_copy_total == 1_442
    assert profile.outputs_per_batch == 1
    assert profile.qec_cycles_per_batch == pytest.approx(20.14)
    assert profile.qec_cycles_per_output == pytest.approx(20.14)
    assert profile.mean_batch_interval_s(10_000, 1e-3) == pytest.approx(
        20.14e-3
    )
    assert profile.mean_batch_interval_s(1_000, 1e-3) == pytest.approx(85.86e-3)


def test_distillation_rate_scale_preserves_non_timing_protocol_semantics() -> None:
    profile = get_entanglement_distillation_profile(
        "boosting-dbell9-ds19-pbell1e2"
    )
    accelerated = profile.with_distillation_rate_scale(8)
    assert accelerated.qec_cycles_per_batch == pytest.approx(20.14 / 8)
    assert accelerated.qec_cycles_per_output == pytest.approx(20.14 / 8)
    assert accelerated.ideal_logical_bell_pair_rate_per_s == pytest.approx(
        8 * profile.ideal_logical_bell_pair_rate_per_s
    )
    assert accelerated.saturation_physical_bell_pair_rate_per_s == pytest.approx(
        8 * profile.saturation_physical_bell_pair_rate_per_s
    )
    assert accelerated.outputs_per_batch == profile.outputs_per_batch
    assert accelerated.raw_bell_pairs_per_output == profile.raw_bell_pairs_per_output
    assert accelerated.output_fidelity == profile.output_fidelity
    assert accelerated.physical_qubits_per_copy_total == (
        profile.physical_qubits_per_copy_total
    )
    assert profile.qec_cycles_per_batch == pytest.approx(20.14)


def test_protocol_profiles_round_trip_with_one_canonical_content_hash() -> None:
    magic = get_magic_state_factory_profile("cultivation-d5-d15-p1e3")
    bell = get_entanglement_distillation_profile(
        "boosting-dbell9-ds19-pbell1e2"
    )

    assert type(magic).from_dict(magic.to_dict()) == magic
    assert type(bell).from_dict(bell.to_dict()) == bell
    assert magic.profile_hash == semantic_hash(magic.to_dict())
    assert bell.profile_hash == semantic_hash(bell.to_dict())


def test_resource_binding_round_trip_preserves_base_and_effective_arrivals() -> None:
    profile = get_magic_state_factory_profile("cultivation-d5-d15-p1e3")
    base = ArrivalDistribution(
        kind=profile.arrival_model,
        mean_interval_s=profile.cycles_per_batch * 1e-3,
    )
    binding = ResolvedResourceProtocolBinding(
        resource_kind="magic_state",
        requested_protocol_id="cultivation",
        protocol_id=profile.id,
        protocol_family="magic_state_factory",
        protocol_profile_hash=profile.profile_hash,
        copies=2,
        outputs_per_copy_per_batch=profile.outputs_per_batch,
        base_arrival_distribution=base,
        effective_arrival_distribution=base,
        arrival_source="protocol_profile",
        output_error_probability=profile.output_error_probability,
        output_fidelity=1 - profile.output_error_probability,
        physical_footprint={"physical_qubits_per_copy": 463},
        operating_point={"qec_cycle_time_s": 1e-3},
        source=profile.source,
        assumptions=profile.assumptions,
        provenance={"requested_protocol_alias": "cultivation"},
    )
    collection = ResolvedResourceProtocolBindings({"magic_state": binding})
    override = ArrivalDistribution.from_rate(10.0, kind="deterministic")
    effective = apply_arrival_overrides(
        collection,
        magic_state_arrival=override,
    )

    assert effective.magic_state is not None
    assert effective.magic_state.base_arrival_distribution == base
    assert effective.magic_state.effective_arrival_distribution == override
    assert effective.magic_state.arrival_source == "explicit_arrival_override"
    assert (
        ResolvedResourceProtocolBindings.from_dict(effective.to_dict()).bindings_hash
        == effective.bindings_hash
    )

    tampered = effective.to_dict()
    tampered["bindings"]["magic_state"]["copies"] = 3
    with pytest.raises(ValueError, match="hash"):
        ResolvedResourceProtocolBindings.from_dict(tampered)


def test_resource_binding_uses_the_canonicalized_protocol_reference() -> None:
    specification = build_architecture_specification(
        _one_t_circuit(),
        "1.1",
        policy_overrides={"protocols.magic_state.id": "cultivation"},
    )
    binding = resolve_resource_protocol_bindings(
        specification,
        OperationLatencyProfile(),
    ).magic_state

    assert binding is not None
    assert binding.requested_protocol_id == "cultivation-d5-d15-p1e3"
    assert binding.protocol_id == "cultivation-d5-d15-p1e3"
    assert "requested_protocol_alias" not in binding.provenance


def test_resource_binding_uses_factory_modality_and_shared_bell_lane_rate() -> None:
    neutral_atom = build_architecture_specification(_one_t_circuit(), "1.1")
    superconducting = build_architecture_specification(_one_t_circuit(), "1.2")
    remote = build_architecture_specification(
        _one_t_circuit(),
        "1.3",
        policy_overrides={
            "protocols.entanglement_distillation.copies": 3,
        },
    )

    magic_profile = get_magic_state_factory_profile(
        "cultivation-d5-d15-p1e3"
    )
    latency = OperationLatencyProfile()
    neutral_atom_bindings = resolve_resource_protocol_bindings(
        neutral_atom,
        latency,
    )
    superconducting_bindings = resolve_resource_protocol_bindings(
        superconducting,
        latency,
    )
    remote_bindings = resolve_resource_protocol_bindings(remote, latency)
    assert neutral_atom_bindings.magic_state is not None
    assert superconducting_bindings.magic_state is not None
    assert (
        neutral_atom_bindings.magic_state
        .base_arrival_distribution.mean_interval_s
        == pytest.approx(magic_profile.cycles_per_batch * 1e-3)
    )
    assert (
        superconducting_bindings.magic_state
        .base_arrival_distribution.mean_interval_s
        == pytest.approx(magic_profile.cycles_per_batch * 1e-6)
    )

    bell = remote_bindings.logical_bell_pair
    assert bell is not None
    assert bell.copies == 3
    assert bell.operating_point["per_lane_physical_bell_pair_rate_per_s"] == (
        pytest.approx(10_000 / 3)
    )
    assert bell.base_arrival_distribution.mean_interval_s == pytest.approx(
        85.86 / (10_000 / 3)
    )
