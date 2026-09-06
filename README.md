# ArqSim

ArqSim estimates **execution time, physical-qubit footprint, and modeled
success probability** for fault-tolerant quantum programs. It models resource
production, communication, waiting, and contention across quantum architectures.

Version **0.2.0** is a research release. Results are estimates under recorded
model assumptions, not hardware guarantees.

[Demo notebook](examples/notebooks/quickstart.ipynb) ·
[Examples](examples/README.md) · [Documentation](docs/README.md)

## Install from source

Use Python **3.10+**; CI tests 3.10, 3.11, and 3.12.
The PyPI project named `arqsim` is unrelated; install from this repository:

```bash
git clone https://github.com/QuTone/ArqSim.git
cd ArqSim
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
```

On Windows, activate with `.venv\Scripts\activate`. The base installation is
sufficient for the Python demo; Jupyter and the web UI are optional.

## Python quickstart

From the checkout root, evaluate the bundled two-qubit `H`, `S`, `CX`, `T`
circuit. Input files are already synthesized logical OpenQASM 2 circuits.

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

Profile `2.3` combines neutral-atom memory/compute with a remote superconducting
magic-state factory. Fidelity uses `canonical_reference_v1` by default;
only explicit `fidelity_profile=None` disables it. Incomplete coverage raises
an error. See the [model limitations](docs/known-limitations.md) before
interpreting results.

Run the same example with `python examples/quickstart.py`, or use the installed
`arqsim evaluate` CLI. [Examples](examples/README.md) covers CLI commands,
report save/load, architecture selection, and notebook setup. The
[notebook](examples/notebooks/quickstart.ipynb) includes real saved outputs and
runs without the frontend.

## Next steps

| I want to… | Start here |
| --- | --- |
| Run demos and understand results | [Examples](examples/README.md) |
| Use or extend the Python package | [Core package guide](arqsim/README.md) |
| Inspect timelines and resource use | [Frontend setup](https://github.com/QuTone/ArqSim/tree/main/frontend#readme) (full checkout) |
| Use the local HTTP adapter | [Server setup](https://github.com/QuTone/ArqSim/tree/main/server#readme) (full checkout) |
| Read the API, compiler, runtime, or extension contracts | [Documentation index](docs/README.md) |
| Check calibration sources and reproduction limits | [PPM provenance](docs/provenance/ppm/README.md) |

## License

Copyright 2024–2026 Xiang Fang. [Apache-2.0](LICENSE).
See [Third-Party Notices](THIRD_PARTY_NOTICES.md) for separately licensed
benchmark, UI, and calibration material.
