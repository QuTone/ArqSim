"""Contract smoke tests for the thin versioned HTTP adapter."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError


CORE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CORE_ROOT))

from server.src import api  # noqa: E402
from arqsim import EvaluationConfig, EvaluationReport, FTCircuit  # noqa: E402
from arqsim.program import LogicalLayer, LogicalOperation  # noqa: E402


def _request() -> api.EvaluationRequest:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(0, (LogicalOperation("gate", "h", (0,)),)),
        ),
    )
    return api.EvaluationRequest(
        representation="clifford_t",
        workload=circuit.to_dict(),
        config=EvaluationConfig(profile_id="1.1").to_dict(),
    )


def _finite_injection_request() -> api.EvaluationRequest:
    """Build the UI's explicit finite-injection demo request."""

    return api.EvaluationRequest(
        representation="clifford_t",
        benchmark_name="arqsim_timeline_demo",
        config={
            "schema_version": "arqsim.evaluation-config.v1",
            "profile_id": "2.3",
            "workflow_id": (
                "frontend:finite_t_injection_demo_v1:"
                "arqsim_timeline_demo__2.3"
            ),
            "latency_profile": {
                "reaction_latency_by_modality_s": {
                    "neutral_atom": 5e-4,
                    "superconducting": 1e-5,
                },
                "provenance": {
                    "reaction_latency_profile": (
                        "reference_reaction_latency_profile_v1"
                    ),
                    "reaction_latency_source": (
                        "reference classical-control assumptions"
                    ),
                },
            },
            "evaluation_policy": {
                "trace_level": "full",
                "runtime_injection_mode": "finite_state_injection_v1",
                "seed": 0,
            },
            "fidelity_profile": {"preset": "canonical_reference_v1"},
        },
    )


def test_routes_expose_native_v2_and_only_one_deprecated_v1_boundary() -> None:
    routes = {route.path: route for route in api.app.routes}

    assert "/evaluate-v2" in routes
    assert routes["/evaluate-v2"].deprecated is None
    assert "/evaluate" in routes
    assert routes["/evaluate"].deprecated is True
    assert "/simulate" not in routes


def test_profile_catalog_is_canonical_v3() -> None:
    profiles = api.get_architecture_profiles()

    assert len(profiles) == 6
    assert all(
        profile["schema_version"] == "arqsim.architecture-profile.v3"
        for profile in profiles
    )


def test_benchmark_catalog_advertises_available_normalized_inputs() -> None:
    benchmarks = {item["id"]: item for item in api.get_benchmarks()}

    assert benchmarks["arqsim_timeline_demo"]["representations"] == [
        "clifford_t"
    ]
    assert benchmarks["qaoa_30"]["representations"] == ["clifford_t", "pbc"]
    assert api._benchmark_workload_path("qaoa_30", "clifford_t").name == (
        "qaoa_30_ctr_transpiled.qasm"
    )
    for benchmark in benchmarks.values():
        for representation in benchmark["representations"]:
            assert api._benchmark_workload_path(
                benchmark["id"], representation
            ).is_file()


def test_benchmark_representations_exclude_missing_and_ambiguous_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api, "PROJECT_ROOT", tmp_path)
    gate_root = tmp_path / "benchmark" / "clifford+T"
    gate_root.mkdir(parents=True)
    (gate_root / "example_transpiled.qasm").write_text("", encoding="utf-8")

    assert api._benchmark_representations("example") == ["clifford_t"]
    (gate_root / "example_other_transpiled.qasm").write_text(
        "", encoding="utf-8"
    )
    assert api._benchmark_representations("example") == []


@pytest.mark.parametrize("profile_id", ["1.1", "1.2", "1.3", "2.1", "2.2", "2.3"])
def test_default_demo_has_full_timeline_on_every_preset(profile_id: str) -> None:
    report = api.evaluate_v2(
        api.EvaluationRequest(
            benchmark_name="arqsim_timeline_demo",
            representation="clifford_t",
            config={
                "schema_version": "arqsim.evaluation-config.v1",
                "profile_id": profile_id,
            },
        )
    )

    assert report["request"]["config"]["profile_id"] == profile_id
    assert report["artifacts"]["execution_plan"]["policy"]["trace_level"] == "full"
    assert report["artifacts"]["execution_trace"]["transitions"]
    assert report["results"]["observations"]["discrete_time_log"]
    assert report["results"]["summary"]["fidelity_complete_coverage"] is True
    assert all(report["results"]["summary"]["invariant_checks"].values())


