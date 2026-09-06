from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from arqsim.api import EvaluationConfig, EvaluationRunError, run_evaluation
from arqsim.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    ProgramWorkTemplate,
)
from arqsim.architecture.recipes import (
    ExactPiAngle,
    InjectionRecipe,
    InjectionStage,
    ProgramRecipeMember,
    ProgramWorkLineage,
    ResourceRef,
    ResourceStateKind,
    build_angle_doubling_recipe,
)
from arqsim.architecture.state import ArchitectureState
from arqsim.compiler import LogicalCompilerValidationError, canonical_compiler_spec
from arqsim.evaluation import (
    BufferSpec,
    EngineSpec,
    EvaluationError,
    EvaluationPolicy,
    ExecutionPlan,
    ExecutionTrace,
    ExecutionTransition,
    ExecutionTransitionKind,
    LogicalMeasurementOutcome,
    LogicalMeasurementResult,
    ProgramDAG,
    ResourceDAG,
    ResourceProcess,
    RuntimeInjectionMode,
    TraceReplayError,
    TraceValidationError,
    compile_and_lower,
    evaluate,
    replay_execution_trace,
    validate_discrete_time_log_document,
)
from arqsim.evaluation.components import (
    ContinuationDecision,
    ContinuationRequest,
    RuntimeComponentDescriptor,
    RuntimeComponentSet,
    StateBoundRuntimeRealizer,
    build_runtime_component_set,
    default_direct_runtime_component_manifest,
)
from arqsim.operation_profiles import (
    OperationLatencyProfile,
    REFERENCE_REACTION_LATENCY_BY_MODALITY_S,
    reference_reaction_latency_profile_v1,
    resolve_resource_protocol_bindings,
    with_effective_arrivals,
)
from arqsim.program import FTCircuit, LogicalLayer, LogicalOperation
from arqsim.report_v1 import render_evaluation_report_v1
from arqsim.schema import semantic_hash
from arqsim.specification import build_architecture_specification


def _compile_plan(*args, **kwargs) -> ExecutionPlan:
    return compile_and_lower(*args, **kwargs)[1]


def _t_recipe() -> InjectionRecipe:
    return InjectionRecipe(
        invocation_id="t0",
        recipe_id="surface_code.t_injection.v1",
        source_layer_index=0,
        source_operation_index=0,
        qubits=(0,),
        stages=(
            InjectionStage(
                index=0,
                resource=ResourceRef(
                    "t_magic",
                    ResourceStateKind.T_MAGIC,
                    "magic",
                    "magic_state",
                ),
                failure_correction="s",
            ),
        ),
        data_mapping={0: "compute/q0"},
        compute_location="compute",
        compute_engine="compute",
    )


def _recipe_templates(
    recipe: InjectionRecipe,
    *,
    attempt_durations_s: tuple[float, ...],
    reaction_duration_s: float,
    correction_duration_s: float,
    measurement_duration_s: float = 0.05,
    initial_recipe_members: tuple[ProgramRecipeMember, ...] | None = None,
    include_initial_measurement: bool = True,
) -> tuple[ProgramWorkTemplate, ...]:
    templates: list[ProgramWorkTemplate] = []
    for stage in recipe.stages:
        member = ProgramRecipeMember(recipe.invocation_id, stage.index)
        if stage.index > 0:
            templates.append(
                ProgramWorkTemplate(
                    recipe_members=(member,),
                    step="injection",
                    opcode=ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=attempt_durations_s[stage.index],
                    qubits=recipe.qubits,
                    consumes={
                        stage.resource.buffer_id: stage.resource.quantity
                    },
                    engines={recipe.compute_engine: 1},
                    required_locations={
                        f"q:{qubit}": recipe.compute_location
                        for qubit in recipe.qubits
                    },
                    target_modules=(recipe.compute_location,),
                )
            )
        if stage.index > 0 or include_initial_measurement:
            templates.append(
                ProgramWorkTemplate(
                    recipe_members=(
                        initial_recipe_members
                        if stage.index == 0
                        and initial_recipe_members is not None
                        else (member,)
                    ),
                    step="measurement",
                    opcode=ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=measurement_duration_s,
                    engines={recipe.compute_engine: 1},
                    target_modules=(recipe.compute_location,),
                    metadata={
                        "implementation_phase": "shared_magic_measurement",
                        "measurement_basis": "z",
                    },
                )
            )
        templates.append(
            ProgramWorkTemplate(
                recipe_members=(member,),
                step="reaction",
                opcode=ArchitectureOpcode.CLASSICAL_REACTION,
                duration_s=reaction_duration_s,
            )
        )
        if stage.failure_correction is not None:
            templates.append(
                ProgramWorkTemplate(
                    recipe_members=(member,),
                    step="correction",
                    opcode=ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=correction_duration_s,
                    qubits=recipe.qubits,
                    engines={recipe.compute_engine: 1},
                    required_locations={
                        f"q:{qubit}": recipe.compute_location
                        for qubit in recipe.qubits
                    },
                    target_modules=(recipe.compute_location,),
                    metadata={
                        "gates": {
                            stage.failure_correction: list(recipe.qubits)
                        }
                    },
                )
            )
    return tuple(templates)


