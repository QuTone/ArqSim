"""Replaceable logical routing backends consuming an external placement."""

from __future__ import annotations

import math
from typing import Callable, Iterable

import networkx as nx

from arqsim.architecture.specification import ArchitectureSpecification
from arqsim.compiler.neutral_atom.aod_layer_scheduler import (
    AODTimingModel,
    schedule_logical_layer,
)
from arqsim.program import FTCircuit
from arqsim.operation_profiles import NeutralAtomMovementProfile

from .errors import LogicalRoutingError, UnsupportedLogicalBackendError
from .models import (
    BackendSpec,
    LogicalLayout,
    LogicalPlacement,
    LogicalRoutePlan,
    Movement,
    RouteStep,
)


# Capacity lowering invokes the same backend once per localized execution unit.
# Keep immutable routing-graph BFS trees at the architecture-layout boundary so
# those calls share graph work instead of rebuilding it for every unit.
_SHORTEST_PATHS_BY_LAYOUT: dict[str, dict[str, dict[str, list[str]]]] = {}
_MAX_CACHED_LAYOUTS = 64


ROUTING_BACKEND_VERSIONS = {
    "powermove_na": "5",
    "powermove_fixed_layout": "5",
    "greedy_steiner_sc": "3",
    "steiner_graph": "3",
}

_POWERMOVE_BACKENDS = frozenset({"powermove_na", "powermove_fixed_layout"})


def validate_routing_operation_compatibility(
    circuit: FTCircuit,
    spec: BackendSpec,
) -> None:
    """Reject operations a routing backend cannot lower.

    Compatibility is intentionally checked from canonical operations, not
    from ``FTCircuit.representation``.  A representation names an IR dialect;
    it does not prove that every operation belongs to a particular gate set.
    """

    if spec.backend not in _POWERMOVE_BACKENDS:
        return
    unsupported = [
        {
            "layer": layer.index,
            "operation": operation_index,
            "kind": operation.kind,
            "name": operation.name,
            "width": len(operation.qubits),
        }
        for layer in circuit.layers
        for operation_index, operation in enumerate(layer.operations)
        if operation.kind not in {"gate", "measurement"}
        or len(operation.qubits) > 2
    ]
    if unsupported:
        raise LogicalRoutingError(
            "PowerMove routing supports gate/measurement operations of width "
            "at most two",
            details={
                "backend": spec.backend,
                "representation": circuit.representation,
                "unsupported_operations": unsupported,
            },
        )


def aod_ordering_valid(movements: Iterable[Movement]) -> bool:
    """Check the row/column ordering invariant used by PowerMove groups."""

    movements = tuple(movements)
    for index, left in enumerate(movements):
        for right in movements[index + 1 :]:
            for dimension in range(2):
                source_delta = right.source[dimension] - left.source[dimension]
                destination_delta = right.destination[dimension] - left.destination[dimension]
                source_sign = 0 if source_delta == 0 else (1 if source_delta > 0 else -1)
                destination_sign = (
                    0 if destination_delta == 0 else (1 if destination_delta > 0 else -1)
                )
                if source_sign != destination_sign:
                    return False
    return True


def _empty_route_plan(
    *,
    placement: LogicalPlacement,
    layout: LogicalLayout,
    spec: BackendSpec,
) -> LogicalRoutePlan:
    return LogicalRoutePlan(
        backend=spec.backend,
        backend_version=ROUTING_BACKEND_VERSIONS[spec.backend],
        effective_options=spec.options,
        layout_hashes={layout.module_id: layout.layout_hash},
        initial_placement=placement,
        final_placement=placement,
        steps=(),
        metrics={"route_steps": 0},
    )


