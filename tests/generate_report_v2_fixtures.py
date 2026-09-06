"""Check or explicitly refresh the canonical static/dynamic Report-v2 fixtures.

The default mode is read-only.  ``--update`` is required because changing a
checked-in report is a public schema/semantic review action, not routine test
cleanup.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from arqsim.api import (
    CANONICAL_FIDELITY_PRESET,
    EvaluationConfig,
    run_evaluation,
)
from arqsim.evaluation import ExecutionPolicy, RuntimeInjectionMode
from arqsim.operation_profiles import (
    ArrivalDistribution,
    OperationLatencyProfile,
    reference_reaction_latency_profile_v1,
)
from arqsim.program import (
    FTCircuit,
    LogicalLayer,
    LogicalOperation,
    load_ft_workload,
)
from arqsim.report_v2 import load_report_v2_document


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "report_v2"
STATIC_FIXTURE = FIXTURE_DIR / "static-summary.v2.json"
DYNAMIC_T_FIXTURE = FIXTURE_DIR / "dynamic-t-full.v2.json"
SMALL_QASM = ROOT / "tests" / "fixtures" / "small_original.qasm"


def _static_report() -> str:
    circuit = load_ft_workload(SMALL_QASM, "clifford_t")
    latency = OperationLatencyProfile(
        magic_state_arrival=ArrivalDistribution.from_rate(
            1_000.0, kind="deterministic"
        ),
        bell_pair_arrival=ArrivalDistribution.from_rate(
            1_000.0, kind="deterministic"
        ),
    )
    return run_evaluation(
        circuit,
        EvaluationConfig(
            profile_id="1.2",
            run_label="report-v2-static-fixture",
            latency_profile=latency,
            execution_policy=ExecutionPolicy(
                observation_level="summary", run_seed=7
            ),
            fidelity_profile=CANONICAL_FIDELITY_PRESET,
        ),
    ).to_json()


def _dynamic_t_report() -> str:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "t", qubits=(0,)),),
            ),
        ),
        provenance={"fixture": "dynamic-t-full"},
    )
    return run_evaluation(
        circuit,
        EvaluationConfig(
            profile_id="1.1",
            run_label="report-v2-dynamic-t-fixture",
            latency_profile=reference_reaction_latency_profile_v1(),
            execution_policy=ExecutionPolicy(
                observation_level="full",
                run_seed=5,
                injection_lowering_mode=(
                    RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
                ),
            ),
        ),
    ).to_json()


def render_current_fixtures() -> dict[Path, str]:
    rendered = {
        STATIC_FIXTURE: _static_report(),
        DYNAMIC_T_FIXTURE: _dynamic_t_report(),
    }
    for text in rendered.values():
        load_report_v2_document(text)
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--update",
        action="store_true",
        help="explicitly replace the committed Report-v2 fixtures",
    )
    args = parser.parse_args(argv)

    first = render_current_fixtures()
    second = render_current_fixtures()
    if first != second:
        raise AssertionError("Fixed Report-v2 fixture configs were nondeterministic")

    if args.update:
        FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
        for path, rendered in first.items():
            path.write_text(rendered, encoding="utf-8")
            print(f"UPDATED {path.relative_to(ROOT)}")
        return 0

    failed = False
    for path, rendered in first.items():
        if not path.is_file():
            print(f"MISSING {path.relative_to(ROOT)}; review and use --update")
            failed = True
            continue
        stored = path.read_text(encoding="utf-8")
        load_report_v2_document(stored)
        if stored != rendered:
            print(f"MISMATCH {path.relative_to(ROOT)}; review before --update")
            failed = True
        else:
            print(f"PASS {path.relative_to(ROOT)}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
