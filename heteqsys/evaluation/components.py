"""Pure runtime-component contracts around the transactional Event Engine.

The objects in this module are advanced/internal extension seams.  Components
receive detached immutable requests and may propose work, but they never receive
``ArchitectureState`` or any Engine-owned frontier, heap, RNG, or trace sink.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from heteqsys.architecture.isa import (
    ArchitectureOpcode,
    DeferredDispatchRecipe,
    MagicRouteDispatchRecipe,
    MoveOperands,
    ResourceMoveDispatchRecipe,
)
from heteqsys.architecture.recipes import (
    InjectionRecipe,
    ProgramWorkLineage,
)
from heteqsys.architecture.state import (
    StateReservation,
    StateSnapshot,
    TentativeBinding,
)
from heteqsys.schema import deep_freeze_json, normalize_json, semantic_hash


RUNTIME_COMPONENT_SCHEMA_VERSION = "arqsim.runtime-component.v3"
RUNTIME_MANIFEST_SCHEMA_VERSION = "arqsim.runtime-manifest.v3"

RUNTIME_COMPONENT_ROLES = (
    "runtime_realizer",
    "scheduler",
    "execution_backend",
    "outcome_model",
)

# A runtime compiler artifact is an observational receipt. State claims,
# dispatch identity, and Engine accounting stay in typed fields and therefore
# cannot be smuggled back into execution through an untyped artifact.
_COMPILER_ARTIFACT_CONTROL_FIELDS = frozenset(
    {
        "attempted_outputs",
        "batch_amount",
        "binding",
        "buffered_outputs",
        "candidate_id",
        "compiler_binding_time",
        "completion_locations",
        "consumed_slots",
        "consumed_tokens",
        "consumes",
        "destination_slots",
        "discarded_outputs",
        "dispatch_policy",
        "engines",
        "forwards",
        "layer",
        "mapping",
        "moved_tokens",
        "operation",
        "operation_indices",
        "plane",
        "produced_slots",
        "produces",
        "program_ready_s",
        "protocol",
        "qubits",
        "required_locations",
        "resource_wait_s",
        "route_binding_status",
        "runtime_move_compilation",
        "runtime_route_request",
        "source_id",
        "source_slots",
        "target_links",
        "target_modules",
    }
)


class RuntimeComponentError(ValueError):
    """Raised when a component descriptor, request, or result is invalid."""


class UnsupportedRuntimeComponentError(RuntimeComponentError):
    """Raised when a serialized selection has no built-in implementation."""


def _strict_frozen_json(value: Any, *, label: str) -> Any:
    try:
        # deep_freeze_json already normalizes and performs the same strict
        # finite-JSON validation before freezing.  Avoid repeating both full
        # traversals for every runtime candidate and completion record.
        return deep_freeze_json(value)
    except ValueError as exc:
        raise RuntimeComponentError(f"{label} must be finite JSON data") from exc


def _require_exact_json_wire(value: Any, *, path: str) -> None:
    """Reject Python aliases at the runtime-component codec boundary."""

    value_type = type(value)
    if value_type is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise RuntimeComponentError(
                    f"{path} JSON object keys must be strings"
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
            raise RuntimeComponentError(
                f"{path} must contain only finite JSON numbers"
            )
        return
    if isinstance(value, Mapping):
        raise RuntimeComponentError(
            f"{path} JSON objects must be plain dictionaries"
        )
    if isinstance(value, (tuple, list)):
        raise RuntimeComponentError(f"{path} JSON arrays must be plain lists")
    raise RuntimeComponentError(
        f"{path} contains a non-JSON value of type {value_type.__name__}"
    )


def _require_exact_fields(
    data: Mapping[str, Any],
    *,
    fields: frozenset[str],
    label: str,
) -> None:
    missing = fields - set(data)
    if missing:
        raise RuntimeComponentError(f"Missing {label} fields: {sorted(missing)}")
    unknown = set(data) - fields
    if unknown:
        raise RuntimeComponentError(f"Unknown {label} fields: {sorted(unknown)}")


def json_type_strict_equal(left: Any, right: Any) -> bool:
    """Compare JSON-shaped values without Python's bool/int aliasing."""

    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            json_type_strict_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, (tuple, list)) and isinstance(right, (tuple, list)):
        return len(left) == len(right) and all(
            json_type_strict_equal(a, b) for a, b in zip(left, right)
        )
    return type(left) is type(right) and left == right


