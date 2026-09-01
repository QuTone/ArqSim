# `heteqsys` package

`heteqsys` is ArqSim's installable core. Its supported user-facing path is
intentionally small:

- `FTCircuit` is the synthesized logical Program input;
- `ArchitectureProfile` describes capacity- and QEC-free structure;
- `ArchitectureSpecification` is the resolved, workload-specific architecture
  receipt built by `build_architecture_specification`;
- `OperationLatencyProfile` and `FidelityProfile` supply measured or simulated
  evaluation facts; and
- `EvaluationConfig`, `EvaluationReport`, and `run_evaluation` define the
  versioned one-shot service shared by CLI and future HTTP adapters.

The architecture concepts are `ArchitectureProfile ->
ArchitectureSpecification -> ArchitectureState(t)`. Layout contexts/plans,
`ArchitectureSpec`, `QECConfiguration`, and `ResolvedFTSystemSpec` were
temporary migration containers and were deleted; they are not extra
hierarchy levels or supported imports. Compiler pipelines, DAGs, traces, and
direct evaluation remain advanced composition interfaces.
`ArchitectureState` and event-loop machinery are internal and are not user
configuration.

Compiler, footprint accounting, execution-plan lowering, evaluator, and
`run_evaluation()` all consume the canonical `ArchitectureSpecification`
directly. `ExecutionPlan` uses the strict
`arqsim.execution-plan.v6` wire contract. V6 fails closed on former shapes
because typed implementation recipes and the dynamic frontier are part of its
meaning. Native `EvaluationReport` uses the self-contained
`arqsim.evaluation-report.v2` boundary. The report-v1 module remains only as
an explicit pure one-way renderer from typed run artifacts to compatibility
JSON; it never hydrates an older architecture/QEC object, accepts dynamic
recipes, or feeds output into execution.

The compiler boundary is self-contained. A `CompilerPipeline`
returns the immutable `heteqsys.logical-compilation-result.v1`
`LogicalCompilationResult`, including source hashes, effective compiler spec, typed
compute units, mapping state, effective magic-state consumption policy, and its
own semantic hash. Each compiled route explicitly owns a boolean
`dispatch_deferred` state; route metrics cannot duplicate that control fact.
Plan lowering validates the three source hashes, policy equality, and exact
source-operation coverage before consuming the result, rechecks compiled work
against canonical compute/magic capacities, records `compilation_hash` in plan
provenance, and creates a magic-route recipe only when the typed route state
says dispatch is deferred. `ComputePartition.active_qubits` is the operated
source-partition set, while `CompiledComputeUnit.mapping` keys own compute
residency and may include filler qubits selected by the default compiler to
reach exactly `min(circuit width, compute capacity)` residents
deterministically.
Eager Program movement uses `MoveOperands`; unresolved state-dependent Program
routing and Resource movement use tagged
`arqsim.deferred-dispatch-recipe.v1` records instead of hidden metadata
requests. The runtime-component manifest identifies callback algorithms; for
ArqSim's official state-bound realizer, evaluation separately verifies both
captured Program/Resource callback contexts against the plan's existing
circuit/architecture/compiler/latency authorities.

The flat immutable `OperationClaims` base is shared between
`ArchitectureInstruction` and `ResourceProcess`. The base owns only buffer,
forwarding, engine, target-Module, and target-link claims; plane-specific
operands, locations, recurrence policy, and descriptive receipts stay on the
concrete operation. It has no independent codec or hash, and each operation's
strict wire format keeps these claims as flat fields.

The strict `arqsim.runtime-manifest.v3` records four roles:
`runtime_realizer`, `scheduler`, `execution_backend`, and `outcome_model`.
The realizer is the single pure pre-commit boundary that proposes a
`TentativeBinding` and applies the supported Program or Resource deferred
callback. A candidate carries typed identity, operation, binding, duration,
and compiler artifact; backend preparation returns only duration plus a
separate artifact. Neither forwards a second generic control-metadata record.

The canonical trace is `arqsim.execution-trace.v3`. Its serialized/hash
authority contains transitions, typed Program-lineage/measurement/continuation
receipts, and state projections, not completed event
spans. `ExecutionTrace.events` and `EvaluationResult.events` derive the v1
timeline view from matching dispatch/completion transitions; the report-v1
renderer is the only boundary that serializes that projection. The full
`discrete_time_log` is a plan-and-ledger-validated diagnostic cache for exact
wait and occupancy attribution, not causal input and not part of `trace_hash`.

