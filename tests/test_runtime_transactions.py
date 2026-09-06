from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import importlib
from types import SimpleNamespace

import pytest

from arqsim.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    MagicRouteDispatchRecipe,
    OperationClaims,
    ResourceMoveDispatchRecipe,
)
from arqsim.architecture.state import (
    ArchitectureState,
    InvalidStateTransitionError,
    StaleBindingError,
    StaleCompletionError,
    TentativeBinding,
)
from arqsim.evaluation import (
    BufferSpec,
    EngineSpec,
    EvaluationError,
    EvaluationPolicy,
    ExecutionPlan,
    ExecutionTransition,
    ExecutionTransitionKind,
    ProgramDAG,
    ResourceDAG,
    ResourceProcess,
    evaluate,
)
from arqsim.evaluation.components import (
    RuntimeComponentDescriptor,
    StateBoundRuntimeRealizer,
    build_runtime_component_set,
    default_direct_runtime_component_manifest,
    default_runtime_component_manifest,
)
from arqsim.operation_profiles import ArrivalDistribution


def _with_compiler_component(
    plan: ExecutionPlan,
    *,
    program=None,
    resource=None,
):
    components = build_runtime_component_set(
        default_direct_runtime_component_manifest(),
        runtime_instruction_compiler=None,
        runtime_resource_compiler=None,
    )
    if program is not None or resource is not None:
        components = replace(
            components,
            runtime_realizer=StateBoundRuntimeRealizer(
                RuntimeComponentDescriptor(
                    "runtime_realizer",
                    "tests.runtime_realizer.transaction_probe.v1",
                    provider="tests",
                ),
                program,
                resource,
            ),
        )
    bound = replace(
        plan,
        provenance={},
        runtime_components=components.manifest.to_dict(),
    )
    return bound, components


def _state(
    *buffers: BufferSpec,
    engines: tuple[EngineSpec, ...] = (),
    locations: dict[str, str] | None = None,
) -> ArchitectureState:
    plan = SimpleNamespace(
        buffers=buffers,
        engines=engines,
        initial_locations=locations or {},
    )
    return ArchitectureState.from_plan(plan)


def test_snapshot_and_tentative_binding_are_immutable_and_pure() -> None:
    state = _state(
        BufferSpec(
            "source",
            2,
            "magic_state",
            slots=("S0", "S1"),
            initial_contents=("m0", "m1"),
        ),
        BufferSpec(
            "destination",
            2,
            "magic_state",
            slots=("D0", "D1"),
        ),
        engines=(EngineSpec("move"),),
    )
    operation = ResourceProcess(
        "move",
        ArchitectureOpcode.MOVE_QUBITS,
        consumes={"source": 1},
        produces={"destination": 1},
        forwards={"source": "destination"},
        engines={"move": 1},
    )
    before = state.snapshot()

    first = before.propose(operation)
    second = before.propose(operation)

    assert first == second
    assert first.consumed_tokens == {"source": ("m0",)}
    assert first.consumed_slots == {"source": ("S0",)}
    assert first.produced_slots == {"destination": ("D0",)}
    assert state.snapshot() == before
    with pytest.raises(TypeError):
        before.locations["m0"] = "elsewhere"  # type: ignore[index]
    with pytest.raises(TypeError):
        first.produced_slots["destination"] = ("D1",)  # type: ignore[index]


def test_state_proposal_inherits_instruction_location_contract() -> None:
    state = _state(locations={"q:0": "compute"})
    operation = ArchitectureInstruction(
        0,
        ArchitectureOpcode.MOVE_QUBITS,
        required_locations={"q:0": "compute"},
        completion_locations={"q:0": "memory"},
    )

    binding = state.propose(operation)
    reservation = state.commit(binding, operation)
    state.apply_completion(state.propose_completion(reservation))

    assert state.locations["q:0"] == "memory"