def _exact_nonnegative_float(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise RuntimeComponentError(f"{label} cannot be boolean")
    if type(value) not in {int, float}:
        raise RuntimeComponentError(f"{label} must be an exact JSON number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise RuntimeComponentError(f"{label} must be finite/non-negative")
    return result


@dataclass(frozen=True)
class RuntimeComponentDescriptor:
    """Stable, self-reported semantic identity for one trusted extension."""

    role: str
    component_id: str
    contract_version: str = "1"
    implementation_version: str = "1"
    effective_config: Mapping[str, Any] = field(default_factory=dict)
    provider: str = "arqsim"

    def __post_init__(self) -> None:
        string_fields = {
            "role": self.role,
            "component_id": self.component_id,
            "contract_version": self.contract_version,
            "implementation_version": self.implementation_version,
            "provider": self.provider,
        }
        for name, value in string_fields.items():
            if type(value) is not str or not value or value != value.strip():
                raise RuntimeComponentError(
                    f"Runtime component {name} must be a canonical non-empty string"
                )
        role = self.role
        component_id = self.component_id
        contract_version = self.contract_version
        implementation_version = self.implementation_version
        provider = self.provider
        if role not in {*RUNTIME_COMPONENT_ROLES, "event_engine"}:
            raise RuntimeComponentError(f"Unknown runtime component role: {role!r}")
        if not all(
            (component_id, contract_version, implementation_version, provider)
        ):
            raise RuntimeComponentError("Runtime component identity cannot be empty")
        if not isinstance(self.effective_config, Mapping):
            raise RuntimeComponentError(
                "Runtime component effective_config must be a mapping"
            )
        object.__setattr__(self, "role", role)
        object.__setattr__(self, "component_id", component_id)
        object.__setattr__(self, "contract_version", contract_version)
        object.__setattr__(self, "implementation_version", implementation_version)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(
            self,
            "effective_config",
            _strict_frozen_json(
                self.effective_config,
                label=f"{role} effective_config",
            ),
        )

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_COMPONENT_SCHEMA_VERSION,
            "role": self.role,
            "component_id": self.component_id,
            "contract_version": self.contract_version,
            "implementation_version": self.implementation_version,
            "effective_config": normalize_json(self.effective_config),
            "provider": self.provider,
        }

    @property
    def component_hash(self) -> str:
        return semantic_hash(self.semantic_dict())

    def to_dict(self) -> dict[str, Any]:
        result = self.semantic_dict()
        result["component_hash"] = self.component_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RuntimeComponentDescriptor":
        _require_exact_json_wire(data, path="runtime-component")
        if type(data) is not dict:
            raise RuntimeComponentError(
                "Runtime component descriptor must be a plain dictionary"
            )
        fields = frozenset(
            {
            "schema_version",
            "role",
            "component_id",
            "contract_version",
            "implementation_version",
            "effective_config",
            "provider",
            "component_hash",
            }
        )
        _require_exact_fields(data, fields=fields, label="runtime-component")
        if data.get("schema_version") != RUNTIME_COMPONENT_SCHEMA_VERSION:
            raise RuntimeComponentError(
                "Unsupported runtime-component schema: "
                f"{data.get('schema_version')!r}"
            )
        result = cls(
            role=data["role"],
            component_id=data["component_id"],
            contract_version=data["contract_version"],
            implementation_version=data["implementation_version"],
            effective_config=data["effective_config"],
            provider=data["provider"],
        )
        expected = data["component_hash"]
        if type(expected) is not str or expected != result.component_hash:
            raise RuntimeComponentError(
                f"Runtime component hash mismatch for {result.role}"
            )
        if not json_type_strict_equal(data, result.to_dict()):
            raise RuntimeComponentError(
                "Runtime component descriptor must be its exact canonical record"
            )
        return result


def default_event_engine_descriptor() -> RuntimeComponentDescriptor:
    return RuntimeComponentDescriptor(
        role="event_engine",
        component_id="event_engine.transactional_dynamic_frontier.v2",
        effective_config={
            "timestamp_phases": [
                "complete_all",
                "program_plane_first",
                "same_time_fixed_point",
                "advance",
            ],
            "dispatch": "eager_when_ready",
            "program_horizon": "stop_without_resource_drain",
            "commit_granularity": "single_candidate",
            "resource_fitting": "typed_dispatch_receipt_v1",
            "program_continuations": "finite_injection_recipe_v1",
            "continuation_order": "activation_order_then_monotonic_schedule_id",
        },
    )


@dataclass(frozen=True)
class RuntimeComponentManifest:
    """Canonical identity of the Event Engine kernel and four runtime roles."""

    components: Mapping[str, RuntimeComponentDescriptor]
    event_engine: RuntimeComponentDescriptor = field(
        default_factory=default_event_engine_descriptor
    )

    def __post_init__(self) -> None:
        if not isinstance(self.components, Mapping) or any(
            type(role) is not str for role in self.components
        ):
            raise RuntimeComponentError(
                "Runtime manifest components must map exact string roles"
            )
        components = dict(self.components)
        if set(components) != set(RUNTIME_COMPONENT_ROLES):
            raise RuntimeComponentError(
                "Runtime manifest must name exactly these roles: "
                f"{list(RUNTIME_COMPONENT_ROLES)}"
            )
        for role, descriptor in components.items():
            if not isinstance(descriptor, RuntimeComponentDescriptor):
                raise RuntimeComponentError(
                    f"Runtime manifest entry {role!r} is not a descriptor"
                )
            if descriptor.role != role:
                raise RuntimeComponentError(
                    f"Runtime manifest role mismatch: {role!r} != "
                    f"{descriptor.role!r}"
                )
        if not isinstance(self.event_engine, RuntimeComponentDescriptor):
            raise RuntimeComponentError("Manifest kernel must be a descriptor")
        if self.event_engine.role != "event_engine":
            raise RuntimeComponentError("Manifest kernel must use event_engine role")
        object.__setattr__(self, "components", MappingProxyType(components))

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_MANIFEST_SCHEMA_VERSION,
            "event_engine": self.event_engine.to_dict(),
            "components": {
                role: self.components[role].to_dict()
                for role in RUNTIME_COMPONENT_ROLES
            },
        }

    @property
    def manifest_hash(self) -> str:
        return semantic_hash(self.semantic_dict())

    def to_dict(self) -> dict[str, Any]:
        result = self.semantic_dict()
        result["manifest_hash"] = self.manifest_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RuntimeComponentManifest":
        _require_exact_json_wire(data, path="runtime-manifest")
        if type(data) is not dict:
            raise RuntimeComponentError(
                "Runtime manifest must be a plain dictionary"
            )
        fields = frozenset(
            {"schema_version", "event_engine", "components", "manifest_hash"}
        )
        _require_exact_fields(data, fields=fields, label="runtime-manifest")
        if data.get("schema_version") != RUNTIME_MANIFEST_SCHEMA_VERSION:
            raise RuntimeComponentError(
                "Unsupported runtime-manifest schema: "
                f"{data.get('schema_version')!r}"
            )
        raw_components = data["components"]
        if type(raw_components) is not dict or set(raw_components) != set(
            RUNTIME_COMPONENT_ROLES
        ):
            raise RuntimeComponentError(
                "Runtime manifest must contain exactly the four canonical roles"
            )
        event_engine = RuntimeComponentDescriptor.from_dict(data["event_engine"])
        components = {
            role: RuntimeComponentDescriptor.from_dict(raw_components[role])
            for role in RUNTIME_COMPONENT_ROLES
        }
        result = cls(components=components, event_engine=event_engine)
        expected = data["manifest_hash"]
        if type(expected) is not str or expected != result.manifest_hash:
            raise RuntimeComponentError("Runtime manifest hash does not match content")
        if not json_type_strict_equal(data, result.to_dict()):
            raise RuntimeComponentError(
                "Runtime manifest must be its exact canonical record"
            )
        return result


