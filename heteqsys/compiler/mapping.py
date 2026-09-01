"""Replaceable logical-qubit mapping backends."""

from __future__ import annotations

from typing import Callable, Mapping

from heteqsys.program import FTCircuit

from .errors import LogicalMappingError, UnsupportedLogicalBackendError
from .models import BackendSpec, LogicalLayout, LogicalPlacement, PlacementEntry


MAPPING_BACKEND_VERSIONS = {
    "sabre_na": "1",
    "row_major_checkerboard_sc": "1",
    "fixed_mapping": "1",
}


def _sorted_data_slots(layout: LogicalLayout):
    return tuple(
        sorted(
            layout.slots_of_kind("data"),
            key=lambda slot: (slot.coordinate[1], slot.coordinate[0], slot.id),
        )
    )


def _validate_capacity(circuit: FTCircuit, layout: LogicalLayout) -> None:
    slots = layout.slots_of_kind("data")
    if len(slots) < circuit.num_qubits:
        raise LogicalMappingError(
            "Logical circuit does not fit the compute-module layout",
            details={
                "logical_qubits": circuit.num_qubits,
                "data_slots": len(slots),
                "module": layout.module_id,
                "layout_hash": layout.layout_hash,
            },
        )


def _placement(
    *,
    circuit: FTCircuit,
    layout: LogicalLayout,
    assignments: Mapping[int, str],
    backend: str,
    options: Mapping[str, object],
    metrics: Mapping[str, object],
) -> LogicalPlacement:
    entries = []
    for logical_qubit in range(circuit.num_qubits):
        slot_id = assignments[logical_qubit]
        slot = layout.slot(slot_id)
        if slot.kind != "data":
            raise LogicalMappingError(
                "Logical qubits can only occupy data slots",
                details={"logical_qubit": logical_qubit, "slot": slot_id, "kind": slot.kind},
            )
        entries.append(
            PlacementEntry(
                logical_qubit=logical_qubit,
                node_id=layout.node_id,
                module_id=layout.module_id,
                slot_id=slot.id,
                coordinate=slot.coordinate,
            )
        )
    return LogicalPlacement(
        backend=backend,
        backend_version=MAPPING_BACKEND_VERSIONS[backend],
        effective_options=options,
        layout_hash=layout.layout_hash,
        entries=tuple(entries),
        metrics=metrics,
    )


def row_major_checkerboard_sc(
    circuit: FTCircuit,
    layout: LogicalLayout,
    spec: BackendSpec,
) -> LogicalPlacement:
    _validate_capacity(circuit, layout)
    if layout.modality != "superconducting":
        raise LogicalMappingError(
            "row_major_checkerboard_sc requires a superconducting compute module",
            details={"modality": layout.modality},
        )
    slots = _sorted_data_slots(layout)
    assignments = {qubit: slots[qubit].id for qubit in range(circuit.num_qubits)}
    return _placement(
        circuit=circuit,
        layout=layout,
        assignments=assignments,
        backend=spec.backend,
        options={},
        metrics={"occupied_slots": circuit.num_qubits},
    )


def fixed_mapping(
    circuit: FTCircuit,
    layout: LogicalLayout,
    spec: BackendSpec,
) -> LogicalPlacement:
    _validate_capacity(circuit, layout)
    raw_assignments = spec.options.get("assignments")
    if not isinstance(raw_assignments, Mapping):
        raise LogicalMappingError(
            "fixed_mapping requires an assignments mapping",
            details={"options": spec.options},
        )
    assignments = {int(qubit): str(slot) for qubit, slot in raw_assignments.items()}
    expected = set(range(circuit.num_qubits))
    if set(assignments) != expected:
        raise LogicalMappingError(
            "fixed_mapping must assign every logical qubit exactly once",
            details={
                "missing": sorted(expected - set(assignments)),
                "unknown": sorted(set(assignments) - expected),
            },
        )
    if len(set(assignments.values())) != len(assignments):
        raise LogicalMappingError(
            "fixed_mapping cannot place two logical qubits in one slot",
            details={"assignments": assignments},
        )
    return _placement(
        circuit=circuit,
        layout=layout,
        assignments=assignments,
        backend=spec.backend,
        options={"assignments": {str(key): value for key, value in sorted(assignments.items())}},
        metrics={"occupied_slots": circuit.num_qubits},
    )