def test_stale_binding_is_rejected_without_mutation() -> None:
    state = _state(
        BufferSpec("a", 1, "a_token", initial_contents=("a0",)),
        BufferSpec("b", 1, "b_token", initial_contents=("b0",)),
    )
    snapshot = state.snapshot()
    operation_a = OperationClaims(consumes={"a": 1})
    operation_b = OperationClaims(consumes={"b": 1})
    binding_a = snapshot.propose(operation_a)
    binding_b = snapshot.propose(operation_b)

    state.commit(binding_a, operation_a)
    before_rejection = state.snapshot()
    with pytest.raises(StaleBindingError):
        state.commit(binding_b, operation_b)

    assert state.snapshot() == before_rejection
    assert tuple(state.buffers["b"].ready_tokens) == ("b0",)


def test_invalid_binding_commit_is_atomic() -> None:
    state = _state(
        BufferSpec("source", 1, "magic_state", initial_contents=("m0",)),
        BufferSpec("destination", 1, "magic_state", slots=("D0",)),
        engines=(EngineSpec("move"),),
    )
    operation = ResourceProcess(
        "move",
        ArchitectureOpcode.MOVE_QUBITS,
        consumes={"source": 1},
        produces={"destination": 1},
        forwards={"source": "destination"},
        engines={"move": 1},
    )
    binding = state.propose(operation)
    invalid = replace(binding, produced_slots={"destination": ("NOT_A_SLOT",)})
    before = state.snapshot()

    with pytest.raises(InvalidStateTransitionError):
        state.commit(invalid, operation)

    assert state.snapshot() == before
    assert tuple(state.buffers["source"].ready_tokens) == ("m0",)
    assert state.engines["move"].users == 0


def test_commit_validates_feasibility_without_owning_fifo_first_free_policy() -> None:
    state = _state(
        BufferSpec(
            "source",
            2,
            "magic_state",
            slots=("S0", "S1"),
            initial_contents=("m0", "m1"),
        ),
        BufferSpec(
            "destination",
            2,
            "magic_state",
            slots=("D0", "D1"),
        ),
    )
    operation = ResourceProcess(
        "move",
        ArchitectureOpcode.MOVE_QUBITS,
        consumes={"source": 1},
        produces={"destination": 1},
        forwards={"source": "destination"},
    )
    default = state.propose(operation)
    policy_selected = replace(
        default,
        consumed_tokens={"source": ("m1",)},
        consumed_slots={"source": ("S1",)},
        produced_slots={"destination": ("D1",)},
    )

    reservation = state.commit(policy_selected, operation)

    assert tuple(state.buffers["source"].ready_tokens) == ("m0",)
    assert reservation.consumed_tokens == {"source": ("m1",)}
    assert reservation.produced_slots == {"destination": ("D1",)}


@pytest.mark.parametrize("elide", ("consume", "produce_and_engine"))
def test_commit_rejects_binding_that_changes_operation_contract(elide: str) -> None:
    state = _state(
        BufferSpec(
            "source",
            2,
            "magic_state",
            initial_contents=("m0", "m1"),
        ),
        BufferSpec("destination", 1, "magic_state"),
        engines=(EngineSpec("move"),),
    )
    operation = ResourceProcess(
        "move",
        ArchitectureOpcode.MOVE_QUBITS,
        consumes={"source": 2},
        produces={"destination": 1},
        engines={"move": 1},
    )
    binding = state.propose(operation)
    if elide == "consume":
        invalid = replace(
            binding,
            consumes={"source": 1},
            consumed_tokens={"source": ("m0",)},
            consumed_slots={"source": ("source:0",)},
        )
    else:
        invalid = replace(
            binding,
            produces={},
            produced_slots={},
            engines={},
        )
    before = state.snapshot()

    with pytest.raises(InvalidStateTransitionError):
        state.commit(invalid, operation)

    assert state.snapshot() == before