def default_runtime_component_manifest() -> RuntimeComponentManifest:
    """Return the fully materialized minimal-runtime component selection."""

    return RuntimeComponentManifest(
        components={
            "runtime_realizer": RuntimeComponentDescriptor(
                "runtime_realizer",
                "runtime_realizer.state_bound.v2",
                effective_config={
                    "binding": "fifo_first_free",
                    "deferred_program": "joint_magic_route",
                    "deferred_resource": "state_bound_resource_move",
                    "continuation": "finite_injection_recipe_v1",
                },
            ),
            "scheduler": RuntimeComponentDescriptor(
                "scheduler",
                "scheduler.program_first_eager.v1",
                effective_config={
                    "program_order": "ascending_instruction_id",
                    "resource_order": "resource_dag_insertion_order",
                },
            ),
            "execution_backend": RuntimeComponentDescriptor(
                "execution_backend",
                "backend.profile.v1",
                effective_config={"timing_source": "candidate_implementation"},
            ),
            "outcome_model": RuntimeComponentDescriptor(
                "outcome_model",
                "outcome.seeded_bernoulli.v1",
                effective_config={
                    "distribution": "independent_bernoulli_half",
                    "seed_identity": "program_work_lineage",
                },
            ),
        }
    )


def default_direct_runtime_component_manifest() -> RuntimeComponentManifest:
    """Built-in manifest for plans with no deferred realization recipe."""

    canonical = default_runtime_component_manifest()
    components = dict(canonical.components)
    components["runtime_realizer"] = RuntimeComponentDescriptor(
        "runtime_realizer",
        "runtime_realizer.direct_fifo_injection.v2",
        effective_config={
            "binding": "fifo_first_free",
            "continuation": "finite_injection_recipe_v1",
        },
    )
    return RuntimeComponentManifest(
        components=components,
        event_engine=canonical.event_engine,
    )


@dataclass(frozen=True)
class RuntimeOperationView:
    """Detached immutable superset view of a Program or Resource operation."""

    id: int | str
    plane: str
    opcode: ArchitectureOpcode
    duration_s: float
    predecessor_ids: tuple[int, ...] = ()
    layer_index: int | None = None
    qubits: tuple[int, ...] = ()
    consumes: Mapping[str, int] = field(default_factory=dict)
    produces: Mapping[str, int] = field(default_factory=dict)
    forwards: Mapping[str, str] = field(default_factory=dict)
    engines: Mapping[str, int] = field(default_factory=dict)
    required_locations: Mapping[str, str] = field(default_factory=dict)
    completion_locations: Mapping[str, str] = field(default_factory=dict)
    target_modules: tuple[str, ...] = ()
    target_links: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    parallelism: int = 1
    dispatch_policy: str = "single"
    output_overflow_policy: str = "block"
    protocol: str = "default"
    move_operands: MoveOperands | None = None
    deferred_dispatch: DeferredDispatchRecipe | None = None
    implementation_recipes: tuple[InjectionRecipe, ...] = ()

    def __post_init__(self) -> None:
        if self.plane not in {"program", "resource"}:
            raise RuntimeComponentError(f"Unknown operation plane: {self.plane!r}")
        duration = _exact_nonnegative_float(
            self.duration_s,
            label="Operation duration",
        )
        object.__setattr__(self, "duration_s", duration)
        for name in (
            "consumes",
            "produces",
            "forwards",
            "engines",
            "required_locations",
            "completion_locations",
            "metadata",
        ):
            object.__setattr__(
                self,
                name,
                _strict_frozen_json(getattr(self, name), label=f"operation.{name}"),
            )
        object.__setattr__(self, "predecessor_ids", tuple(self.predecessor_ids))
        object.__setattr__(self, "qubits", tuple(self.qubits))
        object.__setattr__(self, "target_modules", tuple(self.target_modules))
        object.__setattr__(self, "target_links", tuple(self.target_links))
        if self.move_operands is not None and not isinstance(
            self.move_operands,
            MoveOperands,
        ):
            raise RuntimeComponentError("operation.move_operands must be typed")
        if self.deferred_dispatch is not None and not isinstance(
            self.deferred_dispatch,
            (MagicRouteDispatchRecipe, ResourceMoveDispatchRecipe),
        ):
            raise RuntimeComponentError("operation.deferred_dispatch must be typed")
        recipes = tuple(self.implementation_recipes)
        if any(not isinstance(recipe, InjectionRecipe) for recipe in recipes):
            raise RuntimeComponentError(
                "operation.implementation_recipes must be typed"
            )
        object.__setattr__(self, "implementation_recipes", recipes)

    @classmethod
    def from_operation(cls, operation: Any, *, plane: str) -> "RuntimeOperationView":
        return cls(
            id=operation.id,
            plane=plane,
            opcode=ArchitectureOpcode(operation.opcode),
            duration_s=operation.duration_s,
            predecessor_ids=tuple(getattr(operation, "predecessor_ids", ())),
            layer_index=getattr(operation, "layer_index", None),
            qubits=tuple(getattr(operation, "qubits", ())),
            consumes=operation.consumes,
            produces=operation.produces,
            forwards=operation.forwards,
            engines=operation.engines,
            required_locations=getattr(operation, "required_locations", {}),
            completion_locations=getattr(operation, "completion_locations", {}),
            target_modules=tuple(getattr(operation, "target_modules", ())),
            target_links=tuple(getattr(operation, "target_links", ())),
            metadata=operation.metadata,
            parallelism=int(getattr(operation, "parallelism", 1)),
            dispatch_policy=str(getattr(operation, "dispatch_policy", "single")),
            output_overflow_policy=str(
                getattr(operation, "output_overflow_policy", "block")
            ),
            protocol=str(getattr(operation, "protocol", "default")),
            move_operands=getattr(operation, "move_operands", None),
            deferred_dispatch=getattr(operation, "deferred_dispatch", None),
            implementation_recipes=tuple(
                getattr(operation, "implementation_recipes", ())
            ),
        )


