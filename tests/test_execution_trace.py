from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
from types import MappingProxyType

import pytest

from arqsim.architecture.isa import ArchitectureInstruction, ArchitectureOpcode
from arqsim.evaluation import (
    BufferSpec,
    EngineSpec,
    EvaluationPolicy,
    ExecutionPlan,
    ExecutionEvent,
    ExecutionTrace,
    ExecutionTransitionKind,
    ProgramDAG,
    ResourceDAG,
    ResourceProcess,
    TraceReplayError,
    TraceValidationError,
    evaluate,
    replay_execution_trace,
    validate_discrete_time_log_document,
    validate_execution_trace_document,
)
from arqsim.schema import semantic_hash


def _zero_duration_plan() -> ExecutionPlan:
    return ExecutionPlan(
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
            )
        ),
        ResourceDAG(()),
        (),
        (),
    )


def _out_of_order_completion_plan() -> ExecutionPlan:
    return ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="summary"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=2.0,
                ),
                ArchitectureInstruction(
                    1,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=1.0,
                ),
            )
        ),
        ResourceDAG(()),
        (),
        (),
    )


def _inflight_plan(*, trace_level: str = "summary") -> ExecutionPlan:
    return ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level=trace_level),
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
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    duration_s=2.0,
                    produces={"magic": 1},
                    engines={"factory": 1},
                ),
            )
        ),
        (BufferSpec("magic", 1, "magic_state", slots=("M0",)),),
        (EngineSpec("factory"),),
    )


def _forwarding_plan() -> ExecutionPlan:
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
                    duration_s=0.25,
                    consumes={"destination": 1},
                    engines={"compute": 1},
                    required_locations={"q:0": "compute-a"},
                    completion_locations={"q:0": "compute-b"},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "move",
                    ArchitectureOpcode.MOVE_QUBITS,
                    duration_s=0.5,
                    consumes={"source": 1},
                    produces={"destination": 1},
                    forwards={"source": "destination"},
                    engines={"move": 1},
                    metadata={"nested": {"route": ["S0", "D0"]}},
                ),
            )
        ),
        (
            BufferSpec(
                "source",
                1,
                "magic_state",
                initial_contents=("m0",),
                slots=("S0",),
            ),
            BufferSpec(
                "destination",
                1,
                "magic_state",
                slots=("D0",),
            ),
        ),
        (EngineSpec("move"), EngineSpec("compute")),
        initial_locations={"q:0": "compute-a"},
    )


def test_trace_v4_still_parses_pre_refreeze_open_outcome_payloads() -> None:
    plan = _zero_duration_plan()
    document = deepcopy(evaluate(plan).trace.to_dict())
    completion = next(
        transition
        for transition in document["transitions"]
        if transition["kind"] == "completion"
    )
    completion["outcome"] = {
        "values": {"legacy_value": 1},
        "metadata": {"legacy_source": "custom-outcome-hook"},
    }
    document["trace_hash"] = semantic_hash(
        {
            key: value
            for key, value in document.items()
            if key != "trace_hash"
        }
    )

    parsed = ExecutionTrace.from_dict(document)

    assert parsed.to_dict() == document
    assert replay_execution_trace(parsed, plan) == parsed.terminal_state


def _parallel_resource_plan(*, parallelism: int) -> ExecutionPlan:
    return ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="summary"),
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
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    duration_s=2.0,
                    produces={"magic": 1},
                    engines={"factory": 1},
                    parallelism=parallelism,
                ),
            )
        ),
        (BufferSpec("magic", 2, "magic_state", slots=("M0", "M1")),),
        (EngineSpec("factory", capacity=2),),
    )


def _eager_resource_plan(*, dispatch_policy: str) -> ExecutionPlan:
    return ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="summary"),
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
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    duration_s=2.0,
                    consumes={"raw": 1},
                    produces={"magic": 1},
                    dispatch_policy=dispatch_policy,
                    protocol="eager-test",
                ),
            )
        ),
        (
            BufferSpec(
                "raw",
                2,
                "raw_state",
                slots=("R0", "R1"),
                initial_contents=("r0", "r1"),
            ),
            BufferSpec("magic", 2, "magic_state", slots=("M0", "M1")),
        ),
        (),
    )


def test_zero_duration_ledger_preserves_real_dispatch_completion_order() -> None:
    plan = _zero_duration_plan()
    result = evaluate(plan)

    assert [item.kind for item in result.transitions] == [
        ExecutionTransitionKind.DISPATCH,
        ExecutionTransitionKind.COMPLETION,
        ExecutionTransitionKind.DISPATCH,
        ExecutionTransitionKind.COMPLETION,
    ]
    assert [item.event_id for item in result.transitions] == [0, 0, 1, 1]
    assert [
        (item.state_version_before, item.state_version_after)
        for item in result.transitions
    ] == [(0, 1), (1, 2), (2, 3), (3, 4)]
    assert replay_execution_trace(result.trace, plan) == result.trace.terminal_state


