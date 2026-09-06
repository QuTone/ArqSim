"""Check or explicitly refresh the public report fixtures.

The default mode verifies both the native Report-v2 fixture and the frozen
Report-v1 compatibility fixture. Updating either document is an intentional
schema review action and therefore requires an explicit flag. Report v1 has a
separate flag because it should change only during a reviewed breaking
migration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from arqsim.api import (
    EvaluationConfig,
    load_evaluation_report_document,
    run_evaluation,
)
from arqsim.program import FTCircuit
from arqsim.report_v1 import render_evaluation_report_v1
from arqsim.schema import normalize_json


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (
    ROOT
    / "tests"
    / "fixtures"
    / "behavior_baselines"
    / "pbc_magic_measurement_v1"
)
FIXTURE = ROOT / "tests" / "fixtures" / "public_api" / "report-v2.json"
V1_FIXTURE = ROOT / "tests" / "fixtures" / "public_api" / "report-v1.json"


def render_current_report() -> str:
    circuit = FTCircuit.from_json(
        (SOURCE / "workload.json").read_text(encoding="utf-8")
    )
    config = EvaluationConfig.from_json(
        (SOURCE / "config.json").read_text(encoding="utf-8")
    )
    first = run_evaluation(circuit, config).to_json()
    second = run_evaluation(circuit, config).to_json()
    if first != second:
        raise AssertionError("Fixed config/seed did not produce one report")
    load_evaluation_report_document(first)
    return first


def render_v1_compatibility_report() -> str:
    circuit = FTCircuit.from_json(
        (SOURCE / "workload.json").read_text(encoding="utf-8")
    )
    config = EvaluationConfig.from_json(
        (SOURCE / "config.json").read_text(encoding="utf-8")
    )
    first = json.dumps(
        normalize_json(render_evaluation_report_v1(run_evaluation(circuit, config))),
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    second = json.dumps(
        normalize_json(render_evaluation_report_v1(run_evaluation(circuit, config))),
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    if first != second:
        raise AssertionError("Fixed config/seed did not produce one report")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--update",
        action="store_true",
        help="explicitly replace the committed native public Report-v2 fixture",
    )
    parser.add_argument(
        "--update-v1",
        action="store_true",
        help=(
            "explicitly replace the frozen Report-v1 compatibility fixture; "
            "use only for a reviewed breaking migration"
        ),
    )
    args = parser.parse_args(argv)
    rendered = render_current_report()
    rendered_v1 = render_v1_compatibility_report()
    if args.update_v1:
        V1_FIXTURE.write_text(rendered_v1, encoding="utf-8")
        print(f"UPDATED {V1_FIXTURE.relative_to(ROOT)}")
    elif V1_FIXTURE.read_text(encoding="utf-8") != rendered_v1:
        print(
            "MISMATCH "
            f"{V1_FIXTURE.relative_to(ROOT)}; the frozen v1 fixture changed"
        )
        return 1
    if args.update:
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(rendered, encoding="utf-8")
        print(f"UPDATED {FIXTURE.relative_to(ROOT)}")
        return 0
    if not FIXTURE.is_file():
        print(
            "MISSING "
            f"{FIXTURE.relative_to(ROOT)}; review and run with --update"
        )
        return 1
    stored = FIXTURE.read_text(encoding="utf-8")
    load_evaluation_report_document(stored)
    if stored != rendered:
        print(
            "MISMATCH "
            f"{FIXTURE.relative_to(ROOT)}; review before using --update"
        )
        return 1
    print(f"PASS {FIXTURE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
