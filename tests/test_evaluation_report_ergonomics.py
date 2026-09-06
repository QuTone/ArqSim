from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from arqsim.api import (
    EvaluationConfig,
    EvaluationReport,
    EvaluationSummary,
    load_evaluation_report_document,
    run_evaluation,
    validate_evaluation_report_document,
)
from arqsim.evaluation import PhysicalFootprintEstimate
from arqsim.program import load_ft_workload
from arqsim.report_v2 import ReportV2Codec


FIXTURE = Path(__file__).parent / "fixtures/small_original.qasm"


def _report() -> EvaluationReport:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    return run_evaluation(circuit, EvaluationConfig(profile_id="1.2"))


def test_summary_is_the_typed_view_of_the_frozen_wire_record() -> None:
    report = _report()
    loaded = EvaluationReport.from_json(report.to_json())

    assert isinstance(report.summary, EvaluationSummary)
    assert report.summary.to_dict() == report.to_dict()["results"]["summary"]
    assert loaded.summary == report.summary
    assert loaded.summary.to_dict() == loaded.to_dict()["results"]["summary"]
    assert report.summary.total_latency_s == report.execution_trace.total_latency_s
    assert (
        report.summary.total_physical_qubits
        == report.footprint.total_physical_qubits
    )
    assert report.summary.all_invariants_satisfied
    assert isinstance(report.footprint, PhysicalFootprintEstimate)


def test_report_identity_and_repr_are_stable_across_json_round_trip() -> None:
    report = _report()
    loaded = EvaluationReport.from_json(report.to_json())

    assert report == loaded
    assert hash(report) == hash(loaded)
    assert report.report_hash == loaded.report_hash
    assert report.to_dict() == loaded.to_dict()

    display = repr(report)
    assert len(display) < 400
    assert report.report_hash in display
    assert report.workflow_id in display
    assert "execution_plan=" not in display
    assert "evaluation=" not in display


def test_report_direct_construction_is_rejected_with_factory_guidance() -> None:
    with pytest.raises(TypeError, match=r"run_evaluation\(\)"):
        EvaluationReport()


def test_json_load_validates_once_and_keeps_detached_output() -> None:
    original = _report()
    text = original.to_json()
    with patch.object(ReportV2Codec, "from_dict", wraps=ReportV2Codec.from_dict) as codec:
        loaded = EvaluationReport.from_json(text)
    assert codec.call_count == 1
    detached = loaded.to_dict()
    detached["results"]["summary"]["event_count"] = -1
    assert loaded.to_json() == text
    assert loaded.summary == original.summary


@pytest.mark.parametrize("as_json", [False, True])
def test_document_loaders_preserve_recursive_immutability(as_json: bool) -> None:
    report = _report()
    source = report.to_dict()
    document = (
        load_evaluation_report_document(report.to_json())
        if as_json
        else validate_evaluation_report_document(source)
    )
    source["results"]["summary"]["event_count"] = -1
    assert document["results"]["summary"]["event_count"] == report.summary.event_count
    with pytest.raises(TypeError):
        document["results"]["summary"]["event_count"] = -1
