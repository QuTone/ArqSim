"""Generate actual API reports for the frontend preview matrix check."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.src import api  # noqa: E402


def evaluate_case(case: tuple[int, dict, str]) -> dict:
    index, payload, output_dir = case
    label = f"{payload['benchmark_name']} / {payload['config']['profile_id']}"
    try:
        report = api.evaluate_v2(api.EvaluationRequest.model_validate(payload))
        summary = report["results"]["summary"]
        assert summary["fidelity_complete_coverage"] is True
        assert summary["invariant_checks"] and all(summary["invariant_checks"].values())
        instructions = report["artifacts"]["execution_plan"]["program_dag"]["instructions"]
        assert summary["completed_program_instructions"] == len(instructions) > 0
        assert report["results"]["observations"]["discrete_time_log"]
        filename = f"report-{index:03d}.json"
        (Path(output_dir) / filename).write_text(json.dumps(report), encoding="utf-8")
        print(f"Evaluated {label}", flush=True)
        return {"request": payload, "filename": filename}
    except Exception as exc:
        raise RuntimeError(f"Preview evaluation failed: {label}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", action="store_true")
    parser.add_argument("--requests", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.catalog:
        print(json.dumps(api.get_benchmarks()))
        return
    if args.requests is None or args.output is None:
        parser.error("provide --catalog or both --requests and --output")
    requests = json.loads(args.requests.read_text(encoding="utf-8"))
    assert requests, "No preview requests supplied"
    args.output.mkdir(parents=True, exist_ok=True)
    cases = [(index, request, str(args.output)) for index, request in enumerate(requests)]
    with ProcessPoolExecutor(max_workers=2) as pool:
        reports = list(pool.map(evaluate_case, cases))
    (args.output / "index.json").write_text(json.dumps(reports), encoding="utf-8")


if __name__ == "__main__":
    main()
