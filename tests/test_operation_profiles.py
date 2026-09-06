"""Regression tests for operation-profile calibration formulas."""

from __future__ import annotations

import math
import random

import pytest

from arqsim.api import EvaluationConfig, run_evaluation
from arqsim.architecture.isa import ArchitectureOpcode
from arqsim.operation_profiles import (
    ArrivalDistribution,
    NeutralAtomMovementProfile,
    OperationLatencyProfile,
    canonical_fidelity_profile,
    resolve_resource_protocol_bindings,
)
from arqsim.operation_profiles.canonical_fidelity import (
    bb_288_12_18_failure_per_logical_qubit_cycle,
    rotated_surface_memory_failure_for_rounds,
    rotated_surface_memory_failure_per_cycle,
    unrotated_surface_clifford_failure,
    unrotated_surface_ppm_failure,
)
from arqsim.operation_profiles.fidelity_model_config import load_fidelity_fit
from arqsim.program import FTCircuit, LogicalLayer, LogicalOperation
from arqsim.qec import (
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
)
from arqsim.specification import build_architecture_specification


def test_neutral_atom_movement_profile_is_hash_stable_and_round_trips() -> None:
    profile = NeutralAtomMovementProfile(
        x_spacing_um=24.0,
        y_spacing_um=18.0,
        aod_count=3,
        transfer_duration_us=9.0,
        reference_distance_um=120.0,
        reference_move_duration_us=240.0,
        provenance={"source": "calibration-a"},
    )

    restored = NeutralAtomMovementProfile.from_dict(profile.to_dict())

    assert restored == profile
    assert restored.profile_hash == profile.profile_hash


def test_operation_latency_profile_commits_to_movement_calibration() -> None:
    baseline = OperationLatencyProfile()
    calibrated = OperationLatencyProfile(
        neutral_atom_movement=NeutralAtomMovementProfile(x_spacing_um=24.0)
    )

    assert calibrated.profile_hash != baseline.profile_hash
    assert OperationLatencyProfile.from_dict(calibrated.to_dict()) == calibrated


def test_required_modality_timings_distinguish_missing_from_explicit_zero() -> None:
    missing = OperationLatencyProfile(
        gate_duration_s={"superconducting": 1e-6},
    )
    with pytest.raises(ValueError, match="No gate duration.*neutral_atom"):
        missing.gate_duration("neutral_atom")
    with pytest.raises(ValueError, match="No reaction latency.*neutral_atom"):
        missing.require_reaction_latency("neutral_atom")

    explicit_zero = OperationLatencyProfile(
        gate_duration_s={"neutral_atom": 0.0},
        reaction_latency_by_modality_s={"neutral_atom": 0.0},
    )
    assert explicit_zero.gate_duration("neutral_atom") == 0.0
    assert explicit_zero.require_reaction_latency("neutral_atom") == 0.0
    assert OperationLatencyProfile.from_dict(
        explicit_zero.to_dict()
    ) == explicit_zero


@pytest.mark.parametrize("aod_count", [0, -1, True, 1.5])
def test_movement_profile_requires_a_positive_plain_integer_aod_count(
    aod_count: object,
) -> None:
    with pytest.raises(ValueError, match="aod_count must be a positive integer"):
        NeutralAtomMovementProfile(aod_count=aod_count)


def test_legacy_latency_payload_gets_the_canonical_movement_default() -> None:
    payload = OperationLatencyProfile().to_dict()
    del payload["neutral_atom_movement"]

    restored = OperationLatencyProfile.from_dict(payload)

    assert restored.neutral_atom_movement == NeutralAtomMovementProfile()


def test_custom_movement_calibration_reaches_the_compiler_pipeline() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=2,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "cx", qubits=(0, 1)),),
            ),
        ),
    )
    baseline = run_evaluation(
        circuit,
        EvaluationConfig(
            profile_id="1.1",
            latency_profile=OperationLatencyProfile(),
        ),
    ).to_dict()
    calibration = NeutralAtomMovementProfile(x_spacing_um=76.0)
    custom = run_evaluation(
        circuit,
        EvaluationConfig(
            profile_id="1.1",
            latency_profile=OperationLatencyProfile(
                neutral_atom_movement=calibration
            ),
        ),
    ).to_dict()

    assert custom["results"]["summary"]["total_latency_s"] > baseline["results"]["summary"][
        "total_latency_s"
    ]
    assert custom["resolved_inputs"]["latency_profile"][
        "neutral_atom_movement"
    ] == calibration.to_dict()


