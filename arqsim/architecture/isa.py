"""Shared instruction set understood by architecture state.

Program and resource DAGs use the same operation vocabulary.  The plane that
owns an instruction determines its purpose; the opcode determines what state
guards and transitions the target architecture must implement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, TypeAlias

from arqsim.schema import deep_freeze_json, normalize_json

from .recipes import InjectionRecipe, ProgramRecipeMember


class ArchitectureOpcode(str, Enum):
    EXECUTE_COMPUTE = "EXECUTE_COMPUTE"
    PREPARE_MAGIC_STATE = "PREPARE_MAGIC_STATE"
    PREPARE_LOGICAL_BELL = "PREPARE_LOGICAL_BELL"
    MOVE_QUBITS = "MOVE_QUBITS"
    STORE_QUBITS = "STORE_QUBITS"
    LOAD_QUBITS = "LOAD_QUBITS"
    TELEPORT_QUBITS = "TELEPORT_QUBITS"
    CLASSICAL_REACTION = "CLASSICAL_REACTION"
    FENCE = "FENCE"


PROGRAM_OPCODES = frozenset(
    {
        ArchitectureOpcode.EXECUTE_COMPUTE,
        ArchitectureOpcode.MOVE_QUBITS,
        ArchitectureOpcode.STORE_QUBITS,
        ArchitectureOpcode.LOAD_QUBITS,
        ArchitectureOpcode.TELEPORT_QUBITS,
        ArchitectureOpcode.CLASSICAL_REACTION,
        ArchitectureOpcode.FENCE,
    }
)

RESOURCE_OPCODES = frozenset(
    {
        ArchitectureOpcode.PREPARE_MAGIC_STATE,
        ArchitectureOpcode.PREPARE_LOGICAL_BELL,
        ArchitectureOpcode.MOVE_QUBITS,
        ArchitectureOpcode.STORE_QUBITS,
        ArchitectureOpcode.LOAD_QUBITS,
        ArchitectureOpcode.TELEPORT_QUBITS,
    }
)


# ``metadata`` is deliberately a diagnostic/provenance escape hatch.  The
# names below already have typed execution semantics on Program/Resource
# records and must not acquire a second, weakly typed authority in metadata.
# ``gates`` is the one documented historical exception for compute work until
# the planned typed ComputeWork operand replaces it.
_TYPED_OPERATION_METADATA_FIELDS = frozenset(
    {
        "id",
        "opcode",
        "layer",
        "layer_index",
        "predecessors",
        "predecessor_ids",
        "duration_s",
        "qubits",
        "consumes",
        "produces",
        "forwards",
        "engines",
        "required_locations",
        "completion_locations",
        "target_modules",
        "target_links",
        "move_operands",
        "deferred_dispatch",
        "implementation_recipes",
        "continuation_templates",
        "recipe_members",
        "step",
        "arrival_distribution",
        "parallelism",
        "dispatch_policy",
        "output_overflow_policy",
        "protocol",
    }
)


def _validate_diagnostic_metadata(
    metadata: Mapping[str, Any],
    *,
    label: str,
    allow_legacy_gates: bool = False,
) -> None:
    """Keep executable control in typed fields, not opaque metadata.

    This is intentionally a reserved-name check rather than an allowlist:
    compilers and backends may attach new observational receipts without a
    schema release, while execution-affecting values retain one typed source
    of truth.
    """

    if any(type(key) is not str for key in metadata):
        raise TypeError(f"{label} metadata keys must be strings")
    duplicated = _TYPED_OPERATION_METADATA_FIELDS & set(metadata)
    if duplicated:
        raise ValueError(
            f"Typed operation semantics cannot be stored in {label} metadata: "
            f"{sorted(duplicated)}"
        )
    if "gates" in metadata and not allow_legacy_gates:
        raise ValueError(
            f"Historical metadata.gates is valid only for EXECUTE_COMPUTE; "
            f"found it on {label}"
        )


DEFERRED_DISPATCH_RECIPE_SCHEMA_VERSION = "arqsim.deferred-dispatch-recipe.v1"


class MoveEntityKind(str, Enum):
    """Architecture entities whose slot geometry can be compiled as movement."""

    LOGICAL_QUBIT = "logical_qubit"
    MAGIC_STATE = "magic_state"


def _strict_fields(
    data: Mapping[str, Any],
    *,
    allowed: set[str],
    required: set[str],
    label: str,
) -> None:
    if not isinstance(data, Mapping):
        raise ValueError(f"{label} must be a mapping")
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unknown {label} fields: {sorted(unknown)}")
    missing = required - set(data)
    if missing:
        raise ValueError(f"{label} is missing: {sorted(missing)}")


def _slot_binding(
    value: Mapping[int | str, str],
    *,
    label: str,
) -> Mapping[int, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    result: dict[int, str] = {}
    for raw_qubit, raw_slot in value.items():
        if type(raw_qubit) is int:
            qubit = raw_qubit
        elif (
            type(raw_qubit) is str
            and raw_qubit.isascii()
            and raw_qubit.isdecimal()
            and raw_qubit == str(int(raw_qubit))
        ):
            qubit = int(raw_qubit)
        else:
            raise ValueError(f"{label} qubit ids must be non-negative integers")
        if type(raw_slot) is not str:
            raise ValueError(f"{label} slot ids must be strings")
        slot = raw_slot.strip()
        if qubit < 0 or not slot or slot != raw_slot:
            raise ValueError(
                f"{label} needs non-negative qubit ids and non-empty slot ids"
            )
        if qubit in result:
            raise ValueError(f"{label} repeats logical qubit {qubit}")
        result[qubit] = slot
    if len(set(result.values())) != len(result):
        raise ValueError(f"{label} slot ids must be unique")
    return MappingProxyType(dict(sorted(result.items())))


@dataclass(frozen=True, slots=True)
class MoveOperands:
    """Concrete per-qubit slot endpoints for an eagerly compiled Program MOVE.

    ``required_locations`` and ``completion_locations`` remain the runtime
    state claims at Module/Submodule granularity.  This record owns the finer
    slot geometry needed by the movement compiler; it is not a second location
    authority.
    """

    source_slots: Mapping[int | str, str]
    destination_slots: Mapping[int | str, str]
    entity_kind: MoveEntityKind | str = MoveEntityKind.LOGICAL_QUBIT

    def __post_init__(self) -> None:
        if not isinstance(self.entity_kind, (str, MoveEntityKind)):
            raise ValueError("Movement entity kind must be a string")
        try:
            entity_kind = MoveEntityKind(self.entity_kind)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported movement entity kind: {self.entity_kind!r}"
            ) from exc
        source = _slot_binding(self.source_slots, label="move source_slots")
        destination = _slot_binding(
            self.destination_slots,
            label="move destination_slots",
        )
        if not source or set(source) != set(destination):
            raise ValueError(
                "MOVE source/destination slot bindings must name the same "
                "non-empty logical-qubit set"
            )
        object.__setattr__(self, "entity_kind", entity_kind)
        object.__setattr__(self, "source_slots", source)
        object.__setattr__(self, "destination_slots", destination)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_kind": self.entity_kind.value,
            "source_slots": {
                str(qubit): slot for qubit, slot in self.source_slots.items()
            },
            "destination_slots": {
                str(qubit): slot
                for qubit, slot in self.destination_slots.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MoveOperands":
        allowed = {"entity_kind", "source_slots", "destination_slots"}
        _strict_fields(
            data,
            allowed=allowed,
            required=allowed,
            label="move operands",
        )
        if type(data["entity_kind"]) is not str:
            raise ValueError("Move-operands entity_kind must be a string")
        return cls(
            entity_kind=data["entity_kind"],
            source_slots=data["source_slots"],
            destination_slots=data["destination_slots"],
        )


@dataclass(frozen=True, slots=True)
class MagicRouteDispatchRecipe:
    """Static compiler facts awaiting concrete consumed magic-state slots.

    Operated qubits, resident locations, source layer, and magic demand already
    belong to ``ArchitectureInstruction``.  The recipe therefore stores only
    the circuit slice and logical-to-slot mapping not represented by the ISA.
    """

    operation_indices: tuple[int, ...]
    data_mapping: Mapping[int | str, str]

    def __post_init__(self) -> None:
        if type(self.operation_indices) is not tuple or any(
            type(value) is not int for value in self.operation_indices
        ):
            raise ValueError("Magic-route operation indices must be integers")
        indices = self.operation_indices
        if not indices or any(value < 0 for value in indices):
            raise ValueError(
                "Magic-route operation indices must be non-empty/non-negative"
            )
        if len(indices) != len(set(indices)) or indices != tuple(sorted(indices)):
            raise ValueError(
                "Magic-route operation indices must be unique and ascending"
            )
        mapping = _slot_binding(self.data_mapping, label="magic-route data_mapping")
        if not mapping:
            raise ValueError("Magic-route data_mapping cannot be empty")
        object.__setattr__(self, "operation_indices", indices)
        object.__setattr__(self, "data_mapping", mapping)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DEFERRED_DISPATCH_RECIPE_SCHEMA_VERSION,
            "kind": "joint_magic_route",
            "operation_indices": list(self.operation_indices),
            "data_mapping": {
                str(qubit): slot for qubit, slot in self.data_mapping.items()
            },
        }


@dataclass(frozen=True, slots=True)
class ResourceMoveDispatchRecipe:
    """Request state-bound endpoints for one Resource-plane MOVE batch."""

    entity_kind: MoveEntityKind | str = MoveEntityKind.MAGIC_STATE

    def __post_init__(self) -> None:
        if not isinstance(self.entity_kind, (str, MoveEntityKind)):
            raise ValueError("Movement entity kind must be a string")
        try:
            entity_kind = MoveEntityKind(self.entity_kind)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported movement entity kind: {self.entity_kind!r}"
            ) from exc
        object.__setattr__(self, "entity_kind", entity_kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": DEFERRED_DISPATCH_RECIPE_SCHEMA_VERSION,
            "kind": "state_bound_resource_move",
            "entity_kind": self.entity_kind.value,
        }


DeferredDispatchRecipe: TypeAlias = (
    MagicRouteDispatchRecipe | ResourceMoveDispatchRecipe
)


def deferred_dispatch_recipe_from_dict(
    data: Mapping[str, Any],
) -> DeferredDispatchRecipe:
    """Strictly parse one tagged deferred-dispatch recipe."""

    if not isinstance(data, Mapping):
        raise ValueError("Deferred-dispatch recipe must be a mapping")
    if (
        type(data.get("schema_version")) is not str
        or data.get("schema_version")
        != DEFERRED_DISPATCH_RECIPE_SCHEMA_VERSION
    ):
        raise ValueError(
            "Unsupported deferred-dispatch recipe schema: "
            f"{data.get('schema_version')!r}"
        )
    kind = data.get("kind")
    if type(kind) is not str:
        raise ValueError("Deferred-dispatch recipe kind must be a string")
    if kind == "joint_magic_route":
        allowed = {
            "schema_version",
            "kind",
            "operation_indices",
            "data_mapping",
        }
        _strict_fields(
            data,
            allowed=allowed,
            required=allowed,
            label="joint-magic-route recipe",
        )
        if type(data["operation_indices"]) is not list:
            raise ValueError(
                "joint-magic-route operation_indices must be an array"
            )
        if not isinstance(data["data_mapping"], Mapping):
            raise ValueError("joint-magic-route data_mapping must be a mapping")
        return MagicRouteDispatchRecipe(
            operation_indices=tuple(data["operation_indices"]),
            data_mapping=data["data_mapping"],
        )
    if kind == "state_bound_resource_move":
        allowed = {"schema_version", "kind", "entity_kind"}
        _strict_fields(
            data,
            allowed=allowed,
            required=allowed,
            label="state-bound-resource-move recipe",
        )
        if type(data["entity_kind"]) is not str:
            raise ValueError(
                "state-bound-resource-move entity_kind must be a string"
            )
        return ResourceMoveDispatchRecipe(entity_kind=data["entity_kind"])
    raise ValueError(f"Unsupported deferred-dispatch recipe kind: {kind!r}")


@dataclass(frozen=True, kw_only=True)
class OperationClaims:
    """State and topology claims common to both execution planes.

    This is an in-memory contract, not another wire record.  Program and
    Resource codecs keep their existing flat fields, while both operation
    types inherit the same validation and immutable representation here.
    Plane-specific operands, locations, recurrence policy, and descriptive
    receipts remain on their owning operation type.
    """

    consumes: Mapping[str, int] = field(default_factory=dict)
    produces: Mapping[str, int] = field(default_factory=dict)
    forwards: Mapping[str, str] = field(default_factory=dict)
    engines: Mapping[str, int] = field(default_factory=dict)
    target_modules: tuple[str, ...] = field(default_factory=tuple)
    target_links: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        for name in ("consumes", "produces", "engines"):
            claims = getattr(self, name)
            if not isinstance(claims, Mapping) or any(
                type(key) is not str or type(value) is not int
                for key, value in claims.items()
            ):
                raise TypeError(
                    f"Operation claims {name} must map strings to integers"
                )
            if any(value <= 0 for value in claims.values()):
                raise ValueError(
                    f"Operation claims {name} quantities must be positive"
                )
        if not isinstance(self.forwards, Mapping) or any(
            type(source) is not str or type(destination) is not str
            for source, destination in self.forwards.items()
        ):
            raise TypeError(
                "Operation claims forwards must map strings to strings"
            )
        for name in ("target_modules", "target_links"):
            values = getattr(self, name)
            if any(type(value) is not str for value in values):
                raise TypeError(
                    f"Operation claims {name} must contain strings"
                )
        object.__setattr__(
            self,
            "consumes",
            deep_freeze_json(dict(self.consumes)),
        )
        object.__setattr__(
            self,
            "produces",
            deep_freeze_json(dict(self.produces)),
        )
        object.__setattr__(
            self,
            "forwards",
            deep_freeze_json(dict(self.forwards)),
        )
        object.__setattr__(
            self,
            "engines",
            deep_freeze_json(dict(self.engines)),
        )
        object.__setattr__(
            self,
            "target_modules",
            tuple(sorted(set(self.target_modules))),
        )
        object.__setattr__(
            self,
            "target_links",
            tuple(sorted(set(self.target_links))),
        )


@dataclass(frozen=True)
class ProgramWorkTemplate(OperationClaims):
    """One lowerer-generated, conditionally activated Program operation.

    A template is a frozen execution-plan receipt, not an architecture timing
    input.  Its duration has already been resolved from the plan's canonical
    latency and compiler authorities.  Runtime recipes reference only causal
    stages and branches; the Event Engine activates the matching template
    without reading timing scalars back out of a recipe.
    """

    recipe_members: tuple[ProgramRecipeMember, ...]
    step: str
    opcode: ArchitectureOpcode | str
    duration_s: float
    qubits: tuple[int, ...] = field(default_factory=tuple)
    required_locations: Mapping[str, str] = field(default_factory=dict)
    completion_locations: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        members = tuple(self.recipe_members)
        if not members or any(
            not isinstance(item, ProgramRecipeMember) for item in members
        ):
            raise TypeError(
                "Program-work template recipe_members must contain at least "
                "one ProgramRecipeMember"
            )
        member_keys = [item.key for item in members]
        if len(member_keys) != len(set(member_keys)):
            raise ValueError("Program-work template recipe members must be unique")
        if self.step not in {
            "injection",
            "measurement",
            "reaction",
            "correction",
        }:
            raise ValueError(f"Unsupported Program-work template step: {self.step!r}")
        if self.step != "measurement" and len(members) != 1:
            raise ValueError(
                f"Program-work template {self.step} must belong to exactly one recipe"
            )
        try:
            opcode = ArchitectureOpcode(self.opcode)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported Program-work template opcode: {self.opcode!r}"
            ) from exc
        expected_opcode = (
            ArchitectureOpcode.CLASSICAL_REACTION
            if self.step == "reaction"
            else ArchitectureOpcode.EXECUTE_COMPUTE
        )
        if opcode != expected_opcode:
            raise ValueError(
                f"Program-work template step {self.step!r} requires "
                f"{expected_opcode.value}"
            )
        if type(self.duration_s) not in {int, float} or not math.isfinite(
            self.duration_s
        ):
            raise TypeError(
                "Program-work template duration_s must be a finite number"
            )
        duration = float(self.duration_s)
        if duration < 0:
            raise ValueError(
                "Program-work template duration_s must be non-negative"
            )
        raw_qubits = tuple(self.qubits)
        if any(type(value) is not int or value < 0 for value in raw_qubits):
            raise ValueError(
                "Program-work template qubits must be non-negative integers"
            )
        OperationClaims.__post_init__(self)
        for name, claims in (
            ("required_locations", self.required_locations),
            ("completion_locations", self.completion_locations),
        ):
            if not isinstance(claims, Mapping) or any(
                type(key) is not str or type(value) is not str
                for key, value in claims.items()
            ):
                raise TypeError(
                    f"Program-work template {name} must map strings to strings"
                )
        if not isinstance(self.metadata, Mapping):
            raise TypeError("Program-work template metadata must be a mapping")
        _validate_diagnostic_metadata(
            self.metadata,
            label="Program-work template",
            allow_legacy_gates=(opcode == ArchitectureOpcode.EXECUTE_COMPUTE),
        )
        object.__setattr__(self, "opcode", opcode)
        object.__setattr__(self, "recipe_members", members)
        object.__setattr__(self, "duration_s", duration)
        object.__setattr__(self, "qubits", tuple(sorted(set(raw_qubits))))
        object.__setattr__(
            self,
            "required_locations",
            deep_freeze_json(self.required_locations),
        )
        object.__setattr__(
            self,
            "completion_locations",
            deep_freeze_json(self.completion_locations),
        )
        object.__setattr__(self, "metadata", deep_freeze_json(self.metadata))

    @property
    def key(self) -> tuple[tuple[tuple[str, int], ...], str]:
        return (tuple(item.key for item in self.recipe_members), self.step)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe_members": [item.to_dict() for item in self.recipe_members],
            "step": self.step,
            "opcode": self.opcode.value,
            "duration_s": self.duration_s,
            "qubits": list(self.qubits),
            "consumes": normalize_json(self.consumes),
            "produces": normalize_json(self.produces),
            "forwards": normalize_json(self.forwards),
            "engines": normalize_json(self.engines),
            "required_locations": normalize_json(self.required_locations),
            "completion_locations": normalize_json(self.completion_locations),
            "target_modules": list(self.target_modules),
            "target_links": list(self.target_links),
            "metadata": normalize_json(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProgramWorkTemplate":
        required = {
            "recipe_members",
            "step",
            "opcode",
            "duration_s",
            "qubits",
            "consumes",
            "produces",
            "forwards",
            "engines",
            "required_locations",
            "completion_locations",
            "target_modules",
            "target_links",
            "metadata",
        }
        _strict_fields(
            data,
            allowed=required,
            required=required,
            label="Program-work template",
        )
        for name in (
            "recipe_members",
            "qubits",
            "target_modules",
            "target_links",
        ):
            if type(data[name]) is not list:
                raise ValueError(
                    f"Program-work template {name} must be an array"
                )
        for name in (
            "consumes",
            "produces",
            "forwards",
            "engines",
            "required_locations",
            "completion_locations",
            "metadata",
        ):
            if not isinstance(data[name], Mapping):
                raise ValueError(
                    f"Program-work template {name} must be a mapping"
                )
        return cls(
            recipe_members=tuple(
                ProgramRecipeMember.from_dict(item)
                for item in data["recipe_members"]
            ),
            step=data["step"],
            opcode=data["opcode"],
            duration_s=data["duration_s"],
            qubits=tuple(data["qubits"]),
            consumes=data["consumes"],
            produces=data["produces"],
            forwards=data["forwards"],
            engines=data["engines"],
            required_locations=data["required_locations"],
            completion_locations=data["completion_locations"],
            target_modules=tuple(data["target_modules"]),
            target_links=tuple(data["target_links"]),
            metadata=data["metadata"],
        )


@dataclass(frozen=True)
class ArchitectureInstruction(OperationClaims):
    """One node in the architecture-facing Program DAG.

    Dependencies encode program order.  Buffers, engines, and locations encode
    state guards and completion transitions; they do not add a second request
    language on top of the ISA.
    """

    id: int
    opcode: ArchitectureOpcode | str
    predecessor_ids: tuple[int, ...] = field(default_factory=tuple)
    duration_s: float = 0.0
    layer_index: int | None = None
    qubits: tuple[int, ...] = field(default_factory=tuple)
    required_locations: Mapping[str, str] = field(default_factory=dict)
    completion_locations: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    move_operands: MoveOperands | None = None
    deferred_dispatch: DeferredDispatchRecipe | None = None
    implementation_recipes: tuple[InjectionRecipe, ...] = field(default_factory=tuple)
    continuation_templates: tuple[ProgramWorkTemplate, ...] = field(
        default_factory=tuple
    )

    def __post_init__(self) -> None:
        if type(self.id) is not int:
            raise TypeError("Architecture instruction id must be an integer")
        if type(self.duration_s) not in {int, float} or not math.isfinite(
            self.duration_s
        ):
            raise TypeError(
                "Architecture instruction duration_s must be a finite number"
            )
        raw_predecessors = tuple(self.predecessor_ids)
        raw_qubits = tuple(self.qubits)
        if any(type(value) is not int for value in raw_predecessors):
            raise TypeError("Instruction predecessors must be integers")
        if any(type(value) is not int for value in raw_qubits):
            raise TypeError("Instruction qubits must be integers")
        if self.layer_index is not None and type(self.layer_index) is not int:
            raise TypeError("Instruction layer index must be an integer or None")
        OperationClaims.__post_init__(self)
        for name, claims in (
            ("required_locations", self.required_locations),
            ("completion_locations", self.completion_locations),
        ):
            if not isinstance(claims, Mapping) or any(
                type(key) is not str or type(value) is not str
                for key, value in claims.items()
            ):
                raise TypeError(f"Instruction {name} must map strings to strings")
        if not isinstance(self.metadata, Mapping):
            raise TypeError("Instruction metadata must be a mapping")
        try:
            opcode = ArchitectureOpcode(self.opcode)
        except ValueError as exc:
            raise ValueError(f"Unsupported architecture opcode: {self.opcode}") from exc
        duration = float(self.duration_s)
        predecessors = tuple(sorted(set(raw_predecessors)))
        if self.id < 0 or not math.isfinite(duration) or duration < 0:
            raise ValueError("Instruction id and duration must be non-negative")
        if any(value < 0 or value >= self.id for value in predecessors):
            raise ValueError("Instruction predecessors must be earlier non-negative ids")
        object.__setattr__(self, "opcode", opcode)
        object.__setattr__(self, "predecessor_ids", predecessors)
        object.__setattr__(self, "duration_s", duration)
        object.__setattr__(self, "qubits", tuple(sorted(set(raw_qubits))))
        object.__setattr__(
            self,
            "required_locations",
            deep_freeze_json(self.required_locations),
        )
        object.__setattr__(
            self,
            "completion_locations",
            deep_freeze_json(self.completion_locations),
        )
        object.__setattr__(self, "metadata", deep_freeze_json(self.metadata))
        legacy_control_metadata = {
            "deferred_until_dispatch",
            "destination_slots",
            "dispatch_deferred",
            "entity_kind",
            "route_binding_status",
            "runtime_route_request",
            "source_slots",
        } & set(self.metadata)
        if legacy_control_metadata:
            raise ValueError(
                "Legacy dispatch control cannot be stored in instruction "
                f"metadata: {sorted(legacy_control_metadata)}"
            )
        route_metrics = self.metadata.get("route_metrics")
        if isinstance(route_metrics, Mapping):
            nested_legacy_control = {
                "deferred_until_dispatch",
                "dispatch_deferred",
            } & set(route_metrics)
            if nested_legacy_control:
                raise ValueError(
                    "Legacy dispatch control cannot be stored in instruction "
                    "route_metrics: "
                    f"{sorted(nested_legacy_control)}"
                )
        _validate_diagnostic_metadata(
            self.metadata,
            label=f"{opcode.value} instruction",
            allow_legacy_gates=(opcode == ArchitectureOpcode.EXECUTE_COMPUTE),
        )
        if self.move_operands is not None:
            if opcode != ArchitectureOpcode.MOVE_QUBITS:
                raise ValueError("Move operands are valid only for MOVE_QUBITS")
            if set(self.move_operands.source_slots) != set(self.qubits):
                raise ValueError(
                    "Move operands must bind every and only instruction qubit"
                )
            duplicate_move_facts = {
                "source_slots",
                "destination_slots",
                "entity_kind",
            } & set(self.metadata)
            if duplicate_move_facts:
                raise ValueError(
                    "Typed move operands cannot be duplicated in metadata: "
                    f"{sorted(duplicate_move_facts)}"
                )
        recipe = self.deferred_dispatch
        if recipe is not None and not isinstance(
            recipe,
            (MagicRouteDispatchRecipe, ResourceMoveDispatchRecipe),
        ):
            raise ValueError("deferred_dispatch must be a typed dispatch recipe")
        if isinstance(recipe, MagicRouteDispatchRecipe):
            if opcode != ArchitectureOpcode.EXECUTE_COMPUTE:
                raise ValueError(
                    "A magic-route recipe is valid only for EXECUTE_COMPUTE"
                )
            if not self.consumes:
                raise ValueError("A magic-route recipe requires a resource demand")
            if not set(self.qubits) <= set(recipe.data_mapping):
                raise ValueError(
                    "A magic-route recipe data_mapping must cover every "
                    "operated instruction qubit"
                )
            duplicate_recipe_facts = {
                "data_mapping",
                "deferred_until_dispatch",
                "dispatch_deferred",
                "runtime_route_request",
                "route_binding_status",
                "operation_indices",
                "mapping",
            } & set(self.metadata)
            if duplicate_recipe_facts:
                raise ValueError(
                    "Typed magic-route recipe cannot be duplicated in metadata: "
                    f"{sorted(duplicate_recipe_facts)}"
                )
        if isinstance(recipe, ResourceMoveDispatchRecipe):
            raise ValueError(
                "Resource-move recipes belong to ResourceProcess, not the Program DAG"
            )
        implementation_recipes = tuple(self.implementation_recipes)
        if any(
            not isinstance(item, InjectionRecipe) for item in implementation_recipes
        ):
            raise TypeError(
                "implementation_recipes must contain typed InjectionRecipe values"
            )
        if implementation_recipes:
            if opcode != ArchitectureOpcode.EXECUTE_COMPUTE:
                raise ValueError(
                    "Injection recipes are valid only for EXECUTE_COMPUTE"
                )
            invocation_ids = [item.invocation_id for item in implementation_recipes]
            if len(invocation_ids) != len(set(invocation_ids)):
                raise ValueError("Injection recipe invocation ids must be unique")
            if any(
                item.source_layer_index != self.layer_index
                for item in implementation_recipes
            ):
                raise ValueError(
                    "Injection recipes must refer to their instruction source layer"
                )
            if any(
                not set(item.qubits) <= set(self.qubits)
                for item in implementation_recipes
            ):
                raise ValueError(
                    "Injection-recipe qubits must belong to the host instruction"
                )
            entry_claims: dict[str, int] = {}
            for item in implementation_recipes:
                resource = item.stages[0].resource
                entry_claims[resource.buffer_id] = (
                    entry_claims.get(resource.buffer_id, 0) + resource.quantity
                )
            if entry_claims != dict(self.consumes):
                raise ValueError(
                    "Injection-recipe entry resources must exactly match host consumes"
                )
        continuation_templates = tuple(self.continuation_templates)
        if any(
            not isinstance(item, ProgramWorkTemplate)
            for item in continuation_templates
        ):
            raise TypeError(
                "continuation_templates must contain typed ProgramWorkTemplate values"
            )
        if continuation_templates and opcode != ArchitectureOpcode.EXECUTE_COMPUTE:
            raise ValueError(
                "Program-work continuation templates are valid only for "
                "EXECUTE_COMPUTE"
            )
        template_keys = [item.key for item in continuation_templates]
        if len(template_keys) != len(set(template_keys)):
            raise ValueError("Program-work template keys must be unique")
        recipes_by_invocation = {
            item.invocation_id: item for item in implementation_recipes
        }
        unknown_template_invocations = {
            member.recipe_invocation_id
            for template in continuation_templates
            for member in template.recipe_members
        } - set(recipes_by_invocation)
        if unknown_template_invocations:
            raise ValueError(
                "Program-work templates reference unknown recipe invocations: "
                f"{sorted(unknown_template_invocations)}"
            )
        for template in continuation_templates:
            for member in template.recipe_members:
                recipe_item = recipes_by_invocation[member.recipe_invocation_id]
                if member.stage_index >= len(recipe_item.stages):
                    raise ValueError(
                        "Program-work template references an unknown recipe stage: "
                        f"{member.key!r}"
                    )
        expected_template_keys: set[
            tuple[tuple[tuple[str, int], ...], str]
        ] = set()
        if implementation_recipes:
            initial_members = tuple(
                ProgramRecipeMember(recipe_item.invocation_id, 0)
                for recipe_item in implementation_recipes
            )
            expected_template_keys.add(
                (tuple(item.key for item in initial_members), "measurement")
            )
        for recipe_item in implementation_recipes:
            for stage in recipe_item.stages:
                expected_template_keys.add(
                    (((recipe_item.invocation_id, stage.index),), "reaction")
                )
                if stage.index > 0:
                    expected_template_keys.add(
                        (((recipe_item.invocation_id, stage.index),), "injection")
                    )
                    expected_template_keys.add(
                        (((recipe_item.invocation_id, stage.index),), "measurement")
                    )
                if stage.failure_correction is not None:
                    expected_template_keys.add(
                        (((recipe_item.invocation_id, stage.index),), "correction")
                    )
        if set(template_keys) != expected_template_keys:
            missing = sorted(expected_template_keys - set(template_keys))
            extra = sorted(set(template_keys) - expected_template_keys)
            raise ValueError(
                "Program-work templates must exactly cover recipe continuations; "
                f"missing={missing}, extra={extra}"
            )
        if opcode == ArchitectureOpcode.FENCE:
            forbidden_claims = {
                "qubits": bool(self.qubits),
                "consumes": bool(self.consumes),
                "produces": bool(self.produces),
                "forwards": bool(self.forwards),
                "engines": bool(self.engines),
                "required_locations": bool(self.required_locations),
                "completion_locations": bool(self.completion_locations),
                "target_modules": bool(self.target_modules),
                "target_links": bool(self.target_links),
                "move_operands": self.move_operands is not None,
                "deferred_dispatch": self.deferred_dispatch is not None,
                "implementation_recipes": bool(implementation_recipes),
                "continuation_templates": bool(continuation_templates),
            }
            present = sorted(
                name for name, is_present in forbidden_claims.items() if is_present
            )
            if present:
                raise ValueError(
                    "FENCE is an ordering boundary and cannot carry execution "
                    f"claims: {present}"
                )
            if duration != 0.0:
                raise ValueError("FENCE duration_s must be zero")
        if opcode == ArchitectureOpcode.TELEPORT_QUBITS:
            entities = {f"q:{qubit}" for qubit in self.qubits}
            if not entities:
                raise ValueError(
                    "Program TELEPORT_QUBITS requires at least one logical qubit"
                )
            if not self.target_links:
                raise ValueError(
                    "Program TELEPORT_QUBITS must identify at least one target link"
                )
            if not self.consumes:
                raise ValueError(
                    "Program TELEPORT_QUBITS must consume its teleportation resource"
                )
            if self.produces or self.forwards:
                raise ValueError(
                    "Program TELEPORT_QUBITS moves logical-qubit locations; "
                    "Resource-plane token delivery owns produces/forwards"
                )
            if set(self.required_locations) != entities or set(
                self.completion_locations
            ) != entities:
                raise ValueError(
                    "Program TELEPORT_QUBITS required/completion locations must "
                    "exactly cover its logical qubits"
                )
            if any(
                self.required_locations[entity]
                == self.completion_locations[entity]
                for entity in entities
            ):
                raise ValueError(
                    "Program TELEPORT_QUBITS must change every logical-qubit location"
                )
        continuation_templates = tuple(
            sorted(continuation_templates, key=lambda item: item.key)
        )
        object.__setattr__(self, "implementation_recipes", implementation_recipes)
        object.__setattr__(self, "continuation_templates", continuation_templates)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "opcode": self.opcode.value,
            "predecessors": list(self.predecessor_ids),
            "duration_s": self.duration_s,
            "qubits": list(self.qubits),
            "consumes": normalize_json(self.consumes),
            "produces": normalize_json(self.produces),
            "forwards": normalize_json(self.forwards),
            "engines": normalize_json(self.engines),
            "required_locations": normalize_json(self.required_locations),
            "completion_locations": normalize_json(self.completion_locations),
            "target_modules": list(self.target_modules),
            "target_links": list(self.target_links),
            "metadata": normalize_json(self.metadata),
        }
        if self.layer_index is not None:
            result["layer"] = self.layer_index
        if self.move_operands is not None:
            result["move_operands"] = self.move_operands.to_dict()
        if self.deferred_dispatch is not None:
            result["deferred_dispatch"] = self.deferred_dispatch.to_dict()
        if self.implementation_recipes:
            result["implementation_recipes"] = [
                item.to_dict() for item in self.implementation_recipes
            ]
        if self.continuation_templates:
            result["continuation_templates"] = [
                item.to_dict() for item in self.continuation_templates
            ]
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArchitectureInstruction":
        allowed = {
            "id",
            "opcode",
            "predecessors",
            "duration_s",
            "layer",
            "qubits",
            "consumes",
            "produces",
            "forwards",
            "engines",
            "required_locations",
            "completion_locations",
            "target_modules",
            "target_links",
            "metadata",
            "move_operands",
            "deferred_dispatch",
            "implementation_recipes",
            "continuation_templates",
        }
        required = allowed - {
            "layer",
            "move_operands",
            "deferred_dispatch",
            "implementation_recipes",
            "continuation_templates",
        }
        _strict_fields(
            data,
            allowed=allowed,
            required=required,
            label="architecture instruction",
        )
        if type(data["id"]) is not int:
            raise ValueError("Architecture instruction id must be an integer")
        if type(data["opcode"]) is not str:
            raise ValueError("Architecture instruction opcode must be a string")
        duration = data["duration_s"]
        if type(duration) not in {int, float} or not math.isfinite(duration):
            raise ValueError(
                "Architecture instruction duration_s must be a finite number"
            )
        for name in ("predecessors", "qubits"):
            value = data[name]
            if type(value) is not list or any(type(item) is not int for item in value):
                raise ValueError(
                    f"Architecture instruction {name} must be an integer array"
                )
        if "layer" in data and type(data["layer"]) is not int:
            raise ValueError("Architecture instruction layer must be an integer")
        for name in ("consumes", "produces", "engines"):
            value = data[name]
            if not isinstance(value, Mapping) or any(
                type(key) is not str or type(amount) is not int
                for key, amount in value.items()
            ):
                raise ValueError(
                    f"Architecture instruction {name} must map strings to integers"
                )
        for name in ("forwards", "required_locations", "completion_locations"):
            value = data[name]
            if not isinstance(value, Mapping) or any(
                type(key) is not str or type(item) is not str
                for key, item in value.items()
            ):
                raise ValueError(
                    f"Architecture instruction {name} must map strings to strings"
                )
        for name in ("target_modules", "target_links"):
            value = data[name]
            if type(value) is not list or any(type(item) is not str for item in value):
                raise ValueError(
                    f"Architecture instruction {name} must be a string array"
                )
        if not isinstance(data["metadata"], Mapping):
            raise ValueError("Architecture instruction metadata must be a mapping")
        for name in ("move_operands", "deferred_dispatch"):
            if name in data and not isinstance(data[name], Mapping):
                raise ValueError(
                    f"Architecture instruction {name} must be a mapping"
                )
        raw_implementation_recipes = data.get("implementation_recipes", [])
        if type(raw_implementation_recipes) is not list or any(
            not isinstance(item, Mapping) for item in raw_implementation_recipes
        ):
            raise ValueError(
                "Architecture instruction implementation_recipes must be an array of mappings"
            )
        raw_continuation_templates = data.get("continuation_templates", [])
        if type(raw_continuation_templates) is not list or any(
            not isinstance(item, Mapping) for item in raw_continuation_templates
        ):
            raise ValueError(
                "Architecture instruction continuation_templates must be an array of mappings"
            )
        return cls(
            id=data["id"],
            opcode=data["opcode"],
            predecessor_ids=tuple(data["predecessors"]),
            duration_s=duration,
            layer_index=data.get("layer"),
            qubits=tuple(data["qubits"]),
            consumes=data["consumes"],
            produces=data["produces"],
            forwards=data["forwards"],
            engines=data["engines"],
            required_locations=data["required_locations"],
            completion_locations=data["completion_locations"],
            target_modules=tuple(data["target_modules"]),
            target_links=tuple(data["target_links"]),
            metadata=data["metadata"],
            move_operands=(
                MoveOperands.from_dict(data["move_operands"])
                if data.get("move_operands") is not None
                else None
            ),
            deferred_dispatch=(
                deferred_dispatch_recipe_from_dict(data["deferred_dispatch"])
                if data.get("deferred_dispatch") is not None
                else None
            ),
            implementation_recipes=tuple(
                InjectionRecipe.from_dict(item)
                for item in raw_implementation_recipes
            ),
            continuation_templates=tuple(
                ProgramWorkTemplate.from_dict(item)
                for item in raw_continuation_templates
            ),
        )