@dataclass(frozen=True)
class CandidateImplementation:
    """One realized but uncommitted operation.

    The operation owns the plane, source identity, and abstract claims.  The
    binding owns concrete state claims.  A local compiler may only select the
    duration and attach an observational artifact; it cannot restate either
    authority in a metadata dictionary.
    """

    candidate_id: str
    operation: RuntimeOperationView
    binding: TentativeBinding
    duration_s: float
    compiler_artifact: Mapping[str, Any] = field(default_factory=dict)
    lineage: ProgramWorkLineage | None = None
    expected_measurements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.candidate_id) is not str or not self.candidate_id.strip():
            raise RuntimeComponentError("candidate_id must be a non-empty string")
        if not isinstance(self.operation, RuntimeOperationView):
            raise RuntimeComponentError("Candidate operation must be a runtime view")
        if not isinstance(self.binding, TentativeBinding):
            raise RuntimeComponentError("Candidate binding must be tentative")
        if self.operation.plane == "program":
            if not isinstance(self.lineage, ProgramWorkLineage):
                raise RuntimeComponentError(
                    "Program candidates require typed ProgramWorkLineage"
                )
        elif self.lineage is not None:
            raise RuntimeComponentError("Resource candidates cannot carry Program lineage")
        measurements = tuple(self.expected_measurements)
        if any(type(value) is not str or not value for value in measurements):
            raise RuntimeComponentError(
                "Expected measurement registers must be non-empty strings"
            )
        if len(set(measurements)) != len(measurements):
            raise RuntimeComponentError("Expected measurement registers must be unique")
        duration = _exact_nonnegative_float(
            self.duration_s,
            label="Candidate duration",
        )
        if not isinstance(self.compiler_artifact, Mapping):
            raise RuntimeComponentError("Candidate compiler_artifact must be a mapping")
        duplicated_controls = sorted(
            set(self.compiler_artifact) & _COMPILER_ARTIFACT_CONTROL_FIELDS
        )
        if duplicated_controls:
            raise RuntimeComponentError(
                "Compiler artifact cannot restate typed/Engine control fields: "
                f"{duplicated_controls}"
            )
        object.__setattr__(self, "duration_s", duration)
        object.__setattr__(
            self,
            "compiler_artifact",
            _strict_frozen_json(
                self.compiler_artifact,
                label="candidate compiler artifact",
            ),
        )
        object.__setattr__(self, "expected_measurements", measurements)


@dataclass(frozen=True)
class RealizationRequest:
    """Immutable ready-operation context presented to one runtime realizer."""

    snapshot: StateSnapshot
    candidate_id: str
    operation: RuntimeOperationView
    base_duration_s: float
    now_s: float
    instance: int | None = None
    batch_amount: int = 1
    ready_program: tuple[RuntimeOperationView, ...] = ()
    lineage: ProgramWorkLineage | None = None

    def __post_init__(self) -> None:
        if type(self.candidate_id) is not str or not self.candidate_id.strip():
            raise RuntimeComponentError("candidate_id must be a non-empty string")
        if not isinstance(self.snapshot, StateSnapshot):
            raise RuntimeComponentError("Realization snapshot must be typed")
        if not isinstance(self.operation, RuntimeOperationView):
            raise RuntimeComponentError("Realization operation must be a runtime view")
        duration = _exact_nonnegative_float(
            self.base_duration_s,
            label="Base duration",
        )
        now_s = _exact_nonnegative_float(self.now_s, label="Realization time")
        if self.instance is not None and (
            type(self.instance) is not int or self.instance < 0
        ):
            raise RuntimeComponentError(
                "Realization instance must be None or a non-negative integer"
            )
        if isinstance(self.batch_amount, bool) or not isinstance(
            self.batch_amount, int
        ) or self.batch_amount <= 0:
            raise RuntimeComponentError("batch_amount must be a positive integer")
        if type(self.ready_program) is not tuple or any(
            not isinstance(operation, RuntimeOperationView)
            for operation in self.ready_program
        ):
            raise RuntimeComponentError(
                "ready_program must be a tuple of RuntimeOperationView values"
            )
        if self.operation.plane == "program":
            if not isinstance(self.lineage, ProgramWorkLineage):
                raise RuntimeComponentError(
                    "Program realization requests require typed lineage"
                )
        elif self.lineage is not None:
            raise RuntimeComponentError(
                "Resource realization requests cannot carry Program lineage"
            )
        object.__setattr__(self, "base_duration_s", duration)
        object.__setattr__(self, "now_s", now_s)


