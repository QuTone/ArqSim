"""Immutable causal execution traces and evaluation results."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import Enum
from types import SimpleNamespace
from typing import Any, Mapping

from heteqsys.architecture.isa import ArchitectureOpcode
from heteqsys.architecture.recipes import ProgramWorkLineage
from heteqsys.schema import (
    deep_freeze_json,
    normalize_json,
    semantic_hash,
)


EXECUTION_TRACE_SCHEMA_VERSION = "arqsim.execution-trace.v3"


class TraceValidationError(ValueError):
    """Raised when a serialized causal trace is internally inconsistent."""


class TraceReplayError(TraceValidationError):
    """Raised when a trace cannot be replayed against its execution plan."""


class ExecutionPlane(str, Enum):
    PROGRAM = "program"
    RESOURCE = "resource"


class ExecutionTransitionKind(str, Enum):
    DISPATCH = "dispatch"
    COMPLETION = "completion"


@dataclass(frozen=True)
class ProgramContinuationReceipt:
    """Typed Engine authority for a completed Program work item."""

    kind: str
    activated_work_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.kind not in {"complete_source", "activate"}:
            raise TraceValidationError(
                f"Unsupported Program continuation receipt: {self.kind!r}"
            )
        activated = tuple(self.activated_work_ids)
        if any(type(value) is not str or not value for value in activated):
            raise TraceValidationError(
                "Activated Program work ids must be non-empty strings"
            )
        if len(set(activated)) != len(activated):
            raise TraceValidationError("Activated Program work ids must be unique")
        if self.kind == "complete_source" and activated:
            raise TraceValidationError(
                "A complete_source receipt cannot activate continuation work"
            )
        object.__setattr__(self, "activated_work_ids", activated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "activated_work_ids": list(self.activated_work_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProgramContinuationReceipt":
        if not isinstance(data, Mapping) or set(data) != {
            "kind",
            "activated_work_ids",
        }:
            raise TraceValidationError(
                "Program continuation receipt must use its exact typed fields"
            )
        if type(data["activated_work_ids"]) is not list:
            raise TraceValidationError(
                "Program continuation activated_work_ids must be an array"
            )
        return cls(
            kind=data["kind"],
            activated_work_ids=tuple(data["activated_work_ids"]),
        )


def _require_plain_int(value: Any, *, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise TraceValidationError(f"{label} must be an integer >= {minimum}")
    return value


def _require_finite(value: Any, *, label: str) -> float:
    if type(value) not in (int, float):
        raise TraceValidationError(f"{label} must be a finite JSON number")
    result = float(value)
    if not math.isfinite(result):
        raise TraceValidationError(f"{label} must be finite")
    return result


def _require_nonempty_plain_str(value: Any, *, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise TraceValidationError(f"{label} must be a non-empty string")
    return value


def _freeze_mapping(value: Mapping[str, Any], *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TraceValidationError(f"{label} must be a mapping")
    if any(type(key) is not str for key in value):
        raise TraceValidationError(f"{label} keys must be strings")
    return deep_freeze_json(value)


def _freeze_identifier_sequence_mapping(
    value: Mapping[str, Any],
    *,
    label: str,
) -> Mapping[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise TraceValidationError(f"{label} must be a mapping")
    if any(type(key) is not str for key in value):
        raise TraceValidationError(f"{label} keys must be strings")
    for name, identifiers in value.items():
        if type(identifiers) not in (list, tuple):
            raise TraceValidationError(
                f"{label}[{name!r}] must be a JSON array"
            )
        if any(
            type(identifier) is not str or not identifier
            for identifier in identifiers
        ):
            raise TraceValidationError(
                f"{label}[{name!r}] must contain non-empty strings"
            )
    return deep_freeze_json(value)


def _freeze_string_sequence(value: Any, *, label: str) -> tuple[str, ...]:
    if type(value) not in (list, tuple):
        raise TraceValidationError(f"{label} must be a JSON array")
    result = tuple(value)
    if any(type(item) is not str for item in result):
        raise TraceValidationError(f"{label} must contain strings")
    return result


def _json_type_strict_equal(left: Any, right: Any) -> bool:
    """Compare JSON-shaped values without Python's bool/int aliasing."""

    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return set(left) == set(right) and all(
            _json_type_strict_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, (tuple, list)) and isinstance(right, (tuple, list)):
        return len(left) == len(right) and all(
            _json_type_strict_equal(a, b) for a, b in zip(left, right)
        )
    return type(left) is type(right) and left == right


