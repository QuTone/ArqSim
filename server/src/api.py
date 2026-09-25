"""Thin versioned HTTP adapter for the public ArqSim evaluation facade.

The native endpoint returns Report v2; the legacy route invokes the explicit
one-way Report-v1 adapter. The service does not build an alternate
frontend-specific timing or footprint model.
"""

from __future__ import annotations

import sys
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, ConfigDict, Field

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
    ``preview_max_layers`` optionally evaluates only the first N normalized
    input layers. Omit it for a complete workload evaluation.
    """

    model_config = ConfigDict(extra="forbid")

    representation: WorkloadRepresentation
    config: dict[str, Any]
    benchmark_name: Optional[str] = None
    workload: Optional[dict[str, Any]] = None
    preview_max_layers: Optional[int] = Field(default=None, strict=True, ge=1, le=256)

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
            representations = _benchmark_representations(bench_id)
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
                    "representations": representations,
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
                        "representations": representations,
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


def _benchmark_representations(benchmark_name: str) -> list[str]:
    """Advertise only normalized derivatives that resolve unambiguously."""

    available: list[str] = []
    for representation in ("clifford_t", "pbc"):
        try:
            _benchmark_workload_path(benchmark_name, representation)
        except (FileNotFoundError, ValueError):
            continue
        available.append(representation)
    return available


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
        if request.preview_max_layers is not None:
            circuit = _prefix_preview(circuit, request.preview_max_layers)
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


def _prefix_preview(circuit: FTCircuit, max_layers: int) -> FTCircuit:
    """Select whole input layers before architecture resolution and execution.

    Register widths and operations are preserved. The resulting architecture,
    compilation, trace and metrics describe this prefix circuit, not the first
    timestamps of a separately sized and compiled complete workload.
    """

    layers = circuit.layers[:max_layers]
    scope = {
        "kind": "prefix_preview",
        "requested_max_layers": max_layers,
        "source_workload_hash": circuit.semantic_hash,
        "source_layer_count": len(circuit.layers),
        "source_operation_count": circuit.operation_count,
        "evaluated_layer_count": len(layers),
        "evaluated_operation_count": sum(len(layer.operations) for layer in layers),
        "truncated": len(layers) < len(circuit.layers),
    }
    return replace(
        circuit,
        layers=layers,
        provenance={**circuit.provenance, "evaluation_scope": scope},
    )


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