def _powermove_route(
    circuit: FTCircuit,
    layout: LogicalLayout,
    placement: LogicalPlacement,
    spec: BackendSpec,
    specification: ArchitectureSpecification | None,
    movement_profile: NeutralAtomMovementProfile | None,
) -> LogicalRoutePlan:
    if layout.modality != "neutral_atom":
        raise LogicalRoutingError(
            "PowerMove routing requires a neutral-atom layout",
            details={"modality": layout.modality},
        )
    routed_operations = [
        operation
        for layer in circuit.layers
        for operation in layer.operations
        if len(operation.qubits) >= 2 or operation.name.lower() in {"t", "tdg"}
    ]
    if not routed_operations:
        return _empty_route_plan(placement=placement, layout=layout, spec=spec)

    home_positions = {
        qubit: placement.entry(qubit).coordinate
        for qubit in range(circuit.num_qubits)
    }
    if any(
        len(position) != 2
        or any(not math.isfinite(value) for value in position)
        for position in home_positions.values()
    ):
        raise LogicalRoutingError(
            "PowerMove layer routing requires finite 2D home positions",
            details={
                "positions": {
                    str(qubit): list(position)
                    for qubit, position in home_positions.items()
                }
            },
        )
    if len(set(home_positions.values())) != len(home_positions):
        raise LogicalRoutingError("PowerMove placement contains duplicate positions")

    all_magic_slots = layout.slots_of_kind("magic_state")
    requested_magic_order = spec.options.get("magic_state_slot_order")
    if requested_magic_order is None:
        magic_slots = all_magic_slots
    else:
        if not isinstance(requested_magic_order, (list, tuple)):
            raise LogicalRoutingError("magic_state_slot_order must be a sequence")
        requested_ids = tuple(str(value) for value in requested_magic_order)
        available_by_id = {slot.id: slot for slot in all_magic_slots}
        if len(set(requested_ids)) != len(requested_ids) or not set(
            requested_ids
        ) <= set(available_by_id):
            raise LogicalRoutingError(
                "magic_state_slot_order must name unique architecture-owned slots",
                details={
                    "requested": list(requested_ids),
                    "available": sorted(available_by_id),
                },
            )
        magic_slots = tuple(available_by_id[slot_id] for slot_id in requested_ids)
    has_t_gates = any(
        operation.name.lower() in {"t", "tdg"}
        for operation in routed_operations
    )
    if has_t_gates and not magic_slots:
        raise LogicalRoutingError(
            "T-gate routing requires an architecture-owned magic-state buffer slot",
            details={"layout_hash": layout.layout_hash},
        )
    if any(
        len(slot.coordinate) != 2
        or any(not math.isfinite(value) for value in slot.coordinate)
        for slot in magic_slots
    ):
        raise LogicalRoutingError(
            "PowerMove layer routing requires finite 2D magic-state positions",
            details={
                "positions": {slot.id: list(slot.coordinate) for slot in magic_slots},
            },
        )
    magic_coordinates = [slot.coordinate for slot in magic_slots]
    if len(set(magic_coordinates)) != len(magic_coordinates):
        raise LogicalRoutingError("PowerMove magic-state positions are not unique")
    if set(magic_coordinates) & set(home_positions.values()):
        raise LogicalRoutingError(
            "PowerMove magic-state positions overlap the data-patch home mapping"
        )
    if specification is not None:
        node = next(
            (item for item in specification.nodes if item.id == layout.node_id),
            None,
        )
        if node is None:
            raise LogicalRoutingError(
                "PowerMove layout references a Node missing from the Architecture "
                "Specification",
                details={"node": layout.node_id},
            )
        if node.modality != "neutral_atom":
            raise LogicalRoutingError(
                "PowerMove layout references a non-neutral-atom Node",
                details={"node": node.id, "modality": node.modality},
            )
    misplaced_physical_options = sorted(
        {
            "x_spacing_um",
            "y_spacing_um",
            "transfer_duration_us",
            "reference_distance_um",
            "reference_move_duration_us",
            "gate_duration_us",
            "num_aod",
        }
        & set(spec.options)
    )
    if misplaced_physical_options:
        raise LogicalRoutingError(
            "PowerMove physical parameters belong to NeutralAtomMovementProfile, not "
            "compiler options",
            details={"options": misplaced_physical_options},
        )
    calibration = movement_profile or NeutralAtomMovementProfile()
    try:
        num_aod = calibration.aod_count
        timing = AODTimingModel(
            x_spacing_um=calibration.x_spacing_um,
            y_spacing_um=calibration.y_spacing_um,
            distance_metric=str(spec.options.get("distance_metric", "euclidean")),
            distance_scale=float(spec.options.get("distance_scale", 1.0)),
            transfer_duration_us=calibration.transfer_duration_us,
            reference_distance_um=calibration.reference_distance_um,
            reference_move_duration_us=calibration.reference_move_duration_us,
            gate_duration_us=0.0,
        )
    except (TypeError, ValueError) as exc:
        raise LogicalRoutingError(
            "PowerMove timing options are invalid",
            details={"options": dict(spec.options)},
        ) from exc
    steps: list[RouteStep] = []
    layer_schedules = []
    magic_state_positions = tuple(
        (slot.id, tuple(slot.coordinate)) for slot in magic_slots
    )
    for layer in circuit.layers:
        try:
            schedule = schedule_logical_layer(
                layer,
                home_positions,
                magic_state_positions=magic_state_positions,
                num_aod=num_aod,
                timing=timing,
            )
        except ValueError as exc:
            raise LogicalRoutingError(
                "PowerMove could not route a logical layer",
                details={
                    "layer": layer.index,
                    "layout_hash": layout.layout_hash,
                    "placement_hash": placement.placement_hash,
                    "num_aod": num_aod,
                },
            ) from exc
        if not schedule.stages:
            continue
        layer_schedules.append(schedule)
        for stage in schedule.stages:
            moves_by_id = stage.moves_by_id
            start_batches = {
                start: index
                for index, start in enumerate(
                    sorted({task.start_us for task in stage.pipeline.tasks})
                )
            }
            movements = []
            for task in sorted(
                stage.pipeline.tasks,
                key=lambda item: (item.start_us, item.end_us, item.aod, item.id),
            ):
                task_movements = tuple(
                    moves_by_id[move_id] for move_id in task.movement_ids
                )
                if not aod_ordering_valid(
                    Movement(
                        logical_qubit=move.logical_qubit,
                        source=move.source,
                        destination=move.destination,
                        batch=start_batches[task.start_us],
                        aod_group=task.aod,
                        entity_kind=move.entity_kind,
                        source_slot_id=move.source_slot_id,
                        target_logical_qubit=move.target_logical_qubit,
                        phase=move.phase,
                    )
                    for move in task_movements
                ):
                    raise LogicalRoutingError(
                        "PowerMove produced an AOD task that violates row/column ordering",
                        details={"layer": layer.index, "task": task.id},
                    )
                movements.extend(
                    Movement(
                        logical_qubit=move.logical_qubit,
                        source=move.source,
                        destination=move.destination,
                        batch=start_batches[task.start_us],
                        aod_group=task.aod,
                        entity_kind=move.entity_kind,
                        source_slot_id=move.source_slot_id,
                        target_logical_qubit=move.target_logical_qubit,
                        phase=move.phase,
                        task_id=task.id,
                        start_time_us=task.start_us,
                        end_time_us=task.end_us,
                    )
                    for move in task_movements
                )
            magic_assignments = tuple(stage.magic_state_assignments)
            steps.append(
                RouteStep(
                    id=len(steps),
                    layer_index=layer.index,
                    operation_indices=stage.operation_indices,
                    kind="na_round_trip_movement",
                    module_ids=(layout.module_id,),
                    terminal_slots=tuple(
                        str(assignment["source_slot"])
                        for assignment in magic_assignments
                    ),
                    movements=tuple(movements),
                    group=stage.index,
                    metadata={
                        "routing_scope": "logical_layer",
                        "return_home": True,
                        "pipeline_policy": stage.pipeline.policy,
                        "pipeline_duration_us": stage.pipeline.duration_us,
                        "round_trip_barrier_duration_us": (
                            stage.pipeline.round_trip_barrier_duration_us
                        ),
                        "pipeline_savings_us": stage.pipeline.pipeline_savings_us,
                        "magic_state_assignments": [
                            dict(value) for value in magic_assignments
                        ],
                        "aod_schedule": stage.pipeline.to_dict(),
                    },
                )
            )

    total_distance = sum(
        movement.manhattan_distance for step in steps for movement in step.movements
    )
    pipeline_duration = sum(schedule.duration_us for schedule in layer_schedules)
    barrier_duration = sum(
        schedule.round_trip_barrier_duration_us for schedule in layer_schedules
    )
    return LogicalRoutePlan(
        backend=spec.backend,
        backend_version=ROUTING_BACKEND_VERSIONS[spec.backend],
        effective_options={
            "num_aod": num_aod,
            "storage": False,
            "routing_scope": "logical_layer",
            "return_home": True,
            "magic_state_placement": layout.metadata.get("magic_state_placement"),
            "magic_state_slot_order": (
                list(requested_magic_order)
                if requested_magic_order is not None
                else None
            ),
            "timing_model": timing.to_dict(),
            "movement_profile_schema": calibration.schema_version,
            "movement_profile_hash": calibration.profile_hash,
        },
        layout_hashes={layout.module_id: layout.layout_hash},
        initial_placement=placement,
        final_placement=placement,
        steps=tuple(steps),
        metrics={
            "route_steps": len(steps),
            "movement_count": sum(len(step.movements) for step in steps),
            "approach_movement_count": sum(
                movement.phase == "approach"
                for step in steps
                for movement in step.movements
            ),
            "return_movement_count": sum(
                movement.phase == "return"
                for step in steps
                for movement in step.movements
            ),
            "magic_state_movement_count": sum(
                movement.entity_kind == "magic_state"
                for step in steps
                for movement in step.movements
            ),
            "movement_manhattan_distance": total_distance,
            "aod_pipeline_duration_us": pipeline_duration,
            "aod_round_trip_barrier_duration_us": barrier_duration,
            "aod_pipeline_savings_us": max(barrier_duration - pipeline_duration, 0.0),
            "layer_timing": [
                {
                    "layer": schedule.layer_index,
                    "stage_count": len(schedule.stages),
                    "pipeline_duration_us": schedule.duration_us,
                    "round_trip_barrier_duration_us": (
                        schedule.round_trip_barrier_duration_us
                    ),
                    "pipeline_savings_us": max(
                        schedule.round_trip_barrier_duration_us
                        - schedule.duration_us,
                        0.0,
                    ),
                }
                for schedule in layer_schedules
            ],
        },
    )