def test_nondemo_benchmark_returns_full_timeline() -> None:
    report = api.evaluate_v2(
        api.EvaluationRequest(
            benchmark_name="adder_n64",
            representation="clifford_t",
            config={
                "schema_version": "arqsim.evaluation-config.v1",
                "profile_id": "1.1",
            },
        )
    )

    assert report["request"]["workload"]["num_qubits"] == 64
    assert report["results"]["summary"]["completed_program_instructions"] > 100
    assert report["results"]["observations"]["discrete_time_log"]
    assert report["results"]["summary"]["fidelity_complete_coverage"] is True
    assert all(report["results"]["summary"]["invariant_checks"].values())


def test_native_and_compatibility_endpoints_are_explicit() -> None:
    request = _request()

    native = api.evaluate_v2(request)
    response = Response()
    compatibility = api.evaluate(request, response)

    assert native["schema_version"] == "arqsim.evaluation-report.v2"
    assert compatibility["schema_version"] == "arqsim.evaluation-report.v1"
    assert response.headers["Deprecation"] == "true"


def test_finite_injection_demo_crosses_public_api_with_dynamic_runtime_semantics() -> None:
    benchmark = next(
        item for item in api.get_benchmarks() if item["id"] == "arqsim_timeline_demo"
    )
    assert benchmark["name"] == "ArqSim Timeline Demo"
    assert benchmark["tGates"] == "2"

    report = api.evaluate_v2(_finite_injection_request())

    assert report["workflow_id"] == (
        "frontend:finite_t_injection_demo_v1:"
        "arqsim_timeline_demo__2.3"
    )

    plan = report["artifacts"]["execution_plan"]
    assert report["request"]["workload"]["num_qubits"] == 4
    assert plan["policy"]["runtime_injection_mode"] == "finite_state_injection_v1"
    assert plan["policy"]["seed"] == 0
    recipes = [
        recipe
        for instruction in plan["program_dag"]["instructions"]
        for recipe in instruction.get("implementation_recipes", [])
    ]
    assert len(recipes) == 2
    assert {recipe["recipe_id"] for recipe in recipes} == {
        "surface_code.t_injection.v1"
    }
    assert {recipe["convention"] for recipe in recipes} == {
        "cx_data_magic_measure_magic_z_v1"
    }

    completed_program_events = [
        transition
        for transition in report["artifacts"]["execution_trace"]["transitions"]
        if transition["kind"] == "completion"
        and transition["program_lineage"] is not None
    ]
    measurements = [
        transition
        for transition in completed_program_events
        if transition["program_lineage"]["step"] == "measurement"
        and transition["measurements"]
    ]
    assert [
        next(iter(transition["measurements"].values()))
        for transition in measurements
    ] == [1, 0]
    assert all(
        not transition["measurements"]
        for transition in completed_program_events
        if transition["program_lineage"]["step"] == "entangle"
    )

    reactions = [
        transition
        for transition in completed_program_events
        if transition["program_lineage"]["step"] == "reaction"
    ]
    corrections = [
        transition
        for transition in completed_program_events
        if transition["program_lineage"]["step"] == "correction"
    ]
    assert len(reactions) == 2
    assert len(corrections) == 1
    assert corrections[0]["metadata"]["gates"] == {"s": [0]}

    results = report["results"]
    invariants = results["summary"]["invariant_checks"]
    assert invariants
    assert all(invariants.values())

    fidelity = results["fidelity"]
    assert fidelity["complete_coverage"] is True
    assert fidelity["logical_operation_counts"]["s"] == 1
    assert sum(fidelity["idle_cycles_by_location"].values()) > 0
    assert sum(fidelity["resource_idle_cycles_by_location"].values()) > 0


def test_http_boundary_reports_incomplete_fidelity_as_structured_422() -> None:
    circuit = FTCircuit(
        representation="gate",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (
                    LogicalOperation(
                        "gate", "rz", (0,), parameters=(0.125,)
                    ),
                ),
            ),
        ),
    )
    request = api.EvaluationRequest(
        representation="gate",
        workload=circuit.to_dict(),
        config=EvaluationConfig(profile_id="1.1").to_dict(),
    )

    with pytest.raises(HTTPException) as caught:
        api.evaluate_v2(request)

    assert caught.value.status_code == 422
    assert caught.value.detail["code"] == "fidelity_coverage_incomplete"
    assert caught.value.detail["stage"] == "result_analysis"
    assert caught.value.detail["details"]["coverage_gaps"][
        "unprofiled_logical_operation_counts"
    ] == {"rz": 1}