@dataclass(frozen=True)
class DeferredDispatchRequest:
    """One immutable recipe paired with its tentative state binding.

    This is the only input understood by built-in dispatch-time compiler
    callbacks.  The recipe owns static unresolved compiler facts; the three
    slot/token mappings come exclusively from ``TentativeBinding``.
    """

    operation: RuntimeOperationView
    recipe: DeferredDispatchRecipe
    consumed_tokens: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    consumed_slots: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    produced_slots: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.operation.deferred_dispatch != self.recipe:
            raise RuntimeComponentError(
                "Deferred-dispatch request recipe disagrees with its operation"
            )
        if not isinstance(
            self.recipe,
            (MagicRouteDispatchRecipe, ResourceMoveDispatchRecipe),
        ):
            raise RuntimeComponentError("Deferred-dispatch request needs a typed recipe")
        for name in ("consumed_tokens", "consumed_slots", "produced_slots"):
            raw = getattr(self, name)
            if not isinstance(raw, Mapping) or any(
                type(key) is not str
                or type(values) is not tuple
                or any(type(value) is not str for value in values)
                for key, values in raw.items()
            ):
                raise RuntimeComponentError(
                    f"Deferred-dispatch {name} must map strings to string tuples"
                )
            object.__setattr__(
                self,
                name,
                MappingProxyType(
                    {
                        key: tuple(values)
                        for key, values in sorted(raw.items())
                    }
                ),
            )

    @classmethod
    def from_candidate(
        cls,
        candidate: CandidateImplementation,
    ) -> "DeferredDispatchRequest":
        recipe = candidate.operation.deferred_dispatch
        if recipe is None:
            raise RuntimeComponentError(
                "Operation has no deferred-dispatch recipe"
            )
        binding = candidate.binding
        return cls(
            operation=candidate.operation,
            recipe=recipe,
            consumed_tokens=binding.consumed_tokens,
            consumed_slots=binding.consumed_slots,
            produced_slots=binding.produced_slots,
        )


@dataclass(frozen=True)
class BackendRequest:
    candidate: CandidateImplementation
    now_s: float

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, CandidateImplementation):
            raise RuntimeComponentError("Backend candidate must be realized")
        object.__setattr__(
            self,
            "now_s",
            _exact_nonnegative_float(self.now_s, label="Backend time"),
        )


@dataclass(frozen=True)
class PreparedExecution:
    duration_s: float
    artifact: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        duration = _exact_nonnegative_float(
            self.duration_s,
            label="Backend duration",
        )
        object.__setattr__(self, "duration_s", duration)
        object.__setattr__(
            self,
            "artifact",
            _strict_frozen_json(self.artifact, label="backend artifact"),
        )


@dataclass(frozen=True)
class ProgramSchedulingRequest:
    ready_ids: tuple[int, ...]
    snapshot: StateSnapshot
    now_s: float


@dataclass(frozen=True)
class ResourceSchedulingRequest:
    process_ids: tuple[str, ...]
    snapshot: StateSnapshot
    now_s: float


@dataclass(frozen=True)
class CompletionRequest:
    snapshot: StateSnapshot
    operation: RuntimeOperationView
    reservation: StateReservation
    candidate_id: str
    event_id: int
    start_s: float
    end_s: float
    backend_artifact: Mapping[str, Any] = field(default_factory=dict)
    outcome_seed: int = 0
    lineage: ProgramWorkLineage | None = None
    expected_measurements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "backend_artifact",
            _strict_frozen_json(
                self.backend_artifact,
                label="completion backend artifact",
            ),
        )
        object.__setattr__(self, "outcome_seed", int(self.outcome_seed))
        measurements = tuple(self.expected_measurements)
        if any(type(value) is not str or not value for value in measurements):
            raise RuntimeComponentError(
                "Completion expected_measurements must be string ids"
            )
        if self.operation.plane == "program" and not isinstance(
            self.lineage, ProgramWorkLineage
        ):
            raise RuntimeComponentError("Program completion needs typed lineage")
        if self.operation.plane == "resource" and self.lineage is not None:
            raise RuntimeComponentError("Resource completion cannot carry lineage")
        object.__setattr__(self, "expected_measurements", measurements)


@dataclass(frozen=True)
class MeasurementOutcome:
    register_id: str
    bit: int

    def __post_init__(self) -> None:
        if type(self.register_id) is not str or not self.register_id:
            raise RuntimeComponentError(
                "Measurement register_id must be a non-empty string"
            )
        if type(self.bit) is not int or self.bit not in {0, 1}:
            raise RuntimeComponentError("Measurement outcome must be bit 0 or 1")

    def to_dict(self) -> dict[str, Any]:
        return {"register_id": self.register_id, "bit": self.bit}


