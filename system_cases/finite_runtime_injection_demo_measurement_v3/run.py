#!/usr/bin/env python3
"""Run the Plan-v9 logical-measurement-provider System Case."""

from __future__ import annotations

import argparse
from pathlib import Path

from system_cases.finite_runtime_injection_demo import run as _shared


CASE_ROOT = Path(__file__).resolve().parent
MANIFEST_PATH = CASE_ROOT / "manifest.yaml"
CASE_ID = "clifford_t_toy__2.3__seed_0"
SystemCaseError = _shared.SystemCaseError
LoadedSystemCase = _shared.LoadedSystemCase
SystemCaseRun = _shared.SystemCaseRun


def load_system_case(*, verify_reference_files: bool = True) -> LoadedSystemCase:
    return _shared.load_system_case(
        verify_reference_files=verify_reference_files,
        manifest_path=MANIFEST_PATH,
        expected_id=_shared.LIVE_ACCEPTANCE_CASE_ID,
    )


def build_config(system_case: LoadedSystemCase):
    return _shared.build_config(system_case)


def build_request(system_case: LoadedSystemCase):
    return _shared.build_request(system_case)


def run_case(system_case: LoadedSystemCase) -> SystemCaseRun:
    return _shared.run_case(system_case)


def verify_reference(*, rerun: bool = True) -> SystemCaseRun:
    verified = _shared.verify_reference(
        rerun=rerun,
        system_case=load_system_case(),
    )
    if not isinstance(verified, SystemCaseRun):
        raise SystemCaseError("The measurement-v3 successor must use strict live replay")
    return verified


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-rerun",
        action="store_true",
        help="strictly reload/replay references without a fresh public-API run",
    )
    args = parser.parse_args(argv)
    verified = verify_reference(rerun=not args.no_rerun)
    print(
        "PASS "
        f"{verified.receipt['case_id']} "
        f"report={verified.report.report_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
