"""Contracts for the Plan-v9 logical-measurement-provider System Case."""

from __future__ import annotations

from system_cases.finite_runtime_injection_demo_measurement_v3 import run as case_v3


def test_measurement_v3_manifest_records_immutable_locus_predecessor() -> None:
    system_case = case_v3.load_system_case()

    assert system_case.id == "finite_runtime_injection_demo_measurement_v3"
    assert system_case.document["lineage"] == {
        "predecessor_id": "finite_runtime_injection_demo_locus_v2",
        "change": (
            "logical_measurement_provider_plan_v9_seeded_branch_coverage"
        ),
    }
    assert system_case.document["reference"]["mutation_policy"] == (
        "immutable_create_successor_do_not_overwrite"
    )
    config = case_v3.build_config(system_case)
    assert config.run_label == (
        "finite_runtime_injection_demo_measurement_v3:clifford_t_toy__2.3"
    )
    assert config.execution_policy.run_seed == 0
    assert case_v3.build_request(system_case)["case_id"] == case_v3.CASE_ID


def test_measurement_v3_reference_and_live_run_are_byte_identical() -> None:
    verified = case_v3.verify_reference(rerun=True)

    assert verified.report.report_hash == verified.receipt["hashes"]["report"]
    plan = verified.report.execution_plan.to_dict()
    assert plan["schema_version"] == "arqsim.execution-plan.v9"
    assert plan["runtime_components"]["schema_version"] == (
        "arqsim.runtime-manifest.v4"
    )
    components = plan["runtime_components"]["components"]
    assert "outcome_model" not in components
    assert components["measurement_provider"]["component_id"] == (
        "measurement.seeded_bernoulli.v1"
    )

    injection = verified.receipt["runtime_injection"]
    assert injection["measurement_provider"] == "measurement.seeded_bernoulli.v1"
    assert [item["outcome_bit"] for item in injection["invocations"]] == [1, 0]
    assert [item["steps"] for item in injection["invocations"]] == [
        ["entangle", "measurement", "reaction", "correction"],
        ["entangle", "measurement", "reaction"],
    ]
    assert verified.receipt["results"]["logical_operation_counts"] == {
        "cx": 2,
        "h": 4,
        "s": 1,
        "t": 2,
    }
