from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from heteqsys.api import EvaluationConfig, run_evaluation
from heteqsys.evaluation import EvaluationPolicy, RuntimeInjectionMode
from heteqsys.operation_profiles import ArrivalDistribution, OperationLatencyProfile
from heteqsys.program import (
    FTCircuit,
    LogicalLayer,
    LogicalOperation,
    load_ft_workload,
)
from heteqsys.report_v2 import (
    EVALUATION_REPORT_V2_SCHEMA_VERSION,
    ReportV2Codec,
    ReportV2Renderer,
    ReportV2ValidationError,
    load_report_v2_document,
)
from heteqsys.schema import normalize_json, semantic_hash


FIXTURE = Path(__file__).parent / "fixtures/small_original.qasm"
REPORT_V2_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "report_v2"


def test_native_report_construction_does_not_import_report_v1() -> None:
    source = """
import sys
from heteqsys import EvaluationConfig, FTCircuit, run_evaluation
from heteqsys.program import LogicalLayer, LogicalOperation
circuit = FTCircuit(
    representation='clifford_t',
    num_qubits=1,
    num_clbits=0,
    layers=(LogicalLayer(0, (LogicalOperation('gate', 'h', qubits=(0,)),)),),
)
report = run_evaluation(circuit, EvaluationConfig(profile_id='1.1'))
assert report.to_dict()['schema_version'] == 'arqsim.evaluation-report.v2'
assert 'heteqsys.report_v1' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def _config() -> EvaluationConfig:
    return EvaluationConfig(
        profile_id="1.2",
        latency_profile=OperationLatencyProfile(
            magic_state_arrival=ArrivalDistribution.from_rate(
                1_000.0, kind="deterministic"
            ),
            bell_pair_arrival=ArrivalDistribution.from_rate(
                1_000.0, kind="deterministic"
            ),
        ),
        evaluation_policy=EvaluationPolicy(trace_level="summary", seed=7),
    )


@pytest.fixture(scope="module")
def report_document() -> dict:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    config = _config()
    native = run_evaluation(circuit, config)
    return normalize_json(
        ReportV2Renderer().render(
            native._run_artifacts,
            requested_config=config.to_dict(),
            workflow_id="report-v2-codec-test",
        )
    )


def _resign(document: dict) -> None:
    document["report_hash"] = semantic_hash(
        {key: value for key, value in document.items() if key != "report_hash"}
    )


@pytest.mark.parametrize(
    "name",
    ("static-summary.v2.json", "dynamic-t-full.v2.json"),
)
def test_checked_in_report_v2_fixtures_are_strict_and_canonical(name: str) -> None:
    text = (REPORT_V2_FIXTURE_DIR / name).read_text(encoding="utf-8")
    parsed = load_report_v2_document(text)

    assert parsed.to_json() == text
    if name == "dynamic-t-full.v2.json":
        assert any(
            transition.program_lineage is not None
            and transition.program_lineage.step == "correction"
            for transition in parsed.execution_trace.transitions
        )


def test_report_v2_has_only_the_frozen_topology(report_document: dict) -> None:
    assert report_document["schema_version"] == EVALUATION_REPORT_V2_SCHEMA_VERSION
    assert set(report_document) == {
        "schema_version",
        "workflow_id",
        "request",
        "resolved_inputs",
        "artifacts",
        "results",
        "report_hash",
    }
    assert set(report_document["artifacts"]) == {
        "logical_compilation",
        "execution_plan",
        "execution_trace",
    }
    assert "hashes" not in report_document
    assert "events" not in report_document
    assert "final_state" not in report_document
    assert report_document["results"]["observations"]["discrete_time_log"] == []


def test_report_v2_strict_round_trip_preserves_typed_authorities(
    report_document: dict,
) -> None:
    parsed = ReportV2Codec.from_dict(deepcopy(report_document))
    encoded = parsed.to_json()
    round_trip = ReportV2Codec.from_json(encoded)

    assert round_trip.to_dict() == report_document
    assert encoded.endswith("\n")
    assert round_trip.report_hash == report_document["report_hash"]
    assert (
        round_trip.logical_compilation.compilation_hash
        == report_document["artifacts"]["logical_compilation"][
            "compilation_hash"
        ]
    )
    assert (
        round_trip.execution_plan.plan_hash
        == report_document["artifacts"]["execution_plan"]["plan_hash"]
    )
    assert (
        round_trip.execution_trace.trace_hash
        == report_document["artifacts"]["execution_trace"]["trace_hash"]
    )
    assert round_trip.execution_trace.plan_hash == round_trip.execution_plan.plan_hash


@pytest.mark.parametrize(
    ("section", "mutation"),
    [
        ("top", lambda value: value.__setitem__("unknown", None)),
        ("request", lambda value: value.pop("workload")),
        ("resolved", lambda value: value.__setitem__("unknown", None)),
        ("artifacts", lambda value: value.pop("execution_trace")),
        ("results", lambda value: value.__setitem__("events", [])),
        ("summary", lambda value: value.pop("event_count")),
    ],
)
def test_report_v2_rejects_unknown_and_missing_fields(
    report_document: dict,
    section: str,
    mutation,
) -> None:
    candidate = deepcopy(report_document)
    target = {
        "top": candidate,
        "request": candidate["request"],
        "resolved": candidate["resolved_inputs"],
        "artifacts": candidate["artifacts"],
        "results": candidate["results"],
        "summary": candidate["results"]["summary"],
    }[section]
    mutation(target)
    _resign(candidate)

    with pytest.raises(ReportV2ValidationError):
        ReportV2Codec.from_dict(candidate)


def test_report_v2_rejects_duplicate_keys_and_nonfinite_values(
    report_document: dict,
) -> None:
    encoded = json.dumps(report_document, sort_keys=True, allow_nan=False)
    duplicate = encoded.replace(
        '"workflow_id":',
        '"workflow_id":"duplicate","workflow_id":',
        1,
    )
    with pytest.raises(ReportV2ValidationError, match="Duplicate"):
        ReportV2Codec.from_json(duplicate)

    nonfinite = deepcopy(report_document)
    nonfinite["results"]["summary"]["total_latency_s"] = float("nan")
    with pytest.raises(ReportV2ValidationError, match="finite"):
        ReportV2Codec.from_dict(nonfinite)


def test_report_v2_rejects_nested_hash_and_derived_result_tampering(
    report_document: dict,
) -> None:
    nested_hash = deepcopy(report_document)
    nested_hash["artifacts"]["execution_trace"]["trace_hash"] = "0" * 64
    _resign(nested_hash)
    with pytest.raises(ValueError, match="trace_hash"):
        ReportV2Codec.from_dict(nested_hash)

    derived_cases = (
        (
            "footprint",
            lambda candidate: candidate["results"]["footprint"].__setitem__(
                "total_physical_qubits",
                candidate["results"]["footprint"]["total_physical_qubits"] + 1,
            ),
        ),
        (
            "fidelity",
            lambda candidate: candidate["results"].__setitem__("fidelity", {}),
        ),
        (
            "analysis",
            lambda candidate: candidate["results"]["analysis"].__setitem__(
                "engine_utilization", {}
            ),
        ),
        (
            "summary",
            lambda candidate: candidate["results"]["summary"].__setitem__(
                "event_count",
                candidate["results"]["summary"]["event_count"] + 1,
            ),
        ),
    )
    for label, mutate in derived_cases:
        derived = deepcopy(report_document)
        mutate(derived)
        _resign(derived)
        with pytest.raises(ReportV2ValidationError, match=label):
            ReportV2Codec.from_dict(derived)

    outer_hash = deepcopy(report_document)
    outer_hash["report_hash"] = "0" * 64
    with pytest.raises(ReportV2ValidationError, match="Report-v2 hash"):
        ReportV2Codec.from_dict(outer_hash)


def test_report_v2_rejects_a_validly_rehashed_noncanonical_plan(
    report_document: dict,
) -> None:
    candidate = deepcopy(report_document)
    plan = candidate["artifacts"]["execution_plan"]
    plan["provenance"]["plan_builder_version"] = "tampered-builder"
    plan["plan_hash"] = semantic_hash(
        {key: value for key, value in plan.items() if key != "plan_hash"}
    )
    trace = candidate["artifacts"]["execution_trace"]
    trace["plan_hash"] = plan["plan_hash"]
    trace["trace_hash"] = semantic_hash(
        {key: value for key, value in trace.items() if key != "trace_hash"}
    )
    _resign(candidate)

    with pytest.raises(ReportV2ValidationError, match="deterministically lowered"):
        ReportV2Codec.from_dict(candidate)


def test_report_v2_rejects_a_validly_rehashed_trace_seed_mismatch(
    report_document: dict,
) -> None:
    candidate = deepcopy(report_document)
    trace = candidate["artifacts"]["execution_trace"]
    trace["seed"] += 1
    trace["trace_hash"] = semantic_hash(
        {key: value for key, value in trace.items() if key != "trace_hash"}
    )
    _resign(candidate)

    with pytest.raises(ReportV2ValidationError, match="seed"):
        ReportV2Codec.from_dict(candidate)


def test_report_v2_rejects_noncanonical_types_and_older_schema(
    report_document: dict,
) -> None:
    coerced = deepcopy(report_document)
    coerced["results"]["summary"]["event_count"] = True
    _resign(coerced)
    with pytest.raises(ReportV2ValidationError, match="summary"):
        ReportV2Codec.from_dict(coerced)

    old = deepcopy(report_document)
    old["schema_version"] = "arqsim.evaluation-report.v1"
    _resign(old)
    with pytest.raises(ReportV2ValidationError, match="Unsupported"):
        ReportV2Codec.from_dict(old)


def test_workflow_label_changes_only_the_outer_report_identity(
    report_document: dict,
) -> None:
    changed = deepcopy(report_document)
    changed["workflow_id"] = "a-different-non-semantic-label"
    _resign(changed)
    parsed = ReportV2Codec.from_dict(changed)

    assert parsed.report_hash != report_document["report_hash"]
    assert (
        changed["artifacts"]["logical_compilation"]["compilation_hash"]
        == report_document["artifacts"]["logical_compilation"][
            "compilation_hash"
        ]
    )
    assert (
        changed["artifacts"]["execution_plan"]["plan_hash"]
        == report_document["artifacts"]["execution_plan"]["plan_hash"]
    )
    assert (
        changed["artifacts"]["execution_trace"]["trace_hash"]
        == report_document["artifacts"]["execution_trace"]["trace_hash"]
    )


def test_dynamic_t_full_trace_round_trip_and_observation_tamper() -> None:
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
    report = run_evaluation(
        circuit,
        EvaluationConfig(
            profile_id="1.1",
            latency_profile=OperationLatencyProfile(
                reaction_latency_by_modality_s={"neutral_atom": 0.0}
            ),
            evaluation_policy=EvaluationPolicy(
                trace_level="full",
                seed=0,
                runtime_injection_mode=(
                    RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
                ),
            ),
        ),
    )
    parsed = ReportV2Codec.from_json(report.to_json())
    program_events = [
        event
        for event in parsed.execution_trace.events
        if event.plane.value == "program"
    ]

    assert any(event.continuation.kind == "activate" for event in program_events)
    assert any(
        event.program_lineage is not None
        and event.program_lineage.step == "correction"
        for event in program_events
    )
    assert parsed.to_dict() == report.to_dict()

    tampered = report.to_dict()
    dynamic_ready_rows = [
        entry["program_ready_now"]
        for entry in tampered["results"]["observations"]["discrete_time_log"]
        if any(schedule_id >= 2 for schedule_id in entry["program_ready_now"])
    ]
    assert dynamic_ready_rows
    dynamic_ready_rows[0][0] += 100
    _resign(tampered)
    with pytest.raises(ValueError, match="program_ready_now"):
        ReportV2Codec.from_dict(tampered)
