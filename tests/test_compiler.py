from __future__ import annotations

from dataclasses import replace
import math

import pytest

from arqsim.architecture.specification import (
    ArchitectureSpecification,
    LogicalSlot,
    Module,
    Node,
    QECBinding,
    Submodule,
)
from arqsim.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    MoveOperands,
)
from arqsim.compiler import (
    BackendSpec,
    LayoutSlot,
    LogicalLayout,
    LogicalPlacement,
    PlacementEntry,
    canonical_compiler_spec,
    compile_ft_circuit,
)
from arqsim.compiler.errors import (
    LogicalCompilerValidationError,
    LogicalRoutingError,
)
from arqsim.compiler.neutral_atom.aod_layer_scheduler import (
    AODTimingModel,
    RoundTripGroup,
    schedule_logical_layer,
    schedule_round_trip_tasks,
)
from arqsim.compiler.layout import materialize_compute_layout
from arqsim.compiler.movement import bind_program_move_costs
from arqsim.compiler.pipeline import _route_duration_components_s
from arqsim.compiler.routing import route_logical_circuit
from arqsim.operation_profiles import NeutralAtomMovementProfile
from arqsim.operation_profiles import OperationLatencyProfile
from arqsim.program import FTCircuit, LogicalLayer, LogicalOperation


def _slots(
    coordinates: tuple[tuple[int, int] | None, ...],
) -> tuple[LogicalSlot, ...]:
    return tuple(
        LogicalSlot(f"slot_{index}", coordinate)
        for index, coordinate in enumerate(coordinates)
    )


def _compute_specification(
    *,
    modality: str,
    data_coordinates: tuple[tuple[int, int] | None, ...],
    magic_coordinates: tuple[tuple[int, int] | None, ...] = (),
    data_origin: tuple[int, int] | None = None,
    data_grid: tuple[int, int] | None = None,
    magic_origin: tuple[int, int] | None = None,
    external_coordinates: tuple[tuple[int, int], ...] = (),
    store_load_coordinates: tuple[tuple[int, int], ...] = (),
    include_memory: bool = False,
) -> ArchitectureSpecification:
    compute_submodules = [
        Submodule(
            id="compute_region",
            type="region",
            payload="logical_qubit",
            capacity=len(data_coordinates),
            qec=QECBinding("surface_code", {"distance": 3}),
            slots=_slots(data_coordinates),
            logical_origin=data_origin,
            grid_shape=data_grid,
        ),
        Submodule(
            id="magic_input",
            type="buffer",
            payload="magic_state",
            capacity=len(magic_coordinates),
            slots=_slots(magic_coordinates),
            logical_origin=magic_origin,
        ),
    ]
    if store_load_coordinates:
        compute_submodules.append(
            Submodule(
                id="store_load",
                type="buffer",
                payload="logical_qubit",
                capacity=len(store_load_coordinates),
                slots=_slots(store_load_coordinates),
            )
        )
    modules = [
        Module(
            id="compute",
            type="compute",
            submodules=tuple(compute_submodules),
        )
    ]
    if include_memory:
        modules.append(
            Module(
                id="memory",
                type="memory",
                submodules=(
                    Submodule(
                        id="memory_region",
                        type="region",
                        payload="logical_qubit",
                        capacity=1,
                        qec=QECBinding("bivariate_bicycle", {"distance": 3}),
                        slots=(LogicalSlot("slot_0"),),
                    ),
                ),
            )
        )
    if external_coordinates:
        modules.append(
            Module(
                id="factory",
                type="resource_factory",
                submodules=(
                    Submodule(
                        id="magic_output",
                        type="buffer",
                        payload="magic_state",
                        capacity=len(external_coordinates),
                        slots=_slots(external_coordinates),
                    ),
                ),
            )
        )
    return ArchitectureSpecification(
        nodes=(
            Node(
                id="node",
                modality=modality,
                modules=tuple(modules),
            ),
        )
    )