def test_v1_event_projection_preserves_historical_dispatch_id_order() -> None:
    result = evaluate(_out_of_order_completion_plan())

    completion_order = [
        item.event_id
        for item in result.transitions
        if item.kind == ExecutionTransitionKind.COMPLETION
    ]
    assert completion_order == [1, 0]
    assert [event.event_id for event in result.events] == [0, 1]


@pytest.mark.parametrize("trace_level", ("summary", "full"))
def test_program_horizon_keeps_full_inflight_dispatch(trace_level: str) -> None:
    plan = _inflight_plan(trace_level=trace_level)
    result = evaluate(plan)

    assert [event.event_id for event in result.events] == [0]
    assert len(result.terminal_inflight) == 1
    inflight = result.terminal_inflight[0]
    assert inflight.kind == ExecutionTransitionKind.DISPATCH
    assert inflight.event_id == inflight.reservation_id == 1
    assert inflight.process_id == "factory"
    assert inflight.start_s == 0.0
    assert inflight.end_s == 2.0
    assert inflight.produces == {"magic": 1}
    assert inflight.produced_slots == {"magic": ("M0",)}
    assert inflight.engines == {"factory": 1}
    assert result.trace.terminal_state.active_reservation_ids == (1,)
    assert result.trace.terminal_state.reserved_output_slots == {
        "magic": ("M0",)
    }
    assert replay_execution_trace(result.trace, plan) == result.trace.terminal_state
    if trace_level == "summary":
        assert result.discrete_time_log == ()


def test_replay_preserves_forwarded_token_slots_engines_and_locations() -> None:
    plan = _forwarding_plan()
    result = evaluate(plan)
    resource_completion = next(
        item
        for item in result.transitions
        if item.kind == ExecutionTransitionKind.COMPLETION
        and item.plane.value == "resource"
    )
    program_dispatch = next(
        item
        for item in result.transitions
        if item.kind == ExecutionTransitionKind.DISPATCH
        and item.plane.value == "program"
    )

    assert resource_completion.consumed_tokens == {"source": ("m0",)}
    assert resource_completion.consumed_slots == {"source": ("S0",)}
    assert resource_completion.produced_tokens == {"destination": ("m0",)}
    assert resource_completion.produced_slots == {"destination": ("D0",)}
    assert resource_completion.forwards == {"source": "destination"}
    assert resource_completion.engines == {"move": 1}
    assert program_dispatch.required_locations == {"q:0": "compute-a"}
    assert program_dispatch.completion_locations == {"q:0": "compute-b"}
    assert result.trace.terminal_state.locations["q:0"] == "compute-b"
    assert result.trace.terminal_state.engine_usage == {"compute": 0, "move": 0}
    assert replay_execution_trace(result.trace, plan) == result.trace.terminal_state


def test_trace_and_result_payloads_are_deeply_immutable() -> None:
    result = evaluate(_forwarding_plan())
    original_hash = result.trace_hash
    original_payload = result.diagnostic_dict()

    with pytest.raises(TypeError):
        result.events[0].metadata["new"] = "mutation"
    with pytest.raises(TypeError):
        result.events[0].metadata["nested"]["route"] = ()
    with pytest.raises(TypeError):
        result.transitions[0].metadata["new"] = "mutation"
    with pytest.raises(TypeError):
        result.trace.initial_state.locations["q:0"] = "elsewhere"
    with pytest.raises(TypeError):
        result.metrics["new"] = 1
    with pytest.raises(TypeError):
        result.discrete_time_log[0]["new"] = 1

    assert result.trace_hash == original_hash
    assert result.diagnostic_dict() == original_payload

    with pytest.raises(TraceValidationError, match="completed_program_instructions"):
        replace(
            result,
            completed_program_instructions=(
                result.completed_program_instructions + 1
            ),
        )


def test_trace_rejects_reordered_or_missing_causal_transition() -> None:
    result = evaluate(_zero_duration_plan())
    reordered = (result.transitions[1], result.transitions[0], *result.transitions[2:])
    with pytest.raises(TraceValidationError):
        replace(result.trace, transitions=reordered)

    with pytest.raises(TraceValidationError):
        replace(result.trace, transitions=result.transitions[:-1])


