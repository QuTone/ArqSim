from __future__ import annotations

from dataclasses import replace
import math

import pytest

from arqsim.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    ProgramWorkTemplate,
)
from arqsim.architecture.recipes import (
    InjectionRecipe,
    InjectionStage,
    ProgramRecipeMember,
    ResourceRef,
    ResourceStateKind,
)
from arqsim.evaluation import (
    BufferSpec,
    EngineSpec,
    EvaluationPolicy,
    ExecutionPlan,
    ProgramDAG,
    ResourceDAG,
    ResourceProcess,
    RuntimeInjectionMode,
    TraceReplayError,
    estimate_fidelity,
    evaluate,
    qubit_exposure,
    replay_execution_trace,
)
from arqsim.operation_profiles import (
    FidelityProfile,
    ResourceStateFidelityModel,
)


ENTANGLE_DURATION_S = 0.2
MEASUREMENT_DURATION_S = 0.3
REACTION_DURATION_S = 0.1
CORRECTION_DURATION_S = 0.4

T_FAILURE = 0.1
S_FAILURE = 0.2
MAGIC_OUTPUT_FAILURE = 0.01
IDLE_FAILURE_RATE_PER_S = 0.05


def _recipe(invocation_id: str, qubit: int) -> InjectionRecipe:
    return InjectionRecipe(
        invocation_id=invocation_id,
        recipe_id="surface_code.t_injection.v1",
        source_layer_index=0,
        source_operation_index=qubit,
        qubits=(qubit,),
        stages=(
            InjectionStage(
                index=0,
                resource=ResourceRef(
                    ref_id=f"{invocation_id}_magic",
                    state_kind=ResourceStateKind.T_MAGIC,
                    buffer_id="magic",
                    token_kind="magic_state",
                ),
                failure_correction="s",
            ),
        ),
        data_mapping={qubit: f"compute/q{qubit}"},
        compute_location="compute",
        compute_engine="compute",
    )


def _templates(
    recipes: tuple[InjectionRecipe, ...],
) -> tuple[ProgramWorkTemplate, ...]:
    members = tuple(
        ProgramRecipeMember(recipe.invocation_id, 0) for recipe in recipes
    )
    templates = [
        ProgramWorkTemplate(
            recipe_members=members,
            step="measurement",
            opcode=ArchitectureOpcode.EXECUTE_COMPUTE,
            duration_s=MEASUREMENT_DURATION_S,
            engines={"compute": 1},
            target_modules=("compute",),
            metadata={
                "implementation_phase": "shared_magic_measurement",
                "measurement_basis": "z",
            },
        )
    ]
    for recipe, member in zip(recipes, members):
        templates.extend(
            (
                ProgramWorkTemplate(
                    recipe_members=(member,),
                    step="reaction",
                    opcode=ArchitectureOpcode.CLASSICAL_REACTION,
                    duration_s=REACTION_DURATION_S,
                ),
                ProgramWorkTemplate(
                    recipe_members=(member,),
                    step="correction",
                    opcode=ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=CORRECTION_DURATION_S,
                    qubits=recipe.qubits,
                    engines={"compute": 1},
                    required_locations={
                        f"q:{qubit}": "compute" for qubit in recipe.qubits
                    },
                    target_modules=("compute",),
                    metadata={"gates": {"s": list(recipe.qubits)}},
                ),
            )
        )
    return tuple(templates)