def _t_plan(seed: int, *, unrelated_resource: bool = False) -> ExecutionPlan:
    recipe = _t_recipe()
    buffers = [BufferSpec("magic", 1, "magic_state", module="compute")]
    engines = [EngineSpec("compute"), EngineSpec("factory")]
    processes = [
        ResourceProcess(
            "factory",
            ArchitectureOpcode.PREPARE_MAGIC_STATE,
            duration_s=0.4,
            produces={"magic": 1},
            engines={"factory": 1},
        )
    ]
    if unrelated_resource:
        buffers.append(BufferSpec("aux", 1, "aux_state", module="elsewhere"))
        engines.append(EngineSpec("aux_factory"))
        processes.insert(
            0,
            ResourceProcess(
                "unrelated",
                ArchitectureOpcode.PREPARE_MAGIC_STATE,
                duration_s=0.05,
                produces={"aux": 1},
                engines={"aux_factory": 1},
            ),
        )
    return ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(
            seed=seed,
            runtime_injection_mode=RuntimeInjectionMode.FINITE_STATE_INJECTION_V1,
        ),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=0.5,
                    layer_index=0,
                    qubits=(0,),
                    consumes={"magic": 1},
                    engines={"compute": 1},
                    required_locations={"q:0": "compute"},
                    target_modules=("compute",),
                    implementation_recipes=(recipe,),
                    continuation_templates=_recipe_templates(
                        recipe,
                        attempt_durations_s=(0.5,),
                        reaction_duration_s=0.1,
                        correction_duration_s=0.2,
                    ),
                ),
            )
        ),
        ResourceDAG(tuple(processes)),
        tuple(buffers),
        tuple(engines),
        initial_locations={"q:0": "compute"},
    )


def _program_events(result):
    return [event for event in result.events if event.plane.value == "program"]


def test_t_terminal_decision_names_a_materialized_logical_correction() -> None:
    realizer = StateBoundRuntimeRealizer(
        RuntimeComponentDescriptor(
            role="runtime_realizer",
            component_id="test.state_bound_runtime_realizer.v1",
            provider="tests",
        )
    )

    decision = realizer.continue_after(
        ContinuationRequest(
            lineage=ProgramWorkLineage("program:0", 0),
            recipe=_t_recipe(),
            stage_index=0,
            outcome_bit=1,
        )
    )

    assert decision.kind == "materialized_logical_correction"
    assert decision.correction == "s"


def test_t_injection_branches_are_seeded_typed_and_replayable() -> None:
    success = evaluate(_t_plan(2))
    correction = evaluate(_t_plan(0))

    assert _program_events(success)[1].measurements == {
        "t0:stage:0:bit": 0
    }
    assert [event.program_lineage.step for event in _program_events(success)] == [
        "entangle",
        "measurement",
        "reaction",
    ]
    assert not any(
        event.metadata.get("gates", {}).get("s")
        for event in _program_events(success)
    )

    assert _program_events(correction)[1].measurements == {
        "t0:stage:0:bit": 1
    }
    assert [event.program_lineage.step for event in _program_events(correction)] == [
        "entangle",
        "measurement",
        "reaction",
        "correction",
    ]
    assert _program_events(correction)[-1].metadata["gates"] == {"s": (0,)}
    assert correction.completed_program_instructions == 1
    assert correction.total_latency_s == pytest.approx(1.25)

    plan = _t_plan(0)
    assert replay_execution_trace(correction.trace, plan) == correction.trace.terminal_state
    round_trip = ExecutionTrace.from_dict(correction.trace.to_dict())
    assert round_trip.trace_hash == correction.trace.trace_hash
    assert ExecutionTrace.from_json(correction.trace.to_json()) == round_trip
    with pytest.raises(Exception, match="Duplicate JSON object key"):
        ExecutionTrace.from_json('{"schema_version": 1, "schema_version": 2}')