def test_arrival_distribution_applies_cold_start_once_per_parallel_copy() -> None:
    distribution = ArrivalDistribution(
        kind="deterministic",
        mean_interval_s=0.025,
        initial_delay_s=0.020,
        initial_delay_samples=3,
    )
    rng = random.Random(1)
    assert [
        distribution.sample_interval(rng, instance)[0]
        for instance in range(5)
    ] == pytest.approx([0.045, 0.045, 0.045, 0.025, 0.025])
    assert ArrivalDistribution.from_dict(distribution.to_dict()) == distribution


def test_rotated_surface_memory_fit_is_per_cycle_before_extrapolation() -> None:
    per_cycle = rotated_surface_memory_failure_per_cycle(13)
    assert per_cycle == pytest.approx(2.645645400004027e-9)
    assert rotated_surface_memory_failure_for_rounds(13, 13) == pytest.approx(
        3.439338924771107e-8
    )


def test_bb_literature_block_survival_is_preserved_by_ledger_adapter() -> None:
    per_logical_qubit = bb_288_12_18_failure_per_logical_qubit_cycle()
    assert per_logical_qubit == pytest.approx(1.6666666666681946e-13)
    assert 12 * math.log1p(-per_logical_qubit) == pytest.approx(
        math.log1p(-2.0e-12)
    )


def test_clifford_fits_use_reference_aggregation_and_transversal_cnot() -> None:
    assert unrotated_surface_clifford_failure("h", 13) == pytest.approx(
        5.226442749324039e-9
    )
    assert unrotated_surface_clifford_failure("s", 13) == pytest.approx(
        1.6580660448155085e-7
    )
    assert unrotated_surface_clifford_failure("cx", 13) == pytest.approx(
        1.9211980350109168e-8
    )


def test_ppm_fit_uses_paired_corridor_weight_and_supports_distance_extrapolation() -> None:
    configuration = load_fidelity_fit("unrotated_surface_ppm_p1e3")
    assert configuration["experiment"]["metric"] == (
        "decoded_z_product_parity_failure"
    )
    assert configuration["multi_patch_fit"]["coefficients"]["a"] == pytest.approx(
        -0.535717275935929
    )
    assert unrotated_surface_ppm_failure(2, 13) == pytest.approx(
        1.4667338846465167e-8
    )
    assert unrotated_surface_ppm_failure(3, 13) == pytest.approx(
        unrotated_surface_ppm_failure(4, 13)
    )
    assert unrotated_surface_ppm_failure(4, 13) > unrotated_surface_ppm_failure(
        1, 13
    )
    with pytest.raises(ValueError, match="positive integer"):
        unrotated_surface_ppm_failure(0, 13)


def test_selected_factory_output_error_is_separate_from_magic_consumption() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(0, (LogicalOperation("gate", "t", qubits=(0,)),)),
        ),
    )
    specification = build_architecture_specification(
        circuit,
        "1.1",
        policy_overrides={
            "protocols.magic_state.id": "litinski-15to1-17-7-7-p1e3",
        },
    )
    catalog = get_magic_state_factory_profile(
        "litinski-15to1-17-7-7-p1e3"
    )
    output_error = catalog.output_error_probability
    latency = OperationLatencyProfile()
    profile = canonical_fidelity_profile(
        specification,
        latency,
        resolve_resource_protocol_bindings(specification, latency),
    )
    consumption_proxy = rotated_surface_memory_failure_for_rounds(13, 13)
    assert profile.logical_operation_failure_probability["t"] == pytest.approx(
        consumption_proxy
    )
    resource_model = profile.resource_state_models["magic_state"]
    assert resource_model.output_failure_probability == pytest.approx(output_error)
    assert resource_model.protocol_id == catalog.id
    assert resource_model.protocol_profile_hash == catalog.profile_hash
    provenance = profile.provenance["magic_state_operations"]
    assert output_error == pytest.approx(4.5e-8)
    assert provenance["factory_profile"] == catalog.id
    assert provenance["protocol_profile_hash"] == catalog.profile_hash
    assert provenance["factory_output_failure_probability"] == output_error


