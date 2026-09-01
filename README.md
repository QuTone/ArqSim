# ArqSim

ArqSim is a system-level evaluation framework for fault-tolerant quantum
architectures. It co-executes a finite Program DAG and a streaming Resource DAG
against the same hierarchical architectural state, preserving stalls,
contention, buffer occupancy, resource readiness, and qubit locations without
materializing a monolithic full-system circuit.

ArqSim is the public project name. The installable Python distribution, import
package, and CLI remain named `heteqsys`. The v0.2 serialized contracts use
the `arqsim.*` namespace.

## Core model

```text
source program ──► Synthesizer ──► FTCircuit
FTCircuit ──► CircuitStatistics ──► SizingPolicy ──► SizingResult

ArchitectureProfile + SizingResult
  ──► LogicalLayoutPolicy ──► LogicalLayoutResult

ArchitectureProfile + SizingResult + LogicalLayoutResult
  + QEC bindings + selected resource-protocol profiles
  ──► ArchitectureSpecification

FTCircuit + ArchitectureSpecification ──► Compiler ──► LogicalCompilationResult

LogicalCompilationResult + ArchitectureSpecification + operational bindings
  ──► staged plan lowering ──► ExecutionPlan v6
        ├── ProgramDAG
        ├── ResourceDAG
        └── architectural inventory

ExecutionPlan v6 ──► ArchitectureState ──► ExecutionTrace v3 + EvaluationResult

request + resolved inputs + LogicalCompilationResult + Plan v6 + Trace v3
  + checked results ──► EvaluationReport v2
```

Program dependencies stay in `ProgramDAG`. Resource-production dependencies
stay in `ResourceDAG`. They interact only through `ArchitectureState` guards
and transitions at runtime.

Bundled reference architectures are co-located under
`heteqsys/architecture/gallery/`: each named bundle contains its unsized
`profile.yaml`, sizing policy, and logical-layout policy. A shared
`QuantileSizingConfig` is experiment input to those independent policies; the
generic constructor and resolver do not import the gallery.

## Package layout

```text
heteqsys/
├── architecture/       canonical static model and gallery; internal isa.py/state.py are quarantined
├── qec/                code/protocol catalogs and exact binding vocabulary
├── program/            FTCircuit and circuit readers
├── synthesizer/        external synthesis adapters and artifact cache
├── compiler/           direct canonical-spec allocation, mapping, routing, and movement
├── operation_profiles/ latency distributions and fidelity inputs
├── evaluation/         ExecutionPlan v6, staged lowering, four-role runtime, trace, and estimators
├── visualization/      pure views over public circuit/DAG/trace objects
├── api.py               typed one-shot evaluation service and native Report-v2 facade
├── report_v2.py         strict self-contained Report-v2 renderer/parser
├── report_v1.py         explicit one-way compatibility renderer
└── cli.py

frontend/                 React UI plus its thin FastAPI adapter
```

Compiler, footprint accounting, plan lowering, evaluator, and the public API
consume the canonical `ArchitectureSpecification` directly; the former
architecture compatibility package and resolved containers are not part of
the supported model.
Native `EvaluationReport` is the self-contained Report-v2 boundary. The
retained report-v1 path is an explicit one-way plain-JSON compatibility
renderer; it does not reconstruct a second architecture object model, feed
report data back into execution, or represent dynamic recipes.

The compiler/plan seam is explicit: logical compilation returns the strict,
hashed `LogicalCompilationResult` record, including the effective
magic-state consumption policy and each route's typed `dispatch_deferred`
state. Plan lowering rejects a result whose policy differs from the evaluation
policy and creates a deferred recipe only from that typed route state. Eager
Program movement uses typed `MoveOperands`; state-bound Program and Resource
work carries tagged deferred-dispatch recipes; and plan lowering consumes
transient typed cost inputs rather than recovering control or cost facts from
metadata. The current `arqsim.execution-plan.v6` boundary includes typed
implementation recipes and the dynamic Program frontier; older documents
fail closed instead of being silently reinterpreted. Its codec is covered by a serialize → parse → explicit
`evaluate()` gate. In compiler output, `ComputePartition.active_qubits` names
the qubits touched by that source-operation partition; the keys of
`CompiledComputeUnit.mapping` are the authority for which logical qubits are
actually compute-resident. The strict plan decoder accepts only exact JSON
object/array/scalar shapes, and official state-bound callbacks are checked
against the plan's source hashes before execution through the built-in
realizer.

