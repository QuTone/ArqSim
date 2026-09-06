"""Thin versioned HTTP adapter for the public ArqSim evaluation facade.

The native endpoint returns Report v2; the legacy route invokes the explicit
one-way Report-v1 adapter. The service does not build an alternate
frontend-specific timing or footprint model.
"""

from __future__ import annotations

import sys
import re
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, ConfigDict

sys.path.append(str(Path(__file__).parent))

from arqsim import (
    EvaluationConfig,
    FTCircuit,
    run_evaluation,
)
from arqsim.api import EvaluationRunError
from arqsim.architecture import list_architecture_profiles
from arqsim.program import load_ft_workload, workload_stats
from arqsim.report_v1 import render_evaluation_report_v1
from arqsim.schema import normalize_json


app = FastAPI(title="ArqSim Evaluation API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1_024, compresslevel=5)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_CACHE: dict[str, dict[str, Any]] = {}

WorkloadRepresentation = Literal["gate", "clifford_t", "pbc"]


class EvaluationRequest(BaseModel):
    """One public workload/config request.

    Exactly one workload source is accepted:

    * ``benchmark_name`` resolves a bundled normalized FT workload; or
    * ``workload`` carries an existing ``arqsim.ft-workload.v2`` document.

    ``representation`` is always explicit and must agree with the workload
    document. ``config`` is a public ``arqsim.evaluation-config.v1`` document.
    """

    model_config = ConfigDict(extra="forbid")

    representation: WorkloadRepresentation
    config: dict[str, Any]
    benchmark_name: Optional[str] = None
    workload: Optional[dict[str, Any]] = None

@app.get("/")
def root() -> dict[str, str]:
    return {
        "message": "ArqSim backend is running",
        "report_schema": "arqsim.evaluation-report.v2",
        "compatibility_report_schema": "arqsim.evaluation-report.v1",
    }


@app.get("/architecture-profiles")
def get_architecture_profiles() -> list[dict[str, Any]]:
    """Return the canonical capacity-free Profile-v3 catalog."""

    try:
        return [profile.to_dict() for profile in list_architecture_profiles()]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/benchmarks")
def get_benchmarks() -> list[dict[str, Any]]:
    qasm_root = PROJECT_ROOT / "benchmark" / "original-circuit"
    categories = {
        "arithmetic": ["adder", "multiplier", "modexp"],
        "qft": ["qft"],
        "simulation": ["ising", "hubbard", "femo", "qaoa", "vqe"],
        "cryptography": ["shor", "grover"],
    }
    benchmarks: list[dict[str, Any]] = []
    try:
        for file_path in sorted(qasm_root.glob("*.qasm")):
            bench_id = file_path.stem
            if bench_id in BENCHMARK_CACHE:
                benchmarks.append(BENCHMARK_CACHE[bench_id])
                continue
            try:
                normalized = _benchmark_workload_path(bench_id, "clifford_t")
                stats = workload_stats(load_ft_workload(normalized, "clifford_t"))
                category = "other"
                for candidate, keywords in categories.items():
                    if any(keyword in bench_id.lower() for keyword in keywords):
                        category = candidate
                        break
                bench_data = {
                    "id": bench_id,
                    "name": (
                        "ArqSim Timeline Demo"
                        if bench_id == "arqsim_timeline_demo"
                        else bench_id.replace("_", " ").title()
                    ),
                    "tGates": f"{int(stats['t_count']):,}",
                    "depth": int(stats["depth"]),
                    "category": category,
                }
                BENCHMARK_CACHE[bench_id] = bench_data
                benchmarks.append(bench_data)
            except Exception as exc:  # one malformed benchmark must not hide the rest
                print(f"Error parsing {bench_id}: {exc}")
                benchmarks.append(
                    {
                        "id": bench_id,
                        "name": bench_id,
                        "tGates": "-",
                        "depth": 0,
                        "category": "other",
                    }
                )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return benchmarks


def _benchmark_workload_path(
    benchmark_name: str,
    representation: WorkloadRepresentation,
) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", benchmark_name):
        raise ValueError("benchmark_name must be a plain benchmark ID")

    if representation == "pbc":
        candidates = [
            PROJECT_ROOT / "benchmark" / "pbc" / f"{benchmark_name}_pbc.qasm"
        ]
    else:
        root = PROJECT_ROOT / "benchmark" / "clifford+T"
        candidates = sorted(root.glob(f"{benchmark_name}*_transpiled.qasm"))

    matches = [candidate for candidate in candidates if candidate.is_file()]
    if not matches:
        raise FileNotFoundError(
            f"No bundled {representation} workload for {benchmark_name!r}"
        )
    if len(matches) != 1:
        raise ValueError(
            f"Benchmark {benchmark_name!r} has ambiguous {representation} workloads"
        )
    return matches[0]


def _resolve_workload(request: EvaluationRequest) -> FTCircuit:
    has_benchmark = request.benchmark_name is not None
    has_document = request.workload is not None
    if has_benchmark == has_document:
        raise ValueError(
            "Exactly one of benchmark_name or workload must be provided"
        )

    if request.workload is not None:
        circuit = FTCircuit.from_dict(request.workload)
    else:
        source = _benchmark_workload_path(
            request.benchmark_name or "", request.representation
        )
        circuit = load_ft_workload(
            source,
            request.representation,
            provenance={
                "benchmark": request.benchmark_name,
                "source_kind": "bundled_benchmark",
            },
        )

    if circuit.representation != request.representation:
        raise ValueError(
            "representation does not match the resolved FTCircuit: "
            f"{request.representation!r} != {circuit.representation!r}"
        )
    return circuit


def _run_public_evaluation(
    request: EvaluationRequest,
    *,
    report_version: Literal["v1", "v2"],
) -> dict[str, Any]:
    try:
        circuit = _resolve_workload(request)
        config = EvaluationConfig.from_dict(request.config)
        report = run_evaluation(circuit, config)
        if report_version == "v1":
            return normalize_json(render_evaluation_report_v1(report))
        return report.to_dict()
    except EvaluationRunError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.to_dict()["error"],
        ) from exc
    except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        import traceback

        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/evaluate", deprecated=True)
def evaluate(request: EvaluationRequest, response: Response) -> dict[str, Any]:
    """Return the frozen Report-v1 compatibility document."""

    response.headers["Deprecation"] = "true"
    return _run_public_evaluation(request, report_version="v1")


@app.post("/evaluate-v2")
def evaluate_v2(request: EvaluationRequest) -> dict[str, Any]:
    """Return the native self-contained ``evaluation-report.v2`` document."""

    return _run_public_evaluation(request, report_version="v2")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api:app", host="127.0.0.1", port=8002, reload=True)
