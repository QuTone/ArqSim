"""Event-driven co-execution of Program and Resource DAGs.

Continuous time advances between discrete state transitions.  At each event
time the engine applies every completion, repeatedly dispatches newly enabled
commands to a fixed point, and advances to the next completion.  Program nodes
never poll and WAIT/STOP are not instructions: a node simply remains blocked
until a relevant architectural-state transition enables it.
"""

from __future__ import annotations

import heapq
import math
import random
from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any, Mapping, Sequence

from heteqsys.architecture.isa import ArchitectureInstruction, ArchitectureOpcode
from heteqsys.architecture.recipes import InjectionRecipe, ProgramWorkLineage
from heteqsys.architecture.state import (
    ArchitectureState,
    StateReservation,
    StateSnapshot,
    TentativeBinding,
)
from .components import (
    BackendRequest,
    CandidateImplementation,
    CompletionRequest,
    ContinuationDecision,
    ContinuationRequest,
    EventOutcome,
    PreparedExecution,
    ProgramSchedulingRequest,
    RealizationRequest,
    ResourceSchedulingRequest,
    RuntimeComponentError,
    RuntimeComponentManifest,
    RuntimeComponentSet,
    RuntimeOperationView,
    build_runtime_component_set,
    validate_builtin_component_source_context,
    validate_exact_permutation,
)
from .plan import ExecutionPlan
from .resource_dag import ResourceProcess
from .result import (
    EvaluationResult,
    ExecutionPlane,
    ExecutionTrace,
    ExecutionTransition,
    ExecutionTransitionKind,
    ProgramContinuationReceipt,
    TraceStateProjection,
)
from heteqsys.schema import canonical_float_sum, normalize_json, semantic_hash


class EvaluationError(RuntimeError):
    pass


@dataclass
class _Running:
    event_id: int
    plane: ExecutionPlane
    opcode: ArchitectureOpcode
    start_s: float
    end_s: float
    instruction_id: int | None
    process_id: str | None
    instance: int | None
    reservation: StateReservation
    wait_reasons: tuple[str, ...]
    trace_metadata: dict[str, Any]
    discarded_outputs: dict[str, int]
    candidate_id: str
    operation: RuntimeOperationView
    backend_artifact: dict[str, Any]
    lineage: ProgramWorkLineage | None
    expected_measurements: tuple[str, ...]
    recipe: InjectionRecipe | None
    continuation: ContinuationDecision | None


@dataclass(frozen=True)
class _ProgramWork:
    """One static source or dynamically activated Program frontier item."""

    schedule_id: int
    lineage: ProgramWorkLineage
    operation: RuntimeOperationView
    recipe: InjectionRecipe | None = None
    continuation: ContinuationDecision | None = None


@dataclass(frozen=True)
class _DispatchCandidate:
    """One fully compiled, still-uncommitted Program or Resource event."""

    plane: ExecutionPlane
    opcode: ArchitectureOpcode
    program_work: _ProgramWork | None
    instruction_id: int | None
    process: ResourceProcess | None
    instance: int | None
    batch_amount: int
    duration_s: float
    binding: TentativeBinding
    wait_reasons: tuple[str, ...]
    trace_metadata: Mapping[str, Any]
    discarded_outputs: Mapping[str, int]
    candidate_id: str
    operation: RuntimeOperationView
    backend_artifact: Mapping[str, Any]
    lineage: ProgramWorkLineage | None
    expected_measurements: tuple[str, ...]


@dataclass(frozen=True)
class _OutputFit:
    """Typed output fitting receipt kept outside ResourceProcess metadata."""

    process: ResourceProcess
    attempted_outputs: Mapping[str, int] | None = None
    buffered_outputs: Mapping[str, int] | None = None
    discarded_outputs: Mapping[str, int] | None = None


