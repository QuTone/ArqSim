# ArqSim

ArqSim estimates how a fault-tolerant quantum program runs on a quantum
architecture: its **execution time, physical-qubit footprint, and modeled
success probability**. It tracks resource production, communication, waiting,
and contention so you can inspect why a run takes time and compare architecture
choices under explicit assumptions.

ArqSim 0.2.0 is a research release. Results are model-based estimates; timing,
QEC, resource-supply, and fidelity assumptions are retained in each report.

**Start here:** [executed demo notebook](examples/notebooks/quickstart.ipynb)
· [examples](examples/README.md) · [API reference](docs/01-public-api/public-api.md)

## Install from source

Use Python **3.10 or newer**; CI tests 3.10, 3.11, and 3.12.
Install from this repository. The PyPI project named `arqsim` is a different
project.

```bash
git clone https://github.com/QuTone/ArqSim.git
cd ArqSim
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

On Windows, activate with `.venv\Scripts\activate` instead. The Python demo
requires only the base installation; Jupyter, the web UI, plotting, and the
NWQEC synthesis backend are optional.

## Python quickstart

Run this from the checkout root. The bundled two-qubit example contains
`H`, `S`, `CX`, and `T` logical gates. Profile `2.3` combines neutral-atom
memory/compute with a remote superconducting magic-state factory.

```python
from arqsim import EvaluationConfig, run_evaluation
from arqsim.program import load_ft_workload

circuit = load_ft_workload("examples/workloads/small.qasm", representation="gate")
report = run_evaluation(circuit, EvaluationConfig(profile_id="2.3"))

print(f"Latency (s): {report.summary.total_latency_s:.6g}")
print(f"Physical qubits: {report.summary.total_physical_qubits}")
print(f"Success probability: {report.summary.success_probability:.6g}")
assert report.summary.fidelity_complete_coverage
assert report.summary.all_invariants_satisfied
```

You can also run `python examples/quickstart.py` from the checkout root.
The common API is:

```text
FTCircuit + EvaluationConfig → run_evaluation() → EvaluationReport
```

| Result | How to read it |
| --- | --- |
| `total_latency_s` | Simulated program completion time in seconds, including runtime stalls |
| `total_physical_qubits` | Estimated installed footprint for the resolved architecture |
| `success_probability` | Combined success estimate under the selected fidelity model |
| `fidelity_complete_coverage` | Every required effect has a model; this does not establish experimental calibration |
| `all_invariants_satisfied` | Runtime consistency checks passed |

Defaults select seed zero, full tracing, the `black_box` injection model,
`canonical_reference_v1` fidelity, and `protocol_aware_reference_v1` footprint
accounting. Fidelity is enabled by default. Only explicit
`fidelity_profile=None` disables it; an incomplete model makes the common API
reject the evaluation. See the [model limitations](docs/roadmap/known-limitations.md)
and [PPM calibration provenance](docs/provenance/ppm/README.md) when interpreting
scientific results.

Save a self-contained report and load it later:

```python
from pathlib import Path
from arqsim import EvaluationReport

