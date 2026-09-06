"""Unsized, user-authored logical architecture Profiles.

A Profile owns only the architecture facts that exist before sizing, logical
placement, and QEC selection. Nodes and Interconnects are peer resource
owners: each contains Modules, Submodules, and optional owner-local
connections. Interconnect endpoints are absolute references to Node-owned
Submodules and declare where the distributed domain attaches.

Identifiers are opaque labels. Their spelling never selects architecture
behavior; hierarchy is expressed by nesting and qualified references.

This module also provides the generic file loader used by custom Profiles.
Lookup of the designs shipped with ArqSim belongs to
``arqsim.architecture.gallery`` rather than to this core data model.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, TypeVar

import yaml

from arqsim.schema import semantic_hash

from .identifiers import require_local_id as _local_id
from .identifiers import require_profile_id as _profile_id


ARCHITECTURE_PROFILE_SCHEMA_VERSION = "arqsim.architecture-profile.v3"

# The small semantic vocabulary consumed by the current policies. IDs remain
# opaque, and ownership is carried by the hierarchy rather than by names.
_NODE_MODALITIES = frozenset({"neutral_atom", "superconducting"})
_MODULE_TYPES = frozenset(
    {"compute", "memory", "resource_factory", "bell_engine", "bell_storage"}
)
_SUBMODULE_TYPES = frozenset({"region", "buffer", "engine"})
_PAYLOADS = frozenset({"logical_qubit", "magic_state", "bell_pair"})
_CONNECTION_DIRECTIONS = frozenset({"directed", "bidirectional"})


def _enum(value: Any, choices: frozenset[str], *, where: str) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(
            f"Unsupported {where}: {value}; supported={sorted(choices)}"
        )
    return value


def _mapping(value: Any, *, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{where} must be a mapping")
    return value


def _sequence(value: Any, *, where: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{where} must be a sequence")
    return value


def _record(
    value: Any,
    *,
    where: str,
    required: set[str],
    optional: set[str] | None = None,
) -> Mapping[str, Any]:
    item = _mapping(value, where=where)
    optional = optional or set()
    missing = sorted(required - set(item), key=repr)
    if missing:
        raise ValueError(f"Missing fields in {where}: {missing}")
    unknown = sorted(set(item) - required - optional, key=repr)
    if unknown:
        raise ValueError(f"Unknown fields in {where}: {unknown}")
    return item


def _local_ref(value: Any, *, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{where} must be a Module/Submodule reference")
    parts = value.split("/")
    if len(parts) != 2:
        raise ValueError(f"{where} must be a Module/Submodule reference")
    module_id = _local_id(parts[0], where=f"{where} Module")
    submodule_id = _local_id(parts[1], where=f"{where} Submodule")
    return f"{module_id}/{submodule_id}"


def _absolute_ref(value: Any, *, where: str) -> str:
    if not isinstance(value, str):
        raise ValueError(
            f"{where} must be a Node/Module/Submodule reference"
        )
    parts = value.split("/")
    if len(parts) != 3:
        raise ValueError(
            f"{where} must be a Node/Module/Submodule reference"
        )
    node_id = _local_id(parts[0], where=f"{where} Node")
    module_id = _local_id(parts[1], where=f"{where} Module")
    submodule_id = _local_id(parts[2], where=f"{where} Submodule")
    return f"{node_id}/{module_id}/{submodule_id}"


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader which rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"Duplicate YAML mapping key: {key!r}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


_T = TypeVar("_T")


def _typed_tuple(
    values: object,
    expected_type: type[_T],
    *,
    where: str,
) -> tuple[_T, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise TypeError(f"{where} must be a sequence")
    result = tuple(values)
    if any(not isinstance(value, expected_type) for value in result):
        raise TypeError(f"{where} must contain {expected_type.__name__} values")
    return result


@dataclass(frozen=True, slots=True)
class ProfileSubmodule:
    """One unsized logical resource within a Profile Module."""

    id: str
    type: str
    payload: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _local_id(self.id, where="Submodule ID"))
        object.__setattr__(
            self,
            "type",
            _enum(self.type, _SUBMODULE_TYPES, where="Submodule type"),
        )
        object.__setattr__(
            self,
            "payload",
            _enum(self.payload, _PAYLOADS, where="Submodule payload"),
        )


@dataclass(frozen=True, slots=True)
class ProfileModule:
    """An unsized Module and its owned Submodules."""

    id: str
    type: str
    submodules: tuple[ProfileSubmodule, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _local_id(self.id, where="Module ID"))
        object.__setattr__(
            self,
            "type",
            _enum(self.type, _MODULE_TYPES, where="Module type"),
        )
        submodules = tuple(
            sorted(
                _typed_tuple(
                    self.submodules,
                    ProfileSubmodule,
                    where=f"Module {self.id} Submodules",
                ),
                key=lambda item: item.id,
            )
        )
        if not submodules:
            raise ValueError(f"Module {self.id} needs at least one Submodule")
        ids = [item.id for item in submodules]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Module {self.id} has duplicate Submodule IDs")
        object.__setattr__(self, "submodules", submodules)


@dataclass(frozen=True, slots=True)
class ProfileLocalConnection:
    """Cross-Module state-transfer topology local to one owner.

    Directed endpoints are ordered as ``(source, destination)``.
    """

    id: str
    direction: str
    endpoints: tuple[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _local_id(self.id, where="connection ID"))
        direction = _enum(
            self.direction,
            _CONNECTION_DIRECTIONS,
            where="connection direction",
        )
        object.__setattr__(self, "direction", direction)
        endpoints = tuple(
            _local_ref(value, where=f"Connection {self.id} endpoint")
            for value in _sequence(
                self.endpoints,
                where=f"Connection {self.id} endpoints",
            )
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


def _owner_contents(
    *,
    owner_id: str,
    owner_kind: str,
    modules: object,
    connections: object,
) -> tuple[tuple[ProfileModule, ...], tuple[ProfileLocalConnection, ...]]:
    normalized_modules = tuple(
        sorted(
            _typed_tuple(
                modules,
                ProfileModule,
                where=f"{owner_kind} {owner_id} Modules",
            ),
            key=lambda item: item.id,
        )
    )
    module_ids = [item.id for item in normalized_modules]
    if len(module_ids) != len(set(module_ids)):
        raise ValueError(f"{owner_kind} {owner_id} has duplicate Module IDs")

    normalized_connections = tuple(
        sorted(
            _typed_tuple(
                connections,
                ProfileLocalConnection,
                where=f"{owner_kind} {owner_id} connections",
            ),
            key=lambda item: item.id,
        )
    )
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
        payloads = {
            submodules[endpoint].payload for endpoint in connection.endpoints
        }
        if len(payloads) != 1:
            raise ValueError(
                f"Connection {connection.id} endpoints must have the same payload"
            )
    return normalized_modules, normalized_connections


@dataclass(frozen=True, slots=True)
class ProfileNode:
    """An unsized logical Node and its owner-local topology."""

    id: str
    modality: str
    modules: tuple[ProfileModule, ...]
    connections: tuple[ProfileLocalConnection, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _local_id(self.id, where="Node ID"))
        object.__setattr__(
            self,
            "modality",
            _enum(self.modality, _NODE_MODALITIES, where="Node modality"),
        )
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


@dataclass(frozen=True, slots=True)
class ProfileInterconnect:
    """An unsized Node-peer resource owner and its attachment points."""

    id: str
    endpoints: tuple[str, ...]
    modules: tuple[ProfileModule, ...]
    connections: tuple[ProfileLocalConnection, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "id",
            _local_id(self.id, where="Interconnect ID"),
        )
        endpoints = tuple(
            sorted(
                _absolute_ref(
                    value,
                    where=f"Interconnect {self.id} endpoint",
                )
                for value in _sequence(
                    self.endpoints,
                    where=f"Interconnect {self.id} endpoints",
                )
            )
        )
        if len(endpoints) < 2:
            raise ValueError(f"Interconnect {self.id} needs at least two endpoints")
        if len(endpoints) != len(set(endpoints)):
            raise ValueError(f"Interconnect {self.id} needs distinct endpoints")
        endpoint_nodes = {value.split("/", 1)[0] for value in endpoints}
        if len(endpoint_nodes) != 2:
            raise ValueError(
                f"Interconnect {self.id} endpoints must belong to exactly "
                "two distinct Nodes"
            )
        modules, connections = _owner_contents(
            owner_id=self.id,
            owner_kind="Interconnect",
            modules=self.modules,
            connections=self.connections,
        )
        if not modules:
            raise ValueError(f"Interconnect {self.id} needs at least one Module")
        object.__setattr__(self, "endpoints", endpoints)
        object.__setattr__(self, "modules", modules)
        object.__setattr__(self, "connections", connections)


@dataclass(frozen=True, slots=True)
class ArchitectureProfile:
    """One immutable, unsized logical architecture graph."""

    id: str
    name: str
    nodes: tuple[ProfileNode, ...]
    description: str = ""
    interconnects: tuple[ProfileInterconnect, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _profile_id(self.id))
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("An ArchitectureProfile needs a non-empty name")
        if not isinstance(self.description, str):
            raise TypeError("ArchitectureProfile description must be a string")
        nodes = tuple(
            sorted(
                _typed_tuple(
                    self.nodes,
                    ProfileNode,
                    where="ArchitectureProfile nodes",
                ),
                key=lambda item: item.id,
            )
        )
        if not nodes:
            raise ValueError("An ArchitectureProfile needs at least one Node")
        node_ids = [item.id for item in nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError(f"Duplicate Node ID in Profile {self.id}")

        interconnects = tuple(
            sorted(
                _typed_tuple(
                    self.interconnects,
                    ProfileInterconnect,
                    where="ArchitectureProfile Interconnects",
                ),
                key=lambda item: item.id,
            )
        )
        interconnect_ids = [item.id for item in interconnects]
        if len(interconnect_ids) != len(set(interconnect_ids)):
            raise ValueError(f"Duplicate Interconnect ID in Profile {self.id}")
        if set(node_ids) & set(interconnect_ids):
            raise ValueError(
                "Node and Interconnect owner IDs must be globally unique"
            )

        nodes_by_id = {item.id: item for item in nodes}
        for interconnect in interconnects:
            for endpoint in interconnect.endpoints:
                node_id, module_id, submodule_id = endpoint.split("/")
                node = nodes_by_id.get(node_id)
                if node is None:
                    raise ValueError(
                        f"Interconnect {interconnect.id} endpoint references "
                        f"unknown Node: {endpoint}"
                    )
                module = next(
                    (item for item in node.modules if item.id == module_id),
                    None,
                )
                if module is None:
                    raise ValueError(
                        "Unknown Interconnect endpoint Module: "
                        f"{endpoint}"
                    )
                if not any(
                    item.id == submodule_id for item in module.submodules
                ):
                    raise ValueError(
                        "Unknown Interconnect endpoint Submodule: "
                        f"{endpoint}"
                    )

        object.__setattr__(self, "nodes", nodes)
        object.__setattr__(self, "interconnects", interconnects)

    @property
    def profile_hash(self) -> str:
        """Return the semantic hash of the canonical Profile document."""

        return semantic_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Serialize the normalized Profile-v3 authoring document."""

        result: dict[str, Any] = {
            "schema_version": ARCHITECTURE_PROFILE_SCHEMA_VERSION,
            "id": self.id,
            "name": self.name,
        }
        if self.description:
            result["description"] = self.description
        result["nodes"] = {
            node.id: _owner_dict(node, modality=node.modality)
            for node in self.nodes
        }
        result["interconnects"] = {
            interconnect.id: _owner_dict(
                interconnect,
                endpoints=interconnect.endpoints,
            )
            for interconnect in self.interconnects
        }
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArchitectureProfile":
        """Parse one strict Profile-v3 authoring document."""

        item = _record(
            data,
            where="architecture Profile v3",
            required={
                "schema_version",
                "id",
                "name",
                "nodes",
                "interconnects",
            },
            optional={"description"},
        )
        schema = item["schema_version"]
        if schema != ARCHITECTURE_PROFILE_SCHEMA_VERSION:
            raise ValueError(f"Unsupported architecture-Profile schema: {schema}")

        nodes = tuple(
            _parse_node(raw_id, raw_node)
            for raw_id, raw_node in _mapping(
                item["nodes"],
                where="Profile v3 Nodes",
            ).items()
        )
        interconnects = tuple(
            _parse_interconnect(raw_id, raw_interconnect)
            for raw_id, raw_interconnect in _mapping(
                item["interconnects"],
                where="Profile v3 Interconnects",
            ).items()
        )
        return cls(
            id=item["id"],
            name=item["name"],
            description=item.get("description", ""),
            nodes=nodes,
            interconnects=interconnects,
        )


