"""Utilities for the small pre-refactor runtime behavior baselines."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping

from heteqsys.api import EvaluationConfig, EvaluationReport, run_evaluation
from heteqsys.evaluation import EvaluationPolicy
from heteqsys.operation_profiles import ArrivalDistribution, OperationLatencyProfile
from heteqsys.program import FTCircuit, load_ft_workload
from heteqsys.schema import normalize_json, semantic_hash


BASELINE_SCHEMA_VERSION = "arqsim.behavior-baseline.v1"
BASELINE_ROOT = Path(__file__).parent / "fixtures" / "behavior_baselines"


@dataclass(frozen=True)
class BehaviorBaselineCase:
    id: str
    representation: str
    profile_id: str
    workflow_id: str
    purpose: str

    @property
    def directory(self) -> Path:
        return BASELINE_ROOT / self.id


CASES = (
    BehaviorBaselineCase(
        id="clifford_t_remote_magic_v1",
        representation="clifford_t",
        profile_id="1.3",
        workflow_id="step2-clifford-t-v1",
        purpose=(
            "Remote magic/Bell production, cold-start wait, token forwarding, "
            "and dispatch-time T routing/binding."
        ),
    ),
    BehaviorBaselineCase(
        id="pbc_magic_measurement_v1",
        representation="pbc",
        profile_id="1.2",
        workflow_id="step2-pbc-v1",
        purpose=(
            "Local magic production/delivery, PBC rotation and measurement, "
            "runtime route binding, and the current Program-completion horizon."
        ),
    ),
)
CASE_BY_ID = {case.id: case for case in CASES}


def baseline_config(case: BehaviorBaselineCase) -> EvaluationConfig:
    """Return the explicit source configuration used to refresh input JSON."""

    arrival = ArrivalDistribution(
        kind="deterministic",
        mean_interval_s=0.005,
        success_probability=1.0,
        trace_intervals_s=(),
        repeat_trace=True,
        initial_delay_s=0.0,
        initial_delay_samples=0,
    )
    latency = OperationLatencyProfile(
        magic_state_arrival=arrival,
        bell_pair_arrival=arrival,
    )
    return EvaluationConfig(
        profile_id=case.profile_id,
        workflow_id=case.workflow_id,
        latency_profile=latency,
        evaluation_policy=EvaluationPolicy(trace_level="full", seed=0),
    )


def refresh_case_inputs(case: BehaviorBaselineCase) -> None:
    """Materialize normalized workload/config inputs after an explicit review."""

    circuit = load_ft_workload(
        case.directory / "workload.qasm", case.representation
    )
    _write_json(case.directory / "workload.json", circuit.to_dict())
    _write_json(case.directory / "config.json", baseline_config(case).to_dict())


def load_case_inputs(
    case: BehaviorBaselineCase,
) -> tuple[FTCircuit, EvaluationConfig]:
    circuit = FTCircuit.from_json(
        (case.directory / "workload.json").read_text(encoding="utf-8")
    )
    config = EvaluationConfig.from_json(
        (case.directory / "config.json").read_text(encoding="utf-8")
    )
    return circuit, config


_RUNTIME_METADATA_KEYS = (
    "amount",
    "batch_amount",
    "destination_slots",
    "direction",
    "dispatch_policy",
    "duration_components_s",
    "engines",
    "gates",
    "layer",
    "magic_demand",
    "mapping",
    "move_backend",
    "move_compiler",
    "moved_tokens",
    "operated_qubits",
    "program_ready_s",
    "protocol",
    "qubits",
    "resource_wait_s",
    "route_binding_status",
    "route_metrics",
    "route_steps",
    "runtime_move_compilation",
    "runtime_route_request",
    "runtime_route_resolution",
    "source_slots",
    "target_links",
    "target_modules",
    "terminal_interfaces",
    "terminal_slots",
)


def _runtime_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: metadata[key]
        for key in _RUNTIME_METADATA_KEYS
        if key in metadata
    }


def _completed_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "plane": event["plane"],
        "opcode": event["opcode"],
        "instruction_id": event["instruction_id"],
        "process_id": event["process_id"],
        "instance": event["instance"],
        "start_s": event["start_s"],
        "end_s": event["end_s"],
        "duration_s": event["duration_s"],
        "wait_reasons": event["wait_reasons"],
        "consumed_tokens": event["consumed_tokens"],
        "consumed_slots": event["consumed_slots"],
        "produced_tokens": event["produced_tokens"],
        "produced_slots": event["produced_slots"],
        "buffer_occupancy_start": event["buffer_occupancy_start"],
        "buffer_occupancy_end": event["buffer_occupancy_end"],
        "pending_incoming_start": event["pending_incoming_start"],
        "pending_incoming_end": event["pending_incoming_end"],
        "runtime_metadata": _runtime_metadata(event["metadata"]),
    }


def _dispatched_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "event_id": event["event_id"],
        "plane": event["plane"],
        "opcode": event["opcode"],
        "instruction_id": event["instruction_id"],
        "process_id": event["process_id"],
        "instance": event["instance"],
        "start_s": event["start_s"],
        "end_s": event["end_s"],
        "program_ready": event["program_ready"],
        "resource_ready": event["resource_ready"],
        "reservation": event["reservation"],
        "runtime_metadata": _runtime_metadata(event["metadata"]),
    }


def _timepoint(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "time_s": item["time_s"],
        "program_ready_now": item["program_ready_now"],
        "completed": item["completed"],
        "dispatched": [
            _dispatched_event(event) for event in item["dispatched"]
        ],
        "architecture_state_delta": item["architecture_state_delta"],
        "architecture_state_after": item["architecture_state_after"],
        "frontier_after": item["frontier_after"],
    }


def semantic_baseline(
    case: BehaviorBaselineCase, report: EvaluationReport
) -> dict[str, Any]:
    """Project a full report onto the behavior Step 3 must preserve."""

    evaluation_result = report.evaluation
    if evaluation_result is None:
        raise TypeError("Behavior baselines require native run diagnostics")
    evaluation = evaluation_result.to_dict()
    events = [event.to_dict() for event in evaluation_result.events]
    log = normalize_json(evaluation_result.discrete_time_log)
    terminal = log[-1]
    completed_ids = [event["event_id"] for event in events]
    running = terminal["frontier_after"]["running"]
    plan = report.execution_plan.to_dict()
    analysis = report.analysis.to_dict()
    summary = {
        "total_latency_s": evaluation_result.total_latency_s,
        "total_physical_qubits": report.footprint.total_physical_qubits,
        "success_probability": (
            report.fidelity.success_probability
            if report.fidelity is not None
            else None
        ),
        "fidelity_complete_coverage": (
            report.fidelity.complete_coverage
            if report.fidelity is not None
            else None
        ),
        "completed_program_instructions": (
            evaluation_result.completed_program_instructions
        ),
        "event_count": len(events),
        "invariant_checks": normalize_json(evaluation_result.invariant_checks),
        "footprint_checks": normalize_json(report.footprint.checks),
    }

    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "case_id": case.id,
        "purpose": case.purpose,
        "inputs": {
            "workload": report.circuit.semantic_dict(),
            "config": report.config.to_dict(),
        },
        "plan_contract": {
            "policy": plan["policy"],
            "program_dag": plan["program_dag"],
            "resource_dag": plan["resource_dag"],
            "architectural_state": plan["architectural_state"],
        },
        "runtime_contract": {
            "timepoints": [_timepoint(item) for item in log],
            "completed_events": [
                _completed_event(event) for event in events
            ],
            "program_horizon": {
                "total_latency_s": evaluation["total_latency_s"],
                "completed_event_ids": completed_ids,
                "terminal_running": running,
                "terminal_engine_state": terminal["architecture_state_after"][
                    "engines"
                ],
                "final_state": evaluation["final_state"],
                "inflight_resource_events": evaluation["metrics"][
                    "inflight_resource_events_at_program_completion"
                ],
            },
        },
        "metrics_contract": {
            "summary": summary,
            "program_state_blocked_s": evaluation["program_state_blocked_s"],
            "producer_blocked_s": evaluation["producer_blocked_s"],
            "buffer_peaks": evaluation["buffer_peaks"],
            "metrics": evaluation["metrics"],
            "invariant_checks": evaluation["invariant_checks"],
            "analysis": {
                "exclusive_time_s": analysis["exclusive_time_s"],
                "engine_utilization": analysis["engine_utilization"],
                "buffer_occupancy": analysis["buffer_occupancy"],
                "physical_space_qubits": analysis["physical_space_qubits"],
            },
        },
        "report_contract": {
            "schema_version": report.to_dict()["schema_version"],
            "top_level_keys": sorted(report.to_dict()),
            "evaluation_keys": sorted(report.execution_trace.to_dict()),
            "summary_keys": sorted(summary),
            "analysis_keys": sorted(analysis),
        },
        "diagnostic_hashes": {
            "config": report.config.config_hash,
            "workload": report.circuit.semantic_hash,
            "architecture": report.specification.architecture_hash,
            "compiler": report.compiler_spec.compiler_hash,
            "compilation": report.logical_compilation.compilation_hash,
            "latency_profile": report.latency_profile.profile_hash,
            "execution_plan": report.execution_plan.plan_hash,
            "runtime_components": report.execution_plan.runtime_components.get(
                "manifest_hash"
            ),
            "execution": evaluation_result.execution_hash,
            "trace": evaluation_result.trace_hash,
            "footprint_estimate": report.footprint.estimate_hash,
            "fidelity_profile": (
                report.fidelity_profile.profile_hash
                if report.fidelity_profile is not None
                else None
            ),
            "report": report.report_hash,
        },
        "known_limitations": [
            "Automatic finite recipe lowering currently covers T; automatic STAR and general gadget lowering remain future work.",
            "The frozen report-v1 projection rejects dynamic implementation recipes instead of dropping typed lineage.",
            "Compatibility completed-event spans omit terminal in-flight Resource work; the causal transition ledger preserves it.",
        ],
    }


def run_case(case: BehaviorBaselineCase) -> EvaluationReport:
    circuit, config = load_case_inputs(case)
    return run_evaluation(circuit, config)


def case_digest(case_id: str) -> str:
    case = CASE_BY_ID[case_id]
    return semantic_hash(semantic_baseline(case, run_case(case)))


def assert_semantic_subset(
    actual: Any,
    expected: Any,
    *,
    path: str = "$",
    rel_tol: float = 1e-12,
    abs_tol: float = 1e-15,
) -> None:
    """Compare a frozen semantic subset while allowing additive dict fields."""

    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            raise AssertionError(f"{path}: expected mapping, got {type(actual).__name__}")
        missing = sorted(set(expected) - set(actual))
        if missing:
            raise AssertionError(f"{path}: missing keys {missing}")
        for key, value in expected.items():
            assert_semantic_subset(
                actual[key],
                value,
                path=f"{path}.{key}",
                rel_tol=rel_tol,
                abs_tol=abs_tol,
            )
        return

    if isinstance(expected, list):
        if not isinstance(actual, list):
            raise AssertionError(f"{path}: expected list, got {type(actual).__name__}")
        if len(actual) != len(expected):
            raise AssertionError(
                f"{path}: expected {len(expected)} items, got {len(actual)}"
            )
        for index, value in enumerate(expected):
            assert_semantic_subset(
                actual[index],
                value,
                path=f"{path}[{index}]",
                rel_tol=rel_tol,
                abs_tol=abs_tol,
            )
        return

    if isinstance(expected, float):
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            raise AssertionError(
                f"{path}: expected numeric value, got {type(actual).__name__}"
            )
        if not math.isclose(
            float(actual), float(expected), rel_tol=rel_tol, abs_tol=abs_tol
        ):
            raise AssertionError(f"{path}: expected {expected!r}, got {actual!r}")
        return

    if isinstance(expected, int) and not isinstance(expected, bool):
        if (
            not isinstance(actual, int)
            or isinstance(actual, bool)
            or actual != expected
        ):
            raise AssertionError(
                f"{path}: expected integer {expected!r}, got {actual!r}"
            )
        return

    if actual != expected:
        raise AssertionError(f"{path}: expected {expected!r}, got {actual!r}")


def assert_behavior_baseline(
    actual: Mapping[str, Any], expected: Mapping[str, Any]
) -> None:
    """Apply the frozen behavior gate without freezing evolving audit notes."""

    actual_core = {
        key: value
        for key, value in actual.items()
        if key not in {"diagnostic_hashes", "known_limitations", "report_contract"}
    }
    expected_core = {
        key: value
        for key, value in expected.items()
        if key not in {"diagnostic_hashes", "known_limitations", "report_contract"}
    }
    assert_semantic_subset(actual_core, expected_core)

    actual_contract = actual["report_contract"]
    expected_contract = expected["report_contract"]
    if actual_contract["schema_version"] != expected_contract["schema_version"]:
        raise AssertionError(
            "$.report_contract.schema_version: expected "
            f"{expected_contract['schema_version']!r}, got "
            f"{actual_contract['schema_version']!r}"
        )
    for key in (
        "top_level_keys",
        "evaluation_keys",
        "summary_keys",
        "analysis_keys",
    ):
        missing = sorted(set(expected_contract[key]) - set(actual_contract[key]))
        if missing:
            raise AssertionError(
                f"$.report_contract.{key}: missing required fields {missing}"
            )


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