def test_na_layout_reads_resolved_coordinates_and_absolute_slot_ids() -> None:
    specification = _compute_specification(
        modality="neutral_atom",
        data_coordinates=((0, 0), (1, 0), (2, 0), (0, 1)),
        data_origin=(-3, 5),
        data_grid=(2, 3),
        magic_coordinates=((0, 0), (0, 1)),
        magic_origin=(1, 5),
    )

    layout = materialize_compute_layout(specification)

    assert [slot.coordinate for slot in layout.slots_of_kind("data")] == [
        (-3, 5),
        (-2, 5),
        (-1, 5),
        (-3, 6),
    ]
    assert [slot.id for slot in layout.slots_of_kind("data")] == [
        f"node/compute/compute_region/slot_{index}" for index in range(4)
    ]
    assert [slot.id for slot in layout.slots_of_kind("magic_state")] == [
        "node/compute/magic_input/slot_0",
        "node/compute/magic_input/slot_1",
    ]
    assert layout.metadata["slot_addressing"] == "absolute"


def test_compiler_configuration_uses_canonical_node_modality() -> None:
    specification = _compute_specification(
        modality="superconducting",
        data_coordinates=((0, 0),),
    )

    compiler = canonical_compiler_spec(specification)

    assert compiler.mapping.backend == "row_major_checkerboard_sc"
    assert compiler.routing.backend == "greedy_steiner_sc"


@pytest.mark.parametrize(
    ("modality", "expected_backend"),
    [
        ("neutral_atom", "aod_collective_one_way"),
        ("superconducting", "ppm_steiner_tree"),
    ],
)
def test_local_compute_factory_move_uses_canonical_absolute_endpoints(
    modality: str,
    expected_backend: str,
) -> None:
    specification = _compute_specification(
        modality=modality,
        data_coordinates=((0, 0),),
        magic_coordinates=((2, 0),),
        external_coordinates=((4, 0),),
    )
    command = ArchitectureInstruction(
        id=0,
        opcode=ArchitectureOpcode.MOVE_QUBITS,
        qubits=(0,),
        move_operands=MoveOperands(
            entity_kind="magic_state",
            source_slots={0: "node/factory/magic_output/slot_0"},
            destination_slots={0: "node/compute/magic_input/slot_0"},
        ),
    )

    (compiled,) = bind_program_move_costs(
        (command,),
        specification,
        canonical_compiler_spec(specification),
        OperationLatencyProfile(),
    )

    assert compiled.duration_s is not None and compiled.duration_s > 0
    assert compiled.metadata["move_backend"] == expected_backend
    assert compiled.metadata["buffer_endpoint_policy"] == (
        "canonical_absolute_slot"
    )
    assert compiled.move_operands == command.move_operands
    assert {"source_slots", "destination_slots", "entity_kind"}.isdisjoint(
        compiled.metadata
    )


def test_move_cost_compilation_rejects_legacy_metadata_operands() -> None:
    specification = _compute_specification(
        modality="neutral_atom",
        data_coordinates=((0, 0),),
        magic_coordinates=((2, 0),),
    )
    with pytest.raises(ValueError, match="Legacy dispatch control"):
        ArchitectureInstruction(
            id=0,
            opcode=ArchitectureOpcode.MOVE_QUBITS,
            qubits=(0,),
            metadata={
                "source_slots": {"0": "node/compute/compute_region/slot_0"},
                "destination_slots": {
                    "0": "node/compute/magic_input/slot_0"
                },
            },
        )


