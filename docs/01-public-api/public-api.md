# Public Python API

Level 1 · Role: normative public API · Status: current and frozen for ArqSim 0.2.0

Report-v2, config-v1, and the
Python-facing Level-1 facade below are the accepted public contract.

ArqSim exposes a small common facade and keeps advanced contracts in their
owning subpackages. The common flow is:

```text
external FT program
  -> optional synthesis (for example NWQEC), or identity normalization
  -> canonical FTCircuit
  -> logical compiler and plan lowering
  -> shared Program/Resource runtime
  -> EvaluationReport
```

Here, **identity normalization** means that an input which is already at the
fault-tolerant logical-operation level is parsed, validated, and rewritten into
canonical `FTCircuit` records without synthesis, optimization, or
architecture-aware mapping.

The supported Level-2 call boundary beneath `run_evaluation()` is defined in
the [Offline Pipeline API](../02-offline-pipeline/offline-pipeline-api.md).
Concrete `FTCircuit`,
`LogicalCompilationResult`, and `ExecutionPlan` examples are in the
[Compiler IR Walkthrough](../02-offline-pipeline/compiler-ir-walkthrough.md).
Generated Program and
Resource instruction fields are cataloged separately in the
[ExecutionPlan Instruction Reference](../02-offline-pipeline/execution-plan-instruction-reference.md).

`from arqsim import *` exposes exactly:

```python
EvaluationConfig
EvaluationReport
FTCircuit
run_evaluation
```

`arqsim.__version__` remains a normal attribute. Helpers, failure types,
loaders, authoring records, and extension interfaces come from their owner
modules rather than widening the root facade.

## Common workflow

```python
from arqsim import EvaluationConfig, EvaluationReport, run_evaluation
from arqsim.program import load_ft_workload

circuit = load_ft_workload("program.qasm", representation="gate")
report = run_evaluation(
    circuit,
    EvaluationConfig(
        profile_id="2.3",
        run_label="experiment-01",
    ),
)

print(report.summary.total_latency_s)
print(report.summary.total_physical_qubits)
print(report.summary.success_probability)
assert report.summary.fidelity_complete_coverage
assert report.summary.all_invariants_satisfied

loaded = EvaluationReport.from_json(report.to_json())
assert loaded == report
assert loaded.summary == report.summary
```

If the source still needs FT synthesis, use `arqsim.synthesizer` first. NWQEC
is an optional backend; an already synthesized artifact takes the
identity-normalization path directly to `FTCircuit`.

## `FTCircuit`: compiler-facing Program IR

`FTCircuit` is the immutable, architecture-independent logical IR after
synthesis or identity normalization and before architecture-aware allocation,
mapping, routing, and runtime lowering. It does not contain a selected
architecture, physical placement, runtime resources, or outcomes.

Normal callers should use:

```python
from arqsim.program import load_ft_workload

circuit = load_ft_workload("program.qasm", representation="gate")
print(circuit.statistics)
print(circuit.semantic_hash)
```

The built-in loader recognizes `gate`, the legacy `clifford_t` spelling, and
`pbc`. Gate QASM is parsed through Qiskit and conservative normalized layers
are derived from the parsed circuit DAG, not source-file line groupings. The
current `FTCircuit` schema does not retain the complete dependency-edge graph.
PBC input is normalized into conservative sequential layers until a stronger
dependency contract is available.

`representation` selects a parser and identifies an IR dialect. It is not a
gate-set proof: the string `clifford_t`, for example, does not prove that every
operation is Clifford+T. The selected compiler validates the operations it
actually receives. Compatibility validation is therefore currently bounded by
the selected backend's implemented capabilities, rather than by the
representation label alone. `gate` and legacy `clifford_t` select the QASM
gate parser and produce gate/measurement operations; `pbc` selects the Pauli
rotation/measurement parser and produces Pauli-operation IR that the selected
architecture/compiler may accept or reject. The field is useful as a dialect
selector even though it should never be used as evidence that a circuit belongs
to a native gate set.

Direct construction with `FTCircuit`, `LogicalLayer`, `LogicalOperation`, or
`make_layers()` is an advanced authoring interface for adapters and tests, not
the ordinary ingestion path. Parsing failures use
`arqsim.program.WorkloadParseError`.

