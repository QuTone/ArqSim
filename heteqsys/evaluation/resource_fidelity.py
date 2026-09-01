"""Trace-grounded lifecycle ledger for buffered resource tokens.

The live architecture state deliberately stores no token age or fidelity.  A
causal execution trace already contains the facts needed for fidelity analysis:
token identity, buffer identity, forwarding, and dispatch/completion time.  This
module reconstructs those facts after execution without mutating or replaying
runtime state.  It separately derives the reverse dependency slice that reaches
completed Program consumption, so physical background work remains observable
without automatically entering application fidelity.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from .plan import ExecutionPlan
from .result import EvaluationResult, ExecutionTransitionKind


@dataclass(frozen=True)
class ResourceBufferRef:
    """Typed plan-owned identity for one runtime resource buffer."""

    buffer_id: str
    resource_kind: str
    module: str | None
    submodule: str | None

    @property
    def location(self) -> str:
        """Return the most specific canonical location available."""

        return self.submodule or self.module or self.buffer_id


@dataclass(frozen=True)
class ResourceResidenceInterval:
    """One interval during which a completed token was ready in a buffer."""

    buffer_id: str
    location: str
    start_s: float
    end_s: float

    def __post_init__(self) -> None:
        start = float(self.start_s)
        end = float(self.end_s)
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("Resource residence times must be finite")
        if start < 0 or end < start:
            raise ValueError("Resource residence interval is invalid")
        object.__setattr__(self, "start_s", start)
        object.__setattr__(self, "end_s", end)

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class ResourceConsumption:
    """The unique non-forwarding consumption that terminates one token."""

    time_s: float
    plane: str
    opcode: str
    event_id: int
    buffer_id: str


@dataclass(frozen=True)
class ResourceTokenRecord:
    """One token's birth, buffered residence, forwarding, and consumption."""

    token_id: str
    resource_kind: str
    producer_protocol_id: str | None
    residence_intervals: tuple[ResourceResidenceInterval, ...]
    consumption: ResourceConsumption | None

    @property
    def consumed(self) -> bool:
        return self.consumption is not None


@dataclass(frozen=True)
class ResourceTokenLedger:
    """Immutable lifecycle records derived from one plan-bound trace."""

    plan_hash: str
    trace_hash: str
    buffers: Mapping[str, ResourceBufferRef]
    tokens: tuple[ResourceTokenRecord, ...] = field(default_factory=tuple)
    application_relevant_token_ids: tuple[str, ...] = field(default_factory=tuple)
    application_relevant_resource_event_ids: tuple[int, ...] = field(
        default_factory=tuple
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "buffers",
            MappingProxyType(dict(sorted(self.buffers.items()))),
        )
        token_ids = tuple(record.token_id for record in self.tokens)
        if len(token_ids) != len(set(token_ids)):
            raise ValueError("Resource-token ledger IDs must be unique")
        consumed_token_ids = {
            record.token_id for record in self.tokens if record.consumed
        }
        relevant_token_ids = tuple(self.application_relevant_token_ids)
        if len(relevant_token_ids) != len(set(relevant_token_ids)) or not set(
            relevant_token_ids
        ).issubset(consumed_token_ids):
            raise ValueError(
                "Application-relevant resource-token IDs must be unique consumed "
                "ledger IDs"
            )
        relevant_event_ids = tuple(self.application_relevant_resource_event_ids)
        if (
            len(relevant_event_ids) != len(set(relevant_event_ids))
            or any(
                type(event_id) is not int or event_id < 0
                for event_id in relevant_event_ids
            )
        ):
            raise ValueError(
                "Application-relevant Resource event IDs must be unique "
                "non-negative integers"
            )
        object.__setattr__(
            self,
            "application_relevant_token_ids",
            tuple(sorted(relevant_token_ids)),
        )
        object.__setattr__(
            self,
            "application_relevant_resource_event_ids",
            tuple(sorted(relevant_event_ids)),
        )

    @property
    def consumed_tokens(self) -> tuple[ResourceTokenRecord, ...]:
        return tuple(record for record in self.tokens if record.consumed)

    @property
    def unconsumed_tokens(self) -> tuple[ResourceTokenRecord, ...]:
        return tuple(record for record in self.tokens if not record.consumed)

    @property
    def settled_tokens(self) -> tuple[ResourceTokenRecord, ...]:
        """Return consumed tokens whose quality can reach completed Program work.

        ``consumed_tokens`` remains the physical lifecycle fact.  This narrower
        view is the application-fidelity settlement frontier: it starts from
        terminal Program-plane consumption and follows Resource-event token
        dependencies backwards.  Background production therefore remains
        observable without changing application fidelity until one of its
        outputs reaches Program work.
        """

        relevant = set(self.application_relevant_token_ids)
        return tuple(record for record in self.tokens if record.token_id in relevant)

    @property
    def settled_resource_event_ids(self) -> tuple[int, ...]:
        """Return Resource events on a settled token's reverse dependency path."""

        return self.application_relevant_resource_event_ids