def test_memory_compute_move_uses_resolved_store_load_coordinate() -> None:
    specification = _compute_specification(
        modality="neutral_atom",
        data_coordinates=((0, 0),),
        magic_coordinates=((2, 0),),
        store_load_coordinates=((-2, 0),),
        include_memory=True,
    )
    command = ArchitectureInstruction(
        id=0,
        opcode=ArchitectureOpcode.MOVE_QUBITS,
        qubits=(0,),
        move_operands=MoveOperands(
            source_slots={0: "node/compute/store_load/slot_0"},
            destination_slots={0: "node/compute/compute_region/slot_0"},
        ),
    )

    (compiled,) = bind_program_move_costs(
        (command,),
        specification,
        canonical_compiler_spec(specification),
        OperationLatencyProfile(),
    )

    assert compiled.metadata["source_coordinates"] == {"0": (-2.0, 0.0)}
    assert compiled.metadata["destination_coordinates"] == {"0": (0.0, 0.0)}


def test_sc_layout_derives_connected_routing_from_all_node_occupancy() -> None:
    specification = _compute_specification(
        modality="superconducting",
        data_coordinates=((-4, 0), (0, 0)),
        magic_coordinates=((4, 0),),
        external_coordinates=((2, 2),),
    )

    layout = materialize_compute_layout(specification)

    assert {
        slot.id: slot.coordinate for slot in layout.slots
    } == {
        "node/compute/compute_region/slot_0": (-4, 0),
        "node/compute/compute_region/slot_1": (0, 0),
        "node/compute/magic_input/slot_0": (4, 0),
    }
    assert layout.metadata["routing_derivation"] == "occupied_slot_complement"
    assert [2, 2] in layout.metadata["routing_occupied_points"]
    assert "R2_2" not in layout.routing_nodes
    assert all(slot.interfaces for slot in layout.slots)
    connected = {layout.routing_nodes[0]}
    while True:
        expanded = connected | {
            right if left in connected else left
            for left, right in layout.routing_edges
            if left in connected or right in connected
        }
        if expanded == connected:
            break
        connected = expanded
    assert connected == set(layout.routing_nodes)


def test_sc_reserved_grid_envelope_expands_compiler_canvas() -> None:
    specification = _compute_specification(
        modality="superconducting",
        data_coordinates=((0, 0), (2, 0)),
        data_grid=(5, 7),
        magic_coordinates=((0, 0), (0, 2)),
        magic_origin=(8, 0),
    )

    layout = materialize_compute_layout(specification)

    assert layout.metadata["bounds"] == {
        "x_min": -1,
        "x_max": 9,
        "y_min": -1,
        "y_max": 5,
    }
    assert "R6_4" in layout.routing_nodes


def test_sc_canvas_limit_is_checked_before_dense_materialization() -> None:
    specification = _compute_specification(
        modality="superconducting",
        data_coordinates=((0, 0), (1_000_000_000, 0)),
    )

    with pytest.raises(
        LogicalCompilerValidationError,
        match="exceeds the dense compiler limit",
    ) as exc_info:
        materialize_compute_layout(specification)

    assert exc_info.value.details["canvas_sites"] > exc_info.value.details[
        "max_canvas_sites"
    ]


def test_compiler_rejects_identity_only_compute_slots() -> None:
    specification = _compute_specification(
        modality="neutral_atom",
        data_coordinates=(None,),
    )

    with pytest.raises(
        LogicalCompilerValidationError,
        match="need logical coordinates",
    ):
        materialize_compute_layout(specification)


def test_materializer_accepts_only_canonical_specification() -> None:
    with pytest.raises(TypeError, match="ArchitectureSpecification"):
        materialize_compute_layout(object())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("groups", "aods", "duration"),
    [(3, 1, 6.0), (3, 2, 3.0), (4, 2, 4.0)],
)
def test_aod_pipeline_respects_capacity(groups: int, aods: int, duration: float) -> None:
    schedule = schedule_round_trip_tasks(
        tuple(RoundTripGroup(index, 1.0, 1.0) for index in range(groups)),
        num_aod=aods,
    )
    assert schedule.duration_us == duration
    assert schedule.duration_us <= schedule.round_trip_barrier_duration_us