Program instructions and recurrent Resource processes share one flat,
immutable `OperationClaims` contract for buffer, forwarding, engine, Module,
and link claims. It is an in-memory base contract, not a nested wire object or
another hash authority; the Program and Resource codecs keep those fields
flat. The runtime manifest is
`arqsim.runtime-manifest.v3`: one `RuntimeRealizer` owns tentative binding
and the supported state-bound compiler callbacks, followed by Scheduler,
execution backend, and outcome model. The former separate binding/compiler/
Resource-resolver roles and their pass-through request/result metadata chain
were deleted.

`arqsim.execution-trace.v3` stores and hashes the ordered transition ledger,
typed Program-work lineage, measurements and continuation receipts, its
initial/terminal state projections, and ledger-derived terminal
in-flight Resource dispatches. Completed event spans are derived Python views;
only the one-way report-v1 renderer injects them into the compatibility report.
The full discrete-time log remains a strictly checked diagnostic cache for
wait/occupancy attribution, never a second causal authority.

`arqsim.evaluation-report.v2` is the native Python, CLI, and integration
boundary. Its exact seven top-level fields are `schema_version`,
`workflow_id`, `request`, `resolved_inputs`, `artifacts`, `results`, and
`report_hash`. It embeds the logical compilation, Plan v6, and Trace v3 exactly;
strict loading re-lowers compilation, replays Trace, validates observations,
and recomputes derived results without rerunning compiler or runtime. Report v1
is available only through its explicit adapter or CLI `--report-version v1`
during the compatibility window.

The explicit `runtime_injection_mode` keeps injection policy separate from
timing data. The default `black_box` mode is monolithic;
`finite_state_injection_v1` enables a finite typed
recipe: the initial T slice executes CX(data, magic), measures magic, and emits
a materialized logical S only on outcome one. The same contract supports exact
finite STAR angle-doubling chains with branch-only resource consumption. Reaction-latency
data is a separate input and never activates the semantic mode implicitly.

`architecture/isa.py` and `architecture/state.py` remain internal execution
support despite their physical package location; neither extends the static
architecture hierarchy. Moving only one would create a less coherent split,
so any later source move should relocate the ISA/claims/recipes and mutable
state together behind an execution-owned package boundary.

The installable package is the only implementation source of truth. Historical
engines, generated results, and private research workflows are not runtime
dependencies and are excluded from the public source tree.

## Repository layout

```text
heteqsys/     installable framework
tests/        regression and contract tests
docs/         maintained architecture and evaluation documentation
examples/     small inputs and public-API examples
system_cases/ frozen, end-to-end evaluation contracts for source checkouts
frontend/     React UI, thin FastAPI adapter, and attributed benchmark catalog
```

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install /path/to/heteqsys-0.2.0-py3-none-any.whl
```

For a source checkout, use `python -m pip install .`. Contributors can instead
install `python -m pip install -e '.[test]'` and run `pytest -q`.

Plotting helpers are intentionally optional; install them with
`python -m pip install 'heteqsys[visualization]'`. The NWQEC synthesis adapter
is available through `python -m pip install 'heteqsys[nwqec]'`.

## Web UI

The frontend is a local research and development UI, not a production-ready
network service. It is part of this monorepo; start the API and browser
development servers in separate terminals:

```bash
cd frontend/backend
./setup.sh
source venv/bin/activate
uvicorn api:app --app-dir src --host 127.0.0.1 --port 8002
```

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5174`. Keep the development API bound to loopback unless
you have added deployment authentication, origin restrictions, and resource
limits. See [frontend/README.md](frontend/README.md) for
the report adapter, demo workflow, and production build gates.

## Python quickstart

This example is self-contained; it does not rely on files from the source
repository.