def test_store_load_uses_requested_inter_module_low_ci_proxy() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=2,
        num_clbits=0,
        layers=(
            LogicalLayer(0, (LogicalOperation("gate", "h", qubits=(0,)),)),
        ),
    )
    specification = build_architecture_specification(circuit, "2.1")
    latency = OperationLatencyProfile()
    profile = canonical_fidelity_profile(
        specification,
        latency,
        resolve_resource_protocol_bindings(specification, latency),
    )
    for opcode in ("STORE_QUBITS", "LOAD_QUBITS"):
        assert profile.operation_failure_probability[opcode] == pytest.approx(
            2.3e-6
        )
    provenance = profile.provenance["bb_surface_store_load"]
    assert provenance["reported_low_mean_high"] == pytest.approx(
        [2.3e-6, 2.8e-6, 3.2e-6]
    )
    assert "optimistic" in provenance["modeling_status"]
    assert "batch" in provenance["aggregation"]


def test_canonical_fidelity_explicitly_covers_every_architecture_opcode() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(LogicalLayer(0, (LogicalOperation("gate", "h", qubits=(0,)),)),),
    )
    specification = build_architecture_specification(circuit, "1.1")
    latency = OperationLatencyProfile()
    profile = canonical_fidelity_profile(
        specification,
        latency,
        resolve_resource_protocol_bindings(specification, latency),
    )
    scalar = set(profile.operation_failure_probability)
    parameterized = set(profile.operation_failure_models)

    assert scalar.isdisjoint(parameterized)
    assert scalar | parameterized == {opcode.value for opcode in ArchitectureOpcode}
    assert profile.operation_failure_probability["EXECUTE_COMPUTE"] == 0.0
    assert profile.operation_failure_probability["PREPARE_MAGIC_STATE"] == 0.0
    assert profile.operation_failure_probability["PREPARE_LOGICAL_BELL"] == 0.0
    assert profile.operation_failure_probability["MOVE_QUBITS"] == 0.0
    assert profile.operation_failure_probability["CLASSICAL_REACTION"] == 0.0
    assert profile.operation_failure_probability["FENCE"] == 0.0


def test_pauli_frame_is_free_but_sx_remains_an_explicit_composite() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(LogicalLayer(0, (LogicalOperation("gate", "sx", qubits=(0,)),)),),
    )
    specification = build_architecture_specification(circuit, "1.1")
    latency = OperationLatencyProfile()
    profile = canonical_fidelity_profile(
        specification,
        latency,
        resolve_resource_protocol_bindings(specification, latency),
    )
    logical = profile.logical_operation_failure_probability
    assert logical["x"] == 0
    assert logical["z"] == 0
    expected_sx = 1 - (
        (1 - unrotated_surface_clifford_failure("h", 13)) ** 2
        * (1 - unrotated_surface_clifford_failure("s", 13))
    )
    assert logical["sx"] == pytest.approx(expected_sx)
    assert profile.provenance["surface_logical_operations"]["sx"][
        "decomposition"
    ] == "H-S-H up to global phase"


def test_selected_logical_bell_quality_and_transversal_cnot_bind_teleport() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(LogicalLayer(0, (LogicalOperation("gate", "h", qubits=(0,)),)),),
    )
    specification = build_architecture_specification(circuit, "1.3")
    catalog = get_entanglement_distillation_profile(
        "boosting-dbell9-ds19-pbell1e2"
    )
    bell_error = catalog.output_error_probability
    latency = OperationLatencyProfile()
    profile = canonical_fidelity_profile(
        specification,
        latency,
        resolve_resource_protocol_bindings(specification, latency),
    )
    channels = profile.operation_failure_models["TELEPORT_QUBITS"]["channels"]
    assert set(channels) == {"transversal_cnot"}
    assert channels["transversal_cnot"] == pytest.approx(
        unrotated_surface_clifford_failure("cnot", 13)
    )
    resource_model = profile.resource_state_models["logical_bell_pair"]
    assert resource_model.output_failure_probability == pytest.approx(bell_error)
    assert resource_model.protocol_id == catalog.id
    assert resource_model.protocol_profile_hash == catalog.profile_hash
    assert resource_model.buffer_idle_models
    for idle_model in resource_model.buffer_idle_models.values():
        assert idle_model.owner_kind == "interconnect"
        assert idle_model.qec_code == catalog.qec_code
        assert idle_model.qec_parameters == {"distance": catalog.code_distance}
        assert idle_model.logical_qubits_per_token == 2
    provenance = profile.provenance["logical_bell_operations"]
    assert provenance["factory_profile"] == catalog.id
    assert provenance["factory_family"] == catalog.family
    assert provenance["protocol_profile_hash"] == catalog.profile_hash
