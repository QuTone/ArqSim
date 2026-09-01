"""Modality-specific compiler bindings for the shared MOVE_QUBITS opcode."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Any, Mapping

import networkx as nx

from heteqsys.architecture.specification import ArchitectureSpecification
from heteqsys.compiler.layout import (
    materialize_compute_layout,
    primary_qec_binding,
    single_node_module,
    slot_coordinates,
)
from heteqsys.compiler.models import LogicalCompilerSpec
from heteqsys.compiler.neutral_atom.aod_layer_scheduler import (
    AODTimingModel,
    LayerMove,
    group_collective_movements,
)

from heteqsys.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    MoveOperands,
    ResourceMoveDispatchRecipe,
)
from heteqsys.operation_profiles import (
    NeutralAtomMovementProfile,
    OperationLatencyProfile,
)
from heteqsys.schema import deep_freeze_json, normalize_json


MOVE_COMPILER_VERSION = "arqsim-move-compiler-v2"


def _architecture_slot_records(
    specification: ArchitectureSpecification,
) -> dict[str, tuple[int, int]]:
    return {
        slot_id: coordinate
        for slot_id, coordinate in slot_coordinates(specification).items()
        if coordinate is not None
    }


def _compute_module(specification: ArchitectureSpecification):
    owned = single_node_module(specification, "compute")
    assert owned is not None
    return owned


def _aod_timing(
    compiler_spec: LogicalCompilerSpec,
    calibration: NeutralAtomMovementProfile,
) -> tuple[AODTimingModel, int]:
    options = compiler_spec.routing.options
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
        & set(options)
    )
    if misplaced_physical_options:
        raise ValueError(
            "Neutral-atom movement physical parameters belong to "
            "NeutralAtomMovementProfile, not compiler options: "
            f"{misplaced_physical_options}"
        )
    return (
        AODTimingModel(
            x_spacing_um=calibration.x_spacing_um,
            y_spacing_um=calibration.y_spacing_um,
            distance_metric=str(options.get("distance_metric", "euclidean")),
            distance_scale=float(options.get("distance_scale", 1.0)),
            transfer_duration_us=calibration.transfer_duration_us,
            reference_distance_um=calibration.reference_distance_um,
            reference_move_duration_us=calibration.reference_move_duration_us,
        ),
        calibration.aod_count,
    )


def _list_schedule(durations: list[float], machines: int) -> float:
    loads = [0.0] * machines
    for duration in sorted(durations, reverse=True):
        index = min(range(machines), key=lambda item: (loads[item], item))
        loads[index] += duration
    return max(loads, default=0.0)


def _compile_na_move(
    operands: MoveOperands,
    specification: ArchitectureSpecification,
    compiler_spec: LogicalCompilerSpec,
    model: OperationLatencyProfile,
) -> tuple[float, dict[str, Any]]:
    layout = materialize_compute_layout(specification)
    source_slots = operands.source_slots
    destination_slots = operands.destination_slots
    compute_slots = {slot.id: slot for slot in layout.slots}
    architecture_slots = _architecture_slot_records(specification)

    def coordinate(slot_id: str) -> tuple[float, float]:
        if slot_id in compute_slots:
            value = compute_slots[slot_id].coordinate
            return float(value[0]), float(value[1])
        value = architecture_slots.get(slot_id)
        if value is None:
            raise ValueError(f"MOVE endpoint {slot_id} has no compiler-visible coordinate")
        return float(value[0]), float(value[1])

    calibration = model.neutral_atom_movement
    timing, num_aod = _aod_timing(compiler_spec, calibration)
    moves = tuple(
        LayerMove(
            id=f"move:q{qubit}",
            operation_index=index,
            logical_qubit=qubit,
            source=coordinate(source_slots[qubit]),
            destination=coordinate(destination_slots[qubit]),
            entity_kind=operands.entity_kind.value,
            phase=(
                "delivery"
                if operands.entity_kind.value == "magic_state"
                else "approach"
            ),
        )
        for index, qubit in enumerate(source_slots)
    )
    groups = group_collective_movements(moves, timing)
    durations = [timing.task_duration_us(group) for group in groups]
    duration_us = _list_schedule(durations, num_aod)
    return duration_us * 1e-6, {
        "move_compiler": MOVE_COMPILER_VERSION,
        "move_backend": "aod_collective_one_way",
        "num_aod": num_aod,
        "source_coordinates": {
            str(move.logical_qubit): list(move.source) for move in moves
        },
        "destination_coordinates": {
            str(move.logical_qubit): list(move.destination) for move in moves
        },
        "collective_groups": [
            [move.logical_qubit for move in group] for group in groups
        ],
        "group_duration_us": durations,
        "move_duration_us": duration_us,
        "timing_model": timing.to_dict(),
        "movement_profile_schema": calibration.schema_version,
        "movement_profile_hash": calibration.profile_hash,
        "buffer_endpoint_policy": "canonical_absolute_slot",
    }


def _routing_graph(layout) -> nx.Graph:
    graph = nx.Graph()
    graph.add_nodes_from(layout.routing_nodes)
    graph.add_edges_from(layout.routing_edges)
    return graph


def _compile_ppm_move(
    operands: MoveOperands,
    specification: ArchitectureSpecification,
    model: OperationLatencyProfile,
) -> tuple[float, dict[str, Any]]:
    _node, compute = _compute_module(specification)
    layout = materialize_compute_layout(specification)
    graph = _routing_graph(layout)
    source_slots = operands.source_slots
    destination_slots = operands.destination_slots
    compute_slots = {slot.id: slot for slot in layout.slots}
    architecture_slots = _architecture_slot_records(specification)
    routing_coordinates = {
        str(key): tuple(float(value) for value in point)
        for key, point in layout.metadata.get("routing_coordinates", {}).items()
    }

    def interfaces_for(slot_id: str) -> tuple[str, ...]:
        if slot_id in compute_slots:
            interfaces = compute_slots[slot_id].interfaces
            if interfaces:
                return interfaces
        coordinate = architecture_slots.get(slot_id)
        if coordinate is None or not routing_coordinates:
            raise ValueError(f"MOVE endpoint {slot_id} has no routing interface")
        point = tuple(float(value) for value in coordinate)
        adjacent = tuple(
            sorted(
                node
                for node, candidate in routing_coordinates.items()
                if math.dist(point, candidate[:2]) == 1.0
            )
        )
        if not adjacent:
            raise ValueError(
                f"MOVE endpoint {slot_id} has no adjacent routing interface"
            )
        return adjacent

    terminal_sets: list[tuple[str, ...]] = []
    terminal_labels: list[str] = []
    for qubit in source_slots:
        for slot_id in (source_slots[qubit], destination_slots[qubit]):
            interfaces = interfaces_for(slot_id)
            if not interfaces:
                raise ValueError(f"MOVE terminal {slot_id} has no routing interface")
            terminal_sets.append(tuple(interfaces))
            terminal_labels.append(slot_id)

    # Greedy multi-terminal Steiner approximation: repeatedly connect the
    # closest unconnected terminal interface to the current tree.
    first = min(terminal_sets[0])
    tree_nodes = {first}
    tree_edges: set[tuple[str, str]] = set()
    selected_interfaces = [first]
    for interfaces in terminal_sets[1:]:
        candidates = []
        for interface in interfaces:
            for source in tree_nodes:
                path = nx.shortest_path(graph, source, interface)
                candidates.append((len(path), interface, source, path))
        _, interface, _, path = min(candidates, key=lambda item: item[:3])
        selected_interfaces.append(interface)
        tree_nodes.update(path)
        tree_edges.update(
            tuple(sorted((left, right)))
            for left, right in zip(path, path[1:])
        )

    qec = primary_qec_binding(compute)
    distance = int(qec.parameters["distance"])
    protocol = model.syndrome_profile(model.compute_protocol("superconducting"))
    duration_s = distance * protocol.cycle_time_s
    return duration_s, {
        "move_compiler": MOVE_COMPILER_VERSION,
        "move_backend": "ppm_steiner_tree",
        "qec_distance": distance,
        "rounds": distance,
        "cycle_time_s": protocol.cycle_time_s,
        "terminal_slots": terminal_labels,
        "terminal_interfaces": selected_interfaces,
        "steiner_nodes": sorted(tree_nodes),
        "steiner_edges": [list(edge) for edge in sorted(tree_edges)],
        "buffer_endpoint_policy": "canonical_absolute_slot",
    }


def bind_program_move_costs(
    commands: tuple[ArchitectureInstruction, ...],
    specification: ArchitectureSpecification,
    compiler_spec: LogicalCompilerSpec,
    model: OperationLatencyProfile,
) -> tuple[ArchitectureInstruction, ...]:
    """Compile every Program-plane MOVE with known source/destination slots."""

    compute_node, _compute = _compute_module(specification)
    result = []
    for command in commands:
        if command.opcode != ArchitectureOpcode.MOVE_QUBITS:
            result.append(command)
            continue
        operands = command.move_operands
        if operands is None:
            raise ValueError(f"MOVE command {command.id} lacks typed move operands")
        if compute_node.modality == "neutral_atom":
            duration_s, binding = _compile_na_move(
                operands,
                specification,
                compiler_spec,
                model,
            )
        elif compute_node.modality == "superconducting":
            duration_s, binding = _compile_ppm_move(
                operands, specification, model
            )
        else:
            raise ValueError(
                f"MOVE compiler does not support {compute_node.modality}"
            )
        result.append(
            replace(
                command,
                duration_s=duration_s,
                metadata={**dict(command.metadata), **binding},
            )
        )
    return tuple(result)


def compile_resource_move(
    process: Any,
    consumed_tokens: Mapping[str, tuple[str, ...]],
    consumed_slots: Mapping[str, tuple[str, ...]],
    produced_slots: Mapping[str, tuple[str, ...]],
    specification: ArchitectureSpecification,
    compiler_spec: LogicalCompilerSpec,
    model: OperationLatencyProfile,
    *,
    binding_cache: dict[tuple[Any, ...], Mapping[str, Any]] | None = None,
) -> Mapping[str, Any]:
    """Compile one state-bound Resource-plane MOVE batch."""

    if process.opcode != ArchitectureOpcode.MOVE_QUBITS:
        return {}
    recipe = process.deferred_dispatch
    if not isinstance(recipe, ResourceMoveDispatchRecipe):
        raise ValueError(
            f"Resource MOVE {process.id} lacks a typed deferred-dispatch recipe"
        )
    if len(process.forwards) != 1:
        raise ValueError(f"Resource MOVE {process.id} needs one forwarded token flow")
    source_buffer, destination_buffer = next(iter(process.forwards.items()))
    tokens = tuple(consumed_tokens.get(source_buffer, ()))
    sources = tuple(consumed_slots.get(source_buffer, ()))
    destinations = tuple(produced_slots.get(destination_buffer, ()))
    if not tokens or not (len(tokens) == len(sources) == len(destinations)):
        raise ValueError(f"Resource MOVE {process.id} has incomplete slot binding")

    # The geometry/timing binding is a pure function of this compiler
    # instance, process template, and concrete endpoint slots. Resource token
    # IDs stay solely in the typed tentative binding/transition ledger; they do
    # not affect routing or appear in this observational compiler artifact.
    cache_key = (
        str(process.id),
        process.opcode.value,
        str(source_buffer),
        str(destination_buffer),
        recipe.entity_kind.value,
        sources,
        destinations,
    )
    static_binding = (
        binding_cache.get(cache_key) if binding_cache is not None else None
    )
    if static_binding is None:
        indices = tuple(range(len(tokens)))
        operands = MoveOperands(
            entity_kind=recipe.entity_kind,
            source_slots={index: sources[index] for index in indices},
            destination_slots={
                index: destinations[index] for index in indices
            },
        )
        compute_node, _compute = _compute_module(specification)
        if compute_node.modality == "neutral_atom":
            duration_s, binding = _compile_na_move(
                operands,
                specification,
                compiler_spec,
                model,
            )
        elif compute_node.modality == "superconducting":
            duration_s, binding = _compile_ppm_move(
                operands, specification, model
            )
        else:
            raise ValueError(
                f"MOVE compiler does not support {compute_node.modality}"
            )
        static_record = {
            "duration_s": duration_s,
            **binding,
        }
        static_binding = (
            deep_freeze_json(static_record)
            if binding_cache is not None
            else static_record
        )
        if binding_cache is not None:
            binding_cache[cache_key] = static_binding
    return normalize_json(static_binding)