def _routing_graph(layout: LogicalLayout) -> nx.Graph:
    graph = nx.Graph()
    graph.add_nodes_from(layout.routing_nodes)
    graph.add_edges_from(layout.routing_edges)
    blocked = set(layout.metadata.get("blocked_nodes", ()))
    graph.remove_nodes_from(blocked)
    return graph


def _greedy_interface_tree(
    graph: nx.Graph,
    interfaces: list[tuple[str, ...]],
    *,
    layer_index: int,
    operation_index: int,
    layout: LogicalLayout,
    shortest_paths_by_source: dict[str, dict[str, list[str]]] | None = None,
) -> tuple[set[str], set[tuple[str, str]]]:
    missing = [index for index, values in enumerate(interfaces) if not values]
    if missing:
        raise LogicalRoutingError(
            "A logical terminal has no routing-graph interface",
            details={
                "layer": layer_index,
                "operation": operation_index,
                "terminal_indices": missing,
                "layout_hash": layout.layout_hash,
            },
        )
    tree = {sorted(interfaces[0])[0]}
    tree_edges: set[tuple[str, str]] = set()
    shortest_paths_by_source = (
        shortest_paths_by_source
        if shortest_paths_by_source is not None
        else {}
    )
    for terminal_interfaces in interfaces[1:]:
        candidates = []
        for source in sorted(tree):
            paths = shortest_paths_by_source.get(source)
            if paths is None:
                paths = nx.single_source_shortest_path(graph, source)
                shortest_paths_by_source[source] = paths
            for target in sorted(terminal_interfaces):
                path = paths.get(target)
                if path is None:
                    continue
                candidates.append((len(path), tuple(path)))
        if not candidates:
            raise LogicalRoutingError(
                "Logical routing graph cannot connect all operation terminals",
                details={
                    "layer": layer_index,
                    "operation": operation_index,
                    "connected_tree": sorted(tree),
                    "terminal_interfaces": list(terminal_interfaces),
                    "layout_hash": layout.layout_hash,
                },
            )
        _, path = min(candidates)
        tree.update(path)
        tree_edges.update(
            tuple(sorted((left, right))) for left, right in zip(path, path[1:])
        )
    if any(not tree.intersection(values) for values in interfaces):
        raise LogicalRoutingError(
            "Logical route failed terminal-coverage validation",
            details={"layer": layer_index, "operation": operation_index},
        )
    return tree, tree_edges