def test_aod_distance_models_are_explicit() -> None:
    euclidean = AODTimingModel(x_spacing_um=19.0, y_spacing_um=15.0)
    manhattan = AODTimingModel(
        x_spacing_um=19.0, y_spacing_um=15.0, distance_metric="manhattan"
    )
    assert euclidean.distance_um((0, 0), (1, 1)) == pytest.approx(math.hypot(19, 15))
    assert manhattan.distance_um((0, 0), (1, 1)) == 34.0


def test_layer_route_returns_moved_patch_home() -> None:
    layer = LogicalLayer(
        0, (LogicalOperation("gate", "cx", qubits=(0, 2)),)
    )
    home = {0: (1.0, 2.0), 1: (2.0, 1.0), 2: (2.0, 0.0)}
    schedule = schedule_logical_layer(
        layer, home, num_aod=2, timing=AODTimingModel(transfer_duration_us=0.0)
    )
    moves = (*schedule.stages[0].forward_moves, *schedule.stages[0].return_moves)
    assert [(item.logical_qubit, item.phase) for item in moves] == [
        (0, "approach"), (0, "return")
    ]
    assert moves[0].source == home[0]
    assert moves[-1].destination == home[0]


def _powermove_route(
    movement_profile: NeutralAtomMovementProfile | None,
    *,
    representation: str = "clifford_t",
    routing_options: dict | None = None,
    coordinates: tuple[tuple[int, int], ...] = ((0, 0), (2, 0)),
    interactions: tuple[tuple[int, int], ...] = ((0, 1),),
):
    slot_ids = tuple(
        f"node/compute/compute_region/slot_{index}"
        for index in range(len(coordinates))
    )
    layout = LogicalLayout(
        module_id="compute",
        node_id="node",
        modality="neutral_atom",
        layout_type="zoned_grid",
        slots=tuple(
            LayoutSlot(slot_ids[index], "data", coordinate)
            for index, coordinate in enumerate(coordinates)
        ),
    )
    placement = LogicalPlacement(
        backend="fixed_mapping",
        backend_version="1",
        effective_options={},
        layout_hash=layout.layout_hash,
        entries=tuple(
            PlacementEntry(
                index,
                "node",
                "compute",
                slot_ids[index],
                coordinate,
            )
            for index, coordinate in enumerate(coordinates)
        ),
    )
    circuit = FTCircuit(
        representation=representation,
        num_qubits=len(coordinates),
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                tuple(
                    LogicalOperation("gate", "cx", qubits=qubits)
                    for qubits in interactions
                ),
            ),
        ),
    )
    return route_logical_circuit(
        circuit,
        layout,
        placement,
        BackendSpec(
            "powermove_na",
            routing_options or {"distance_metric": "euclidean"},
        ),
        movement_profile=movement_profile,
    )


def _superconducting_steiner_route(
    interactions_by_layer: tuple[tuple[tuple[int, int], ...], ...],
):
    """Route small, explicit paths whose conflict groups are easy to audit."""

    routing_nodes = tuple(f"r{index}" for index in range(5))
    coordinates = ((0.0, 0.0), (1.0, 0.0), (3.0, 0.0), (4.0, 0.0))
    interfaces = (("r0",), ("r1",), ("r3",), ("r4",))
    layout = LogicalLayout(
        module_id="compute",
        node_id="node",
        modality="superconducting",
        layout_type="test_line",
        slots=tuple(
            LayoutSlot(
                f"slot_{index}",
                "data",
                coordinate,
                interfaces[index],
            )
            for index, coordinate in enumerate(coordinates)
        ),
        routing_nodes=routing_nodes,
        routing_edges=tuple(
            (f"r{index}", f"r{index + 1}") for index in range(4)
        ),
    )
    placement = LogicalPlacement(
        backend="fixed_mapping",
        backend_version="1",
        effective_options={},
        layout_hash=layout.layout_hash,
        entries=tuple(
            PlacementEntry(
                index,
                "node",
                "compute",
                f"slot_{index}",
                coordinate,
            )
            for index, coordinate in enumerate(coordinates)
        ),
    )
    circuit = FTCircuit(
        representation="gate",
        num_qubits=4,
        num_clbits=0,
        layers=tuple(
            LogicalLayer(
                layer_index,
                tuple(
                    LogicalOperation("gate", "cx", qubits=qubits)
                    for qubits in interactions
                ),
            )
            for layer_index, interactions in enumerate(interactions_by_layer)
        ),
    )
    return route_logical_circuit(
        circuit,
        layout,
        placement,
        BackendSpec("greedy_steiner_sc", {"guard_distance": 0}),
    )