def test_dynamic_trace_tampering_fails_closed_at_codec_or_replay() -> None:
    plan = _t_plan(0)
    canonical = evaluate(plan).trace

    lineage_tamper = deepcopy(canonical.to_dict())
    for transition in lineage_tamper["transitions"]:
        lineage = transition["program_lineage"]
        if lineage is not None and lineage["step"] == "reaction":
            lineage["recipe_members"][0]["stage_index"] = 999
    lineage_tamper["trace_hash"] = semantic_hash(
        {
            key: value
            for key, value in lineage_tamper.items()
            if key != "trace_hash"
        }
    )
    parsed_lineage_tamper = ExecutionTrace.from_dict(lineage_tamper)
    with pytest.raises(TraceReplayError, match="Unknown continuation recipe"):
        replay_execution_trace(parsed_lineage_tamper, plan)

    measurement_tamper = deepcopy(canonical.to_dict())
    for transition in measurement_tamper["transitions"]:
        lineage = transition["program_lineage"]
        if (
            transition["kind"] == "completion"
            and lineage is not None
            and lineage["step"] == "measurement"
        ):
            transition["measurements"]["t0:stage:0:bit"] = 0
            transition["outcome"]["measurements"][0]["bit"] = 0
    measurement_tamper["trace_hash"] = semantic_hash(
        {
            key: value
            for key, value in measurement_tamper.items()
            if key != "trace_hash"
        }
    )
    parsed_measurement_tamper = ExecutionTrace.from_dict(measurement_tamper)
    with pytest.raises(TraceReplayError, match="frozen recipe"):
        replay_execution_trace(parsed_measurement_tamper, plan)

    continuation_tamper = deepcopy(canonical.to_dict())
    for transition in continuation_tamper["transitions"]:
        lineage = transition["program_lineage"]
        if (
            transition["kind"] == "completion"
            and lineage is not None
            and lineage["step"] == "reaction"
        ):
            transition["continuation"]["activated_work_ids"][0] = (
                "program:0:recipe:t0:stage:0:bogus"
            )
    continuation_tamper["trace_hash"] = semantic_hash(
        {
            key: value
            for key, value in continuation_tamper.items()
            if key != "trace_hash"
        }
    )
    with pytest.raises(TraceValidationError, match="not activated"):
        ExecutionTrace.from_dict(continuation_tamper)

    duration_tamper = deepcopy(canonical.to_dict())
    reaction_event_id = next(
        transition["event_id"]
        for transition in duration_tamper["transitions"]
        if transition["kind"] == "dispatch"
        and transition["program_lineage"] is not None
        and transition["program_lineage"]["step"] == "reaction"
    )
    for transition in duration_tamper["transitions"]:
        if transition["event_id"] != reaction_event_id:
            continue
        shortened_end = transition["start_s"] + 0.05
        transition["end_s"] = shortened_end
        if transition["kind"] == "completion":
            transition["time_s"] = shortened_end
    duration_tamper["trace_hash"] = semantic_hash(
        {
            key: value
            for key, value in duration_tamper.items()
            if key != "trace_hash"
        }
    )
    parsed_duration_tamper = ExecutionTrace.from_dict(duration_tamper)
    with pytest.raises(TraceReplayError, match="duration disagrees"):
        replay_execution_trace(parsed_duration_tamper, plan)

    obsolete = canonical.to_dict()
    obsolete["schema_version"] = "arqsim.execution-trace.v2"
    with pytest.raises(TraceValidationError, match="Unsupported execution-trace"):
        ExecutionTrace.from_dict(obsolete)

    source_lineage = next(
        transition["program_lineage"]
        for transition in canonical.to_dict()["transitions"]
        if transition["program_lineage"] is not None
        and transition["program_lineage"]["parent_event_id"] is None
    )
    source_lineage["parent_event_id"] = 0
    with pytest.raises(ValueError, match="parent event"):
        ProgramWorkLineage.from_dict(source_lineage)