@dataclass(frozen=True)
class EventOutcome:
    measurements: tuple[MeasurementOutcome, ...] = ()
    values: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        measurements = tuple(self.measurements)
        if any(not isinstance(item, MeasurementOutcome) for item in measurements):
            raise RuntimeComponentError(
                "EventOutcome measurements must be typed MeasurementOutcome values"
            )
        register_ids = [item.register_id for item in measurements]
        if len(register_ids) != len(set(register_ids)):
            raise RuntimeComponentError("EventOutcome repeats a measurement register")
        object.__setattr__(self, "measurements", measurements)
        object.__setattr__(
            self,
            "values",
            _strict_frozen_json(self.values, label="outcome values"),
        )
        object.__setattr__(
            self,
            "metadata",
            _strict_frozen_json(self.metadata, label="outcome metadata"),
        )

    def to_state_payload(self) -> dict[str, Any]:
        if not self.measurements and not self.values and not self.metadata:
            return {}
        return {
            "measurements": [item.to_dict() for item in self.measurements],
            "values": normalize_json(self.values),
            "metadata": normalize_json(self.metadata),
        }

    def bit_for(self, register_id: str) -> int:
        matches = [item.bit for item in self.measurements if item.register_id == register_id]
        if len(matches) != 1:
            raise RuntimeComponentError(
                f"Outcome does not contain exactly one bit for {register_id!r}"
            )
        return matches[0]


@dataclass(frozen=True)
class ContinuationRequest:
    lineage: ProgramWorkLineage
    recipe: InjectionRecipe
    stage_index: int
    outcome_bit: int

    def __post_init__(self) -> None:
        if not isinstance(self.lineage, ProgramWorkLineage):
            raise RuntimeComponentError("Continuation needs typed Program lineage")
        if not isinstance(self.recipe, InjectionRecipe):
            raise RuntimeComponentError("Continuation needs a typed InjectionRecipe")
        if type(self.stage_index) is not int or not (
            0 <= self.stage_index < len(self.recipe.stages)
        ):
            raise RuntimeComponentError("Continuation stage is outside its recipe")
        if type(self.outcome_bit) is not int or self.outcome_bit not in {0, 1}:
            raise RuntimeComponentError("Continuation outcome must be bit 0 or 1")


@dataclass(frozen=True)
class ContinuationDecision:
    kind: str
    next_stage: int | None = None
    correction: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {
            "complete",
            "next_stage",
            "materialized_logical_correction",
        }:
            raise RuntimeComponentError(f"Unknown continuation kind: {self.kind!r}")
        if self.kind == "complete" and (
            self.next_stage is not None or self.correction is not None
        ):
            raise RuntimeComponentError("Complete continuation cannot carry a target")
        if self.kind == "next_stage" and (
            type(self.next_stage) is not int or self.correction is not None
        ):
            raise RuntimeComponentError("next_stage continuation needs one stage")
        if self.kind == "materialized_logical_correction" and (
            type(self.correction) is not str
            or not self.correction
            or self.next_stage is not None
        ):
            raise RuntimeComponentError(
                "materialized_logical_correction continuation needs one logical gate"
            )


class RuntimeRealizer(Protocol):
    descriptor: RuntimeComponentDescriptor

    def realize(self, request: RealizationRequest) -> CandidateImplementation: ...

    def continue_after(self, request: ContinuationRequest) -> ContinuationDecision: ...


class RuntimeScheduler(Protocol):
    descriptor: RuntimeComponentDescriptor

    def order_program(self, request: ProgramSchedulingRequest) -> Sequence[int]: ...

    def order_resources(self, request: ResourceSchedulingRequest) -> Sequence[str]: ...


class ProfileExecutionBackend(Protocol):
    descriptor: RuntimeComponentDescriptor

    def prepare(self, request: BackendRequest) -> PreparedExecution: ...


class OutcomeModel(Protocol):
    descriptor: RuntimeComponentDescriptor

    def resolve(self, request: CompletionRequest) -> EventOutcome: ...


def _patch_candidate(
    candidate: CandidateImplementation,
    result: Mapping[str, Any],
    *,
    owner: str,
) -> CandidateImplementation:
    if not isinstance(result, Mapping):
        raise RuntimeComponentError(f"{owner} must return a mapping")
    normalized = normalize_json(result)
    _strict_frozen_json(normalized, label=f"{owner} result")
    duration = candidate.duration_s
    if "duration_s" in normalized:
        raw_duration = normalized["duration_s"]
        duration = _exact_nonnegative_float(
            raw_duration,
            label=f"{owner} duration",
        )
    artifact = {
        key: value
        for key, value in normalized.items()
        if key != "duration_s"
    }
    return CandidateImplementation(
        candidate_id=candidate.candidate_id,
        operation=candidate.operation,
        binding=candidate.binding,
        duration_s=duration,
        compiler_artifact=artifact,
        lineage=candidate.lineage,
        expected_measurements=candidate.expected_measurements,
    )


