from __future__ import annotations

from types import SimpleNamespace

import pytest

from arqsim.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
)
from arqsim.architecture.state import (
    ArchitectureState,
)
from arqsim.evaluation import (
    BufferSpec,
    EngineSpec,
    EvaluationAnalysis,
    EvaluationPolicy,
    ExecutionPlan,
    ProgramDAG,
    ResourceDAG,
    ResourceProcess,
    engine_utilization,
    estimate_fidelity,
    evaluate,
    exclusive_time_breakdown,
    qubit_exposure,
)
from arqsim.operation_profiles import FidelityProfile


def _transition(
    transition_id: int,
    kind: str,
    time_s: float,
    event_id: int,
    *,
    plane: str,
    opcode: str,
    start_s: float,
    end_s: float,
    engines: dict[str, int] | None = None,
    qubits: tuple[int, ...] = (),
    gates: dict[str, list[int]] | None = None,
    completion_locations: dict[str, str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        transition_id=transition_id,
        kind=kind,
        time_s=time_s,
        event_id=event_id,
        plane=plane,
        opcode=opcode,
        start_s=start_s,
        end_s=end_s,
        engines=engines or {},
        metadata={"qubits": qubits, "gates": gates or {}},
        consumed_tokens={},
        completion_locations=completion_locations or {},
    )


def _trace_result() -> SimpleNamespace:
    transitions = (
        _transition(
            0,
            "dispatch",
            0.0,
            0,
            plane="program",
            opcode="EXECUTE_COMPUTE",
            start_s=0.0,
            end_s=1.0,
            engines={"compute": 1},
            qubits=(0,),
            gates={"h": [0]},
        ),
        _transition(
            1,
            "completion",
            1.0,
            0,
            plane="program",
            opcode="EXECUTE_COMPUTE",
            start_s=0.0,
            end_s=1.0,
            engines={"compute": 1},
            qubits=(0,),
            gates={"h": [0]},
            completion_locations={"q:0": "memory"},
        ),
        # This Resource reservation is still running at the Program horizon.
        _transition(
            2,
            "dispatch",
            1.0,
            1,
            plane="resource",
            opcode="PREPARE_MAGIC_STATE",
            start_s=1.0,
            end_s=3.0,
            engines={"factory": 1},
        ),
        _transition(
            3,
            "dispatch",
            1.0,
            2,
            plane="program",
            opcode="EXECUTE_COMPUTE",
            start_s=1.0,
            end_s=2.0,
            engines={"compute": 1},
            qubits=(1,),
            gates={"h": [1]},
        ),
        _transition(
            4,
            "completion",
            2.0,
            2,
            plane="program",
            opcode="EXECUTE_COMPUTE",
            start_s=1.0,
            end_s=2.0,
            engines={"compute": 1},
            qubits=(1,),
            gates={"h": [1]},
        ),
    )
    trace = SimpleNamespace(
        total_latency_s=2.0,
        initial_state=SimpleNamespace(
            locations={"q:0": "compute", "q:1": "compute"}
        ),
        transitions=transitions,
        terminal_inflight=(transitions[2],),
    )
    return SimpleNamespace(
        trace=trace,
        total_latency_s=2.0,
        metrics=_PoisonMetrics(),
    )


class _PoisonMetrics:
    def get(self, *_args, **_kwargs):
        raise AssertionError("Analyzer must not read legacy Engine metrics")


def _inflight_plan(trace_level: str) -> ExecutionPlan:
    return ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level=trace_level, seed=7),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=0.5,
                    qubits=(0,),
                    consumes={"magic": 1},
                    engines={"compute": 1},
                    metadata={"gates": {"h": [0]}},
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
        initial_locations={"q:0": "compute"},
    )


def test_qubit_exposure_is_trace_derived_and_conserves_time() -> None:
    exposure = qubit_exposure(_trace_result())

    assert exposure.logical_qubit_count == 2
    assert exposure.active_by_opcode == {"EXECUTE_COMPUTE": pytest.approx(2.0)}
    assert exposure.active_by_qubit == {
        "q:0": pytest.approx(1.0),
        "q:1": pytest.approx(1.0),
    }
    assert exposure.idle_by_location == {
        "compute": pytest.approx(1.0),
        "memory": pytest.approx(1.0),
    }
    assert exposure.classified_total == pytest.approx(4.0)
    assert exposure.time_conserved
    assert exposure.conflict_free


