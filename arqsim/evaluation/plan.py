"""Immutable input contract for state-coupled evaluation."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from arqsim.architecture.isa import (
    MagicRouteDispatchRecipe,
    MoveEntityKind,
    ResourceMoveDispatchRecipe,
)
from arqsim.evaluation.policy import EvaluationPolicy, RuntimeInjectionMode
from arqsim.schema import deep_freeze_json, normalize_json, semantic_hash

from .program_dag import ProgramDAG
from .resource_dag import ResourceDAG


EXECUTION_PLAN_SCHEMA_VERSION = "arqsim.execution-plan.v9"


def _require_exact_json_wire(value: Any, *, path: str) -> None:
    """Reject Python container aliases before typed plan reconstruction."""

    value_type = type(value)
    if value_type is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise TypeError(
                    f"{path} must map strings to strings at the JSON key "
                    "boundary; JSON object keys must be strings"
                )
            _require_exact_json_wire(child, path=f"{path}.{key}")
        return
    if value_type is list:
        for index, child in enumerate(value):
            _require_exact_json_wire(child, path=f"{path}[{index}]")
        return
    if value is None or value_type in {str, bool, int}:
        return
    if value_type is float:
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite JSON numbers")
        return
    if isinstance(value, Mapping):
        raise ValueError(
            "Execution-plan input must use the exact canonical JSON wire "
            f"shape: {path} JSON objects must be plain dictionaries"
        )
    if isinstance(value, (tuple, list)):
        raise ValueError(
            "Execution-plan input must use the exact canonical JSON wire "
            f"shape: {path} JSON arrays must be plain lists"
        )
    raise TypeError(f"{path} contains a non-JSON value of type {value_type.__name__}")


def _require_fields(
    data: Mapping[str, Any],
    *,
    required: frozenset[str],
    optional: frozenset[str] = frozenset(),
    label: str,
) -> None:
    """Reject incomplete or widened records at the plan wire boundary."""

    if not isinstance(data, Mapping):
        raise TypeError(f"{label} must be a mapping")
    missing = required - set(data)
    if missing:
        raise ValueError(f"Missing {label} fields: {sorted(missing)}")
    unknown = set(data) - required - optional
    if unknown:
        raise ValueError(f"Unknown {label} fields: {sorted(unknown)}")


def _exact_json_equal(left: Any, right: Any) -> bool:
    """Compare canonical wire values without coercion or container aliases."""

    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(
            _exact_json_equal(left[key], right[key]) for key in left
        )
    if type(left) is list:
        return len(left) == len(right) and all(
            _exact_json_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


@dataclass(frozen=True)
class BufferSpec:
    id: str
    capacity: int
    token_kind: str
    module: str | None = None
    slots: tuple[str, ...] = field(default_factory=tuple)
    initial_contents: tuple[str, ...] = field(default_factory=tuple)
    submodule: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise TypeError("Buffer id must be a non-empty string")
        if not isinstance(self.token_kind, str) or not self.token_kind.strip():
            raise TypeError("Buffer token kind must be a non-empty string")
        if type(self.capacity) is not int or self.capacity <= 0:
            raise TypeError("Buffer capacity must be a positive plain integer")
        if self.module is not None and (
            not isinstance(self.module, str) or not self.module.strip()
        ):
            raise TypeError("Buffer module must be a non-empty string or None")
        if self.submodule is not None and (
            not isinstance(self.submodule, str) or not self.submodule.strip()
        ):
            raise TypeError("Buffer submodule must be a non-empty string or None")
        slots = tuple(self.slots) or tuple(
            f"S{index}" for index in range(self.capacity)
        )
        initial = tuple(self.initial_contents)
        if any(not isinstance(value, str) or not value for value in slots):
            raise TypeError("Buffer slots must be non-empty strings")
        if any(not isinstance(value, str) or not value for value in initial):
            raise TypeError("Initial buffer contents must be non-empty strings")
        if self.submodule is not None and self.module is None:
            raise ValueError("A Buffer submodule owner requires a Module owner")
        if len(slots) != self.capacity or len(set(slots)) != len(slots):
            raise ValueError("Buffer slots must be unique and match capacity")
        if len(initial) > self.capacity or len(set(initial)) != len(initial):
            raise ValueError("Initial buffer contents must be unique and fit")
        object.__setattr__(self, "slots", slots)
        object.__setattr__(self, "initial_contents", initial)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "capacity": self.capacity,
            "token_kind": self.token_kind,
            "slots": list(self.slots),
            "initial_contents": list(self.initial_contents),
        }
        if self.module is not None:
            result["module"] = self.module
        if self.submodule is not None:
            result["submodule"] = self.submodule
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BufferSpec":
        _require_fields(
            data,
            required=frozenset(
                {
                    "id",
                    "capacity",
                    "token_kind",
                    "slots",
                    "initial_contents",
                }
            ),
            optional=frozenset({"module", "submodule"}),
            label="buffer",
        )
        for name in ("slots", "initial_contents"):
            value = data[name]
            if type(value) is not list or any(
                type(item) is not str for item in value
            ):
                raise TypeError(f"Buffer {name} must be a string array")
        return cls(
            id=data["id"],
            capacity=data["capacity"],
            token_kind=data["token_kind"],
            module=data.get("module"),
            submodule=(
                data["submodule"]
                if data.get("submodule") is not None
                else None
            ),
            slots=tuple(data["slots"]),
            initial_contents=tuple(data["initial_contents"]),
        )


@dataclass(frozen=True)
class EngineSpec:
    id: str
    capacity: int = 1
    module: str | None = None
    submodule: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise TypeError("Engine id must be a non-empty string")
        if type(self.capacity) is not int or self.capacity <= 0:
            raise TypeError("Engine capacity must be a positive plain integer")
        if self.module is not None and (
            not isinstance(self.module, str) or not self.module.strip()
        ):
            raise TypeError("Engine module must be a non-empty string or None")
        if self.submodule is not None and (
            not isinstance(self.submodule, str) or not self.submodule.strip()
        ):
            raise TypeError("Engine submodule must be a non-empty string or None")
        if self.submodule is not None and self.module is None:
            raise ValueError("An Engine submodule owner requires a Module owner")

    def to_dict(self) -> dict[str, Any]:
        result = {"id": self.id, "capacity": self.capacity}
        if self.module is not None:
            result["module"] = self.module
        if self.submodule is not None:
            result["submodule"] = self.submodule
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EngineSpec":
        _require_fields(
            data,
            required=frozenset({"id", "capacity"}),
            optional=frozenset({"module", "submodule"}),
            label="engine",
        )
        return cls(
            id=data["id"],
            capacity=data["capacity"],
            module=data.get("module"),
            submodule=(
                data["submodule"]
                if data.get("submodule") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ExecutionPlan:
    circuit_hash: str
    architecture_hash: str
    latency_profile_hash: str
    policy: EvaluationPolicy
    program_dag: ProgramDAG
    resource_dag: ResourceDAG
    buffers: tuple[BufferSpec, ...]
    engines: tuple[EngineSpec, ...]
    initial_locations: Mapping[str, str] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)
    runtime_components: Mapping[str, Any] = field(default_factory=dict)
    _plan_hash: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        for name in (
            "circuit_hash",
            "architecture_hash",
            "latency_profile_hash",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise TypeError(f"ExecutionPlan {name} must be a non-empty string")
        if not isinstance(self.policy, EvaluationPolicy):
            raise TypeError("ExecutionPlan policy must be an EvaluationPolicy")
        if not isinstance(self.program_dag, ProgramDAG):
            raise TypeError("ExecutionPlan program_dag must be a ProgramDAG")
        if not isinstance(self.resource_dag, ResourceDAG):
            raise TypeError("ExecutionPlan resource_dag must be a ResourceDAG")
        if not isinstance(self.initial_locations, Mapping):
            raise TypeError("ExecutionPlan initial_locations must be a mapping")
        if any(
            type(key) is not str or type(value) is not str
            for key, value in self.initial_locations.items()
        ):
            raise TypeError(
                "ExecutionPlan initial_locations must map strings to strings"
            )
        if not isinstance(self.provenance, Mapping):
            raise TypeError("ExecutionPlan provenance must be a mapping")
        if not isinstance(self.runtime_components, Mapping):
            raise TypeError("ExecutionPlan runtime_components must be a mapping")
        if any(not isinstance(item, BufferSpec) for item in self.buffers):
            raise TypeError("ExecutionPlan buffers must contain BufferSpec values")
        if any(not isinstance(item, EngineSpec) for item in self.engines):
            raise TypeError("ExecutionPlan engines must contain EngineSpec values")
        buffer_ids = [item.id for item in self.buffers]
        engine_ids = [item.id for item in self.engines]
        if len(buffer_ids) != len(set(buffer_ids)) or len(engine_ids) != len(set(engine_ids)):
            raise ValueError("Buffer and engine ids must be unique")
        known_buffers = set(buffer_ids)
        known_engines = set(engine_ids)
        buffer_capacities = {item.id: item.capacity for item in self.buffers}
        engine_capacities = {item.id: item.capacity for item in self.engines}
        buffer_kinds = {item.id: item.token_kind for item in self.buffers}
        initial_token_ids = [
            token
            for buffer in self.buffers
            for token in buffer.initial_contents
        ]
        if len(initial_token_ids) != len(set(initial_token_ids)):
            raise ValueError("Initial resource-token ids must be globally unique")
        conflicting_initial_ids = set(initial_token_ids) & set(self.initial_locations)
        if conflicting_initial_ids:
            raise ValueError(
                "Initial resource-token ids conflict with initial location entities: "
                f"{sorted(conflicting_initial_ids)}"
            )
        operations = [*self.program_dag.instructions, *self.resource_dag.processes]
        recipe_invocation_ids: set[str] = set()
        has_implementation_recipes = False
        for operation in operations:
            unknown_buffers = (set(operation.consumes) | set(operation.produces)) - known_buffers
            unknown_engines = set(operation.engines) - known_engines
            if unknown_buffers:
                raise ValueError(f"Unknown buffers in {operation.id}: {sorted(unknown_buffers)}")
            if unknown_engines:
                raise ValueError(f"Unknown engines in {operation.id}: {sorted(unknown_engines)}")
            oversized_consumes = {
                buffer_id: amount
                for buffer_id, amount in operation.consumes.items()
                if amount > buffer_capacities[buffer_id]
            }
            oversized_engines = {
                engine_id: amount
                for engine_id, amount in operation.engines.items()
                if amount > engine_capacities[engine_id]
            }
            permits_discard = (
                getattr(operation, "output_overflow_policy", "block")
                == "discard_excess"
            )
            oversized_produces = {
                buffer_id: amount
                for buffer_id, amount in operation.produces.items()
                if amount > buffer_capacities[buffer_id]
                and not permits_discard
            }
            if oversized_consumes or oversized_produces:
                raise ValueError(
                    f"Operation {operation.id} buffer claims exceed installed "
                    "capacity"
                )
            if oversized_engines:
                raise ValueError(
                    f"Operation {operation.id} engine claims exceed installed "
                    "capacity"
                )
            recipe = getattr(operation, "deferred_dispatch", None)
            if isinstance(recipe, MagicRouteDispatchRecipe) and (
                not operation.consumes
                or any(
                    buffer_kinds[buffer_id] != "magic_state"
                    for buffer_id in operation.consumes
                )
            ):
                raise ValueError(
                    f"Magic-route operation {operation.id} must consume only "
                    "installed magic_state buffers"
                )
            implementation_recipes = tuple(
                getattr(operation, "implementation_recipes", ())
            )
            has_implementation_recipes = (
                has_implementation_recipes or bool(implementation_recipes)
            )
            source_operations: set[tuple[int, int]] = set()
            for implementation_recipe in implementation_recipes:
                if implementation_recipe.invocation_id in recipe_invocation_ids:
                    raise ValueError(
                        "Injection recipe invocation ids must be globally unique "
                        "within one ExecutionPlan"
                    )
                recipe_invocation_ids.add(implementation_recipe.invocation_id)
                source_identity = (
                    implementation_recipe.source_layer_index,
                    implementation_recipe.source_operation_index,
                )
                if source_identity in source_operations:
                    raise ValueError(
                        f"Program operation {operation.id} repeats implementation "
                        f"recipe source {source_identity}"
                    )
                source_operations.add(source_identity)
                if implementation_recipe.compute_engine not in known_engines:
                    raise ValueError(
                        f"Injection recipe {implementation_recipe.invocation_id} "
                        "references an unknown compute engine"
                    )
                for stage in implementation_recipe.stages:
                    resource = stage.resource
                    if resource.buffer_id not in known_buffers:
                        raise ValueError(
                            f"Injection recipe {implementation_recipe.invocation_id} "
                            f"references unknown buffer {resource.buffer_id!r}"
                        )
                    if buffer_kinds[resource.buffer_id] != resource.token_kind:
                        raise ValueError(
                            f"Injection recipe {implementation_recipe.invocation_id} "
                            f"expects token kind {resource.token_kind!r} in "
                            f"buffer {resource.buffer_id!r}, found "
                            f"{buffer_kinds[resource.buffer_id]!r}"
                        )
                    if resource.quantity > buffer_capacities[resource.buffer_id]:
                        raise ValueError(
                            f"Injection recipe {implementation_recipe.invocation_id} "
                            "resource quantity exceeds installed capacity"
                        )
                templates = {
                    template.key: template
                    for template in operation.continuation_templates
                    if any(
                        member.recipe_invocation_id
                        == implementation_recipe.invocation_id
                        for member in template.recipe_members
                    )
                }
                for template in templates.values():
                    matching_members = tuple(
                        member
                        for member in template.recipe_members
                        if member.recipe_invocation_id
                        == implementation_recipe.invocation_id
                    )
                    if len(matching_members) != 1:
                        raise ValueError(
                            "Program-work template must reference each recipe at "
                            "most once"
                        )
                    member = matching_members[0]
                    unknown_template_buffers = (
                        set(template.consumes) | set(template.produces)
                    ) - known_buffers
                    unknown_template_engines = set(template.engines) - known_engines
                    if unknown_template_buffers:
                        raise ValueError(
                            "Program-work template references unknown buffers: "
                            f"{sorted(unknown_template_buffers)}"
                        )
                    if unknown_template_engines:
                        raise ValueError(
                            "Program-work template references unknown engines: "
                            f"{sorted(unknown_template_engines)}"
                        )
                    if any(
                        amount > buffer_capacities[buffer_id]
                        for buffer_id, amount in {
                            **dict(template.consumes),
                            **dict(template.produces),
                        }.items()
                    ):
                        raise ValueError(
                            "Program-work template buffer claims exceed installed capacity"
                        )
                    if any(
                        amount > engine_capacities[engine_id]
                        for engine_id, amount in template.engines.items()
                    ):
                        raise ValueError(
                            "Program-work template engine claims exceed installed capacity"
                        )
                    stage = implementation_recipe.stages[member.stage_index]
                    if template.step == "injection" and dict(template.consumes) != {
                        stage.resource.buffer_id: stage.resource.quantity
                    }:
                        raise ValueError(
                            "Injection work template must consume exactly its stage resource"
                        )
                    if template.step in {
                        "injection",
                        "measurement",
                        "correction",
                    } and dict(
                        template.engines
                    ) != {implementation_recipe.compute_engine: 1}:
                        raise ValueError(
                            "Compute continuation work must claim its recipe compute engine"
                        )
                    if template.step == "measurement" and (
                        template.consumes
                        or template.produces
                        or template.qubits
                        or template.metadata.get("gates")
                    ):
                        raise ValueError(
                            "Measurement work must be a timing-only resource-state "
                            "phase without logical gates or data-qubit claims"
                        )
                    if template.step == "correction":
                        expected_gates = {
                            stage.failure_correction: list(
                                implementation_recipe.qubits
                            )
                        }
                        if normalize_json(template.metadata.get("gates")) != expected_gates:
                            raise ValueError(
                                "Correction work template must materialize the recipe's "
                                "terminal logical correction"
                            )
            move_operands = getattr(operation, "move_operands", None)
            if (
                hasattr(operation, "move_operands")
                and operation.opcode.value == "MOVE_QUBITS"
                and move_operands is None
            ):
                raise ValueError(
                    f"Program MOVE {operation.id} requires typed MoveOperands"
                )
            if move_operands is not None:
                if (
                    operation.consumes
                    or operation.produces
                    or operation.forwards
                ):
                    raise ValueError(
                        f"Program MOVE {operation.id} moves logical-qubit "
                        "locations and cannot carry Resource-buffer transitions"
                    )
                if move_operands.entity_kind != MoveEntityKind.LOGICAL_QUBIT:
                    raise ValueError(
                        f"Program MOVE {operation.id} MoveOperands entity_kind "
                        "must be logical_qubit"
                    )
                moved_entities = {
                    f"q:{qubit}" for qubit in move_operands.source_slots
                }
                if moved_entities != set(operation.required_locations):
                    raise ValueError(
                        f"Program MOVE {operation.id} must declare every source "
                        "ArchitectureState location and no extra claims; the "
                        "keys must exactly match MoveOperands"
                    )
                if moved_entities != set(operation.completion_locations):
                    raise ValueError(
                        f"Program MOVE {operation.id} must declare every "
                        "destination ArchitectureState location and no extra "
                        "claims; the keys must exactly match MoveOperands"
                    )
                unchanged_entities = sorted(
                    entity
                    for entity in moved_entities
                    if operation.required_locations[entity]
                    == operation.completion_locations[entity]
                )
                if unchanged_entities:
                    raise ValueError(
                        f"Program MOVE {operation.id} must change each entity's "
                        "ArchitectureState location; unchanged="
                        f"{unchanged_entities}"
                    )
                for qubit, source_slot in move_operands.source_slots.items():
                    entity = f"q:{qubit}"
                    source_location = operation.required_locations[entity]
                    if not source_slot.startswith(
                        f"{source_location.rstrip('/')}/"
                    ):
                        raise ValueError(
                            f"Program MOVE {operation.id} source slot for "
                            f"{entity} must be nested under its required "
                            "ArchitectureState location"
                        )
                for qubit, destination_slot in (
                    move_operands.destination_slots.items()
                ):
                    entity = f"q:{qubit}"
                    destination_location = operation.completion_locations[
                        entity
                    ]
                    if not destination_slot.startswith(
                        f"{destination_location.rstrip('/')}/"
                    ):
                        raise ValueError(
                            f"Program MOVE {operation.id} destination slot for "
                            f"{entity} must be nested under its completion "
                            "ArchitectureState location"
                        )
            if len(set(operation.forwards.values())) != len(operation.forwards):
                raise ValueError(
                    "Each token-forwarding destination must have exactly one source"
                )
            for source, destination in operation.forwards.items():
                if source not in operation.consumes or destination not in operation.produces:
                    raise ValueError("Token forwarding must name consumed and produced buffers")
                if operation.consumes[source] != operation.produces[destination]:
                    raise ValueError("Token forwarding must preserve token count")
                if buffer_kinds[source] != buffer_kinds[destination]:
                    raise ValueError("Token forwarding must preserve token kind")
            if isinstance(recipe, ResourceMoveDispatchRecipe):
                source, destination = next(iter(operation.forwards.items()))
                expected_kind = recipe.entity_kind.value
                actual_kinds = {
                    buffer_kinds[source],
                    buffer_kinds[destination],
                }
                if actual_kinds != {expected_kind}:
                    raise ValueError(
                        f"Resource-move operation {operation.id} forwarded "
                        "buffer token kinds must match recipe entity_kind "
                        f"{expected_kind!r}"
                    )
        if (
            has_implementation_recipes
            and self.policy.runtime_injection_mode
            != RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
        ):
            raise ValueError(
                "ExecutionPlan implementation recipes require "
                "runtime_injection_mode='finite_state_injection_v1'"
            )
        object.__setattr__(self, "buffers", tuple(self.buffers))
        object.__setattr__(self, "engines", tuple(self.engines))
        object.__setattr__(
            self,
            "initial_locations",
            deep_freeze_json(self.initial_locations),
        )
        provenance = normalize_json(self.provenance)
        if not isinstance(provenance, dict):
            raise ValueError("ExecutionPlan provenance must be a mapping")
        from .components import (
            RuntimeComponentManifest,
            default_direct_runtime_component_manifest,
            json_type_strict_equal,
        )

        runtime_record = normalize_json(
            self.runtime_components
            if self.runtime_components
            else default_direct_runtime_component_manifest().to_dict()
        )
        manifest = RuntimeComponentManifest.from_dict(runtime_record)
        if self.runtime_components and not json_type_strict_equal(
            runtime_record,
            manifest.to_dict(),
        ):
            raise ValueError(
                "ExecutionPlan runtime_components must be the exact canonical "
                "RuntimeComponentManifest.to_dict() record"
            )
        has_magic_dispatch = any(
            isinstance(
                getattr(operation, "deferred_dispatch", None),
                MagicRouteDispatchRecipe,
            )
            for operation in operations
        )
        has_resource_dispatch = any(
            isinstance(
                getattr(operation, "deferred_dispatch", None),
                ResourceMoveDispatchRecipe,
            )
            for operation in operations
        )
        has_deferred_dispatch = has_magic_dispatch or has_resource_dispatch
        if (
            has_deferred_dispatch
            and manifest.manifest_hash
            == default_direct_runtime_component_manifest().manifest_hash
        ):
            raise ValueError(
                "ExecutionPlan deferred-dispatch recipes require a "
                "state-bound runtime-realizer component manifest"
            )
        recorded_hash = provenance.get("runtime_manifest_hash")
        if recorded_hash is not None and recorded_hash != manifest.manifest_hash:
            raise ValueError(
                "ExecutionPlan provenance runtime manifest hash mismatch"
            )
        provenance["runtime_manifest_hash"] = manifest.manifest_hash
        object.__setattr__(self, "provenance", deep_freeze_json(provenance))
        object.__setattr__(
            self,
            "runtime_components",
            deep_freeze_json(manifest.to_dict()),
        )
        # The plan is recursively immutable after normalization above.  Its
        # semantic hash therefore cannot change, while large plans may expose
        # it many times during evaluation and report construction.
        object.__setattr__(self, "_plan_hash", semantic_hash(self.semantic_dict()))

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EXECUTION_PLAN_SCHEMA_VERSION,
            "source": {"circuit_hash": self.circuit_hash},
            "architecture_hash": self.architecture_hash,
            "latency_profile_hash": self.latency_profile_hash,
            "policy": self.policy.to_dict(),
            "program_dag": self.program_dag.to_dict(),
            "resource_dag": self.resource_dag.to_dict(),
            "architectural_state": {
                "buffers": [item.to_dict() for item in self.buffers],
                "engines": [item.to_dict() for item in self.engines],
                "initial_locations": normalize_json(self.initial_locations),
            },
            "provenance": normalize_json(self.provenance),
            "runtime_components": normalize_json(self.runtime_components),
        }

    @property
    def plan_hash(self) -> str:
        return self._plan_hash

    def to_dict(self) -> dict[str, Any]:
        result = self.semantic_dict()
        result["plan_hash"] = self.plan_hash
        return result

    def to_json(self, *, indent: int | None = 2) -> str:
        return (
            json.dumps(
                self.to_dict(),
                indent=indent,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionPlan":
        _require_exact_json_wire(data, path="execution-plan")
        _require_fields(
            data,
            required=frozenset(
                {
                    "schema_version",
                    "source",
                    "architecture_hash",
                    "latency_profile_hash",
                    "policy",
                    "program_dag",
                    "resource_dag",
                    "architectural_state",
                    "provenance",
                    "runtime_components",
                    "plan_hash",
                }
            ),
            label="execution-plan",
        )
        if data.get("schema_version") != EXECUTION_PLAN_SCHEMA_VERSION:
            raise ValueError("Unsupported execution-plan schema")
        source = data["source"]
        state = data["architectural_state"]
        policy = data["policy"]
        _require_fields(
            source,
            required=frozenset({"circuit_hash"}),
            label="execution-plan source",
        )
        _require_fields(
            state,
            required=frozenset(
                {"buffers", "engines", "initial_locations"}
            ),
            label="execution-plan architectural_state",
        )
        for name in ("buffers", "engines"):
            if type(state[name]) is not list:
                raise TypeError(
                    f"Execution-plan architectural_state {name} must be an array"
                )
        if not isinstance(state["initial_locations"], Mapping):
            raise TypeError(
                "Execution-plan architectural_state initial_locations must be a mapping"
            )
        if any(
            type(key) is not str or type(value) is not str
            for key, value in state["initial_locations"].items()
        ):
            raise TypeError(
                "Execution-plan architectural_state initial_locations must map "
                "strings to strings"
            )
        if not isinstance(data["provenance"], Mapping):
            raise TypeError("Execution-plan provenance must be a mapping")
        if not isinstance(data["runtime_components"], Mapping):
            raise TypeError("Execution-plan runtime_components must be a mapping")
        if not data["runtime_components"]:
            raise ValueError(
                "Execution-plan runtime_components must contain a full "
                "canonical runtime manifest"
            )
        _require_fields(
            policy,
            required=frozenset(
                {
                    "magic_state_consumption",
                    "store_load_policy",
                    "resource_fill_policy",
                    "trace_level",
                    "runtime_injection_mode",
                    "selected_layers",
                    "seed",
                    "max_events",
                }
            ),
            label="execution-plan policy",
        )
        result = cls(
            circuit_hash=source["circuit_hash"],
            architecture_hash=data["architecture_hash"],
            latency_profile_hash=data["latency_profile_hash"],
            policy=EvaluationPolicy.from_dict(policy),
            program_dag=ProgramDAG.from_dict(data["program_dag"]),
            resource_dag=ResourceDAG.from_dict(data["resource_dag"]),
            buffers=tuple(BufferSpec.from_dict(item) for item in state.get("buffers", ())),
            engines=tuple(EngineSpec.from_dict(item) for item in state.get("engines", ())),
            initial_locations=state.get("initial_locations", {}),
            provenance=data.get("provenance", {}),
            runtime_components=data.get("runtime_components", {}),
        )
        if not _exact_json_equal(
            data["runtime_components"],
            normalize_json(result.runtime_components),
        ):
            raise ValueError(
                "Execution-plan runtime_components must use the exact "
                "canonical JSON wire shape"
            )
        unsigned = {key: value for key, value in data.items() if key != "plan_hash"}
        if not _exact_json_equal(unsigned, result.semantic_dict()):
            raise ValueError(
                "Execution plan must use the exact canonical JSON wire shape"
            )
        if type(data["plan_hash"]) is not str:
            raise TypeError("Execution-plan plan_hash must be a string")
        if data["plan_hash"] != result.plan_hash:
            raise ValueError("Execution-plan hash does not match content")
        return result

    @classmethod
    def from_json(cls, text: str) -> "ExecutionPlan":
        if type(text) is not str:
            raise TypeError("Execution-plan JSON document must be a string")

        def reject_constant(value: str) -> None:
            raise ValueError(
                f"Non-finite JSON constant is not allowed in an execution plan: {value}"
            )

        def reject_duplicate_keys(
            pairs: list[tuple[str, Any]],
        ) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(
                        f"Duplicate JSON object key in execution plan: {key!r}"
                    )
                result[key] = value
            return result

        data = json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
        if not isinstance(data, Mapping):
            raise ValueError("Execution-plan JSON must contain an object")
        return cls.from_dict(data)