def test_measurement_seed_uses_stable_program_work_identity_not_event_id() -> None:
    baseline = evaluate(_t_plan(2))
    with_unrelated_event = evaluate(_t_plan(2, unrelated_resource=True))
    baseline_events = _program_events(baseline)
    shifted_events = _program_events(with_unrelated_event)
    baseline_source = next(
        event for event in baseline_events if event.program_lineage.step == "entangle"
    )
    shifted_source = next(
        event for event in shifted_events if event.program_lineage.step == "entangle"
    )
    baseline_measurement = next(
        event
        for event in baseline_events
        if event.program_lineage.step == "measurement"
    )
    shifted_measurement = next(
        event
        for event in shifted_events
        if event.program_lineage.step == "measurement"
    )

    assert baseline_source.event_id != shifted_source.event_id
    assert baseline_measurement.event_id != shifted_measurement.event_id
    assert baseline_measurement.measurements
    assert baseline_measurement.measurements == shifted_measurement.measurements
    assert baseline_source.program_lineage.work_id == "program:0"
    assert (
        baseline_measurement.program_lineage.work_id
        == shifted_measurement.program_lineage.work_id
        == "program:0:recipe:t0:stage:0:measurement"
    )


def _two_t_plan() -> ExecutionPlan:
    first = _t_recipe()
    second = replace(
        first,
        invocation_id="t1",
        source_operation_index=1,
        qubits=(1,),
        data_mapping={1: "compute/q1"},
    )
    return ExecutionPlan(
        "two-t-circuit",
        "architecture",
        "latency",
        EvaluationPolicy(
            seed=2,
            trace_level="full",
            runtime_injection_mode=RuntimeInjectionMode.FINITE_STATE_INJECTION_V1,
        ),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=0.5,
                    layer_index=0,
                    qubits=(0, 1),
                    consumes={"magic": 2},
                    engines={"compute": 1},
                    required_locations={"q:0": "compute", "q:1": "compute"},
                    target_modules=("compute",),
                    implementation_recipes=(first, second),
                    continuation_templates=(
                        *_recipe_templates(
                            first,
                            attempt_durations_s=(0.5,),
                            reaction_duration_s=0.1,
                            correction_duration_s=0.2,
                            initial_recipe_members=(
                                ProgramRecipeMember("t0", 0),
                                ProgramRecipeMember("t1", 0),
                            ),
                        ),
                        *_recipe_templates(
                            second,
                            attempt_durations_s=(0.5,),
                            reaction_duration_s=0.1,
                            correction_duration_s=0.2,
                            include_initial_measurement=False,
                        ),
                    ),
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    duration_s=0.1,
                    produces={"magic": 1},
                    engines={"factory": 1},
                ),
            )
        ),
        (BufferSpec("magic", 2, "magic_state", module="compute"),),
        (EngineSpec("compute"), EngineSpec("factory")),
        initial_locations={"q:0": "compute", "q:1": "compute"},
    )


def test_one_source_activates_multiple_recipe_children_in_stable_order() -> None:
    plan = _two_t_plan()

    result = evaluate(plan)
    source = _program_events(result)[0]
    assert source.continuation.activated_work_ids == (
        "program:0:stage:0:shared-measurement",
    )
    measurement = _program_events(result)[1]
    assert measurement.program_lineage.step == "measurement"
    assert tuple(
        member.recipe_invocation_id
        for member in measurement.program_lineage.recipe_members
    ) == ("t0", "t1")
    assert measurement.continuation.activated_work_ids == (
        "program:0:recipe:t0:stage:0:reaction",
        "program:0:recipe:t1:stage:0:reaction",
    )
    reaction_starts = [
        event.start_s
        for event in _program_events(result)
        if event.program_lineage.step == "reaction"
    ]
    assert len(reaction_starts) == 2
    assert reaction_starts[0] == reaction_starts[1]
    assert result.completed_program_instructions == 1
    assert replay_execution_trace(result.trace, plan) == result.trace.terminal_state
    assert validate_discrete_time_log_document(
        result.discrete_time_log,
        result.trace,
        plan,
        "full",
    ) == result.discrete_time_log

    canonical_members = [
        {"recipe_invocation_id": "t0", "stage_index": 0},
        {"recipe_invocation_id": "t1", "stage_index": 0},
    ]
    for forged_members in (
        list(reversed(canonical_members)),
        canonical_members[:1],
    ):
        tampered = deepcopy(result.trace.to_dict())
        source_event_id = next(
            transition["event_id"]
            for transition in tampered["transitions"]
            if transition["kind"] == "dispatch"
            and transition["program_lineage"] is not None
            and transition["program_lineage"]["parent_event_id"] is None
        )
        for transition in tampered["transitions"]:
            if transition["event_id"] == source_event_id:
                transition["program_lineage"]["recipe_members"] = forged_members
        tampered["trace_hash"] = semantic_hash(
            {key: value for key, value in tampered.items() if key != "trace_hash"}
        )
        parsed = ExecutionTrace.from_dict(tampered)
        with pytest.raises(
            TraceReplayError,
            match="Static Program lineage does not match its source recipes",
        ):
            replay_execution_trace(parsed, plan)