@dataclass(frozen=True)
class StateBoundRuntimeRealizer:
    """Built-in joint binding/compilation boundary for both operation planes."""

    descriptor: RuntimeComponentDescriptor
    program_callback: Callable[
        [DeferredDispatchRequest], Mapping[str, Any]
    ] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    resource_callback: Callable[
        [DeferredDispatchRequest], Mapping[str, Any]
    ] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def realize(self, request: RealizationRequest) -> CandidateImplementation:
        if request.lineage is not None and request.lineage.step == "injection":
            matching = tuple(
                recipe
                for recipe in request.operation.implementation_recipes
                if recipe.invocation_id == request.lineage.recipe_invocation_id
                and recipe.recipe_id == request.lineage.recipe_id
            )
            if len(matching) != 1 or request.lineage.stage_index is None:
                raise RuntimeComponentError(
                    "Injection work lineage must identify exactly one recipe stage"
                )
            expected_measurements = (
                matching[0].measurement_register(request.lineage.stage_index),
            )
        elif request.lineage is not None and request.lineage.step == "source":
            expected_measurements = tuple(
                recipe.measurement_register(0)
                for recipe in request.operation.implementation_recipes
            )
        else:
            expected_measurements = ()
        candidate = CandidateImplementation(
            candidate_id=request.candidate_id,
            operation=request.operation,
            binding=request.snapshot.propose(request.operation),
            duration_s=request.base_duration_s,
            lineage=request.lineage,
            expected_measurements=expected_measurements,
        )
        recipe = request.operation.deferred_dispatch
        if recipe is None:
            return candidate
        if isinstance(recipe, MagicRouteDispatchRecipe):
            if request.operation.plane != "program":
                raise RuntimeComponentError(
                    "Magic-route recipe must belong to a Program operation"
                )
            callback = self.program_callback
            owner = f"Program realizer for {request.candidate_id}"
        elif isinstance(recipe, ResourceMoveDispatchRecipe):
            if request.operation.plane != "resource":
                raise RuntimeComponentError(
                    "Resource-move recipe must belong to a Resource operation"
                )
            callback = self.resource_callback
            owner = f"Resource realizer for {request.candidate_id}"
        else:  # pragma: no cover - RuntimeOperationView already closes the union.
            raise RuntimeComponentError(
                "Runtime realizer received an unsupported deferred recipe"
            )
        if callback is None:
            raise RuntimeComponentError(
                f"{owner} has no trusted lowering callback"
            )
        result = callback(DeferredDispatchRequest.from_candidate(candidate))
        return _patch_candidate(
            candidate,
            result,
            owner=owner,
        )

    def continue_after(
        self,
        request: ContinuationRequest,
    ) -> ContinuationDecision:
        stage = request.recipe.stages[request.stage_index]
        if request.outcome_bit == 0:
            return ContinuationDecision("complete")
        if stage.failure_next_stage is not None:
            return ContinuationDecision(
                "next_stage",
                next_stage=stage.failure_next_stage,
            )
        assert stage.failure_correction is not None
        return ContinuationDecision(
            "materialized_logical_correction",
            correction=stage.failure_correction,
        )


def validate_builtin_component_source_context(
    components: "RuntimeComponentSet",
    *,
    circuit_hash: str,
    architecture_hash: str,
    compiler_spec_hash: Any,
    latency_profile_hash: str,
) -> None:
    """Bind official state-dependent callbacks to one plan's authorities.

    Runtime component descriptors identify algorithms.  The input objects
    captured by a particular built-in callback are execution context, already
    owned canonically by ``ExecutionPlan``.  Validate that context immediately
    before evaluation instead of duplicating the source hashes in the
    descriptor and manifest.
    """

    expected_contexts = {
        "runtime_compiler": {
            "circuit_hash": circuit_hash,
            "architecture_hash": architecture_hash,
            "compiler_spec_hash": compiler_spec_hash,
            "latency_profile_hash": latency_profile_hash,
        },
        "resource_resolver": {
            "architecture_hash": architecture_hash,
            "compiler_spec_hash": compiler_spec_hash,
            "latency_profile_hash": latency_profile_hash,
        },
    }
    component = components.runtime_realizer
    if component.descriptor.component_id != "runtime_realizer.state_bound.v2":
        return
    if not isinstance(component, StateBoundRuntimeRealizer):
        raise RuntimeComponentError(
            "Runtime realizer claims ArqSim's built-in identity without "
            "its official adapter"
        )
    callbacks = {
        "runtime_compiler": (
            component.program_callback,
            "runtime_compiler.state_bound_local.v1",
        ),
        "resource_resolver": (
            component.resource_callback,
            "resource_resolver.state_bound.v1",
        ),
    }
    for role, (callback, callback_id) in callbacks.items():
        if callback is None or getattr(
            callback,
            "__arqsim_builtin_component_id__",
            None,
        ) != callback_id:
            raise RuntimeComponentError(
                f"Built-in runtime_realizer is missing its trusted {role} callback"
            )
        actual_context = getattr(
            callback,
            "__arqsim_source_context__",
            None,
        )
        if not isinstance(actual_context, MappingProxyType) or not (
            json_type_strict_equal(
                actual_context,
                expected_contexts[role],
            )
        ):
            raise RuntimeComponentError(
                f"Built-in runtime_realizer {role} source context does not match the "
                "ExecutionPlan authorities"
            )


@dataclass(frozen=True)
class _DeterministicScheduler:
    descriptor: RuntimeComponentDescriptor

    def order_program(self, request: ProgramSchedulingRequest) -> Sequence[int]:
        return tuple(sorted(request.ready_ids))

    def order_resources(self, request: ResourceSchedulingRequest) -> Sequence[str]:
        return tuple(request.process_ids)


@dataclass(frozen=True)
class PassthroughProfileBackend:
    descriptor: RuntimeComponentDescriptor

    def prepare(self, request: BackendRequest) -> PreparedExecution:
        return PreparedExecution(
            duration_s=request.candidate.duration_s,
        )


@dataclass(frozen=True)
class SeededInjectionOutcomeModel:
    descriptor: RuntimeComponentDescriptor

    def resolve(self, request: CompletionRequest) -> EventOutcome:
        rng = random.Random(request.outcome_seed)
        return EventOutcome(
            measurements=tuple(
                MeasurementOutcome(register_id, rng.randrange(2))
                for register_id in request.expected_measurements
            )
        )