| Member | Meaning |
| --- | --- |
| `representation` | Parser/IR dialect identifier |
| `num_qubits`, `num_clbits` | Logical register sizes |
| `layers` | Immutable normalized logical layers |
| `provenance` | Non-semantic source/artifact receipt |
| `operation_count` | Number of normalized logical operations |
| `statistics` | Cached architecture-independent `CircuitStatistics` |
| `semantic_hash` | Semantic IR identity, excluding provenance |
| `to_dict()` / `to_json()` | Serialize the workload record |
| `from_dict()` / `from_json()` | Restore and validate the workload record |

## `EvaluationConfig`: requested inputs

`EvaluationConfig` is immutable and keyword-only. Its Python-facing fields are
grouped by how callers should use them:

| Tier | Fields | Intended use |
| --- | --- | --- |
| Common | `profile_id`, `run_label` | Select a gallery architecture and optionally label the run |
| Scientific advanced | `architecture_overrides`, `latency_profile`, `execution_policy`, `fidelity_profile`, `footprint_model` | Replace sizing/layout/protocol, timing, execution, fidelity, or footprint assumptions |
| Integration | `compiler_spec`, `logical_layout` | Configure the built-in compiler boundary or supply an authored logical-layout request |
| Reserved | `runtime_components` | Planned `RuntimeComponentManifest` replay receipt for the built-in one-shot runtime; not a constructor argument or injection seam |

Custom runtime components use a live `RuntimeComponentSet` through the
lower-level `arqsim.evaluation` interfaces; callers do not edit the reserved
planned-manifest receipt named `runtime_components` in Report-v2.

### Defaults

- `profile_id="1.1"` selects the default gallery architecture. A profile ID is
  an opaque stable catalog identifier, not a numeric type. Future profiles may
  use names such as `star-v1` or `star-v2`.
- `run_label=None` derives a label from workload provenance or the circuit
  hash. It does not change architecture, compilation, plan, or trace identity.
- `architecture_overrides={}` preserves the selected architecture policy.
- `execution_policy` defaults to full observation, seed zero, bulk-wave
  magic-state batching, and black-box injection lowering. Finite gadget
  execution is an explicit advanced opt-in and is never enabled by timing
  data.
- `compiler_spec=None` selects the canonical compiler for the resolved
  architecture.
- `fidelity_profile="canonical_reference_v1"` enables the reference
  end-to-end, physical-error-rate-`1e-3` model. It combines modeled logical
  operations, architecture operations, qubit idling, consumed resource-state
  output error, and buffered resource-state idling in additive log-success
  space. Use `fidelity_profile=None` to disable fidelity estimation explicitly.
- `footprint_model="protocol_aware_reference_v1"` converts resolved module
  capacity, QEC bindings, and pinned resource-production protocols into a
  physical-qubit estimate using the reference space rules. Supply a
  `PhysicalFootprintModel` for custom rules. This is not a boolean and does not
  accept `True`, `False`, or `None`.
- `logical_layout=None` selects the architecture's canonical logical canvas
  and is omitted from the serialized request.

There is no universal deterministic 1,000-item/s resource-arrival default.
The default `OperationLatencyProfile` leaves arrivals unresolved until the
architecture selects resource-protocol profiles. Bundled profiles currently
specify exponential arrivals at their own derived rates. Explicit arrival
distributions or a derived-kind override are scientific inputs; their resolved
values and sources appear in `report.resource_protocol_bindings` and
`report.latency_profile`.

### Python names and frozen wire names

New Python code uses `run_label` and `architecture_overrides`. The frozen
`arqsim.evaluation-config.v1` request and Report-v2 wire continue to use
`workflow_id` and `layout_policy_overrides`. The same-named config properties
remain read-only compatibility aliases. `to_dict()` / `to_json()` deliberately
emit the wire names, and `from_dict()` / `from_json()` accept them. This is a
Python vocabulary improvement, not a Report-v2 migration.

New Python code likewise uses `execution_policy`. The read-only
`evaluation_policy` property and the serialized `evaluation_policy` key remain
for compatibility. The two preset strings above are also frozen machine IDs;
descriptive Python symbols are available from `arqsim.api`:

```python
from arqsim.api import (
    REFERENCE_END_TO_END_FIDELITY_P1E3_PRESET,
    REFERENCE_PHYSICAL_QUBIT_FOOTPRINT_PRESET,
)
```

### `ExecutionPolicy`: the actual boundary

`ExecutionPolicy` is imported from `arqsim.evaluation`. It controls only
semantics between logical compilation and one runtime execution:

| Field | Choices / meaning |
| --- | --- |
| `magic_state_batching` | `bulk_wave` or `incremental`; compiler-to-lowering batching semantics |
| `injection_lowering_mode` | `black_box` or `finite_state_injection_v1`; whether an injection remains aggregate or becomes a runtime gadget |
| `observation_level` | `full` retains the discrete observation log; `summary` retains the canonical causal trace but omits heavier observations |
| `run_seed` | Seed for resource arrivals and sampled runtime outcomes |
| `max_transitions` | Positive event-engine safety bound |

It does **not** choose an architecture, sizing/layout policy, compiler backend,
logical-layout request, scheduler implementation, latency model, or fidelity
model. Those have separate owners. `EvaluationPolicy` remains an exact import
alias for one compatibility window. The frozen evaluation-config-v1 policy
wire still contains
`magic_state_consumption`, `runtime_injection_mode`, `trace_level`, `seed`, and
`max_events`; three additional historical fields are fixed receipts rather
than user-selectable behavior.

Example finite-gadget selection:

```python
from arqsim.evaluation import ExecutionPolicy, RuntimeInjectionMode

policy = ExecutionPolicy(
    injection_lowering_mode=RuntimeInjectionMode.FINITE_STATE_INJECTION_V1,
    observation_level="full",
    run_seed=7,
)
```

### Model completeness is fail-closed

The one-shot facade never treats an absent **required** timing/error parameter
as zero:

- latency coverage is checked on demand after the architecture and resource
  protocols are known. A required gate duration, QEC timing, resource arrival,
  finite-gadget reaction time, or routed-movement metric that is absent raises
  `EvaluationRunError` in the stage that first needs it. An explicitly modeled
  `0.0` remains legal and is distinct from a missing key;
- fidelity coverage is checked after execution because conditional gadget
  branches and actual idle residence are runtime facts. If any realized
  architecture operation, logical operation, qubit-idle location, consumed
  resource output, or resource-buffer idle location has no model,
  `run_evaluation()` raises `EvaluationRunError` with code
  `fidelity_coverage_incomplete`. `details["coverage_gaps"]` identifies every
  missing term.

This means an unmodeled RZ/STAR operation cannot silently inherit a zero
error. To inspect an incomplete experiment intentionally, either disable
fidelity in the one-shot facade with `fidelity_profile=None`, or use the
advanced `estimate_fidelity()`, `fidelity_coverage_gaps()`, and
`require_complete_fidelity()` APIs from `arqsim.evaluation`. Low-level partial
estimates and historical Report-v2 documents remain readable for diagnostics,
but their numeric partial product is not presented as a complete end-to-end
estimate.

### What the profile objects configure

`OperationLatencyProfile` and `FidelityProfile` are parallel scientific-input
families, but they are not one combined object:

| Profile | Owns |
| --- | --- |
| `OperationLatencyProfile` | modality gate service, QEC syndrome protocols, Store/Load protocol, neutral-atom movement, local/link transfer, resource arrivals, and optional classical-reaction latency |
| `FidelityProfile` | architecture-op and logical-op failure models, qubit-idle models by location, resource-state output errors, and resource-buffer idle models |

`OperationLatencyProfile()` is the bundled reference timing input. Resource
arrivals remain unresolved until the architecture binds its production
protocols. `reference_reaction_latency_profile_v1()` adds explicit reference
reaction times for experiments that enable finite gadget lowering. Custom
profiles are ordinary immutable typed objects; their complete resolved forms,
hashes, and provenance are recorded in the report.

The fidelity selection has two public modes: the named reference preset or an
explicit `FidelityProfile`; `None` means intentionally disabled. The footprint
selection likewise accepts the named reference preset or an explicit
`PhysicalFootprintModel`, but has no disabled state because physical-space
accounting is part of the normal evaluation result.

### Requested versus resolved

`report.config` is the exact request. Named presets, `None`, overrides, and
implicit canonical choices are resolved into separate report authorities:

| Requested | Effective report objects |
| --- | --- |
| profile, architecture overrides, logical layout | `specification`, `compiler_layout` |
| latency request | `latency_profile`, `resource_protocol_bindings` |
| compiler selection | `compiler_spec`, `logical_compilation` |
| execution policy | `execution_plan.policy`, `execution_trace` |
| fidelity selection | `fidelity_profile`, `fidelity` |
| footprint selection | `footprint_model`, `footprint` |

Use resolved objects and their hashes when comparing effective scientific
experiments. `config_hash` identifies the request. Config codecs reject unknown
fields, duplicate JSON keys, and non-finite values.