@dataclass
class _OpenToken:
    token_id: str
    resource_kind: str
    producer_protocol_id: str | None
    intervals: list[ResourceResidenceInterval] = field(default_factory=list)
    open_buffer_id: str | None = None
    open_since_s: float | None = None
    consumption: ResourceConsumption | None = None


def resource_token_ledger(
    result: EvaluationResult,
    plan: ExecutionPlan,
) -> ResourceTokenLedger:
    """Reconstruct resource-token residence from a causal trace and its plan.

    A token is idle only while it is a completed, ready buffer occupant.
    Production time and forwarding/delivery time are active intervals: the
    token does not enter a buffer until completion and leaves its source buffer
    at dispatch.  A forwarding completion reopens residence under the same
    token identity.  Only a non-forwarding consumption terminates the token.
    """

    if not isinstance(result, EvaluationResult):
        raise TypeError("result must be an EvaluationResult")
    if not isinstance(plan, ExecutionPlan):
        raise TypeError("plan must be an ExecutionPlan")
    if result.plan_hash != plan.plan_hash:
        raise ValueError("Resource-token ledger plan does not match the trace")

    buffers = {
        spec.id: ResourceBufferRef(
            buffer_id=spec.id,
            resource_kind=spec.token_kind,
            module=spec.module,
            submodule=spec.submodule,
        )
        for spec in plan.buffers
    }
    tokens: dict[str, _OpenToken] = {}
    inflight_forward: dict[str, tuple[int, str]] = {}
    program_consumption_roots: set[str] = set()
    resource_event_inputs: defaultdict[int, set[str]] = defaultdict(set)
    resource_event_outputs: defaultdict[int, set[str]] = defaultdict(set)

    def open_residence(
        token: _OpenToken,
        buffer_id: str,
        time_s: float,
    ) -> None:
        if token.consumption is not None or token.open_buffer_id is not None:
            raise ValueError(f"Token {token.token_id} cannot re-enter {buffer_id}")
        token.open_buffer_id = buffer_id
        token.open_since_s = time_s

    def close_residence(
        token: _OpenToken,
        buffer_id: str,
        time_s: float,
    ) -> None:
        if token.open_buffer_id != buffer_id or token.open_since_s is None:
            raise ValueError(
                f"Token {token.token_id} is not resident in buffer {buffer_id}"
            )
        token.intervals.append(
            ResourceResidenceInterval(
                buffer_id=buffer_id,
                location=buffers[buffer_id].location,
                start_s=token.open_since_s,
                end_s=time_s,
            )
        )
        token.open_buffer_id = None
        token.open_since_s = None

    for spec in plan.buffers:
        for token_id in spec.initial_contents:
            if token_id in tokens:
                raise ValueError(f"Initial token {token_id} appears more than once")
            token = _OpenToken(
                token_id=token_id,
                resource_kind=spec.token_kind,
                producer_protocol_id=None,
            )
            tokens[token_id] = token
            open_residence(token, spec.id, 0.0)

    for transition in result.trace.transitions:
        timestamp = float(transition.time_s)
        if transition.kind == ExecutionTransitionKind.DISPATCH:
            plane = str(getattr(transition.plane, "value", transition.plane))
            event_id = int(transition.event_id)
            for source_buffer, token_ids in transition.consumed_tokens.items():
                if source_buffer not in buffers:
                    raise ValueError(
                        f"Trace consumes from unknown buffer {source_buffer}"
                    )
                destination = transition.forwards.get(source_buffer)
                for token_id in token_ids:
                    try:
                        token = tokens[token_id]
                    except KeyError as exc:
                        raise ValueError(
                            f"Trace consumes unknown resource token {token_id}"
                        ) from exc
                    close_residence(token, source_buffer, timestamp)
                    if destination is not None:
                        if token_id in inflight_forward:
                            raise ValueError(
                                f"Token {token_id} is already being forwarded"
                            )
                        inflight_forward[token_id] = (
                            event_id,
                            destination,
                        )
                    else:
                        token.consumption = ResourceConsumption(
                            time_s=timestamp,
                            plane=str(
                                getattr(transition.plane, "value", transition.plane)
                            ),
                            opcode=str(
                                getattr(transition.opcode, "value", transition.opcode)
                            ),
                            event_id=event_id,
                            buffer_id=source_buffer,
                        )
                        if plane == "program":
                            program_consumption_roots.add(token_id)
                    if plane == "resource":
                        resource_event_inputs[event_id].add(token_id)
            continue

        if transition.kind != ExecutionTransitionKind.COMPLETION:
            raise ValueError(f"Unknown execution transition kind {transition.kind}")
        protocol = transition.metadata.get("protocol")
        protocol_id = str(protocol) if protocol is not None else None
        plane = str(getattr(transition.plane, "value", transition.plane))
        event_id = int(transition.event_id)
        for destination_buffer, token_ids in transition.produced_tokens.items():
            if destination_buffer not in buffers:
                raise ValueError(
                    f"Trace produces into unknown buffer {destination_buffer}"
                )
            resource_kind = buffers[destination_buffer].resource_kind
            for token_id in token_ids:
                forwarded = inflight_forward.pop(token_id, None)
                if forwarded is not None:
                    forward_event_id, expected_destination = forwarded
                    if (
                        forward_event_id != event_id
                        or expected_destination != destination_buffer
                    ):
                        raise ValueError(
                            f"Forwarded token {token_id} completed at the wrong destination"
                        )
                    token = tokens[token_id]
                    if token.resource_kind != resource_kind:
                        raise ValueError(
                            f"Forwarded token {token_id} changed resource kind"
                        )
                else:
                    if token_id in tokens:
                        raise ValueError(
                            f"Completion regenerated existing token {token_id}"
                        )
                    token = _OpenToken(
                        token_id=token_id,
                        resource_kind=resource_kind,
                        producer_protocol_id=protocol_id,
                    )
                    tokens[token_id] = token
                open_residence(token, destination_buffer, timestamp)
                if plane == "resource":
                    resource_event_outputs[event_id].add(token_id)

    horizon = float(result.total_latency_s)
    for token in tokens.values():
        if token.open_buffer_id is not None:
            close_residence(token, token.open_buffer_id, horizon)

    records = tuple(
        ResourceTokenRecord(
            token_id=token.token_id,
            resource_kind=token.resource_kind,
            producer_protocol_id=token.producer_protocol_id,
            residence_intervals=tuple(token.intervals),
            consumption=token.consumption,
        )
        for token in tokens.values()
    )

    # Settle only resource facts that can causally reach completed Program work.
    # One token identity may be produced by its factory and then by several
    # forwarding completions, so retain every Resource event that emitted that
    # identity rather than overwriting it with the latest event.  For a generic
    # multi-input/multi-output event the trace has no per-item pairing.  Once any
    # output is relevant, conservatively treat every input and the whole event as
    # relevant; unused sibling outputs do not become relevant merely by sharing
    # the event.
    output_events_by_token: defaultdict[str, list[int]] = defaultdict(list)
    for event_id, output_token_ids in resource_event_outputs.items():
        for token_id in output_token_ids:
            output_events_by_token[token_id].append(event_id)
    relevant_token_ids = set(program_consumption_roots)
    relevant_resource_event_ids: set[int] = set()
    pending_token_ids = deque(sorted(relevant_token_ids))
    expanded_token_ids: set[str] = set()
    while pending_token_ids:
        token_id = pending_token_ids.popleft()
        if token_id in expanded_token_ids:
            continue
        expanded_token_ids.add(token_id)
        for event_id in output_events_by_token.get(token_id, ()):
            relevant_resource_event_ids.add(event_id)
            for input_token_id in resource_event_inputs.get(event_id, ()):
                # A generic event may forward several independent inputs.  One
                # relevant output settles terminally consumed ancillas, but it
                # must not make an unused forwarded sibling application-
                # relevant.  A forwarded identity that is eventually consumed
                # is already a physical-consumption record and remains eligible.
                if tokens[input_token_id].consumption is None:
                    continue
                if input_token_id not in relevant_token_ids:
                    relevant_token_ids.add(input_token_id)
                    pending_token_ids.append(input_token_id)

    return ResourceTokenLedger(
        plan_hash=plan.plan_hash,
        trace_hash=result.trace_hash,
        buffers=buffers,
        tokens=records,
        application_relevant_token_ids=tuple(sorted(relevant_token_ids)),
        application_relevant_resource_event_ids=tuple(
            sorted(relevant_resource_event_ids)
        ),
    )


__all__ = [
    "ResourceBufferRef",
    "ResourceConsumption",
    "ResourceResidenceInterval",
    "ResourceTokenLedger",
    "ResourceTokenRecord",
    "resource_token_ledger",
]