@pytest.mark.parametrize("limit", [0, -1, 257, True, 1.5, "12"])
def test_preview_limit_is_a_bounded_strict_integer(limit: object) -> None:
    data = _request().model_dump()
    data["preview_max_layers"] = limit
    with pytest.raises(ValidationError):
        api.EvaluationRequest.model_validate(data)


def test_preview_excludes_suffix_before_compilation_and_preserves_registers() -> None:
    # RZ has no canonical fidelity coverage. A preview must not compile or
    # evaluate that excluded suffix, nor remove qubits unused in the prefix.
    source = FTCircuit(
        representation="gate",
        num_qubits=4,
        num_clbits=2,
        layers=(
            LogicalLayer(0, (
                LogicalOperation("gate", "h", (0,)),
                LogicalOperation("gate", "h", (1,)),
            )),
            LogicalLayer(1, (LogicalOperation("gate", "rz", (3,), parameters=(0.125,)),)),
        ),
        provenance={"benchmark": "prefix_test"},
    )
    request = api.EvaluationRequest(
        representation="gate",
        workload=source.to_dict(),
        config=EvaluationConfig(profile_id="1.1").to_dict(),
        preview_max_layers=1,
    )
    report = api.evaluate_v2(request)
    evaluated = FTCircuit.from_dict(report["request"]["workload"])
    assert evaluated.num_qubits == 4
    assert evaluated.num_clbits == 2
    assert evaluated.layers == source.layers[:1]
    assert evaluated.operation_count == 2
    assert evaluated.provenance["evaluation_scope"] == {
        "kind": "prefix_preview",
        "requested_max_layers": 1,
        "source_workload_hash": source.semantic_hash,
        "source_layer_count": 2,
        "source_operation_count": 3,
        "evaluated_layer_count": 1,
        "evaluated_operation_count": 2,
        "truncated": True,
    }
    assert len(source.layers) == 2
    assert "evaluation_scope" not in source.provenance
    assert report["results"]["summary"]["fidelity_complete_coverage"] is True
    assert all(report["results"]["summary"]["invariant_checks"].values())
    assert EvaluationReport.from_dict(report).report_hash == report["report_hash"]

    with pytest.raises(HTTPException) as caught:
        api.evaluate_v2(request.model_copy(update={"preview_max_layers": None}))
    assert caught.value.detail["code"] == "fidelity_coverage_incomplete"


@pytest.mark.parametrize(("seed", "measurement"), [(0, 0), (4, 1)])
def test_preview_finishes_dynamic_children_when_prefix_ends_on_t(
    seed: int, measurement: int
) -> None:
    request = _finite_injection_request().model_copy(update={"preview_max_layers": 2})
    request.config["evaluation_policy"]["seed"] = seed
    report = api.evaluate_v2(request)
    assert len(report["request"]["workload"]["layers"]) == 2
    assert report["request"]["workload"]["num_qubits"] == 4
    completed = [
        transition for transition in report["artifacts"]["execution_trace"]["transitions"]
        if transition["kind"] == "completion" and transition["program_lineage"] is not None
    ]
    steps = [transition["program_lineage"]["step"] for transition in completed]
    for step in ("entangle", "measurement", "reaction"):
        assert steps.count(step) == 1
    bits = [value for event in completed for value in event["measurements"].values()]
    assert bits == [measurement]
    assert steps.count("correction") == measurement
    assert report["results"]["fidelity"]["logical_operation_counts"].get("s", 0) == measurement
    assert report["results"]["summary"]["fidelity_complete_coverage"] is True
    assert all(report["results"]["summary"]["invariant_checks"].values())
    assert EvaluationReport.from_dict(report).report_hash == report["report_hash"]


def test_preview_covering_whole_workload_preserves_execution_results() -> None:
    request = _finite_injection_request()
    full = api.evaluate_v2(request)
    preview = api.evaluate_v2(request.model_copy(update={"preview_max_layers": 12}))
    scope = preview["request"]["workload"]["provenance"]["evaluation_scope"]
    assert scope["truncated"] is False
    assert scope["evaluated_layer_count"] == scope["source_layer_count"] == 6
    assert preview["results"] == full["results"]
    assert preview["artifacts"]["execution_trace"] == full["artifacts"]["execution_trace"]
    assert preview["resolved_inputs"]["architecture"] == full["resolved_inputs"]["architecture"]