def _plan(seed: int, *, qubit_count: int = 1) -> ExecutionPlan:
    qubits = tuple(range(qubit_count))
    recipes = tuple(_recipe(f"t{qubit}", qubit) for qubit in qubits)
    return ExecutionPlan(
        "runtime-injection-fidelity-circuit",
        "runtime-injection-fidelity-architecture",
        "runtime-injection-fidelity-latency",
        EvaluationPolicy(
            seed=seed,
            runtime_injection_mode=RuntimeInjectionMode.FINITE_STATE_INJECTION_V1,
        ),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=ENTANGLE_DURATION_S,
                    layer_index=0,
                    qubits=qubits,
                    consumes={"magic": qubit_count},
                    engines={"compute": 1},
                    required_locations={
                        f"q:{qubit}": "compute" for qubit in qubits
                    },
                    target_modules=("compute",),
                    implementation_recipes=recipes,
                    continuation_templates=_templates(recipes),
                    metadata={
                        "gates": {"t": list(qubits)},
                        "implementation_phase": "shared_logical_entangle_cx",
                    },
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    duration_s=0.0,
                    produces={"magic": 1},
                    engines={"factory": 1},
                ),
            )
        ),
        (BufferSpec("magic", qubit_count, "magic_state", module="compute"),),
        (EngineSpec("compute"), EngineSpec("factory")),
        initial_locations={f"q:{qubit}": "compute" for qubit in qubits},
    )


def _profile() -> FidelityProfile:
    return FidelityProfile(
        operation_failure_probability={
            ArchitectureOpcode.EXECUTE_COMPUTE.value: 0.0,
            ArchitectureOpcode.CLASSICAL_REACTION.value: 0.0,
            ArchitectureOpcode.PREPARE_MAGIC_STATE.value: 0.0,
        },
        logical_operation_failure_probability={
            "t": T_FAILURE,
            "s": S_FAILURE,
            # These deliberately non-zero sentinels prove that implementation
            # CX/MZ phases do not masquerade as source-level logical gates.
            "cx": 0.3,
            "measure": 0.4,
        },
        idle_failure_rate_per_s={"compute": IDLE_FAILURE_RATE_PER_S},
        resource_state_models={
            "magic_state": ResourceStateFidelityModel(
                resource_kind="magic_state",
                output_failure_probability=MAGIC_OUTPUT_FAILURE,
            )
        },
    )


def _program_completions(result):
    return [
        transition
        for transition in result.transitions
        if transition.kind.value == "completion"
        and transition.plane.value == "program"
    ]


@pytest.mark.parametrize(
    ("seed", "expected_bit", "expected_steps", "expected_active_s"),
    (
        (2, 0, ("entangle", "measurement", "reaction"), 0.2),
        (0, 1, ("entangle", "measurement", "reaction", "correction"), 0.6),
    ),
)
def test_t_parent_is_the_single_fidelity_authority_and_idle_is_phase_causal(
    seed: int,
    expected_bit: int,
    expected_steps: tuple[str, ...],
    expected_active_s: float,
) -> None:
    plan = _plan(seed)
    result = evaluate(plan)
    completions = _program_completions(result)

    assert tuple(item.program_lineage.step for item in completions) == expected_steps
    assert completions[0].metadata["gates"] == {"t": (0,)}
    measurement = completions[1]
    reaction = completions[2]
    assert measurement.measurements == {"t0:stage:0:bit": expected_bit}
    assert "gates" not in measurement.metadata
    assert "gates" not in reaction.metadata
    if expected_bit:
        assert completions[-1].metadata["gates"] == {"s": (0,)}

    estimate = estimate_fidelity(result, _profile(), plan=plan)
    expected_counts = {"t": 1}
    if expected_bit:
        expected_counts["s"] = 1
    assert estimate.logical_operation_counts == expected_counts
    assert "cx" not in estimate.log_success_by_logical_operation
    assert "measure" not in estimate.log_success_by_logical_operation
    assert estimate.log_success_by_logical_operation["t"] == pytest.approx(
        math.log1p(-T_FAILURE)
    )
    if expected_bit:
        assert estimate.log_success_by_logical_operation["s"] == pytest.approx(
            math.log1p(-S_FAILURE)
        )

    exposure = qubit_exposure(result)
    assert exposure.active_by_qubit["q:0"] == pytest.approx(expected_active_s)
    assert exposure.idle_by_qubit["q:0"] == pytest.approx(
        MEASUREMENT_DURATION_S + REACTION_DURATION_S
    )
    assert exposure.conflict_free
    assert exposure.time_conserved
    assert estimate.log_success_by_idle_location["compute"] == pytest.approx(
        -IDLE_FAILURE_RATE_PER_S
        * (MEASUREMENT_DURATION_S + REACTION_DURATION_S)
    )

    expected_success = (
        (1.0 - MAGIC_OUTPUT_FAILURE)
        * (1.0 - T_FAILURE)
        * math.exp(
            -IDLE_FAILURE_RATE_PER_S
            * (MEASUREMENT_DURATION_S + REACTION_DURATION_S)
        )
    )
    if expected_bit:
        expected_success *= 1.0 - S_FAILURE
    assert estimate.success_probability == pytest.approx(expected_success)
    assert estimate.complete_coverage