## `run_evaluation()`: one-shot orchestration

```python
run_evaluation(
    circuit: FTCircuit,
    config: EvaluationConfig | None = None,
    *,
    magic_sizing_circuit: FTCircuit | None = None,
) -> EvaluationReport
```

`circuit` is compiled and executed. `magic_sizing_circuit` is an advanced
experimental reference that may replace magic-state demand for architecture
sizing; it is never executed.

Wrong facade argument types raise `TypeError`. After a well-typed request
enters the pipeline, failures use the stable staged boundary:

```python
from arqsim.api import EvaluationRunError

try:
    report = run_evaluation(circuit, config)
except EvaluationRunError as exc:
    print(exc.stage, exc.code, exc.details)
    raise  # exc.__cause__ retains the diagnostic exception
```

| `stage` | Work covered |
| --- | --- |
| `architecture_resolution` | Sizing, layout, QEC, and Specification construction |
| `resource_protocol_resolution` | Protocol binding and effective arrivals |
| `compiler_setup` | Compiler and built-in runtime setup |
| `logical_compilation` | Logical compilation and plan lowering |
| `runtime_evaluation` | Shared Program/Resource runtime |
| `result_analysis` | Footprint, fidelity, and analysis |
| `report_rendering` | Report-v2 construction and validation |

Each `EvaluationRunError` also has a stage-specific `code`, immutable
`details`, `to_dict()`, and the original exception as `__cause__`.

## `EvaluationReport`: start with `summary`

`EvaluationReport` is factory-only. Direct construction is rejected. Obtain it
from `run_evaluation()`, `EvaluationReport.from_dict()`, or
`EvaluationReport.from_json()`.

The normal result surface begins with the typed immutable `report.summary`:

| Field/property | Meaning |
| --- | --- |
| `total_latency_s` | Realized end-to-end Program latency |
| `total_physical_qubits` | Resolved physical footprint total |
| `success_probability` | Complete one-shot estimate, or `None` when fidelity is disabled; a loaded historical partial report must be read together with coverage |
| `fidelity_complete_coverage` | Fidelity coverage status, or `None` when disabled |
| `completed_program_instructions` | Completed source Program instructions |
| `event_count` | Derived completed execution-event count |
| `invariant_checks` | Named terminal correctness checks |
| `all_invariants_satisfied` | Convenience check requiring every invariant |

`EvaluationSummary` is importable from `arqsim.api` for type annotations;
`summary.to_dict()` returns the exact Report-v2 summary record.

### Live and loaded reports

| Member | Live report | Loaded Report v2 |
| --- | --- | --- |
| `summary`, `execution_trace` | Available | Available and equal |
| `discrete_time_log` | Available according to trace policy | Available according to trace policy |
| `footprint`, `fidelity`, `analysis` | Available | Available and revalidated |
| resolved inputs/compiler artifacts | Available | Available and reconstructed/validated |
| `evaluation` | Live-only compatibility cache | `None` |
| Report-v1 projection | Supported only for compatible live runs | Unsupported |

New code must not depend on `report.evaluation`. Use `summary`,
`execution_trace`, `discrete_time_log`, `footprint`, `fidelity`, and `analysis`;
these keep the same meaning after serialization. `effective_configuration` is
only for the temporary Report-v1 adapter.

The broader typed inspection surface is:

| Group | Members |
| --- | --- |
| Request | `config`, `run_label`, `circuit`, `magic_sizing_circuit` |
| Resolved inputs | `specification`, `compiler_layout`, `resource_protocol_bindings`, `latency_profile`, `compiler_spec`, `fidelity_profile`, `footprint_model` |
| Compiler/runtime authorities | `logical_compilation`, `execution_plan`, `execution_trace` |
| Scientific results | `footprint`, `fidelity`, `analysis`, `discrete_time_log`, `summary` |

Detailed analysis fields may be unavailable under a summary trace;
`analysis.unavailable` records why.

### Serialization and identity

`to_dict()` returns an independent JSON-shaped Report-v2 document.
`to_json()` emits sorted finite JSON with a final newline. Strict loading
rejects malformed structure, duplicate keys, non-finite values, unknown
versions, hash mismatches, and derived-result drift. It re-lowers embedded
compilation and replays the trace, but does not rerun the compiler or runtime.

Report equality and Python hashing use `report_hash`, so live and loaded copies
of one document compare equally:

| Comparison | Identity |
| --- | --- |
| Complete report | `report_hash` |
| Requested configuration | `config.config_hash` |
| Lowered plan | `execution_plan.plan_hash` |
| Realized execution | `execution_trace.trace_hash` |

`run_label` is non-semantic for the plan and trace, but the frozen top-level
Report-v2 `workflow_id` includes it in `report_hash`.

## Compiler, layout, and runtime integration boundaries

The one-shot facade intentionally exposes selections, not implementation
objects:

- `compiler_spec=None` asks ArqSim to derive the canonical
  `LogicalCompilerSpec` for the resolved architecture. An explicit spec
  configures the built-in compiler boundary; it is not an arbitrary foreign
  compiler callback.
- `logical_layout=None` asks the selected architecture profile to construct
  its canonical logical canvas. An explicit `LogicalLayoutRequest` supplies
  authored placement/capacity intent before compilation; it is not a finished
  routed placement.
- `runtime_components` is a planned-manifest report/plan receipt and is
  deliberately not a constructor argument. Custom schedulers and realizers
  compose as live implementations through
  `RuntimeScheduler`, `RuntimeRealizer`, `RuntimeComponentManifest`, and
  `RuntimeComponentSet` in `arqsim.evaluation`.

The current replacement boundary is the whole `CompilerPipeline`, but its v1
result contract is source-layer-indexed and layer-preserving. Typed pass records
exist, but individual mapper/router passes are not yet promised as independently
stable plugins. A trusted Python adapter that conforms to those v1 semantics can
produce `LogicalCompilationResult` and call `lower_compilation_result()` through
the lower-level API. Receiving a complete `FTCircuit` does not make v1 a
cross-layer optimizer or a general scheduled-program contract.

External-team evaluation of general compilers requires a new versioned
compiler-plan exchange contract capable of representing an action DAG,
cross-layer placement continuity, compiler-inserted SE/checkpoint actions, and
bounded late binding. `LogicalCompilationResult v1` can remain an exchange
format for layer-preserving compilers, but it is not that general format.
Evaluator-owned validation, timing, fidelity, runtime, and reporting remain the
trust boundary. This portable submission API is roadmap work; the current
facade does not accept untrusted compiler output.

See the normative
[Offline Pipeline API](../02-offline-pipeline/offline-pipeline-api.md) for exact
signatures, artifact ownership, validation, and stability. The
[Compiler IR Walkthrough](../02-offline-pipeline/compiler-ir-walkthrough.md)
supplies a runnable toy
circuit, the actual Qiskit-derived logical layers, and abbreviated records.

## Where the timeline lives

`report.summary` contains only derived end-to-end metrics. The authoritative
timeline is `report.execution_trace`, whose ordered causal transitions retain
dispatch/completion time, ownership locus, claims, lineage, and gadget-parent
relationships. `report.discrete_time_log` is the optional heavier observation
log controlled by `ExecutionPolicy.observation_level`; it is not the timeline
authority. Both live and loaded Report-v2 objects expose the same
`execution_trace`.

## Owning subpackages

- `arqsim.program` owns immutable Program IR/statistics, loaders, and advanced
  authoring records.
- `arqsim.architecture` owns Profile, sizing/layout, Specification, and finite
  recipe contracts.
- `arqsim.qec` owns resource-protocol profile records and catalogs.
- `arqsim.operation_profiles` owns latency, movement, fidelity, and resolved
  resource bindings.
- `arqsim.compiler` owns pipeline configuration, `CompilerPipeline`, and
  `LogicalCompilationResult`; individual passes remain internal.
- `arqsim.evaluation` owns Plan/DAG/Trace, analysis and footprint result types,
  direct evaluation/replay, and four-role runtime composition.
- `arqsim.synthesizer` owns optional synthesis services and artifacts.
- `arqsim.report_v2` owns the direct typed codec for schema tooling; ordinary
  callers prefer `EvaluationReport.from_dict()` / `from_json()`.
- `arqsim.visualization` owns optional pure plotting functions.

Exact wildcard manifests are test-guarded. A new export needs an owner,
stability level, and real caller or extension use case.

## Compatibility and internal paths

Temporary pre-0.2 root aliases remain outside `__all__` for the documented
compatibility window. Report v1 is a one-way output adapter, not an input API.
Report-v2 loaders are v2-only, and trace validation accepts only Trace v4.

`architecture.isa`, `architecture.state`, concrete compiler passes and
registries, transient lowering inputs, built-in runtime implementations, and
paper-fit helpers are implementation modules without a pre-1.0 import promise.