@pytest.mark.parametrize("failure", ("wrong_decision", "raise"))
def test_shared_measurement_completion_is_transactional(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    plan = _two_t_plan()
    state = ArchitectureState.from_plan(plan)
    monkeypatch.setattr(
        ArchitectureState,
        "from_plan",
        classmethod(lambda cls, _plan: state),
    )

    base = build_runtime_component_set(
        default_direct_runtime_component_manifest(),
        runtime_instruction_compiler=None,
        runtime_resource_compiler=None,
    )

    class CapturingMeasurementProvider:
        descriptor = RuntimeComponentDescriptor(
            "measurement_provider",
            "tests.logical_measurement.transaction_probe.v1",
            provider="tests",
        )
        request = None

        def resolve(self, request):
            self.request = request
            return LogicalMeasurementResult(
                tuple(
                    LogicalMeasurementOutcome(register_id, 0)
                    for register_id in request.expected_measurements
                )
            )

    class FailingSecondContinuationRealizer:
        descriptor = RuntimeComponentDescriptor(
            "runtime_realizer",
            "tests.runtime_realizer.transaction_probe.v1",
            provider="tests",
        )

        def __init__(self):
            self.calls = 0

        def realize(self, request):
            return base.runtime_realizer.realize(request)

        def continue_after(self, request):
            self.calls += 1
            if self.calls == 1:
                return base.runtime_realizer.continue_after(request)
            if failure == "raise":
                raise RuntimeError("second continuation failed")
            return ContinuationDecision(
                "materialized_logical_correction",
                correction="s",
            )

    provider = CapturingMeasurementProvider()
    realizer = FailingSecondContinuationRealizer()
    components = RuntimeComponentSet(
        runtime_realizer=realizer,
        scheduler=base.scheduler,
        execution_backend=base.execution_backend,
        measurement_provider=provider,
    )
    plan = replace(
        plan,
        provenance={},
        runtime_components=components.manifest.to_dict(),
    )

    constructed_completions: list[int] = []
    original_post_init = ExecutionTransition.__post_init__

    def track_constructed_transition(transition):
        original_post_init(transition)
        if transition.kind == ExecutionTransitionKind.COMPLETION:
            constructed_completions.append(transition.event_id)

    monkeypatch.setattr(
        ExecutionTransition,
        "__post_init__",
        track_constructed_transition,
    )

    expected_error = RuntimeError if failure == "raise" else EvaluationError
    with pytest.raises(expected_error):
        evaluate(plan, runtime_components=components)

    assert provider.request is not None
    measurement_event_id = provider.request.event_id
    before_completion = provider.request.snapshot
    assert realizer.calls == 2
    assert state.snapshot() == before_completion
    assert provider.request.reservation.reservation_id in (
        before_completion.active_reservation_ids
    )
    assert state.engines["compute"].users == 1
    assert measurement_event_id not in constructed_completions


def test_star_angle_doubling_chain_uses_each_typed_attempt_duration() -> None:
    resources = tuple(
        ResourceRef(
            ref_id=name,
            state_kind=ResourceStateKind.RZ_ANGLE,
            buffer_id=name,
            token_kind=f"rz_pi_{numerator}_{denominator}",
            angle=ExactPiAngle(numerator, denominator),
        )
        for name, numerator, denominator in (
            ("theta", 1, 16),
            ("two_theta", 1, 8),
            ("four_theta", 1, 4),
        )
    )
    recipe = build_angle_doubling_recipe(
        invocation_id="star0",
        recipe_id="star.rz_angle_doubling.v1",
        source_layer_index=0,
        source_operation_index=0,
        qubits=(0,),
        resources=resources,
        data_mapping={0: "compute/q0"},
        compute_location="compute",
        compute_engine="compute",
    )
    plan = ExecutionPlan(
        "star-circuit",
        "star-architecture",
        "latency",
        EvaluationPolicy(
            seed=7,
            runtime_injection_mode=RuntimeInjectionMode.FINITE_STATE_INJECTION_V1,
        ),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=0.11,
                    layer_index=0,
                    qubits=(0,),
                    consumes={"theta": 1},
                    engines={"compute": 1},
                    required_locations={"q:0": "compute"},
                    target_modules=("compute",),
                    implementation_recipes=(recipe,),
                    continuation_templates=_recipe_templates(
                        recipe,
                        attempt_durations_s=(0.11, 0.22, 0.33),
                        reaction_duration_s=0.1,
                        correction_duration_s=0.2,
                    ),
                ),
            )
        ),
        ResourceDAG(
            tuple(
                ResourceProcess(
                    f"make_{resource.buffer_id}",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    produces={resource.buffer_id: 1},
                    engines={"factory": 1},
                )
                for resource in resources
            )
        ),
        tuple(
            BufferSpec(
                resource.buffer_id,
                1,
                resource.token_kind,
                module="compute",
            )
            for resource in resources
        ),
        (EngineSpec("compute"), EngineSpec("factory", capacity=3)),
        initial_locations={"q:0": "compute"},
    )

    result = evaluate(plan)
    events = _program_events(result)
    assert [event.program_lineage.step for event in events] == [
        "entangle",
        "measurement",
        "reaction",
        "injection",
        "measurement",
        "reaction",
        "injection",
        "measurement",
        "reaction",
        "correction",
    ]
    injection_durations = [
        event.end_s - event.start_s
        for event in events
        if event.program_lineage.step == "injection"
    ]
    assert events[0].end_s - events[0].start_s == pytest.approx(0.11)
    assert injection_durations == pytest.approx([0.22, 0.33])
    assert events[-1].metadata["gates"] == {"s": (0,)}
    assert result.total_latency_s == pytest.approx(1.31)
    assert replay_execution_trace(result.trace, plan) == result.trace.terminal_state

    early_plan = replace(
        plan,
        policy=EvaluationPolicy(
            seed=6,
            runtime_injection_mode=RuntimeInjectionMode.FINITE_STATE_INJECTION_V1,
        ),
        provenance={},
    )
    early = evaluate(early_plan)
    early_events = _program_events(early)
    assert [event.program_lineage.step for event in early_events] == [
        "entangle",
        "measurement",
        "reaction",
        "injection",
        "measurement",
        "reaction",
    ]
    assert early_events[1].measurements == {"star0:stage:0:bit": 1}
    assert early_events[4].measurements == {"star0:stage:1:bit": 0}
    assert early.metrics["buffer_tokens_consumed"] == {
        "theta": 1,
        "two_theta": 1,
    }
    assert not any(
        event.program_lineage.stage_index == 2 for event in early_events
    )
    assert replay_execution_trace(early.trace, early_plan) == early.trace.terminal_state