@dataclass(frozen=True)
class RuntimeComponentSet:
    runtime_realizer: RuntimeRealizer
    scheduler: RuntimeScheduler
    execution_backend: ProfileExecutionBackend
    outcome_model: OutcomeModel

    def __post_init__(self) -> None:
        for role in RUNTIME_COMPONENT_ROLES:
            component = getattr(self, role)
            descriptor = getattr(component, "descriptor", None)
            if not isinstance(descriptor, RuntimeComponentDescriptor):
                raise RuntimeComponentError(
                    f"Runtime component {role} has no stable descriptor"
                )
            if descriptor.role != role:
                raise RuntimeComponentError(
                    f"Runtime component {role} reports role {descriptor.role!r}"
                )

    @property
    def manifest(self) -> RuntimeComponentManifest:
        return RuntimeComponentManifest(
            components={
                role: getattr(self, role).descriptor
                for role in RUNTIME_COMPONENT_ROLES
            },
            event_engine=default_event_engine_descriptor(),
        )


def build_runtime_component_set(
    manifest: RuntimeComponentManifest,
    *,
    runtime_instruction_compiler: (
        Callable[[DeferredDispatchRequest], Mapping[str, Any]] | None
    ),
    runtime_resource_compiler: (
        Callable[[DeferredDispatchRequest], Mapping[str, Any]] | None
    ),
) -> RuntimeComponentSet:
    """Resolve the fixed built-ins used by the public facade.

    Advanced/custom components are supplied as an explicit
    :class:`RuntimeComponentSet`; this resolver never wraps arbitrary callables
    or scheduling objects under a built-in identity.
    """
    canonical = default_runtime_component_manifest()
    direct = default_direct_runtime_component_manifest()
    if manifest.manifest_hash == canonical.manifest_hash:
        if runtime_instruction_compiler is None or runtime_resource_compiler is None:
            raise RuntimeComponentError(
                "The state-bound built-in components require both compiler adapters"
            )
        expected_compiler = "runtime_compiler.state_bound_local.v1"
        expected_resolver = "resource_resolver.state_bound.v1"
        if (
            getattr(
                runtime_instruction_compiler,
                "__arqsim_builtin_component_id__",
                None,
            )
            != expected_compiler
            or getattr(
                runtime_resource_compiler,
                "__arqsim_builtin_component_id__",
                None,
            )
            != expected_resolver
        ):
            raise RuntimeComponentError(
                "The canonical manifest requires compiler adapters created by "
                "ArqSim's built-in lowering pipeline"
            )
    elif manifest.manifest_hash == direct.manifest_hash:
        if (
            runtime_instruction_compiler is not None
            or runtime_resource_compiler is not None
        ):
            raise RuntimeComponentError(
                "The direct no-op manifest cannot hide compiler callbacks"
            )
    else:
        raise UnsupportedRuntimeComponentError(
            "The built-in resolver accepts only an exact canonical or direct "
            "runtime manifest; use an explicit RuntimeComponentSet for a "
            "trusted advanced component"
        )
    return RuntimeComponentSet(
        runtime_realizer=StateBoundRuntimeRealizer(
            manifest.components["runtime_realizer"],
            runtime_instruction_compiler,
            runtime_resource_compiler,
        ),
        scheduler=_DeterministicScheduler(
            manifest.components["scheduler"],
        ),
        execution_backend=PassthroughProfileBackend(
            manifest.components["execution_backend"]
        ),
        outcome_model=SeededInjectionOutcomeModel(
            manifest.components["outcome_model"]
        ),
    )


def validate_exact_permutation(
    selected: Iterable[Any],
    available: Sequence[Any],
    *,
    owner: str,
) -> tuple[Any, ...]:
    try:
        ordered = tuple(selected)
    except TypeError as exc:
        raise RuntimeComponentError(
            f"{owner} must return an iterable of presented ids"
        ) from exc
    expected = tuple(available)
    if len(ordered) != len(expected):
        raise RuntimeComponentError(
            f"{owner} must return each presented id exactly once"
        )
    remaining = list(expected)
    for value in ordered:
        match = next(
            (
                index
                for index, expected_value in enumerate(remaining)
                if type(value) is type(expected_value) and value == expected_value
            ),
            None,
        )
        if match is None:
            raise RuntimeComponentError(
                f"{owner} returned unknown, duplicate, or type-mismatched ids"
            )
        remaining.pop(match)
    return ordered


__all__ = [
    "BackendRequest",
    "CandidateImplementation",
    "CompletionRequest",
    "ContinuationDecision",
    "ContinuationRequest",
    "DeferredDispatchRequest",
    "EventOutcome",
    "MeasurementOutcome",
    "OutcomeModel",
    "PreparedExecution",
    "ProfileExecutionBackend",
    "ProgramSchedulingRequest",
    "RealizationRequest",
    "RUNTIME_COMPONENT_ROLES",
    "RUNTIME_COMPONENT_SCHEMA_VERSION",
    "RUNTIME_MANIFEST_SCHEMA_VERSION",
    "ResourceSchedulingRequest",
    "RuntimeComponentDescriptor",
    "RuntimeComponentError",
    "RuntimeComponentManifest",
    "RuntimeComponentSet",
    "RuntimeOperationView",
    "RuntimeRealizer",
    "RuntimeScheduler",
    "StateBoundRuntimeRealizer",
    "SeededInjectionOutcomeModel",
    "UnsupportedRuntimeComponentError",
    "default_runtime_component_manifest",
]