def _require_exact_json_wire(value: Any, *, path: str) -> None:
    """Reject Python container aliases at the canonical trace codec."""

    value_type = type(value)
    if value_type is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise TraceValidationError(
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
            raise TraceValidationError(
                f"{path} must contain only finite JSON numbers"
            )
        return
    if isinstance(value, Mapping):
        raise TraceValidationError(
            f"{path} JSON objects must be plain dictionaries"
        )
    if isinstance(value, (tuple, list)):
        raise TraceValidationError(f"{path} JSON arrays must be plain lists")
    raise TraceValidationError(
        f"{path} contains a non-JSON value of type {value_type.__name__}"
    )


def _exact_json_equal(left: Any, right: Any) -> bool:
    """Compare exact canonical JSON types without Python scalar aliases."""

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


# These fields are projected by the fixed Event Engine when it commits an
# operation. They belong to trace replay validation, not to a runtime
# component's candidate contract.
_TRACE_PROJECTED_METADATA_FIELDS = frozenset(
    {
        "attempted_outputs",
        "batch_amount",
        "buffered_outputs",
        "discarded_outputs",
        "dispatch_policy",
        "engines",
        "layer",
        "program_ready_s",
        "protocol",
        "qubits",
        "resource_wait_s",
        "target_links",
        "target_modules",
    }
)


@dataclass(frozen=True)
class TraceStateProjection:
    """Exact replay-relevant projection of one ``ArchitectureState``.

    Capacity and static slot inventories live in the referenced ExecutionPlan.
    This record contains every dynamic field needed to compare a replay with the
    state that the Event Engine actually reached.
    """

    state_version: int = 0
    buffers: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    pending_incoming: Mapping[str, int] = field(default_factory=dict)
    reserved_output_slots: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    engine_usage: Mapping[str, int] = field(default_factory=dict)
    locations: Mapping[str, str] = field(default_factory=dict)
    token_slots: Mapping[str, str] = field(default_factory=dict)
    token_sequence: Mapping[str, int] = field(default_factory=dict)
    active_reservation_ids: tuple[int, ...] = field(default_factory=tuple)
    next_reservation_id: int = 0

    def __post_init__(self) -> None:
        _require_plain_int(self.state_version, label="state_version")
        _require_plain_int(self.next_reservation_id, label="next_reservation_id")
        if type(self.active_reservation_ids) not in (list, tuple):
            raise TraceValidationError(
                "active_reservation_ids must be a JSON array"
            )
        active = tuple(
            _require_plain_int(value, label="active reservation id")
            for value in self.active_reservation_ids
        )
        if len(active) != len(set(active)) or active != tuple(sorted(active)):
            raise TraceValidationError(
                "active_reservation_ids must be unique and sorted"
            )
        object.__setattr__(self, "active_reservation_ids", active)
        for name in ("buffers", "reserved_output_slots"):
            object.__setattr__(
                self,
                name,
                _freeze_identifier_sequence_mapping(
                    getattr(self, name), label=name
                ),
            )
        for name in (
            "pending_incoming",
            "engine_usage",
            "locations",
            "token_slots",
            "token_sequence",
        ):
            object.__setattr__(
                self,
                name,
                _freeze_mapping(getattr(self, name), label=name),
            )
        for label, values in (
            ("pending_incoming", self.pending_incoming),
            ("engine_usage", self.engine_usage),
            ("token_sequence", self.token_sequence),
        ):
            for name, raw_amount in values.items():
                _require_plain_int(raw_amount, label=f"{label}[{name!r}]")
        for label, values in (
            ("locations", self.locations),
            ("token_slots", self.token_slots),
        ):
            if any(type(value) is not str or not value for value in values.values()):
                raise TraceValidationError(
                    f"{label} values must be non-empty strings"
                )

    @classmethod
    def from_snapshot(cls, snapshot: Any) -> "TraceStateProjection":
        return cls(
            state_version=int(snapshot.version),
            buffers={
                str(name): tuple(buffer.ready_tokens)
                for name, buffer in snapshot.buffers.items()
            },
            pending_incoming={
                str(name): int(buffer.pending_outputs)
                for name, buffer in snapshot.buffers.items()
            },
            reserved_output_slots={
                str(name): tuple(sorted(buffer.reserved_output_slots))
                for name, buffer in snapshot.buffers.items()
            },
            engine_usage={
                str(name): int(engine.users)
                for name, engine in snapshot.engines.items()
            },
            locations=dict(snapshot.locations),
            token_slots=dict(snapshot.token_slots),
            token_sequence=dict(snapshot.token_sequence),
            active_reservation_ids=tuple(sorted(snapshot.active_reservation_ids)),
            next_reservation_id=int(snapshot.next_reservation_id),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_version": self.state_version,
            "buffers": normalize_json(self.buffers),
            "pending_incoming": normalize_json(self.pending_incoming),
            "reserved_output_slots": normalize_json(self.reserved_output_slots),
            "engine_usage": normalize_json(self.engine_usage),
            "locations": normalize_json(self.locations),
            "token_slots": normalize_json(self.token_slots),
            "token_sequence": normalize_json(self.token_sequence),
            "active_reservation_ids": list(self.active_reservation_ids),
            "next_reservation_id": self.next_reservation_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TraceStateProjection":
        if not isinstance(data, Mapping):
            raise TraceValidationError("Trace state projection must be a mapping")
        allowed = {
            "state_version",
            "buffers",
            "pending_incoming",
            "reserved_output_slots",
            "engine_usage",
            "locations",
            "token_slots",
            "token_sequence",
            "active_reservation_ids",
            "next_reservation_id",
        }
        unknown = set(data) - allowed
        if unknown:
            raise TraceValidationError(
                f"Unknown trace-state fields: {sorted(unknown)}"
            )
        missing = allowed - set(data)
        if missing:
            raise TraceValidationError(
                f"Trace state projection is missing: {sorted(missing)}"
            )
        active_ids = data["active_reservation_ids"]
        if type(active_ids) not in (list, tuple):
            raise TraceValidationError(
                "active_reservation_ids must be a JSON array"
            )
        return cls(
            state_version=data["state_version"],
            buffers=data["buffers"],
            pending_incoming=data["pending_incoming"],
            reserved_output_slots=data["reserved_output_slots"],
            engine_usage=data["engine_usage"],
            locations=data["locations"],
            token_slots=data["token_slots"],
            token_sequence=data["token_sequence"],
            active_reservation_ids=tuple(active_ids),
            next_reservation_id=data["next_reservation_id"],
        )


@dataclass(frozen=True)
class ExecutionTransition:
    """One append-only committed dispatch or completion state transition."""

    transition_id: int
    kind: ExecutionTransitionKind | str
    time_s: float
    event_id: int
    reservation_id: int
    state_version_before: int
    state_version_after: int
    plane: ExecutionPlane | str
    opcode: ArchitectureOpcode | str
    instruction_id: int | None
    process_id: str | None
    instance: int | None
    candidate_id: str
    start_s: float
    end_s: float
    wait_reasons: tuple[str, ...] = field(default_factory=tuple)
    consumes: Mapping[str, int] = field(default_factory=dict)
    consumed_tokens: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    consumed_slots: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    produces: Mapping[str, int] = field(default_factory=dict)
    produced_slots: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    produced_tokens: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    forwards: Mapping[str, str] = field(default_factory=dict)
    engines: Mapping[str, int] = field(default_factory=dict)
    required_locations: Mapping[str, str] = field(default_factory=dict)
    completion_locations: Mapping[str, str] = field(default_factory=dict)
    token_sequence_after: Mapping[str, int] = field(default_factory=dict)
    buffer_occupancy_after: Mapping[str, int] = field(default_factory=dict)
    pending_incoming_after: Mapping[str, int] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    backend_artifact: Mapping[str, Any] = field(default_factory=dict)
    outcome: Mapping[str, Any] = field(default_factory=dict)
    program_lineage: ProgramWorkLineage | None = None
    measurements: Mapping[str, int] = field(default_factory=dict)
    continuation: ProgramContinuationReceipt | None = None

    def __post_init__(self) -> None:
        _require_plain_int(self.transition_id, label="transition_id")
        _require_plain_int(self.event_id, label="event_id")
        _require_plain_int(self.reservation_id, label="reservation_id")
        _require_plain_int(self.state_version_before, label="state_version_before")
        _require_plain_int(self.state_version_after, label="state_version_after")
        if self.state_version_after != self.state_version_before + 1:
            raise TraceValidationError(
                "Every execution transition must advance state version by one"
            )
        time_s = _require_finite(self.time_s, label="transition time_s")
        start_s = _require_finite(self.start_s, label="transition start_s")
        end_s = _require_finite(self.end_s, label="transition end_s")
        if start_s > end_s:
            raise TraceValidationError("Execution transition end_s precedes start_s")
        if time_s < 0 or start_s < 0:
            raise TraceValidationError(
                "Execution transition times cannot be negative"
            )
        kind = ExecutionTransitionKind(self.kind)
        if kind == ExecutionTransitionKind.DISPATCH and time_s != start_s:
            raise TraceValidationError("Dispatch transition time must equal start_s")
        if kind == ExecutionTransitionKind.COMPLETION and time_s != end_s:
            raise TraceValidationError("Completion transition time must equal end_s")
        plane = ExecutionPlane(self.plane)
        opcode = ArchitectureOpcode(self.opcode)
        candidate_id = _require_nonempty_plain_str(
            self.candidate_id, label="candidate_id"
        )
        if plane == ExecutionPlane.PROGRAM:
            if self.process_id is not None or self.instance is not None:
                raise TraceValidationError(
                    "Program transition requires an integer instruction_id and no process/instance"
                )
            _require_plain_int(self.instruction_id, label="instruction_id")
            if not isinstance(self.program_lineage, ProgramWorkLineage):
                raise TraceValidationError(
                    "Program transitions require typed Program work lineage"
                )
            if self.program_lineage.source_instruction_id != self.instruction_id:
                raise TraceValidationError(
                    "Program lineage source does not match instruction_id"
                )
        else:
            _require_nonempty_plain_str(self.process_id, label="process_id")
            _require_plain_int(self.instance, label="resource instance")
            if self.instruction_id is not None:
                raise TraceValidationError(
                    "Resource transition cannot identify a Program instruction"
                )
            if self.program_lineage is not None:
                raise TraceValidationError(
                    "Resource transitions cannot carry Program lineage"
                )
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "plane", plane)
        object.__setattr__(self, "opcode", opcode)
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "time_s", time_s)
        object.__setattr__(self, "start_s", start_s)
        object.__setattr__(self, "end_s", end_s)
        wait_reasons = _freeze_string_sequence(
            self.wait_reasons, label="wait_reasons"
        )
        object.__setattr__(self, "wait_reasons", wait_reasons)
        for name in (
            "consumed_tokens",
            "consumed_slots",
            "produced_slots",
            "produced_tokens",
        ):
            object.__setattr__(
                self,
                name,
                _freeze_identifier_sequence_mapping(
                    getattr(self, name), label=name
                ),
            )
        for name in (
            "consumes",
            "produces",
            "forwards",
            "engines",
            "required_locations",
            "completion_locations",
            "token_sequence_after",
            "buffer_occupancy_after",
            "pending_incoming_after",
            "metadata",
            "backend_artifact",
            "outcome",
            "measurements",
        ):
            object.__setattr__(
                self,
                name,
                _freeze_mapping(getattr(self, name), label=name),
            )
        if kind == ExecutionTransitionKind.DISPATCH and self.produced_tokens:
            raise TraceValidationError(
                "Dispatch transitions cannot contain completed output tokens"
            )
        if kind == ExecutionTransitionKind.DISPATCH and (
            self.outcome or self.token_sequence_after or self.measurements
        ):
            raise TraceValidationError(
                "Dispatch transitions cannot contain completion outcome/sequence data"
            )
        if kind == ExecutionTransitionKind.DISPATCH and self.continuation is not None:
            raise TraceValidationError(
                "Dispatch transitions cannot contain a continuation receipt"
            )
        if kind == ExecutionTransitionKind.COMPLETION:
            if plane == ExecutionPlane.PROGRAM and not isinstance(
                self.continuation, ProgramContinuationReceipt
            ):
                raise TraceValidationError(
                    "Program completion requires a typed continuation receipt"
                )
            if plane == ExecutionPlane.RESOURCE and self.continuation is not None:
                raise TraceValidationError(
                    "Resource completion cannot contain a Program continuation"
                )
        for register_id, bit in self.measurements.items():
            _require_nonempty_plain_str(register_id, label="measurement register")
            if type(bit) is not int or bit not in {0, 1}:
                raise TraceValidationError("Measurement results must be exact bits")
        raw_measurements = self.outcome.get("measurements", [])
        if raw_measurements:
            if type(raw_measurements) not in {list, tuple}:
                raise TraceValidationError("Outcome measurements must be an array")
            projected: dict[str, int] = {}
            for item in raw_measurements:
                if not isinstance(item, Mapping) or set(item) != {"register_id", "bit"}:
                    raise TraceValidationError(
                        "Outcome measurements must use typed register/bit records"
                    )
                register_id = _require_nonempty_plain_str(
                    item["register_id"], label="outcome measurement register"
                )
                bit = item["bit"]
                if type(bit) is not int or bit not in {0, 1} or register_id in projected:
                    raise TraceValidationError("Outcome measurements must be unique bits")
                projected[register_id] = bit
            if projected != dict(self.measurements):
                raise TraceValidationError(
                    "Typed measurements disagree with the outcome payload"
                )
        elif self.measurements:
            raise TraceValidationError(
                "Typed measurements require the matching state outcome payload"
            )
        if set(self.consumed_tokens) != set(self.consumes) or set(
            self.consumed_slots
        ) != set(self.consumes):
            raise TraceValidationError(
                "Concrete consumed token/slot claims must match consumes"
            )
        if set(self.produced_slots) != set(self.produces):
            raise TraceValidationError(
                "Reserved output-slot claims must match produces"
            )
        for name, raw_amount in self.consumes.items():
            amount = _require_plain_int(
                raw_amount, label=f"consumes[{name!r}]", minimum=1
            )
            if (
                len(self.consumed_tokens[name]) != amount
                or len(self.consumed_slots[name]) != amount
            ):
                raise TraceValidationError(
                    f"Consumed token/slot cardinality mismatch for {name}"
                )
        for name, raw_amount in self.produces.items():
            amount = _require_plain_int(
                raw_amount, label=f"produces[{name!r}]", minimum=1
            )
            if len(self.produced_slots[name]) != amount:
                raise TraceValidationError(
                    f"Produced-slot cardinality mismatch for {name}"
                )
        if kind == ExecutionTransitionKind.COMPLETION:
            if set(self.produced_tokens) != set(self.produces):
                raise TraceValidationError(
                    "Completion output-token claims must match produces"
                )
            for name, amount in self.produces.items():
                if len(self.produced_tokens[name]) != amount:
                    raise TraceValidationError(
                        f"Produced-token cardinality mismatch for {name}"
                    )
        for name, raw_amount in self.engines.items():
            _require_plain_int(
                raw_amount, label=f"engines[{name!r}]", minimum=1
            )
        for label, values in (
            ("buffer_occupancy_after", self.buffer_occupancy_after),
            ("pending_incoming_after", self.pending_incoming_after),
            ("token_sequence_after", self.token_sequence_after),
        ):
            for name, raw_amount in values.items():
                _require_plain_int(
                    raw_amount, label=f"{label}[{name!r}]"
                )

    def same_event_identity(self, other: "ExecutionTransition") -> bool:
        return (
            self.event_id,
            self.reservation_id,
            self.plane,
            self.opcode,
            self.instruction_id,
            self.process_id,
            self.instance,
            self.candidate_id,
            self.program_lineage,
            self.start_s,
            self.end_s,
        ) == (
            other.event_id,
            other.reservation_id,
            other.plane,
            other.opcode,
            other.instruction_id,
            other.process_id,
            other.instance,
            other.candidate_id,
            other.program_lineage,
            other.start_s,
            other.end_s,
        )

    def reservation_facts(self) -> tuple[Any, ...]:
        return (
            self.consumes,
            self.consumed_tokens,
            self.consumed_slots,
            self.produces,
            self.produced_slots,
            self.forwards,
            self.engines,
            self.required_locations,
            self.completion_locations,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "transition_id": self.transition_id,
            "kind": ExecutionTransitionKind(self.kind).value,
            "time_s": self.time_s,
            "event_id": self.event_id,
            "reservation_id": self.reservation_id,
            "state_version_before": self.state_version_before,
            "state_version_after": self.state_version_after,
            "plane": ExecutionPlane(self.plane).value,
            "opcode": ArchitectureOpcode(self.opcode).value,
            "instruction_id": self.instruction_id,
            "process_id": self.process_id,
            "instance": self.instance,
            "candidate_id": self.candidate_id,
            "start_s": self.start_s,
            "end_s": self.end_s,
            "wait_reasons": list(self.wait_reasons),
            "consumes": normalize_json(self.consumes),
            "consumed_tokens": normalize_json(self.consumed_tokens),
            "consumed_slots": normalize_json(self.consumed_slots),
            "produces": normalize_json(self.produces),
            "produced_slots": normalize_json(self.produced_slots),
            "produced_tokens": normalize_json(self.produced_tokens),
            "forwards": normalize_json(self.forwards),
            "engines": normalize_json(self.engines),
            "required_locations": normalize_json(self.required_locations),
            "completion_locations": normalize_json(self.completion_locations),
            "token_sequence_after": normalize_json(self.token_sequence_after),
            "buffer_occupancy_after": normalize_json(self.buffer_occupancy_after),
            "pending_incoming_after": normalize_json(self.pending_incoming_after),
            "metadata": normalize_json(self.metadata),
            "backend_artifact": normalize_json(self.backend_artifact),
            "outcome": normalize_json(self.outcome),
            "program_lineage": (
                self.program_lineage.to_dict()
                if self.program_lineage is not None
                else None
            ),
            "measurements": normalize_json(self.measurements),
            "continuation": (
                self.continuation.to_dict()
                if self.continuation is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionTransition":
        if not isinstance(data, Mapping):
            raise TraceValidationError("Execution transition must be a mapping")
        required = {
            "transition_id",
            "kind",
            "time_s",
            "event_id",
            "reservation_id",
            "state_version_before",
            "state_version_after",
            "plane",
            "opcode",
            "instruction_id",
            "process_id",
            "instance",
            "candidate_id",
            "start_s",
            "end_s",
            "wait_reasons",
            "consumes",
            "consumed_tokens",
            "consumed_slots",
            "produces",
            "produced_slots",
            "produced_tokens",
            "forwards",
            "engines",
            "required_locations",
            "completion_locations",
            "token_sequence_after",
            "buffer_occupancy_after",
            "pending_incoming_after",
            "metadata",
            "backend_artifact",
            "outcome",
            "program_lineage",
            "measurements",
            "continuation",
        }
        unknown = set(data) - required
        missing = required - set(data)
        if unknown:
            raise TraceValidationError(
                f"Unknown execution-transition fields: {sorted(unknown)}"
            )
        if missing:
            raise TraceValidationError(
                f"Execution transition is missing: {sorted(missing)}"
            )
        values = {name: data[name] for name in required}
        raw_lineage = values["program_lineage"]
        raw_continuation = values["continuation"]
        if raw_lineage is not None:
            if not isinstance(raw_lineage, Mapping):
                raise TraceValidationError("Program lineage must be a mapping or null")
            values["program_lineage"] = ProgramWorkLineage.from_dict(raw_lineage)
        if raw_continuation is not None:
            if not isinstance(raw_continuation, Mapping):
                raise TraceValidationError(
                    "Program continuation must be a mapping or null"
                )
            values["continuation"] = ProgramContinuationReceipt.from_dict(
                raw_continuation
            )
        return cls(**values)


@dataclass(frozen=True)
class ExecutionEvent:
    """Compatibility timeline span for one event that completed by the horizon."""

    event_id: int
    plane: ExecutionPlane | str
    opcode: ArchitectureOpcode | str
    instruction_id: int | None
    process_id: str | None
    instance: int | None
    start_s: float
    end_s: float
    reservation_id: int
    candidate_id: str = ""
    wait_reasons: tuple[str, ...] = field(default_factory=tuple)
    consumed_tokens: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    consumed_slots: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    produced_slots: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    produced_tokens: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    buffer_occupancy_start: Mapping[str, int] = field(default_factory=dict)
    buffer_occupancy_end: Mapping[str, int] = field(default_factory=dict)
    pending_incoming_start: Mapping[str, int] = field(default_factory=dict)
    pending_incoming_end: Mapping[str, int] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    backend_artifact: Mapping[str, Any] = field(default_factory=dict)
    outcome: Mapping[str, Any] = field(default_factory=dict)
    program_lineage: ProgramWorkLineage | None = None
    measurements: Mapping[str, int] = field(default_factory=dict)
    continuation: ProgramContinuationReceipt | None = None

    def __post_init__(self) -> None:
        _require_plain_int(self.event_id, label="event_id")
        _require_plain_int(self.reservation_id, label="reservation_id")
        plane = ExecutionPlane(self.plane)
        opcode = ArchitectureOpcode(self.opcode)
        start_s = _require_finite(self.start_s, label="event start_s")
        end_s = _require_finite(self.end_s, label="event end_s")
        if end_s < start_s:
            raise TraceValidationError("ExecutionEvent end_s precedes start_s")
        if start_s < 0:
            raise TraceValidationError("ExecutionEvent start_s cannot be negative")
        candidate_id = _require_nonempty_plain_str(
            self.candidate_id, label="candidate_id"
        )
        if plane == ExecutionPlane.PROGRAM:
            if self.process_id is not None or self.instance is not None:
                raise TraceValidationError(
                    "Program event requires an integer instruction_id and no process/instance"
                )
            _require_plain_int(self.instruction_id, label="instruction_id")
            if not isinstance(self.program_lineage, ProgramWorkLineage):
                raise TraceValidationError(
                    "Program events require typed Program work lineage"
                )
        else:
            _require_nonempty_plain_str(self.process_id, label="process_id")
            _require_plain_int(self.instance, label="resource instance")
            if self.instruction_id is not None:
                raise TraceValidationError(
                    "Resource event cannot identify a Program instruction"
                )
            if self.program_lineage is not None or self.continuation is not None:
                raise TraceValidationError(
                    "Resource events cannot carry Program continuation facts"
                )
        if plane == ExecutionPlane.PROGRAM and not isinstance(
            self.continuation, ProgramContinuationReceipt
        ):
            raise TraceValidationError(
                "Completed Program events require a continuation receipt"
            )
        object.__setattr__(self, "plane", plane)
        object.__setattr__(self, "opcode", opcode)
        object.__setattr__(self, "candidate_id", candidate_id)
        object.__setattr__(self, "start_s", start_s)
        object.__setattr__(self, "end_s", end_s)
        wait_reasons = _freeze_string_sequence(
            self.wait_reasons, label="wait_reasons"
        )
        object.__setattr__(self, "wait_reasons", wait_reasons)
        for name in (
            "consumed_tokens",
            "consumed_slots",
            "produced_slots",
            "produced_tokens",
        ):
            object.__setattr__(
                self,
                name,
                _freeze_identifier_sequence_mapping(
                    getattr(self, name), label=name
                ),
            )
        for name in (
            "buffer_occupancy_start",
            "buffer_occupancy_end",
            "pending_incoming_start",
            "pending_incoming_end",
            "metadata",
            "backend_artifact",
            "outcome",
            "measurements",
        ):
            object.__setattr__(
                self,
                name,
                _freeze_mapping(getattr(self, name), label=name),
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "plane": ExecutionPlane(self.plane).value,
            "opcode": ArchitectureOpcode(self.opcode).value,
            "instruction_id": self.instruction_id,
            "process_id": self.process_id,
            "instance": self.instance,
            "start_s": self.start_s,
            "end_s": self.end_s,
            "duration_s": self.end_s - self.start_s,
            "reservation_id": self.reservation_id,
            "candidate_id": self.candidate_id,
            "wait_reasons": list(self.wait_reasons),
            "consumed_tokens": normalize_json(self.consumed_tokens),
            "consumed_slots": normalize_json(self.consumed_slots),
            "produced_slots": normalize_json(self.produced_slots),
            "produced_tokens": normalize_json(self.produced_tokens),
            "buffer_occupancy_start": normalize_json(self.buffer_occupancy_start),
            "buffer_occupancy_end": normalize_json(self.buffer_occupancy_end),
            "pending_incoming_start": normalize_json(self.pending_incoming_start),
            "pending_incoming_end": normalize_json(self.pending_incoming_end),
            "metadata": normalize_json(self.metadata),
            "backend_artifact": normalize_json(self.backend_artifact),
            "outcome": normalize_json(self.outcome),
            "program_lineage": (
                self.program_lineage.to_dict()
                if self.program_lineage is not None
                else None
            ),
            "measurements": normalize_json(self.measurements),
            "continuation": (
                self.continuation.to_dict()
                if self.continuation is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionEvent":
        if not isinstance(data, Mapping):
            raise TraceValidationError("ExecutionEvent must be a mapping")
        allowed = {
            "event_id",
            "plane",
            "opcode",
            "instruction_id",
            "process_id",
            "instance",
            "start_s",
            "end_s",
            "duration_s",
            "reservation_id",
            "candidate_id",
            "wait_reasons",
            "consumed_tokens",
            "consumed_slots",
            "produced_slots",
            "produced_tokens",
            "buffer_occupancy_start",
            "buffer_occupancy_end",
            "pending_incoming_start",
            "pending_incoming_end",
            "metadata",
            "backend_artifact",
            "outcome",
            "program_lineage",
            "measurements",
            "continuation",
        }
        unknown = set(data) - allowed
        if unknown:
            raise TraceValidationError(
                f"Unknown ExecutionEvent fields: {sorted(unknown)}"
            )
        required = allowed - {"duration_s", "candidate_id", "backend_artifact", "outcome"}
        missing = required - set(data)
        if missing:
            raise TraceValidationError(
                f"ExecutionEvent is missing: {sorted(missing)}"
            )
        raw_lineage = data["program_lineage"]
        raw_continuation = data["continuation"]
        if raw_lineage is not None and not isinstance(raw_lineage, Mapping):
            raise TraceValidationError("Event Program lineage must be a mapping")
        if raw_continuation is not None and not isinstance(
            raw_continuation, Mapping
        ):
            raise TraceValidationError("Event Program continuation must be a mapping")
        result = cls(
            event_id=data["event_id"],
            plane=data["plane"],
            opcode=data["opcode"],
            instruction_id=data["instruction_id"],
            process_id=data["process_id"],
            instance=data["instance"],
            start_s=data["start_s"],
            end_s=data["end_s"],
            reservation_id=data["reservation_id"],
            candidate_id=data.get("candidate_id", ""),
            wait_reasons=data["wait_reasons"],
            consumed_tokens=data["consumed_tokens"],
            consumed_slots=data["consumed_slots"],
            produced_slots=data["produced_slots"],
            produced_tokens=data["produced_tokens"],
            buffer_occupancy_start=data["buffer_occupancy_start"],
            buffer_occupancy_end=data["buffer_occupancy_end"],
            pending_incoming_start=data["pending_incoming_start"],
            pending_incoming_end=data["pending_incoming_end"],
            metadata=data["metadata"],
            backend_artifact=data.get("backend_artifact", {}),
            outcome=data.get("outcome", {}),
            program_lineage=(
                ProgramWorkLineage.from_dict(raw_lineage)
                if raw_lineage is not None
                else None
            ),
            measurements=data["measurements"],
            continuation=(
                ProgramContinuationReceipt.from_dict(raw_continuation)
                if raw_continuation is not None
                else None
            ),
        )
        duration = data.get("duration_s")
        if duration is not None and _require_finite(
            duration, label="event duration_s"
        ) != result.end_s - result.start_s:
            raise TraceValidationError(
                f"ExecutionEvent {result.event_id} duration_s is inconsistent"
            )
        return result


@dataclass(frozen=True)
class ExecutionTrace:
    """Canonical v3 causal ledger.

    Completed event spans are a compatibility projection of the ordered
    transitions and initial state. They are deliberately neither constructor
    input nor serialized/hash authority.
    """

    plan_hash: str
    seed: int
    total_latency_s: float
    transitions: tuple[ExecutionTransition, ...] = field(default_factory=tuple)
    initial_state: TraceStateProjection = field(default_factory=TraceStateProjection)
    terminal_state: TraceStateProjection = field(default_factory=TraceStateProjection)
    terminal_inflight: tuple[ExecutionTransition, ...] = field(default_factory=tuple)
    _events: tuple[ExecutionEvent, ...] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _trace_hash: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_nonempty_plain_str(self.plan_hash, label="plan_hash")
        latency = _require_finite(self.total_latency_s, label="total_latency_s")
        if latency < 0:
            raise TraceValidationError("total_latency_s cannot be negative")
        if type(self.seed) is not int:
            raise TraceValidationError("seed must be an integer")
        transitions = tuple(self.transitions)
        terminal_inflight = tuple(self.terminal_inflight)
        if not all(
            isinstance(item, ExecutionTransition) for item in transitions
        ):
            raise TraceValidationError(
                "transitions must contain ExecutionTransition values"
            )
        if not all(
            isinstance(item, ExecutionTransition) for item in terminal_inflight
        ):
            raise TraceValidationError(
                "terminal_inflight must contain dispatch ExecutionTransition values"
            )
        if not isinstance(self.initial_state, TraceStateProjection) or not isinstance(
            self.terminal_state, TraceStateProjection
        ):
            raise TraceValidationError(
                "initial_state and terminal_state must be TraceStateProjection values"
            )
        object.__setattr__(self, "total_latency_s", latency)
        object.__setattr__(self, "transitions", transitions)
        object.__setattr__(self, "terminal_inflight", terminal_inflight)
        self._validate_causal_ledger()
        object.__setattr__(self, "_events", _derive_execution_events(self))
        # Transitions and state projections are immutable typed values at this
        # point. Hashing the full causal ledger is O(report
        # size), so compute it once rather than once per report cross-check.
        object.__setattr__(self, "_trace_hash", semantic_hash(self.semantic_dict()))

    def _validate_causal_ledger(self) -> None:
        if not self.transitions:
            if self.terminal_inflight:
                raise TraceValidationError(
                    "In-flight events require a causal transition ledger"
                )
            if self.initial_state != self.terminal_state:
                raise TraceValidationError(
                    "An empty transition ledger cannot change architectural state"
                )
            if self.total_latency_s != 0:
                raise TraceValidationError(
                    "An empty Program execution must have a zero horizon"
                )
            return

        active: dict[int, ExecutionTransition] = {}
        completed: dict[int, ExecutionTransition] = {}
        activated_program_work: dict[str, int] = {}
        dispatched_program_work: set[str] = set()
        completed_sources: set[int] = set()
        previous_time = -math.inf
        expected_version = self.initial_state.state_version
        for expected_id, transition in enumerate(self.transitions):
            if transition.transition_id != expected_id:
                raise TraceValidationError(
                    "Execution transition ids must be contiguous append order"
                )
            if transition.time_s < previous_time:
                raise TraceValidationError(
                    "Execution transition times must be nondecreasing"
                )
            if transition.time_s > self.total_latency_s:
                raise TraceValidationError(
                    "Execution transition occurs after the Program horizon"
                )
            if transition.state_version_before != expected_version:
                raise TraceValidationError(
                    "Execution transition state versions are not contiguous"
                )
            previous_time = transition.time_s
            expected_version = transition.state_version_after
            if transition.kind == ExecutionTransitionKind.DISPATCH:
                if transition.event_id in active or transition.event_id in completed:
                    raise TraceValidationError(
                        f"Event {transition.event_id} was dispatched more than once"
                    )
                if transition.plane == ExecutionPlane.PROGRAM:
                    assert transition.program_lineage is not None
                    lineage = transition.program_lineage
                    if lineage.work_id != transition.candidate_id:
                        raise TraceValidationError(
                            "Program candidate_id must equal its stable work_id"
                        )
                    if lineage.work_id in dispatched_program_work:
                        raise TraceValidationError(
                            f"Program work {lineage.work_id!r} was dispatched twice"
                        )
                    if lineage.step == "source":
                        if lineage.work_id != f"program:{lineage.source_instruction_id}":
                            raise TraceValidationError(
                                "Static source work must use its canonical identity"
                            )
                    else:
                        parent = activated_program_work.get(lineage.work_id)
                        if parent is None or parent != lineage.parent_event_id:
                            raise TraceValidationError(
                                "Continuation work was not activated by its typed parent"
                            )
                    dispatched_program_work.add(lineage.work_id)
                active[transition.event_id] = transition
                continue
            dispatch = active.pop(transition.event_id, None)
            if dispatch is None:
                raise TraceValidationError(
                    f"Event {transition.event_id} completed without an active dispatch"
                )
            if not dispatch.same_event_identity(transition):
                raise TraceValidationError(
                    f"Event {transition.event_id} changed identity before completion"
                )
            if dispatch.reservation_facts() != transition.reservation_facts():
                raise TraceValidationError(
                    f"Event {transition.event_id} changed reservation facts"
                )
            if (
                dispatch.wait_reasons != transition.wait_reasons
                or dispatch.metadata != transition.metadata
                or dispatch.backend_artifact != transition.backend_artifact
            ):
                raise TraceValidationError(
                    f"Event {transition.event_id} changed execution context"
                )
            completed[transition.event_id] = transition
            if transition.plane == ExecutionPlane.PROGRAM:
                assert transition.continuation is not None
                if transition.continuation.kind == "complete_source":
                    source_id = int(transition.instruction_id)
                    if source_id in completed_sources:
                        raise TraceValidationError(
                            f"Program source {source_id} completed more than once"
                        )
                    completed_sources.add(source_id)
                else:
                    for work_id in transition.continuation.activated_work_ids:
                        if work_id in activated_program_work or work_id in dispatched_program_work:
                            raise TraceValidationError(
                                f"Program work {work_id!r} was activated more than once"
                            )
                        activated_program_work[work_id] = transition.event_id

        if expected_version != self.terminal_state.state_version:
            raise TraceValidationError(
                "Terminal state version does not match the causal ledger"
            )
        expected_inflight = tuple(active[event_id] for event_id in sorted(active))
        if self.terminal_inflight != expected_inflight:
            raise TraceValidationError(
                "terminal_inflight does not equal unmatched dispatch transitions"
            )
        if any(
            item.plane != ExecutionPlane.RESOURCE
            or item.start_s > self.total_latency_s
            or item.end_s <= self.total_latency_s
            for item in expected_inflight
        ):
            raise TraceValidationError(
                "Only unfinished Resource dispatches may remain at the Program horizon"
            )
        if self.terminal_state.active_reservation_ids != tuple(
            item.reservation_id for item in expected_inflight
        ):
            raise TraceValidationError(
                "Terminal active reservations do not match terminal_inflight"
            )
        undispatched_activations = set(activated_program_work) - dispatched_program_work
        if undispatched_activations:
            raise TraceValidationError(
                "Program horizon contains activated but undispatched continuation work"
            )
        program_completion_times = [
            item.end_s
            for item in completed.values()
            if item.plane == ExecutionPlane.PROGRAM
            and item.continuation is not None
            and item.continuation.kind == "complete_source"
        ]
        if not program_completion_times or max(program_completion_times) != self.total_latency_s:
            raise TraceValidationError(
                "total_latency_s must equal the final Program completion"
            )

    @property
    def events(self) -> tuple[ExecutionEvent, ...]:
        """Return completed v1-style spans derived from the causal ledger."""

        return self._events

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EXECUTION_TRACE_SCHEMA_VERSION,
            "plan_hash": self.plan_hash,
            "seed": self.seed,
            "total_latency_s": self.total_latency_s,
            "transitions": [item.to_dict() for item in self.transitions],
            "initial_state": self.initial_state.to_dict(),
            "terminal_state": self.terminal_state.to_dict(),
            "terminal_inflight": [item.to_dict() for item in self.terminal_inflight],
        }

    @property
    def trace_hash(self) -> str:
        return self._trace_hash

    def to_dict(self) -> dict[str, Any]:
        result = self.semantic_dict()
        result["trace_hash"] = self.trace_hash
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
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionTrace":
        if type(data) is not dict:
            raise TraceValidationError(
                "ExecutionTrace document must be a plain dictionary"
            )
        _require_exact_json_wire(data, path="ExecutionTrace")
        if data.get("schema_version") != EXECUTION_TRACE_SCHEMA_VERSION:
            raise TraceValidationError(
                "Unsupported execution-trace schema: "
                f"{data.get('schema_version')!r}"
            )
        required = {
            "plan_hash",
            "seed",
            "total_latency_s",
            "transitions",
            "initial_state",
            "terminal_state",
            "terminal_inflight",
            "trace_hash",
        }
        missing = required - set(data)
        unknown = set(data) - required - {"schema_version"}
        if unknown:
            raise TraceValidationError(
                f"Unknown ExecutionTrace fields: {sorted(unknown)}"
            )
        if missing:
            raise TraceValidationError(
                f"ExecutionTrace document is missing: {sorted(missing)}"
            )
        try:
            result = cls(
                plan_hash=data["plan_hash"],
                seed=data["seed"],
                total_latency_s=data["total_latency_s"],
                transitions=tuple(
                    ExecutionTransition.from_dict(item)
                    for item in data["transitions"]
                ),
                initial_state=TraceStateProjection.from_dict(data["initial_state"]),
                terminal_state=TraceStateProjection.from_dict(data["terminal_state"]),
                terminal_inflight=tuple(
                    ExecutionTransition.from_dict(item)
                    for item in data["terminal_inflight"]
                ),
            )
        except TypeError as exc:
            raise TraceValidationError(
                "ExecutionTrace arrays contain invalid values"
            ) from exc
        unsigned = {
            name: value for name, value in data.items() if name != "trace_hash"
        }
        if not _exact_json_equal(unsigned, result.semantic_dict()):
            raise TraceValidationError(
                "ExecutionTrace input is not the exact canonical v3 wire form"
            )
        if data["trace_hash"] != result.trace_hash:
            raise TraceValidationError("ExecutionTrace trace_hash does not match content")
        return result

    @classmethod
    def from_json(cls, text: str) -> "ExecutionTrace":
        """Parse the strict canonical JSON boundary, rejecting duplicate keys."""

        if type(text) is not str:
            raise TypeError("ExecutionTrace JSON document must be a string")

        def reject_constant(value: str) -> None:
            raise TraceValidationError(
                f"Non-finite JSON constant is not allowed in a trace: {value}"
            )

        def reject_duplicate_keys(
            pairs: list[tuple[str, Any]],
        ) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise TraceValidationError(
                        f"Duplicate JSON object key in execution trace: {key!r}"
                    )
                result[key] = value
            return result

        try:
            data = json.loads(
                text,
                parse_constant=reject_constant,
                object_pairs_hook=reject_duplicate_keys,
            )
        except json.JSONDecodeError as exc:
            raise TraceValidationError("ExecutionTrace JSON is invalid") from exc
        if type(data) is not dict:
            raise TraceValidationError("ExecutionTrace JSON must contain an object")
        return cls.from_dict(data)


def _derive_execution_events(
    trace: ExecutionTrace,
) -> tuple[ExecutionEvent, ...]:
    """Project completed v1 spans from one already-validated causal ledger."""

    occupancy = {
        name: len(tokens) for name, tokens in trace.initial_state.buffers.items()
    }
    pending = dict(trace.initial_state.pending_incoming)
    dispatches: dict[
        int,
        tuple[ExecutionTransition, Mapping[str, int], Mapping[str, int]],
    ] = {}
    completed: list[ExecutionEvent] = []
    for transition in trace.transitions:
        if transition.kind == ExecutionTransitionKind.DISPATCH:
            dispatches[transition.event_id] = (
                transition,
                dict(occupancy),
                dict(pending),
            )
        else:
            dispatch, occupancy_start, pending_start = dispatches.pop(
                transition.event_id
            )
            completed.append(
                ExecutionEvent(
                    event_id=transition.event_id,
                    plane=transition.plane,
                    opcode=transition.opcode,
                    instruction_id=transition.instruction_id,
                    process_id=transition.process_id,
                    instance=transition.instance,
                    start_s=transition.start_s,
                    end_s=transition.end_s,
                    reservation_id=transition.reservation_id,
                    candidate_id=transition.candidate_id,
                    wait_reasons=transition.wait_reasons,
                    consumed_tokens=transition.consumed_tokens,
                    consumed_slots=transition.consumed_slots,
                    produced_slots=transition.produced_slots,
                    produced_tokens=transition.produced_tokens,
                    buffer_occupancy_start=occupancy_start,
                    buffer_occupancy_end=transition.buffer_occupancy_after,
                    pending_incoming_start=pending_start,
                    pending_incoming_end=transition.pending_incoming_after,
                    metadata=transition.metadata,
                    backend_artifact=transition.backend_artifact,
                    outcome=transition.outcome,
                    program_lineage=transition.program_lineage,
                    measurements=transition.measurements,
                    continuation=transition.continuation,
                )
            )
            # The trace validator already proves this is the matching dispatch.
            assert dispatch.same_event_identity(transition)
        occupancy = dict(transition.buffer_occupancy_after)
        pending = dict(transition.pending_incoming_after)
    return tuple(sorted(completed, key=lambda event: event.event_id))


@dataclass(frozen=True)
class EvaluationResult:
    """Execution outcome around one authoritative causal trace.

    ``discrete_time_log`` is an optional, non-authoritative diagnostic cache.
    It contains rejected-candidate observations that are intentionally absent
    from the causal ledger and must be validated against that ledger plus the
    source plan before a serialized report trusts it.
    """

    trace: ExecutionTrace
    completed_program_instructions: int
    final_buffers: Mapping[str, tuple[str, ...]]
    final_pending_incoming: Mapping[str, int]
    final_locations: Mapping[str, str]
    program_state_blocked_s: float
    producer_blocked_s: Mapping[str, float]
    buffer_peaks: Mapping[str, int]
    metrics: Mapping[str, Any]
    invariant_checks: Mapping[str, bool]
    discrete_time_log: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    runtime_components: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.trace, ExecutionTrace):
            raise TraceValidationError("trace must be an ExecutionTrace")
        _require_plain_int(
            self.completed_program_instructions,
            label="completed_program_instructions",
        )
        completed_program_ids = {
            transition.instruction_id
            for transition in self.trace.transitions
            if transition.kind == ExecutionTransitionKind.COMPLETION
            and transition.plane == ExecutionPlane.PROGRAM
            and transition.continuation is not None
            and transition.continuation.kind == "complete_source"
        }
        if self.completed_program_instructions != len(completed_program_ids):
            raise TraceValidationError(
                "completed_program_instructions disagrees with the causal ledger"
            )
        object.__setattr__(
            self,
            "program_state_blocked_s",
            _require_finite(
                self.program_state_blocked_s,
                label="program_state_blocked_s",
            ),
        )
        for name in (
            "final_buffers",
            "final_pending_incoming",
            "final_locations",
            "producer_blocked_s",
            "buffer_peaks",
            "metrics",
            "invariant_checks",
            "runtime_components",
        ):
            object.__setattr__(
                self,
                name,
                _freeze_mapping(getattr(self, name), label=name),
            )
        object.__setattr__(
            self,
            "discrete_time_log",
            tuple(deep_freeze_json(item) for item in self.discrete_time_log),
        )
        if self.final_buffers != self.trace.terminal_state.buffers:
            raise TraceValidationError(
                "EvaluationResult final buffers disagree with terminal trace state"
            )
        if self.final_pending_incoming != self.trace.terminal_state.pending_incoming:
            raise TraceValidationError(
                "EvaluationResult pending outputs disagree with terminal trace state"
            )
        if self.final_locations != self.trace.terminal_state.locations:
            raise TraceValidationError(
                "EvaluationResult locations disagree with terminal trace state"
            )

    @property
    def plan_hash(self) -> str:
        return self.trace.plan_hash

    @property
    def seed(self) -> int:
        return self.trace.seed

    @property
    def total_latency_s(self) -> float:
        return self.trace.total_latency_s

    @property
    def events(self) -> tuple[ExecutionEvent, ...]:
        return self.trace.events

    @property
    def transitions(self) -> tuple[ExecutionTransition, ...]:
        return self.trace.transitions

    @property
    def terminal_inflight(self) -> tuple[ExecutionTransition, ...]:
        return self.trace.terminal_inflight

    @property
    def trace_hash(self) -> str:
        return self.trace.trace_hash

    @property
    def execution_hash(self) -> str:
        """Identity of requested execution inputs, not a digest of trace output."""

        return semantic_hash(
            {
                "plan_hash": self.plan_hash,
                "seed": self.seed,
                "runtime_manifest_hash": self.runtime_components.get(
                    "manifest_hash"
                ),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EXECUTION_TRACE_SCHEMA_VERSION,
            "plan_hash": self.plan_hash,
            "execution_hash": self.execution_hash,
            "trace_hash": self.trace_hash,
            "seed": self.seed,
            "total_latency_s": self.total_latency_s,
            "completed_program_instructions": self.completed_program_instructions,
            "transitions": [item.to_dict() for item in self.transitions],
            "initial_state": self.trace.initial_state.to_dict(),
            "terminal_state": self.trace.terminal_state.to_dict(),
            "terminal_inflight": [
                item.to_dict() for item in self.terminal_inflight
            ],
            "final_state": {
                "buffers": normalize_json(self.final_buffers),
                "pending_incoming": normalize_json(self.final_pending_incoming),
                "locations": normalize_json(self.final_locations),
            },
            "program_state_blocked_s": self.program_state_blocked_s,
            "producer_blocked_s": normalize_json(self.producer_blocked_s),
            "buffer_peaks": normalize_json(self.buffer_peaks),
            "metrics": normalize_json(self.metrics),
            "invariant_checks": normalize_json(self.invariant_checks),
            "discrete_time_log": normalize_json(self.discrete_time_log),
            "runtime_components": normalize_json(self.runtime_components),
        }

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


def replay_execution_trace(trace: ExecutionTrace, plan: Any) -> TraceStateProjection:
    """Replay recorded state mutations without re-running runtime components.

    This validates architectural state lineage. It intentionally does not
    re-execute scheduler/compiler/backend decisions or simulate quantum state.
    """

    from heteqsys.architecture.state import ArchitectureState, TentativeBinding

    def validate_trace_projected_metadata(
        actual: Mapping[str, Any],
        expected: Mapping[str, Any],
        *,
        owner: str,
    ) -> None:
        for key in _TRACE_PROJECTED_METADATA_FIELDS:
            if (
                (key in actual) != (key in expected)
                or (
                    key in actual
                    and not _json_type_strict_equal(actual[key], expected[key])
                )
            ):
                raise TraceReplayError(
                    f"{owner} has invalid trace-projected metadata {key!r}"
                )

    if plan.plan_hash != trace.plan_hash:
        raise TraceReplayError("ExecutionTrace plan_hash does not match the plan")
    state = ArchitectureState.from_plan(plan)
    initial = TraceStateProjection.from_snapshot(state.snapshot())
    if initial != trace.initial_state:
        raise TraceReplayError("Trace initial state does not match ExecutionPlan")

    program_by_id = {
        instruction.id: instruction for instruction in plan.program_dag.instructions
    }
    recipe_by_invocation = {
        recipe.invocation_id: recipe
        for instruction in plan.program_dag.instructions
        for recipe in getattr(instruction, "implementation_recipes", ())
    }
    resource_by_id = {
        process.id: process for process in plan.resource_dag.processes
    }
    dispatched_program: set[int] = set()
    dispatched_program_work: set[str] = set()
    completed_program: set[int] = set()
    program_completion_times: dict[int, float] = {}
    next_resource_instance = {process_id: 0 for process_id in resource_by_id}
    active_resource_instances = {process_id: 0 for process_id in resource_by_id}
    dispatches: dict[int, ExecutionTransition] = {}
    completed_transitions: dict[int, ExecutionTransition] = {}
    open_recipe_invocations: dict[int, set[str]] = {}

    def continuation_work_id(
        source_id: int,
        recipe: Any,
        stage_index: int,
        step: str,
    ) -> str:
        return (
            f"program:{source_id}:recipe:{recipe.invocation_id}:"
            f"stage:{stage_index}:{step}"
        )

    def validate_program_continuation(
        transition: ExecutionTransition,
    ) -> None:
        lineage = transition.program_lineage
        receipt = transition.continuation
        assert lineage is not None and receipt is not None
        source_id = lineage.source_instruction_id
        source = program_by_id[source_id]
        if lineage.step == "source":
            recipes = tuple(getattr(source, "implementation_recipes", ()))
            expected_measurements = {
                recipe.measurement_register(0) for recipe in recipes
            }
            if set(transition.measurements) != expected_measurements:
                raise TraceReplayError(
                    "Source completion measurements do not match its recipes"
                )
            if not recipes:
                expected = ProgramContinuationReceipt("complete_source")
            else:
                if source_id in open_recipe_invocations:
                    raise TraceReplayError("Program source opened recipes twice")
                open_recipe_invocations[source_id] = {
                    recipe.invocation_id for recipe in recipes
                }
                expected = ProgramContinuationReceipt(
                    "activate",
                    tuple(
                        continuation_work_id(source_id, recipe, 0, "reaction")
                        for recipe in recipes
                    ),
                )
        else:
            recipe = recipe_by_invocation.get(lineage.recipe_invocation_id)
            if recipe is None or lineage.stage_index is None:
                raise TraceReplayError("Continuation completion has no frozen recipe")
            stage_index = lineage.stage_index
            if lineage.step == "injection":
                register_id = recipe.measurement_register(stage_index)
                if set(transition.measurements) != {register_id}:
                    raise TraceReplayError(
                        "Injection completion has invalid typed measurement"
                    )
                expected = ProgramContinuationReceipt(
                    "activate",
                    (
                        continuation_work_id(
                            source_id, recipe, stage_index, "reaction"
                        ),
                    ),
                )
            elif lineage.step == "reaction":
                if transition.measurements:
                    raise TraceReplayError("Reaction work cannot measure an injection bit")
                parent = completed_transitions.get(lineage.parent_event_id)
                register_id = recipe.measurement_register(stage_index)
                if (
                    parent is None
                    or parent.program_lineage is None
                    or parent.program_lineage.recipe_invocation_id
                    not in {None, recipe.invocation_id}
                    or register_id not in parent.measurements
                ):
                    raise TraceReplayError(
                        "Reaction work does not follow its measured injection"
                    )
                bit = parent.measurements[register_id]
                stage = recipe.stages[stage_index]
                if bit == 1 and stage.failure_next_stage is not None:
                    expected = ProgramContinuationReceipt(
                        "activate",
                        (
                            continuation_work_id(
                                source_id,
                                recipe,
                                stage.failure_next_stage,
                                "injection",
                            ),
                        ),
                    )
                elif bit == 1:
                    expected = ProgramContinuationReceipt(
                        "activate",
                        (
                            continuation_work_id(
                                source_id, recipe, stage_index, "correction"
                            ),
                        ),
                    )
                else:
                    open_items = open_recipe_invocations.get(source_id)
                    if open_items is None or recipe.invocation_id not in open_items:
                        raise TraceReplayError("Reaction terminates an unopened recipe")
                    open_items.remove(recipe.invocation_id)
                    expected = ProgramContinuationReceipt(
                        "complete_source" if not open_items else "activate"
                    )
                    if not open_items:
                        open_recipe_invocations.pop(source_id)
            elif lineage.step == "correction":
                if transition.measurements:
                    raise TraceReplayError(
                        "Materialized logical correction cannot emit an outcome bit"
                    )
                stage = recipe.stages[stage_index]
                expected_gate = {stage.failure_correction: list(recipe.qubits)}
                if not _json_type_strict_equal(
                    transition.metadata.get("gates"), expected_gate
                ):
                    raise TraceReplayError(
                        "Materialized logical correction does not expose its frozen gate"
                    )
                open_items = open_recipe_invocations.get(source_id)
                if open_items is None or recipe.invocation_id not in open_items:
                    raise TraceReplayError("Correction terminates an unopened recipe")
                open_items.remove(recipe.invocation_id)
                expected = ProgramContinuationReceipt(
                    "complete_source" if not open_items else "activate"
                )
                if not open_items:
                    open_recipe_invocations.pop(source_id)
            else:  # ProgramWorkLineage closes this union.
                raise TraceReplayError("Unsupported Program continuation step")
        if receipt != expected:
            raise TraceReplayError(
                f"Program continuation receipt for {lineage.work_id!r} disagrees "
                "with the frozen recipe"
            )

    for transition in trace.transitions:
        if state.version != transition.state_version_before:
            raise TraceReplayError(
                f"Transition {transition.transition_id} starts from the wrong version"
            )
        if transition.kind == ExecutionTransitionKind.DISPATCH:
            if transition.plane == ExecutionPlane.PROGRAM:
                source = program_by_id.get(transition.instruction_id)
                if source is None:
                    raise TraceReplayError(
                        f"Unknown Program instruction {transition.instruction_id}"
                    )
                instruction_id = int(transition.instruction_id)
                lineage = transition.program_lineage
                assert lineage is not None
                if lineage.work_id in dispatched_program_work:
                    raise TraceReplayError(
                        f"Program work {lineage.work_id!r} was dispatched twice"
                    )
                if lineage.step == "source":
                    if instruction_id in dispatched_program:
                        raise TraceReplayError(
                            f"Program instruction {instruction_id} was dispatched twice"
                        )
                    if not set(source.predecessor_ids).issubset(completed_program):
                        raise TraceReplayError(
                            f"Program instruction {instruction_id} dispatched before predecessors"
                        )
                    expected_opcode = source.opcode
                    expected_consumes = source.consumes
                    expected_produces = source.produces
                    expected_forwards = source.forwards
                    expected_engines = source.engines
                    expected_required = source.required_locations
                    expected_completion = source.completion_locations
                    expected_qubits = source.qubits
                    expected_targets = source.target_modules
                    expected_links = source.target_links
                    expected_metadata = dict(source.metadata)
                    ready_s = max(
                        (
                            program_completion_times[predecessor]
                            for predecessor in source.predecessor_ids
                        ),
                        default=0.0,
                    )
                    dispatched_program.add(instruction_id)
                else:
                    recipe = recipe_by_invocation.get(lineage.recipe_invocation_id)
                    if (
                        recipe is None
                        or recipe.recipe_id != lineage.recipe_id
                        or lineage.stage_index is None
                        or recipe not in getattr(source, "implementation_recipes", ())
                        or not (0 <= lineage.stage_index < len(recipe.stages))
                        or lineage.work_id
                        != (
                            f"program:{instruction_id}:recipe:{recipe.invocation_id}:"
                            f"stage:{lineage.stage_index}:{lineage.step}"
                        )
                    ):
                        raise TraceReplayError(
                            f"Unknown continuation recipe for {lineage.work_id!r}"
                        )
                    stage = recipe.stages[lineage.stage_index]
                    expected_produces = {}
                    expected_forwards = {}
                    expected_completion = {}
                    expected_metadata = {}
                    ready_s = next(
                        item.end_s
                        for item in trace.transitions
                        if item.kind == ExecutionTransitionKind.COMPLETION
                        and item.event_id == lineage.parent_event_id
                    )
                    if lineage.step == "reaction":
                        expected_opcode = ArchitectureOpcode.CLASSICAL_REACTION
                        expected_consumes = {}
                        expected_engines = {}
                        expected_required = {}
                        expected_qubits = ()
                    elif lineage.step == "injection":
                        expected_opcode = ArchitectureOpcode.EXECUTE_COMPUTE
                        expected_consumes = {
                            stage.resource.buffer_id: stage.resource.quantity
                        }
                        expected_engines = {recipe.compute_engine: 1}
                        expected_required = {
                            f"q:{qubit}": recipe.compute_location
                            for qubit in recipe.qubits
                        }
                        expected_qubits = recipe.qubits
                    elif lineage.step == "correction":
                        expected_opcode = ArchitectureOpcode.EXECUTE_COMPUTE
                        expected_consumes = {}
                        expected_engines = {recipe.compute_engine: 1}
                        expected_required = {
                            f"q:{qubit}": recipe.compute_location
                            for qubit in recipe.qubits
                        }
                        expected_qubits = recipe.qubits
                    else:  # ProgramWorkLineage closes this union.
                        raise TraceReplayError("Unsupported continuation step")
                    expected_targets = (recipe.compute_location,)
                    expected_links = ()
                if (
                    transition.opcode != expected_opcode
                    or transition.candidate_id != lineage.work_id
                    or transition.instance is not None
                    or transition.consumes != expected_consumes
                    or transition.produces != expected_produces
                    or transition.forwards != expected_forwards
                    or transition.engines != expected_engines
                    or transition.required_locations != expected_required
                    or transition.completion_locations != expected_completion
                ):
                    raise TraceReplayError(
                        f"Program transition does not match work {lineage.work_id!r}"
                    )
                expected_metadata.update(
                    {
                        "layer": (
                            source.layer_index
                            if lineage.step == "source"
                            else recipe.source_layer_index
                        ),
                        "qubits": list(expected_qubits),
                        "target_modules": list(expected_targets),
                        "target_links": list(expected_links),
                        "engines": dict(expected_engines),
                        "program_ready_s": ready_s,
                        "resource_wait_s": transition.start_s - ready_s,
                    }
                )
                validate_trace_projected_metadata(
                    transition.metadata,
                    expected_metadata,
                    owner=f"Program instruction {instruction_id}",
                )
                dispatched_program_work.add(lineage.work_id)
            else:
                source = resource_by_id.get(transition.process_id)
                if source is None:
                    raise TraceReplayError(
                        f"Unknown Resource process {transition.process_id!r}"
                    )
                instance = _require_plain_int(
                    transition.instance,
                    label="resource transition instance",
                )
                expected_instance = next_resource_instance[source.id]
                if instance != expected_instance:
                    raise TraceReplayError(
                        f"Resource process {source.id} instance is not monotonic"
                    )
                if active_resource_instances[source.id] >= source.parallelism:
                    raise TraceReplayError(
                        f"Resource process {source.id} exceeds declared parallelism"
                    )
                batch_amount = _require_plain_int(
                    transition.metadata.get("batch_amount", 1),
                    label="resource batch_amount",
                    minimum=1,
                )
                snapshot = state.snapshot()
                fitted_produces = dict(source.produces)
                expected_metadata = dict(source.metadata)
                if source.output_overflow_policy == "discard_excess":
                    accepted: dict[str, int] = {}
                    discarded: dict[str, int] = {}
                    for name, raw_amount in source.produces.items():
                        amount = int(raw_amount)
                        buffer = snapshot.buffers[name]
                        free = max(
                            0,
                            buffer.capacity
                            - len(buffer.ready_tokens)
                            - buffer.pending_outputs,
                        )
                        accepted_amount = min(amount, free)
                        discarded_amount = amount - accepted_amount
                        if accepted_amount:
                            accepted[name] = accepted_amount
                        if discarded_amount:
                            discarded[name] = discarded_amount
                    if source.produces and not accepted:
                        raise TraceReplayError(
                            f"Resource process {source.id} dispatched into full outputs"
                        )
                    fitted_produces = accepted
                    expected_metadata.update(
                        {
                            "attempted_outputs": dict(source.produces),
                            "buffered_outputs": accepted,
                            "discarded_outputs": discarded,
                        }
                    )
                expected_batch_amount = (
                    snapshot.eager_batch_size(source)
                    if source.dispatch_policy == "eager_available"
                    else 1
                )
                if expected_batch_amount <= 0 or batch_amount != expected_batch_amount:
                    raise TraceReplayError(
                        f"Resource process {source.id} has invalid batch_amount"
                    )
                expected_metadata.update(
                    {
                        "batch_amount": expected_batch_amount,
                        "dispatch_policy": source.dispatch_policy,
                        "protocol": source.protocol,
                        "target_modules": list(source.target_modules),
                        "target_links": list(source.target_links),
                        "engines": dict(source.engines),
                    }
                )
                expected_consumes = {
                    name: int(amount) * batch_amount
                    for name, amount in source.consumes.items()
                }
                expected_produces = {
                    name: int(amount) * batch_amount
                    for name, amount in fitted_produces.items()
                }
                if (
                    transition.opcode != source.opcode
                    or transition.candidate_id
                    != f"resource:{source.id}:{instance}"
                    or transition.consumes != expected_consumes
                    or transition.produces != expected_produces
                    or transition.forwards != source.forwards
                    or transition.engines != source.engines
                    or transition.required_locations
                    or transition.completion_locations
                ):
                    raise TraceReplayError(
                        f"Resource transition does not match process {source.id}"
                    )
                validate_trace_projected_metadata(
                    transition.metadata,
                    expected_metadata,
                    owner=f"Resource process {source.id}",
                )
                next_resource_instance[source.id] += 1
            binding = TentativeBinding(
                state_version=transition.state_version_before,
                consumes=transition.consumes,
                consumed_tokens=transition.consumed_tokens,
                consumed_slots=transition.consumed_slots,
                produced_slots=transition.produced_slots,
                produces=transition.produces,
                forwards=transition.forwards,
                engines=transition.engines,
                required_locations=transition.required_locations,
                completion_locations=transition.completion_locations,
            )
            operation = SimpleNamespace(
                consumes=transition.consumes,
                produces=transition.produces,
                forwards=transition.forwards,
                engines=transition.engines,
                required_locations=transition.required_locations,
                completion_locations=transition.completion_locations,
            )
            try:
                reservation = state.commit(binding, operation)
            except Exception as exc:
                raise TraceReplayError(
                    f"Dispatch transition {transition.transition_id} is invalid"
                ) from exc
            if (
                reservation.reservation_id != transition.reservation_id
                or reservation.binding_version != transition.state_version_before
                or reservation.committed_version != transition.state_version_after
            ):
                raise TraceReplayError(
                    f"Dispatch transition {transition.transition_id} reservation mismatch"
                )
            dispatches[transition.event_id] = transition
            if transition.plane == ExecutionPlane.RESOURCE:
                active_resource_instances[str(transition.process_id)] += 1
        else:
            dispatch = dispatches.get(transition.event_id)
            if dispatch is None:
                raise TraceReplayError(
                    f"Completion transition {transition.transition_id} has no dispatch"
                )
            reservation = state.active_reservations.get(transition.reservation_id)
            if reservation is None:
                raise TraceReplayError(
                    f"Completion transition {transition.transition_id} has no reservation"
                )
            try:
                delta = state.propose_completion(
                    reservation,
                    outcome=transition.outcome,
                )
            except Exception as exc:
                raise TraceReplayError(
                    f"Completion transition {transition.transition_id} is invalid"
                ) from exc
            if (
                delta.produced_tokens != transition.produced_tokens
                or delta.produced_slots != transition.produced_slots
                or delta.released_engines != transition.engines
                or delta.completion_locations != transition.completion_locations
                or delta.token_sequence_after != transition.token_sequence_after
                or delta.buffer_occupancy != transition.buffer_occupancy_after
                or delta.pending_outputs != transition.pending_incoming_after
                or delta.outcome != transition.outcome
            ):
                raise TraceReplayError(
                    f"Completion transition {transition.transition_id} delta mismatch"
                )
            state.apply_completion(delta)
            dispatches.pop(transition.event_id, None)
            if transition.plane == ExecutionPlane.PROGRAM:
                validate_program_continuation(transition)
                instruction_id = int(transition.instruction_id)
                assert transition.continuation is not None
                if transition.continuation.kind == "complete_source":
                    completed_program.add(instruction_id)
                    program_completion_times[instruction_id] = transition.end_s
            else:
                process_id = str(dispatch.process_id)
                if active_resource_instances[process_id] <= 0:
                    raise TraceReplayError(
                        f"Resource process {process_id} completion underflow"
                    )
                active_resource_instances[process_id] -= 1
            completed_transitions[transition.event_id] = transition

        if state.version != transition.state_version_after:
            raise TraceReplayError(
                f"Transition {transition.transition_id} ended at the wrong version"
            )
        snapshot = state.snapshot()
        if (
            snapshot.occupancy() != transition.buffer_occupancy_after
            or snapshot.pending_outputs() != transition.pending_incoming_after
        ):
            raise TraceReplayError(
                f"Transition {transition.transition_id} post-state counts mismatch"
            )

    terminal = TraceStateProjection.from_snapshot(state.snapshot())
    if terminal != trace.terminal_state:
        raise TraceReplayError("Replayed state does not match terminal trace state")
    expected_inflight = tuple(dispatches[event_id] for event_id in sorted(dispatches))
    if expected_inflight != trace.terminal_inflight:
        raise TraceReplayError("Replayed active reservations do not match inflight trace")
    if completed_program != set(program_by_id):
        raise TraceReplayError("Trace did not complete the ExecutionPlan Program DAG")
    if open_recipe_invocations:
        raise TraceReplayError("Trace ended with open Program recipe invocations")
    return terminal


def validate_discrete_time_log_document(
    log: Any,
    trace: ExecutionTrace,
    plan: Any,
    trace_level: str,
) -> tuple[Mapping[str, Any], ...]:
    """Validate the diagnostic discrete-time log against the causal ledger.

    The transition ledger is the source of truth.  This validator only accepts
    the exact summary/full v1 projection emitted by the Event Engine and never
    uses the log to infer execution semantics.
    """

    from heteqsys.architecture.state import ArchitectureState, TentativeBinding

    def require_array(value: Any, *, label: str) -> tuple[Any, ...]:
        if type(value) not in (list, tuple):
            raise TraceValidationError(f"{label} must be a JSON array")
        return tuple(value)

    def require_exact_mapping(
        value: Any,
        keys: set[str],
        *,
        label: str,
    ) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise TraceValidationError(f"{label} must be a mapping")
        if any(type(key) is not str for key in value) or set(value) != keys:
            raise TraceValidationError(f"{label} has an invalid field schema")
        return value

    def require_projection(
        actual: Any,
        expected: Any,
        *,
        label: str,
    ) -> None:
        if not _json_type_strict_equal(actual, expected):
            raise TraceValidationError(f"{label} disagrees with transition ledger")

    if not isinstance(trace, ExecutionTrace):
        raise TraceValidationError("trace must be an ExecutionTrace")
    if type(trace_level) is not str or trace_level not in {"summary", "full"}:
        raise TraceValidationError("trace_level must be 'summary' or 'full'")
    if getattr(getattr(plan, "policy", None), "trace_level", None) != trace_level:
        raise TraceValidationError(
            "trace_level disagrees with the ExecutionPlan policy"
        )
    entries = require_array(log, label="discrete_time_log")
    replay_execution_trace(trace, plan)
    if trace_level == "summary":
        if entries:
            raise TraceValidationError(
                "summary trace_level cannot contain a discrete_time_log"
            )
        return ()

    transitions_by_time: dict[float, list[ExecutionTransition]] = {}
    for transition in trace.transitions:
        transitions_by_time.setdefault(transition.time_s, []).append(transition)
    expected_times = tuple(transitions_by_time)
    if len(entries) != len(expected_times):
        raise TraceValidationError(
            "full discrete_time_log times must equal transition times"
        )

    top_level_keys = {
        "time_s",
        "completed",
        "architecture_state_delta",
        "program_ready_now",
        "dispatched",
        "frontier_after",
        "architecture_state_after",
    }
    completed_keys = {
        "event_id",
        "plane",
        "opcode",
        "instruction_id",
        "process_id",
        "instance",
        "start_s",
        "end_s",
        "produced_tokens",
        "reservation_id",
        "state_version_before",
        "state_version_after",
        "candidate_id",
        "backend_artifact",
        "outcome",
    }
    dispatched_keys = {
        "event_id",
        "plane",
        "opcode",
        "instruction_id",
        "process_id",
        "instance",
        "start_s",
        "end_s",
        "program_ready",
        "resource_ready",
        "reservation",
        "metadata",
        "candidate_id",
        "backend_artifact",
    }
    reservation_keys = {
        "reservation_id",
        "state_version_before",
        "state_version_after",
        "consumed_tokens",
        "consumed_slots",
        "produced_slots",
        "engines",
    }
    running_keys = {
        "event_id",
        "plane",
        "opcode",
        "instruction_id",
        "process_id",
        "instance",
        "reservation_id",
        "candidate_id",
        "started_s",
        "completes_s",
    }
    waiting_keys = {
        "instruction_id",
        "opcode",
        "program_ready",
        "resource_ready",
        "resource_blockers",
        "program_ready_s",
    }

    program_by_id = {
        instruction.id: instruction for instruction in plan.program_dag.instructions
    }
    successors: dict[int, list[int]] = {
        instruction_id: [] for instruction_id in program_by_id
    }
    remaining_predecessors = {
        instruction.id: len(instruction.predecessor_ids)
        for instruction in plan.program_dag.instructions
    }
    for instruction in plan.program_dag.instructions:
        for predecessor in instruction.predecessor_ids:
            successors[predecessor].append(instruction.id)
    program_dispatch_by_work_id = {
        transition.program_lineage.work_id: transition
        for transition in trace.transitions
        if transition.kind == ExecutionTransitionKind.DISPATCH
        and transition.plane == ExecutionPlane.PROGRAM
        and transition.program_lineage is not None
    }
    program_schedule_by_work_id = {
        f"program:{instruction_id}": instruction_id
        for instruction_id in program_by_id
    }
    program_operation_by_schedule: dict[int, Any] = dict(program_by_id)
    program_source_by_schedule = {
        instruction_id: instruction_id for instruction_id in program_by_id
    }
    next_program_schedule_id = max(program_by_id, default=-1) + 1

    def activate_program_work(work_id: str, *, ready_s: float) -> int:
        """Reproduce the Engine's stable runtime schedule-id allocation."""

        nonlocal next_program_schedule_id
        if work_id in program_schedule_by_work_id:
            raise TraceValidationError(
                f"Program work {work_id!r} was activated more than once"
            )
        dispatch = program_dispatch_by_work_id.get(work_id)
        if dispatch is None or dispatch.program_lineage is None:
            raise TraceValidationError(
                f"Activated Program work {work_id!r} has no dispatch ledger"
            )
        schedule_id = next_program_schedule_id
        next_program_schedule_id += 1
        program_schedule_by_work_id[work_id] = schedule_id
        # StateSnapshot.check_start consumes only these operation claims.  The
        # later canonical dispatch carries the exact realized claims for this
        # continuation work, even while validating an earlier waiting row.
        program_operation_by_schedule[schedule_id] = dispatch
        program_source_by_schedule[schedule_id] = (
            dispatch.program_lineage.source_instruction_id
        )
        ready_program.add(schedule_id)
        ready_times[schedule_id] = ready_s
        return schedule_id

    ready_program = {
        instruction_id
        for instruction_id, remaining in remaining_predecessors.items()
        if remaining == 0
    }
    ready_times = {instruction_id: 0.0 for instruction_id in ready_program}
    initially_ready = tuple(sorted(ready_program))

    buffer_ready = {
        name: len(tokens) for name, tokens in trace.initial_state.buffers.items()
    }
    pending = dict(trace.initial_state.pending_incoming)
    engine_used = dict(trace.initial_state.engine_usage)
    locations = dict(trace.initial_state.locations)
    engine_capacity = {engine.id: engine.capacity for engine in plan.engines}
    state_version = trace.initial_state.state_version
    active: dict[int, ExecutionTransition] = {}
    log_state = ArchitectureState.from_plan(plan)

    validated: list[Mapping[str, Any]] = []
    for index, (raw_entry, expected_time) in enumerate(zip(entries, expected_times)):
        entry = require_exact_mapping(
            raw_entry,
            top_level_keys,
            label=f"discrete_time_log[{index}]",
        )
        if not _json_type_strict_equal(entry["time_s"], expected_time):
            raise TraceValidationError(
                f"discrete_time_log[{index}].time_s disagrees with ledger"
            )

        before_buffers = dict(buffer_ready)
        before_pending = dict(pending)
        before_engines = dict(engine_used)
        before_locations = dict(locations)
        ready_now = list(initially_ready if index == 0 else ())
        expected_dispatched: list[dict[str, Any]] = []
        expected_completed: list[dict[str, Any]] = []

        for transition in transitions_by_time[expected_time]:
            state_version = transition.state_version_after
            buffer_ready = dict(transition.buffer_occupancy_after)
            pending = dict(transition.pending_incoming_after)
            if transition.kind == ExecutionTransitionKind.DISPATCH:
                binding = TentativeBinding(
                    state_version=transition.state_version_before,
                    consumes=transition.consumes,
                    consumed_tokens=transition.consumed_tokens,
                    consumed_slots=transition.consumed_slots,
                    produced_slots=transition.produced_slots,
                    produces=transition.produces,
                    forwards=transition.forwards,
                    engines=transition.engines,
                    required_locations=transition.required_locations,
                    completion_locations=transition.completion_locations,
                )
                operation = SimpleNamespace(
                    consumes=transition.consumes,
                    produces=transition.produces,
                    forwards=transition.forwards,
                    engines=transition.engines,
                    required_locations=transition.required_locations,
                    completion_locations=transition.completion_locations,
                )
                try:
                    log_state.commit(binding, operation)
                except Exception as exc:
                    raise TraceValidationError(
                        f"Cannot reduce dispatch transition {transition.transition_id}"
                    ) from exc
                for name, amount in transition.engines.items():
                    engine_used[name] += int(amount)
                for tokens in transition.consumed_tokens.values():
                    for token in tokens:
                        locations.pop(token, None)
                active[transition.event_id] = transition
                if transition.plane == ExecutionPlane.PROGRAM:
                    if transition.program_lineage is None:
                        raise TraceValidationError(
                            "Program dispatch lost its typed work lineage"
                        )
                    schedule_id = program_schedule_by_work_id.get(
                        transition.program_lineage.work_id
                    )
                    if schedule_id is None or schedule_id not in ready_program:
                        raise TraceValidationError(
                            "Program dispatch does not match the reconstructed "
                            "runtime-ready frontier"
                        )
                    ready_program.remove(schedule_id)
                expected_dispatched.append(
                    {
                        "event_id": transition.event_id,
                        "plane": transition.plane.value,
                        "opcode": transition.opcode.value,
                        "instruction_id": transition.instruction_id,
                        "process_id": transition.process_id,
                        "instance": transition.instance,
                        "start_s": transition.start_s,
                        "end_s": transition.end_s,
                        "program_ready": (
                            True
                            if transition.plane == ExecutionPlane.PROGRAM
                            else None
                        ),
                        "resource_ready": True,
                        "reservation": {
                            "reservation_id": transition.reservation_id,
                            "state_version_before": transition.state_version_before,
                            "state_version_after": transition.state_version_after,
                            "consumed_tokens": normalize_json(
                                transition.consumed_tokens
                            ),
                            "consumed_slots": normalize_json(
                                transition.consumed_slots
                            ),
                            "produced_slots": normalize_json(
                                transition.produced_slots
                            ),
                            "engines": normalize_json(transition.engines),
                        },
                        "metadata": normalize_json(transition.metadata),
                        "candidate_id": transition.candidate_id,
                        "backend_artifact": normalize_json(
                            transition.backend_artifact
                        ),
                    }
                )
                continue

            active.pop(transition.event_id)
            reservation = log_state.active_reservations.get(
                transition.reservation_id
            )
            if reservation is None:
                raise TraceValidationError(
                    f"Cannot reduce completion transition {transition.transition_id}"
                )
            try:
                delta = log_state.propose_completion(
                    reservation, outcome=transition.outcome
                )
                log_state.apply_completion(delta)
            except Exception as exc:
                raise TraceValidationError(
                    f"Cannot reduce completion transition {transition.transition_id}"
                ) from exc
            for name, amount in transition.engines.items():
                engine_used[name] -= int(amount)
            for name, tokens in transition.produced_tokens.items():
                for token in tokens:
                    locations[token] = name
            locations.update(transition.completion_locations)
            if transition.plane == ExecutionPlane.PROGRAM:
                instruction_id = int(transition.instruction_id)
                continuation = transition.continuation
                if continuation is None:
                    raise TraceValidationError(
                        "Program completion lost its continuation receipt"
                    )
                for work_id in continuation.activated_work_ids:
                    ready_now.append(
                        activate_program_work(work_id, ready_s=transition.end_s)
                    )
                if continuation.kind == "complete_source":
                    for successor in successors[instruction_id]:
                        remaining_predecessors[successor] -= 1
                        if remaining_predecessors[successor] == 0:
                            ready_program.add(successor)
                            ready_times[successor] = transition.end_s
                            ready_now.append(successor)
            expected_completed.append(
                {
                    "event_id": transition.event_id,
                    "plane": transition.plane.value,
                    "opcode": transition.opcode.value,
                    "instruction_id": transition.instruction_id,
                    "process_id": transition.process_id,
                    "instance": transition.instance,
                    "start_s": transition.start_s,
                    "end_s": transition.end_s,
                    "produced_tokens": normalize_json(
                        transition.produced_tokens
                    ),
                    "reservation_id": transition.reservation_id,
                    "state_version_before": transition.state_version_before,
                    "state_version_after": transition.state_version_after,
                    "candidate_id": transition.candidate_id,
                    "backend_artifact": normalize_json(
                        transition.backend_artifact
                    ),
                    "outcome": normalize_json(transition.outcome),
                }
            )

        actual_completed = require_array(
            entry["completed"],
            label=f"discrete_time_log[{index}].completed",
        )
        for item_index, item in enumerate(actual_completed):
            require_exact_mapping(
                item,
                completed_keys,
                label=f"discrete_time_log[{index}].completed[{item_index}]",
            )
        require_projection(
            actual_completed,
            expected_completed,
            label=f"discrete_time_log[{index}].completed",
        )

        actual_dispatched = require_array(
            entry["dispatched"],
            label=f"discrete_time_log[{index}].dispatched",
        )
        for item_index, item in enumerate(actual_dispatched):
            dispatched = require_exact_mapping(
                item,
                dispatched_keys,
                label=f"discrete_time_log[{index}].dispatched[{item_index}]",
            )
            require_exact_mapping(
                dispatched["reservation"],
                reservation_keys,
                label=(
                    f"discrete_time_log[{index}].dispatched"
                    f"[{item_index}].reservation"
                ),
            )
        require_projection(
            actual_dispatched,
            expected_dispatched,
            label=f"discrete_time_log[{index}].dispatched",
        )

        frontier = require_exact_mapping(
            entry["frontier_after"],
            {"waiting", "running", "completed_now"},
            label=f"discrete_time_log[{index}].frontier_after",
        )
        expected_running = [
            {
                "event_id": item.event_id,
                "plane": item.plane.value,
                "opcode": item.opcode.value,
                "instruction_id": item.instruction_id,
                "process_id": item.process_id,
                "instance": item.instance,
                "reservation_id": item.reservation_id,
                "candidate_id": item.candidate_id,
                "started_s": item.start_s,
                "completes_s": item.end_s,
            }
            for item in sorted(
                active.values(), key=lambda candidate: (candidate.end_s, candidate.event_id)
            )
        ]
        actual_running = require_array(
            frontier["running"],
            label=f"discrete_time_log[{index}].frontier_after.running",
        )
        for item_index, item in enumerate(actual_running):
            require_exact_mapping(
                item,
                running_keys,
                label=(
                    f"discrete_time_log[{index}].frontier_after.running"
                    f"[{item_index}]"
                ),
            )
        require_projection(
            actual_running,
            expected_running,
            label=f"discrete_time_log[{index}].frontier_after.running",
        )
        require_projection(
            require_array(
                frontier["completed_now"],
                label=(
                    f"discrete_time_log[{index}].frontier_after.completed_now"
                ),
            ),
            [item["event_id"] for item in expected_completed],
            label=f"discrete_time_log[{index}].frontier_after.completed_now",
        )

        waiting = require_array(
            frontier["waiting"],
            label=f"discrete_time_log[{index}].frontier_after.waiting",
        )
        expected_waiting: list[dict[str, Any]] = []
        for item_index, raw_waiting in enumerate(waiting):
            require_exact_mapping(
                raw_waiting,
                waiting_keys,
                label=(
                    f"discrete_time_log[{index}].frontier_after.waiting"
                    f"[{item_index}]"
                ),
            )
        snapshot = log_state.snapshot()
        for schedule_id in sorted(ready_program):
            operation = program_operation_by_schedule[schedule_id]
            blockers = tuple(
                str(reason)
                for reason in snapshot.check_start(
                    operation,
                    required_locations=operation.required_locations,
                )
            )
            if not blockers:
                continue
            opcode = getattr(operation.opcode, "value", operation.opcode)
            expected_waiting.append(
                {
                    "instruction_id": program_source_by_schedule[schedule_id],
                    "opcode": str(opcode),
                    "program_ready": True,
                    "resource_ready": False,
                    "resource_blockers": list(blockers),
                    "program_ready_s": ready_times[schedule_id],
                }
            )
        require_projection(
            waiting,
            expected_waiting,
            label=f"discrete_time_log[{index}].frontier_after.waiting",
        )

        require_projection(
            require_array(
                entry["program_ready_now"],
                label=f"discrete_time_log[{index}].program_ready_now",
            ),
            sorted(set(ready_now)),
            label=f"discrete_time_log[{index}].program_ready_now",
        )

        expected_state_after = {
            "state_version": state_version,
            "buffers": {
                name: {
                    "ready": buffer_ready[name],
                    "pending_incoming": pending[name],
                }
                for name in sorted(buffer_ready)
            },
            "engines": {
                name: {
                    "used": engine_used[name],
                    "capacity": engine_capacity[name],
                }
                for name in sorted(engine_used)
            },
        }
        require_exact_mapping(
            entry["architecture_state_after"],
            {"state_version", "buffers", "engines"},
            label=f"discrete_time_log[{index}].architecture_state_after",
        )
        require_projection(
            entry["architecture_state_after"],
            expected_state_after,
            label=f"discrete_time_log[{index}].architecture_state_after",
        )

        expected_delta: dict[str, Any] = {}
        buffer_delta = {}
        for name in sorted(buffer_ready):
            if (
                before_buffers[name] != buffer_ready[name]
                or before_pending[name] != pending[name]
            ):
                buffer_delta[name] = {
                    "ready": buffer_ready[name],
                    "ready_delta": buffer_ready[name] - before_buffers[name],
                    "pending_incoming": pending[name],
                    "pending_delta": pending[name] - before_pending[name],
                }
        if buffer_delta:
            expected_delta["buffers"] = buffer_delta
        engine_delta = {}
        for name in sorted(engine_used):
            if before_engines[name] != engine_used[name]:
                engine_delta[name] = {
                    "used": engine_used[name],
                    "used_delta": engine_used[name] - before_engines[name],
                    "capacity": engine_capacity[name],
                }
        if engine_delta:
            expected_delta["engines"] = engine_delta
        location_delta = {}
        for name in sorted(set(before_locations) | set(locations)):
            source = before_locations.get(name)
            destination = locations.get(name)
            if source != destination:
                location_delta[name] = {"from": source, "to": destination}
        if location_delta:
            expected_delta["locations"] = location_delta
        if not isinstance(entry["architecture_state_delta"], Mapping):
            raise TraceValidationError(
                f"discrete_time_log[{index}].architecture_state_delta must be a mapping"
            )
        require_projection(
            entry["architecture_state_delta"],
            expected_delta,
            label=f"discrete_time_log[{index}].architecture_state_delta",
        )
        validated.append(deep_freeze_json(entry))

    return tuple(validated)


def validate_execution_trace_document(data: Mapping[str, Any]) -> ExecutionTrace:
    """Validate one exact canonical ExecutionTrace-v3 document.

    Flattened :class:`EvaluationResult` diagnostics and derived event/final-state
    projections are deliberately not accepted. Report v1 owns its one-way
    compatibility projection; callers with a Plan may additionally use
    :func:`replay_execution_trace` for full state-transition validation.
    """

    return ExecutionTrace.from_dict(data)