def test_replay_rejects_a_different_plan() -> None:
    result = evaluate(_zero_duration_plan())
    other = replace(_zero_duration_plan(), circuit_hash="different")

    with pytest.raises(TraceReplayError, match="plan_hash"):
        replay_execution_trace(result.trace, other)


def test_empty_program_has_equal_initial_and_terminal_projection() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="summary"),
        ProgramDAG(()),
        ResourceDAG(()),
        (),
        (),
    )
    result = evaluate(plan)

    assert result.transitions == result.events == result.terminal_inflight == ()
    assert result.trace.initial_state == result.trace.terminal_state
    assert replay_execution_trace(result.trace, plan) == result.trace.terminal_state


def test_fixed_plan_and_seed_produce_same_trace_hash() -> None:
    plan = _forwarding_plan()

    first = evaluate(plan)
    second = evaluate(plan)

    assert first.trace_hash == second.trace_hash
    assert first.trace.semantic_dict() == second.trace.semantic_dict()


def test_serialized_trace_document_round_trips_and_rejects_tampering() -> None:
    result = evaluate(_forwarding_plan())
    document = result.trace.to_dict()

    assert document["schema_version"] == "arqsim.execution-trace.v4"
    assert "events" not in document
    assert result.trace.from_dict(document) == result.trace

    validated = validate_execution_trace_document(document)
    assert validated == result.trace
    assert validated.trace_hash == document["trace_hash"]

    tampered = deepcopy(document)
    tampered["transitions"][0], tampered["transitions"][1] = (
        tampered["transitions"][1],
        tampered["transitions"][0],
    )
    # Re-signing an outer report cannot make an invalid causal ledger valid.
    tampered["report_hash"] = "recomputed-outer-hash"
    with pytest.raises(TraceValidationError):
        validate_execution_trace_document(tampered)


def test_trace_v2_codec_rejects_resigned_python_and_scalar_aliases() -> None:
    result = evaluate(_forwarding_plan())

    def resign(document):
        unsigned = {
            key: value
            for key, value in document.items()
            if key != "trace_hash"
        }
        document["trace_hash"] = semantic_hash(unsigned)
        return document

    tuple_alias = deepcopy(result.trace.to_dict())
    tuple_alias["transitions"] = tuple(tuple_alias["transitions"])

    proxy_alias = deepcopy(result.trace.to_dict())
    proxy_alias["initial_state"] = MappingProxyType(
        proxy_alias["initial_state"]
    )

    non_string_key = deepcopy(result.trace.to_dict())
    non_string_key["initial_state"]["locations"][1] = "compute"

    numeric_alias = deepcopy(result.trace.to_dict())
    numeric_alias["transitions"][0]["time_s"] = 0

    for document, message in (
        (resign(tuple_alias), "plain lists"),
        (resign(proxy_alias), "plain dictionaries"),
        (resign(non_string_key), "keys must be strings"),
        (resign(numeric_alias), "exact canonical"),
    ):
        with pytest.raises(TraceValidationError, match=message):
            result.trace.from_dict(document)

    nonfinite = deepcopy(result.trace.to_dict())
    nonfinite["transitions"][0]["time_s"] = float("inf")
    with pytest.raises(TraceValidationError, match="finite JSON numbers"):
        result.trace.from_dict(nonfinite)


def test_trace_validator_rejects_flattened_and_derived_compatibility_views() -> None:
    result = evaluate(_forwarding_plan())
    flattened_result = result.diagnostic_dict()
    assert "schema_version" not in flattened_result
    with pytest.raises(TraceValidationError, match="Unsupported execution-trace"):
        validate_execution_trace_document(flattened_result)

    canonical_with_projection = result.trace.to_dict()
    canonical_with_projection["events"] = [
        event.to_dict() for event in result.events
    ]
    with pytest.raises(TraceValidationError, match="Unknown ExecutionTrace"):
        validate_execution_trace_document(canonical_with_projection)

    report_v1_shape = result.trace.to_dict()
    report_v1_shape["schema_version"] = "arqsim.execution-trace.v1"
    with pytest.raises(TraceValidationError, match="Unsupported execution-trace"):
        validate_execution_trace_document(report_v1_shape)


def test_serialized_trace_rejects_duplicate_final_state_projection() -> None:
    result = evaluate(_inflight_plan())
    document = result.trace.to_dict()
    document["final_state"] = {
        "buffers": {},
        "pending_incoming": {},
        "locations": {},
    }

    with pytest.raises(TraceValidationError, match="Unknown ExecutionTrace"):
        validate_execution_trace_document(document)


