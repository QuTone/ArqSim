"""Stable contracts for architecture-aware logical compilation."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Mapping, Sequence

from heteqsys.schema import canonical_json, deep_freeze_json, strict_json
from heteqsys.schema import normalize_json as normalize_json_value

from .errors import LogicalCompilerValidationError


COMPILER_SPEC_SCHEMA_VERSION = "heteqsys.logical-compiler-spec.v1"
ISSUE_POLICY = "conservative_layers"
_MODEL_FIELDS = frozenset(
    {
        "duration",
        "error_rate",
        "fidelity",
        "latency",
        "logical_error_rate",
        "noise_model",
        "p_success",
        "success_probability",
    }
)


def semantic_hash(data: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(data).encode("ascii")).hexdigest()




def _reject_model_values(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        forbidden = sorted(set(str(key) for key in value) & _MODEL_FIELDS)
        if forbidden:
            raise LogicalCompilerValidationError(
                "Logical compiler artifacts cannot contain performance-model values",
                details={"path": path, "fields": forbidden},
            )
        for key, item in value.items():
            _reject_model_values(item, path=f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_model_values(item, path=f"{path}[{index}]")


def _coordinate(value: Sequence[float | int]) -> tuple[float, ...]:
    coordinate = tuple(float(item) for item in value)
    if not coordinate or any(not math.isfinite(item) for item in coordinate):
        raise LogicalCompilerValidationError(
            "Layout coordinates must contain finite numbers",
            details={"coordinate": list(value)},
        )
    return coordinate


@dataclass(frozen=True)
class BackendSpec:
    backend: str
    options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.backend, str):
            raise TypeError("Compiler backend must be a string")
        if not isinstance(self.options, Mapping):
            raise TypeError("Compiler backend options must be a mapping")
        backend = self.backend.strip().lower()
        if not backend:
            raise LogicalCompilerValidationError("Compiler backend name cannot be empty")
        options = strict_json(self.options, label=f"backend {backend} options")
        _reject_model_values(options, path=f"backend.{backend}.options")
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "options", deep_freeze_json(options))

    def to_dict(self) -> dict[str, Any]:
        return {"backend": self.backend, "options": normalize_json_value(self.options)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BackendSpec":
        unknown = set(data) - {"backend", "options"}
        if unknown:
            raise LogicalCompilerValidationError(
                "Unknown compiler-backend fields",
                details={"fields": sorted(unknown)},
            )
        return cls(backend=data["backend"], options=data.get("options", {}))


@dataclass(frozen=True)
class LogicalCompilerSpec:
    mapping: BackendSpec
    routing: BackendSpec
    instruction_set: str = "heteqsys.hqisa.v1"
    issue_policy: str = ISSUE_POLICY

    def __post_init__(self) -> None:
        if not isinstance(self.mapping, BackendSpec) or not isinstance(
            self.routing, BackendSpec
        ):
            raise TypeError("mapping and routing must be BackendSpec records")
        if not isinstance(self.instruction_set, str) or not isinstance(
            self.issue_policy, str
        ):
            raise TypeError("Compiler instruction_set and issue_policy must be strings")
        if self.instruction_set != "heteqsys.hqisa.v1":
            raise LogicalCompilerValidationError(
                "Unsupported logical instruction set",
                details={"instruction_set": self.instruction_set},
            )
        if self.issue_policy != ISSUE_POLICY:
            raise LogicalCompilerValidationError(
                "Only strict layer-by-layer issue is supported in v1",
                details={"issue_policy": self.issue_policy, "supported": ISSUE_POLICY},
            )

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": COMPILER_SPEC_SCHEMA_VERSION,
            "instruction_set": self.instruction_set,
            "issue_policy": self.issue_policy,
            "mapping": self.mapping.to_dict(),
            "routing": self.routing.to_dict(),
        }

    @cached_property
    def compiler_hash(self) -> str:
        return semantic_hash(self.semantic_dict())

    def to_dict(self) -> dict[str, Any]:
        result = self.semantic_dict()
        result["compiler_hash"] = self.compiler_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogicalCompilerSpec":
        allowed = {
            "schema_version",
            "instruction_set",
            "issue_policy",
            "mapping",
            "routing",
            "compiler_hash",
        }
        unknown = set(data) - allowed
        if unknown:
            raise LogicalCompilerValidationError(
                "Unknown logical compiler-spec fields",
                details={"fields": sorted(unknown)},
            )
        if data.get("schema_version") != COMPILER_SPEC_SCHEMA_VERSION:
            raise LogicalCompilerValidationError(
                "Unsupported logical compiler-spec schema",
                details={"schema_version": data.get("schema_version")},
            )
        result = cls(
            instruction_set=data.get("instruction_set", "heteqsys.hqisa.v1"),
            issue_policy=data.get("issue_policy", ISSUE_POLICY),
            mapping=BackendSpec.from_dict(data["mapping"]),
            routing=BackendSpec.from_dict(data["routing"]),
        )
        expected_hash = data.get("compiler_hash")
        if expected_hash is not None and expected_hash != result.compiler_hash:
            raise LogicalCompilerValidationError(
                "Logical compiler-spec hash does not match its content",
                details={"expected": expected_hash, "actual": result.compiler_hash},
            )
        return result


@dataclass(frozen=True)
class LayoutSlot:
    id: str
    kind: str
    coordinate: tuple[float, ...]
    interfaces: tuple[str, ...] = field(default_factory=tuple)
    zone: str | None = None

    def __post_init__(self) -> None:
        if not self.id or not self.kind:
            raise LogicalCompilerValidationError("Layout slot IDs and kinds cannot be empty")
        object.__setattr__(self, "coordinate", _coordinate(self.coordinate))
        object.__setattr__(self, "interfaces", tuple(sorted(set(self.interfaces))))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "coordinate": list(self.coordinate),
            "interfaces": list(self.interfaces),
        }
        if self.zone is not None:
            result["zone"] = self.zone
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LayoutSlot":
        return cls(
            id=str(data["id"]),
            kind=str(data["kind"]),
            coordinate=tuple(data["coordinate"]),
            interfaces=tuple(str(item) for item in data.get("interfaces", ())),
            zone=str(data["zone"]) if data.get("zone") is not None else None,
        )


@dataclass(frozen=True)
class LogicalLayout:
    module_id: str
    node_id: str
    modality: str
    layout_type: str
    slots: tuple[LayoutSlot, ...]
    routing_nodes: tuple[str, ...] = field(default_factory=tuple)
    routing_edges: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        slots = tuple(sorted(self.slots, key=lambda slot: slot.id))
        slot_ids = [slot.id for slot in slots]
        routing_nodes = tuple(sorted(set(self.routing_nodes)))
        all_ids = slot_ids + list(routing_nodes)
        duplicates = sorted({item for item in all_ids if all_ids.count(item) > 1})
        if duplicates:
            raise LogicalCompilerValidationError(
                "Layout slot and routing-node IDs must be unique",
                details={"module": self.module_id, "ids": duplicates},
            )
        node_set = set(routing_nodes)
        edges = tuple(
            sorted(
                {
                    tuple(sorted((str(left), str(right))))
                    for left, right in self.routing_edges
                    if left != right
                }
            )
        )
        bad_edges = [list(edge) for edge in edges if not set(edge) <= node_set]
        bad_interfaces = sorted(
            {interface for slot in slots for interface in slot.interfaces if interface not in node_set}
        )
        if bad_edges or bad_interfaces:
            raise LogicalCompilerValidationError(
                "Layout graph references unknown routing nodes",
                details={
                    "module": self.module_id,
                    "edges": bad_edges,
                    "interfaces": bad_interfaces,
                },
            )
        metadata = normalize_json_value(self.metadata)
        _reject_model_values(metadata, path=f"layouts.{self.module_id}.metadata")
        object.__setattr__(self, "slots", slots)
        object.__setattr__(self, "routing_nodes", routing_nodes)
        object.__setattr__(self, "routing_edges", edges)
        object.__setattr__(self, "metadata", metadata)

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "module": self.module_id,
            "node": self.node_id,
            "modality": self.modality,
            "type": self.layout_type,
            "slots": [slot.to_dict() for slot in self.slots],
            "routing_nodes": list(self.routing_nodes),
            "routing_edges": [list(edge) for edge in self.routing_edges],
            "metadata": normalize_json_value(self.metadata),
        }

    @cached_property
    def layout_hash(self) -> str:
        return semantic_hash(self.semantic_dict())

    def slots_of_kind(self, kind: str) -> tuple[LayoutSlot, ...]:
        return tuple(slot for slot in self.slots if slot.kind == kind)

    def slot(self, slot_id: str) -> LayoutSlot:
        for slot in self.slots:
            if slot.id == slot_id:
                return slot
        raise LogicalCompilerValidationError(
            "Layout does not contain the requested slot",
            details={"module": self.module_id, "slot": slot_id},
        )

    def to_dict(self) -> dict[str, Any]:
        result = self.semantic_dict()
        result["layout_hash"] = self.layout_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogicalLayout":
        result = cls(
            module_id=str(data["module"]),
            node_id=str(data["node"]),
            modality=str(data["modality"]),
            layout_type=str(data["type"]),
            slots=tuple(LayoutSlot.from_dict(item) for item in data.get("slots", ())),
            routing_nodes=tuple(str(item) for item in data.get("routing_nodes", ())),
            routing_edges=tuple(tuple(str(value) for value in edge) for edge in data.get("routing_edges", ())),
            metadata=data.get("metadata", {}),
        )
        expected_hash = data.get("layout_hash")
        if expected_hash is not None and expected_hash != result.layout_hash:
            raise LogicalCompilerValidationError(
                "Logical layout hash does not match its content",
                details={"expected": expected_hash, "actual": result.layout_hash},
            )
        return result


@dataclass(frozen=True, order=True)
class PlacementEntry:
    logical_qubit: int
    node_id: str
    module_id: str
    slot_id: str
    coordinate: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.logical_qubit < 0:
            raise LogicalCompilerValidationError("Logical-qubit IDs cannot be negative")
        object.__setattr__(self, "coordinate", _coordinate(self.coordinate))

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_qubit": self.logical_qubit,
            "node": self.node_id,
            "module": self.module_id,
            "slot": self.slot_id,
            "coordinate": list(self.coordinate),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PlacementEntry":
        return cls(
            logical_qubit=int(data["logical_qubit"]),
            node_id=str(data["node"]),
            module_id=str(data["module"]),
            slot_id=str(data["slot"]),
            coordinate=tuple(data["coordinate"]),
        )


@dataclass(frozen=True)
class LogicalPlacement:
    backend: str
    backend_version: str
    effective_options: Mapping[str, Any]
    layout_hash: str
    entries: tuple[PlacementEntry, ...]
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        entries = tuple(sorted(self.entries))
        qubits = [entry.logical_qubit for entry in entries]
        slots = [(entry.module_id, entry.slot_id) for entry in entries]
        if len(set(qubits)) != len(qubits) or len(set(slots)) != len(slots):
            raise LogicalCompilerValidationError(
                "Logical placement must be one-to-one",
                details={"qubits": qubits, "slots": [list(slot) for slot in slots]},
            )
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "effective_options", normalize_json_value(self.effective_options))
        object.__setattr__(self, "metrics", normalize_json_value(self.metrics))

    def entry(self, logical_qubit: int) -> PlacementEntry:
        for entry in self.entries:
            if entry.logical_qubit == logical_qubit:
                return entry
        raise LogicalCompilerValidationError(
            "Placement does not contain logical qubit",
            details={"logical_qubit": logical_qubit},
        )

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "backend_version": self.backend_version,
            "effective_options": normalize_json_value(self.effective_options),
            "layout_hash": self.layout_hash,
            "entries": [entry.to_dict() for entry in self.entries],
            "metrics": normalize_json_value(self.metrics),
        }

    @cached_property
    def placement_hash(self) -> str:
        return semantic_hash(self.semantic_dict())

    def to_dict(self) -> dict[str, Any]:
        result = self.semantic_dict()
        result["placement_hash"] = self.placement_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogicalPlacement":
        result = cls(
            backend=str(data["backend"]),
            backend_version=str(data["backend_version"]),
            effective_options=data.get("effective_options", {}),
            layout_hash=str(data["layout_hash"]),
            entries=tuple(PlacementEntry.from_dict(item) for item in data.get("entries", ())),
            metrics=data.get("metrics", {}),
        )
        expected_hash = data.get("placement_hash")
        if expected_hash is not None and expected_hash != result.placement_hash:
            raise LogicalCompilerValidationError(
                "Logical placement hash does not match its content",
                details={"expected": expected_hash, "actual": result.placement_hash},
            )
        return result


@dataclass(frozen=True)
class Movement:
    logical_qubit: int
    source: tuple[float, ...]
    destination: tuple[float, ...]
    batch: int
    aod_group: int = 0
    entity_kind: str = "logical_qubit"
    source_slot_id: str | None = None
    target_logical_qubit: int | None = None
    phase: str = "approach"
    task_id: str | None = None
    start_time_us: float | None = None
    end_time_us: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _coordinate(self.source))
        object.__setattr__(self, "destination", _coordinate(self.destination))
        if self.entity_kind not in {"logical_qubit", "magic_state"}:
            raise LogicalCompilerValidationError(
                "Movement entity_kind is not supported",
                details={"entity_kind": self.entity_kind},
            )
        if self.phase not in {"approach", "delivery", "return"}:
            raise LogicalCompilerValidationError(
                "Movement phase is not supported",
                details={"phase": self.phase},
            )
        if self.entity_kind == "magic_state":
            if self.source_slot_id is None or self.target_logical_qubit is None:
                raise LogicalCompilerValidationError(
                    "Magic-state movement requires a source slot and target logical qubit"
                )
            if self.logical_qubit != self.target_logical_qubit:
                raise LogicalCompilerValidationError(
                    "Magic-state movement logical_qubit must identify its injection target"
                )
            if self.phase != "delivery":
                raise LogicalCompilerValidationError(
                    "Magic-state movement must use the delivery phase"
                )
        elif self.phase == "delivery":
            raise LogicalCompilerValidationError(
                "Only magic-state movements can use the delivery phase"
            )
        if (self.start_time_us is None) != (self.end_time_us is None):
            raise LogicalCompilerValidationError(
                "Movement start and end timestamps must be provided together"
            )
        if self.start_time_us is not None:
            start = float(self.start_time_us)
            end = float(self.end_time_us)
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
                raise LogicalCompilerValidationError(
                    "Movement timestamps must be finite, non-negative, and ordered",
                    details={"start_time_us": start, "end_time_us": end},
                )
            object.__setattr__(self, "start_time_us", start)
            object.__setattr__(self, "end_time_us", end)

    @property
    def manhattan_distance(self) -> float:
        return sum(abs(left - right) for left, right in zip(self.source, self.destination))

    def to_dict(self) -> dict[str, Any]:
        result = {
            "logical_qubit": self.logical_qubit,
            "source": list(self.source),
            "destination": list(self.destination),
            "batch": self.batch,
            "aod_group": self.aod_group,
            "entity_kind": self.entity_kind,
            "phase": self.phase,
        }
        if self.source_slot_id is not None:
            result["source_slot_id"] = self.source_slot_id
        if self.target_logical_qubit is not None:
            result["target_logical_qubit"] = self.target_logical_qubit
        if self.task_id is not None:
            result["task_id"] = self.task_id
        if self.start_time_us is not None:
            result["start_time_us"] = self.start_time_us
            result["end_time_us"] = self.end_time_us
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Movement":
        return cls(
            logical_qubit=int(data["logical_qubit"]),
            source=tuple(data["source"]),
            destination=tuple(data["destination"]),
            batch=int(data["batch"]),
            aod_group=int(data.get("aod_group", 0)),
            entity_kind=str(data.get("entity_kind", "logical_qubit")),
            source_slot_id=(
                str(data["source_slot_id"])
                if data.get("source_slot_id") is not None
                else None
            ),
            target_logical_qubit=(
                int(data["target_logical_qubit"])
                if data.get("target_logical_qubit") is not None
                else None
            ),
            phase=str(
                data.get(
                    "phase",
                    "delivery"
                    if data.get("entity_kind") == "magic_state"
                    else "approach",
                )
            ),
            task_id=(str(data["task_id"]) if data.get("task_id") is not None else None),
            start_time_us=(
                float(data["start_time_us"])
                if data.get("start_time_us") is not None
                else None
            ),
            end_time_us=(
                float(data["end_time_us"])
                if data.get("end_time_us") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class RouteStep:
    id: int
    layer_index: int
    operation_indices: tuple[int, ...]
    kind: str
    module_ids: tuple[str, ...]
    terminal_slots: tuple[str, ...] = field(default_factory=tuple)
    path_nodes: tuple[str, ...] = field(default_factory=tuple)
    path_edges: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    movements: tuple[Movement, ...] = field(default_factory=tuple)
    group: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        metadata = normalize_json_value(self.metadata)
        _reject_model_values(metadata, path=f"route_steps.{self.id}.metadata")
        object.__setattr__(self, "operation_indices", tuple(sorted(set(self.operation_indices))))
        object.__setattr__(self, "module_ids", tuple(sorted(set(self.module_ids))))
        object.__setattr__(self, "terminal_slots", tuple(self.terminal_slots))
        path_nodes = tuple(self.path_nodes)
        path_edges = tuple(
            sorted(
                {
                    tuple(sorted((str(left), str(right))))
                    for left, right in self.path_edges
                    if left != right
                }
            )
        )
        bad_edges = [list(edge) for edge in path_edges if not set(edge) <= set(path_nodes)]
        if bad_edges:
            raise LogicalCompilerValidationError(
                "Route-step edges must reference nodes in the same route step",
                details={"route_step": self.id, "edges": bad_edges},
            )
        object.__setattr__(self, "path_nodes", path_nodes)
        object.__setattr__(self, "path_edges", path_edges)
        object.__setattr__(self, "movements", tuple(self.movements))
        object.__setattr__(self, "metadata", metadata)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "layer": self.layer_index,
            "operations": list(self.operation_indices),
            "kind": self.kind,
            "modules": list(self.module_ids),
            "terminal_slots": list(self.terminal_slots),
            "path_nodes": list(self.path_nodes),
            "movements": [movement.to_dict() for movement in self.movements],
            "group": self.group,
            "metadata": normalize_json_value(self.metadata),
        }
        if self.path_edges:
            result["path_edges"] = [list(edge) for edge in self.path_edges]
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RouteStep":
        return cls(
            id=int(data["id"]),
            layer_index=int(data["layer"]),
            operation_indices=tuple(int(item) for item in data.get("operations", ())),
            kind=str(data["kind"]),
            module_ids=tuple(str(item) for item in data.get("modules", ())),
            terminal_slots=tuple(str(item) for item in data.get("terminal_slots", ())),
            path_nodes=tuple(str(item) for item in data.get("path_nodes", ())),
            path_edges=tuple(
                tuple(str(value) for value in edge)
                for edge in data.get("path_edges", ())
            ),
            movements=tuple(Movement.from_dict(item) for item in data.get("movements", ())),
            group=int(data.get("group", 0)),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class LogicalRoutePlan:
    backend: str
    backend_version: str
    effective_options: Mapping[str, Any]
    layout_hashes: Mapping[str, str]
    initial_placement: LogicalPlacement
    final_placement: LogicalPlacement
    steps: tuple[RouteStep, ...]
    metrics: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        steps = tuple(sorted(self.steps, key=lambda step: step.id))
        if [step.id for step in steps] != list(range(len(steps))):
            raise LogicalCompilerValidationError("Route-step IDs must be contiguous and zero-based")
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "effective_options", normalize_json_value(self.effective_options))
        object.__setattr__(self, "layout_hashes", normalize_json_value(self.layout_hashes))
        object.__setattr__(self, "metrics", normalize_json_value(self.metrics))

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "backend_version": self.backend_version,
            "effective_options": normalize_json_value(self.effective_options),
            "layout_hashes": normalize_json_value(self.layout_hashes),
            "initial_placement": self.initial_placement.to_dict(),
            "final_placement": self.final_placement.to_dict(),
            "steps": [step.to_dict() for step in self.steps],
            "metrics": normalize_json_value(self.metrics),
        }

    @cached_property
    def route_hash(self) -> str:
        return semantic_hash(self.semantic_dict())

    def to_dict(self) -> dict[str, Any]:
        result = self.semantic_dict()
        result["route_hash"] = self.route_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogicalRoutePlan":
        result = cls(
            backend=str(data["backend"]),
            backend_version=str(data["backend_version"]),
            effective_options=data.get("effective_options", {}),
            layout_hashes=data.get("layout_hashes", {}),
            initial_placement=LogicalPlacement.from_dict(data["initial_placement"]),
            final_placement=LogicalPlacement.from_dict(data["final_placement"]),
            steps=tuple(RouteStep.from_dict(item) for item in data.get("steps", ())),
            metrics=data.get("metrics", {}),
        )
        expected_hash = data.get("route_hash")
        if expected_hash is not None and expected_hash != result.route_hash:
            raise LogicalCompilerValidationError(
                "Logical route-plan hash does not match its content",
                details={"expected": expected_hash, "actual": result.route_hash},
            )
        return result