```python
from heteqsys import (
    EvaluationConfig,
    FTCircuit,
    run_evaluation,
)
from heteqsys.program import LogicalLayer, LogicalOperation

circuit = FTCircuit(
    representation="clifford_t",
    num_qubits=2,
    num_clbits=0,
    layers=(
        LogicalLayer(0, (LogicalOperation("gate", "h", (0,)),)),
        LogicalLayer(
            1,
            (
                LogicalOperation(
                    "gate",
                    "rz",
                    (0,),
                    parameters=(0.39269908169872414,),
                ),
            ),
        ),
        LogicalLayer(2, (LogicalOperation("gate", "cx", (0, 1)),)),
        LogicalLayer(3, (LogicalOperation("gate", "t", (1,)),)),
    ),
)

report = run_evaluation(circuit, EvaluationConfig(profile_id="2.3"))
summary = report.to_dict()["results"]["summary"]
print(summary["total_latency_s"])
print(summary["total_physical_qubits"])
assert all(summary["invariant_checks"].values())
```

## CLI quickstart

Create a small synthesized input anywhere on the filesystem:

```bash
cat > /tmp/arqsim-small.qasm <<'QASM'
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
h q[0];
rz(pi/8) q[0];
cx q[0],q[1];
t q[1];
QASM
```

Then evaluate it through the installed command:

```bash
heteqsys evaluate /tmp/arqsim-small.qasm \
  --representation clifford_t \
  --architecture 2.3 \
  --seed 0 \
  --output /tmp/arqsim-example.json
```

The evaluator starts cold. Magic states and logical Bell pairs are produced by
streaming resource processes using deterministic 1,000-item/s intervals by
default. Select an exponential arrival model explicitly when stochastic
production is desired.

Architecture sizing uses the workload-aware layout policy.  Override one
versioned policy field with `--set`, for example:

```bash
heteqsys evaluate /tmp/arqsim-small.qasm \
  --representation clifford_t \
  --architecture 2.3 \
  --set protocols.magic_state.buffer_capacity=4
```

The CLI and Python facade return the same native
`arqsim.evaluation-report.v2` document by default. During the compatibility
window, integrations that have not migrated may request the frozen one-way
projection explicitly with `--report-version v1`; new consumers should not
depend on it. Fidelity is opt-in via
`--fidelity-profile canonical-reference-v1` because the bundled reference
profile contains literature-derived assumptions.

A serialized `arqsim.evaluation-config.v1` request can be replayed without
reconstructing flags:

```bash
heteqsys evaluate /tmp/arqsim-small.qasm \
  --representation clifford_t \
  --config /path/to/config.json
```

`--config` is intentionally exclusive with configuration-building flags. The
v2 report keeps the original request under `request` and records canonical
resolved authorities under `resolved_inputs`, with independent hashes on the
embedded architecture, logical compilation, execution plan, and causal trace.

A source checkout also includes `python examples/quickstart.py`. See
[Architecture Specification](docs/architecture-specification.md) for Profiles,
resolved layouts, and logical coordinates; [Extension Guide](docs/extension-guide.md)
for supported extension seams; [Report and Trace Schema](docs/report-and-trace-schema.md)
for the implemented output boundary;
[Report/API v2 Contract](docs/report-api-v2-contract.md) for the implemented
frozen boundary; and [Known Limitations](docs/known-limitations.md)
for modeling scope and deferred capabilities. The lower-level contracts are documented
in [Core Contracts](docs/core-contracts.md), [Evaluation Engine](docs/evaluation-engine.md),
and [Repository Structure](docs/repository-structure.md). Stable, advanced,
and internal extension surfaces are listed in
[Public Python API](docs/public-api.md).

## Finite runtime-injection demonstration System Case

The repository includes a separate, ArqSim-owned four-qubit System Case for reviewing
dynamic T-gate teleportation and location-aware fidelity without changing the
global `black_box` default:

```bash
python system_cases/finite_runtime_injection_demo/run.py
```

The fixed profile-2.3/seed-5 run contains two source T operations and covers
both measurement outcomes in one trace. Outcome one materializes
source → classical reaction → logical S; outcome zero terminates after the
reaction. The gate strictly reloads and replays native Report v2, verifies a
fresh byte-identical public-API run, requires every runtime invariant and full
fidelity coverage, and checks nonzero logical-qubit and resource-state idling.
Its owned input, explicit request, hashes, reference report, receipt, mutation
policy, and review commands are documented in the
[finite runtime-injection System Case guide](system_cases/finite_runtime_injection_demo/README.md).

## License

Copyright 2024-2026 Xiang Fang. ArqSim is distributed under the Apache
License 2.0; see [LICENSE](LICENSE). The frontend benchmark catalog and UI
components include separately licensed third-party material documented in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
