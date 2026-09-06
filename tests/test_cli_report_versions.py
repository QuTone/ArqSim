from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from arqsim import api as public_api
from arqsim import report_v1
from arqsim.cli import main as cli_main
from arqsim.program import FTCircuit, LogicalLayer, LogicalOperation


class _StubReport:
    def __init__(self) -> None:
        self.v2_calls = 0

    def to_json(self) -> str:
        self.v2_calls += 1
        return json.dumps(
            {
                "schema_version": "arqsim.evaluation-report.v2",
                "report_hash": "v2-hash",
            },
            sort_keys=True,
        ) + "\n"


def _write_program(path: Path) -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                index=0,
                operations=(LogicalOperation("gate", "h", (0,)),),
            ),
        ),
    )
    path.write_text(circuit.to_json(), encoding="utf-8")


def test_cli_evaluate_defaults_to_native_report_v2(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program = tmp_path / "program.json"
    _write_program(program)
    report = _StubReport()
    monkeypatch.setattr(public_api, "run_evaluation", lambda *_: report)
    monkeypatch.setattr(
        report_v1,
        "render_evaluation_report_v1",
        lambda _: pytest.fail("default CLI output must not invoke Report v1"),
    )

    status = cli_main(["evaluate", str(program)])
    captured = capsys.readouterr()

    assert status == 0
    assert report.v2_calls == 1
    assert json.loads(captured.out)["schema_version"] == (
        "arqsim.evaluation-report.v2"
    )
    assert captured.err == ""


def test_cli_evaluate_renders_v1_only_through_explicit_adapter(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program = tmp_path / "program.json"
    output = tmp_path / "report.json"
    _write_program(program)
    report = _StubReport()
    seen: list[Any] = []

    monkeypatch.setattr(public_api, "run_evaluation", lambda *_: report)

    def _render(candidate: Any) -> dict[str, str]:
        seen.append(candidate)
        return {
            "schema_version": "arqsim.evaluation-report.v1",
            "report_hash": "v1-hash",
        }

    monkeypatch.setattr(report_v1, "render_evaluation_report_v1", _render)

    status = cli_main(
        [
            "evaluate",
            str(program),
            "--report-version",
            "v1",
            "--output",
            str(output),
        ]
    )
    captured = capsys.readouterr()

    assert status == 0
    assert seen == [report]
    assert report.v2_calls == 0
    assert json.loads(captured.out)["schema_version"] == (
        "arqsim.evaluation-report.v1"
    )
    assert output.read_text(encoding="utf-8") == captured.out
    assert captured.err == ""


def test_cli_v1_adapter_failure_uses_the_versioned_error_contract(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program = tmp_path / "program.json"
    _write_program(program)
    monkeypatch.setattr(public_api, "run_evaluation", lambda *_: _StubReport())

    def _reject(_: Any) -> None:
        raise ValueError("Report v1 cannot represent dynamic recipes")

    monkeypatch.setattr(report_v1, "render_evaluation_report_v1", _reject)

    status = cli_main(
        ["evaluate", str(program), "--report-version", "v1"]
    )
    captured = capsys.readouterr()

    assert status == 2
    assert captured.out == ""
    error = json.loads(captured.err)
    assert error["schema_version"] == "arqsim.error.v1"
    assert "dynamic recipes" in error["error"]["message"]


def test_cli_incomplete_fidelity_preserves_nested_coverage_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    program = tmp_path / "uncovered.qasm"
    output = tmp_path / "report.json"
    program.write_text(
        'OPENQASM 2.0;\ninclude "qelib1.inc";\n'
        'qreg q[1];\nrz(pi/7) q[0];\n',
        encoding="utf-8",
    )

    status = cli_main(
        [
            "evaluate", str(program), "--representation", "gate",
            "--architecture", "2.3", "--output", str(output),
        ]
    )
    captured = capsys.readouterr()

    assert status == 2
    assert captured.out == ""
    assert not output.exists()
    document = json.loads(captured.err)
    assert document["schema_version"] == "arqsim.error.v1"
    error = document["error"]
    assert error["stage"] == "result_analysis"
    assert error["code"] == "fidelity_coverage_incomplete"
    gaps = error["details"]["coverage_gaps"]
    assert any("rz" in key for key in gaps["unprofiled_logical_operation_counts"])