def test_angle_resources_must_use_distinguishable_installed_pools() -> None:
    with pytest.raises(ValueError, match="distinct installed buffer"):
        build_angle_doubling_recipe(
            invocation_id="bad",
            recipe_id="star.bad.v1",
            source_layer_index=0,
            source_operation_index=0,
            qubits=(0,),
            resources=(
                ResourceRef(
                    "theta",
                    ResourceStateKind.RZ_ANGLE,
                    "generic_rz",
                    "generic_rz",
                    angle=ExactPiAngle(1, 8),
                ),
                ResourceRef(
                    "two_theta",
                    ResourceStateKind.RZ_ANGLE,
                    "generic_rz",
                    "generic_rz",
                    angle=ExactPiAngle(1, 4),
                ),
            ),
            data_mapping={0: "compute/q0"},
            compute_location="compute",
            compute_engine="compute",
        )


def _lower_one_gate(
    name: str,
    latency: OperationLatencyProfile,
    policy: EvaluationPolicy | None = None,
) -> ExecutionPlan:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(0, (LogicalOperation("gate", name, qubits=(0,)),)),
        ),
    )
    specification = build_architecture_specification(circuit, "1.1")
    bindings = resolve_resource_protocol_bindings(specification, latency)
    resolved = with_effective_arrivals(latency, bindings)
    return _compile_plan(
        circuit,
        specification,
        resolved,
        policy or EvaluationPolicy(),
        compiler_spec=canonical_compiler_spec(specification),
        resource_protocol_bindings=bindings,
    )