def test_two_t_shared_phases_do_not_invent_logical_cx_or_measurement() -> None:
    plan = _plan(5, qubit_count=2)
    result = evaluate(plan)
    completions = _program_completions(result)
    measurement = next(
        item for item in completions if item.program_lineage.step == "measurement"
    )
    correction_count = sum(measurement.measurements.values())

    assert completions[0].program_lineage.step == "entangle"
    assert len(completions[0].program_lineage.recipe_members) == 2
    assert completions[0].metadata["gates"] == {"t": (0, 1)}
    assert len(measurement.program_lineage.recipe_members) == 2
    assert "gates" not in measurement.metadata
    assert all(
        "gates" not in item.metadata
        for item in completions
        if item.program_lineage.step == "reaction"
    )

    estimate = estimate_fidelity(result, _profile(), plan=plan)
    expected_counts = {"t": 2}
    if correction_count:
        expected_counts["s"] = correction_count
    assert estimate.logical_operation_counts == expected_counts
    assert "cx" not in estimate.logical_operation_counts
    assert "measure" not in estimate.logical_operation_counts
    assert estimate.log_success_by_logical_operation["t"] == pytest.approx(
        2 * math.log1p(-T_FAILURE)
    )
    if correction_count:
        assert estimate.log_success_by_logical_operation["s"] == pytest.approx(
            correction_count * math.log1p(-S_FAILURE)
        )

    exposure = qubit_exposure(result)
    assert exposure.active_total == pytest.approx(
        2 * ENTANGLE_DURATION_S
        + correction_count * CORRECTION_DURATION_S
    )
    assert exposure.idle_total == pytest.approx(
        2 * (MEASUREMENT_DURATION_S + REACTION_DURATION_S)
        + correction_count * CORRECTION_DURATION_S
    )
    assert exposure.conflict_free
    assert exposure.time_conserved


def test_parameterized_child_fidelity_cannot_borrow_its_source_qubits() -> None:
    plan = _plan(0)
    profile = replace(
        _profile(),
        operation_failure_models={
            "CLASSICAL_REACTION": {
                "kind": "independent_per_logical_qubit",
                "failure_probability": 0.1,
            }
        },
    )
    # The T source operates a data qubit, but its reaction template has no
    # logical-qubit payload. A per-qubit reaction model lacks required context.
    with pytest.raises(ValueError, match="non-empty typed item payload"):
        estimate_fidelity(evaluate(plan), profile, plan=plan)


def test_implementation_child_cannot_forge_logical_gate_authority() -> None:
    plan = _plan(3)
    result = evaluate(plan)
    measurement_event_ids = {
        item.event_id
        for item in result.transitions
        if item.program_lineage is not None
        and item.program_lineage.step == "measurement"
    }
    forged_transitions = tuple(
        replace(
            item,
            metadata={**dict(item.metadata), "gates": {"cx": [0]}},
        )
        if item.event_id in measurement_event_ids
        else item
        for item in result.transitions
    )
    forged_trace = replace(result.trace, transitions=forged_transitions)
    forged_result = replace(result, trace=forged_trace)

    with pytest.raises(TraceReplayError, match="metadata 'gates'"):
        replay_execution_trace(forged_trace, plan)
    with pytest.raises(
        ValueError,
        match="Implementation-only Program work cannot claim logical gate",
    ):
        estimate_fidelity(forged_result, _profile(), plan=plan)