def _paths_conflict(
    left: set[str],
    right: set[str],
    graph: nx.Graph,
    guard_distance: int,
) -> bool:
    if left & right:
        return True
    if guard_distance <= 0:
        return False
    for source in left:
        lengths = nx.single_source_shortest_path_length(graph, source, cutoff=guard_distance)
        if right.intersection(lengths):
            return True
    return False


def _steiner_route(
    circuit: FTCircuit,
    layout: LogicalLayout,
    placement: LogicalPlacement,
    spec: BackendSpec,
    specification: ArchitectureSpecification | None,
    movement_profile: NeutralAtomMovementProfile | None,
) -> LogicalRoutePlan:
    if layout.modality != "superconducting":
        raise LogicalRoutingError(
            "Steiner-graph routing requires a superconducting layout",
            details={"modality": layout.modality},
        )
    graph = _routing_graph(layout)
    guard_distance = int(spec.options.get("guard_distance", 0))
    if guard_distance < 0:
        raise LogicalRoutingError("guard_distance cannot be negative")
    magic_slots = tuple(
        sorted(layout.slots_of_kind("magic_state"), key=lambda slot: (slot.coordinate, slot.id))
    )
    requested_magic_order = spec.options.get("magic_state_slot_order")
    forced_magic_slots = None
    if requested_magic_order is not None:
        if not isinstance(requested_magic_order, (list, tuple)):
            raise LogicalRoutingError("magic_state_slot_order must be a sequence")
        requested_ids = tuple(str(value) for value in requested_magic_order)
        available_by_id = {slot.id: slot for slot in magic_slots}
        if len(set(requested_ids)) != len(requested_ids) or not set(
            requested_ids
        ) <= set(available_by_id):
            raise LogicalRoutingError(
                "magic_state_slot_order must name unique architecture-owned slots",
                details={
                    "requested": list(requested_ids),
                    "available": sorted(available_by_id),
                },
            )
        forced_magic_slots = tuple(
            available_by_id[slot_id] for slot_id in requested_ids
        )
    steps: list[RouteStep] = []
    groups_per_layer: dict[int, list[set[str]]] = {}
    # The routing graph is architecture-fixed across all operations.  Cache
    # one BFS tree per source node instead of invoking bidirectional shortest
    # path for every source/terminal pair in every PBC operation.
    if layout.layout_hash not in _SHORTEST_PATHS_BY_LAYOUT:
        if len(_SHORTEST_PATHS_BY_LAYOUT) >= _MAX_CACHED_LAYOUTS:
            _SHORTEST_PATHS_BY_LAYOUT.pop(next(iter(_SHORTEST_PATHS_BY_LAYOUT)))
        _SHORTEST_PATHS_BY_LAYOUT[layout.layout_hash] = {}
    shortest_paths_by_source = _SHORTEST_PATHS_BY_LAYOUT[layout.layout_hash]

    for layer in circuit.layers:
        groups_per_layer[layer.index] = []
        available_magic_slots = list(magic_slots)
        forced_magic_index = 0
        for operation_index, operation in enumerate(layer.operations):
            if not operation.qubits:
                continue
            terminal_slots = [placement.entry(qubit).slot_id for qubit in operation.qubits]
            needs_magic = operation.kind == "pauli_rotation" or operation.name.lower() in {"t", "tdg"}
            selected_magic = None
            tree: set[str]
            tree_edges: set[tuple[str, str]]
            if needs_magic:
                if not magic_slots:
                    raise LogicalRoutingError(
                        "Magic-state operation has no architecture-owned magic-state slot",
                        details={"layer": layer.index, "operation": operation_index},
                    )
                if forced_magic_slots is not None:
                    if forced_magic_index >= len(forced_magic_slots):
                        raise LogicalRoutingError(
                            "magic_state_slot_order is shorter than the layer demand",
                            details={
                                "layer": layer.index,
                                "operation": operation_index,
                                "requested": [
                                    slot.id for slot in forced_magic_slots
                                ],
                            },
                        )
                    candidate_slots = [forced_magic_slots[forced_magic_index]]
                    forced_magic_index += 1
                else:
                    candidate_slots = available_magic_slots or list(magic_slots)
                candidates = []
                for magic in candidate_slots:
                    candidate_terminal_slots = [*terminal_slots, magic.id]
                    interfaces = [
                        layout.slot(slot_id).interfaces
                        for slot_id in candidate_terminal_slots
                    ]
                    try:
                        candidate_tree, candidate_edges = _greedy_interface_tree(
                            graph,
                            interfaces,
                            layer_index=layer.index,
                            operation_index=operation_index,
                            layout=layout,
                            shortest_paths_by_source=shortest_paths_by_source,
                        )
                    except LogicalRoutingError:
                        continue
                    candidates.append(
                        (
                            len(candidate_edges),
                            len(candidate_tree),
                            magic.id,
                            magic,
                            candidate_terminal_slots,
                            candidate_tree,
                            candidate_edges,
                        )
                    )
                if not candidates:
                    raise LogicalRoutingError(
                        "No magic-state patch can connect to the operation terminals",
                        details={
                            "layer": layer.index,
                            "operation": operation_index,
                            "magic_state_slots": [slot.id for slot in magic_slots],
                            "layout_hash": layout.layout_hash,
                        },
                    )
                (
                    _,
                    _,
                    _,
                    selected_magic,
                    terminal_slots,
                    tree,
                    tree_edges,
                ) = min(candidates, key=lambda candidate: candidate[:3])
                if selected_magic in available_magic_slots:
                    available_magic_slots.remove(selected_magic)
            else:
                interfaces = [layout.slot(slot_id).interfaces for slot_id in terminal_slots]
                tree, tree_edges = _greedy_interface_tree(
                    graph,
                    interfaces,
                    layer_index=layer.index,
                    operation_index=operation_index,
                    layout=layout,
                    shortest_paths_by_source=shortest_paths_by_source,
                )
            group = 0
            while group < len(groups_per_layer[layer.index]) and _paths_conflict(
                tree,
                groups_per_layer[layer.index][group],
                graph,
                guard_distance,
            ):
                group += 1
            if group == len(groups_per_layer[layer.index]):
                groups_per_layer[layer.index].append(set())
            groups_per_layer[layer.index][group].update(tree)
            steps.append(
                RouteStep(
                    id=len(steps),
                    layer_index=layer.index,
                    operation_indices=(operation_index,),
                    kind="ancilla_tree",
                    module_ids=(layout.module_id,),
                    terminal_slots=tuple(terminal_slots),
                    path_nodes=tuple(sorted(tree)),
                    path_edges=tuple(sorted(tree_edges)),
                    group=group,
                    metadata={
                        "guard_distance": guard_distance,
                        "magic_state_slot": selected_magic.id if selected_magic else None,
                        "pauli": operation.pauli,
                        "terminal_count": len(terminal_slots),
                    },
                )
            )

    return LogicalRoutePlan(
        backend=spec.backend,
        backend_version=ROUTING_BACKEND_VERSIONS[spec.backend],
        effective_options={
            "guard_distance": guard_distance,
            "conflict_policy": "shared_or_guarded_node",
            "magic_state_slot_order": (
                [slot.id for slot in forced_magic_slots]
                if forced_magic_slots is not None
                else None
            ),
        },
        layout_hashes={layout.module_id: layout.layout_hash},
        initial_placement=placement,
        final_placement=placement,
        steps=tuple(steps),
        metrics={
            "route_steps": len(steps),
            "ancilla_nodes": sum(len(step.path_nodes) for step in steps),
            "groups_per_layer": {
                str(layer): len(groups) for layer, groups in sorted(groups_per_layer.items())
            },
        },
    )


