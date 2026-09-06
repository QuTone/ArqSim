"""Canonical reference-evaluation fidelity inputs and their provenance.

The profile combines circuit-level LightStim results with explicitly marked
literature-backed proxies at physical error rate 1e-3.  The aggregation path is
production code; every extrapolation and proxy remains visible in provenance.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from arqsim.architecture.isa import ArchitectureOpcode
from arqsim.architecture.specification import (
    ArchitectureSpecification,
    Module,
    Node,
    QECBinding,
)
from arqsim.qec.protocol import get_entanglement_distillation_profile
from arqsim.schema import normalize_json

from .fidelity_model_config import load_fidelity_fit
from .fidelity_profile import (
    FidelityProfile,
    ResourceBufferFidelityModel,
    ResourceStateFidelityModel,
)

if TYPE_CHECKING:
    from .latency_profile import OperationLatencyProfile
    from .resource_binding import ResolvedResourceProtocolBindings


# LightStim paper-artifact circuit-level logical-operation experiments use an
# unrotated surface code and p=1e-3.  For H and transversal CNOT, aggregate the
# subexperiments by their arithmetic mean at each distance, matching the paper
# plotting scripts; S has one S_oneway subexperiment.  The reported LER is for
# one complete logical-operation protocol (two SE rounds), so it is not divided
# by rounds before fitting.
_UNROTATED_SURFACE_CLIFFORD_FITS = {
    "h": {
        "protocol": "fold_transversal_h",
        "log10_slope": -0.5269077359068703,
        "log10_intercept": -1.4319932357329686,
        "r_squared": 0.994115572324686,
        "aggregation": "mean over H_ZtoX and H_XtoZ at each distance",
        "source_points": (
            {"distance": 3, "operation_ler": 0.00108166666666665},
            {"distance": 5, "operation_ler": 6.915893630179344e-5},
            {"distance": 7, "operation_ler": 8.442333472172151e-6},
        ),
    },
    "s": {
        "protocol": "fold_transversal_s_oneway",
        "log10_slope": -0.3979597428152572,
        "log10_intercept": -1.6069215178341625,
        "r_squared": 0.9602161984579384,
        "aggregation": "direct S_oneway LER",
        "source_points": (
            {"distance": 3, "operation_ler": 0.0019621621621621},
            {"distance": 5, "operation_ler": 0.0001645161290322},
            {"distance": 7, "operation_ler": 5.022222222222222e-5},
        ),
    },
    "cnot": {
        "protocol": "transversal_cnot",
        "log10_slope": -0.4904864044483076,
        "log10_intercept": -1.3401046083984722,
        "r_squared": 0.9995127006761091,
        "aggregation": "mean over five initial/measurement subexperiments",
        "source_points": (
            {"distance": 3, "operation_ler": 0.00158833333333332},
            {"distance": 5, "operation_ler": 0.00015221323822911997},
            {"distance": 7, "operation_ler": 1.733788158495141e-5},
        ),
    },
}


# LightStim multi-patch Z-product experiment at circuit-level p=1e-3.  The
# canonical layout alternates patches along the two sides of one vertical
# corridor, so adding an odd patch creates a new corridor row.  The paired
# effective weight captures that measured staircase and is strongly preferred
# to raw Pauli weight (Delta AIC = 62.97 for the parity metric).
_PPM_FIDELITY_CONFIG = load_fidelity_fit("unrotated_surface_ppm_p1e3")
_PPM_MULTI_PATCH_CONFIG = _PPM_FIDELITY_CONFIG["multi_patch_fit"]
_PPM_MULTI_PATCH_COEFFICIENTS = _PPM_MULTI_PATCH_CONFIG["coefficients"]
_UNROTATED_SURFACE_PPM_PARITY_FIT = {
    "log10_distance_coefficient": _PPM_MULTI_PATCH_COEFFICIENTS["a"],
    "log10_effective_weight_coefficient": _PPM_MULTI_PATCH_COEFFICIENTS["b"],
    "log10_intercept": _PPM_MULTI_PATCH_COEFFICIENTS["c"],
    "r_squared_log_cumulative_hazard": (
        _PPM_MULTI_PATCH_CONFIG["transformed_hazard_r_squared"]
    ),
    "delta_aic_raw_minus_paired": _PPM_MULTI_PATCH_CONFIG[
        "delta_aic_raw_minus_paired"
    ],
    "calibrated_distances": tuple(_PPM_MULTI_PATCH_CONFIG["calibrated_distances"]),
    "calibrated_weights": tuple(_PPM_MULTI_PATCH_CONFIG["calibrated_weights"]),
}

# The weight-one point is a separate no-coupler Z-memory/readout control with
# the same total 2d syndrome rounds.  Keep it separate from the multi-patch fit
# rather than pretending that a one-patch measurement uses a corridor.
_PPM_WEIGHT_ONE_CONFIG = _PPM_FIDELITY_CONFIG["weight_one_control"]
_PPM_WEIGHT_ONE_COEFFICIENTS = _PPM_WEIGHT_ONE_CONFIG["coefficients"]
_UNROTATED_SURFACE_SINGLE_Z_CONTROL_FIT = {
    "log10_distance_coefficient": _PPM_WEIGHT_ONE_COEFFICIENTS["a"],
    "log10_intercept": _PPM_WEIGHT_ONE_COEFFICIENTS["c"],
    "r_squared_log_cumulative_hazard": (
        _PPM_WEIGHT_ONE_CONFIG["transformed_hazard_r_squared"]
    ),
    "source_points": tuple(_PPM_WEIGHT_ONE_CONFIG["source_points"]),
}


def unrotated_surface_clifford_failure(
    operation: str,
    distance: int | float,
) -> float:
    """Return a LightStim paper-artifact Clifford-operation LER at p=1e-3."""

    aliases = {"cx": "cnot", "sdg": "s"}
    name = aliases.get(str(operation).lower(), str(operation).lower())
    try:
        fit = _UNROTATED_SURFACE_CLIFFORD_FITS[name]
    except KeyError as exc:
        raise ValueError(f"No calibrated Clifford-operation fit for {operation}") from exc
    value = float(distance)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Surface-code distance must be finite and positive")
    return 10.0 ** (
        float(fit["log10_slope"]) * value + float(fit["log10_intercept"])
    )


def unrotated_surface_ppm_failure(
    weight: int,
    distance: int | float,
) -> float:
    """Return the fitted Z-product PPM parity LER at circuit-level p=1e-3.

    Weight one uses the separately fitted no-coupler control.  Weights two and
    above use the canonical alternating left/right multi-patch corridor, whose
    effective weight is ``2*ceil(weight/2)``.  Distances beyond 3, 5, and 7 or
    weights beyond 2--16 are extrapolations of the LightStim fit.
    """

    if isinstance(weight, bool) or not isinstance(weight, int) or weight <= 0:
        raise ValueError("PPM weight must be a positive integer")
    value = float(distance)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Surface-code distance must be finite and positive")

    if weight == 1:
        fit = _UNROTATED_SURFACE_SINGLE_Z_CONTROL_FIT
        log10_hazard = (
            float(fit["log10_distance_coefficient"]) * value
            + float(fit["log10_intercept"])
        )
    else:
        fit = _UNROTATED_SURFACE_PPM_PARITY_FIT
        effective_weight = 2 * math.ceil(weight / 2)
        log10_hazard = (
            float(fit["log10_distance_coefficient"]) * value
            + float(fit["log10_effective_weight_coefficient"])
            * math.log10(effective_weight)
            + float(fit["log10_intercept"])
        )
    hazard = 10.0**log10_hazard
    return -math.expm1(-hazard)

# Rotated-surface-code Z-memory at circuit-level physical error p=1e-3.  The
# LightStim paper artifact reports d-round experiment LERs at d=3,5,7.  Normalize
# each source point to LER/cycle first, then fit log10(LER/cycle) = slope*d +
# intercept.  Do not extrapolate the multi-round experiment LER itself.
_ROTATED_SURFACE_MEMORY_LOG10_SLOPE = -0.5122366259401231
_ROTATED_SURFACE_MEMORY_LOG10_INTERCEPT = -1.9183922281958368
_ROTATED_SURFACE_MEMORY_FIT_R_SQUARED = 0.9996354509980524
_ROTATED_SURFACE_MEMORY_SOURCE_POINTS = (
    {"distance": 3, "rounds": 3, "experiment_ler": 0.001025},
    {"distance": 5, "rounds": 5, "experiment_ler": 0.0001745762711864},
    {"distance": 7, "rounds": 7, "experiment_ler": 2.1367521367521368e-5},
)


def rotated_surface_memory_failure_per_cycle(distance: int | float) -> float:
    """Return the p=1e-3 rotated-SC memory LER per syndrome cycle.

    This empirical formula is calibrated only for the circuit-level Z-memory
    experiment in the LightStim paper artifact.  It supports architecture QEC
    distances beyond the simulated d=3,5,7 points without hard-coding d=13.
    """

    value = float(distance)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Surface-code distance must be finite and positive")
    return 10.0 ** (
        _ROTATED_SURFACE_MEMORY_LOG10_SLOPE * value
        + _ROTATED_SURFACE_MEMORY_LOG10_INTERCEPT
    )


def rotated_surface_memory_failure_for_rounds(
    distance: int | float,
    rounds: int | float,
) -> float:
    """Convert the fitted per-cycle LER into an any-failure probability."""

    count = float(rounds)
    if not math.isfinite(count) or count < 0:
        raise ValueError("Syndrome-cycle count must be finite and non-negative")
    per_cycle = rotated_surface_memory_failure_per_cycle(distance)
    return -math.expm1(count * math.log1p(-per_cycle))


# Bravyi et al., Nature 627, 778-782 (2024), arXiv:2308.07915v2, Table 1.
# Their p_L(0.001)=2e-12 is already the logical error probability per syndrome
# cycle for the complete [[288,12,18]] block.  It must not be divided by 18
# again.  ArqSim's current idle ledger is expressed in logical-qubit-cycles,
# so use the equivalent symmetric per-logical-qubit probability whose product
# across k=12 logical modes reproduces the paper's block-level survival.
_BB_288_12_18_BLOCK_FAILURE_PER_CYCLE = 2.0e-12
_BB_288_12_18_LOGICAL_QUBITS_PER_BLOCK = 12

# Wills et al., arXiv:2605.21898v2, Table 3.  At uniform p=1e-3 and Relay-BP
# S=100, the inter-module X1X1 measurement has a reported 95% CI of
# [2.3e-6, 3.2e-6] around a 2.8e-6 mean.  Use the requested optimistic low
# endpoint as an optimistic per-batch proxy for BB/surface-code STORE and LOAD.
# The source circuit joins adjacent [[144,12,12]] gross-code modules; it is not
# a direct simulation of ArqSim's [[288,12,18]] BB/surface-code adapter.
_BB_SURFACE_STORE_LOAD_FAILURE_PER_BATCH = 2.3e-6


def bb_288_12_18_failure_per_logical_qubit_cycle() -> float:
    """Adapt the cited block-cycle LER to the current qubit-cycle ledger."""

    return -math.expm1(
        math.log1p(-_BB_288_12_18_BLOCK_FAILURE_PER_CYCLE)
        / _BB_288_12_18_LOGICAL_QUBITS_PER_BLOCK
    )


def _single_node_module(
    specification: ArchitectureSpecification,
    module_type: str,
    *,
    required: bool,
) -> tuple[Node, Module] | None:
    matches = tuple(
        (node, module)
        for node in specification.nodes
        for module in node.modules
        if module.type == module_type
    )
    if len(matches) > 1 or (required and not matches):
        qualifier = "exactly" if required else "at most"
        raise ValueError(
            f"Canonical fidelity requires {qualifier} one {module_type} Module"
        )
    return matches[0] if matches else None


def _primary_qec_binding(module: Module) -> QECBinding:
    regions = tuple(
        submodule
        for submodule in module.submodules
        if submodule.type == "region" and submodule.payload == "logical_qubit"
    )
    if len(regions) != 1 or regions[0].qec is None:
        raise ValueError(
            f"Module {module.id} needs one QEC-bound logical-qubit region"
        )
    return regions[0].qec


def _logical_buffers(
    specification: ArchitectureSpecification,
) -> tuple[tuple[Node, Module, QECBinding, str], ...]:
    result = tuple(
        (
            node,
            module,
            submodule.qec,
            f"{node.id}/{module.id}/{submodule.id}",
        )
        for node in specification.nodes
        for module in node.modules
        for submodule in module.submodules
        if submodule.type == "buffer"
        and submodule.payload == "logical_qubit"
        and submodule.qec is not None
        and submodule.qec.code == "surface"
    )
    return result


def _node_resource_buffers(
    specification: ArchitectureSpecification,
    resource_payload: str,
) -> tuple[tuple[Node, Module, QECBinding, str], ...]:
    """Return QEC-bound Node buffer locations for one resource payload."""

    return tuple(
        (
            node,
            module,
            submodule.qec,
            f"{node.id}/{module.id}/{submodule.id}",
        )
        for node in specification.nodes
        for module in node.modules
        for submodule in module.submodules
        if submodule.type == "buffer"
        and submodule.payload == resource_payload
        and submodule.qec is not None
    )


def _interconnect_resource_buffer_locations(
    specification: ArchitectureSpecification,
    resource_payload: str,
) -> tuple[str, ...]:
    """Return canonical Interconnect-owned resource-buffer locations."""

    return tuple(
        f"{interconnect.id}/{module.id}/{submodule.id}"
        for interconnect in specification.interconnects
        for module in interconnect.modules
        for submodule in module.submodules
        if submodule.type == "buffer" and submodule.payload == resource_payload
    )


def canonical_fidelity_profile(
    specification: ArchitectureSpecification,
    latency: "OperationLatencyProfile",
    resource_protocol_bindings: "ResolvedResourceProtocolBindings",
) -> FidelityProfile:
    """Resolve a location-aware audit profile against one architecture.

    Runtime locations use qualified canonical Module and Submodule addresses.
    This binding therefore belongs in evaluation inputs rather than in the
    static architecture specification.
    """

    if not isinstance(specification, ArchitectureSpecification):
        raise TypeError("specification must be an ArchitectureSpecification")
    compute_entry = _single_node_module(specification, "compute", required=True)
    assert compute_entry is not None
    compute_node, compute = compute_entry
    memory_entry = _single_node_module(specification, "memory", required=False)

    def cycle_time(node: Node, module: Module) -> float:
        if module.type == "compute":
            protocol = latency.compute_protocol(node.modality)
        else:
            protocol = latency.store_load_protocol
        return latency.syndrome_profile(protocol).cycle_time_s

    def idle_probability(module: Module) -> float:
        binding = _primary_qec_binding(module)
        return idle_probability_for_binding(binding)

    def idle_probability_for_binding(binding: QECBinding) -> float:
        if binding.code == "bb":
            parameters = binding.parameters
            identity = (
                int(parameters.get("n", -1)),
                int(parameters.get("k", -1)),
                int(parameters.get("distance", -1)),
            )
            if identity != (288, 12, 18):
                raise ValueError(
                    "The literature-backed BB idle profile only covers "
                    f"[[288,12,18]], not {identity}"
                )
            return bb_288_12_18_failure_per_logical_qubit_cycle()
        if binding.code == "surface":
            return rotated_surface_memory_failure_per_cycle(
                binding.parameters["distance"]
            )
        raise ValueError(
            f"No canonical idle-fidelity calibration for QEC code {binding.code}"
        )

    compute_binding = _primary_qec_binding(compute)
    if compute_binding.code != "surface":
        raise ValueError(
            "The current logical-operation fidelity profile requires a surface-code "
            f"compute module, not {compute_binding.code}"
        )
    compute_distance = compute_binding.parameters["distance"]
    buffer_distance = compute_distance
    surface_buffer_memory_experiment_failure = (
        rotated_surface_memory_failure_for_rounds(
            buffer_distance,
            buffer_distance,
        )
    )
    h_failure = unrotated_surface_clifford_failure("h", compute_distance)
    s_failure = unrotated_surface_clifford_failure("s", compute_distance)
    cnot_failure = unrotated_surface_clifford_failure("cnot", compute_distance)
    # Qiskit's SX is not a Pauli operation.  Until a directly calibrated SX
    # protocol is available, use the compiler identity SX ~ H S H (up to
    # global phase) and keep the approximation explicit in provenance.
    sx_failure = 1.0 - (1.0 - h_failure) ** 2 * (1.0 - s_failure)
    from .resource_binding import ResolvedResourceProtocolBindings

    if not isinstance(
        resource_protocol_bindings,
        ResolvedResourceProtocolBindings,
    ):
        raise TypeError(
            "resource_protocol_bindings must be ResolvedResourceProtocolBindings"
        )
    magic_binding = resource_protocol_bindings.magic_state
    if magic_binding is None:
        raise ValueError("Canonical fidelity needs one resolved magic-state protocol")
    bell_binding = resource_protocol_bindings.logical_bell_pair
    magic_output_failure = float(magic_binding.output_error_probability)
    if not 0 <= magic_output_failure < 1:
        raise ValueError(
            "Magic-state output error probability must be in [0, 1)"
        )
    logical_bell_output_failure = (
        float(bell_binding.output_error_probability)
        if bell_binding is not None
        else 0.0
    )
    if not 0 <= logical_bell_output_failure < 1:
        raise ValueError("Logical-Bell output error probability must be in [0, 1)")
    # Treat the stored-state quality and the logical consumption/teleportation
    # proxy as independent failure channels.  This keeps the cited factory
    # infidelity visible instead of silently replacing the execution cost.
    magic_consumption_failure = 1.0 - (
        (1.0 - surface_buffer_memory_experiment_failure)
        * (1.0 - magic_output_failure)
    )
    compute_location = f"{compute_node.id}/{compute.id}"
    idle_probability_by_location = {
        compute_location: idle_probability(compute),
    }
    cycle_time_by_location = {
        compute_location: cycle_time(compute_node, compute),
    }
    for buffer_node, buffer_module, buffer_qec, buffer_location in _logical_buffers(
        specification
    ):
        idle_probability_by_location[buffer_location] = idle_probability_for_binding(
            buffer_qec
        )
        cycle_time_by_location[buffer_location] = cycle_time(
            buffer_node, buffer_module
        )
    if memory_entry is not None:
        memory_node, memory = memory_entry
        memory_location = f"{memory_node.id}/{memory.id}"
        idle_probability_by_location[memory_location] = idle_probability(memory)
        cycle_time_by_location[memory_location] = cycle_time(memory_node, memory)

    magic_idle_models: dict[str, ResourceBufferFidelityModel] = {}
    for buffer_node, _buffer_module, buffer_qec, buffer_location in (
        _node_resource_buffers(specification, "magic_state")
    ):
        buffer_cycle_time = latency.syndrome_profile(
            latency.compute_protocol(buffer_node.modality)
        ).cycle_time_s
        magic_idle_models[buffer_location] = ResourceBufferFidelityModel(
            location=buffer_location,
            owner_kind="node",
            qec_code=buffer_qec.code,
            qec_parameters=buffer_qec.parameters,
            logical_qubits_per_token=1,
            idle_failure_probability_per_cycle=(
                idle_probability_for_binding(buffer_qec)
            ),
            idle_cycle_time_s=buffer_cycle_time,
        )

    resource_models: dict[str, ResourceStateFidelityModel] = {
        "magic_state": ResourceStateFidelityModel(
            resource_kind="magic_state",
            output_failure_probability=magic_output_failure,
            protocol_id=magic_binding.protocol_id,
            protocol_profile_hash=magic_binding.protocol_profile_hash,
            buffer_idle_models=magic_idle_models,
        )
    }
    if bell_binding is not None:
        bell_profile = get_entanglement_distillation_profile(
            bell_binding.protocol_id
        )
        bell_locations = _interconnect_resource_buffer_locations(
            specification,
            "bell_pair",
        )
        bell_idle_models: dict[str, ResourceBufferFidelityModel] = {}
        if bell_profile.qec_code is not None and bell_profile.code_distance is not None:
            for bell_location in bell_locations:
                bell_idle_models[bell_location] = ResourceBufferFidelityModel(
                    location=bell_location,
                    owner_kind="interconnect",
                    qec_code=bell_profile.qec_code,
                    qec_parameters={"distance": bell_profile.code_distance},
                    # One logical Bell state has one encoded endpoint qubit at
                    # each end of the Interconnect.
                    logical_qubits_per_token=2,
                    idle_failure_probability_per_cycle=(
                        rotated_surface_memory_failure_per_cycle(
                            bell_profile.code_distance
                        )
                    ),
                    idle_cycle_time_s=bell_profile.qec_cycle_time_s,
                )
        resource_models["logical_bell_pair"] = ResourceStateFidelityModel(
            resource_kind="logical_bell_pair",
            output_failure_probability=logical_bell_output_failure,
            protocol_id=bell_binding.protocol_id,
            protocol_profile_hash=bell_binding.protocol_profile_hash,
            buffer_idle_models=bell_idle_models,
        )

    # LightStim does not yet provide a frozen T-injection protocol profile for
    # this reference configuration.  Keep the conservative logical-block
    # consumption proxy here; the selected factory's output error is charged
    # independently by the consumed-resource ledger.
    logical_operations = {
        "h": h_failure,
        "s": s_failure,
        "sdg": s_failure,
        "cx": cnot_failure,
        "cnot": cnot_failure,
        # Pauli-frame X/Z updates are free and therefore carry no direct LER.
        "x": 0.0,
        "z": 0.0,
        "sx": sx_failure,
        "t": surface_buffer_memory_experiment_failure,
        "tdg": surface_buffer_memory_experiment_failure,
        "t_pauli": surface_buffer_memory_experiment_failure,
        "measure": surface_buffer_memory_experiment_failure,
    }
    return FidelityProfile(
        operation_failure_probability={
            ArchitectureOpcode.EXECUTE_COMPUTE.value: 0.0,
            ArchitectureOpcode.PREPARE_MAGIC_STATE.value: 0.0,
            ArchitectureOpcode.PREPARE_LOGICAL_BELL.value: 0.0,
            ArchitectureOpcode.MOVE_QUBITS.value: 0.0,
            ArchitectureOpcode.STORE_QUBITS.value: (
                _BB_SURFACE_STORE_LOAD_FAILURE_PER_BATCH
            ),
            ArchitectureOpcode.LOAD_QUBITS.value: (
                _BB_SURFACE_STORE_LOAD_FAILURE_PER_BATCH
            ),
            ArchitectureOpcode.CLASSICAL_REACTION.value: 0.0,
            ArchitectureOpcode.FENCE.value: 0.0,
        },
        operation_failure_models={
            ArchitectureOpcode.TELEPORT_QUBITS.value: {
                "kind": "independent_channels_per_teleported_item",
                "channels": {
                    "transversal_cnot": cnot_failure,
                },
            }
        },
        logical_operation_failure_probability=logical_operations,
        logical_operation_failure_models={
            "m_pauli": {
                "kind": "unrotated_surface_ppm_parity",
                "distance": compute_distance,
                "weight_one_fit": _UNROTATED_SURFACE_SINGLE_Z_CONTROL_FIT,
                "multi_patch_fit": _UNROTATED_SURFACE_PPM_PARITY_FIT,
            }
        },
        idle_failure_probability_per_cycle=idle_probability_by_location,
        idle_cycle_time_s=cycle_time_by_location,
        resource_state_models=resource_models,
        provenance={
            "calibration_status": "reference_evaluation_v1_mixed_simulation_and_literature",
            "physical_error_probability": 1e-3,
            "accounting_scope": {
                "EXECUTE_COMPUTE": (
                    "direct logical-operation LER plus idle exposure of every "
                    "logical qubit not active in the realized event"
                ),
                "STORE_QUBITS": (
                    "one optimistic failure proxy per realized multi-qubit batch, "
                    "plus elapsed-time idle exposure"
                ),
                "LOAD_QUBITS": (
                    "one optimistic failure proxy per realized multi-qubit batch, "
                    "plus elapsed-time idle exposure"
                ),
                "MOVE_QUBITS": (
                    "no separate movement LER; elapsed time contributes idle "
                    "exposure to qubits not participating in the move"
                ),
                "CLASSICAL_REACTION": (
                    "no direct failure term; its elapsed time contributes idle exposure"
                ),
                "FENCE": "zero-duration ordering primitive with no direct failure term",
                "PREPARE_MAGIC_STATE": (
                    "factory output infidelity is charged when the state reaches "
                    "terminal Program consumption through the backward token "
                    "provenance closure; unused states are not charged"
                ),
                "PREPARE_LOGICAL_BELL": (
                    "no direct application failure is charged at production; the "
                    "selected protocol's output infidelity is charged only when the "
                    "logical Bell pair lies on a Program-consumption provenance closure"
                ),
                "TELEPORT_QUBITS": (
                    "per teleported item, charge the LightStim transversal-CNOT LER; "
                    "the consumed logical-Bell token ledger separately charges its "
                    "selected output infidelity and buffered idle exposure"
                ),
            },
            "surface_memory": {
                "source": "LightStim paper-artifact circuit-level rotated-SC Z-memory",
                "source_file": (
                    "https://github.com/QuTone/LightStim/blob/"
                    "59e5ba569dc10a721c46888ba0e098d60fb41419/"
                    "paper_artifact/memory/precomputed/fig1_surface_codes.csv"
                ),
                "physical_error_probability": 1e-3,
                "normalization": "experiment_ler / rounds before fitting",
                "source_points": _ROTATED_SURFACE_MEMORY_SOURCE_POINTS,
                "formula": "log10(p_idle_per_cycle) = slope * distance + intercept",
                "log10_slope": _ROTATED_SURFACE_MEMORY_LOG10_SLOPE,
                "log10_intercept": _ROTATED_SURFACE_MEMORY_LOG10_INTERCEPT,
                "r_squared": _ROTATED_SURFACE_MEMORY_FIT_R_SQUARED,
            },
            "surface_logical_operations": {
                "source": (
                    "LightStim paper-artifact circuit-level logical-operation "
                    "experiments on the unrotated surface code"
                ),
                "source_files": {
                    "h": (
                        "https://github.com/QuTone/LightStim/blob/"
                        "59e5ba569dc10a721c46888ba0e098d60fb41419/"
                        "paper_artifact/logical_ops/precomputed/fig4_h.csv"
                    ),
                    "s": (
                        "https://github.com/QuTone/LightStim/blob/"
                        "59e5ba569dc10a721c46888ba0e098d60fb41419/"
                        "paper_artifact/logical_ops/precomputed/fig5_s.csv"
                    ),
                    "cnot": (
                        "https://github.com/QuTone/LightStim/blob/"
                        "59e5ba569dc10a721c46888ba0e098d60fb41419/"
                        "paper_artifact/logical_ops/precomputed/fig3_cnot_trans.csv"
                    ),
                },
                "physical_error_probability": 1e-3,
                "operation_scope": "complete two-SE-round logical protocol",
                "normalization": "no per-cycle division for operation LER",
                "formula": "log10(p_operation) = slope * distance + intercept",
                "fits": _UNROTATED_SURFACE_CLIFFORD_FITS,
                "pauli_frame": {
                    "x": "free Pauli-frame update; zero direct LER",
                    "z": "free Pauli-frame update; zero direct LER",
                },
                "sx": {
                    "status": "compiler-decomposition surrogate, not direct calibration",
                    "decomposition": "H-S-H up to global phase",
                    "failure_probability": sx_failure,
                    "combination": "1-(1-p_H)^2*(1-p_S)",
                },
            },
            "surface_ppm": {
                "source": (
                    "LightStim circuit-level unrotated-SC multi-patch "
                    "Z-product experiment"
                ),
                "source_artifacts": normalize_json(
                    _PPM_FIDELITY_CONFIG["source"]
                ),
                "configuration": (
                    "arqsim/operation_profiles/fidelity_profiles/"
                    "unrotated_surface_ppm_p1e3.yaml"
                ),
                "physical_error_probability": 1e-3,
                "metric": "XOR of decoded logical-Z residuals (PPM parity)",
                "layout": "canonical alternating left/right vertical corridor",
                "formula": (
                    "p=1-exp(-10^(a*d+b*log10(2*ceil(w/2))+c)), w>=2"
                ),
                "fit": _UNROTATED_SURFACE_PPM_PARITY_FIT,
                "weight_one_control": _UNROTATED_SURFACE_SINGLE_Z_CONTROL_FIT,
                "extrapolation_notice": (
                    "Only d in {3,5,7} and w in {2,3,4,5,6,7,8,12,16} "
                    "were directly simulated"
                ),
            },
            "bb_memory": {
                "source": "Bravyi et al., High-threshold and low-overhead fault-tolerant quantum memory",
                "source_url": "https://arxiv.org/abs/2308.07915",
                "source_version": "arXiv:2308.07915v2",
                "source_location": "Table 1 and definition of p_L in Section 3",
                "code": "[[288,12,18]]",
                "physical_error_probability": 1e-3,
                "block_failure_probability_per_cycle": (
                    _BB_288_12_18_BLOCK_FAILURE_PER_CYCLE
                ),
                "normalization": (
                    "paper p_L is already per syndrome cycle; adapt block survival "
                    "to the logical-qubit-cycle ledger via 1-(1-p_block)^(1/k)"
                ),
                "logical_qubits_per_block": (
                    _BB_288_12_18_LOGICAL_QUBITS_PER_BLOCK
                ),
                "failure_probability_per_logical_qubit_cycle": (
                    bb_288_12_18_failure_per_logical_qubit_cycle()
                ),
            },
            "bb_surface_store_load": {
                "source": (
                    "Wills et al., Concatenating Algebraic Codes over "
                    "High-Rate Quantum LDPC Codes"
                ),
                "source_url": "https://arxiv.org/abs/2605.21898",
                "source_version": "arXiv:2605.21898v2",
                "source_location": "Table 3",
                "physical_error_probability": 1e-3,
                "source_circuit": (
                    "inter-module X1X1 logical measurement between adjacent "
                    "[[144,12,12]] gross-code modules"
                ),
                "decoder": "Relay-BP",
                "decoder_parameter_s": 100,
                "reported_low_mean_high": [2.3e-6, 2.8e-6, 3.2e-6],
                "selected_failure_probability": (
                    _BB_SURFACE_STORE_LOAD_FAILURE_PER_BATCH
                ),
                "selected_statistic": "low endpoint of reported 95% CI",
                "aggregation": (
                    "apply once to each realized STORE_QUBITS or LOAD_QUBITS "
                    "batch, independent of the number of logical qubits in it"
                ),
                "modeling_status": (
                    "optimistic batch-level inter-module proxy; Table 3 measures "
                    "one X1X1 operation and does not calibrate multi-qubit batch "
                    "scaling or the [[288,12,18]] BB-to-surface-code adapter"
                ),
            },
            "magic_state_operations": {
                "status": "separate_resource_output_and_consumption_proxy",
                "factory_profile": magic_binding.protocol_id,
                "protocol_profile_hash": magic_binding.protocol_profile_hash,
                "factory_output_failure_probability": magic_output_failure,
                "consumption_proxy": (
                    f"corrected_surface_d{buffer_distance}_"
                    f"{buffer_distance}_cycle_memory_failure"
                ),
                "consumption_proxy_failure_probability": (
                    surface_buffer_memory_experiment_failure
                ),
                "combined_failure_probability": magic_consumption_failure,
                "combination": "1-(1-p_factory_output)*(1-p_consumption_proxy)",
                "accounting": (
                    "factory output is charged once by the Program-reachable "
                    "resource ledger; the T-family logical-operation entry "
                    "contains only the fixed consumption proxy"
                ),
            },
            "logical_bell_operations": {
                "factory_profile": (
                    bell_binding.protocol_id if bell_binding is not None else None
                ),
                "factory_family": (
                    bell_binding.protocol_family if bell_binding is not None else None
                ),
                "protocol_profile_hash": (
                    bell_binding.protocol_profile_hash
                    if bell_binding is not None
                    else None
                ),
                "factory_output_failure_probability": logical_bell_output_failure,
                "teleport_proxy": "LightStim unrotated-surface-code transversal CNOT",
                "teleport_proxy_failure_probability": cnot_failure,
                "aggregation": (
                    "Bell output infidelity is charged once by token identity; "
                    "TELEPORT_QUBITS independently carries the transversal-CNOT "
                    "operation channel"
                ),
                "idling_scope": (
                    "no additional cross-modality idling/syndrome channel is folded "
                    "into TELEPORT_QUBITS; the trace still accounts ordinary idle "
                    "exposure of non-participating program qubits"
                ),
                "source": (
                    normalize_json(bell_binding.source)
                    if bell_binding is not None
                    else {}
                ),
                "assumptions": (
                    normalize_json(bell_binding.assumptions)
                    if bell_binding is not None
                    else {}
                ),
            },
        },
    )


__all__ = [
    "bb_288_12_18_failure_per_logical_qubit_cycle",
    "canonical_fidelity_profile",
    "rotated_surface_memory_failure_for_rounds",
    "rotated_surface_memory_failure_per_cycle",
    "unrotated_surface_clifford_failure",
    "unrotated_surface_ppm_failure",
]
