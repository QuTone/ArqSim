"""Versioned architectural state with guarded, atomic transitions."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Protocol


class StatefulOperation(Protocol):
    consumes: Mapping[str, int]
    produces: Mapping[str, int]
    forwards: Mapping[str, str]
    engines: Mapping[str, int]


class StateTransitionError(RuntimeError):
    """Base class for rejected architectural-state transitions."""


class StateBlockedError(StateTransitionError):
    """Raised when a tentative binding cannot be formed from a snapshot."""

    def __init__(self, blockers: tuple["BlockReason", ...]):
        self.blockers = blockers
        super().__init__(
            f"Cannot bind blocked operation: {[str(item) for item in blockers]}"
        )


class StaleBindingError(StateTransitionError):
    """Raised when a dispatch binding no longer matches the current version."""


class StaleCompletionError(StateTransitionError):
    """Raised when a completion delta no longer matches the current version."""


class InvalidStateTransitionError(StateTransitionError):
    """Raised when a binding or completion delta is internally inconsistent."""


def _freeze_mapping(values: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(values))


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_json(item) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_json(item) for item in value)
    return value


@dataclass(frozen=True)
class BlockReason:
    kind: str
    resource: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.resource}:{self.detail}"


@dataclass
class BufferState:
    capacity: int
    token_kind: str
    slots: tuple[str, ...]
    ready_tokens: deque[str] = field(default_factory=deque)
    pending_outputs: int = 0
    reserved_output_slots: set[str] = field(default_factory=set)


@dataclass
class EngineState:
    capacity: int
    users: int = 0


@dataclass(frozen=True)
class BufferSnapshot:
    capacity: int
    token_kind: str
    slots: tuple[str, ...]
    ready_tokens: tuple[str, ...]
    pending_outputs: int
    reserved_output_slots: frozenset[str]


@dataclass(frozen=True)
class EngineSnapshot:
    capacity: int
    users: int


@dataclass(frozen=True)
class TentativeBinding:
    """Concrete FIFO/first-free claims proposed without mutating state."""

    state_version: int
    consumes: Mapping[str, int]
    consumed_tokens: Mapping[str, tuple[str, ...]]
    consumed_slots: Mapping[str, tuple[str, ...]]
    produced_slots: Mapping[str, tuple[str, ...]]
    produces: Mapping[str, int]
    forwards: Mapping[str, str]
    engines: Mapping[str, int]
    required_locations: Mapping[str, str]
    completion_locations: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "consumes",
            _freeze_mapping(
                {str(name): int(amount) for name, amount in self.consumes.items()}
            ),
        )
        object.__setattr__(
            self,
            "consumed_tokens",
            _freeze_mapping(
                {
                    str(name): tuple(str(token) for token in tokens)
                    for name, tokens in self.consumed_tokens.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "consumed_slots",
            _freeze_mapping(
                {
                    str(name): tuple(str(slot) for slot in slots)
                    for name, slots in self.consumed_slots.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "produced_slots",
            _freeze_mapping(
                {
                    str(name): tuple(str(slot) for slot in slots)
                    for name, slots in self.produced_slots.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "produces",
            _freeze_mapping(
                {str(name): int(amount) for name, amount in self.produces.items()}
            ),
        )
        object.__setattr__(
            self,
            "forwards",
            _freeze_mapping(
                {
                    str(source): str(destination)
                    for source, destination in self.forwards.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "engines",
            _freeze_mapping(
                {str(name): int(amount) for name, amount in self.engines.items()}
            ),
        )
        object.__setattr__(
            self,
            "required_locations",
            _freeze_mapping(
                {
                    str(item): str(location)
                    for item, location in self.required_locations.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "completion_locations",
            _freeze_mapping(
                {
                    str(item): str(location)
                    for item, location in self.completion_locations.items()
                }
            ),
        )


@dataclass(frozen=True)
class StateSnapshot:
    """Deeply immutable read model used by binders and runtime compilers."""

    version: int
    buffers: Mapping[str, BufferSnapshot]
    engines: Mapping[str, EngineSnapshot]
    locations: Mapping[str, str]
    token_slots: Mapping[str, str]
    token_sequence: Mapping[str, int]
    active_reservation_ids: frozenset[int]
    next_reservation_id: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "buffers", _freeze_mapping(self.buffers))
        object.__setattr__(self, "engines", _freeze_mapping(self.engines))
        object.__setattr__(
            self,
            "locations",
            _freeze_mapping(
                {str(item): str(location) for item, location in self.locations.items()}
            ),
        )
        object.__setattr__(
            self,
            "token_slots",
            _freeze_mapping(
                {str(token): str(slot) for token, slot in self.token_slots.items()}
            ),
        )
        object.__setattr__(
            self,
            "token_sequence",
            _freeze_mapping(
                {
                    str(token_kind): int(sequence)
                    for token_kind, sequence in self.token_sequence.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "active_reservation_ids",
            frozenset(int(value) for value in self.active_reservation_ids),
        )

    def occupancy(self) -> dict[str, int]:
        return {
            name: len(buffer.ready_tokens) for name, buffer in self.buffers.items()
        }

    def pending_outputs(self) -> dict[str, int]:
        return {
            name: buffer.pending_outputs for name, buffer in self.buffers.items()
        }

    def engine_usage(self) -> dict[str, int]:
        return {name: engine.users for name, engine in self.engines.items()}

    def eager_batch_size(self, operation: StatefulOperation) -> int:
        """Return the largest item batch dispatchable from this snapshot."""

        if any(
            self.engines[name].users + int(amount) > self.engines[name].capacity
            for name, amount in operation.engines.items()
        ):
            return 0
        limits: list[int] = []
        for name, amount in operation.consumes.items():
            limits.append(len(self.buffers[name].ready_tokens) // int(amount))
        for name, amount in operation.produces.items():
            buffer = self.buffers[name]
            free = (
                buffer.capacity
                - len(buffer.ready_tokens)
                - buffer.pending_outputs
            )
            limits.append(free // int(amount))
        return max(0, min(limits, default=1))

    def check_start(
        self,
        operation: StatefulOperation,
        *,
        required_locations: Mapping[str, str] | None = None,
    ) -> tuple[BlockReason, ...]:
        reasons: list[BlockReason] = []
        for name, amount in operation.consumes.items():
            available = len(self.buffers[name].ready_tokens)
            if available < int(amount):
                reasons.append(
                    BlockReason("buffer_empty", name, f"{available}/{amount}")
                )
        for name, amount in operation.produces.items():
            buffer = self.buffers[name]
            occupied = len(buffer.ready_tokens)
            if occupied + buffer.pending_outputs + int(amount) > buffer.capacity:
                reasons.append(
                    BlockReason(
                        "buffer_full",
                        name,
                        (
                            f"{occupied}+{buffer.pending_outputs}+{amount}/"
                            f"{buffer.capacity}"
                        ),
                    )
                )
        for name, amount in operation.engines.items():
            engine = self.engines[name]
            if engine.users + int(amount) > engine.capacity:
                reasons.append(
                    BlockReason(
                        "engine_busy",
                        name,
                        f"{engine.users}+{amount}/{engine.capacity}",
                    )
                )
        for item, expected in (required_locations or {}).items():
            actual = self.locations.get(str(item))
            if actual != expected:
                reasons.append(
                    BlockReason("location", str(item), f"{actual}->{expected}")
                )
        return tuple(sorted(reasons, key=str))

    def propose(
        self,
        operation: StatefulOperation,
        *,
        required_locations: Mapping[str, str] | None = None,
        completion_locations: Mapping[str, str] | None = None,
    ) -> TentativeBinding:
        """Resolve FIFO tokens and first-free slots without changing state."""

        effective_required_locations = (
            getattr(operation, "required_locations", {})
            if required_locations is None
            else required_locations
        )
        effective_completion_locations = (
            getattr(operation, "completion_locations", {})
            if completion_locations is None
            else completion_locations
        )
        blockers = self.check_start(
            operation, required_locations=effective_required_locations
        )
        if blockers:
            raise StateBlockedError(blockers)

        consumed_tokens: dict[str, tuple[str, ...]] = {}
        consumed_slots: dict[str, tuple[str, ...]] = {}
        for name, raw_amount in operation.consumes.items():
            amount = int(raw_amount)
            tokens = self.buffers[name].ready_tokens[:amount]
            consumed_tokens[name] = tokens
            consumed_slots[name] = tuple(self.token_slots[token] for token in tokens)

        produced_slots: dict[str, tuple[str, ...]] = {}
        for name, raw_amount in operation.produces.items():
            amount = int(raw_amount)
            buffer = self.buffers[name]
            occupied_slots = {
                self.token_slots[token] for token in buffer.ready_tokens
            } | set(buffer.reserved_output_slots)
            slots = tuple(
                candidate
                for candidate in buffer.slots
                if candidate not in occupied_slots
            )[:amount]
            if len(slots) != amount:
                raise InvalidStateTransitionError(
                    f"Cannot tentatively reserve {amount} output slots in {name}"
                )
            produced_slots[name] = slots

        return TentativeBinding(
            state_version=self.version,
            consumes=operation.consumes,
            consumed_tokens=consumed_tokens,
            consumed_slots=consumed_slots,
            produced_slots=produced_slots,
            produces=operation.produces,
            forwards=operation.forwards,
            engines=operation.engines,
            required_locations=effective_required_locations,
            completion_locations=effective_completion_locations,
        )


@dataclass(frozen=True)
class StateReservation:
    reservation_id: int
    binding_version: int
    committed_version: int
    consumes: Mapping[str, int]
    consumed_tokens: Mapping[str, tuple[str, ...]]
    consumed_slots: Mapping[str, tuple[str, ...]]
    produced_slots: Mapping[str, tuple[str, ...]]
    produces: Mapping[str, int]
    forwards: Mapping[str, str]
    engines: Mapping[str, int]
    completion_locations: Mapping[str, str]

    def __post_init__(self) -> None:
        binding = TentativeBinding(
            state_version=self.binding_version,
            consumes=self.consumes,
            consumed_tokens=self.consumed_tokens,
            consumed_slots=self.consumed_slots,
            produced_slots=self.produced_slots,
            produces=self.produces,
            forwards=self.forwards,
            engines=self.engines,
            required_locations={},
            completion_locations=self.completion_locations,
        )
        object.__setattr__(self, "consumes", binding.consumes)
        object.__setattr__(self, "consumed_tokens", binding.consumed_tokens)
        object.__setattr__(self, "consumed_slots", binding.consumed_slots)
        object.__setattr__(self, "produced_slots", binding.produced_slots)
        object.__setattr__(self, "produces", binding.produces)
        object.__setattr__(self, "forwards", binding.forwards)
        object.__setattr__(self, "engines", binding.engines)
        object.__setattr__(
            self, "completion_locations", binding.completion_locations
        )


@dataclass(frozen=True)
class StateDelta:
    """Explicit completion transition proposed before it is applied."""

    state_version: int
    reservation_id: int
    produced_tokens: Mapping[str, tuple[str, ...]]
    produced_slots: Mapping[str, tuple[str, ...]]
    released_engines: Mapping[str, int]
    completion_locations: Mapping[str, str]
    token_sequence_after: Mapping[str, int]
    buffer_occupancy: Mapping[str, int]
    pending_outputs: Mapping[str, int]
    outcome: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "produced_tokens",
            _freeze_mapping(
                {
                    str(name): tuple(str(token) for token in tokens)
                    for name, tokens in self.produced_tokens.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "produced_slots",
            _freeze_mapping(
                {
                    str(name): tuple(str(slot) for slot in slots)
                    for name, slots in self.produced_slots.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "released_engines",
            _freeze_mapping(
                {
                    str(name): int(amount)
                    for name, amount in self.released_engines.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "completion_locations",
            _freeze_mapping(
                {
                    str(item): str(location)
                    for item, location in self.completion_locations.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "token_sequence_after",
            _freeze_mapping(
                {
                    str(kind): int(sequence)
                    for kind, sequence in self.token_sequence_after.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "buffer_occupancy",
            _freeze_mapping(
                {
                    str(name): int(amount)
                    for name, amount in self.buffer_occupancy.items()
                }
            ),
        )
        object.__setattr__(
            self,
            "pending_outputs",
            _freeze_mapping(
                {
                    str(name): int(amount)
                    for name, amount in self.pending_outputs.items()
                }
            ),
        )
        object.__setattr__(self, "outcome", _freeze_json(self.outcome))


@dataclass
class ArchitectureState:
    buffers: dict[str, BufferState]
    engines: dict[str, EngineState]
    locations: dict[str, str]
    token_slots: dict[str, str] = field(default_factory=dict)
    token_sequence: defaultdict[str, int] = field(
        default_factory=lambda: defaultdict(int)
    )
    version: int = 0
    next_reservation_id: int = 0
    active_reservations: dict[int, StateReservation] = field(
        default_factory=dict, repr=False
    )

    @classmethod
    def from_plan(cls, plan) -> "ArchitectureState":
        buffers = {
            spec.id: BufferState(
                capacity=spec.capacity,
                token_kind=spec.token_kind,
                slots=spec.slots,
                ready_tokens=deque(spec.initial_contents),
            )
            for spec in plan.buffers
        }
        conflicting_initial_ids = {
            token
            for buffer in buffers.values()
            for token in buffer.ready_tokens
        } & set(plan.initial_locations)
        if conflicting_initial_ids:
            raise ValueError(
                "Initial resource-token ids conflict with initial location entities: "
                f"{sorted(conflicting_initial_ids)}"
            )
        state = cls(
            buffers=buffers,
            engines={
                spec.id: EngineState(capacity=spec.capacity) for spec in plan.engines
            },
            locations={
                str(key): str(value) for key, value in plan.initial_locations.items()
            },
        )
        seen_tokens: set[str] = set()
        for buffer_id, buffer in state.buffers.items():
            duplicates = seen_tokens.intersection(buffer.ready_tokens)
            if duplicates:
                raise ValueError(
                    "Initial resource-token ids must be globally unique: "
                    f"{sorted(duplicates)}"
                )
            for token, slot in zip(
                buffer.ready_tokens,
                buffer.slots[: len(buffer.ready_tokens)],
                strict=True,
            ):
                state.locations[token] = buffer_id
                state.token_slots[token] = slot
                prefix = f"{buffer.token_kind}:"
                if token.startswith(prefix):
                    suffix = token[len(prefix) :]
                    if suffix.isdigit():
                        state.token_sequence[buffer.token_kind] = max(
                            state.token_sequence[buffer.token_kind],
                            int(suffix) + 1,
                        )
            seen_tokens.update(buffer.ready_tokens)
        return state

    def snapshot(self) -> StateSnapshot:
        return StateSnapshot(
            version=self.version,
            buffers={
                name: BufferSnapshot(
                    capacity=buffer.capacity,
                    token_kind=buffer.token_kind,
                    slots=tuple(buffer.slots),
                    ready_tokens=tuple(buffer.ready_tokens),
                    pending_outputs=buffer.pending_outputs,
                    reserved_output_slots=frozenset(buffer.reserved_output_slots),
                )
                for name, buffer in self.buffers.items()
            },
            engines={
                name: EngineSnapshot(
                    capacity=engine.capacity,
                    users=engine.users,
                )
                for name, engine in self.engines.items()
            },
            locations=dict(self.locations),
            token_slots=dict(self.token_slots),
            token_sequence=dict(self.token_sequence),
            active_reservation_ids=frozenset(self.active_reservations),
            next_reservation_id=self.next_reservation_id,
        )

    def occupancy(self) -> dict[str, int]:
        return {
            name: len(buffer.ready_tokens) for name, buffer in self.buffers.items()
        }

    def pending_outputs(self) -> dict[str, int]:
        return {
            name: buffer.pending_outputs for name, buffer in self.buffers.items()
        }

    def engine_usage(self) -> dict[str, int]:
        return {name: engine.users for name, engine in self.engines.items()}

    def propose(
        self,
        operation: StatefulOperation,
        *,
        snapshot: StateSnapshot | None = None,
        required_locations: Mapping[str, str] | None = None,
        completion_locations: Mapping[str, str] | None = None,
    ) -> TentativeBinding:
        candidate_snapshot = snapshot or self.snapshot()
        if candidate_snapshot.version != self.version:
            raise StaleBindingError(
                "Cannot propose from stale state version "
                f"{candidate_snapshot.version}; current={self.version}"
            )
        return candidate_snapshot.propose(
            operation,
            required_locations=required_locations,
            completion_locations=completion_locations,
        )

    def _validate_binding(
        self,
        binding: TentativeBinding,
        operation: StatefulOperation,
    ) -> None:
        if binding.state_version != self.version:
            raise StaleBindingError(
                f"Stale binding version {binding.state_version}; current={self.version}"
            )

        expected_required_locations = dict(
            getattr(operation, "required_locations", {})
        )
        expected_completion_locations = dict(
            getattr(operation, "completion_locations", {})
        )
        contracts = (
            ("consumes", binding.consumes, operation.consumes),
            ("produces", binding.produces, operation.produces),
            ("forwards", binding.forwards, operation.forwards),
            ("engines", binding.engines, operation.engines),
            (
                "required locations",
                binding.required_locations,
                expected_required_locations,
            ),
            (
                "completion locations",
                binding.completion_locations,
                expected_completion_locations,
            ),
        )
        for label, actual, expected in contracts:
            if dict(actual) != dict(expected):
                raise InvalidStateTransitionError(
                    f"Binding {label} do not match the operation contract"
                )

        if set(binding.consumed_tokens) != set(binding.consumes):
            raise InvalidStateTransitionError(
                "Consumed quantity and concrete-token claims do not match"
            )

        seen_tokens: set[str] = set()
        for name, raw_amount in binding.consumes.items():
            if name not in self.buffers:
                raise InvalidStateTransitionError(f"Unknown input buffer {name}")
            amount = int(raw_amount)
            tokens = binding.consumed_tokens[name]
            ready = set(self.buffers[name].ready_tokens)
            if any(token not in ready for token in tokens):
                raise InvalidStateTransitionError(
                    f"Binding names an unavailable token in {name}"
                )
            if (
                amount <= 0
                or len(tokens) != amount
                or len(set(tokens)) != len(tokens)
                or seen_tokens.intersection(tokens)
            ):
                raise InvalidStateTransitionError(
                    "A binding must consume positive, unique concrete tokens"
                )
            expected_slots = tuple(self.token_slots.get(token) for token in tokens)
            if binding.consumed_slots.get(name) != expected_slots:
                raise InvalidStateTransitionError(
                    f"Input slots do not match concrete tokens in {name}"
                )
            if any(self.locations.get(token) != name for token in tokens):
                raise InvalidStateTransitionError(
                    f"Input token location does not match buffer {name}"
                )
            seen_tokens.update(tokens)
        if set(binding.consumed_slots) != set(binding.consumed_tokens):
            raise InvalidStateTransitionError(
                "Consumed token and slot buffer claims do not match"
            )

        for name, raw_amount in binding.produces.items():
            if name not in self.buffers:
                raise InvalidStateTransitionError(f"Unknown output buffer {name}")
            amount = int(raw_amount)
            if amount <= 0:
                raise InvalidStateTransitionError(
                    "Output quantities must be positive"
                )
            buffer = self.buffers[name]
            occupied_slots = {
                self.token_slots[token] for token in buffer.ready_tokens
            } | buffer.reserved_output_slots
            slots = binding.produced_slots.get(name, ())
            if (
                len(slots) != amount
                or len(set(slots)) != amount
                or any(slot not in buffer.slots for slot in slots)
                or any(slot in occupied_slots for slot in slots)
            ):
                raise InvalidStateTransitionError(
                    f"Output slot claim is not currently feasible in {name}"
                )
        if set(binding.produced_slots) != set(binding.produces):
            raise InvalidStateTransitionError(
                "Produced quantity and slot buffer claims do not match"
            )

        for name, raw_amount in binding.engines.items():
            if name not in self.engines:
                raise InvalidStateTransitionError(f"Unknown engine {name}")
            amount = int(raw_amount)
            engine = self.engines[name]
            if amount <= 0 or engine.users + amount > engine.capacity:
                raise InvalidStateTransitionError(
                    f"Engine claim is not currently feasible for {name}"
                )

        for item, expected in binding.required_locations.items():
            if self.locations.get(item) != expected:
                raise InvalidStateTransitionError(
                    f"Required location changed for {item}"
                )

        for source, destination in binding.forwards.items():
            if source not in binding.consumed_tokens:
                raise InvalidStateTransitionError(
                    f"Forwarding source {source} is not consumed"
                )
            if destination not in binding.produces:
                raise InvalidStateTransitionError(
                    f"Forwarding destination {destination} is not produced"
                )
            if len(binding.consumed_tokens[source]) != binding.produces[destination]:
                raise InvalidStateTransitionError(
                    "Forwarding must preserve token count"
                )
            if (
                self.buffers[source].token_kind
                != self.buffers[destination].token_kind
            ):
                raise InvalidStateTransitionError(
                    "Forwarding must preserve token kind"
                )
        if len(set(binding.forwards.values())) != len(binding.forwards):
            raise InvalidStateTransitionError(
                "Each forwarding destination must have exactly one source"
            )

    def commit(
        self,
        binding: TentativeBinding,
        operation: StatefulOperation,
    ) -> StateReservation:
        """Atomically commit one validated tentative binding."""

        self._validate_binding(binding, operation)
        reservation = StateReservation(
            reservation_id=self.next_reservation_id,
            binding_version=binding.state_version,
            committed_version=self.version + 1,
            consumes=binding.consumes,
            consumed_tokens=binding.consumed_tokens,
            consumed_slots=binding.consumed_slots,
            produced_slots=binding.produced_slots,
            produces=binding.produces,
            forwards=binding.forwards,
            engines=binding.engines,
            completion_locations=binding.completion_locations,
        )

        for name, tokens in binding.consumed_tokens.items():
            buffer = self.buffers[name]
            for token in tokens:
                buffer.ready_tokens.remove(token)
                self.token_slots.pop(token)
                self.locations.pop(token, None)

        for name, slots in binding.produced_slots.items():
            buffer = self.buffers[name]
            buffer.reserved_output_slots.update(slots)
            buffer.pending_outputs += len(slots)

        for name, amount in binding.engines.items():
            self.engines[name].users += int(amount)

        self.version += 1
        self.next_reservation_id += 1
        self.active_reservations[reservation.reservation_id] = reservation
        return reservation

    def _completion_delta(
        self,
        reservation: StateReservation,
        snapshot: StateSnapshot,
        *,
        outcome: Mapping[str, Any] | None = None,
    ) -> StateDelta:
        active = self.active_reservations.get(reservation.reservation_id)
        if active != reservation:
            raise InvalidStateTransitionError(
                f"Reservation {reservation.reservation_id} is not active"
            )

        for name, raw_amount in reservation.engines.items():
            amount = int(raw_amount)
            if snapshot.engines[name].users < amount:
                raise InvalidStateTransitionError(
                    f"Engine {name} cannot release {amount} users"
                )

        forwarded: dict[str, list[str]] = defaultdict(list)
        for source, destination in reservation.forwards.items():
            forwarded[destination].extend(reservation.consumed_tokens[source])

        produced_tokens: dict[str, tuple[str, ...]] = {}
        token_sequence_after: dict[str, int] = {}
        next_sequences = dict(snapshot.token_sequence)
        occupied_generated_tokens = (
            set(snapshot.locations)
            | set(snapshot.token_slots)
            | {
                token
                for tokens in forwarded.values()
                for token in tokens
            }
        )
        for name, raw_amount in reservation.produces.items():
            amount = int(raw_amount)
            buffer = snapshot.buffers[name]
            slots = reservation.produced_slots[name]
            if buffer.pending_outputs < amount:
                raise InvalidStateTransitionError(
                    f"Output buffer {name} has insufficient pending outputs"
                )
            if len(slots) != amount or any(
                slot not in buffer.reserved_output_slots for slot in slots
            ):
                raise InvalidStateTransitionError(
                    f"Output slots are not reserved for completion in {name}"
                )
            ready_slots = {
                snapshot.token_slots[token] for token in buffer.ready_tokens
            }
            if ready_slots.intersection(slots):
                raise InvalidStateTransitionError(
                    f"Reserved output slot is already occupied in {name}"
                )

            tokens = list(forwarded.get(name, ()))[:amount]
            sequence = int(next_sequences.get(buffer.token_kind, 0))
            generated = False
            while len(tokens) < amount:
                token = f"{buffer.token_kind}:{sequence}"
                if token in occupied_generated_tokens or token in tokens:
                    sequence += 1
                    continue
                tokens.append(token)
                sequence += 1
                generated = True
            if generated:
                next_sequences[buffer.token_kind] = sequence
                token_sequence_after[buffer.token_kind] = sequence
            produced_tokens[name] = tuple(tokens)
            occupied_generated_tokens.update(tokens)

        all_produced_tokens = [
            token
            for tokens in produced_tokens.values()
            for token in tokens
        ]
        if len(all_produced_tokens) != len(set(all_produced_tokens)):
            raise InvalidStateTransitionError(
                "A completion cannot place one token in multiple buffers"
            )

        occupancy_after = snapshot.occupancy()
        pending_after = snapshot.pending_outputs()
        for name, amount in reservation.produces.items():
            occupancy_after[name] += int(amount)
            pending_after[name] -= int(amount)

        return StateDelta(
            state_version=snapshot.version,
            reservation_id=reservation.reservation_id,
            produced_tokens=produced_tokens,
            produced_slots=reservation.produced_slots,
            released_engines=reservation.engines,
            completion_locations=reservation.completion_locations,
            token_sequence_after=token_sequence_after,
            buffer_occupancy=occupancy_after,
            pending_outputs=pending_after,
            outcome=outcome or {},
        )

    def propose_completion(
        self,
        reservation: StateReservation,
        *,
        snapshot: StateSnapshot | None = None,
        outcome: Mapping[str, Any] | None = None,
    ) -> StateDelta:
        """Purely resolve the effects of one active reservation completing."""

        candidate_snapshot = snapshot or self.snapshot()
        if candidate_snapshot.version != self.version:
            raise StaleCompletionError(
                "Cannot propose completion from stale state version "
                f"{candidate_snapshot.version}; current={self.version}"
            )
        return self._completion_delta(
            reservation, candidate_snapshot, outcome=outcome
        )

    def apply_completion(self, delta: StateDelta) -> StateDelta:
        """Atomically apply a fully resolved completion delta."""

        if delta.state_version != self.version:
            raise StaleCompletionError(
                f"Stale completion version {delta.state_version}; current={self.version}"
            )
        reservation = self.active_reservations.get(delta.reservation_id)
        if reservation is None:
            raise InvalidStateTransitionError(
                f"Reservation {delta.reservation_id} is not active"
            )
        expected = self._completion_delta(
            reservation,
            self.snapshot(),
            outcome=delta.outcome,
        )
        if delta != expected:
            raise InvalidStateTransitionError(
                f"Completion delta for reservation {delta.reservation_id} is invalid"
            )

        for name, raw_amount in reservation.engines.items():
            self.engines[name].users -= int(raw_amount)

        for name, raw_amount in reservation.produces.items():
            amount = int(raw_amount)
            buffer = self.buffers[name]
            buffer.pending_outputs -= amount
            slots = reservation.produced_slots[name]
            tokens = delta.produced_tokens[name]
            for token, slot in zip(tokens, slots, strict=True):
                buffer.reserved_output_slots.remove(slot)
                buffer.ready_tokens.append(token)
                self.locations[token] = name
                self.token_slots[token] = slot

        for token_kind, sequence in delta.token_sequence_after.items():
            self.token_sequence[token_kind] = int(sequence)
        for item, location in reservation.completion_locations.items():
            self.locations[str(item)] = str(location)

        self.active_reservations.pop(reservation.reservation_id)
        self.version += 1
        return delta
