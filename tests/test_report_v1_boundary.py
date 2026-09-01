from __future__ import annotations

import ast
from pathlib import Path

import pytest

from heteqsys._run_artifacts import EvaluationRunArtifacts
from heteqsys.architecture import get_architecture_profile
from heteqsys.api import EvaluationConfig, EvaluationReport, run_evaluation
from heteqsys.program import FTCircuit, LogicalLayer, LogicalOperation
from heteqsys.report_v1 import (
    ReportV1Renderer,
    render_evaluation_report_v1,
    render_policy_v1,
    render_profile_v2,
)
from heteqsys.schema import normalize_json
from tests.behavior_baseline_support import CASE_BY_ID, load_case_inputs


def test_run_artifacts_render_the_existing_report_v1_document_exactly() -> None:
    case = CASE_BY_ID["pbc_magic_measurement_v1"]
    circuit, config = load_case_inputs(case)

    report = run_evaluation(circuit, config)

    assert isinstance(report, EvaluationReport)
    assert isinstance(report._run_artifacts, EvaluationRunArtifacts)
    artifacts = report._run_artifacts
    assert artifacts.circuit is report.circuit
    assert artifacts.specification is report.specification
    assert artifacts.execution_plan is report.execution_plan
    assert artifacts.evaluation is report.evaluation
    before = {
        "specification": artifacts.specification.to_dict(),
        "compiler_layout": artifacts.compiler_layout.to_dict(),
        "footprint": artifacts.footprint.to_dict(),
        "execution_plan": artifacts.execution_plan.to_dict(),
    }
    rendered = ReportV1Renderer().render(
        artifacts,
        requested_config=report.config.to_dict(),
        requested_config_hash=report.config.config_hash,
        effective_configuration=report.effective_configuration.to_dict(),
        effective_config_hash=(
            report.effective_configuration.effective_config_hash
        ),
        effective_latency_profile_hash=(
            report.effective_configuration.latency_profile.profile_hash
        ),
    )
    compatibility = render_evaluation_report_v1(report)

    assert normalize_json(rendered) == normalize_json(compatibility)
    assert rendered["report_hash"] == compatibility["report_hash"]
    assert report.to_dict()["schema_version"] == "arqsim.evaluation-report.v2"

    specification = rendered["specification"]
    assert set(specification) == {
        "schema_version",
        "workflow",
        "profile",
        "policy_parameters",
        "resource_protocol_bindings",
        "layout_plan",
        "resolved_system",
        "architecture_slot_layout",
        "physical_footprint_model",
        "physical_footprint",
        "logical_architecture",
    }
    assert rendered["hashes"]["qec"] == (
        specification["resolved_system"]["qec_configuration"]["qec_hash"]
    )
    assert rendered["hashes"]["system"] == (
        specification["resolved_system"]["system_hash"]
    )
    assert all(
        "/" not in slot["id"]
        for slot in specification["architecture_slot_layout"]["slots"]
    )
    assert rendered["execution_plan"]["schema_version"] == (
        "arqsim.execution-plan.v5"
    )
    assert before == {
        "specification": artifacts.specification.to_dict(),
        "compiler_layout": artifacts.compiler_layout.to_dict(),
        "footprint": artifacts.footprint.to_dict(),
        "execution_plan": artifacts.execution_plan.to_dict(),
    }


def test_report_v1_helpers_are_one_way_plain_mapping_renderers() -> None:
    profile = render_profile_v2(get_architecture_profile("1.3"))
    policy = render_policy_v1({"quantiles.compute": 0.75})

    assert profile["schema_version"] == "arqsim.architecture-profile.v2"
    assert profile["interconnects"]
    assert policy["schema_version"] == "arqsim.quantile-layout-policy.v1"
    assert policy["quantiles"]["compute"] == 0.75

    source = Path(__file__).parents[1] / "heteqsys" / "report_v1.py"
    text = source.read_text(encoding="utf-8")
    assert "ResolvedFTSystemSpec" not in text
    assert "architecture.legacy_adapter" not in text
    assert "architecture.profile_compat" not in text

    tree = ast.parse(text, filename=str(source))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert {
        "heteqsys.architecture.legacy_adapter",
        "heteqsys.architecture.profile_compat",
        "heteqsys.architecture.spec",
        "heteqsys.qec.configuration",
        "heteqsys.compiler.pipeline",
        "heteqsys.evaluation.plan",
    }.isdisjoint(imported)
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "from_dict"
        for node in ast.walk(tree)
    )


@pytest.mark.parametrize(
    "profile_id", ("1.1", "1.2", "1.3", "2.1", "2.2", "2.3")
)
def test_report_v1_projection_covers_every_gallery_architecture(
    profile_id: str,
) -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=2,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "h", qubits=(0,)),),
            ),
        ),
        provenance={"benchmark": "report-v1-gallery-smoke"},
    )
    report = render_evaluation_report_v1(
        run_evaluation(
            circuit,
            EvaluationConfig(
                profile_id=profile_id,
                workflow_id="gallery-smoke",
            ),
        )
    )
    specification = report["specification"]

    assert len(specification) == 11
    assert report["hashes"]["qec"] == (
        specification["resolved_system"]["qec_configuration"]["qec_hash"]
    )
    assert len(specification["logical_architecture"]["interconnects"]) == (
        1 if profile_id in {"1.3", "2.2", "2.3"} else 0
    )