Report v2 embeds `LogicalCompilationResult`, Plan v6, and Trace v3 as the
unique stage authorities under `artifacts`. Its exact outer fields are
`schema_version`, `workflow_id`, `request`, `resolved_inputs`, `artifacts`,
`results`, and `report_hash`. The strict codec re-lowers compilation, replays
Trace, validates observations, and recomputes footprint, fidelity, analysis,
and summary without rerunning compiler or runtime.

`EvaluationPolicy.runtime_injection_mode` is independent of latency data.
`black_box` is the default; `finite_state_injection_v1` opts into finite typed
T/angle-doubling recipe expansion and requires an explicit reaction-latency key
for the active compute modality. The named reference latency preset supplies timing
values only and does not silently select the recipe mode.

An `ArchitectureSpecification` is logical/evaluation-level input to the
compiler and evaluator, not a physical-qubit placement. Its physical-footprint
record is an estimate for accounting. Code-aware patch/coupler placement and
LightStim lowering remain a later backend boundary.

The target open-source contract is documented in
[`docs/core-contracts.md`](../docs/core-contracts.md). Current object ownership
and execution-loop behavior are documented in
[`docs/evaluation-engine.md`](../docs/evaluation-engine.md).
The implemented, frozen Report/API-v2 boundary is recorded in
[`docs/report-api-v2-contract.md`](../docs/report-api-v2-contract.md).
The audited owner-scoped exports and release/deprecation gates are in
[`docs/public-api.md`](../docs/public-api.md).

Public modules must not import historical implementations. Visualization is a
pure consumer and may not calculate evaluation semantics.

## Suggested reading order

1. `api.py` defines the supported one-shot request and report contract.
2. `program/circuit.py` and `program/load.py` define the workload boundary.
3. `architecture/profile.py`, `architecture/sizing.py`,
   `architecture/logical_layout.py`, `architecture/logical_layout_policy.py`,
   and `architecture/resolver.py` define the canonical construction path into
   `architecture/specification.py`.
4. `compiler/pipeline.py` and `compiler/output.py` produce the typed logical
   compilation result.
5. `evaluation/lowering.py`, `evaluation/_program_lowering.py`, and
   `evaluation/_plan_inputs.py` lower explicit compiler, topology, and cost
   inputs into the two DAGs; `evaluation/plan.py` owns their strict envelope.
6. `evaluation/engine.py` explicitly executes that plan.
7. `report_v2.py` owns the strict self-contained wire codec; `report_v1.py`
   owns only the temporary one-way compatibility projection.
8. `visualization/` contains read-only views over those public objects.

The former `architecture/layout_policy.py`, `quantile_layout.py`,
`legacy_adapter.py`, compatibility hierarchy/spec modules, horizontal
`layout_policies/`, and separate QEC-configuration wrapper were deleted during
the canonical architecture cutover. `architecture/isa.py` now owns the typed movement and deferred-recipe
records plus the shared operation-claim base used by plan/runtime internals;
`architecture/state.py` owns mutable runtime state. Both remain internal
execution support, not authoring modules or extra architecture levels. A
future physical reorganization should move them together behind one
execution-owned boundary instead of splitting this coupled contract merely to
change import paths.

## Workload-specific specification

The common experiment input is the typed
`architecture.gallery.QuantileSizingConfig`; each gallery architecture turns
that input into its own sizing policy. Compute, Store/Load, and magic-state
quantiles can be overridden independently or swept through the public API:

```python
from heteqsys.specification import (
    build_architecture_specification,
    sweep_architecture_specifications,
)

specification = build_architecture_specification(circuit, "2.3")
points = sweep_architecture_specifications(
    circuit,
    "2.3",
    {
        "quantiles.compute": (0.25, 0.50, 0.75),
        "quantiles.store_load.clifford_t": (0.80, 0.95),
    },
)
```

The compiler no longer invents Store/Load locations.  The specification
materializes data, magic-state, and Store/Load endpoints once; movement and
routing backends consume those exact locations.  Canonical compute layouts use
one shared orientation: Store/Load endpoints on the left and magic-state input
slots on the right.

Users may optionally override placement on a Node-local logical canvas. The
request uses `logical_origin` for a whole Submodule and either a grid or exact
Submodule-local slot coordinates for spatial owners:

```python
from heteqsys import EvaluationConfig
from heteqsys.architecture import (
    LogicalLayoutGrid,
    LogicalLayoutRequest,
    SubmoduleKey,
    SubmoduleLayoutRequest,
)

layout = LogicalLayoutRequest(
    submodules={
        SubmoduleKey("na_node", "na_compute", "compute_region"):
            SubmoduleLayoutRequest(
                logical_origin=(8, 0),
                grid=LogicalLayoutGrid(rows=2, columns=4),
            ),
        SubmoduleKey("na_node", "na_msf", "magic_state_output_buffer"):
            SubmoduleLayoutRequest(logical_origin=(20, 0)),
    }
)
config = EvaluationConfig(profile_id="1.1", logical_layout=layout)
```

The field is omitted entirely when no override is supplied, so established
default request documents and hashes remain unchanged. Slot identity and slot
geometry are deliberately separate: BB/QLDPC memory exposes bindable logical
slots but no per-mode operational coordinates. Its `logical_origin` is only a
macro-level Submodule anchor. A visualization may derive a grid of BB blocks
from the physical-footprint estimate, but those display cells are not runtime
slot coordinates. Physical code placement, block/mode packing, dynamic ancilla,
and LightStim patch offsets belong to a later code-aware lowering stage.
Public logical-coordinate components are signed integers bounded to
`[-1_000_000_000, 1_000_000_000]`; a backend may fail earlier when its resolved
canvas would be impractically large.
`grid(rows, columns)` reserves the full rectangular logical envelope, including
unused cells; it does not manufacture extra slots. Derived Store/Load and magic
buffers are placed beyond that envelope, and same-Node placement collisions are
rejected rather than repaired by silently moving user-authored geometry.
The current SC compatibility backend derives a dense logical routing canvas
and rejects an override whose bounding rectangle exceeds 100,000 logical
sites; a future sparse/LightStim lowerer may replace that implementation limit.

## Resource-protocol binding

Magic-state and logical-Bell production protocols are selected from the QEC
catalog and resolved once into the `ArchitectureSpecification`. Protocol
identity owns intrinsic outputs-per-batch, cycle cost, per-copy space,
raw-resource consumption, and output error. Copy counts, buffer capacities, and
supported hardware operating points remain architecture/layout choices. An
ID-only protocol override hydrates catalog-owned fields, while a conflicting
intrinsic override is rejected.

Resource-arrival distributions in `OperationLatencyProfile` are optional. When
omitted, runtime timing is derived from the selected protocol and hardware
operating point. An explicit distribution is recorded as a timing sensitivity
override; it does not replace protocol identity, footprint, batch size, or
output fidelity. The resolved receipt retains base/effective arrivals and their
source, and the execution plan and canonical fidelity model consume the same
hashed resource-protocol binding.

## One-shot public API

```python
from pathlib import Path

from heteqsys import EvaluationConfig, run_evaluation
from heteqsys.api import CANONICAL_FIDELITY_PRESET
from heteqsys.program import load_ft_workload

circuit = load_ft_workload(
    Path("tests/fixtures/small_original.qasm"),
    "clifford_t",
)
report = run_evaluation(
    circuit,
    EvaluationConfig(
        profile_id="2.3",
        layout_policy_overrides={
            "protocols.magic_state.buffer_capacity": 2,
        },
        fidelity_profile=CANONICAL_FIDELITY_PRESET,
    ),
)
print(report.to_json())
```

Fidelity is disabled unless a profile or the named canonical reference preset
is selected explicitly. `request.config` is the exact standalone request
accepted by `EvaluationConfig.from_dict`; `resolved_inputs` contains the
canonical architecture, effective latency profile, footprint model, and
optional fidelity profile. The effective compiler is embedded in
`artifacts.logical_compilation`, while runtime policy/components and protocol
bindings are bound by `artifacts.execution_plan`. `trace_level="full"` is the
default; `trace_level="summary"` preserves the replayable causal transition
ledger but stores an empty diagnostic observation log, so exact wait-time and
buffer-occupancy attribution is unavailable.

`EvaluationReport.from_dict()` and `from_json()` strictly load Report v2
without rerunning compiler or runtime. The loaded object exposes
`execution_trace` and `discrete_time_log` directly and leaves `evaluation` as
`None`. The lazy `effective_configuration` compatibility receipt is available
only on a native run; accessing it on a loaded v2 report raises `TypeError`.
Use `render_evaluation_report_v1(report)` from `heteqsys.report_v1` only for a
static legacy consumer during the compatibility window.