def test_warm_start_token_ids_seed_the_allocator() -> None:
    state = _state(
        BufferSpec(
            "magic",
            2,
            "magic_state",
            initial_contents=("magic_state:0",),
        )
    )
    producer = ResourceProcess(
        "factory",
        ArchitectureOpcode.PREPARE_MAGIC_STATE,
        produces={"magic": 1},
    )

    reservation = state.commit(state.propose(producer), producer)
    state.apply_completion(state.propose_completion(reservation))

    assert tuple(state.buffers["magic"].ready_tokens) == (
        "magic_state:0",
        "magic_state:1",
    )


def test_generated_and_forwarded_outputs_keep_globally_unique_identity() -> None:
    state = _state(
        BufferSpec(
            "source",
            1,
            "magic_state",
            initial_contents=("magic_state:0",),
        ),
        BufferSpec("generated", 1, "magic_state"),
        BufferSpec("moved", 1, "magic_state"),
    )
    operation = ResourceProcess(
        "split_outputs",
        ArchitectureOpcode.MOVE_QUBITS,
        consumes={"source": 1},
        produces={"generated": 1, "moved": 1},
        forwards={"source": "moved"},
    )

    reservation = state.commit(state.propose(operation), operation)
    state.apply_completion(state.propose_completion(reservation))

    tokens = (
        *state.buffers["generated"].ready_tokens,
        *state.buffers["moved"].ready_tokens,
    )
    assert tokens == ("magic_state:1", "magic_state:0")
    assert len(tokens) == len(set(tokens))


def test_duplicate_initial_token_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="globally unique"):
        _state(
            BufferSpec("a", 1, "token", initial_contents=("dup",)),
            BufferSpec("b", 1, "token", initial_contents=("dup",)),
        )


def test_state_rejects_initial_resource_and_logical_entity_id_collision() -> None:
    with pytest.raises(ValueError, match="conflict with initial location entities"):
        _state(
            BufferSpec("magic", 1, "magic_state", initial_contents=("q:0",)),
            locations={"q:0": "compute"},
        )


def test_duplicate_forwarding_destination_is_rejected_atomically() -> None:
    state = _state(
        BufferSpec("a", 1, "magic_state", initial_contents=("a0",)),
        BufferSpec("b", 1, "magic_state", initial_contents=("b0",)),
        BufferSpec("destination", 1, "magic_state"),
    )
    operation = ResourceProcess(
        "merge",
        ArchitectureOpcode.MOVE_QUBITS,
        consumes={"a": 1, "b": 1},
        produces={"destination": 1},
        forwards={"a": "destination", "b": "destination"},
    )
    binding = state.propose(operation)
    before = state.snapshot()

    with pytest.raises(InvalidStateTransitionError, match="exactly one source"):
        state.commit(binding, operation)

    assert state.snapshot() == before


def test_completion_delta_is_pure_atomic_and_preserves_forwarded_identity() -> None:
    state = _state(
        BufferSpec("source", 1, "magic_state", initial_contents=("m0",)),
        BufferSpec("destination", 1, "magic_state", slots=("D0",)),
        engines=(EngineSpec("move"),),
    )
    operation = ResourceProcess(
        "move",
        ArchitectureOpcode.MOVE_QUBITS,
        consumes={"source": 1},
        produces={"destination": 1},
        forwards={"source": "destination"},
        engines={"move": 1},
    )
    reservation = state.commit(state.propose(operation), operation)
    committed = state.snapshot()
    delta = state.propose_completion(reservation)

    assert state.snapshot() == committed
    assert delta.produced_tokens == {"destination": ("m0",)}
    assert delta.produced_slots == {"destination": ("D0",)}
    assert delta.released_engines == {"move": 1}

    invalid = replace(
        delta, produced_slots={"destination": ("NOT_A_SLOT",)}
    )
    with pytest.raises(InvalidStateTransitionError):
        state.apply_completion(invalid)
    assert state.snapshot() == committed

    state.apply_completion(delta)
    completed = state.snapshot()
    assert completed.version == committed.version + 1
    assert tuple(state.buffers["destination"].ready_tokens) == ("m0",)
    assert state.token_slots["m0"] == "D0"
    assert state.engines["move"].users == 0

    with pytest.raises(StaleCompletionError):
        state.apply_completion(delta)
    assert state.snapshot() == completed