def _superconducting_route_duration(route):
    specification = _compute_specification(
        modality="superconducting",
        data_coordinates=((0, 0), (1, 0), (3, 0), (4, 0)),
    )
    return _route_duration_components_s(
        specification,
        specification.nodes[0],
        route,
        OperationLatencyProfile(),
    )


def test_sc_disjoint_routes_share_one_parallel_syndrome_service_wave() -> None:
    route = _superconducting_steiner_route((((0, 1), (2, 3)),))

    assert [step.group for step in route.steps] == [0, 0]
    duration, syndrome = _superconducting_route_duration(route)
    assert duration.total_s == pytest.approx(syndrome.service_s)
    assert duration.compiler_routing_s == pytest.approx(0.0)


def test_sc_conflicting_routes_pay_one_syndrome_service_per_group() -> None:
    route = _superconducting_steiner_route((((0, 2), (1, 3)),))

    assert [step.group for step in route.steps] == [0, 1]
    duration, syndrome = _superconducting_route_duration(route)
    assert duration.total_s == pytest.approx(2 * syndrome.service_s)
    assert duration.compiler_routing_s == pytest.approx(syndrome.service_s)


def test_sc_route_groups_are_local_to_each_logical_layer() -> None:
    route = _superconducting_steiner_route(
        (
            ((0, 1), (2, 3)),
            ((0, 1), (2, 3)),
        )
    )

    assert [
        (step.layer_index, step.group) for step in route.steps
    ] == [(0, 0), (0, 0), (1, 0), (1, 0)]
    duration, syndrome = _superconducting_route_duration(route)
    assert duration.total_s == pytest.approx(2 * syndrome.service_s)
    assert duration.compiler_routing_s == pytest.approx(syndrome.service_s)


def test_powermove_checks_operations_not_legacy_representation_label() -> None:
    route = _powermove_route(None, representation="clifford_rz")

    assert route.backend == "powermove_na"


def test_compiler_requires_gate_timing_for_active_compute_modality() -> None:
    specification = _compute_specification(
        modality="neutral_atom",
        data_coordinates=((0, 0),),
    )
    circuit = FTCircuit(
        representation="gate",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "h", qubits=(0,)),),
            ),
        ),
    )

    with pytest.raises(ValueError, match="No gate duration.*neutral_atom"):
        compile_ft_circuit(
            circuit,
            specification,
            OperationLatencyProfile(
                gate_duration_s={"superconducting": 1e-6},
            ),
        )


def test_compiler_preserves_explicit_zero_gate_timing() -> None:
    specification = _compute_specification(
        modality="neutral_atom",
        data_coordinates=((0, 0),),
    )
    circuit = FTCircuit(
        representation="gate",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "h", qubits=(0,)),),
            ),
        ),
    )

    compilation = compile_ft_circuit(
        circuit,
        specification,
        OperationLatencyProfile(gate_duration_s={"neutral_atom": 0.0}),
    )

    (unit,) = compilation.compute_units
    assert unit.route.duration.primitive_service_s == (
        unit.route.syndrome_protocol.service_s
    )


