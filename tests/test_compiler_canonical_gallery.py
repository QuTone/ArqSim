"""Canonical compiler smoke coverage for the three local reference systems."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from arqsim.architecture.construction import construct_architecture
from arqsim.architecture.gallery import (
    QuantileSizingConfig,
    get_gallery_entry,
)
from arqsim.architecture.identifiers import SubmoduleKey
from arqsim.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    MoveOperands,
)
from arqsim.architecture.profile import ArchitectureProfile
from arqsim.architecture.specification import QECBinding
from arqsim.compiler import (
    COMPILATION_RESULT_SCHEMA_VERSION,
    LogicalCompilationResult,
    CompiledRouteStep,
    ComputeDuration,
    SyndromeProtocolTiming,
    canonical_compiler_spec,
    compile_ft_circuit,
)
from arqsim.compiler.errors import LogicalCompilerValidationError
from arqsim.compiler.layout import materialize_compute_layout
from arqsim.compiler.movement import bind_program_move_costs
from arqsim.operation_profiles import OperationLatencyProfile
from arqsim.program.statistics import CircuitStatistics
from arqsim.program import FTCircuit, LogicalLayer, LogicalOperation
from arqsim.qec import get_magic_state_factory_profile
from arqsim.schema import normalize_json


def _targets(
    profile: ArchitectureProfile,
) -> tuple[tuple[SubmoduleKey, str, str], ...]:
    return tuple(
        (
            SubmoduleKey(owner.id, module.id, submodule.id),
            module.type,
            submodule.type,
        )
        for owner in (*profile.nodes, *profile.interconnects)
        for module in owner.modules
        for submodule in module.submodules
    )


def _specification(profile_id: str):
    entry = get_gallery_entry(profile_id)
    statistics = CircuitStatistics(
        representation="clifford_t",
        logical_qubits=4,
        magic_states_per_layer=(1, 0),
        operation_qubits_by_layer=(((0, 1),), ((2, 3),)),
    )
    targets = _targets(entry.profile)
    qec = {
        target: QECBinding(
            "bivariate_bicycle" if module_type == "memory" else "surface_code",
            {"distance": 3},
        )
        for target, module_type, submodule_type in targets
        if submodule_type != "engine"
    }
    factory = get_magic_state_factory_profile("cultivation-d5-d15-p1e3")
    protocols = {
        target: factory
        for target, _module_type, submodule_type in targets
        if submodule_type == "engine"
    }
    return construct_architecture(
        entry.profile,
        statistics,
        entry.make_sizing_policy(QuantileSizingConfig.uniform(1.0)),
        layout_policy=entry.make_layout_policy(),
        reference_statistics=statistics,
        qec_bindings=qec,
        selected_qec_protocols=protocols,
    )


@pytest.mark.parametrize(
    ("profile_id", "source", "destination", "backend"),
    [
        (
            "1.1",
            "na_node/na_msf/magic_state_output_buffer/slot_0",
            "na_node/na_compute/magic_state_input_buffer/slot_0",
            "aod_collective_one_way",
        ),
        (
            "1.2",
            "sc_node/sc_msf/magic_state_output_buffer/slot_0",
            "sc_node/sc_compute/magic_state_input_buffer/slot_0",
            "ppm_steiner_tree",
        ),
        (
            "2.1",
            "na_node/na_compute/store_load_buffer/slot_0",
            "na_node/na_compute/compute_region/slot_0",
            "aod_collective_one_way",
        ),
    ],
)
def test_local_gallery_movement_compiles_from_canonical_specification(
    profile_id: str,
    source: str,
    destination: str,
    backend: str,
) -> None:
    specification = _specification(profile_id)
    layout = materialize_compute_layout(specification)
    command = ArchitectureInstruction(
        id=0,
        opcode=ArchitectureOpcode.MOVE_QUBITS,
        qubits=(0,),
        move_operands=MoveOperands(
            source_slots={0: source},
            destination_slots={0: destination},
        ),
    )

    (compiled,) = bind_program_move_costs(
        (command,),
        specification,
        canonical_compiler_spec(specification),
        OperationLatencyProfile(),
    )

    assert all(slot.id.count("/") == 3 for slot in layout.slots)
    assert compiled.metadata["move_backend"] == backend
    if backend == "ppm_steiner_tree":
        assert compiled.metadata["terminal_slots"] == (source, destination)
    else:
        assert "source_coordinates" in compiled.metadata


@pytest.mark.parametrize("profile_id", ["1.1", "1.2", "2.1"])
def test_local_gallery_pipeline_compiles_only_from_canonical_specification(
    profile_id: str,
) -> None:
    specification = _specification(profile_id)
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

    result = compile_ft_circuit(
        circuit,
        specification,
        OperationLatencyProfile(),
        defer_magic_routing=False,
    )

    assert result.compute_units
    assert all(
        slot_id.count("/") == 3 for slot_id in result.initial_mapping.values()
    )


def _typed_compilation_result() -> LogicalCompilationResult:
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
    return compile_ft_circuit(
        circuit,
        _specification("1.1"),
        OperationLatencyProfile(),
        defer_magic_routing=False,
    )


def test_compilation_result_is_a_self_hashed_strict_round_trip() -> None:
    result = _typed_compilation_result()

    restored = LogicalCompilationResult.from_json(result.to_json())

    assert restored == result
    assert restored.to_dict() == result.to_dict()
    assert restored.compilation_hash == result.compilation_hash
    assert restored.circuit_hash
    assert restored.architecture_hash
    assert restored.latency_profile_hash


def test_compilation_result_owns_typed_route_facts_before_projection() -> None:
    unit = _typed_compilation_result().compute_units[0]

    assert unit.route_steps
    assert isinstance(unit.route_steps[0], CompiledRouteStep)
    assert isinstance(unit.route.duration, ComputeDuration)
    assert isinstance(unit.syndrome_protocol, SyndromeProtocolTiming)
    assert unit.route.to_instruction_metadata() == {
        "route_hash": unit.route_hash,
        "route_metrics": normalize_json(unit.route_metrics),
        "route_steps": [step.to_dict() for step in unit.route_steps],
        "duration_s": unit.duration_s,
        "duration_components_s": dict(unit.duration_components_s),
        "syndrome_protocol": unit.syndrome_protocol.to_instruction_metadata(),
    }
    serialized_route = unit.route.to_dict()
    assert serialized_route["dispatch_deferred"] is False
    assert "duration_s" not in serialized_route
    assert "service_s" not in serialized_route["syndrome_protocol"]
    with pytest.raises(TypeError):
        unit.route_metrics["forged"] = True  # type: ignore[index]


@pytest.mark.parametrize("invalid", [None, 0, "false"])
def test_compilation_result_rejects_untyped_route_dispatch_state(
    invalid: object,
) -> None:
    document = deepcopy(_typed_compilation_result().to_dict())
    document["compute_units"][0]["route"]["dispatch_deferred"] = invalid

    with pytest.raises(TypeError, match="dispatch_deferred must be a boolean"):
        LogicalCompilationResult.from_dict(document)


@pytest.mark.parametrize("invalid", [True, 1, "streaming"])
def test_compilation_result_rejects_invalid_magic_consumption(
    invalid: object,
) -> None:
    document = deepcopy(_typed_compilation_result().to_dict())
    document["magic_state_consumption"] = invalid

    with pytest.raises((TypeError, LogicalCompilerValidationError)):
        LogicalCompilationResult.from_dict(document)


def test_compilation_hash_covers_magic_consumption_and_route_dispatch_state() -> None:
    result = _typed_compilation_result()
    assert result.to_dict()["magic_state_consumption"] == "bulk_wave"
    incremental = replace(result, magic_state_consumption="incremental")

    assert incremental.compilation_hash != result.compilation_hash

    magic_circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "t", qubits=(0,)),),
            ),
        ),
    )
    deferred = compile_ft_circuit(
        magic_circuit,
        _specification("1.1"),
        OperationLatencyProfile(),
        defer_magic_routing=True,
    )
    deferred_unit = deferred.compute_units[0]
    assert deferred_unit.route.dispatch_deferred is True
    eager_state = replace(
        deferred,
        compute_units=(
            replace(
                deferred_unit,
                route=replace(
                    deferred_unit.route,
                    dispatch_deferred=False,
                ),
            ),
        ),
    )
    assert eager_state.compilation_hash != deferred.compilation_hash

    route = result.compute_units[0].route
    with pytest.raises(LogicalCompilerValidationError, match="cannot be duplicated"):
        replace(route, metrics={"deferred_until_dispatch": True})


@pytest.mark.parametrize("invalid", [True, 1.5, "1"])
def test_compilation_result_rejects_coerced_integer_fields(invalid: object) -> None:
    document = deepcopy(_typed_compilation_result().to_dict())
    document["compute_units"][0]["source"]["partition"][
        "magic_count"
    ] = invalid

    with pytest.raises((TypeError, LogicalCompilerValidationError)):
        LogicalCompilationResult.from_dict(document)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf")])
def test_compilation_result_rejects_non_finite_values(invalid: float) -> None:
    document = deepcopy(_typed_compilation_result().to_dict())
    document["compute_units"][0]["route"]["duration_components_s"][
        "primitive_service"
    ] = invalid

    with pytest.raises((ValueError, LogicalCompilerValidationError)):
        LogicalCompilationResult.from_dict(document)


def test_compilation_result_rejects_unknown_missing_and_tampered_documents() -> None:
    baseline = _typed_compilation_result().to_dict()
    unknown = deepcopy(baseline)
    unknown["alias"] = "legacy"
    missing = deepcopy(baseline)
    del missing["source"]
    tampered = deepcopy(baseline)
    tampered["initial_mapping"]["0"] = "forged/slot"

    with pytest.raises(LogicalCompilerValidationError, match="Unknown"):
        LogicalCompilationResult.from_dict(unknown)
    with pytest.raises(LogicalCompilerValidationError, match="Missing"):
        LogicalCompilationResult.from_dict(missing)
    with pytest.raises(LogicalCompilerValidationError, match="hash"):
        LogicalCompilationResult.from_dict(tampered)


def _document_target(document: object, path: tuple[object, ...]) -> object:
    target = document
    for key in path:
        target = target[key]  # type: ignore[index]
    return target


@pytest.mark.parametrize("invalid", ["not-an-array", {"0": 0}, 0])
@pytest.mark.parametrize(
    "path",
    [
        ("compute_units",),
        (
            "compute_units",
            0,
            "source",
            "partition",
            "operation_indices",
        ),
        ("compute_units", 0, "source", "partition", "active_qubits"),
        ("compute_units", 0, "source", "operation_indices"),
        ("compute_units", 0, "source", "operated_qubits"),
        ("compute_units", 0, "route", "route_steps"),
        (
            "compute_units",
            0,
            "route",
            "route_steps",
            0,
            "terminal_slots",
        ),
    ],
)
def test_compilation_result_requires_exact_array_wire_shapes(
    path: tuple[object, ...],
    invalid: object,
) -> None:
    document = deepcopy(_typed_compilation_result().to_dict())
    parent = _document_target(document, path[:-1])
    parent[path[-1]] = invalid  # type: ignore[index]

    with pytest.raises(TypeError, match="exact JSON array"):
        LogicalCompilationResult.from_dict(document)


def test_compilation_result_rejects_python_tuple_inside_extension_json() -> None:
    document = deepcopy(_typed_compilation_result().to_dict())
    metrics = document["compute_units"][0]["route"]["route_metrics"]
    metrics["layer_timing"] = tuple(metrics["layer_timing"])

    with pytest.raises(TypeError, match="exact JSON"):
        LogicalCompilationResult.from_dict(document)


def test_compilation_result_requires_canonical_optional_field_shape() -> None:
    document = deepcopy(_typed_compilation_result().to_dict())
    syndrome = document["compute_units"][0]["route"]["syndrome_protocol"]
    syndrome.update(
        {
            "distance_module_role": None,
            "distance_module": None,
            "distance_code": None,
            "distance": None,
        }
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="exact canonical JSON wire shape"
    ):
        LogicalCompilationResult.from_dict(document)


@pytest.mark.parametrize("alias", [b"{}", bytearray(b"{}")])
def test_compilation_result_json_requires_a_string(alias: object) -> None:
    with pytest.raises(TypeError, match="must be a string"):
        LogicalCompilationResult.from_json(alias)  # type: ignore[arg-type]


@pytest.mark.parametrize("nested", [False, True])
def test_compilation_result_json_rejects_duplicate_object_keys(
    nested: bool,
) -> None:
    result = _typed_compilation_result()
    document = result.to_json(indent=None)
    if nested:
        needle = f'"circuit_hash": "{result.circuit_hash}"'
        replacement = f'"circuit_hash": "evil", {needle}'
    else:
        needle = (
            f'"schema_version": "{COMPILATION_RESULT_SCHEMA_VERSION}"'
        )
        replacement = f'"schema_version": "evil", {needle}'
    document = document.replace(needle, replacement, 1)

    with pytest.raises(LogicalCompilerValidationError, match="Duplicate JSON"):
        LogicalCompilationResult.from_json(document)


@pytest.mark.parametrize(
    "path",
    [
        ("schema_version",),
        ("instruction_set",),
        ("issue_policy",),
        ("compiler_hash",),
        ("mapping", "backend"),
        ("mapping", "options"),
        ("routing", "backend"),
        ("routing", "options"),
    ],
)
def test_embedded_compiler_spec_cannot_expand_omitted_defaults(
    path: tuple[str, ...],
) -> None:
    document = deepcopy(_typed_compilation_result().to_dict())
    parent = _document_target(document["compiler"], path[:-1])
    del parent[path[-1]]  # type: ignore[index]

    with pytest.raises(LogicalCompilerValidationError, match="Missing"):
        LogicalCompilationResult.from_dict(document)