def test_stale_completion_delta_is_rejected_without_mutation() -> None:
    state = _state(
        BufferSpec("out", 2, "token", slots=("O0", "O1")),
        engines=(EngineSpec("producer", 2),),
    )
    producer = ResourceProcess(
        "producer",
        ArchitectureOpcode.PREPARE_MAGIC_STATE,
        produces={"out": 1},
        engines={"producer": 1},
        parallelism=2,
    )
    first = state.commit(state.propose(producer), producer)
    first_delta = state.propose_completion(first)
    state.commit(state.propose(producer), producer)
    before_rejection = state.snapshot()

    with pytest.raises(StaleCompletionError):
        state.apply_completion(first_delta)

    assert state.snapshot() == before_rejection


def test_trace_validation_failure_precedes_completion_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="full"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=1.0,
                    engines={"compute": 1},
                ),
            )
        ),
        ResourceDAG(()),
        (),
        (EngineSpec("compute"),),
    )
    state = ArchitectureState.from_plan(plan)
    monkeypatch.setattr(
        ArchitectureState,
        "from_plan",
        classmethod(lambda cls, _plan: state),
    )

    before_completion = None
    original_propose_completion = ArchitectureState.propose_completion

    def track_completion_proposal(self, reservation, **kwargs):
        nonlocal before_completion
        before_completion = self.snapshot()
        return original_propose_completion(self, reservation, **kwargs)

    monkeypatch.setattr(
        ArchitectureState,
        "propose_completion",
        track_completion_proposal,
    )
    original_transition_post_init = ExecutionTransition.__post_init__

    def reject_completion_transition(transition):
        original_transition_post_init(transition)
        if transition.kind == ExecutionTransitionKind.COMPLETION:
            raise RuntimeError("completion trace construction failed")

    monkeypatch.setattr(
        ExecutionTransition,
        "__post_init__",
        reject_completion_transition,
    )

    with pytest.raises(RuntimeError, match="trace construction failed"):
        evaluate(plan)

    assert before_completion is not None
    assert state.snapshot() == before_completion
    assert state.engines["compute"].users == 1
    assert before_completion.active_reservation_ids


def _program_compiler_plan() -> ExecutionPlan:
    return ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="full"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=0.5,
                    consumes={"magic": 1},
                    engines={"compute": 1},
                    deferred_dispatch=MagicRouteDispatchRecipe(
                        operation_indices=(0,),
                        data_mapping={0: "data0"},
                    ),
                ),
            )
        ),
        ResourceDAG(()),
        (
            BufferSpec(
                "magic",
                1,
                "magic_state",
                initial_contents=("m0",),
            ),
        ),
        (EngineSpec("compute"),),
        runtime_components=default_runtime_component_manifest().to_dict(),
    )