def _owner_dict(
    owner: ProfileNode | ProfileInterconnect,
    *,
    modality: str | None = None,
    endpoints: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if modality is not None:
        result["modality"] = modality
    if endpoints is not None:
        result["endpoints"] = list(endpoints)
    result["modules"] = {
        module.id: {
            "type": module.type,
            "submodules": {
                submodule.id: {
                    "type": submodule.type,
                    "payload": submodule.payload,
                }
                for submodule in module.submodules
            },
        }
        for module in owner.modules
    }
    if owner.connections:
        result["connections"] = {
            connection.id: {
                "direction": connection.direction,
                "endpoints": list(connection.endpoints),
            }
            for connection in owner.connections
        }
    return result


def _parse_modules(value: Any, *, owner: str) -> tuple[ProfileModule, ...]:
    modules: list[ProfileModule] = []
    for raw_module_id, raw_module in _mapping(
        value,
        where=f"{owner} Modules",
    ).items():
        module_id = _local_id(raw_module_id, where="Module ID")
        item = _record(
            raw_module,
            where=f"Module {module_id}",
            required={"type", "submodules"},
        )
        submodules: list[ProfileSubmodule] = []
        for raw_submodule_id, raw_submodule in _mapping(
            item["submodules"],
            where=f"Module {module_id} Submodules",
        ).items():
            submodule_id = _local_id(raw_submodule_id, where="Submodule ID")
            submodule = _record(
                raw_submodule,
                where=f"Submodule {module_id}/{submodule_id}",
                required={"type", "payload"},
            )
            submodules.append(
                ProfileSubmodule(
                    id=submodule_id,
                    type=submodule["type"],
                    payload=submodule["payload"],
                )
            )
        modules.append(
            ProfileModule(
                id=module_id,
                type=item["type"],
                submodules=tuple(submodules),
            )
        )
    return tuple(modules)


def _parse_connections(
    value: Any,
    *,
    owner: str,
) -> tuple[ProfileLocalConnection, ...]:
    connections: list[ProfileLocalConnection] = []
    for raw_connection_id, raw_connection in _mapping(
        value,
        where=f"{owner} connections",
    ).items():
        connection_id = _local_id(raw_connection_id, where="connection ID")
        item = _record(
            raw_connection,
            where=f"connection {connection_id}",
            required={"direction", "endpoints"},
        )
        connections.append(
            ProfileLocalConnection(
                id=connection_id,
                direction=item["direction"],
                endpoints=tuple(
                    _sequence(
                        item["endpoints"],
                        where=f"connection {connection_id} endpoints",
                    )
                ),
            )
        )
    return tuple(connections)


def _parse_node(raw_id: Any, value: Any) -> ProfileNode:
    node_id = _local_id(raw_id, where="Node ID")
    item = _record(
        value,
        where=f"Node {node_id}",
        required={"modality", "modules"},
        optional={"connections"},
    )
    return ProfileNode(
        id=node_id,
        modality=item["modality"],
        modules=_parse_modules(item["modules"], owner=f"Node {node_id}"),
        connections=_parse_connections(
            item.get("connections", {}),
            owner=f"Node {node_id}",
        ),
    )


def _parse_interconnect(raw_id: Any, value: Any) -> ProfileInterconnect:
    interconnect_id = _local_id(raw_id, where="Interconnect ID")
    item = _record(
        value,
        where=f"Interconnect {interconnect_id}",
        required={"endpoints", "modules"},
        optional={"connections"},
    )
    return ProfileInterconnect(
        id=interconnect_id,
        endpoints=tuple(
            _sequence(
                item["endpoints"],
                where=f"Interconnect {interconnect_id} endpoints",
            )
        ),
        modules=_parse_modules(
            item["modules"],
            owner=f"Interconnect {interconnect_id}",
        ),
        connections=_parse_connections(
            item.get("connections", {}),
            owner=f"Interconnect {interconnect_id}",
        ),
    )


def load_architecture_profile(path: Path | str) -> ArchitectureProfile:
    payload = yaml.load(
        Path(path).read_text(encoding="utf-8"),
        Loader=_UniqueKeyLoader,
    )
    if not isinstance(payload, Mapping):
        raise ValueError(f"Architecture Profile must be a mapping: {path}")
    return ArchitectureProfile.from_dict(payload)


__all__ = [
    "ARCHITECTURE_PROFILE_SCHEMA_VERSION",
    "ArchitectureProfile",
    "ProfileInterconnect",
    "ProfileLocalConnection",
    "ProfileModule",
    "ProfileNode",
    "ProfileSubmodule",
    "load_architecture_profile",
]