def test_runtime_injection_policy_separates_semantic_mode_from_timing_data() -> None:
    black_box = _lower_one_gate("t", OperationLatencyProfile())
    timing_only = _lower_one_gate(
        "t",
        OperationLatencyProfile(
            reaction_latency_by_modality_s={"neutral_atom": 0.0}
        ),
    )
    explicit_zero = _lower_one_gate(
        "t",
        OperationLatencyProfile(
            reaction_latency_by_modality_s={"neutral_atom": 0.0}
        ),
        EvaluationPolicy(
            runtime_injection_mode=RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
        ),
    )

    assert not any(
        instruction.implementation_recipes
        for instruction in black_box.program_dag.instructions
    )
    assert not any(
        instruction.opcode == ArchitectureOpcode.CLASSICAL_REACTION
        for instruction in black_box.program_dag.instructions
    )
    assert not any(
        instruction.implementation_recipes
        for instruction in timing_only.program_dag.instructions
    )
    assert (
        timing_only.provenance["runtime_injection_recipe_mode"]
        == "black_box"
    )
    recipes = tuple(
        recipe
        for instruction in explicit_zero.program_dag.instructions
        for recipe in instruction.implementation_recipes
    )
    assert len(recipes) == 1
    reaction_templates = tuple(
        template
        for instruction in explicit_zero.program_dag.instructions
        for template in instruction.continuation_templates
        if template.step == "reaction"
    )
    assert len(reaction_templates) == 1
    assert reaction_templates[0].duration_s == 0.0
    assert (
        explicit_zero.provenance["runtime_injection_recipe_mode"]
        == "finite_state_injection_v1"
    )
    with pytest.raises(ValueError, match="requires an explicit reaction latency"):
        _lower_one_gate(
            "t",
            OperationLatencyProfile(),
            EvaluationPolicy(
                runtime_injection_mode=(
                    RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
                )
            ),
        )
    with pytest.raises(
        LogicalCompilerValidationError,
        match="support only the T convention",
    ) as exc_info:
        _lower_one_gate(
            "tdg",
            OperationLatencyProfile(
                reaction_latency_by_modality_s={"neutral_atom": 0.0}
            ),
            EvaluationPolicy(
                runtime_injection_mode=(
                    RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
                )
                ),
            )
    assert exc_info.value.details == {
        "source_layer": 0,
        "operation_indices": [0],
        "unsupported_operations": ["tdg"],
    }


def test_runtime_injection_policy_is_a_strict_plan_contract() -> None:
    plan = _t_plan(2)
    assert (
        EvaluationPolicy.from_dict(plan.policy.to_dict())
        == plan.policy
    )

    document = plan.to_dict()
    document["policy"].pop("runtime_injection_mode")
    with pytest.raises(ValueError, match="Missing execution-plan policy fields"):
        ExecutionPlan.from_dict(document)

    document = plan.to_dict()
    document["policy"]["runtime_injection_mode"] = "implicit_from_latency"
    with pytest.raises(ValueError, match="Unsupported runtime injection mode"):
        ExecutionPlan.from_dict(document)

    with pytest.raises(ValueError, match="implementation recipes require"):
        replace(plan, policy=EvaluationPolicy(), provenance={})

    recipe = plan.program_dag.instructions[0].implementation_recipes[0]
    recipe_document = recipe.to_dict()
    assert "reaction_duration_s" not in recipe_document
    assert "correction_duration_s" not in recipe_document
    assert "attempt_duration_s" not in recipe_document["stages"][0]
    recipe_document["reaction_duration_s"] = 0.1
    with pytest.raises(ValueError, match="Unknown injection recipe fields"):
        InjectionRecipe.from_dict(recipe_document)

    host = plan.program_dag.instructions[0]
    with pytest.raises(ValueError, match="exactly cover recipe continuations"):
        replace(host, continuation_templates=host.continuation_templates[:-1])


def test_named_reference_reaction_profile_is_explicit_and_nondefault() -> None:
    assert not OperationLatencyProfile().reaction_latency_by_modality_s
    profile = reference_reaction_latency_profile_v1()
    assert dict(profile.reaction_latency_by_modality_s) == dict(
        REFERENCE_REACTION_LATENCY_BY_MODALITY_S
    )
    assert profile.provenance["reaction_latency_profile"] == (
        "reference_reaction_latency_profile_v1"
    )


@pytest.mark.parametrize(
    ("operations", "num_qubits"),
    (
        (
            (
                LogicalOperation("gate", "h", qubits=(0,)),
                LogicalOperation("gate", "t", qubits=(1,)),
            ),
            2,
        ),
        (
            (
                LogicalOperation("gate", "cx", qubits=(0, 1)),
                LogicalOperation("gate", "t", qubits=(2,)),
            ),
            3,
        ),
    ),
    ids=("h-and-t", "cx-and-t"),
)
def test_finite_injection_fails_closed_for_mixed_compute_units(
    operations: tuple[LogicalOperation, ...],
    num_qubits: int,
) -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=num_qubits,
        num_clbits=0,
        layers=(LogicalLayer(0, operations),),
    )

    with pytest.raises(
        EvaluationRunError,
        match="mixes T injections with other logical operations",
    ) as exc_info:
        run_evaluation(
            circuit,
            EvaluationConfig(
                profile_id="1.1",
                latency_profile=reference_reaction_latency_profile_v1(),
                execution_policy=EvaluationPolicy(
                    runtime_injection_mode=(
                        RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
                    )
                ),
                fidelity_profile=None,
            ),
        )
    assert isinstance(exc_info.value.__cause__, LogicalCompilerValidationError)
    assert exc_info.value.__cause__.details["source_layer"] == 0