@pytest.mark.parametrize("failure", ("raise", "nan", "string", "decimal"))
def test_program_compiler_failure_leaves_state_unchanged(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    engine_module = importlib.import_module("arqsim.evaluation.engine")
    plan = _program_compiler_plan()
    state = ArchitectureState.from_plan(plan)
    before = state.snapshot()
    commit_calls = []
    original_commit = ArchitectureState.commit

    monkeypatch.setattr(
        ArchitectureState,
        "from_plan",
        classmethod(lambda cls, _plan: state),
    )

    def tracked_commit(self, binding, operation):
        commit_calls.append(binding)
        return original_commit(self, binding, operation)

    monkeypatch.setattr(engine_module.ArchitectureState, "commit", tracked_commit)

    def compiler(_request):
        if failure == "raise":
            raise RuntimeError("compiler failed")
        invalid = {
            "nan": float("nan"),
            "string": "1",
            "decimal": Decimal("1"),
        }
        return {"duration_s": invalid[failure]}

    expected_error = RuntimeError if failure == "raise" else EvaluationError
    plan, components = _with_compiler_component(plan, program=compiler)
    with pytest.raises(expected_error):
        evaluate(plan, runtime_components=components)

    assert commit_calls == []
    assert state.snapshot() == before


def test_resource_compiler_failure_leaves_state_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_module = importlib.import_module("arqsim.evaluation.engine")
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="full"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    consumes={"destination": 1},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "move",
                    ArchitectureOpcode.MOVE_QUBITS,
                    consumes={"source": 1},
                    produces={"destination": 1},
                    forwards={"source": "destination"},
                    engines={"move": 1},
                    deferred_dispatch=ResourceMoveDispatchRecipe(),
                ),
            )
        ),
        (
            BufferSpec(
                "source",
                1,
                "magic_state",
                initial_contents=("m0",),
            ),
            BufferSpec("destination", 1, "magic_state"),
        ),
        (EngineSpec("move"),),
        runtime_components=default_runtime_component_manifest().to_dict(),
    )
    state = ArchitectureState.from_plan(plan)
    before = state.snapshot()
    commit_calls = []
    original_commit = ArchitectureState.commit

    monkeypatch.setattr(
        ArchitectureState,
        "from_plan",
        classmethod(lambda cls, _plan: state),
    )

    def tracked_commit(self, binding, operation):
        commit_calls.append(binding)
        return original_commit(self, binding, operation)

    monkeypatch.setattr(engine_module.ArchitectureState, "commit", tracked_commit)

    def compiler(_request):
        raise RuntimeError("resource compiler failed")

    plan, components = _with_compiler_component(plan, resource=compiler)
    with pytest.raises(RuntimeError, match="resource compiler failed"):
        evaluate(plan, runtime_components=components)

    assert commit_calls == []
    assert state.snapshot() == before


def test_malformed_compiler_metadata_is_rejected_before_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="full"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    consumes={"magic": 1},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "move",
                    ArchitectureOpcode.MOVE_QUBITS,
                    consumes={"source": 1},
                    produces={"magic": 1},
                    forwards={"source": "magic"},
                    deferred_dispatch=ResourceMoveDispatchRecipe(),
                ),
            )
        ),
        (
            BufferSpec(
                "source",
                1,
                "magic_state",
                initial_contents=("m0",),
            ),
            BufferSpec("magic", 1, "magic_state"),
        ),
        (),
        runtime_components=default_runtime_component_manifest().to_dict(),
    )
    state = ArchitectureState.from_plan(plan)
    before = state.snapshot()
    monkeypatch.setattr(
        ArchitectureState,
        "from_plan",
        classmethod(lambda cls, _plan: state),
    )

    def compiler(_request):
        return {"discarded_outputs": 1}

    plan, components = _with_compiler_component(plan, resource=compiler)
    with pytest.raises(EvaluationError, match="typed/Engine control fields"):
        evaluate(plan, runtime_components=components)

    assert state.snapshot() == before


def test_all_same_time_completions_precede_program_first_dispatch() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="full"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=1.0,
                    engines={"compute": 1},
                ),
                ArchitectureInstruction(
                    1,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    predecessor_ids=(0,),
                    duration_s=0.25,
                    consumes={"magic": 1},
                    engines={"compute": 1},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    produces={"magic": 1},
                    engines={"factory": 1},
                    duration_s=1.0,
                ),
            )
        ),
        (BufferSpec("magic", 1, "magic_state"),),
        (EngineSpec("compute"), EngineSpec("factory")),
    )

    result = evaluate(plan)
    at_one = next(item for item in result.discrete_time_log if item["time_s"] == 1.0)

    assert [item["event_id"] for item in at_one["completed"]] == [0, 1]
    assert [item["plane"] for item in at_one["dispatched"]] == [
        "program",
        "resource",
    ]
    assert at_one["dispatched"][0]["reservation"]["consumed_tokens"] == {
        "magic": ("magic_state:0",)
    }
    assert result.total_latency_s == pytest.approx(1.25)