_BACKENDS: dict[
    str,
    Callable[
        [
            FTCircuit,
            LogicalLayout,
            LogicalPlacement,
            BackendSpec,
            ArchitectureSpecification | None,
            NeutralAtomMovementProfile | None,
        ],
        LogicalRoutePlan,
    ],
] = {
    "powermove_na": _powermove_route,
    "powermove_fixed_layout": _powermove_route,
    "greedy_steiner_sc": _steiner_route,
    "steiner_graph": _steiner_route,
}


def route_logical_circuit(
    circuit: FTCircuit,
    layout: LogicalLayout,
    placement: LogicalPlacement,
    spec: BackendSpec,
    *,
    specification: ArchitectureSpecification | None = None,
    movement_profile: NeutralAtomMovementProfile | None = None,
) -> LogicalRoutePlan:
    if specification is not None and not isinstance(
        specification, ArchitectureSpecification
    ):
        raise TypeError(
            "specification must be an ArchitectureSpecification or None"
        )
    if movement_profile is not None and not isinstance(
        movement_profile, NeutralAtomMovementProfile
    ):
        raise TypeError(
            "movement_profile must be a NeutralAtomMovementProfile or None"
        )
    try:
        backend = _BACKENDS[spec.backend]
    except KeyError as exc:
        raise UnsupportedLogicalBackendError(
            "Unknown logical routing backend",
            details={"backend": spec.backend, "supported": sorted(_BACKENDS)},
        ) from exc
    validate_routing_operation_compatibility(circuit, spec)
    return backend(
        circuit,
        layout,
        placement,
        spec,
        specification,
        movement_profile,
    )
