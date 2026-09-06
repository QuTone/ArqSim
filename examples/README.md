# Examples

Start with the [executed quickstart notebook](notebooks/quickstart.ipynb), or
run `python examples/quickstart.py`. Both use the same two-qubit H, S, CX, T
workload as the repository README. They need only the base ArqSim package;
no web server or synthesis backend is required.

Commands below run from the repository root in your Python environment.
Install with `python -m pip install .`; see the [source installation guide](../README.md#install-from-source)
for environment setup. The unrelated PyPI package named `arqsim` is not this
project.

## Run the notebook or scripts

The notebook includes real saved outputs, a two-architecture comparison, and
a report round trip. To execute it locally:

```bash
python -m pip install jupyterlab
python -m jupyterlab examples/notebooks/quickstart.ipynb
```

Select the environment containing ArqSim and use **Restart Kernel and Run
All**. Jupyter is only the runner. The notebook itself is independent of the
working directory.

| Example | Purpose | Command |
| --- | --- | --- |
| [Quickstart](quickstart.py) | Load OpenQASM 2 and print headline results | `python examples/quickstart.py` |
| [Compiler IR walkthrough](compiler_ir_walkthrough.py) | Inspect circuit, compilation, and Plan documents | `python examples/compiler_ir_walkthrough.py > compiler-artifacts.json` |

## Read the result

The common path is `FTCircuit + EvaluationConfig → run_evaluation() →
EvaluationReport`. Start with `report.summary`:

| Field | Meaning |
| --- | --- |
| `total_latency_s` | Simulated Program completion time, including stalls, in seconds; multiply by 1,000 for milliseconds |
| `total_physical_qubits` | Installed architecture footprint under the selected model, not the logical-qubit count |
| `success_probability` | Combined success estimate under the selected fidelity model |
| `fidelity_complete_coverage` | Every required effect has a model; this does not establish experimental calibration |
| `all_invariants_satisfied` | All named terminal runtime consistency checks passed |

Defaults use seed zero, full tracing, `black_box` T injection,
`canonical_reference_v1` fidelity, and `protocol_aware_reference_v1` footprint.
Only explicit `fidelity_profile=None` disables fidelity; incomplete coverage
makes the common API reject the run. These are reference-model estimates,
not measured hardware performance. Consult the [known limitations](../docs/known-limitations.md)
and [calibration provenance](../docs/provenance/ppm/README.md) before interpreting
scientific results.

## Save and reload a report

This complete example writes the portable Report-v2 artifact and reads it back:

```python
from pathlib import Path
from arqsim import EvaluationConfig, EvaluationReport, run_evaluation
from arqsim.program import load_ft_workload

circuit = load_ft_workload("examples/workloads/small.qasm", representation="gate")
report = run_evaluation(circuit, EvaluationConfig(profile_id="2.3"))
Path("report.json").write_text(report.to_json(), encoding="utf-8")
loaded = EvaluationReport.from_json(Path("report.json").read_text(encoding="utf-8"))
assert loaded.summary == report.summary
assert loaded.report_hash == report.report_hash
```

The report retains the request, resolved assumptions, compilation, Plan,
Trace, and checked results. Loading validates that evidence without rerunning
the compiler or runtime. The [public API guide](../docs/01-public-api/public-api.md)
describes the supported fields for live and loaded reports.

## Choose an architecture

Change `EvaluationConfig(profile_id=...)` to compare the same circuit across
reference designs. Each profile resolves its own sizing, layout, QEC, and
resource-protocol assumptions; this is not an equal-hardware-budget comparison.

| Profile | Reference design |
| --- | --- |
| `1.1` | Neutral-atom compute and colocated magic-state supply (default) |
| `1.2` | Superconducting compute and colocated magic-state supply |
| `1.3` | Neutral-atom compute with a remote superconducting factory |
| `2.1` | Neutral-atom memory, compute, and magic-state supply |
| `2.2` | Neutral-atom memory with superconducting compute and supply |
| `2.3` | Neutral-atom memory/compute with a remote superconducting factory |

Profiles and sizing/layout policies live in the [architecture gallery](../arqsim/architecture/gallery/).

## Evaluate your own input

Pass your already synthesized logical OpenQASM 2 file to `load_ft_workload()`.
`representation="gate"` selects the parser; the compiler checks the actual
operations. The loader normalizes the input and does not perform FT synthesis.
If synthesis is needed, the optional NWQEC adapter is available through
`python -m pip install '.[nwqec]'`; see the [public API guide](../docs/01-public-api/public-api.md).

The installed CLI calls the same service and writes native Report-v2 JSON:

```bash
arqsim evaluate examples/workloads/small.qasm \
  --representation gate \
  --architecture 2.3 \
  --run-seed 0 > report.json
```

Run `arqsim evaluate --help` for saved configuration and model overrides.
Parsing errors use `arqsim.program.WorkloadParseError`; evaluation errors use
`arqsim.api.EvaluationRunError`, with `stage`, `code`, and `details`.

## Go further

| Task | Guide |
| --- | --- |
| Inspect compilation and lowering | [Offline pipeline](../docs/02-offline-pipeline/offline-pipeline-api.md), [IR walkthrough](../docs/02-offline-pipeline/compiler-ir-walkthrough.md) |
| Inspect scheduling, transactions, and trace accounting | [Evaluation Engine](../docs/03-runtime/evaluation-engine.md) |
| Run finite T injection with both measurement outcomes | [measurement-v3 System Case](../system_cases/finite_runtime_injection_demo_measurement_v3/README.md) |
| Explore reports interactively | [Web UI](https://github.com/QuTone/ArqSim/tree/main/frontend#readme) |
| Add an architecture, compiler, or model | [Extension Guide](../docs/04-extension/extension-guide.md) |
| Browse all Level 0–4 documentation | [Documentation index](../docs/README.md) |

Optional plotting helpers use `python -m pip install '.[visualization]'`.
Contributors can install `python -m pip install -e '.[test]'`, then run
`python -m pytest -q` and `python tests/run_packaging_smoke.py`.