def test_final_program_completion_does_not_start_new_resource_work() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="full"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=1.0,
                    engines={"shared": 1},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    produces={"magic": 1},
                    engines={"shared": 1},
                    duration_s=1.0,
                ),
            )
        ),
        (BufferSpec("magic", 1, "magic_state"),),
        (EngineSpec("shared"),),
    )

    result = evaluate(plan)

    assert [event.plane.value for event in result.events] == ["program"]
    assert result.metrics["resource_instances_started"]["factory"] == 0
    assert result.total_latency_s == 1.0


def test_large_timestamp_does_not_complete_later_event_early() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="full"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=1_000_000_000.0,
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    produces={"magic": 1},
                    duration_s=1_000_000_000.5,
                ),
            )
        ),
        (BufferSpec("magic", 1, "magic_state"),),
        (),
    )

    result = evaluate(plan)

    assert result.total_latency_s == 1_000_000_000.0
    assert [event.plane.value for event in result.events] == ["program"]
    assert result.metrics["inflight_resource_events_at_program_completion"] == 1


def test_zero_duration_program_chain_drains_before_resource_plane() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="full"),
        ProgramDAG(
            (
                ArchitectureInstruction(0, ArchitectureOpcode.FENCE),
                ArchitectureInstruction(
                    1,
                    ArchitectureOpcode.FENCE,
                    predecessor_ids=(0,),
                ),
                ArchitectureInstruction(
                    2,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    predecessor_ids=(1,),
                    duration_s=1.0,
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    produces={"magic": 1},
                    duration_s=2.0,
                ),
            )
        ),
        (BufferSpec("magic", 1, "magic_state"),),
        (),
    )

    result = evaluate(plan)
    at_zero = result.discrete_time_log[0]

    assert [item["plane"] for item in at_zero["dispatched"]] == [
        "program",
        "program",
        "program",
        "resource",
    ]


def test_zero_duration_resource_cycle_is_stopped_by_transition_budget() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(max_events=4),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=1.0,
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "heartbeat",
                    ArchitectureOpcode.MOVE_QUBITS,
                ),
            )
        ),
        (),
        (),
    )

    with pytest.raises(EvaluationError, match="max_events=4"):
        evaluate(plan)


def test_zero_duration_program_resource_fixed_point_converges() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="full", max_events=20),
        ProgramDAG(
            (
                ArchitectureInstruction(0, ArchitectureOpcode.FENCE),
                ArchitectureInstruction(
                    1,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    predecessor_ids=(0,),
                    consumes={"magic": 1},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    produces={"magic": 1},
                ),
            )
        ),
        (BufferSpec("magic", 1, "magic_state"),),
        (),
    )

    result = evaluate(plan)

    assert result.total_latency_s == 0.0
    # The final Program completion closes the horizon; no post-horizon refill.
    assert len(result.events) == 3
    assert all(event.start_s == event.end_s == 0.0 for event in result.events)
    assert [item["time_s"] for item in result.discrete_time_log] == [0.0]
    assert all(result.invariant_checks.values())


def test_program_and_resource_dispatch_share_tentative_commit_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="full"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    consumes={"magic": 1},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    produces={"magic": 1},
                    duration_s=1.0,
                ),
            )
        ),
        (BufferSpec("magic", 1, "magic_state"),),
        (),
    )
    committed: list[TentativeBinding] = []
    original_commit = ArchitectureState.commit

    def tracked_commit(self, binding, operation):
        committed.append(binding)
        return original_commit(self, binding, operation)

    monkeypatch.setattr(ArchitectureState, "commit", tracked_commit)
    result = evaluate(plan)

    assert all(isinstance(binding, TentativeBinding) for binding in committed)
    assert any(binding.produces == {"magic": 1} for binding in committed)
    assert any(binding.consumed_tokens for binding in committed)
    assert all(
        event.reservation_id == event.event_id for event in result.events
    )
    assert all(
        dispatch["reservation"]["reservation_id"] == dispatch["event_id"]
        for timestamp in result.discrete_time_log
        for dispatch in timestamp["dispatched"]
    )