def sabre_na(
    circuit: FTCircuit,
    layout: LogicalLayout,
    spec: BackendSpec,
) -> LogicalPlacement:
    _validate_capacity(circuit, layout)
    if layout.modality != "neutral_atom":
        raise LogicalMappingError(
            "sabre_na requires a neutral-atom compute module",
            details={"modality": layout.modality},
        )
    slots = _sorted_data_slots(layout)
    seed = int(spec.options.get("seed", 42))
    interactions = [
        tuple(operation.qubits)
        for layer in circuit.layers
        for operation in layer.operations
        if len(operation.qubits) >= 2
    ]
    if not interactions or circuit.num_qubits < 2:
        assignments = {qubit: slots[qubit].id for qubit in range(circuit.num_qubits)}
    else:
        try:
            from qiskit import QuantumCircuit, transpile
            from qiskit.transpiler import CouplingMap
        except ImportError as exc:
            raise LogicalMappingError(
                "sabre_na requires Qiskit",
                details={"backend": spec.backend},
            ) from exc

        coordinate_to_index = {slot.coordinate: index for index, slot in enumerate(slots)}
        coupling_edges = []
        for index, slot in enumerate(slots):
            x, y = slot.coordinate[:2]
            for neighbor in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                neighbor_index = coordinate_to_index.get(neighbor)
                if neighbor_index is not None:
                    coupling_edges.append((index, neighbor_index))
        if not coupling_edges:
            assignments = {qubit: slots[qubit].id for qubit in range(circuit.num_qubits)}
        else:
            qiskit_circuit = QuantumCircuit(circuit.num_qubits)
            for interaction in interactions:
                anchor = interaction[0]
                for other in interaction[1:]:
                    qiskit_circuit.cz(anchor, other)
            try:
                transpiled = transpile(
                    qiskit_circuit,
                    coupling_map=CouplingMap(couplinglist=coupling_edges),
                    layout_method="sabre",
                    routing_method="sabre",
                    seed_transpiler=seed,
                    optimization_level=0,
                )
                initial_layout = transpiled.layout.initial_layout
                virtual_to_physical = initial_layout.get_virtual_bits()
                assignments = {
                    qubit: slots[virtual_to_physical[qiskit_circuit.qubits[qubit]]].id
                    for qubit in range(circuit.num_qubits)
                }
            except Exception as exc:
                raise LogicalMappingError(
                    "SABRE could not map the logical circuit to the NA slots",
                    details={
                        "seed": seed,
                        "logical_qubits": circuit.num_qubits,
                        "slots": len(slots),
                    },
                ) from exc

    assignment_coordinates = {
        qubit: layout.slot(slot_id).coordinate for qubit, slot_id in assignments.items()
    }
    interaction_distance = sum(
        sum(
            abs(left - right)
            for left, right in zip(
                assignment_coordinates[interaction[0]],
                assignment_coordinates[other],
            )
        )
        for interaction in interactions
        for other in interaction[1:]
    )
    return _placement(
        circuit=circuit,
        layout=layout,
        assignments=assignments,
        backend=spec.backend,
        options={"seed": seed},
        metrics={
            "occupied_slots": circuit.num_qubits,
            "interaction_manhattan_distance": interaction_distance,
        },
    )


_BACKENDS: dict[str, Callable[[FTCircuit, LogicalLayout, BackendSpec], LogicalPlacement]] = {
    "sabre_na": sabre_na,
    "row_major_checkerboard_sc": row_major_checkerboard_sc,
    "fixed_mapping": fixed_mapping,
}


def map_logical_qubits(
    circuit: FTCircuit,
    layout: LogicalLayout,
    spec: BackendSpec,
) -> LogicalPlacement:
    try:
        backend = _BACKENDS[spec.backend]
    except KeyError as exc:
        raise UnsupportedLogicalBackendError(
            "Unknown logical mapping backend",
            details={"backend": spec.backend, "supported": sorted(_BACKENDS)},
        ) from exc
    return backend(circuit, layout, spec)
