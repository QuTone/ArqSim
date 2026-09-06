from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from arqsim.api import EvaluationConfig, run_evaluation
from arqsim.evaluation import ExecutionPlan, RuntimeComponentManifest
from arqsim.program import FTCircuit, load_ft_workload
from arqsim.report_v1 import (
    EVALUATION_REPORT_SCHEMA_VERSION as EVALUATION_REPORT_V1_SCHEMA_VERSION,
)
from arqsim.schema import normalize_json, semantic_hash
from tests.behavior_baseline_support import (
    CASES,
    assert_behavior_baseline,
    assert_semantic_subset,
    load_case_inputs,
    semantic_baseline,
)


def test_semantic_comparator_keeps_identity_fields_type_strict() -> None:
    with pytest.raises(AssertionError, match="expected integer"):
        assert_semantic_subset({"event_id": 0.0}, {"event_id": 0})


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_behavior_baseline_inputs_and_runtime(case) -> None:
    circuit, config = load_case_inputs(case)
    source_circuit = load_ft_workload(
        case.directory / "workload.qasm", case.representation
    )

    assert source_circuit.semantic_dict() == circuit.semantic_dict()
    assert (
        FTCircuit.from_json(circuit.to_json()).semantic_hash
        == circuit.semantic_hash
    )
    assert (
        EvaluationConfig.from_json(config.to_json()).config_hash
        == config.config_hash
    )

    first = run_evaluation(circuit, config)
    second = run_evaluation(circuit, config)
    assert first.to_dict() == second.to_dict()
    assert first.execution_plan.plan_hash == first.evaluation.plan_hash
    assert (
        ExecutionPlan.from_json(first.execution_plan.to_json()).plan_hash
        == first.execution_plan.plan_hash
    )
    assert all(first.evaluation.invariant_checks.values())
    assert all(first.footprint.checks.values())
    report = first.to_dict()
    manifest_hash = config.runtime_components.manifest_hash
    assert (
        RuntimeComponentManifest.from_dict(
            normalize_json(first.execution_plan.runtime_components)
        ).manifest_hash
        == manifest_hash
    )
    assert (
        first.execution_plan.provenance["runtime_manifest_hash"]
        == manifest_hash
    )
    assert first.evaluation.runtime_components["manifest_hash"] == manifest_hash
    assert (
        report["artifacts"]["execution_plan"]["runtime_components"][
            "manifest_hash"
        ]
        == manifest_hash
    )

    expected = json.loads(
        (case.directory / "semantic-baseline.json").read_text(encoding="utf-8")
    )
    assert_behavior_baseline(semantic_baseline(case, first), expected)

    stored_report = json.loads(
        (case.directory / "report.v1.json").read_text(encoding="utf-8")
    )
    report_hash = stored_report.pop("report_hash")
    assert stored_report["schema_version"] == EVALUATION_REPORT_V1_SCHEMA_VERSION
    assert stored_report["hashes"]["workload"] == circuit.semantic_hash
    # report.v1.json is the forensic Step-2 snapshot.  Later additive config
    # contracts (for example the Step-4 runtime-component manifest) may change
    # the current source-config hash without rewriting that frozen artifact.
    assert (
        stored_report["hashes"]["config"]
        == expected["diagnostic_hashes"]["config"]
    )
    assert report_hash == expected["diagnostic_hashes"]["report"]
    assert semantic_hash(stored_report) == report_hash

    horizon = expected["runtime_contract"]["program_horizon"]
    assert horizon["terminal_running"]
    assert horizon["inflight_resource_events"] == len(
        horizon["terminal_running"]
    )


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_behavior_baseline_is_independent_of_python_hash_seed(case) -> None:
    command = [
        sys.executable,
        "-c",
        (
            "import sys; "
            "from tests.behavior_baseline_support import case_digest; "
            "print(case_digest(sys.argv[1]))"
        ),
        case.id,
    ]
    digests = []
    for seed in ("1", "997"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            command,
            cwd=Path(__file__).parents[1],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        digests.append(completed.stdout.strip())
    assert digests[0] == digests[1]
