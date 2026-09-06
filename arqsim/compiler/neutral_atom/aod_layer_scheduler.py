"""Layer-local AOD movement grouping and round-trip scheduling.

The row/column ordering invariant is motivated by the collective-movement idea
described by J. Ruan et al., *PowerMove: Optimizing Compilation for Neutral
Atom Quantum Computers with Zoned Architecture*, ASPLOS 2025,
doi:10.1145/3676642.3736128.  PowerMove is cited here for algorithmic
provenance; its source is neither bundled nor imported.  This implementation
represents pairwise movement incompatibilities as a conflict graph and groups
them with deterministic greedy coloring.

Unlike the full PowerMove compiler, this module starts every logical layer from
an externally supplied home placement and returns all data patches before the
layer completes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from arqsim.program import LogicalLayer


Point = tuple[float, float]


@dataclass(frozen=True)
class AODTimingModel:
    """Reference movement model used to timestamp one logical layer."""

    x_spacing_um: float = 19.0
    y_spacing_um: float = 15.0
    distance_metric: str = "euclidean"
    distance_scale: float = 1.0
    transfer_duration_us: float = 8.0
    reference_distance_um: float = 110.0
    reference_move_duration_us: float = 200.0
    gate_duration_us: float = 0.0

    def __post_init__(self) -> None:
        metric = self.distance_metric.strip().lower()
        if metric not in {"euclidean", "manhattan"}:
            raise ValueError(
                "AOD distance_metric must be 'euclidean' or 'manhattan': "
                f"{self.distance_metric!r}"
            )
        object.__setattr__(self, "distance_metric", metric)
        positive = {
            "x_spacing_um": self.x_spacing_um,
            "y_spacing_um": self.y_spacing_um,
            "distance_scale": self.distance_scale,
            "reference_distance_um": self.reference_distance_um,
            "reference_move_duration_us": self.reference_move_duration_us,
        }
        nonnegative = {
            "transfer_duration_us": self.transfer_duration_us,
            "gate_duration_us": self.gate_duration_us,
        }
        if any(not math.isfinite(value) or value <= 0 for value in positive.values()):
            raise ValueError(f"AOD timing parameters must be finite and positive: {positive}")
        if any(not math.isfinite(value) or value < 0 for value in nonnegative.values()):
            raise ValueError(
                f"AOD timing parameters must be finite and non-negative: {nonnegative}"
            )

    def distance_um(self, source: Point, destination: Point) -> float:
        dx_um = abs(destination[0] - source[0]) * self.x_spacing_um
        dy_um = abs(destination[1] - source[1]) * self.y_spacing_um
        if self.distance_metric == "euclidean":
            distance = math.hypot(dx_um, dy_um)
        else:
            distance = dx_um + dy_um
        return self.distance_scale * distance

    def movement_duration_us(self, source: Point, destination: Point) -> float:
        distance = self.distance_um(source, destination)
        if distance == 0:
            return 0.0
        return self.reference_move_duration_us * math.sqrt(
            distance / self.reference_distance_um
        )

    def task_duration_us(self, moves: Iterable["LayerMove"]) -> float:
        moves = tuple(moves)
        if not moves:
            return 0.0
        return 2 * self.transfer_duration_us + max(
            self.movement_duration_us(move.source, move.destination) for move in moves
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "x_spacing_um": self.x_spacing_um,
            "y_spacing_um": self.y_spacing_um,
            "distance_metric": self.distance_metric,
            "distance_scale": self.distance_scale,
            "transfer_duration_us": self.transfer_duration_us,
            "reference_distance_um": self.reference_distance_um,
            "reference_move_duration_us": self.reference_move_duration_us,
            "gate_duration_us": self.gate_duration_us,
        }


@dataclass(frozen=True)
class LayerMove:
    id: str
    operation_index: int
    logical_qubit: int
    source: Point
    destination: Point
    entity_kind: str = "logical_qubit"
    phase: str = "approach"
    source_slot_id: str | None = None
    target_logical_qubit: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", tuple(float(value) for value in self.source))
        object.__setattr__(
            self,
            "destination",
            tuple(float(value) for value in self.destination),
        )
        if len(self.source) != 2 or len(self.destination) != 2:
            raise ValueError("AOD movements require two-dimensional coordinates")
        if any(
            not math.isfinite(value)
            for coordinate in (self.source, self.destination)
            for value in coordinate
        ):
            raise ValueError("AOD movement coordinates must be finite")
        if self.entity_kind not in {"logical_qubit", "magic_state"}:
            raise ValueError(f"Unsupported AOD movement entity: {self.entity_kind}")
        if self.phase not in {"approach", "delivery", "return"}:
            raise ValueError(f"Unsupported AOD movement phase: {self.phase}")

    def reversed(self) -> "LayerMove":
        if self.entity_kind != "logical_qubit":
            raise ValueError("Consumed magic-state movements do not return")
        return LayerMove(
            id=f"return:{self.id}",
            operation_index=self.operation_index,
            logical_qubit=self.logical_qubit,
            source=self.destination,
            destination=self.source,
            entity_kind=self.entity_kind,
            phase="return",
        )

    def to_dict(self, timing: AODTimingModel | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "operation_index": self.operation_index,
            "logical_qubit": self.logical_qubit,
            "source": list(self.source),
            "destination": list(self.destination),
            "entity_kind": self.entity_kind,
            "phase": self.phase,
        }
        if self.source_slot_id is not None:
            result["source_slot_id"] = self.source_slot_id
        if self.target_logical_qubit is not None:
            result["target_logical_qubit"] = self.target_logical_qubit
        if timing is not None:
            result["distance_um"] = timing.distance_um(self.source, self.destination)
            result["movement_duration_us"] = timing.movement_duration_us(
                self.source,
                self.destination,
            )
        return result


@dataclass(frozen=True)
class RoundTripGroup:
    index: int
    forward_duration_us: float
    return_duration_us: float | None

    def __post_init__(self) -> None:
        if not math.isfinite(self.forward_duration_us) or self.forward_duration_us < 0:
            raise ValueError("Forward movement duration must be finite and non-negative")
        if self.return_duration_us is not None and (
            not math.isfinite(self.return_duration_us) or self.return_duration_us < 0
        ):
            raise ValueError("Return movement duration must be finite and non-negative")


@dataclass(frozen=True)
class AODTask:
    id: str
    phase: str
    group_index: int
    aod: int
    start_us: float
    end_us: float
    movement_ids: tuple[str, ...] = field(default_factory=tuple)

    @property
    def duration_us(self) -> float:
        return self.end_us - self.start_us

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "phase": self.phase,
            "group": self.group_index,
            "aod": self.aod,
            "start_us": self.start_us,
            "end_us": self.end_us,
            "duration_us": self.duration_us,
            "movement_ids": list(self.movement_ids),
        }


@dataclass(frozen=True)
class AODPipelineSchedule:
    num_aod: int
    policy: str
    tasks: tuple[AODTask, ...]
    gate_duration_us: float
    duration_us: float
    forward_barrier_duration_us: float
    round_trip_barrier_duration_us: float

    @property
    def pipeline_savings_us(self) -> float:
        return max(self.round_trip_barrier_duration_us - self.duration_us, 0.0)

    def to_dict(self) -> dict[str, Any]:
        return_tasks = tuple(task for task in self.tasks if task.phase == "return")
        return {
            "num_aod": self.num_aod,
            "policy": self.policy,
            "gate_duration_us": self.gate_duration_us,
            "duration_us": self.duration_us,
            "forward_barrier_duration_us": self.forward_barrier_duration_us,
            "round_trip_barrier_duration_us": self.round_trip_barrier_duration_us,
            "pipeline_savings_us": self.pipeline_savings_us,
            "tasks": [task.to_dict() for task in self.tasks],
            "dependency_edges": [
                {
                    "predecessor": f"approach:g{task.group_index}",
                    "successor": task.id,
                    "minimum_delay_us": self.gate_duration_us,
                }
                for task in return_tasks
            ],
        }


@dataclass(frozen=True)
class AODStageSchedule:
    index: int
    operation_indices: tuple[int, ...]
    forward_moves: tuple[LayerMove, ...]
    return_moves: tuple[LayerMove, ...]
    collective_groups: tuple[tuple[str, ...], ...]
    pipeline: AODPipelineSchedule
    magic_state_assignments: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)

    @property
    def moves_by_id(self) -> dict[str, LayerMove]:
        return {move.id: move for move in (*self.forward_moves, *self.return_moves)}

    def to_dict(self, timing: AODTimingModel) -> dict[str, Any]:
        return {
            "index": self.index,
            "operation_indices": list(self.operation_indices),
            "forward_moves": [move.to_dict(timing) for move in self.forward_moves],
            "return_moves": [move.to_dict(timing) for move in self.return_moves],
            "collective_groups": [list(group) for group in self.collective_groups],
            "pipeline": self.pipeline.to_dict(),
            "magic_state_assignments": [dict(value) for value in self.magic_state_assignments],
        }


@dataclass(frozen=True)
class LayerAODSchedule:
    layer_index: int
    num_aod: int
    timing_model: AODTimingModel
    stages: tuple[AODStageSchedule, ...]

    @property
    def duration_us(self) -> float:
        return sum(stage.pipeline.duration_us for stage in self.stages)

    @property
    def round_trip_barrier_duration_us(self) -> float:
        return sum(
            stage.pipeline.round_trip_barrier_duration_us for stage in self.stages
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer_index,
            "num_aod": self.num_aod,
            "timing_model": self.timing_model.to_dict(),
            "duration_us": self.duration_us,
            "round_trip_barrier_duration_us": self.round_trip_barrier_duration_us,
            "pipeline_savings_us": max(
                self.round_trip_barrier_duration_us - self.duration_us,
                0.0,
            ),
            "stages": [stage.to_dict(self.timing_model) for stage in self.stages],
        }


def _sign(value: float) -> int:
    return 0 if value == 0 else (1 if value > 0 else -1)


def movements_conflict(left: LayerMove, right: LayerMove) -> bool:
    """Return whether two moves violate one-AOD row/column ordering."""

    for dimension in range(2):
        source_order = _sign(right.source[dimension] - left.source[dimension])
        destination_order = _sign(
            right.destination[dimension] - left.destination[dimension]
        )
        if source_order != destination_order:
            return True
    return False


def _movement_conflict_graph(
    moves: Sequence[LayerMove],
) -> tuple[frozenset[int], ...]:
    """Build an undirected graph over indices of pairwise-incompatible moves."""

    neighbors: list[set[int]] = [set() for _ in moves]
    for left_index, left in enumerate(moves):
        for right_index in range(left_index + 1, len(moves)):
            if not movements_conflict(left, moves[right_index]):
                continue
            neighbors[left_index].add(right_index)
            neighbors[right_index].add(left_index)
    return tuple(frozenset(adjacent) for adjacent in neighbors)


def _deterministic_greedy_coloring(
    conflict_graph: Sequence[frozenset[int]],
) -> tuple[int, ...]:
    """Assign each vertex the first color absent from colored neighbors."""

    colors: list[int] = []
    for vertex, neighbors in enumerate(conflict_graph):
        unavailable = {
            colors[neighbor]
            for neighbor in neighbors
            if neighbor < vertex
        }
        color = 0
        while color in unavailable:
            color += 1
        colors.append(color)
    return tuple(colors)


def group_collective_movements(
    moves: Sequence[LayerMove],
    timing: AODTimingModel,
) -> tuple[tuple[LayerMove, ...], ...]:
    """Color the movement-conflict graph into compatible collective groups.

    Vertices are ordered by movement duration and then movement ID.  Stable
    sorting preserves caller order if both keys tie.  First-fit coloring in
    that order is deterministic and preserves the historical grouping policy.
    """

    ordered = tuple(
        sorted(
            moves,
            key=lambda move: (
                timing.movement_duration_us(move.source, move.destination),
                move.id,
            ),
        )
    )
    colors = _deterministic_greedy_coloring(_movement_conflict_graph(ordered))
    groups: list[list[LayerMove]] = [
        [] for _ in range(max(colors, default=-1) + 1)
    ]
    for move, color in zip(ordered, colors):
        groups[color].append(move)
    return tuple(tuple(group) for group in groups)


def _barrier_duration(durations: Sequence[float], num_aod: int) -> float:
    return sum(
        max(durations[start : start + num_aod])
        for start in range(0, len(durations), num_aod)
    )


def _simulate_pipeline(
    groups: Sequence[RoundTripGroup],
    *,
    num_aod: int,
    gate_duration_us: float,
    policy: str,
) -> tuple[float, tuple[AODTask, ...]]:
    forward = {group.index: group for group in groups}
    unscheduled_forward = set(forward)
    ready_returns: set[int] = set()
    delayed_returns: dict[int, float] = {}
    running: dict[int, tuple[float, str, int]] = {}
    free_aods = set(range(num_aod))
    terminal_completion = 0.0
    tasks: list[AODTask] = []
    now = 0.0

    def candidate_key(candidate: tuple[str, int]) -> tuple[Any, ...]:
        phase, index = candidate
        group = forward[index]
        duration = (
            group.forward_duration_us
            if phase == "approach"
            else float(group.return_duration_us)
        )
        remaining = duration
        if phase == "approach" and group.return_duration_us is not None:
            remaining += gate_duration_us + group.return_duration_us
        if policy == "forward_first":
            return (phase != "approach", index)
        if policy == "return_first":
            return (phase != "return", index)
        if policy == "longest_remaining":
            return (-remaining, phase != "approach", index)
        if policy == "shortest_task":
            return (duration, phase != "return", index)
        raise ValueError(f"Unknown AOD pipeline policy: {policy}")

    while unscheduled_forward or ready_returns or delayed_returns or running:
        completed = [aod for aod, value in running.items() if value[0] <= now]
        for aod in sorted(completed):
            end_us, phase, index = running.pop(aod)
            free_aods.add(aod)
            group = forward[index]
            if phase == "approach":
                gate_complete = end_us + gate_duration_us
                if group.return_duration_us is None:
                    terminal_completion = max(terminal_completion, gate_complete)
                else:
                    delayed_returns[index] = gate_complete
            else:
                terminal_completion = max(terminal_completion, end_us)

        newly_ready = [
            index for index, release in delayed_returns.items() if release <= now
        ]
        for index in newly_ready:
            ready_returns.add(index)
            delayed_returns.pop(index)

        while free_aods and (unscheduled_forward or ready_returns):
            candidates = [
                *(('approach', index) for index in unscheduled_forward),
                *(('return', index) for index in ready_returns),
            ]
            phase, index = min(candidates, key=candidate_key)
            group = forward[index]
            duration = (
                group.forward_duration_us
                if phase == "approach"
                else float(group.return_duration_us)
            )
            aod = min(free_aods)
            free_aods.remove(aod)
            if phase == "approach":
                unscheduled_forward.remove(index)
            else:
                ready_returns.remove(index)
            end_us = now + duration
            running[aod] = (end_us, phase, index)
            tasks.append(
                AODTask(
                    id=f"{phase}:g{index}",
                    phase=phase,
                    group_index=index,
                    aod=aod,
                    start_us=now,
                    end_us=end_us,
                )
            )

        if not (unscheduled_forward or ready_returns or delayed_returns or running):
            break
        next_events = [value[0] for value in running.values()]
        if free_aods:
            next_events.extend(delayed_returns.values())
        future_events = [value for value in next_events if value >= now]
        if not future_events:
            raise RuntimeError("AOD scheduler reached a state with no future event")
        now = min(future_events)

    return max(now, terminal_completion), tuple(tasks)


def _barrier_schedule(
    groups: Sequence[RoundTripGroup],
    *,
    num_aod: int,
    gate_duration_us: float,
) -> tuple[float, tuple[AODTask, ...]]:
    """Build the conservative forward-barrier-return reference schedule."""

    tasks = []
    now = 0.0
    for start in range(0, len(groups), num_aod):
        batch = groups[start : start + num_aod]
        for aod, group in enumerate(batch):
            tasks.append(
                AODTask(
                    id=f"approach:g{group.index}",
                    phase="approach",
                    group_index=group.index,
                    aod=aod,
                    start_us=now,
                    end_us=now + group.forward_duration_us,
                )
            )
        now += max(group.forward_duration_us for group in batch)

    now += gate_duration_us
    return_groups = tuple(
        group for group in groups if group.return_duration_us is not None
    )
    for start in range(0, len(return_groups), num_aod):
        batch = return_groups[start : start + num_aod]
        for aod, group in enumerate(batch):
            tasks.append(
                AODTask(
                    id=f"return:g{group.index}",
                    phase="return",
                    group_index=group.index,
                    aod=aod,
                    start_us=now,
                    end_us=now + float(group.return_duration_us),
                )
            )
        now += max(float(group.return_duration_us) for group in batch)
    return now, tuple(tasks)


def schedule_round_trip_tasks(
    groups: Sequence[RoundTripGroup],
    *,
    num_aod: int,
    gate_duration_us: float = 0.0,
) -> AODPipelineSchedule:
    """Schedule forward/gate/return chains and retain the best list heuristic."""

    if num_aod < 1:
        raise ValueError("The number of AOD arrays must be positive")
    if gate_duration_us < 0 or not math.isfinite(gate_duration_us):
        raise ValueError("Gate duration must be finite and non-negative")
    indices = [group.index for group in groups]
    if len(set(indices)) != len(indices):
        raise ValueError("Round-trip movement group indices must be unique")
    if not groups:
        return AODPipelineSchedule(
            num_aod=num_aod,
            policy="empty",
            tasks=(),
            gate_duration_us=gate_duration_us,
            duration_us=0.0,
            forward_barrier_duration_us=0.0,
            round_trip_barrier_duration_us=0.0,
        )

    forward_durations = [group.forward_duration_us for group in groups]
    return_durations = [
        group.return_duration_us
        for group in groups
        if group.return_duration_us is not None
    ]
    forward_barrier = _barrier_duration(forward_durations, num_aod)
    round_trip_barrier = (
        forward_barrier
        + gate_duration_us
        + _barrier_duration(return_durations, num_aod)
    )

    candidates = []
    for priority, policy in enumerate((
        "forward_first",
        "return_first",
        "longest_remaining",
        "shortest_task",
    )):
        duration, tasks = _simulate_pipeline(
            groups,
            num_aod=num_aod,
            gate_duration_us=gate_duration_us,
            policy=policy,
        )
        candidates.append((duration, priority, policy, tasks))
    barrier_result, barrier_tasks = _barrier_schedule(
        groups,
        num_aod=num_aod,
        gate_duration_us=gate_duration_us,
    )
    candidates.append(
        (barrier_result, len(candidates), "barrier_fallback", barrier_tasks)
    )
    duration, _, policy, tasks = min(
        candidates,
        key=lambda value: (
            value[0],
            value[1],
            tuple(
                (task.start_us, task.aod, task.phase, task.group_index)
                for task in value[3]
            ),
        ),
    )
    return AODPipelineSchedule(
        num_aod=num_aod,
        policy=policy,
        tasks=tasks,
        gate_duration_us=gate_duration_us,
        duration_us=duration,
        forward_barrier_duration_us=forward_barrier,
        round_trip_barrier_duration_us=round_trip_barrier,
    )


def _stage_schedule(
    *,
    index: int,
    moves: Sequence[LayerMove],
    operation_indices: Iterable[int],
    magic_state_assignments: Sequence[Mapping[str, Any]],
    num_aod: int,
    timing: AODTimingModel,
) -> AODStageSchedule:
    collective_groups = group_collective_movements(moves, timing)
    return_by_forward = {
        move.id: move.reversed()
        for move in moves
        if move.entity_kind == "logical_qubit"
    }
    return_moves = tuple(return_by_forward.values())
    round_trip_groups = []
    for group_index, group in enumerate(collective_groups):
        reverse_group = tuple(
            return_by_forward[move.id]
            for move in group
            if move.id in return_by_forward
        )
        round_trip_groups.append(
            RoundTripGroup(
                index=group_index,
                forward_duration_us=timing.task_duration_us(group),
                return_duration_us=(
                    timing.task_duration_us(reverse_group) if reverse_group else None
                ),
            )
        )
    pipeline = schedule_round_trip_tasks(
        round_trip_groups,
        num_aod=num_aod,
        gate_duration_us=timing.gate_duration_us,
    )
    task_moves = {
        ("approach", group_index): tuple(move.id for move in group)
        for group_index, group in enumerate(collective_groups)
    }
    task_moves.update(
        {
            ("return", group_index): tuple(
                return_by_forward[move.id].id
                for move in group
                if move.id in return_by_forward
            )
            for group_index, group in enumerate(collective_groups)
        }
    )
    tasks = tuple(
        AODTask(
            id=task.id,
            phase=task.phase,
            group_index=task.group_index,
            aod=task.aod,
            start_us=task.start_us,
            end_us=task.end_us,
            movement_ids=task_moves[(task.phase, task.group_index)],
        )
        for task in pipeline.tasks
    )
    pipeline = AODPipelineSchedule(
        num_aod=pipeline.num_aod,
        policy=pipeline.policy,
        tasks=tasks,
        gate_duration_us=pipeline.gate_duration_us,
        duration_us=pipeline.duration_us,
        forward_barrier_duration_us=pipeline.forward_barrier_duration_us,
        round_trip_barrier_duration_us=pipeline.round_trip_barrier_duration_us,
    )
    return AODStageSchedule(
        index=index,
        operation_indices=tuple(sorted(set(operation_indices))),
        forward_moves=tuple(moves),
        return_moves=return_moves,
        collective_groups=tuple(
            tuple(move.id for move in group) for group in collective_groups
        ),
        pipeline=pipeline,
        magic_state_assignments=tuple(dict(value) for value in magic_state_assignments),
    )


def schedule_logical_layer(
    layer: LogicalLayer,
    home_positions: Mapping[int, Point],
    *,
    magic_state_positions: Sequence[tuple[str, Point]] = (),
    num_aod: int = 1,
    timing: AODTimingModel | None = None,
) -> LayerAODSchedule:
    """Route and timestamp one logical layer from home back to home."""

    timing = timing or AODTimingModel()
    if num_aod < 1:
        raise ValueError("The number of AOD arrays must be positive")
    positions = {
        int(qubit): tuple(float(value) for value in coordinate)
        for qubit, coordinate in home_positions.items()
    }
    interaction_moves = []
    interaction_operations = []
    occupied_in_layer: dict[int, int] = {}
    t_operations: list[tuple[int, int]] = []

    for operation_index, operation in enumerate(layer.operations):
        if any(qubit not in positions for qubit in operation.qubits):
            raise ValueError(
                f"Layer {layer.index} references a qubit absent from the home placement"
            )
        if operation.name.lower() in {"t", "tdg"} and operation.qubits:
            t_operations.append((operation_index, operation.qubits[0]))
        if len(operation.qubits) < 2:
            continue
        if len(operation.qubits) != 2:
            raise ValueError(
                "Layer-local AOD routing currently accepts two-qubit interactions only"
            )
        left, right = operation.qubits
        for qubit in (left, right):
            if qubit in occupied_in_layer:
                raise ValueError(
                    f"Logical layer {layer.index} contains overlapping interactions on q{qubit}"
                )
            occupied_in_layer[qubit] = operation_index
        interaction_operations.append(operation_index)
        interaction_moves.append(
            LayerMove(
                id=f"l{layer.index}:op{operation_index}:q{left}:approach",
                operation_index=operation_index,
                logical_qubit=left,
                source=positions[left],
                destination=positions[right],
            )
        )

    if any(target in occupied_in_layer for _, target in t_operations):
        raise ValueError(
            f"Logical layer {layer.index} acts on the same qubit in a T and two-qubit gate"
        )
    if t_operations and not magic_state_positions:
        raise ValueError("T-gate routing requires at least one magic-state buffer position")

    t_waves = [
        t_operations[start : start + len(magic_state_positions)]
        for start in range(0, len(t_operations), len(magic_state_positions))
    ] if t_operations else []
    stage_count = max(1 if interaction_moves else 0, len(t_waves))
    stages = []
    for stage_index in range(stage_count):
        stage_moves = list(interaction_moves if stage_index == 0 else ())
        operation_indices = list(interaction_operations if stage_index == 0 else ())
        magic_assignments = []
        wave = t_waves[stage_index] if stage_index < len(t_waves) else ()
        for assignment_index, (operation_index, target) in enumerate(wave):
            slot_id, source = magic_state_positions[assignment_index]
            move = LayerMove(
                id=f"l{layer.index}:op{operation_index}:{slot_id}:delivery",
                operation_index=operation_index,
                logical_qubit=target,
                source=source,
                destination=positions[target],
                entity_kind="magic_state",
                phase="delivery",
                source_slot_id=slot_id,
                target_logical_qubit=target,
            )
            stage_moves.append(move)
            operation_indices.append(operation_index)
            magic_assignments.append(
                {
                    "source_slot": slot_id,
                    "target_logical_qubit": target,
                }
            )
        stages.append(
            _stage_schedule(
                index=stage_index,
                moves=stage_moves,
                operation_indices=operation_indices,
                magic_state_assignments=magic_assignments,
                num_aod=num_aod,
                timing=timing,
            )
        )

    return LayerAODSchedule(
        layer_index=layer.index,
        num_aod=num_aod,
        timing_model=timing,
        stages=tuple(stages),
    )
