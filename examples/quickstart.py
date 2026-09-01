"""Run one small ArqSim evaluation through the supported public API."""

from pathlib import Path
from pprint import pprint

from heteqsys import EvaluationConfig, run_evaluation
from heteqsys.program import load_ft_workload


SOURCE = Path(__file__).with_name("workloads") / "small.qasm"


def main() -> None:
    circuit = load_ft_workload(SOURCE, "clifford_t")
    report = run_evaluation(circuit, EvaluationConfig(profile_id="2.3"))
    pprint(report.to_dict()["results"]["summary"])


if __name__ == "__main__":
    main()