def test_fidelity_ignores_poisoned_legacy_engine_metrics() -> None:
    result = _trace_result()
    profile = FidelityProfile(
        operation_failure_probability={"EXECUTE_COMPUTE": 0.01},
        logical_operation_failure_probability={"h": 0.02},
        idle_failure_rate_per_s={"compute": 0.03, "memory": 0.04},
    )

    estimate = estimate_fidelity(result, profile)

    assert estimate.idle_cycles_by_location == {}
    assert estimate.log_success_by_idle_location == pytest.approx(
        {"compute": -0.03, "memory": -0.04}
    )
    assert estimate.complete_coverage


def test_terminal_inflight_engine_time_is_clipped_to_program_horizon() -> None:
    plan = SimpleNamespace(
        engines=(EngineSpec("compute"), EngineSpec("factory")),
    )

    utilization = engine_utilization(_trace_result(), plan)

    assert utilization == pytest.approx({"compute": 1.0, "factory": 0.5})


def test_summary_and_full_share_causal_analysis_facts() -> None:
    summary_plan = _inflight_plan("summary")
    full_plan = _inflight_plan("full")
    summary = evaluate(summary_plan)
    full = evaluate(full_plan)

    assert [item.to_dict() for item in summary.trace.transitions] == [
        item.to_dict() for item in full.trace.transitions
    ]
    assert summary.trace.initial_state == full.trace.initial_state
    assert summary.trace.terminal_state == full.trace.terminal_state
    assert [item.to_dict() for item in summary.trace.terminal_inflight] == [
        item.to_dict() for item in full.trace.terminal_inflight
    ]
    assert not summary.discrete_time_log
    assert full.discrete_time_log
    assert qubit_exposure(summary).to_dict() == qubit_exposure(full).to_dict()
    assert engine_utilization(summary, summary_plan) == pytest.approx(
        engine_utilization(full, full_plan)
    )


def test_reanalysis_does_not_reinvoke_engine_or_architecture_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import arqsim.evaluation.engine as engine_module

    plan = _inflight_plan("summary")
    result = evaluate(plan)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Analyzer cannot re-enter execution state")

    monkeypatch.setattr(engine_module, "evaluate", forbidden)
    monkeypatch.setattr(ArchitectureState, "snapshot", forbidden)

    first_exposure = qubit_exposure(result).to_dict()
    second_exposure = qubit_exposure(result).to_dict()
    first_utilization = engine_utilization(result, plan)
    second_utilization = engine_utilization(result, plan)
    assert first_exposure == second_exposure
    assert first_utilization == second_utilization


def test_analysis_outputs_are_deeply_immutable() -> None:
    exposure = qubit_exposure(_trace_result())
    estimate = estimate_fidelity(
        _trace_result(),
        FidelityProfile(idle_failure_rate_per_s={"compute": 0.01, "memory": 0.01}),
    )
    analysis = EvaluationAnalysis(
        exclusive_time_s={"compute": 2.0},
        engine_utilization={"compute": 1.0},
        buffer_occupancy={"magic": {"mean_ready": 0.5}},
        physical_space_qubits={"compute": 10.0},
        fidelity_negative_log_success={"idle_compute": 0.1},
        unavailable={"other": "not retained"},
    )

    with pytest.raises(TypeError):
        exposure.idle_by_location["compute"] = 0.0
    with pytest.raises(TypeError):
        estimate.log_success_by_idle_location["compute"] = 0.0
    with pytest.raises(TypeError):
        analysis.buffer_occupancy["magic"]["mean_ready"] = 0.0


def test_stall_focus_preserves_committed_dispatch_order() -> None:
    log = (
        {
            "time_s": 0.0,
            "frontier_after": {
                "waiting": [
                    {
                        "instruction_id": 7,
                        "resource_blockers": [
                            "buffer_empty:magic_compute:0/1"
                        ],
                    },
                    {
                        "instruction_id": 2,
                        "resource_blockers": ["buffer_empty:bell:link:0/1"],
                    },
                ],
                "running": [],
            },
            "architecture_state_after": {"buffers": {}},
        },
        {
            "time_s": 1.0,
            "dispatched": [
                {"plane": "program", "instruction_id": 7},
                {"plane": "program", "instruction_id": 2},
            ],
            "frontier_after": {"waiting": [], "running": []},
            "architecture_state_after": {"buffers": {}},
        },
    )
    result = SimpleNamespace(discrete_time_log=log, total_latency_s=1.0)

    assert exclusive_time_breakdown(result) == {
        "magic_state_supply_stall": pytest.approx(1.0)
    }