def test_nonempty_neutral_atom_route_requires_pipeline_duration_metric() -> None:
    route = _powermove_route(None)
    assert route.steps
    malformed = replace(route, metrics={"route_steps": len(route.steps)})
    specification = _compute_specification(
        modality="neutral_atom",
        data_coordinates=((0, 0), (2, 0)),
    )

    with pytest.raises(
        LogicalCompilerValidationError,
        match="must report aod_pipeline_duration_us",
    ):
        _route_duration_components_s(
            specification,
            specification.nodes[0],
            malformed,
            OperationLatencyProfile(),
        )


def test_default_compiler_rejects_incompatible_operations_before_deferred_route() -> None:
    specification = _compute_specification(
        modality="neutral_atom",
        data_coordinates=((0, 0),),
        magic_coordinates=((2, 0),),
    )
    circuit = FTCircuit(
        representation="pbc",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (
                    LogicalOperation(
                        "pauli_rotation",
                        "t_pauli",
                        qubits=(0,),
                        pauli="+X",
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(LogicalRoutingError, match="gate/measurement") as exc_info:
        compile_ft_circuit(
            circuit,
            specification,
            OperationLatencyProfile(),
            defer_magic_routing=True,
        )

    assert exc_info.value.details["unsupported_operations"] == [
        {
            "layer": 0,
            "operation": 0,
            "kind": "pauli_rotation",
            "name": "t_pauli",
            "width": 1,
        }
    ]


def test_powermove_default_movement_profile_preserves_implicit_timing() -> None:
    implicit = _powermove_route(None)
    profile = NeutralAtomMovementProfile()
    explicit = _powermove_route(profile)

    assert explicit.metrics["aod_pipeline_duration_us"] == pytest.approx(
        implicit.metrics["aod_pipeline_duration_us"]
    )
    assert explicit.effective_options["timing_model"] == (
        implicit.effective_options["timing_model"]
    )
    assert explicit.effective_options["movement_profile_hash"] == profile.profile_hash


def test_powermove_custom_movement_profile_changes_compiled_duration() -> None:
    baseline = _powermove_route(NeutralAtomMovementProfile())
    calibrated = NeutralAtomMovementProfile(x_spacing_um=76.0)
    custom = _powermove_route(calibrated)

    assert custom.metrics["aod_pipeline_duration_us"] > baseline.metrics[
        "aod_pipeline_duration_us"
    ]
    assert custom.effective_options["timing_model"]["x_spacing_um"] == 76.0
    assert custom.effective_options["movement_profile_hash"] == calibrated.profile_hash


def test_powermove_aod_concurrency_comes_from_movement_profile() -> None:
    coordinates = (
        (2, 0),
        (0, 1),
        (2, 1),
        (1, 0),
        (0, 0),
        (1, 1),
        (3, 1),
        (3, 0),
    )
    interactions = ((0, 1), (2, 3), (4, 5), (6, 7))
    serial = _powermove_route(
        NeutralAtomMovementProfile(aod_count=1),
        coordinates=coordinates,
        interactions=interactions,
    )
    parallel = _powermove_route(
        NeutralAtomMovementProfile(aod_count=4),
        coordinates=coordinates,
        interactions=interactions,
    )

    assert parallel.metrics["aod_pipeline_duration_us"] < serial.metrics[
        "aod_pipeline_duration_us"
    ]
    assert serial.effective_options["num_aod"] == 1
    assert parallel.effective_options["num_aod"] == 4


@pytest.mark.parametrize(
    ("option", "value"),
    [("x_spacing_um", 76.0), ("num_aod", 1)],
)
def test_powermove_rejects_physical_parameters_in_compiler_options(
    option: str,
    value: float | int,
) -> None:
    with pytest.raises(
        LogicalRoutingError,
        match="physical parameters belong to NeutralAtomMovementProfile",
    ):
        _powermove_route(
            NeutralAtomMovementProfile(),
            routing_options={"distance_metric": "euclidean", option: value},
        )