def evaluate(
    plan: ExecutionPlan,
    *,
    runtime_components: RuntimeComponentSet | None = None,
) -> EvaluationResult:
    """Co-execute a finite Program DAG and streaming Resource DAG.

    The canonical initialization is cold start.  Resource processes follow the
    plan's greedy-fill policy and are instantiated lazily, so the simulator
    stores only the active Program frontier and in-flight resource work.
    """

    run_seed = plan.policy.seed
    try:
        planned_manifest = RuntimeComponentManifest.from_dict(
            normalize_json(plan.runtime_components)
        )
        components = runtime_components or build_runtime_component_set(
            planned_manifest,
            runtime_instruction_compiler=None,
            runtime_resource_compiler=None,
        )
        actual_manifest = components.manifest
    except RuntimeComponentError as exc:
        raise EvaluationError(str(exc)) from exc
    if planned_manifest.manifest_hash != actual_manifest.manifest_hash:
        raise EvaluationError(
            "Live runtime components do not match the ExecutionPlan manifest"
        )
    try:
        validate_builtin_component_source_context(
            components,
            circuit_hash=plan.circuit_hash,
            architecture_hash=plan.architecture_hash,
            compiler_spec_hash=plan.provenance.get("compiler_spec_hash"),
            latency_profile_hash=plan.latency_profile_hash,
        )
    except RuntimeComponentError as exc:
        raise EvaluationError(str(exc)) from exc

    rng = random.Random(run_seed)
    state = ArchitectureState.from_plan(plan)
    initial_trace_state = TraceStateProjection.from_snapshot(state.snapshot())

    commands = {item.id: item for item in plan.program_dag.instructions}
    processes = {item.id: item for item in plan.resource_dag.processes}
    # Program instructions and Resource-DAG process templates are immutable for
    # one evaluation.  RuntimeOperationView construction performs a strict,
    # deep JSON detach, so retain those detached views rather than rebuilding
    # them for every candidate and every ready-frontier projection.  Dynamically
    # fitted/batched ResourceProcess replacements still receive fresh views.
    command_views = {
        item.id: RuntimeOperationView.from_operation(
            item,
            plane=ExecutionPlane.PROGRAM.value,
        )
        for item in plan.program_dag.instructions
    }
    program_work: dict[int, _ProgramWork] = {
        item.id: _ProgramWork(
            schedule_id=item.id,
            lineage=ProgramWorkLineage(
                work_id=f"program:{item.id}",
                source_instruction_id=item.id,
            ),
            operation=command_views[item.id],
        )
        for item in plan.program_dag.instructions
    }
    next_program_schedule_id = max(program_work, default=-1) + 1
    process_views = {
        item.id: RuntimeOperationView.from_operation(
            item,
            plane=ExecutionPlane.RESOURCE.value,
        )
        for item in plan.resource_dag.processes
    }
    successors: defaultdict[int, list[int]] = defaultdict(list)
    remaining_predecessors = {
        item.id: len(item.predecessor_ids) for item in plan.program_dag.instructions
    }
    for item in plan.program_dag.instructions:
        for predecessor in item.predecessor_ids:
            successors[predecessor].append(item.id)
    program_ready = {
        command_id
        for command_id, remaining in remaining_predecessors.items()
        if remaining == 0
    }
    pending_program = set(commands)
    completed_program: set[int] = set()
    source_open_invocations: dict[int, set[str]] = {}
    completion_times: dict[int, float] = {}
    program_ready_times = {
        command_id: 0.0 for command_id in program_ready
    }
    wait_history: defaultdict[int, set[str]] = defaultdict(set)

    process_inflight = {process.id: 0 for process in plan.resource_dag.processes}
    process_instances = {process.id: 0 for process in plan.resource_dag.processes}
    process_items_started = {process.id: 0 for process in plan.resource_dag.processes}
    process_blocked_s = {process.id: 0.0 for process in plan.resource_dag.processes}

    running_heap: list[tuple[float, int]] = []
    running: dict[int, _Running] = {}
    execution_transitions: list[ExecutionTransition] = []
    dispatch_transition_by_event: dict[int, ExecutionTransition] = {}
    next_event_id = 0
    now = 0.0
    event_transitions = 0
    buffer_peaks = state.occupancy()
    exposed_program_state_blocked_s = 0.0

    produced_counts: defaultdict[str, int] = defaultdict(int)
    discarded_counts: defaultdict[str, int] = defaultdict(int)
    consumed_counts: defaultdict[str, int] = defaultdict(int)
    opcode_time_s: defaultdict[str, float] = defaultdict(float)
    process_work_s: defaultdict[str, float] = defaultdict(float)
    plane_work_s: defaultdict[str, float] = defaultdict(float)
    idle_exposure_by_location_s: defaultdict[str, float] = defaultdict(float)
    idle_exposure_by_qubit_s: defaultdict[str, float] = defaultdict(float)
    idle_exposure_by_qubit_location_s: defaultdict[
        str, defaultdict[str, float]
    ] = defaultdict(lambda: defaultdict(float))
    active_exposure_by_opcode_s: defaultdict[str, float] = defaultdict(float)
    active_exposure_by_qubit_s: defaultdict[str, float] = defaultdict(float)
    qubit_exposure_conflict = False
    capture_discrete_log = plan.policy.trace_level == "full"
    discrete_time_log: list[dict[str, Any]] = []
    timestamp_dispatched: list[dict[str, Any]] = []
    timestamp_completed: list[dict[str, Any]] = []
    timestamp_program_ready_now: list[int] = sorted(program_ready)

    def ensure_transition_budget(required: int = 1) -> None:
        if event_transitions + required > plan.policy.max_events:
            raise EvaluationError(
                f"Evaluation exceeded max_events={plan.policy.max_events}"
            )

    def note_event_transition() -> None:
        nonlocal event_transitions
        event_transitions += 1

    def trace_state_snapshot() -> dict[str, Any]:
        return {
            "version": state.version,
            "buffers": {
                name: {
                    "ready": len(buffer.ready_tokens),
                    "pending_incoming": buffer.pending_outputs,
                }
                for name, buffer in sorted(state.buffers.items())
            },
            "engines": {
                name: {
                    "used": engine.users,
                    "capacity": engine.capacity,
                }
                for name, engine in sorted(state.engines.items())
            },
            "locations": dict(sorted(state.locations.items())),
        }

    def state_delta(
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> dict[str, Any]:
        buffers = {}
        for name in sorted(set(before["buffers"]) | set(after["buffers"])):
            left = before["buffers"].get(
                name, {"ready": 0, "pending_incoming": 0}
            )
            right = after["buffers"].get(
                name, {"ready": 0, "pending_incoming": 0}
            )
            if left != right:
                buffers[name] = {
                    "ready": right["ready"],
                    "ready_delta": right["ready"] - left["ready"],
                    "pending_incoming": right["pending_incoming"],
                    "pending_delta": (
                        right["pending_incoming"] - left["pending_incoming"]
                    ),
                }
        engines = {}
        for name in sorted(set(before["engines"]) | set(after["engines"])):
            left = before["engines"].get(name, {"used": 0, "capacity": 0})
            right = after["engines"].get(name, {"used": 0, "capacity": 0})
            if left != right:
                engines[name] = {
                    "used": right["used"],
                    "used_delta": right["used"] - left["used"],
                    "capacity": right["capacity"],
                }
        locations = {}
        before_locations = before["locations"]
        after_locations = after["locations"]
        for item in sorted(set(before_locations) | set(after_locations)):
            source = before_locations.get(item)
            destination = after_locations.get(item)
            if source != destination:
                locations[item] = {"from": source, "to": destination}
        result: dict[str, Any] = {}
        if buffers:
            result["buffers"] = buffers
        if engines:
            result["engines"] = engines
        if locations:
            result["locations"] = locations
        return result

    def running_frontier() -> list[dict[str, Any]]:
        return [
            {
                "event_id": item.event_id,
                "plane": item.plane.value,
                "opcode": item.opcode.value,
                "instruction_id": item.instruction_id,
                "process_id": item.process_id,
                "instance": item.instance,
                "reservation_id": item.reservation.reservation_id,
                "candidate_id": item.candidate_id,
                "started_s": item.start_s,
                "completes_s": item.end_s,
            }
            for item in sorted(
                running.values(),
                key=lambda candidate: (
                    candidate.end_s,
                    candidate.event_id,
                ),
            )
        ]

    def blockers_for_program_work(
        work: _ProgramWork,
        snapshot: StateSnapshot | None = None,
    ) -> tuple[str, ...]:
        view = snapshot or state.snapshot()
        return tuple(
            str(reason)
            for reason in view.check_start(
                work.operation,
                required_locations=work.operation.required_locations,
            )
        )

    def fit_process_outputs_to_buffers(
        process: ResourceProcess,
        snapshot: StateSnapshot | None = None,
    ) -> _OutputFit | None:
        """Reserve only outputs that fit when a protocol permits overflow loss.

        A protocol batch is still executed in full.  The destination buffers
        accept as many outputs as their currently unreserved slots permit;
        the remainder are discarded.  A completely full destination stops
        production instead of repeatedly executing batches whose every output
        would be wasted.
        """

        view = snapshot or state.snapshot()
        if process.output_overflow_policy != "discard_excess":
            return _OutputFit(process)
        accepted: dict[str, int] = {}
        discarded: dict[str, int] = {}
        for name, raw_amount in process.produces.items():
            amount = int(raw_amount)
            buffer = view.buffers[name]
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
        if process.produces and not accepted:
            return None
        return _OutputFit(
            replace(process, produces=accepted),
            attempted_outputs=dict(process.produces),
            buffered_outputs=accepted,
            discarded_outputs=discarded,
        )

    def blockers_for_process(
        process: ResourceProcess,
        snapshot: StateSnapshot | None = None,
    ) -> tuple[str, ...]:
        view = snapshot or state.snapshot()
        if process_inflight[process.id] >= process.parallelism:
            return (f"process_busy:{process.id}",)
        fitted = fit_process_outputs_to_buffers(process, view)
        if fitted is None:
            return tuple(
                f"buffer_full:{name}:no free output slot"
                for name in sorted(process.produces)
            )
        return tuple(str(reason) for reason in view.check_start(fitted.process))

    def duration_for_process(process: ResourceProcess, instance: int) -> float:
        if process.arrival_distribution is None:
            return process.duration_s
        duration, _ = process.arrival_distribution.sample_interval(
            rng,
            instance,
        )
        return duration + process.duration_s

    def resolve_candidate(
        *,
        snapshot: StateSnapshot,
        plane: ExecutionPlane,
        opcode: ArchitectureOpcode,
        program_item: _ProgramWork | None,
        instruction_id: int | None,
        process: ResourceProcess | None,
        instance: int | None,
        duration_s: float,
        completion_locations: Mapping[str, str],
        wait_reasons: tuple[str, ...],
        engine_receipt: Mapping[str, Any],
        discarded_outputs: Mapping[str, int] | None = None,
        batch_amount: int = 1,
    ) -> _DispatchCandidate:
        if program_item is not None:
            operation_view = program_item.operation
        elif process is not None and process is processes.get(process.id):
            operation_view = process_views[process.id]
        else:
            operation_view = RuntimeOperationView.from_operation(
                process,
                plane=plane.value,
            )
        if completion_locations != operation_view.completion_locations:
            raise EvaluationError(
                "Engine completion-location context does not match operation"
            )
        candidate_id = (
            program_item.lineage.work_id
            if program_item is not None
            else f"resource:{process.id}:{instance}"
        )
        try:
            implementation = components.runtime_realizer.realize(
                RealizationRequest(
                    snapshot=snapshot,
                    candidate_id=candidate_id,
                    operation=operation_view,
                    base_duration_s=duration_s,
                    now_s=now,
                    instance=instance,
                    batch_amount=batch_amount,
                    ready_program=tuple(
                        program_work[ready_id].operation
                        for ready_id in sorted(program_ready)
                    ),
                    lineage=(program_item.lineage if program_item is not None else None),
                )
            )
            if not isinstance(implementation, CandidateImplementation):
                raise RuntimeComponentError(
                    "RuntimeRealizer must return CandidateImplementation"
                )
            if (
                implementation.candidate_id != candidate_id
                or implementation.operation != operation_view
            ):
                raise RuntimeComponentError(
                    "RuntimeRealizer cannot rewrite candidate identity or operation"
                )
            prepared = components.execution_backend.prepare(
                BackendRequest(candidate=implementation, now_s=now)
            )
            if not isinstance(prepared, PreparedExecution):
                raise RuntimeComponentError(
                    "Execution backend must return PreparedExecution"
                )
        except RuntimeComponentError as exc:
            raise EvaluationError(str(exc)) from exc

        receipt_overlap = sorted(
            set(operation_view.metadata) & set(engine_receipt)
        )
        if receipt_overlap:
            raise EvaluationError(
                "Operation metadata cannot restate Engine receipt fields: "
                f"{receipt_overlap}"
            )
        trace_metadata = {
            **dict(operation_view.metadata),
            **dict(engine_receipt),
            **dict(implementation.compiler_artifact),
        }
        effective_discarded_outputs = (
            {} if discarded_outputs is None else discarded_outputs
        )
        if not isinstance(effective_discarded_outputs, Mapping) or any(
            type(name) is not str
            or isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount < 0
            for name, amount in effective_discarded_outputs.items()
        ):
            raise EvaluationError(
                "Discarded outputs must map buffer ids to non-negative integers"
            )

        effective_duration_s = prepared.duration_s
        resolved_end_s = now + effective_duration_s
        if not math.isfinite(resolved_end_s) or (
            effective_duration_s > 0 and resolved_end_s <= now
        ):
            raise EvaluationError(
                "Resolved event end time must be finite and advance the clock"
            )

        return _DispatchCandidate(
            plane=plane,
            opcode=opcode,
            program_work=program_item,
            instruction_id=instruction_id,
            process=process,
            instance=instance,
            batch_amount=batch_amount,
            duration_s=effective_duration_s,
            binding=implementation.binding,
            wait_reasons=wait_reasons,
            trace_metadata=trace_metadata,
            discarded_outputs=dict(effective_discarded_outputs),
            candidate_id=candidate_id,
            operation=operation_view,
            backend_artifact=prepared.artifact,
            lineage=implementation.lineage,
            expected_measurements=implementation.expected_measurements,
        )

    def commit_candidate(candidate: _DispatchCandidate) -> None:
        nonlocal next_event_id
        if candidate.program_work is not None:
            schedule_id = candidate.program_work.schedule_id
            source_id = candidate.program_work.lineage.source_instruction_id
            if schedule_id not in program_ready or (
                candidate.program_work.lineage.step == "source"
                and source_id not in pending_program
            ):
                raise EvaluationError(
                    f"Program work {candidate.candidate_id} left the ready frontier before commit"
                )
        if candidate.process is not None:
            process_id = candidate.process.id
            if candidate.instance != process_instances[process_id]:
                raise EvaluationError(
                    f"Resource process {process_id} instance changed before commit"
                )
            if process_inflight[process_id] >= candidate.process.parallelism:
                raise EvaluationError(
                    f"Resource process {process_id} exceeded parallelism before commit"
                )

        # A zero-duration dispatch completes synchronously and therefore needs
        # two admitted transitions.  Check before any state/frontier mutation.
        ensure_transition_budget(2 if candidate.duration_s <= 0 else 1)

        operation = (
            candidate.program_work.operation
            if candidate.program_work is not None
            else candidate.process
        )
        assert operation is not None
        reservation = state.commit(candidate.binding, operation)
        for name, tokens in reservation.consumed_tokens.items():
            consumed_counts[name] += len(tokens)

        if candidate.program_work is not None:
            program_ready.remove(candidate.program_work.schedule_id)
        if candidate.process is not None:
            process_id = candidate.process.id
            process_inflight[process_id] += 1
            process_instances[process_id] += 1
            process_items_started[process_id] += candidate.batch_amount

        event_id = next_event_id
        next_event_id += 1
        end_s = now + candidate.duration_s
        item = _Running(
            event_id=event_id,
            plane=candidate.plane,
            opcode=candidate.opcode,
            start_s=now,
            end_s=end_s,
            instruction_id=candidate.instruction_id,
            process_id=(candidate.process.id if candidate.process else None),
            instance=candidate.instance,
            reservation=reservation,
            wait_reasons=candidate.wait_reasons,
            trace_metadata=dict(candidate.trace_metadata),
            discarded_outputs=dict(candidate.discarded_outputs),
            candidate_id=candidate.candidate_id,
            operation=candidate.operation,
            backend_artifact=dict(candidate.backend_artifact),
            lineage=candidate.lineage,
            expected_measurements=candidate.expected_measurements,
            recipe=(candidate.program_work.recipe if candidate.program_work else None),
            continuation=(
                candidate.program_work.continuation
                if candidate.program_work
                else None
            ),
        )
        dispatch_transition = ExecutionTransition(
            transition_id=len(execution_transitions),
            kind=ExecutionTransitionKind.DISPATCH,
            time_s=now,
            event_id=event_id,
            reservation_id=reservation.reservation_id,
            state_version_before=reservation.binding_version,
            state_version_after=reservation.committed_version,
            plane=candidate.plane,
            opcode=candidate.opcode,
            instruction_id=candidate.instruction_id,
            process_id=(candidate.process.id if candidate.process else None),
            instance=candidate.instance,
            candidate_id=candidate.candidate_id,
            start_s=now,
            end_s=end_s,
            wait_reasons=candidate.wait_reasons,
            consumes=reservation.consumes,
            consumed_tokens=reservation.consumed_tokens,
            consumed_slots=reservation.consumed_slots,
            produces=reservation.produces,
            produced_slots=reservation.produced_slots,
            forwards=reservation.forwards,
            engines=reservation.engines,
            required_locations=candidate.binding.required_locations,
            completion_locations=reservation.completion_locations,
            buffer_occupancy_after=state.occupancy(),
            pending_incoming_after=state.pending_outputs(),
            metadata=candidate.trace_metadata,
            backend_artifact=candidate.backend_artifact,
            program_lineage=candidate.lineage,
        )
        execution_transitions.append(dispatch_transition)
        dispatch_transition_by_event[event_id] = dispatch_transition
        if capture_discrete_log:
            timestamp_dispatched.append(
                {
                    "event_id": event_id,
                    "plane": candidate.plane.value,
                    "opcode": candidate.opcode.value,
                    "instruction_id": candidate.instruction_id,
                    "process_id": (
                        candidate.process.id if candidate.process else None
                    ),
                    "instance": candidate.instance,
                    "start_s": now,
                    "end_s": end_s,
                    "program_ready": (
                        True if candidate.program_work is not None else None
                    ),
                    "resource_ready": True,
                    "reservation": {
                        "reservation_id": reservation.reservation_id,
                        "state_version_before": reservation.binding_version,
                        "state_version_after": reservation.committed_version,
                        "consumed_tokens": reservation.consumed_tokens,
                        "consumed_slots": reservation.consumed_slots,
                        "produced_slots": reservation.produced_slots,
                        "engines": reservation.engines,
                    },
                    "metadata": dict(candidate.trace_metadata),
                    "candidate_id": candidate.candidate_id,
                    "backend_artifact": dict(candidate.backend_artifact),
                }
            )
        running[event_id] = item
        if candidate.duration_s <= 0:
            complete(event_id)
        else:
            heapq.heappush(running_heap, (end_s, event_id))
        note_event_transition()

    def install_program_work(
        *,
        source_instruction_id: int,
        recipe: InjectionRecipe,
        stage_index: int,
        step: str,
        parent_event_id: int,
        continuation: ContinuationDecision | None = None,
    ) -> _ProgramWork:
        """Activate one deterministic continuation node on the live frontier."""

        nonlocal next_program_schedule_id
        work_id = (
            f"program:{source_instruction_id}:recipe:{recipe.invocation_id}:"
            f"stage:{stage_index}:{step}"
        )
        if any(item.lineage.work_id == work_id for item in program_work.values()):
            raise EvaluationError(f"Program continuation work {work_id!r} already exists")
        if step == "reaction":
            opcode = ArchitectureOpcode.CLASSICAL_REACTION
            duration_s = recipe.reaction_duration_s
            consumes: Mapping[str, int] = {}
            engines: Mapping[str, int] = {}
            qubits: tuple[int, ...] = ()
            implementation_recipes: tuple[InjectionRecipe, ...] = ()
            metadata: Mapping[str, Any] = {}
        elif step == "injection":
            stage = recipe.stages[stage_index]
            opcode = ArchitectureOpcode.EXECUTE_COMPUTE
            duration_s = stage.attempt_duration_s
            consumes = {stage.resource.buffer_id: stage.resource.quantity}
            engines = {recipe.compute_engine: 1}
            qubits = recipe.qubits
            implementation_recipes = (recipe,)
            metadata = {}
        elif step == "correction":
            if (
                continuation is None
                or continuation.kind != "materialized_logical_correction"
                or continuation.correction is None
            ):
                raise EvaluationError(
                    "Materialized logical correction work requires its typed gate decision"
                )
            opcode = ArchitectureOpcode.EXECUTE_COMPUTE
            duration_s = recipe.correction_duration_s
            consumes = {}
            engines = {recipe.compute_engine: 1}
            qubits = recipe.qubits
            implementation_recipes = ()
            metadata = {
                "gates": {
                    continuation.correction: list(recipe.qubits),
                }
            }
        else:  # pragma: no cover - private closed construction.
            raise EvaluationError(f"Unsupported Program continuation step: {step}")
        lineage = ProgramWorkLineage(
            work_id=work_id,
            source_instruction_id=source_instruction_id,
            parent_event_id=parent_event_id,
            recipe_invocation_id=recipe.invocation_id,
            recipe_id=recipe.recipe_id,
            stage_index=stage_index,
            step=step,
        )
        operation = RuntimeOperationView(
            id=work_id,
            plane=ExecutionPlane.PROGRAM.value,
            opcode=opcode,
            duration_s=duration_s,
            layer_index=recipe.source_layer_index,
            qubits=qubits,
            consumes=consumes,
            engines=engines,
            required_locations={
                f"q:{qubit}": recipe.compute_location for qubit in qubits
            },
            target_modules=(recipe.compute_location,),
            metadata=metadata,
            implementation_recipes=implementation_recipes,
        )
        schedule_id = next_program_schedule_id
        next_program_schedule_id += 1
        work = _ProgramWork(
            schedule_id=schedule_id,
            lineage=lineage,
            operation=operation,
            recipe=recipe,
            continuation=continuation,
        )
        program_work[schedule_id] = work
        program_ready.add(schedule_id)
        program_ready_times[schedule_id] = now
        if capture_discrete_log:
            timestamp_program_ready_now.append(schedule_id)
        return work

    def complete_source_instruction(source_id: int) -> None:
        if source_id in completed_program:
            raise EvaluationError(f"Program source {source_id} completed twice")
        if source_open_invocations.get(source_id):
            raise EvaluationError(
                f"Program source {source_id} still has open recipe invocations"
            )
        source_open_invocations.pop(source_id, None)
        pending_program.remove(source_id)
        completed_program.add(source_id)
        completion_times[source_id] = now
        for successor in successors[source_id]:
            remaining_predecessors[successor] -= 1
            if remaining_predecessors[successor] == 0:
                program_ready.add(successor)
                program_ready_times[successor] = now
                if capture_discrete_log:
                    timestamp_program_ready_now.append(successor)

    def finish_recipe_invocation(source_id: int, invocation_id: str) -> bool:
        open_invocations = source_open_invocations.get(source_id)
        if open_invocations is None or invocation_id not in open_invocations:
            raise EvaluationError(
                f"Recipe invocation {invocation_id!r} is not open for source {source_id}"
            )
        open_invocations.remove(invocation_id)
        if open_invocations:
            return False
        complete_source_instruction(source_id)
        return True

    def advance_program_continuation(
        item: _Running,
        outcome: EventOutcome,
    ) -> ProgramContinuationReceipt:
        lineage = item.lineage
        if lineage is None:
            raise EvaluationError("Program work completed without typed lineage")
        source_id = lineage.source_instruction_id
        activated: list[str] = []

        def validate_decision(
            recipe: InjectionRecipe,
            stage_index: int,
            outcome_bit: int,
            decision: ContinuationDecision,
        ) -> None:
            stage = recipe.stages[stage_index]
            if outcome_bit == 0:
                valid = decision.kind == "complete"
            elif stage.failure_next_stage is not None:
                valid = (
                    decision.kind == "next_stage"
                    and decision.next_stage == stage.failure_next_stage
                )
            else:
                valid = (
                    decision.kind == "materialized_logical_correction"
                    and decision.correction == stage.failure_correction
                )
            if not valid:
                raise EvaluationError(
                    "RuntimeRealizer continuation decision disagrees with the "
                    "frozen InjectionRecipe"
                )
        if lineage.step == "source":
            recipes = item.operation.implementation_recipes
            if not recipes:
                complete_source_instruction(source_id)
                return ProgramContinuationReceipt("complete_source")
            invocation_ids = {recipe.invocation_id for recipe in recipes}
            if source_id in source_open_invocations:
                raise EvaluationError(f"Program source {source_id} opened recipes twice")
            source_open_invocations[source_id] = set(invocation_ids)
            for recipe in recipes:
                register_id = recipe.measurement_register(0)
                outcome_bit = outcome.bit_for(register_id)
                decision = components.runtime_realizer.continue_after(
                    ContinuationRequest(
                        lineage=lineage,
                        recipe=recipe,
                        stage_index=0,
                        outcome_bit=outcome_bit,
                    )
                )
                validate_decision(recipe, 0, outcome_bit, decision)
                child = install_program_work(
                    source_instruction_id=source_id,
                    recipe=recipe,
                    stage_index=0,
                    step="reaction",
                    parent_event_id=item.event_id,
                    continuation=decision,
                )
                activated.append(child.lineage.work_id)
            return ProgramContinuationReceipt("activate", tuple(activated))

        recipe = item.recipe
        if recipe is None or lineage.stage_index is None:
            raise EvaluationError("Continuation work lost its recipe authority")
        if lineage.step == "injection":
            register_id = recipe.measurement_register(lineage.stage_index)
            outcome_bit = outcome.bit_for(register_id)
            decision = components.runtime_realizer.continue_after(
                ContinuationRequest(
                    lineage=lineage,
                    recipe=recipe,
                    stage_index=lineage.stage_index,
                    outcome_bit=outcome_bit,
                )
            )
            validate_decision(recipe, lineage.stage_index, outcome_bit, decision)
            child = install_program_work(
                source_instruction_id=source_id,
                recipe=recipe,
                stage_index=lineage.stage_index,
                step="reaction",
                parent_event_id=item.event_id,
                continuation=decision,
            )
            return ProgramContinuationReceipt(
                "activate", (child.lineage.work_id,)
            )
        if lineage.step == "reaction":
            decision = item.continuation
            if decision is None:
                raise EvaluationError("Reaction work lost its typed continuation")
            if decision.kind == "complete":
                closed = finish_recipe_invocation(
                    source_id, recipe.invocation_id
                )
                return ProgramContinuationReceipt(
                    "complete_source" if closed else "activate"
                )
            if decision.kind == "next_stage":
                assert decision.next_stage is not None
                child = install_program_work(
                    source_instruction_id=source_id,
                    recipe=recipe,
                    stage_index=decision.next_stage,
                    step="injection",
                    parent_event_id=item.event_id,
                )
            else:
                child = install_program_work(
                    source_instruction_id=source_id,
                    recipe=recipe,
                    stage_index=lineage.stage_index,
                    step="correction",
                    parent_event_id=item.event_id,
                    continuation=decision,
                )
            return ProgramContinuationReceipt(
                "activate", (child.lineage.work_id,)
            )
        if lineage.step == "correction":
            closed = finish_recipe_invocation(source_id, recipe.invocation_id)
            return ProgramContinuationReceipt(
                "complete_source" if closed else "activate"
            )
        raise EvaluationError(f"Unsupported Program lineage step: {lineage.step!r}")

    def complete(event_id: int) -> None:
        ensure_transition_budget()
        item = running[event_id]
        completion_snapshot = state.snapshot()
        try:
            outcome = components.outcome_model.resolve(
                CompletionRequest(
                    snapshot=completion_snapshot,
                    operation=item.operation,
                    reservation=item.reservation,
                    candidate_id=item.candidate_id,
                    event_id=item.event_id,
                    start_s=item.start_s,
                    end_s=item.end_s,
                    backend_artifact=item.backend_artifact,
                    outcome_seed=int(
                        semantic_hash(
                            {
                                "run_seed": run_seed,
                                "work_identity": (
                                    item.lineage.work_id
                                    if item.lineage is not None
                                    else item.candidate_id
                                ),
                                "outcome_component": actual_manifest.components[
                                    "outcome_model"
                                ].component_hash,
                            }
                        )[:16],
                        16,
                    ),
                    lineage=item.lineage,
                    expected_measurements=item.expected_measurements,
                )
            )
            if not isinstance(outcome, EventOutcome):
                raise RuntimeComponentError(
                    "OutcomeModel must return EventOutcome"
                )
        except RuntimeComponentError as exc:
            raise EvaluationError(str(exc)) from exc
        actual_measurements = tuple(
            measurement.register_id for measurement in outcome.measurements
        )
        if actual_measurements != item.expected_measurements:
            raise EvaluationError(
                "OutcomeModel measurements do not exactly match the realized "
                f"candidate contract: expected={item.expected_measurements}, "
                f"actual={actual_measurements}"
            )
        delta = state.propose_completion(
            item.reservation,
            snapshot=completion_snapshot,
            outcome=outcome.to_state_payload(),
        )
        state.apply_completion(delta)
        continuation_receipt = (
            advance_program_continuation(item, outcome)
            if item.plane == ExecutionPlane.PROGRAM
            else None
        )
        execution_transitions.append(
            ExecutionTransition(
                transition_id=len(execution_transitions),
                kind=ExecutionTransitionKind.COMPLETION,
                time_s=now,
                event_id=item.event_id,
                reservation_id=item.reservation.reservation_id,
                state_version_before=delta.state_version,
                state_version_after=state.version,
                plane=item.plane,
                opcode=item.opcode,
                instruction_id=item.instruction_id,
                process_id=item.process_id,
                instance=item.instance,
                candidate_id=item.candidate_id,
                start_s=item.start_s,
                end_s=item.end_s,
                wait_reasons=item.wait_reasons,
                consumes=item.reservation.consumes,
                consumed_tokens=item.reservation.consumed_tokens,
                consumed_slots=item.reservation.consumed_slots,
                produces=item.reservation.produces,
                produced_slots=item.reservation.produced_slots,
                produced_tokens=delta.produced_tokens,
                forwards=item.reservation.forwards,
                engines=delta.released_engines,
                required_locations=item.operation.required_locations,
                completion_locations=delta.completion_locations,
                token_sequence_after=delta.token_sequence_after,
                buffer_occupancy_after=delta.buffer_occupancy,
                pending_incoming_after=delta.pending_outputs,
                metadata=item.trace_metadata,
                backend_artifact=item.backend_artifact,
                outcome=delta.outcome,
                program_lineage=item.lineage,
                measurements={
                    measurement.register_id: measurement.bit
                    for measurement in outcome.measurements
                },
                continuation=continuation_receipt,
            )
        )
        running.pop(event_id)
        for name, tokens in delta.produced_tokens.items():
            produced_counts[name] += len(tokens)
            buffer_peaks[name] = max(buffer_peaks[name], delta.buffer_occupancy[name])
        for name, amount in item.discarded_outputs.items():
            discarded_counts[str(name)] += int(amount)

        if item.process_id is not None:
            process_inflight[item.process_id] -= 1
        duration = item.end_s - item.start_s
        opcode_time_s[item.opcode.value] += duration
        if item.process_id is not None:
            process_work_s[item.process_id] += duration
        plane_work_s[item.plane.value] += duration
        if capture_discrete_log:
            timestamp_completed.append(
                {
                    "event_id": item.event_id,
                    "plane": item.plane.value,
                    "opcode": item.opcode.value,
                    "instruction_id": item.instruction_id,
                    "process_id": item.process_id,
                    "instance": item.instance,
                    "start_s": item.start_s,
                    "end_s": item.end_s,
                    "produced_tokens": delta.produced_tokens,
                    "reservation_id": item.reservation.reservation_id,
                    "state_version_before": delta.state_version,
                    "state_version_after": state.version,
                    "candidate_id": item.candidate_id,
                    "backend_artifact": item.backend_artifact,
                    "outcome": delta.outcome,
                }
            )
        note_event_transition()

    def ordered_program_ready(
        snapshot: StateSnapshot | None = None,
    ) -> tuple[int, ...]:
        available = tuple(sorted(program_ready))
        try:
            selected = components.scheduler.order_program(
                ProgramSchedulingRequest(
                    ready_ids=available,
                    snapshot=snapshot or state.snapshot(),
                    now_s=now,
                )
            )
            return validate_exact_permutation(
                selected,
                available,
                owner="RuntimeScheduler.order_program",
            )
        except RuntimeComponentError as exc:
            raise EvaluationError(str(exc)) from exc

    def ordered_resource_processes(
        process_ids: Sequence[str],
        snapshot: StateSnapshot | None = None,
    ) -> tuple[str, ...]:
        available = tuple(process_ids)
        try:
            selected = components.scheduler.order_resources(
                ResourceSchedulingRequest(
                    process_ids=available,
                    snapshot=snapshot or state.snapshot(),
                    now_s=now,
                )
            )
            return validate_exact_permutation(
                selected,
                available,
                owner="RuntimeScheduler.order_resources",
            )
        except RuntimeComponentError as exc:
            raise EvaluationError(str(exc)) from exc

    def dispatch_program() -> bool:
        changed = False
        while True:
            changed_this_pass = False
            for schedule_id in ordered_program_ready():
                work = program_work[schedule_id]
                command = work.operation
                snapshot = state.snapshot()
                reasons = blockers_for_program_work(work, snapshot)
                if reasons:
                    wait_history[schedule_id].update(reasons)
                    continue
                candidate = resolve_candidate(
                    snapshot=snapshot,
                    plane=ExecutionPlane.PROGRAM,
                    opcode=command.opcode,
                    program_item=work,
                    instruction_id=work.lineage.source_instruction_id,
                    process=None,
                    instance=None,
                    duration_s=command.duration_s,
                    completion_locations=command.completion_locations,
                    wait_reasons=tuple(sorted(wait_history[schedule_id])),
                    engine_receipt={
                        "layer": command.layer_index,
                        "qubits": list(command.qubits),
                        "target_modules": list(command.target_modules),
                        "target_links": list(command.target_links),
                        "engines": dict(command.engines),
                        "program_ready_s": program_ready_times.get(schedule_id, 0.0),
                        "resource_wait_s": now
                        - program_ready_times.get(schedule_id, 0.0),
                    },
                )
                commit_candidate(candidate)
                changed = True
                changed_this_pass = True
                if len(completed_program) == len(commands):
                    return changed
            if not changed_this_pass:
                return changed

    def dispatch_resources() -> bool:
        changed = False
        for process_id in ordered_resource_processes(tuple(processes)):
            process = processes[process_id]
            while process_inflight[process.id] < process.parallelism:
                snapshot = state.snapshot()
                reasons = blockers_for_process(process, snapshot)
                if reasons:
                    break
                fitted = fit_process_outputs_to_buffers(process, snapshot)
                if fitted is None:
                    break
                batch_amount = (
                    snapshot.eager_batch_size(process)
                    if process.dispatch_policy == "eager_available"
                    else 1
                )
                if batch_amount <= 0:
                    break
                dispatched = fitted.process
                attempted_outputs = fitted.attempted_outputs
                buffered_outputs = fitted.buffered_outputs
                discarded_outputs = fitted.discarded_outputs
                if batch_amount > 1:
                    dispatched = replace(
                        dispatched,
                        consumes={
                            name: int(amount) * batch_amount
                            for name, amount in dispatched.consumes.items()
                        },
                        produces={
                            name: int(amount) * batch_amount
                            for name, amount in dispatched.produces.items()
                        },
                    )
                    attempted_outputs = (
                        {
                            name: amount * batch_amount
                            for name, amount in attempted_outputs.items()
                        }
                        if attempted_outputs is not None
                        else None
                    )
                    buffered_outputs = (
                        {
                            name: amount * batch_amount
                            for name, amount in buffered_outputs.items()
                        }
                        if buffered_outputs is not None
                        else None
                    )
                    discarded_outputs = (
                        {
                            name: amount * batch_amount
                            for name, amount in discarded_outputs.items()
                        }
                        if discarded_outputs is not None
                        else None
                    )
                instance = process_instances[process.id]
                rng_state = rng.getstate()
                duration_s = duration_for_process(process, instance)
                try:
                    candidate = resolve_candidate(
                        snapshot=snapshot,
                        plane=ExecutionPlane.RESOURCE,
                        opcode=dispatched.opcode,
                        program_item=None,
                        instruction_id=None,
                        process=dispatched,
                        instance=instance,
                        duration_s=duration_s,
                        completion_locations={},
                        wait_reasons=(),
                        batch_amount=batch_amount,
                        engine_receipt={
                            "batch_amount": batch_amount,
                            "dispatch_policy": process.dispatch_policy,
                            "protocol": dispatched.protocol,
                            "target_modules": list(dispatched.target_modules),
                            "target_links": list(dispatched.target_links),
                            "engines": dict(dispatched.engines),
                            **(
                                {
                                    "attempted_outputs": attempted_outputs,
                                    "buffered_outputs": buffered_outputs,
                                    "discarded_outputs": discarded_outputs,
                                }
                                if attempted_outputs is not None
                                else {}
                            ),
                        },
                        discarded_outputs=discarded_outputs or {},
                    )
                    commit_candidate(candidate)
                except Exception:
                    # Sampling is part of tentative realization.  A rejected
                    # pre-commit candidate must not perturb later samples.
                    if state.version == snapshot.version:
                        rng.setstate(rng_state)
                    raise
                changed = True
                # A zero-output process would fire forever under greedy fill.
                if not process.produces:
                    break
        return changed

    def has_exposed_program_state_block() -> bool:
        program_running = any(
            item.plane == ExecutionPlane.PROGRAM for item in running.values()
        )
        if program_running:
            return False
        return any(
            blockers_for_program_work(program_work[item]) for item in program_ready
        )

    def accumulate_process_blocking(delta: float) -> None:
        if delta <= 0:
            return
        for process in plan.resource_dag.processes:
            if process_inflight[process.id] >= process.parallelism:
                continue
            reasons = blockers_for_process(process)
            if any(reason.startswith("buffer_full:") for reason in reasons):
                process_blocked_s[process.id] += delta

    def accumulate_qubit_exposure(delta: float) -> None:
        """Classify every logical-qubit second as active or idle.

        Locations and the running set are constant between event completions.
        Resource-plane work does not make a program qubit active.  Qubits named
        by a running Program command are charged to that opcode; every other
        logical qubit accrues idle exposure at its current architectural
        location.  Operation-specific fidelity will later consume the active
        exposure, while location-specific survival models consume idle exposure.
        """

        nonlocal qubit_exposure_conflict
        if delta <= 0:
            return
        active_opcode_by_qubit: dict[str, str] = {}
        for item in running.values():
            if item.plane != ExecutionPlane.PROGRAM:
                continue
            for raw_qubit in item.operation.qubits:
                qubit = f"q:{int(raw_qubit)}"
                if qubit in active_opcode_by_qubit:
                    qubit_exposure_conflict = True
                active_opcode_by_qubit[qubit] = item.opcode.value

        for qubit, location in state.locations.items():
            if not qubit.startswith("q:"):
                continue
            opcode = active_opcode_by_qubit.get(qubit)
            if opcode is None:
                idle_exposure_by_location_s[location] += delta
                idle_exposure_by_qubit_s[qubit] += delta
                idle_exposure_by_qubit_location_s[qubit][location] += delta
            else:
                active_exposure_by_opcode_s[opcode] += delta
                active_exposure_by_qubit_s[qubit] += delta

    while len(completed_program) < len(commands):
        timestamp_state_before = (
            trace_state_snapshot() if capture_discrete_log else {}
        )
        # Completion -> state update -> dependency update -> same-time fixed point.
        while running_heap and math.isclose(
            running_heap[0][0],
            now,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            _, event_id = running_heap[0]
            complete(event_id)
            heapq.heappop(running_heap)

        while True:
            changed = dispatch_program()
            if len(completed_program) == len(commands):
                break
            changed = dispatch_resources() or changed
            # Zero-duration completions can enable more work at the same time.
            if not changed:
                break

        if capture_discrete_log:
            waiting = []
            # Diagnostics are passive: never call a scheduling component from
            # trace/report construction.
            for schedule_id in sorted(program_ready):
                work = program_work[schedule_id]
                reasons = blockers_for_program_work(work)
                if reasons:
                    waiting.append(
                        {
                            "instruction_id": work.lineage.source_instruction_id,
                            "opcode": work.operation.opcode.value,
                            "program_ready": True,
                            "resource_ready": False,
                            "resource_blockers": list(reasons),
                            "program_ready_s": program_ready_times[schedule_id],
                        }
                    )
            state_after = trace_state_snapshot()
            discrete_time_log.append(
                {
                    "time_s": now,
                    "completed": timestamp_completed,
                    "architecture_state_delta": state_delta(
                        timestamp_state_before, state_after
                    ),
                    "program_ready_now": sorted(set(timestamp_program_ready_now)),
                    "dispatched": timestamp_dispatched,
                    "frontier_after": {
                        "waiting": waiting,
                        "running": running_frontier(),
                        "completed_now": [
                            item["event_id"] for item in timestamp_completed
                        ],
                    },
                    "architecture_state_after": {
                        "state_version": state_after["version"],
                        "buffers": state_after["buffers"],
                        "engines": state_after["engines"],
                    },
                }
            )
            timestamp_dispatched = []
            timestamp_completed = []
            timestamp_program_ready_now = []

        if len(completed_program) == len(commands):
            break
        next_time = running_heap[0][0] if running_heap else float("inf")
        if not math.isfinite(next_time):
            details = {
                program_work[schedule_id].lineage.work_id: blockers_for_program_work(
                    program_work[schedule_id]
                )
                for schedule_id in sorted(program_ready)[:10]
            }
            raise EvaluationError(
                f"Execution deadlocked at t={now}; blockers={details}"
            )
        if next_time <= now:
            raise EvaluationError(
                f"Event time did not advance: now={now}, next={next_time}"
            )
        delta = next_time - now
        if has_exposed_program_state_block():
            exposed_program_state_blocked_s += delta
        accumulate_process_blocking(delta)
        accumulate_qubit_exposure(delta)
        now = next_time

    logical_qubit_count = sum(key.startswith("q:") for key in state.locations)
    idle_qubit_time_s = canonical_float_sum(
        idle_exposure_by_qubit_s[qubit]
        for qubit in sorted(idle_exposure_by_qubit_s)
    )
    active_qubit_time_s = canonical_float_sum(
        active_exposure_by_qubit_s[qubit]
        for qubit in sorted(active_exposure_by_qubit_s)
    )
    classified_qubit_time_s = canonical_float_sum(
        (idle_qubit_time_s, active_qubit_time_s)
    )
    expected_qubit_time_s = logical_qubit_count * now

    active_output_slots: defaultdict[str, set[str]] = defaultdict(set)
    active_engine_claims: defaultdict[str, int] = defaultdict(int)
    for reservation in state.active_reservations.values():
        for name, slots in reservation.produced_slots.items():
            active_output_slots[name].update(slots)
        for name, amount in reservation.engines.items():
            active_engine_claims[name] += int(amount)

    invariants = {
        "all_program_instructions_completed": len(completed_program) == len(commands),
        "buffer_capacities_respected": all(
            len(buffer.ready_tokens) + buffer.pending_outputs <= buffer.capacity
            for buffer in state.buffers.values()
        ),
        "pending_outputs_nonnegative": all(
            buffer.pending_outputs >= 0 for buffer in state.buffers.values()
        ),
        "pending_output_slots_consistent": all(
            len(buffer.reserved_output_slots) == buffer.pending_outputs
            and not (
                buffer.reserved_output_slots
                & {state.token_slots[token] for token in buffer.ready_tokens}
            )
            for buffer in state.buffers.values()
        ),
        "engine_capacities_respected": all(
            0 <= engine.users <= engine.capacity for engine in state.engines.values()
        ),
        "active_reservations_match_running_events": (
            set(state.active_reservations)
            == {
                item.reservation.reservation_id for item in running.values()
            }
        ),
        "active_reservations_account_for_output_slots": all(
            active_output_slots[name] == buffer.reserved_output_slots
            for name, buffer in state.buffers.items()
        ),
        "active_reservations_account_for_engine_usage": all(
            active_engine_claims[name] == engine.users
            for name, engine in state.engines.items()
        ),
        "transaction_versions_consistent": (
            state.next_reservation_id == next_event_id
            and state.version == len(execution_transitions)
        ),
        "token_locations_consistent": all(
            state.locations.get(token) == name
            for name, buffer in state.buffers.items()
            for token in buffer.ready_tokens
        ),
        "buffer_slots_consistent": all(
            len({state.token_slots[token] for token in buffer.ready_tokens})
            == len(buffer.ready_tokens)
            and all(
                state.token_slots[token] in buffer.slots
                for token in buffer.ready_tokens
            )
            for buffer in state.buffers.values()
        ),
        "nonnegative_latency": now >= 0,
        "qubit_exposure_conflict_free": not qubit_exposure_conflict,
        "qubit_exposure_time_conserved": math.isclose(
            classified_qubit_time_s,
            expected_qubit_time_s,
            rel_tol=1e-10,
            abs_tol=1e-12,
        ),
    }
    terminal_trace_state = TraceStateProjection.from_snapshot(state.snapshot())
    terminal_inflight = tuple(
        dispatch_transition_by_event[event_id] for event_id in sorted(running)
    )
    trace = ExecutionTrace(
        plan_hash=plan.plan_hash,
        seed=run_seed,
        total_latency_s=now,
        transitions=tuple(execution_transitions),
        initial_state=initial_trace_state,
        terminal_state=terminal_trace_state,
        terminal_inflight=terminal_inflight,
    )
    return EvaluationResult(
        trace=trace,
        completed_program_instructions=len(completed_program),
        final_buffers={
            name: tuple(buffer.ready_tokens) for name, buffer in state.buffers.items()
        },
        final_pending_incoming=state.pending_outputs(),
        final_locations=dict(state.locations),
        program_state_blocked_s=exposed_program_state_blocked_s,
        producer_blocked_s=process_blocked_s,
        buffer_peaks=buffer_peaks,
        metrics={
            "resource_instances_started": dict(process_instances),
            "resource_items_started": dict(process_items_started),
            "buffer_tokens_produced": dict(produced_counts),
            "buffer_tokens_discarded": dict(discarded_counts),
            "buffer_tokens_consumed": dict(consumed_counts),
            "opcode_work_s": dict(opcode_time_s),
            "resource_process_work_s": dict(process_work_s),
            "plane_work_s": dict(plane_work_s),
            "unused_resource_tokens": {
                name: len(buffer.ready_tokens) for name, buffer in state.buffers.items()
            },
            "inflight_resource_events_at_program_completion": sum(
                item.plane == ExecutionPlane.RESOURCE for item in running.values()
            ),
            "qubit_exposure_s": {
                "logical_qubit_count": logical_qubit_count,
                "idle_by_location": dict(idle_exposure_by_location_s),
                "idle_by_qubit": dict(idle_exposure_by_qubit_s),
                "idle_by_qubit_and_location": {
                    qubit: dict(by_location)
                    for qubit, by_location in idle_exposure_by_qubit_location_s.items()
                },
                "active_by_opcode": dict(active_exposure_by_opcode_s),
                "active_by_qubit": dict(active_exposure_by_qubit_s),
                "idle_total": idle_qubit_time_s,
                "active_total": active_qubit_time_s,
                "classified_total": classified_qubit_time_s,
            },
        },
        invariant_checks=invariants,
        discrete_time_log=tuple(discrete_time_log),
        runtime_components=actual_manifest.to_dict(),
    )