def test_trace_rejects_negative_or_post_horizon_times() -> None:
    result = evaluate(_forwarding_plan())

    with pytest.raises(TraceValidationError, match="negative"):
        replace(result.transitions[0], time_s=-1.0, start_s=-1.0)
    with pytest.raises(TraceValidationError, match="negative"):
        replace(result.events[0], start_s=-1.0)
    with pytest.raises(TraceValidationError, match="final Program completion"):
        replace(result.trace, total_latency_s=result.total_latency_s + 1.0)

    inflight_result = evaluate(_inflight_plan())
    original = inflight_result.terminal_inflight[0]
    clipped = replace(original, end_s=inflight_result.total_latency_s)
    transitions = tuple(
        clipped if item.transition_id == original.transition_id else item
        for item in inflight_result.transitions
    )
    with pytest.raises(TraceValidationError, match="unfinished Resource"):
        replace(
            inflight_result.trace,
            transitions=transitions,
            terminal_inflight=(clipped,),
        )


def test_trace_rejects_opaque_mutable_and_nonfinite_payloads() -> None:
    result = evaluate(_forwarding_plan())

    with pytest.raises(ValueError, match="finite JSON"):
        replace(result.events[0], metadata={"opaque": bytearray(b"mutable")})
    with pytest.raises(ValueError, match="finite JSON"):
        replace(result.transitions[0], metadata={"not_a_number": float("nan")})
    with pytest.raises(ValueError, match="finite JSON"):
        replace(result, metrics={"opaque": bytearray(b"mutable")})
    with pytest.raises(ValueError, match="finite JSON"):
        replace(result, discrete_time_log=({"not_a_number": float("nan")},))
    with pytest.raises(TraceValidationError, match="finite"):
        replace(result.trace, total_latency_s=float("nan"))


def test_replay_rejects_trace_identity_forged_away_from_plan_source() -> None:
    plan = _forwarding_plan()
    result = evaluate(plan)
    program_ids = {
        item.event_id
        for item in result.transitions
        if item.plane.value == "program"
    }
    forged_transitions = tuple(
        replace(item, opcode=ArchitectureOpcode.FENCE)
        if item.event_id in program_ids
        else item
        for item in result.transitions
    )
    forged_trace = replace(
        result.trace,
        transitions=forged_transitions,
    )

    with pytest.raises(TraceReplayError, match="does not match work"):
        replay_execution_trace(forged_trace, plan)


def test_trace_identity_scalars_are_type_strict_and_detached() -> None:
    result = evaluate(_forwarding_plan())
    program_transition = next(
        item for item in result.transitions if item.plane.value == "program"
    )
    resource_transition = next(
        item for item in result.transitions if item.plane.value == "resource"
    )
    program_event = next(
        item for item in result.events if item.plane.value == "program"
    )
    resource_event = next(
        item for item in result.events if item.plane.value == "resource"
    )

    invalid_constructors = (
        lambda: replace(result.trace, plan_hash=bytearray(b"mutable")),
        lambda: replace(result.trace, plan_hash=""),
        lambda: replace(result.trace, seed=True),
        lambda: replace(program_transition, candidate_id=bytearray(b"candidate")),
        lambda: replace(program_transition, instruction_id=True),
        lambda: replace(program_transition, instruction_id=0.0),
        lambda: replace(resource_transition, process_id=bytearray(b"process")),
        lambda: replace(resource_transition, process_id=""),
        lambda: replace(resource_transition, instance=True),
        lambda: replace(resource_transition, instance=0.0),
        lambda: replace(program_event, candidate_id=bytearray(b"candidate")),
        lambda: replace(program_event, instruction_id=False),
        lambda: replace(resource_event, process_id=bytearray(b"process")),
        lambda: replace(resource_event, instance=0.0),
        lambda: replace(program_transition, transition_id=False),
        lambda: replace(program_transition, reservation_id=0.0),
        lambda: replace(program_transition, state_version_before=False),
    )
    for construct in invalid_constructors:
        with pytest.raises(TraceValidationError):
            construct()


def test_replay_enforces_resource_process_parallelism_from_source_plan() -> None:
    source_plan = _parallel_resource_plan(parallelism=2)
    result = evaluate(source_plan)
    resource_dispatches = tuple(
        item
        for item in result.terminal_inflight
        if item.process_id == "factory"
    )
    assert len(resource_dispatches) == 2

    forged_plan = _parallel_resource_plan(parallelism=1)
    forged_trace = replace(result.trace, plan_hash=forged_plan.plan_hash)

    with pytest.raises(TraceReplayError, match="parallelism"):
        replay_execution_trace(forged_trace, forged_plan)


