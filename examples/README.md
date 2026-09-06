# Examples

Start with the [executed quickstart notebook](notebooks/quickstart.ipynb) for a
self-contained Clifford+T circuit, an explanation of the report summary, and
a comparison of two reference architectures. Its saved outputs can be read
directly, and its code cells need only the base ArqSim installation.
It loads the same H, S, CX, T workload as the Python quickstart below.

Install from the repository root into your Python environment:

```bash
python -m pip install .
```

The bare PyPI package named `arqsim` is unrelated to this repository. To run
the notebook locally, install a notebook runner in the same environment:

```bash
python -m pip install jupyterlab
python -m jupyterlab examples/notebooks/quickstart.ipynb
```

| Example | Purpose | Run from the repository root |
| --- | --- | --- |
| [Quickstart notebook](notebooks/quickstart.ipynb) | Read and compare real evaluation reports | Open in your notebook runner |
| [Python quickstart](quickstart.py) | Load a small QASM file and print its summary | `python examples/quickstart.py` |
| [Compiler IR walkthrough](compiler_ir_walkthrough.py) | Inspect the circuit, compilation, and execution-plan documents | `python examples/compiler_ir_walkthrough.py` |

These examples use already synthesized inputs and do not require NWQEC or the
web UI. The [public API guide](../docs/01-public-api/public-api.md) explains the
supported configuration and report interfaces; the [compiler walkthrough
guide](../docs/02-offline-pipeline/compiler-ir-walkthrough.md) explains the
lower-level artifacts.