def test_neutral_atom_expansion_is_one_shared_additive_batch() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=2,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (
                    LogicalOperation("gate", "t", qubits=(0,)),
                    LogicalOperation("gate", "t", qubits=(1,)),
                ),
            ),
        ),
    )
    latency = reference_reaction_latency_profile_v1()
    black_box = run_evaluation(
        circuit,
        EvaluationConfig(
            profile_id="1.1",
            latency_profile=latency,
            execution_policy=EvaluationPolicy(seed=2, trace_level="full"),
            fidelity_profile=None,
        ),
    )
    expanded = run_evaluation(
        circuit,
        EvaluationConfig(
            profile_id="1.1",
            latency_profile=latency,
            execution_policy=EvaluationPolicy(
                seed=2,
                trace_level="full",
                runtime_injection_mode=(
                    RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
                ),
            ),
            fidelity_profile=None,
        ),
    )

    aggregate = next(
        event
        for event in black_box.execution_trace.events
        if event.metadata.get("gates", {}).get("t")
    )
    program_events = tuple(
        event
        for event in expanded.execution_trace.events
        if event.plane.value == "program"
    )
    entangles = tuple(
        event
        for event in program_events
        if event.program_lineage.step == "entangle"
    )
    measurements = tuple(
        event
        for event in program_events
        if event.program_lineage.step == "measurement"
    )
    assert len(entangles) == len(measurements) == 1
    entangle = entangles[0]
    measurement = measurements[0]
    assert len(entangle.program_lineage.recipe_members) == 2
    assert measurement.program_lineage.recipe_members == (
        entangle.program_lineage.recipe_members
    )
    assert entangle.end_s - entangle.start_s + (
        measurement.end_s - measurement.start_s
    ) == pytest.approx(aggregate.end_s - aggregate.start_s)
    assert len(entangle.consumed_tokens["magic_compute"]) == 2
    assert not measurement.consumed_tokens
    assert len(measurement.measurements) == 2
    assert measurement.start_s == entangle.end_s


def test_superconducting_overlap_aggregate_fails_closed_only_for_expansion() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "t", qubits=(0,)),),
            ),
        ),
    )
    latency = reference_reaction_latency_profile_v1()
    black_box = run_evaluation(
        circuit,
        EvaluationConfig(
            profile_id="1.2",
            latency_profile=latency,
            fidelity_profile=None,
        ),
    )
    assert black_box.execution_trace.total_latency_s > 0

    with pytest.raises(EvaluationRunError, match="overlap-aggregated"):
        run_evaluation(
            circuit,
            EvaluationConfig(
                profile_id="1.2",
                latency_profile=latency,
                execution_policy=EvaluationPolicy(
                    runtime_injection_mode=(
                        RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
                    )
                ),
                fidelity_profile=None,
            ),
        )


def test_report_v1_fails_fast_instead_of_erasing_dynamic_lineage() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(0, (LogicalOperation("gate", "t", qubits=(0,)),)),
        ),
    )
    report = run_evaluation(
        circuit,
        EvaluationConfig(
            profile_id="1.1",
            latency_profile=OperationLatencyProfile(
                reaction_latency_by_modality_s={"neutral_atom": 0.0}
            ),
            execution_policy=EvaluationPolicy(
                runtime_injection_mode=(
                    RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
                )
            ),
            fidelity_profile=None,
        ),
    )

    assert report.to_dict()["schema_version"] == "arqsim.evaluation-report.v2"
    with pytest.raises(ValueError, match="cannot represent dynamic Program"):
        render_evaluation_report_v1(report)


def test_t_recipe_rejects_a_non_s_terminal_correction() -> None:
    recipe = _t_recipe()
    with pytest.raises(ValueError, match="terminates in S"):
        replace(
            recipe,
            stages=(replace(recipe.stages[0], failure_correction="z"),),
        )