def test_replay_enforces_resource_dispatch_policy_and_exact_batch() -> None:
    eager_plan = _eager_resource_plan(dispatch_policy="eager_available")
    result = evaluate(eager_plan)
    resource_dispatch = next(
        item
        for item in result.terminal_inflight
        if item.process_id == "factory"
    )
    assert resource_dispatch.metadata["batch_amount"] == 2
    assert resource_dispatch.consumes == {"raw": 2}
    assert resource_dispatch.produces == {"magic": 2}

    single_plan = _eager_resource_plan(dispatch_policy="single")
    forged_trace = replace(result.trace, plan_hash=single_plan.plan_hash)

    with pytest.raises(TraceReplayError, match="batch_amount|metadata"):
        replay_execution_trace(forged_trace, single_plan)


def test_replay_rejects_forged_analyzer_critical_program_metadata() -> None:
    plan = _forwarding_plan()
    result = evaluate(plan)
    program_event_ids = {
        item.event_id
        for item in result.transitions
        if item.plane.value == "program"
    }
    forged_transitions = tuple(
        replace(
            item,
            metadata={**dict(item.metadata), "qubits": [99]},
        )
        if item.event_id in program_event_ids
        else item
        for item in result.transitions
    )
    forged_trace = replace(
        result.trace,
        transitions=forged_transitions,
    )

    with pytest.raises(TraceReplayError, match="trace-projected metadata 'qubits'"):
        replay_execution_trace(forged_trace, plan)


def test_trace_collection_fields_reject_scalar_string_aliases() -> None:
    result = evaluate(_forwarding_plan())
    transition = next(
        item for item in result.transitions if item.consumed_tokens
    )
    event = next(item for item in result.events if item.consumed_tokens)

    invalid_constructors = (
        lambda: replace(
            transition,
            consumed_tokens={"source": "x"},
            consumed_slots={"source": "A"},
        ),
        lambda: replace(event, consumed_tokens={"source": "x"}),
        lambda: replace(transition, wait_reasons="x"),
        lambda: replace(
            result.trace.initial_state,
            buffers={**dict(result.trace.initial_state.buffers), "source": "x"},
        ),
        lambda: replace(
            result.trace.terminal_state,
            reserved_output_slots={
                **dict(result.trace.terminal_state.reserved_output_slots),
                "source": "A",
            },
        ),
    )
    for construct in invalid_constructors:
        with pytest.raises(TraceValidationError, match="JSON array"):
            construct()

    document = event.to_dict()
    document["wait_reasons"] = "x"
    with pytest.raises(TraceValidationError, match="JSON array"):
        ExecutionEvent.from_dict(document)


def test_discrete_time_log_validator_locks_full_and_summary_parity() -> None:
    full_plan = _forwarding_plan()
    full_result = evaluate(full_plan)
    validated = validate_discrete_time_log_document(
        full_result.discrete_time_log,
        full_result.trace,
        full_plan,
        "full",
    )
    assert validated == full_result.discrete_time_log

    summary_plan = _inflight_plan(trace_level="summary")
    summary_result = evaluate(summary_plan)
    assert (
        validate_discrete_time_log_document(
            summary_result.discrete_time_log,
            summary_result.trace,
            summary_plan,
            "summary",
        )
        == ()
    )
    with pytest.raises(TraceValidationError, match="summary"):
        validate_discrete_time_log_document(
            (full_result.discrete_time_log[0],),
            summary_result.trace,
            summary_plan,
            "summary",
        )


def test_discrete_time_log_validator_rejects_re_signed_ledger_projection() -> None:
    plan = _forwarding_plan()
    result = evaluate(plan)
    tampered = deepcopy(result.diagnostic_dict()["discrete_time_log"])
    tampered[0]["dispatched"][0]["candidate_id"] = "resource:forged:0"

    with pytest.raises(TraceValidationError, match="dispatched"):
        validate_discrete_time_log_document(
            tampered,
            result.trace,
            plan,
            "full",
        )

    tampered = deepcopy(result.diagnostic_dict()["discrete_time_log"])
    tampered[0]["frontier_after"]["waiting"][0]["instruction_id"] = True
    with pytest.raises(TraceValidationError, match="waiting"):
        validate_discrete_time_log_document(
            tampered,
            result.trace,
            plan,
            "full",
        )

    tampered = deepcopy(result.diagnostic_dict()["discrete_time_log"])
    tampered[0]["frontier_after"]["waiting"][0]["resource_blockers"] = [
        "forged:changes_attribution"
    ]
    with pytest.raises(TraceValidationError, match="waiting"):
        validate_discrete_time_log_document(
            tampered,
            result.trace,
            plan,
            "full",
        )