Path("report.json").write_text(report.to_json(), encoding="utf-8")
loaded = EvaluationReport.from_json(Path("report.json").read_text(encoding="utf-8"))
assert loaded.summary == report.summary
```

Use `report.summary` for common metrics. Advanced inspection uses the report's
resolved architecture, compilation, execution plan, and causal trace. The
[API reference](docs/01-public-api/public-api.md) describes the same supported
result surface for freshly evaluated and loaded reports.

## Try the notebook

The [quickstart notebook](examples/notebooks/quickstart.ipynb) includes saved,
real evaluation outputs, so you can read the demo directly in the repository.
Run it to evaluate a small Clifford+T circuit, compare two architectures, and
round-trip a report. It uses the Python API and needs no backend server.

After the base installation, install and launch Jupyter from the checkout root:

```bash
python -m pip install jupyterlab
python -m jupyterlab examples/notebooks/quickstart.ipynb
```

Choose the Python environment containing ArqSim and use **Restart Kernel and
Run All**. The notebook is self-contained and does not require a working
directory inside the checkout. More examples, including the compiler
walkthrough, are listed in [examples/README.md](examples/README.md).

## Choose an architecture

The bundled profiles are reference designs with workload-aware sizing.
Change `EvaluationConfig(profile_id=...)` to evaluate the same circuit on
another design; compare the resolved assumptions alongside the results.

| Profile | Reference design |
| --- | --- |
| `1.1` | Neutral-atom compute with colocated magic-state supply (default) |
| `1.2` | Superconducting compute with colocated magic-state supply |
| `1.3` | Neutral-atom compute with a remote superconducting magic-state factory |
| `2.1` | Neutral-atom memory, compute, and magic-state supply |
| `2.2` | Neutral-atom memory connected to superconducting compute and supply |
| `2.3` | Neutral-atom memory/compute with a remote superconducting magic-state factory |

Each profile's YAML and sizing/layout policies live in the
[architecture gallery](arqsim/architecture/gallery/). Custom architectures and
model inputs use the [Extension Guide](docs/04-extension/extension-guide.md).

## Evaluate your own program

Replace the example path with your already synthesized logical OpenQASM 2
artifact. Start with a small Clifford+T circuit. `representation="gate"`
selects the parser; the compiler checks the actual operations. The loader
normalizes input into `FTCircuit` and does not perform FT synthesis.

For programs that still need synthesis, `arqsim.synthesizer` provides an optional
NWQEC adapter that produces a normalized artifact. Install it with
`python -m pip install '.[nwqec]'` from the checkout.
Missing latency/fidelity support is reported as an error rather than a partial
success estimate. `WorkloadParseError` comes from `arqsim.program`;
`EvaluationRunError` comes from `arqsim.api` and carries `stage`, `code`, and
`details` for failed evaluations. Supported inputs, advanced configuration, and
failure codes are documented in the [Public Python API](docs/01-public-api/public-api.md).

The installed CLI uses the same evaluation service. Save its JSON output:

```bash
arqsim evaluate examples/workloads/small.qasm \
  --representation gate \
  --architecture 2.3 \
  --run-seed 0 > report.json
```

Run `arqsim evaluate --help` for model overrides, saved configuration, and
output options. The default JSON output is the native Report-v2 contract;
new integrations should use it.

## Inspect runtime behavior

The optional [web UI](frontend/README.md) provides interactive timelines,
Program lineage, Architecture State, and resource/fidelity views over real
backend reports. It is useful after the Python quickstart when you want to
explore a run visually. Its local setup requires Node.js and the FastAPI
adapter; the notebook provides the shorter first-run path.

For a detailed T-teleportation example, the
[measurement-v3 System Case](system_cases/finite_runtime_injection_demo_measurement_v3/README.md)
uses Profile 2.3 with explicit finite injection. It preserves logical T parents
and shows entangle, measurement, reaction, and conditional logical-S children,
including both measurement outcomes:

```bash
python -m system_cases.finite_runtime_injection_demo_measurement_v3.run
```

This advanced opt-in leaves the global `black_box` default unchanged. Its
supported architecture and timing boundaries are described in the guide.

## Understand or extend ArqSim

Internally, logical compilation produces a `LogicalCompilationResult`, plan
lowering produces an `ExecutionPlan`, and the runtime executes Program and
Resource DAGs against shared architectural state. The resulting trace and
checked estimates feed the public `EvaluationReport`. These separate stages
support independent inspection and extension.

| I want to… | Read |
| --- | --- |
| Run an evaluation or understand its output | [Public Python API](docs/01-public-api/public-api.md) |
| Inspect compilation and lowering | [Offline Pipeline API](docs/02-offline-pipeline/offline-pipeline-api.md) |
| Understand scheduling, state commits, or trace accounting | [Evaluation Engine](docs/03-runtime/evaluation-engine.md) |
| Add an architecture, compiler, or model | [Extension Guide](docs/04-extension/extension-guide.md) |
| Navigate code ownership and all Level 0–4 documents | [Documentation index](docs/README.md) |

Contributors can install `python -m pip install -e '.[test]'`, then run
`python -m pytest -q` and `python tests/run_packaging_smoke.py`.
Plotting helpers are available through `python -m pip install '.[visualization]'`.

## License

Copyright 2024-2026 Xiang Fang. ArqSim is distributed under the Apache
License 2.0; see [LICENSE](LICENSE). Separately licensed benchmark, UI, and
calibration material is described in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
