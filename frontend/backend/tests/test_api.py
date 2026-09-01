"""Contract smoke tests for the thin versioned HTTP adapter."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import Response


CORE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(CORE_ROOT))

from backend.src import api  # noqa: E402
from heteqsys import EvaluationConfig, FTCircuit  # noqa: E402
from heteqsys.program import LogicalLayer, LogicalOperation  # noqa: E402


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
                "seed": 5,
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


def test_native_and_compatibility_endpoints_are_explicit() -> None:
    request = _request()

    native = api.evaluate_v2(request)
    response = Response()
    compatibility = api.evaluate(request, response)

    assert native["schema_version"] == "arqsim.evaluation-report.v2"
    assert compatibility["schema_version"] == "arqsim.evaluation-report.v1"
    assert response.headers["Deprecation"] == "true"


def test_finite_injection_demo_crosses_public_api_with_dynamic_runtime_semantics() -> None:
    report = api.evaluate_v2(_finite_injection_request())

    assert report["workflow_id"] == (
        "frontend:finite_t_injection_demo_v1:"
        "arqsim_timeline_demo__2.3"
    )

    plan = report["artifacts"]["execution_plan"]
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
    measured_sources = [
        transition
        for transition in completed_program_events
        if transition["program_lineage"]["step"] == "source"
        and transition["measurements"]
    ]
    assert [
        next(iter(transition["measurements"].values()))
        for transition in measured_sources
    ] == [1, 0]

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
