"""Run one small ArqSim evaluation through the supported public API."""

from pathlib import Path

from arqsim import EvaluationConfig, run_evaluation
from arqsim.program import load_ft_workload


SOURCE = Path(__file__).with_name("workloads") / "small.qasm"


def main() -> None:
    circuit = load_ft_workload(SOURCE, "gate")
    report = run_evaluation(circuit, EvaluationConfig(profile_id="2.3"))
    print(f"Latency (s): {report.summary.total_latency_s:.6g}")
    print(f"Physical qubits: {report.summary.total_physical_qubits}")
    print(f"Success probability: {report.summary.success_probability:.6g}")
    assert report.summary.fidelity_complete_coverage
    assert report.summary.all_invariants_satisfied


if __name__ == "__main__":
    main()
