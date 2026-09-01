"""Immutable, serializable output contracts for logical compilation."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, field
from functools import cached_property
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from heteqsys.program import FTCircuit

from heteqsys.schema import deep_freeze_json, strict_json
from heteqsys.schema import normalize_json as normalize_json_value

from .errors import LogicalCompilerValidationError
from .models import LogicalCompilerSpec, _reject_model_values, semantic_hash


COMPILATION_RESULT_SCHEMA_VERSION = "heteqsys.logical-compilation-result.v1"


def _strict_wire_json(value: Any, *, path: str = "compilation-result") -> None:
    """Reject Python conveniences that are not the exact JSON wire shape."""

    value_type = type(value)
    if value is None or value_type in {str, int, bool}:
        return
    if value_type is float:
        if not math.isfinite(value):
            raise LogicalCompilerValidationError(
                "Compilation-result wire values must be finite",
                details={"path": path, "value": value},
            )
        return
    if value_type is list:
        for index, item in enumerate(value):
            _strict_wire_json(item, path=f"{path}[{index}]")
        return
    if value_type is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise TypeError(f"{path} object keys must be exact strings")
            _strict_wire_json(item, path=f"{path}.{key}")
        return
    raise TypeError(
        f"{path} must use exact JSON dict/list/scalar wire values; "
        f"got {value_type.__name__}"
    )


def _exact_wire_equal(left: Any, right: Any) -> bool:
    """Compare canonical JSON values without numeric or container coercion."""

    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(
            _exact_wire_equal(left[key], right[key]) for key in left
        )
    if type(left) is list:
        return len(left) == len(right) and all(
            _exact_wire_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def _wire_array(value: Any, *, field_name: str) -> list[Any]:
    if type(value) is not list:
        raise TypeError(f"{field_name} must be an exact JSON array")
    return value


def _strict_fields(
    data: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    label: str,
) -> None:
    if not isinstance(data, Mapping):
        raise TypeError(f"{label} must be a mapping")
    unknown = set(data) - allowed
    if unknown:
        raise LogicalCompilerValidationError(
            f"Unknown {label} fields",
            details={"fields": sorted(unknown)},
        )
    missing = required - set(data)
    if missing:
        raise LogicalCompilerValidationError(
            f"Missing {label} fields",
            details={"fields": sorted(missing)},
        )


def _integer(value: Any, *, field_name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field_name} must be an integer")
    if value < minimum:
        raise LogicalCompilerValidationError(
            f"{field_name} must be at least {minimum}",
            details={field_name: value},
        )
    return value


def _finite_nonnegative(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field_name} must be a real number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise LogicalCompilerValidationError(
            f"{field_name} must be finite and non-negative",
            details={field_name: value},
        )
    return result


def _logical_mapping(
    value: Mapping[int, str],
    *,
    field_name: str,
) -> Mapping[int, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    normalized: dict[int, str] = {}
    for raw_qubit, raw_slot in value.items():
        qubit = _integer(raw_qubit, field_name=f"{field_name} key")
        if (
            not isinstance(raw_slot, str)
            or not raw_slot.strip()
            or raw_slot != raw_slot.strip()
        ):
            raise LogicalCompilerValidationError(
                f"{field_name} slot IDs must be non-empty strings",
                details={"logical_qubit": qubit, "slot": raw_slot},
            )
        normalized[qubit] = raw_slot
    if len(set(normalized.values())) != len(normalized):
        raise LogicalCompilerValidationError(
            f"{field_name} must assign distinct slots",
            details={"slots": sorted(normalized.values())},
        )
    return MappingProxyType(dict(sorted(normalized.items())))


def _logical_mapping_from_dict(
    value: Any,
    *,
    field_name: str,
) -> dict[int, str]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    result: dict[int, str] = {}
    for raw_qubit, raw_slot in value.items():
        if not isinstance(raw_qubit, str) or not raw_qubit.isdecimal():
            raise LogicalCompilerValidationError(
                f"{field_name} keys must be canonical non-negative integer strings",
                details={"key": raw_qubit},
            )
        qubit = int(raw_qubit)
        if str(qubit) != raw_qubit:
            raise LogicalCompilerValidationError(
                f"{field_name} keys must be canonical non-negative integer strings",
                details={"key": raw_qubit},
            )
        if not isinstance(raw_slot, str):
            raise TypeError(f"{field_name} values must be strings")
        result[qubit] = raw_slot
    return result


def _compiler_spec_from_wire(data: Any) -> LogicalCompilerSpec:
    compiler_fields = frozenset(
        {
            "schema_version",
            "instruction_set",
            "issue_policy",
            "mapping",
            "routing",
            "compiler_hash",
        }
    )
    _strict_fields(
        data,
        allowed=compiler_fields,
        required=compiler_fields,
        label="embedded logical compiler-spec",
    )
    backend_fields = frozenset({"backend", "options"})
    for stage in ("mapping", "routing"):
        _strict_fields(
            data[stage],
            allowed=backend_fields,
            required=backend_fields,
            label=f"embedded compiler {stage} backend",
        )
    return LogicalCompilerSpec.from_dict(data)


@dataclass(frozen=True)
class ComputePartition:
    """One source-layer operation partition whose active qubits fit compute capacity."""

    layer_index: int
    partition_index: int
    partition_count: int
    operation_indices: tuple[int, ...]
    active_qubits: tuple[int, ...]
    magic_count: int

    def __post_init__(self) -> None:
        layer_index = _integer(self.layer_index, field_name="layer_index")
        partition_index = _integer(
            self.partition_index, field_name="partition_index"
        )
        partition_count = _integer(
            self.partition_count, field_name="partition_count", minimum=1
        )
        if partition_index >= partition_count:
            raise LogicalCompilerValidationError(
                "partition_index must be smaller than partition_count",
                details={
                    "partition_index": partition_index,
                    "partition_count": partition_count,
                },
            )
        operations = tuple(
            _integer(value, field_name="operation_indices item")
            for value in self.operation_indices
        )
        qubits = tuple(
            _integer(value, field_name="active_qubits item")
            for value in self.active_qubits
        )
        if not operations or len(set(operations)) != len(operations):
            raise LogicalCompilerValidationError(
                "A compute partition must contain distinct operations"
            )
        if not qubits or len(set(qubits)) != len(qubits):
            raise LogicalCompilerValidationError(
                "A compute partition must contain distinct active qubits"
            )
        object.__setattr__(self, "layer_index", layer_index)
        object.__setattr__(self, "partition_index", partition_index)
        object.__setattr__(self, "partition_count", partition_count)
        object.__setattr__(self, "operation_indices", operations)
        object.__setattr__(self, "active_qubits", tuple(sorted(qubits)))
        object.__setattr__(
            self,
            "magic_count",
            _integer(self.magic_count, field_name="magic_count"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer_index,
            "partition": self.partition_index,
            "partition_count": self.partition_count,
            "operation_indices": list(self.operation_indices),
            "active_qubits": list(self.active_qubits),
            "magic_count": self.magic_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ComputePartition":
        allowed = frozenset(
            {
                "layer",
                "partition",
                "partition_count",
                "operation_indices",
                "active_qubits",
                "magic_count",
            }
        )
        _strict_fields(
            data,
            allowed=allowed,
            required=allowed,
            label="compute-partition",
        )
        return cls(
            layer_index=data["layer"],
            partition_index=data["partition"],
            partition_count=data["partition_count"],
            operation_indices=tuple(
                _wire_array(
                    data["operation_indices"],
                    field_name="compute-partition operation_indices",
                )
            ),
            active_qubits=tuple(
                _wire_array(
                    data["active_qubits"],
                    field_name="compute-partition active_qubits",
                )
            ),
            magic_count=data["magic_count"],
        )


@dataclass(frozen=True)
class ComputeBatch:
    """One executable batch within a compute-capacity partition."""

    partition: ComputePartition
    batch_index: int
    batch_count: int
    operation_indices: tuple[int, ...]
    operated_qubits: tuple[int, ...]
    magic_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.partition, ComputePartition):
            raise TypeError("partition must be a ComputePartition")
        batch_index = _integer(self.batch_index, field_name="batch_index")
        batch_count = _integer(
            self.batch_count, field_name="batch_count", minimum=1
        )
        if batch_index >= batch_count:
            raise LogicalCompilerValidationError(
                "batch_index must be smaller than batch_count",
                details={"batch_index": batch_index, "batch_count": batch_count},
            )
        operations = tuple(
            _integer(value, field_name="operation_indices item")
            for value in self.operation_indices
        )
        qubits = tuple(
            _integer(value, field_name="operated_qubits item")
            for value in self.operated_qubits
        )
        if not operations or len(set(operations)) != len(operations):
            raise LogicalCompilerValidationError(
                "A compute batch must contain distinct operations"
            )
        if not qubits or len(set(qubits)) != len(qubits):
            raise LogicalCompilerValidationError(
                "A compute batch must contain distinct operated qubits"
            )
        if not set(operations) <= set(self.partition.operation_indices):
            raise LogicalCompilerValidationError(
                "Compute-batch operations must belong to its partition"
            )
        if not set(qubits) <= set(self.partition.active_qubits):
            raise LogicalCompilerValidationError(
                "Compute-batch qubits must belong to its partition"
            )
        magic_count = _integer(self.magic_count, field_name="magic_count")
        if magic_count > self.partition.magic_count:
            raise LogicalCompilerValidationError(
                "Compute-batch magic demand cannot exceed its partition demand"
            )
        object.__setattr__(self, "batch_index", batch_index)
        object.__setattr__(self, "batch_count", batch_count)
        object.__setattr__(self, "operation_indices", operations)
        object.__setattr__(self, "operated_qubits", tuple(sorted(qubits)))
        object.__setattr__(self, "magic_count", magic_count)

    @property
    def layer_index(self) -> int:
        return self.partition.layer_index

    @property
    def sublayer_index(self) -> int:
        return self.partition.partition_index

    @property
    def active_qubits(self) -> tuple[int, ...]:
        """Compatibility name for the qubits operated on by this batch."""

        return self.operated_qubits

    def to_dict(self) -> dict[str, Any]:
        return {
            "partition": self.partition.to_dict(),
            "batch": self.batch_index,
            "batch_count": self.batch_count,
            "operation_indices": list(self.operation_indices),
            "operated_qubits": list(self.operated_qubits),
            "magic_count": self.magic_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ComputeBatch":
        allowed = frozenset(
            {
                "partition",
                "batch",
                "batch_count",
                "operation_indices",
                "operated_qubits",
                "magic_count",
            }
        )
        _strict_fields(
            data,
            allowed=allowed,
            required=allowed,
            label="compute-batch",
        )
        return cls(
            partition=ComputePartition.from_dict(data["partition"]),
            batch_index=data["batch"],
            batch_count=data["batch_count"],
            operation_indices=tuple(
                _wire_array(
                    data["operation_indices"],
                    field_name="compute-batch operation_indices",
                )
            ),
            operated_qubits=tuple(
                _wire_array(
                    data["operated_qubits"],
                    field_name="compute-batch operated_qubits",
                )
            ),
            magic_count=data["magic_count"],
        )


@dataclass(frozen=True)
class CompiledRouteStep:
    """Stable architecture-facing summary of one backend route step."""

    kind: str
    group: int
    operation_count: int
    terminal_slots: tuple[str, ...]
    path_node_count: int
    path_edge_count: int
    movement_count: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise LogicalCompilerValidationError(
                "Compiled route-step kind must be a non-empty string"
            )
        terminals = tuple(self.terminal_slots)
        if any(
            type(value) is not str or not value or value != value.strip()
            for value in terminals
        ):
            raise LogicalCompilerValidationError(
                "Compiled route-step terminal slots must be non-empty strings"
            )
        metadata = strict_json(
            self.metadata, label="compiled route-step metadata"
        )
        if not isinstance(metadata, dict):
            raise TypeError("compiled route-step metadata must be a mapping")
        _reject_model_values(metadata, path="compiled_route_step.metadata")
        object.__setattr__(self, "terminal_slots", terminals)
        object.__setattr__(
            self, "group", _integer(self.group, field_name="route-step group")
        )
        for name in (
            "operation_count",
            "path_node_count",
            "path_edge_count",
            "movement_count",
        ):
            object.__setattr__(
                self,
                name,
                _integer(getattr(self, name), field_name=name),
            )
        object.__setattr__(self, "metadata", deep_freeze_json(metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "group": self.group,
            "operation_count": self.operation_count,
            "terminal_slots": list(self.terminal_slots),
            "path_node_count": self.path_node_count,
            "path_edge_count": self.path_edge_count,
            "movement_count": self.movement_count,
            "metadata": normalize_json_value(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CompiledRouteStep":
        allowed = frozenset(
            {
                "kind",
                "group",
                "operation_count",
                "terminal_slots",
                "path_node_count",
                "path_edge_count",
                "movement_count",
                "metadata",
            }
        )
        _strict_fields(
            data,
            allowed=allowed,
            required=allowed,
            label="compiled route-step",
        )
        return cls(
            kind=data["kind"],
            group=data["group"],
            operation_count=data["operation_count"],
            terminal_slots=tuple(
                _wire_array(
                    data["terminal_slots"],
                    field_name="compiled route-step terminal_slots",
                )
            ),
            path_node_count=data["path_node_count"],
            path_edge_count=data["path_edge_count"],
            movement_count=data["movement_count"],
            metadata=data["metadata"],
        )


@dataclass(frozen=True)
class ComputeDuration:
    """Latency-aware duration estimate attached to one compiled route."""

    primitive_service_s: float
    compiler_routing_s: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "primitive_service_s",
            _finite_nonnegative(
                self.primitive_service_s, field_name="primitive_service_s"
            ),
        )
        object.__setattr__(
            self,
            "compiler_routing_s",
            _finite_nonnegative(
                self.compiler_routing_s, field_name="compiler_routing_s"
            ),
        )

    @property
    def total_s(self) -> float:
        return self.primitive_service_s + self.compiler_routing_s

    def to_dict(self) -> dict[str, float]:
        return {
            "primitive_service": self.primitive_service_s,
            "compiler_routing": self.compiler_routing_s,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ComputeDuration":
        allowed = frozenset({"primitive_service", "compiler_routing"})
        _strict_fields(
            data,
            allowed=allowed,
            required=allowed,
            label="compute-duration",
        )
        return cls(
            primitive_service_s=data["primitive_service"],
            compiler_routing_s=data["compiler_routing"],
        )


@dataclass(frozen=True)
class SyndromeProtocolTiming:
    """Resolved syndrome protocol and the QEC facts used for its timing."""

    id: str
    round_mode: str
    rounds: int
    cycle_time_s: float
    distance_module_role: str | None = None
    distance_module: str | None = None
    distance_code: str | None = None
    distance: int | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise LogicalCompilerValidationError(
                "Syndrome protocol ID must be a non-empty string"
            )
        if self.round_mode not in {"fixed", "qec_distance"}:
            raise LogicalCompilerValidationError(
                "Syndrome protocol round mode is unsupported",
                details={"round_mode": self.round_mode},
            )
        rounds = _integer(self.rounds, field_name="rounds", minimum=1)
        cycle = _finite_nonnegative(self.cycle_time_s, field_name="cycle_time_s")
        distance_fields = (
            self.distance_module_role,
            self.distance_module,
            self.distance_code,
            self.distance,
        )
        if self.round_mode == "fixed" and any(
            value is not None for value in distance_fields
        ):
            raise LogicalCompilerValidationError(
                "Fixed syndrome timing cannot carry distance-derived fields"
            )
        if self.round_mode == "qec_distance" and any(
            value is None for value in distance_fields
        ):
            raise LogicalCompilerValidationError(
                "Distance-derived syndrome timing requires its source QEC fields"
            )
        if self.distance is not None:
            distance = _integer(self.distance, field_name="distance", minimum=1)
            if distance != rounds:
                raise LogicalCompilerValidationError(
                    "Distance-derived syndrome rounds must equal QEC distance"
                )
            object.__setattr__(self, "distance", distance)
        for name in ("distance_module_role", "distance_module", "distance_code"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise LogicalCompilerValidationError(
                    f"{name} must be a non-empty string when present"
                )
        provenance = strict_json(
            self.provenance, label="syndrome protocol provenance"
        )
        if not isinstance(provenance, dict):
            raise TypeError("syndrome protocol provenance must be a mapping")
        object.__setattr__(self, "rounds", rounds)
        object.__setattr__(self, "cycle_time_s", cycle)
        object.__setattr__(self, "provenance", deep_freeze_json(provenance))

    @property
    def service_s(self) -> float:
        return self.rounds * self.cycle_time_s

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "round_mode": self.round_mode,
            "rounds": self.rounds,
            "cycle_time_s": self.cycle_time_s,
        }
        if self.distance_module_role is not None:
            result.update(
                {
                    "distance_module_role": self.distance_module_role,
                    "distance_module": self.distance_module,
                    "distance_code": self.distance_code,
                    "distance": self.distance,
                }
            )
        if self.provenance:
            result["provenance"] = normalize_json_value(self.provenance)
        return result

    def to_instruction_metadata(self) -> dict[str, Any]:
        """Project the derived service time at the instruction boundary."""

        result = self.to_dict()
        result["service_s"] = self.service_s
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SyndromeProtocolTiming":
        allowed = frozenset(
            {
                "id",
                "round_mode",
                "rounds",
                "cycle_time_s",
                "distance_module_role",
                "distance_module",
                "distance_code",
                "distance",
                "provenance",
            }
        )
        _strict_fields(
            data,
            allowed=allowed,
            required=frozenset(
                {"id", "round_mode", "rounds", "cycle_time_s"}
            ),
            label="syndrome-protocol timing",
        )
        return cls(
            id=data["id"],
            round_mode=data["round_mode"],
            rounds=data["rounds"],
            cycle_time_s=data["cycle_time_s"],
            distance_module_role=data.get("distance_module_role"),
            distance_module=data.get("distance_module"),
            distance_code=data.get("distance_code"),
            distance=data.get("distance"),
            provenance=data.get("provenance", {}),
        )


@dataclass(frozen=True)
class CompiledRouteResult:
    """Typed route summary consumed by architecture-instruction lowering."""

    route_hash: str
    dispatch_deferred: bool
    metrics: Mapping[str, Any]
    steps: tuple[CompiledRouteStep, ...]
    duration: ComputeDuration
    syndrome_protocol: SyndromeProtocolTiming

    def __post_init__(self) -> None:
        if not isinstance(self.route_hash, str) or not self.route_hash.strip():
            raise LogicalCompilerValidationError(
                "Compiled route hash must be a non-empty string"
            )
        if not isinstance(self.dispatch_deferred, bool):
            raise TypeError("dispatch_deferred must be a boolean")
        if not isinstance(self.duration, ComputeDuration):
            raise TypeError("duration must be a ComputeDuration")
        if not isinstance(self.syndrome_protocol, SyndromeProtocolTiming):
            raise TypeError(
                "syndrome_protocol must be a SyndromeProtocolTiming"
            )
        steps = tuple(self.steps)
        if any(not isinstance(step, CompiledRouteStep) for step in steps):
            raise TypeError("steps must contain CompiledRouteStep records")
        if self.dispatch_deferred and steps:
            raise LogicalCompilerValidationError(
                "A dispatch-deferred route cannot contain compiled route steps"
            )
        metrics = strict_json(self.metrics, label="compiled route metrics")
        if not isinstance(metrics, dict):
            raise TypeError("compiled route metrics must be a mapping")
        legacy_control_keys = {"deferred_until_dispatch", "dispatch_deferred"}
        duplicated_control = sorted(legacy_control_keys & set(metrics))
        if duplicated_control:
            raise LogicalCompilerValidationError(
                "Route dispatch state cannot be duplicated in route metrics",
                details={"fields": duplicated_control},
            )
        _reject_model_values(metrics, path="compiled_route.metrics")
        object.__setattr__(self, "steps", steps)
        object.__setattr__(self, "metrics", deep_freeze_json(metrics))

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_hash": self.route_hash,
            "dispatch_deferred": self.dispatch_deferred,
            "route_metrics": normalize_json_value(self.metrics),
            "route_steps": [step.to_dict() for step in self.steps],
            "duration_components_s": self.duration.to_dict(),
            "syndrome_protocol": self.syndrome_protocol.to_dict(),
        }

    def to_instruction_metadata(self) -> dict[str, Any]:
        """Project typed route facts at the architecture-instruction boundary."""

        result = self.to_dict()
        # Dispatch state remains a typed compiler/lowering control.  It is not
        # copied into the diagnostic metadata receipt as a second authority.
        del result["dispatch_deferred"]
        result["duration_s"] = self.duration.total_s
        result["syndrome_protocol"] = (
            self.syndrome_protocol.to_instruction_metadata()
        )
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CompiledRouteResult":
        allowed = frozenset(
            {
                "route_hash",
                "dispatch_deferred",
                "route_metrics",
                "route_steps",
                "duration_components_s",
                "syndrome_protocol",
            }
        )
        _strict_fields(
            data,
            allowed=allowed,
            required=allowed,
            label="compiled route result",
        )
        duration = ComputeDuration.from_dict(data["duration_components_s"])
        return cls(
            route_hash=data["route_hash"],
            dispatch_deferred=data["dispatch_deferred"],
            metrics=data["route_metrics"],
            steps=tuple(
                CompiledRouteStep.from_dict(item)
                for item in _wire_array(
                    data["route_steps"],
                    field_name="compiled route result route_steps",
                )
            ),
            duration=duration,
            syndrome_protocol=SyndromeProtocolTiming.from_dict(
                data["syndrome_protocol"]
            ),
        )


@dataclass(frozen=True)
class CompiledComputeUnit:
    """One typed compiler unit whose mapping owns compute residency."""

    source: ComputeBatch
    mapping: Mapping[int, str]
    route: CompiledRouteResult

    def __post_init__(self) -> None:
        if not isinstance(self.source, ComputeBatch):
            raise TypeError("source must be a ComputeBatch")
        if not isinstance(self.route, CompiledRouteResult):
            raise TypeError("route must be a CompiledRouteResult")
        mapping = _logical_mapping(self.mapping, field_name="compute-unit mapping")
        if not set(self.source.partition.active_qubits) <= set(mapping):
            raise LogicalCompilerValidationError(
                "Compute-unit mapping must cover every active partition qubit"
            )
        object.__setattr__(self, "mapping", mapping)

    @property
    def route_hash(self) -> str:
        return self.route.route_hash

    @property
    def route_metrics(self) -> Mapping[str, Any]:
        return self.route.metrics

    @property
    def route_steps(self) -> tuple[CompiledRouteStep, ...]:
        return self.route.steps

    @property
    def duration_s(self) -> float:
        return self.route.duration.total_s

    @property
    def duration_components_s(self) -> Mapping[str, float]:
        return MappingProxyType(self.route.duration.to_dict())

    @property
    def syndrome_protocol(self) -> SyndromeProtocolTiming:
        return self.route.syndrome_protocol

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.to_dict(),
            "mapping": {
                str(key): value for key, value in sorted(self.mapping.items())
            },
            "route": self.route.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CompiledComputeUnit":
        allowed = frozenset({"source", "mapping", "route"})
        _strict_fields(
            data,
            allowed=allowed,
            required=allowed,
            label="compiled compute-unit",
        )
        return cls(
            source=ComputeBatch.from_dict(data["source"]),
            mapping=_logical_mapping_from_dict(
                data["mapping"], field_name="compute-unit mapping"
            ),
            route=CompiledRouteResult.from_dict(data["route"]),
        )


@dataclass(frozen=True)
class LogicalCompilationResult:
    """Self-identifying, serializable output of logical compilation."""

    circuit_hash: str
    architecture_hash: str
    latency_profile_hash: str
    compiler_spec: LogicalCompilerSpec
    magic_state_consumption: str
    compute_units: tuple[CompiledComputeUnit, ...]
    initial_mapping: Mapping[int, str]
    deferred_capacity_mapping: bool

    def __post_init__(self) -> None:
        for name in ("circuit_hash", "architecture_hash", "latency_profile_hash"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise LogicalCompilerValidationError(
                    f"{name} must be a non-empty string"
                )
        if not isinstance(self.compiler_spec, LogicalCompilerSpec):
            raise TypeError("compiler_spec must be a LogicalCompilerSpec")
        if not isinstance(self.magic_state_consumption, str):
            raise TypeError("magic_state_consumption must be a string")
        if self.magic_state_consumption not in {"bulk_wave", "incremental"}:
            raise LogicalCompilerValidationError(
                "Unsupported magic-state consumption policy",
                details={
                    "magic_state_consumption": self.magic_state_consumption
                },
            )
        units = tuple(self.compute_units)
        if any(not isinstance(unit, CompiledComputeUnit) for unit in units):
            raise TypeError("compute_units must contain CompiledComputeUnit records")
        identities = tuple(
            (
                unit.source.layer_index,
                unit.source.partition.partition_index,
                unit.source.batch_index,
            )
            for unit in units
        )
        if len(set(identities)) != len(identities):
            raise LogicalCompilerValidationError(
                "Compiled compute-unit identities must be unique"
            )
        if not isinstance(self.deferred_capacity_mapping, bool):
            raise TypeError("deferred_capacity_mapping must be a boolean")
        mapping = _logical_mapping(
            self.initial_mapping, field_name="initial mapping"
        )
        if self.deferred_capacity_mapping and mapping:
            raise LogicalCompilerValidationError(
                "Deferred capacity mapping cannot carry an initial mapping"
            )
        object.__setattr__(self, "compute_units", units)
        object.__setattr__(self, "initial_mapping", mapping)

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": COMPILATION_RESULT_SCHEMA_VERSION,
            "source": {
                "circuit_hash": self.circuit_hash,
                "architecture_hash": self.architecture_hash,
                "latency_profile_hash": self.latency_profile_hash,
            },
            "compiler": self.compiler_spec.to_dict(),
            "magic_state_consumption": self.magic_state_consumption,
            "compute_units": [unit.to_dict() for unit in self.compute_units],
            "initial_mapping": {
                str(key): value
                for key, value in sorted(self.initial_mapping.items())
            },
            "deferred_capacity_mapping": self.deferred_capacity_mapping,
        }

    @cached_property
    def compilation_hash(self) -> str:
        return semantic_hash(self.semantic_dict())

    def to_dict(self) -> dict[str, Any]:
        result = self.semantic_dict()
        result["compilation_hash"] = self.compilation_hash
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
    def from_dict(cls, data: Mapping[str, Any]) -> "LogicalCompilationResult":
        _strict_wire_json(data)
        allowed = frozenset(
            {
                "schema_version",
                "source",
                "compiler",
                "magic_state_consumption",
                "compute_units",
                "initial_mapping",
                "deferred_capacity_mapping",
                "compilation_hash",
            }
        )
        _strict_fields(
            data,
            allowed=allowed,
            required=allowed,
            label="compilation-result",
        )
        if data["schema_version"] != COMPILATION_RESULT_SCHEMA_VERSION:
            raise LogicalCompilerValidationError(
                "Unsupported compilation-result schema",
                details={"schema_version": data["schema_version"]},
            )
        source = data["source"]
        source_fields = frozenset(
            {"circuit_hash", "architecture_hash", "latency_profile_hash"}
        )
        _strict_fields(
            source,
            allowed=source_fields,
            required=source_fields,
            label="compilation-result source",
        )
        result = cls(
            circuit_hash=source["circuit_hash"],
            architecture_hash=source["architecture_hash"],
            latency_profile_hash=source["latency_profile_hash"],
            compiler_spec=_compiler_spec_from_wire(data["compiler"]),
            magic_state_consumption=data["magic_state_consumption"],
            compute_units=tuple(
                CompiledComputeUnit.from_dict(item)
                for item in _wire_array(
                    data["compute_units"],
                    field_name="compilation-result compute_units",
                )
            ),
            initial_mapping=_logical_mapping_from_dict(
                data["initial_mapping"], field_name="initial mapping"
            ),
            deferred_capacity_mapping=data["deferred_capacity_mapping"],
        )
        unsigned = {
            key: value for key, value in data.items() if key != "compilation_hash"
        }
        if not _exact_wire_equal(unsigned, result.semantic_dict()):
            raise LogicalCompilerValidationError(
                "Compilation result must use the exact canonical JSON wire shape"
            )
        recorded_hash = data["compilation_hash"]
        if type(recorded_hash) is not str:
            raise TypeError("compilation_hash must be a string")
        if recorded_hash != result.compilation_hash:
            raise LogicalCompilerValidationError(
                "Compilation-result hash does not match its content",
                details={
                    "expected": recorded_hash,
                    "actual": result.compilation_hash,
                },
            )
        return result

    @classmethod
    def from_json(cls, document: str) -> "LogicalCompilationResult":
        if type(document) is not str:
            raise TypeError(
                "LogicalCompilationResult JSON document must be a string"
            )

        def reject_constant(value: str) -> None:
            raise LogicalCompilerValidationError(
                "Non-finite JSON constant is not allowed in a compilation result",
                details={"constant": value},
            )

        def reject_duplicate_keys(
            pairs: list[tuple[str, Any]],
        ) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise LogicalCompilerValidationError(
                        "Duplicate JSON object key in compilation result",
                        details={"key": key},
                    )
                result[key] = value
            return result

        data = json.loads(
            document,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
        if not isinstance(data, dict):
            raise LogicalCompilerValidationError(
                "LogicalCompilationResult JSON document must contain an object"
            )
        return cls.from_dict(data)


def validate_compilation_coverage(
    compilation: LogicalCompilationResult,
    circuit: FTCircuit,
) -> None:
    """Require one compiled compute-unit claim per source operation."""

    from heteqsys.program import FTCircuit
    from heteqsys.program.statistics import operation_uses_magic_state

    if not isinstance(compilation, LogicalCompilationResult):
        raise TypeError("compilation must be a LogicalCompilationResult")
    if not isinstance(circuit, FTCircuit):
        raise TypeError("circuit must be an FTCircuit")

    expected = Counter(
        (layer.index, operation_index)
        for layer in circuit.layers
        for operation_index, _operation in enumerate(layer.operations)
    )
    actual = Counter(
        (unit.source.layer_index, operation_index)
        for unit in compilation.compute_units
        for operation_index in unit.source.operation_indices
    )
    duplicate = sorted(
        [list(identity) for identity, count in actual.items() if count > 1]
    )
    missing = sorted(
        [list(identity) for identity in (expected - actual).elements()]
    )
    extra = sorted(
        [list(identity) for identity in (actual - expected).elements()]
    )

    operated_qubit_mismatches: list[dict[str, Any]] = []
    magic_count_mismatches: list[dict[str, Any]] = []
    consumption_policy_mismatches: list[dict[str, Any]] = []
    route_state_mismatches: list[dict[str, Any]] = []
    partition_errors: list[dict[str, Any]] = []
    partition_groups: dict[tuple[int, int], list[CompiledComputeUnit]] = {}
    unit_order = [
        (
            unit.source.layer_index,
            unit.source.partition.partition_index,
            unit.source.batch_index,
        )
        for unit in compilation.compute_units
    ]
    if unit_order != sorted(unit_order):
        partition_errors.append(
            {
                "reason": "noncanonical_compute_unit_order",
                "actual": [list(identity) for identity in unit_order],
            }
        )
    for unit in compilation.compute_units:
        partition_key = (
            unit.source.layer_index,
            unit.source.partition.partition_index,
        )
        partition_groups.setdefault(partition_key, []).append(unit)
        layer_index = unit.source.layer_index
        if not 0 <= layer_index < len(circuit.layers):
            continue
        layer = circuit.layers[layer_index]
        if any(
            operation_index >= len(layer.operations)
            for operation_index in unit.source.operation_indices
        ):
            continue
        expected_qubits = tuple(
            sorted(
                {
                    qubit
                    for operation_index in unit.source.operation_indices
                    for qubit in layer.operations[operation_index].qubits
                }
            )
        )
        if unit.source.operated_qubits != expected_qubits:
            operated_qubit_mismatches.append(
                {
                    "layer": layer_index,
                    "partition": unit.source.partition.partition_index,
                    "batch": unit.source.batch_index,
                    "expected": list(expected_qubits),
                    "actual": list(unit.source.operated_qubits),
                }
            )
        expected_magic = sum(
            operation_uses_magic_state(layer.operations[operation_index])
            for operation_index in unit.source.operation_indices
        )
        if unit.source.magic_count != expected_magic:
            magic_count_mismatches.append(
                {
                    "scope": "batch",
                    "layer": layer_index,
                    "partition": unit.source.partition.partition_index,
                    "batch": unit.source.batch_index,
                    "expected": expected_magic,
                    "actual": unit.source.magic_count,
                }
            )
        if (
            compilation.magic_state_consumption == "incremental"
            and unit.source.magic_count > 1
        ):
            consumption_policy_mismatches.append(
                {
                    "layer": layer_index,
                    "partition": unit.source.partition.partition_index,
                    "batch": unit.source.batch_index,
                    "magic_count": unit.source.magic_count,
                    "maximum": 1,
                }
            )
        if unit.route.dispatch_deferred and expected_magic == 0:
            route_state_mismatches.append(
                {
                    "layer": layer_index,
                    "partition": unit.source.partition.partition_index,
                    "batch": unit.source.batch_index,
                    "reason": "non_magic_route_cannot_be_dispatch_deferred",
                }
            )

    for (layer_index, partition_index), units in sorted(
        partition_groups.items()
    ):
        partition = units[0].source.partition
        identity = {"layer": layer_index, "partition": partition_index}
        if any(unit.source.partition != partition for unit in units[1:]):
            partition_errors.append(
                {**identity, "reason": "inconsistent_partition_records"}
            )
            continue
        batch_counts = {unit.source.batch_count for unit in units}
        actual_batch_indices = sorted(unit.source.batch_index for unit in units)
        if len(batch_counts) != 1:
            partition_errors.append(
                {**identity, "reason": "inconsistent_batch_counts"}
            )
        else:
            batch_count = next(iter(batch_counts))
            if actual_batch_indices != list(range(batch_count)):
                partition_errors.append(
                    {
                        **identity,
                        "reason": "incomplete_batch_index_coverage",
                        "expected": list(range(batch_count)),
                        "actual": actual_batch_indices,
                    }
                )
        batch_operations = Counter(
            operation_index
            for unit in units
            for operation_index in unit.source.operation_indices
        )
        partition_operations = Counter(partition.operation_indices)
        if batch_operations != partition_operations:
            partition_errors.append(
                {
                    **identity,
                    "reason": "batch_partition_operation_mismatch",
                    "partition_operations": list(partition.operation_indices),
                    "batch_operations": [
                        operation_index
                        for unit in units
                        for operation_index in unit.source.operation_indices
                    ],
                }
            )
        if not 0 <= layer_index < len(circuit.layers):
            continue
        layer = circuit.layers[layer_index]
        if any(
            operation_index >= len(layer.operations)
            for operation_index in partition.operation_indices
        ):
            continue
        expected_active_qubits = tuple(
            sorted(
                {
                    qubit
                    for operation_index in partition.operation_indices
                    for qubit in layer.operations[operation_index].qubits
                }
            )
        )
        if partition.active_qubits != expected_active_qubits:
            partition_errors.append(
                {
                    **identity,
                    "reason": "partition_active_qubit_mismatch",
                    "expected": list(expected_active_qubits),
                    "actual": list(partition.active_qubits),
                }
            )
        expected_partition_magic = sum(
            operation_uses_magic_state(layer.operations[operation_index])
            for operation_index in partition.operation_indices
        )
        if partition.magic_count != expected_partition_magic:
            magic_count_mismatches.append(
                {
                    "scope": "partition",
                    **identity,
                    "expected": expected_partition_magic,
                    "actual": partition.magic_count,
                }
            )

    layer_partitions: dict[int, list[ComputePartition]] = {}
    for (layer_index, _partition_index), units in partition_groups.items():
        layer_partitions.setdefault(layer_index, []).append(
            units[0].source.partition
        )
    for layer_index, partitions in sorted(layer_partitions.items()):
        partition_counts = {partition.partition_count for partition in partitions}
        indices = sorted(partition.partition_index for partition in partitions)
        if len(partition_counts) != 1 or indices != list(
            range(next(iter(partition_counts), 0))
        ):
            partition_errors.append(
                {
                    "layer": layer_index,
                    "reason": "incomplete_partition_index_coverage",
                    "declared_counts": sorted(partition_counts),
                    "actual": indices,
                }
            )

    if (
        duplicate
        or missing
        or extra
        or operated_qubit_mismatches
        or magic_count_mismatches
        or consumption_policy_mismatches
        or route_state_mismatches
        or partition_errors
    ):
        raise LogicalCompilerValidationError(
            "LogicalCompilationResult does not exactly cover the source circuit",
            details={
                "missing_operations": missing,
                "extra_operations": extra,
                "duplicate_operations": duplicate,
                "operated_qubit_mismatches": operated_qubit_mismatches,
                "magic_count_mismatches": magic_count_mismatches,
                "consumption_policy_mismatches": (
                    consumption_policy_mismatches
                ),
                "route_state_mismatches": route_state_mismatches,
                "partition_errors": partition_errors,
            },
        )
