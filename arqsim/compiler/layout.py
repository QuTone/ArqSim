"""Lower canonical logical slots into one compiler routing layout.

The Architecture Specification owns slot identity, capacity, and logical
coordinates. This module reads those completed facts directly. It does not
accept v1 ``ModuleSpec`` layout dictionaries and does not invent historical
``D``/``SL``/``M``/``MO`` aliases.

Superconducting routing sites are deliberately absent from the Architecture
Specification. They are compiler artifacts derived here as the unoccupied
complement of every spatial slot and reserved grid envelope on the compute
Node's logical canvas.
"""

from __future__ import annotations

from collections.abc import Iterator

from arqsim.architecture.specification import (
    ArchitectureSpecification,
    Interconnect,
    Module,
    Node,
    QECBinding,
    Submodule,
)

from .errors import LogicalCompilerValidationError
from .models import LayoutSlot, LogicalLayout


Owner = Node | Interconnect
OwnedModule = tuple[Node, Module]
MAX_DENSE_ROUTING_SITES = 100_000


def absolute_module_id(owner_id: str, module_id: str) -> str:
    """Return the canonical absolute address of one Module."""

    return f"{owner_id}/{module_id}"


def absolute_submodule_id(
    owner_id: str,
    module_id: str,
    submodule_id: str,
) -> str:
    """Return the canonical absolute address of one Submodule."""

    return f"{absolute_module_id(owner_id, module_id)}/{submodule_id}"


def absolute_slot_id(
    owner_id: str,
    module_id: str,
    submodule_id: str,
    slot_id: str,
) -> str:
    """Return the canonical absolute address of one logical slot."""

    return f"{absolute_submodule_id(owner_id, module_id, submodule_id)}/{slot_id}"


def node_modules_by_type(
    specification: ArchitectureSpecification,
    module_type: str,
) -> tuple[OwnedModule, ...]:
    """Return Node-owned Modules with one canonical Module type."""

    if not isinstance(specification, ArchitectureSpecification):
        raise TypeError("specification must be an ArchitectureSpecification")
    return tuple(
        (node, module)
        for node in specification.nodes
        for module in node.modules
        if module.type == module_type
    )


def single_node_module(
    specification: ArchitectureSpecification,
    module_type: str,
    *,
    required: bool = True,
) -> OwnedModule | None:
    """Resolve the one Module of a compiler-supported semantic type."""

    matches = node_modules_by_type(specification, module_type)
    if len(matches) > 1 or (required and not matches):
        raise LogicalCompilerValidationError(
            f"Logical compilation supports one {module_type} Module",
            details={
                "module_type": module_type,
                "modules": [f"{node.id}/{module.id}" for node, module in matches],
            },
        )
    return matches[0] if matches else None


def submodules_by_signature(
    module: Module,
    *,
    submodule_type: str,
    payload: str,
) -> tuple[Submodule, ...]:
    """Return Submodules selected by canonical structural semantics."""

    return tuple(
        submodule
        for submodule in module.submodules
        if submodule.type == submodule_type and submodule.payload == payload
    )


def single_submodule(
    module: Module,
    *,
    submodule_type: str,
    payload: str,
    required: bool = True,
) -> Submodule | None:
    matches = submodules_by_signature(
        module,
        submodule_type=submodule_type,
        payload=payload,
    )
    if len(matches) > 1 or (required and not matches):
        raise LogicalCompilerValidationError(
            "Compiler-facing Module has an unsupported Submodule cardinality",
            details={
                "module": module.id,
                "submodule_type": submodule_type,
                "payload": payload,
                "submodules": [item.id for item in matches],
            },
        )
    return matches[0] if matches else None


def primary_qec_submodule(module: Module) -> Submodule:
    """Return the canonical QEC-bearing region for compute or memory."""

    if module.type not in {"compute", "memory"}:
        raise LogicalCompilerValidationError(
            "Compiler QEC lookup supports compute and memory Modules",
            details={"module": module.id, "module_type": module.type},
        )
    submodule = single_submodule(
        module,
        submodule_type="region",
        payload="logical_qubit",
    )
    assert submodule is not None
    return submodule


def primary_qec_binding(module: Module) -> QECBinding:
    """Return a Module's selected canonical QEC binding."""

    submodule = primary_qec_submodule(module)
    if submodule.qec is None:
        raise LogicalCompilerValidationError(
            "Compiler-facing logical region needs a QEC binding",
            details={"module": module.id, "submodule": submodule.id},
        )
    return submodule.qec


def iter_absolute_slots(
    specification: ArchitectureSpecification,
) -> Iterator[tuple[Owner, Module, Submodule, str, tuple[int, int] | None]]:
    """Yield every canonical slot with its absolute address and coordinate."""

    for owner in (*specification.nodes, *specification.interconnects):
        for module in owner.modules:
            for submodule in module.submodules:
                origin = submodule.logical_origin or (0, 0)
                for slot in submodule.slots:
                    coordinate = (
                        None
                        if slot.coordinate is None
                        else (
                            origin[0] + slot.coordinate[0],
                            origin[1] + slot.coordinate[1],
                        )
                    )
                    yield (
                        owner,
                        module,
                        submodule,
                        absolute_slot_id(
                            owner.id,
                            module.id,
                            submodule.id,
                            slot.id,
                        ),
                        coordinate,
                    )


