"""Regenerate or verify the two Step 2 runtime behavior baselines."""

from __future__ import annotations

import argparse
import json

from tests.behavior_baseline_support import (
    CASES,
    assert_behavior_baseline,
    load_case_inputs,
    refresh_case_inputs,
    semantic_baseline,
)
from arqsim.api import run_evaluation
from arqsim.report_v1 import render_evaluation_report_v1
from arqsim.schema import normalize_json


def _write_json(path, payload) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--refresh-inputs",
        action="store_true",
        help=(
            "intentionally recreate normalized workload/config inputs and "
            "their outputs"
        ),
    )
    mode.add_argument(
        "--update-outputs",
        action="store_true",
        help="intentionally rewrite semantic/report outputs from frozen inputs",
    )
    mode.add_argument(
        "--update-semantic",
        action="store_true",
        help=(
            "rewrite only the semantic baseline while preserving the frozen "
            "forensic report identity"
        ),
    )
    mode.add_argument(
        "--check",
        action="store_true",
        help="verify without writing (the default)",
    )
    args = parser.parse_args()
    write_outputs = args.refresh_inputs or args.update_outputs
    write_semantic = write_outputs or args.update_semantic

    for case in CASES:
        if args.refresh_inputs:
            refresh_case_inputs(case)

        circuit, config = load_case_inputs(case)
        first = run_evaluation(circuit, config)
        second = run_evaluation(circuit, config)
        if first.to_dict() != second.to_dict():
            raise AssertionError(f"{case.id}: repeated fixed-seed reports differ")

        semantic = semantic_baseline(case, first)
        semantic_path = case.directory / "semantic-baseline.json"
        report_path = case.directory / "report.v1.json"
        rendered_v1 = normalize_json(render_evaluation_report_v1(first))

        # ``diagnostic_hashes.report`` is the identity of the checked-in
        # forensic Report-v1 document, not the native Report-v2 object used to
        # derive the evolving semantic baseline.
        if write_outputs:
            semantic["diagnostic_hashes"]["report"] = rendered_v1[
                "report_hash"
            ]

        if not write_semantic:
            expected = json.loads(semantic_path.read_text(encoding="utf-8"))
            assert_behavior_baseline(semantic, expected)
            print(f"PASS {case.id}")
        else:
            if args.update_semantic and semantic_path.is_file():
                previous = json.loads(semantic_path.read_text(encoding="utf-8"))
                # report.v1.json is the frozen forensic artifact.  Keep the
                # hashes that bind that document while reviewing an intentional
                # change to the evolving semantic execution contract.
                semantic["diagnostic_hashes"] = previous["diagnostic_hashes"]
            _write_json(semantic_path, semantic)
            if write_outputs:
                _write_json(report_path, rendered_v1)
            print(f"WROTE {case.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
