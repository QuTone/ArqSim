"""Immutable static architecture specification.

The records in this module contain only final static facts about one
logical/evaluation architecture.  A resolver may use circuit statistics to
choose capacities, but the resulting graph is not bound to that circuit and
does not retain the statistics or sizing policy.  Resolution provenance,
runtime state, compiler routes, and physical placement belong elsewhere.

Coordinates are hierarchical: a slot coordinate is local to its Submodule and
``logical_origin`` translates that Submodule onto its owner's logical canvas.
Nodes and Interconnects are both resource owners.  A missing coordinate means
that the slot has identity but no spatial meaning at this abstraction level.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from heteqsys.architecture.errors import ArchitectureValidationError
from heteqsys.architecture.identifiers import require_local_id
from heteqsys.schema import semantic_hash


ARCHITECTURE_SPECIFICATION_SCHEMA_VERSION = (
    "arqsim.architecture-specification.v3"
)

JsonScalar = None | bool | int | float | str
JsonValue = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


def _plain_int(value: Any, *, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where} must be a plain integer")
    return value


def _nonempty(value: Any, *, where: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{where} must be a non-empty trimmed string")
    return value


def _sha256(value: Any, *, where: str) -> str:
    result = _nonempty(value, where=where)
    if len(result) != 64 or any(
        character not in "0123456789abcdef" for character in result
    ):
        raise ValueError(f"{where} must be a lowercase SHA-256 hex digest")
    return result


def _coordinate(value: Any, *, where: str) -> tuple[int, int]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{where} must contain two signed plain integers")
    if len(value) != 2:
        raise ValueError(f"{where} must contain two signed plain integers")
    return (
        _plain_int(value[0], where=f"{where}[0]"),
        _plain_int(value[1], where=f"{where}[1]"),
    )


def _freeze_json(value: Any, *, where: str) -> JsonValue:
    """Validate and recursively freeze a JSON value without coercion."""

    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{where} must not contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        keys = tuple(value)
        if any(not isinstance(key, str) for key in keys):
            raise ValueError(f"{where} keys must be strings")
        return MappingProxyType(
            {
                key: _freeze_json(value[key], where=f"{where}.{key}")
                for key in sorted(keys)
            }
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(
            _freeze_json(item, where=f"{where}[{offset}]")
            for offset, item in enumerate(value)
        )
    raise ValueError(f"{where} contains a non-JSON value: {type(value).__name__}")


def _json_value(value: JsonValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


def _record(
    value: Any,
    *,
    where: str,
    required: set[str],
    optional: set[str] = frozenset(),
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where} must be a mapping")
    keys = set(value)
    if any(not isinstance(key, str) for key in keys):
        raise ValueError(f"{where} keys must be strings")
    missing = sorted(required - keys)
    unknown = sorted(keys - required - optional)
    if missing:
        raise ValueError(f"Missing fields in {where}: {missing}")
    if unknown:
        raise ValueError(f"Unknown fields in {where}: {unknown}")
    return value


def _records(value: Any, *, where: str) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{where} must be a sequence")
    result: list[Mapping[str, Any]] = []
    for offset, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ValueError(f"{where}[{offset}] must be a mapping")
        result.append(item)
    return tuple(result)


def _local_ref(value: Any, *, where: str) -> str:
    result = _nonempty(value, where=where)
    parts = result.split("/")
    if len(parts) != 2:
        raise ValueError(f"{where} must be Module/Submodule: {result}")
    for offset, part in enumerate(parts):
        require_local_id(part, where=f"{where} component {offset}")
    return result


def _node_ref(value: Any, *, where: str) -> str:
    result = _nonempty(value, where=where)
    parts = result.split("/")
    if len(parts) != 3:
        raise ValueError(f"{where} must be Node/Module/Submodule: {result}")
    for offset, part in enumerate(parts):
        require_local_id(part, where=f"{where} component {offset}")
    return result


@dataclass(frozen=True, slots=True)
class LogicalSlot:
    id: str
    coordinate: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "id", require_local_id(self.id, where="LogicalSlot ID")
        )
        if self.coordinate is not None:
            object.__setattr__(
                self,
                "coordinate",
                _coordinate(self.coordinate, where=f"slot {self.id} coordinate"),
            )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id}
        if self.coordinate is not None:
            result["coordinate"] = list(self.coordinate)
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogicalSlot":
        item = _record(
            data, where="logical slot", required={"id"}, optional={"coordinate"}
        )
        return cls(id=item["id"], coordinate=item.get("coordinate"))


@dataclass(frozen=True, slots=True)
class QECBinding:
    code: str
    parameters: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _nonempty(self.code, where="QEC code"))
        frozen = _freeze_json(self.parameters, where=f"QEC {self.code} parameters")
        if not isinstance(frozen, Mapping):
            raise ValueError("QEC parameters must be a mapping")
        object.__setattr__(self, "parameters", frozen)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "parameters": _json_value(self.parameters)}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QECBinding":
        item = _record(
            data, where="QEC binding", required={"code", "parameters"}
        )
        return cls(code=item["code"], parameters=item["parameters"])


@dataclass(frozen=True, slots=True)
class QECResourceProtocolRef:
    """Content-pinned reference to one QEC resource-protocol catalog profile."""

    id: str
    profile_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "id",
            _nonempty(self.id, where="QEC resource protocol ID"),
        )
        object.__setattr__(
            self,
            "profile_hash",
            _sha256(self.profile_hash, where="QEC resource protocol profile hash"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "profile_hash": self.profile_hash}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QECResourceProtocolRef":
        item = _record(
            data,
            where="QEC resource protocol reference",
            required={"id", "profile_hash"},
        )
        return cls(id=item["id"], profile_hash=item["profile_hash"])


@dataclass(frozen=True, slots=True)
class Submodule:
    id: str
    type: str
    payload: str
    capacity: int
    qec: QECBinding | None = None
    slots: tuple[LogicalSlot, ...] = ()
    logical_origin: tuple[int, int] | None = None
    grid_shape: tuple[int, int] | None = None
    resource_protocol: QECResourceProtocolRef | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_local_id(self.id, where="Submodule ID"))
        object.__setattr__(self, "type", _nonempty(self.type, where="Submodule type"))
        object.__setattr__(
            self, "payload", _nonempty(self.payload, where="Submodule payload")
        )
        capacity = _plain_int(self.capacity, where=f"Submodule {self.id} capacity")
        if self.type == "engine" and capacity <= 0:
            raise ValueError(
                f"Engine Submodule {self.id} capacity must be positive"
            )
        if self.type != "engine" and capacity < 0:
            raise ValueError(
                f"Submodule {self.id} capacity must be non-negative"
            )
        object.__setattr__(self, "capacity", capacity)

        slots = tuple(self.slots)
        if any(not isinstance(item, LogicalSlot) for item in slots):
            raise TypeError(f"Submodule {self.id} slots must contain LogicalSlot values")
        slot_ids = [item.id for item in slots]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError(f"Submodule {self.id} has duplicate slot IDs")
        if self.type == "engine" and slots:
            raise ValueError(f"Engine Submodule {self.id} must not expose slots")
        if self.type != "engine" and len(slots) != capacity:
            raise ValueError(
                f"Submodule {self.id} must expose exactly capacity={capacity} slots; "
                f"found {len(slots)}"
            )
        if capacity == 0 and (
            self.logical_origin is not None or self.grid_shape is not None
        ):
            raise ValueError(
                f"Zero-capacity Submodule {self.id} must not have logical geometry"
            )
        coordinates = [slot.coordinate for slot in slots if slot.coordinate is not None]
        if len(coordinates) != len(set(coordinates)):
            raise ValueError(f"Submodule {self.id} has duplicate slot coordinates")
        object.__setattr__(self, "slots", slots)

        if self.qec is not None and not isinstance(self.qec, QECBinding):
            raise TypeError(f"Submodule {self.id} qec must be a QECBinding")
        if self.resource_protocol is not None and not isinstance(
            self.resource_protocol, QECResourceProtocolRef
        ):
            raise TypeError(
                f"Submodule {self.id} resource_protocol must be a "
                "QECResourceProtocolRef"
            )

        if self.logical_origin is not None:
            if self.type == "engine":
                raise ValueError(
                    f"Engine Submodule {self.id} must not have logical geometry"
                )
            object.__setattr__(
                self,
                "logical_origin",
                _coordinate(
                    self.logical_origin, where=f"Submodule {self.id} logical origin"
                ),
            )
        if self.grid_shape is not None:
            if self.type == "engine":
                raise ValueError(
                    f"Engine Submodule {self.id} must not have logical geometry"
                )
            shape = tuple(self.grid_shape)
            if len(shape) != 2 or any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
                for value in shape
            ):
                raise ValueError(
                    f"Submodule {self.id} grid_shape must be positive (rows, columns)"
                )
            if shape[0] * shape[1] < len(slots):
                raise ValueError(f"Submodule {self.id} grid_shape cannot contain its slots")
            if any(slot.coordinate is None for slot in slots):
                raise ValueError(f"Submodule {self.id} grid_shape requires spatial slots")
            outside = [
                slot.id
                for slot in slots
                if slot.coordinate is not None
                and not (
                    0 <= slot.coordinate[0] < shape[1]
                    and 0 <= slot.coordinate[1] < shape[0]
                )
            ]
            if outside:
                raise ValueError(
                    f"Submodule {self.id} slots lie outside grid_shape: {outside}"
                )
            object.__setattr__(self, "grid_shape", shape)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "payload": self.payload,
            "capacity": self.capacity,
        }
        if self.type != "engine":
            result["slots"] = [slot.to_dict() for slot in self.slots]
        if self.qec is not None:
            result["qec"] = self.qec.to_dict()
        if self.logical_origin is not None:
            result["logical_origin"] = list(self.logical_origin)
        if self.grid_shape is not None:
            result["grid_shape"] = {
                "rows": self.grid_shape[0],
                "columns": self.grid_shape[1],
            }
        if self.resource_protocol is not None:
            result["resource_protocol"] = self.resource_protocol.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Submodule":
        item = _record(
            data,
            where="Submodule",
            required={"id", "type", "payload", "capacity"},
            optional={"slots", "qec", "logical_origin", "grid_shape", "resource_protocol"},
        )
        grid_shape = None
        if "grid_shape" in item:
            shape = _record(
                item["grid_shape"],
                where="logical grid shape",
                required={"rows", "columns"},
            )
            grid_shape = (shape["rows"], shape["columns"])
        return cls(
            id=item["id"],
            type=item["type"],
            payload=item["payload"],
            capacity=item["capacity"],
            qec=QECBinding.from_dict(item["qec"]) if "qec" in item else None,
            slots=tuple(
                LogicalSlot.from_dict(value)
                for value in _records(item.get("slots", ()), where="Submodule slots")
            ),
            logical_origin=item.get("logical_origin"),
            grid_shape=grid_shape,
            resource_protocol=(
                QECResourceProtocolRef.from_dict(item["resource_protocol"])
                if "resource_protocol" in item
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class Module:
    id: str
    type: str
    submodules: tuple[Submodule, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_local_id(self.id, where="Module ID"))
        object.__setattr__(self, "type", _nonempty(self.type, where="Module type"))
        if any(not isinstance(item, Submodule) for item in self.submodules):
            raise TypeError(f"Module {self.id} submodules must be Submodule values")
        submodules = tuple(sorted(self.submodules, key=lambda item: item.id))
        if not submodules:
            raise ValueError(f"Module {self.id} needs at least one Submodule")
        ids = [item.id for item in submodules]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Module {self.id} has duplicate Submodule IDs")
        object.__setattr__(self, "submodules", submodules)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "submodules": [item.to_dict() for item in self.submodules],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Module":
        item = _record(data, where="Module", required={"id", "type", "submodules"})
        return cls(
            id=item["id"],
            type=item["type"],
            submodules=tuple(
                Submodule.from_dict(value)
                for value in _records(item["submodules"], where="Submodules")
            ),
        )


@dataclass(frozen=True, slots=True)
class LocalConnection:
    """Cross-Module state-transfer topology local to one owner.

    Directed endpoints are ordered as ``(source, destination)``.
    """

    id: str
    direction: str
    endpoints: tuple[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_local_id(self.id, where="connection ID"))
        direction = _nonempty(self.direction, where="connection direction")
        if direction not in {"directed", "bidirectional"}:
            raise ValueError(
                f"connection direction must be directed or bidirectional: {direction}"
            )
        object.__setattr__(self, "direction", direction)
        if isinstance(self.endpoints, (str, bytes)) or not isinstance(
            self.endpoints, Sequence
        ):
            raise TypeError(f"Connection {self.id} endpoints must be a sequence")
        endpoints = tuple(
            _local_ref(value, where=f"Connection {self.id} endpoint")
            for value in self.endpoints
        )
        if len(endpoints) != 2 or endpoints[0] == endpoints[1]:
            raise ValueError(f"Connection {self.id} needs two distinct endpoints")
        if endpoints[0].split("/", 1)[0] == endpoints[1].split("/", 1)[0]:
            raise ValueError(
                f"Connection {self.id} must connect two distinct Modules"
            )
        if direction == "bidirectional":
            endpoints = tuple(sorted(endpoints))
        object.__setattr__(self, "endpoints", endpoints)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "direction": self.direction,
            "endpoints": list(self.endpoints),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LocalConnection":
        item = _record(
            data, where="local connection", required={"id", "direction", "endpoints"}
        )
        return cls(
            id=item["id"],
            direction=item["direction"],
            endpoints=item["endpoints"],
        )


def _owner_contents(
    *,
    owner_id: str,
    owner_kind: str,
    modules: Sequence[Module],
    connections: Sequence[LocalConnection],
) -> tuple[tuple[Module, ...], tuple[LocalConnection, ...]]:
    """Normalize and validate resources local to a Node or Interconnect."""

    if any(not isinstance(item, Module) for item in modules):
        raise TypeError(f"{owner_kind} {owner_id} modules must be Module values")
    normalized_modules = tuple(sorted(modules, key=lambda item: item.id))
    module_ids = [item.id for item in normalized_modules]
    if len(module_ids) != len(set(module_ids)):
        raise ValueError(f"{owner_kind} {owner_id} has duplicate Module IDs")

    if any(not isinstance(item, LocalConnection) for item in connections):
        raise TypeError(
            f"{owner_kind} {owner_id} connections must be LocalConnection values"
        )
    normalized_connections = tuple(sorted(connections, key=lambda item: item.id))
    connection_ids = [item.id for item in normalized_connections]
    if len(connection_ids) != len(set(connection_ids)):
        raise ValueError(f"{owner_kind} {owner_id} has duplicate connection IDs")

    submodules = {
        f"{module.id}/{submodule.id}": submodule
        for module in normalized_modules
        for submodule in module.submodules
    }
    for connection in normalized_connections:
        missing = sorted(set(connection.endpoints) - set(submodules))
        if missing:
            raise ValueError(
                f"Connection {connection.id} references unknown Submodules "
                f"in {owner_kind} {owner_id}: {missing}"
            )
        payloads = {submodules[endpoint].payload for endpoint in connection.endpoints}
        if len(payloads) != 1:
            raise ValueError(
                f"Connection {connection.id} endpoints must have the same payload"
            )
    return normalized_modules, normalized_connections


@dataclass(frozen=True, slots=True)
class Node:
    id: str
    modality: str
    modules: tuple[Module, ...]
    connections: tuple[LocalConnection, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_local_id(self.id, where="Node ID"))
        object.__setattr__(self, "modality", _nonempty(self.modality, where="Node modality"))
        modules, connections = _owner_contents(
            owner_id=self.id,
            owner_kind="Node",
            modules=self.modules,
            connections=self.connections,
        )
        if not modules:
            raise ValueError(f"Node {self.id} needs at least one Module")
        object.__setattr__(self, "modules", modules)
        object.__setattr__(self, "connections", connections)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "modality": self.modality,
            "modules": [item.to_dict() for item in self.modules],
            "connections": [item.to_dict() for item in self.connections],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Node":
        item = _record(
            data,
            where="Node",
            required={"id", "modality", "modules", "connections"},
        )
        return cls(
            id=item["id"],
            modality=item["modality"],
            modules=tuple(
                Module.from_dict(value)
                for value in _records(item["modules"], where="Modules")
            ),
            connections=tuple(
                LocalConnection.from_dict(value)
                for value in _records(item["connections"], where="Node connections")
            ),
        )


@dataclass(frozen=True, slots=True)
class Interconnect:
    """A Node-peer domain containing distributed Modules and local topology."""

    id: str
    endpoints: tuple[str, ...]
    modules: tuple[Module, ...] = ()
    connections: tuple[LocalConnection, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "id", require_local_id(self.id, where="Interconnect ID")
        )
        if isinstance(self.endpoints, (str, bytes)) or not isinstance(
            self.endpoints, Sequence
        ):
            raise TypeError(f"Interconnect {self.id} endpoints must be a sequence")
        endpoints = tuple(
            sorted(
                _node_ref(value, where=f"Interconnect {self.id} endpoint")
                for value in self.endpoints
            )
        )
        if len(endpoints) < 2:
            raise ValueError(f"Interconnect {self.id} needs at least two endpoints")
        if len(endpoints) != len(set(endpoints)):
            raise ValueError(f"Interconnect {self.id} needs distinct endpoints")
        object.__setattr__(self, "endpoints", endpoints)

        modules, connections = _owner_contents(
            owner_id=self.id,
            owner_kind="Interconnect",
            modules=self.modules,
            connections=self.connections,
        )
        if not modules:
            raise ValueError(f"Interconnect {self.id} needs at least one Module")
        object.__setattr__(self, "modules", modules)
        object.__setattr__(self, "connections", connections)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "endpoints": list(self.endpoints),
            "modules": [item.to_dict() for item in self.modules],
            "connections": [item.to_dict() for item in self.connections],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Interconnect":
        item = _record(
            data,
            where="Interconnect",
            required={"id", "endpoints", "modules", "connections"},
        )
        return cls(
            id=item["id"],
            endpoints=item["endpoints"],
            modules=tuple(
                Module.from_dict(value)
                for value in _records(item["modules"], where="Interconnect Modules")
            ),
            connections=tuple(
                LocalConnection.from_dict(value)
                for value in _records(
                    item["connections"], where="Interconnect connections"
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class ArchitectureSpecification:
    """One immutable logical architecture graph."""

    nodes: tuple[Node, ...]
    interconnects: tuple[Interconnect, ...] = ()

    def __post_init__(self) -> None:
        if any(not isinstance(item, Node) for item in self.nodes):
            raise TypeError("ArchitectureSpecification nodes must be Node values")
        nodes = tuple(sorted(self.nodes, key=lambda item: item.id))
        if not nodes:
            raise ValueError("ArchitectureSpecification needs at least one Node")
        node_ids = [item.id for item in nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("ArchitectureSpecification has duplicate Node IDs")
        object.__setattr__(self, "nodes", nodes)

        if any(not isinstance(item, Interconnect) for item in self.interconnects):
            raise TypeError(
                "ArchitectureSpecification interconnects must be Interconnect values"
            )
        interconnects = tuple(sorted(self.interconnects, key=lambda item: item.id))
        interconnect_ids = [item.id for item in interconnects]
        if len(interconnect_ids) != len(set(interconnect_ids)):
            raise ValueError("ArchitectureSpecification has duplicate Interconnect IDs")
        if set(node_ids) & set(interconnect_ids):
            raise ValueError(
                "Node and Interconnect owner IDs must be globally unique resource owners"
            )
        object.__setattr__(self, "interconnects", interconnects)

        nodes_by_id = {item.id: item for item in nodes}
        for interconnect in interconnects:
            endpoint_nodes: set[str] = set()
            for endpoint in interconnect.endpoints:
                node_id, module_id, submodule_id = endpoint.split("/")
                node = nodes_by_id.get(node_id)
                if node is None:
                    raise ValueError(
                        f"Interconnect {interconnect.id} endpoint references "
                        f"unknown Node: {endpoint}"
                    )
                self._find_submodule(
                    node, module_id, submodule_id, qualified=endpoint
                )
                endpoint_nodes.add(node_id)
            if len(endpoint_nodes) != 2:
                raise ValueError(
                    f"Interconnect {interconnect.id} endpoints must belong to "
                    "exactly two distinct Nodes"
                )

        for node in nodes:
            self._validate_owner_spatial_ownership(
                owner_id=node.id,
                owner_kind="Node",
                modules=node.modules,
            )
        for interconnect in interconnects:
            self._validate_owner_spatial_ownership(
                owner_id=interconnect.id,
                owner_kind="Interconnect",
                modules=interconnect.modules,
            )

    @staticmethod
    def _find_submodule(
        owner: Node,
        module_id: str,
        submodule_id: str,
        *,
        qualified: str,
    ) -> Submodule:
        module = next((item for item in owner.modules if item.id == module_id), None)
        if module is None:
            raise ValueError(f"Unknown Interconnect endpoint Module: {qualified}")
        submodule = next(
            (item for item in module.submodules if item.id == submodule_id), None
        )
        if submodule is None:
            raise ValueError(f"Unknown Interconnect endpoint Submodule: {qualified}")
        return submodule

    @staticmethod
    def _validate_owner_spatial_ownership(
        *, owner_id: str, owner_kind: str, modules: Sequence[Module]
    ) -> None:
        occupied: dict[tuple[int, int], str] = {}
        reserved: list[tuple[int, int, int, int, str]] = []
        detail_key = owner_kind.lower()
        article = "an" if owner_kind == "Interconnect" else "a"
        for module in modules:
            for submodule in module.submodules:
                local_owner = f"{module.id}/{submodule.id}"
                if submodule.grid_shape is not None:
                    origin = submodule.logical_origin or (0, 0)
                    rows, columns = submodule.grid_shape
                    bounds = (
                        origin[0],
                        origin[0] + columns - 1,
                        origin[1],
                        origin[1] + rows - 1,
                        local_owner,
                    )
                    for left, right, bottom, top, previous in reserved:
                        if not (
                            bounds[1] < left
                            or right < bounds[0]
                            or bounds[3] < bottom
                            or top < bounds[2]
                        ):
                            raise ArchitectureValidationError(
                                "Reserved logical grid envelopes collide on "
                                f"{article} {owner_kind} canvas",
                                details={
                                    detail_key: owner_id,
                                    "grids": [
                                        f"{owner_id}/{previous}",
                                        f"{owner_id}/{local_owner}",
                                    ],
                                },
                            )
                    reserved.append(bounds)
                origin = submodule.logical_origin or (0, 0)
                for slot in submodule.slots:
                    if slot.coordinate is None:
                        continue
                    coordinate = (
                        origin[0] + slot.coordinate[0],
                        origin[1] + slot.coordinate[1],
                    )
                    slot_owner = f"{local_owner}/{slot.id}"
                    previous = occupied.get(coordinate)
                    if previous is not None:
                        raise ArchitectureValidationError(
                            "Logical slot coordinates collide on "
                            f"{article} {owner_kind} canvas",
                            details={
                                detail_key: owner_id,
                                "coordinate": list(coordinate),
                                "slots": [
                                    f"{owner_id}/{previous}",
                                    f"{owner_id}/{slot_owner}",
                                ],
                            },
                        )
                    occupied[coordinate] = slot_owner

        for coordinate, slot_owner in occupied.items():
            submodule_owner = slot_owner.rsplit("/", 1)[0]
            for left, right, bottom, top, reservation_owner in reserved:
                if (
                    reservation_owner != submodule_owner
                    and left <= coordinate[0] <= right
                    and bottom <= coordinate[1] <= top
                ):
                    raise ArchitectureValidationError(
                        "Logical slot collides with a reserved grid envelope on "
                        f"{article} {owner_kind} canvas",
                        details={
                            detail_key: owner_id,
                            "coordinate": list(coordinate),
                            "grid": f"{owner_id}/{reservation_owner}",
                            "slot": f"{owner_id}/{slot_owner}",
                            "bounds": {
                                "x_min": left,
                                "x_max": right,
                                "y_min": bottom,
                                "y_max": top,
                            },
                        },
                    )

    @property
    def architecture_hash(self) -> str:
        return semantic_hash(self._payload())

    def _payload(self) -> dict[str, Any]:
        return {
            "schema_version": ARCHITECTURE_SPECIFICATION_SCHEMA_VERSION,
            "nodes": [item.to_dict() for item in self.nodes],
            "interconnects": [item.to_dict() for item in self.interconnects],
        }

    def to_dict(self) -> dict[str, Any]:
        result = self._payload()
        result["architecture_hash"] = semantic_hash(result)
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArchitectureSpecification":
        item = _record(
            data,
            where="ArchitectureSpecification",
            required={
                "schema_version",
                "nodes",
                "interconnects",
                "architecture_hash",
            },
        )
        if item["schema_version"] != ARCHITECTURE_SPECIFICATION_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported ArchitectureSpecification schema: "
                f"{item['schema_version']}"
            )
        result = cls(
            nodes=tuple(
                Node.from_dict(value)
                for value in _records(item["nodes"], where="Nodes")
            ),
            interconnects=tuple(
                Interconnect.from_dict(value)
                for value in _records(
                    item["interconnects"], where="Interconnects"
                )
            ),
        )
        if item["architecture_hash"] != result.architecture_hash:
            raise ValueError("ArchitectureSpecification hash mismatch")
        return result

    def node(self, node_id: str) -> Node:
        for item in self.nodes:
            if item.id == node_id:
                return item
        raise KeyError(node_id)

    def interconnect(self, interconnect_id: str) -> Interconnect:
        for item in self.interconnects:
            if item.id == interconnect_id:
                return item
        raise KeyError(interconnect_id)

    def module(self, owner_id: str, module_id: str) -> Module:
        """Return a Module owned by either a Node or an Interconnect."""

        owner = next(
            (
                item
                for item in (*self.nodes, *self.interconnects)
                if item.id == owner_id
            ),
            None,
        )
        if owner is not None:
            for item in owner.modules:
                if item.id == module_id:
                    return item
        raise KeyError(f"{owner_id}/{module_id}")

    def submodule(
        self, owner_id: str, module_id: str, submodule_id: str
    ) -> Submodule:
        """Return a Submodule under either kind of resource owner."""

        for item in self.module(owner_id, module_id).submodules:
            if item.id == submodule_id:
                return item
        raise KeyError(f"{owner_id}/{module_id}/{submodule_id}")

    def effective_slot_coordinate(
        self,
        owner_id: str,
        module_id: str,
        submodule_id: str,
        slot_id: str,
    ) -> tuple[int, int] | None:
        """Return one effective coordinate on its owner's logical canvas."""

        submodule = self.submodule(owner_id, module_id, submodule_id)
        slot = next((item for item in submodule.slots if item.id == slot_id), None)
        if slot is None:
            raise KeyError(f"{owner_id}/{module_id}/{submodule_id}/{slot_id}")
        if slot.coordinate is None:
            return None
        origin = submodule.logical_origin or (0, 0)
        return (origin[0] + slot.coordinate[0], origin[1] + slot.coordinate[1])


__all__ = [
    "ARCHITECTURE_SPECIFICATION_SCHEMA_VERSION",
    "ArchitectureSpecification",
    "Interconnect",
    "LocalConnection",
    "LogicalSlot",
    "Module",
    "Node",
    "QECBinding",
    "QECResourceProtocolRef",
    "Submodule",
]