def slot_coordinates(
    specification: ArchitectureSpecification,
) -> dict[str, tuple[int, int] | None]:
    """Index canonical slot coordinates by globally unique absolute address."""

    return {
        slot_id: coordinate
        for _owner, _module, _submodule, slot_id, coordinate in iter_absolute_slots(
            specification
        )
    }


def submodule_slot_coordinates(
    owner: Owner,
    module: Module,
    submodule: Submodule,
) -> tuple[tuple[str, tuple[int, int]], ...]:
    """Return spatial slots of one Submodule using absolute identities."""

    origin = submodule.logical_origin or (0, 0)
    result: list[tuple[str, tuple[int, int]]] = []
    missing: list[str] = []
    for slot in submodule.slots:
        if slot.coordinate is None:
            missing.append(slot.id)
            continue
        result.append(
            (
                absolute_slot_id(owner.id, module.id, submodule.id, slot.id),
                (
                    origin[0] + slot.coordinate[0],
                    origin[1] + slot.coordinate[1],
                ),
            )
        )
    if missing:
        raise LogicalCompilerValidationError(
            "Compiler-active Submodule slots need logical coordinates",
            details={
                "submodule": f"{owner.id}/{module.id}/{submodule.id}",
                "slots": missing,
            },
        )
    return tuple(result)


def _coordinate_node_id(x: int, y: int) -> str:
    return f"R{x}_{y}"


def _adjacent_interfaces(
    coordinate: tuple[int, int],
    routing_coordinates: dict[str, tuple[int, int]],
) -> tuple[str, ...]:
    x, y = coordinate
    candidates = {(x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)}
    return tuple(
        sorted(
            node
            for node, point in routing_coordinates.items()
            if point in candidates
        )
    )


def _node_extent(
    node: Node,
) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """Return occupied sites and extent anchors on one Node canvas."""

    occupied: set[tuple[int, int]] = set()
    extent: set[tuple[int, int]] = set()
    for module in node.modules:
        for submodule in module.submodules:
            origin = submodule.logical_origin or (0, 0)
            for slot in submodule.slots:
                if slot.coordinate is not None:
                    occupied.add(
                        (
                            origin[0] + slot.coordinate[0],
                            origin[1] + slot.coordinate[1],
                        )
                    )
            if submodule.grid_shape is not None:
                rows, columns = submodule.grid_shape
                extent.update(
                    {
                        origin,
                        (origin[0] + columns - 1, origin[1]),
                        (origin[0], origin[1] + rows - 1),
                        (origin[0] + columns - 1, origin[1] + rows - 1),
                    }
                )
    return occupied, extent


def _sc_routing_fabric(
    node: Node,
) -> tuple[
    dict[str, tuple[int, int]],
    tuple[tuple[str, str], ...],
    set[tuple[int, int]],
    dict[str, int],
]:
    occupied, extent_anchors = _node_extent(node)
    extent = occupied | extent_anchors
    if not extent:
        raise LogicalCompilerValidationError(
            "Superconducting compilation needs spatial logical slots",
            details={"node": node.id},
        )
    x_min = min(x for x, _ in extent) - 1
    x_max = max(x for x, _ in extent) + 1
    y_min = min(y for _, y in extent) - 1
    y_max = max(y for _, y in extent) + 1
    canvas_sites = (x_max - x_min + 1) * (y_max - y_min + 1)
    if canvas_sites > MAX_DENSE_ROUTING_SITES:
        raise LogicalCompilerValidationError(
            "Superconducting logical canvas exceeds the dense compiler limit",
            details={
                "node": node.id,
                "canvas_sites": canvas_sites,
                "max_canvas_sites": MAX_DENSE_ROUTING_SITES,
                "bounds": {
                    "x_min": x_min,
                    "x_max": x_max,
                    "y_min": y_min,
                    "y_max": y_max,
                },
            },
        )

    routing_coordinates = {
        _coordinate_node_id(x, y): (x, y)
        for y in range(y_min, y_max + 1)
        for x in range(x_min, x_max + 1)
        if (x, y) not in occupied
    }
    coordinate_to_node = {
        coordinate: routing_node
        for routing_node, coordinate in routing_coordinates.items()
    }
    routing_edges: set[tuple[str, str]] = set()
    adjacency = {routing_node: set() for routing_node in routing_coordinates}
    for routing_node, (x, y) in routing_coordinates.items():
        for neighbor_coordinate in ((x + 1, y), (x, y + 1)):
            neighbor = coordinate_to_node.get(neighbor_coordinate)
            if neighbor is None:
                continue
            edge = tuple(sorted((routing_node, neighbor)))
            routing_edges.add(edge)
            adjacency[routing_node].add(neighbor)
            adjacency[neighbor].add(routing_node)

    first = next(iter(routing_coordinates), None)
    visited: set[str] = set()
    pending = [first] if first is not None else []
    while pending:
        routing_node = pending.pop()
        if routing_node in visited:
            continue
        visited.add(routing_node)
        pending.extend(adjacency[routing_node] - visited)
    if len(visited) != len(routing_coordinates):
        raise LogicalCompilerValidationError(
            "Canonical occupied slots disconnect the derived SC routing fabric",
            details={
                "node": node.id,
                "routing_nodes": len(routing_coordinates),
                "connected_nodes": len(visited),
            },
        )
    return (
        routing_coordinates,
        tuple(sorted(routing_edges)),
        occupied,
        {
            "x_min": x_min,
            "x_max": x_max,
            "y_min": y_min,
            "y_max": y_max,
        },
    )


def materialize_compute_layout(
    specification: ArchitectureSpecification,
) -> LogicalLayout:
    """Derive the compiler's one compute layout from canonical facts."""

    owned_compute = single_node_module(specification, "compute")
    assert owned_compute is not None
    node, module = owned_compute
    compute = single_submodule(
        module,
        submodule_type="region",
        payload="logical_qubit",
    )
    magic_input = single_submodule(
        module,
        submodule_type="buffer",
        payload="magic_state",
    )
    assert compute is not None and magic_input is not None
    if compute.capacity <= 0:
        raise LogicalCompilerValidationError(
            "A compute Module needs positive logical-qubit capacity",
            details={"module": module.id, "capacity": compute.capacity},
        )

    data = submodule_slot_coordinates(node, module, compute)
    magic = submodule_slot_coordinates(node, module, magic_input)
    if node.modality == "neutral_atom":
        slots = tuple(
            [
                LayoutSlot(
                    id=slot_id,
                    kind="data",
                    coordinate=coordinate,
                    zone="compute",
                )
                for slot_id, coordinate in data
            ]
            + [
                LayoutSlot(
                    id=slot_id,
                    kind="magic_state",
                    coordinate=coordinate,
                    zone="magic_state_buffer",
                )
                for slot_id, coordinate in magic
            ]
        )
        coordinates = [slot.coordinate for slot in slots]
        return LogicalLayout(
            module_id=module.id,
            node_id=node.id,
            modality=node.modality,
            layout_type="zoned_grid",
            slots=slots,
            metadata={
                "bounds": {
                    "x_min": min(point[0] for point in coordinates),
                    "x_max": max(point[0] for point in coordinates),
                    "y_min": min(point[1] for point in coordinates),
                    "y_max": max(point[1] for point in coordinates),
                },
                "magic_state_placement": "architecture_resolved",
                "slot_addressing": "absolute",
            },
        )

    if node.modality == "superconducting":
        (
            routing_coordinates,
            routing_edges,
            occupied,
            bounds,
        ) = _sc_routing_fabric(node)
        slots = tuple(
            [
                LayoutSlot(
                    id=slot_id,
                    kind="data",
                    coordinate=coordinate,
                    interfaces=_adjacent_interfaces(coordinate, routing_coordinates),
                    zone="compute",
                )
                for slot_id, coordinate in data
            ]
            + [
                LayoutSlot(
                    id=slot_id,
                    kind="magic_state",
                    coordinate=coordinate,
                    interfaces=_adjacent_interfaces(coordinate, routing_coordinates),
                    zone="magic_state_buffer",
                )
                for slot_id, coordinate in magic
            ]
        )
        missing_interfaces = [slot.id for slot in slots if not slot.interfaces]
        if missing_interfaces:
            raise LogicalCompilerValidationError(
                "Canonical SC terminal has no adjacent routing site",
                details={"node": node.id, "slots": missing_interfaces},
            )
        compiler_slot_coordinates = {
            tuple(int(value) for value in slot.coordinate) for slot in slots
        }
        return LogicalLayout(
            module_id=module.id,
            node_id=node.id,
            modality=node.modality,
            layout_type="checkerboard",
            slots=slots,
            routing_nodes=tuple(routing_coordinates),
            routing_edges=routing_edges,
            metadata={
                "bounds": bounds,
                "magic_state_placement": "architecture_resolved",
                "routing_derivation": "occupied_slot_complement",
                "routing_coordinates": {
                    routing_node: list(coordinate)
                    for routing_node, coordinate in routing_coordinates.items()
                },
                "routing_occupied_points": [
                    list(point)
                    for point in sorted(occupied - compiler_slot_coordinates)
                ],
                "slot_addressing": "absolute",
            },
        )

    raise LogicalCompilerValidationError(
        "No logical-layout compiler exists for the compute modality",
        details={"module": module.id, "modality": node.modality},
    )


__all__ = [
    "absolute_module_id",
    "absolute_slot_id",
    "absolute_submodule_id",
    "iter_absolute_slots",
    "materialize_compute_layout",
    "node_modules_by_type",
    "primary_qec_binding",
    "primary_qec_submodule",
    "single_node_module",
    "single_submodule",
    "slot_coordinates",
    "submodule_slot_coordinates",
]
