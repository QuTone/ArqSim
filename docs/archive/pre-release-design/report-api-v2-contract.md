# Report/API v2 Frozen Contract

> Archived pre-release design record. This document is non-authoritative.
> Start from the current [documentation index](../../README.md).

Historical status at archival time: **implemented and re-frozen before the
first public commit**.

This document freezes the smallest public report and Python facade that
preserve ArqSim's canonical stage boundaries. The implementation follows this contract;
a change to a decision below requires an explicit contract review and, after
release, an appropriate schema or API-version change.

The outer schema remains `arqsim.evaluation-report.v2`; its current embedded
authorities are `arqsim.execution-plan.v8` and
`arqsim.execution-trace.v4`. Because no earlier public commit or release
published Report v2 with the former nested shapes, this is a pre-release
re-freeze rather than an in-band Plan-v7/Trace-v3 migration. Older nested
documents fail closed.

## 1. Scope

The frozen boundary delivers:

1. detach native evaluation results from the report-v1 compatibility renderer;
2. make Report v2 self-contained across architecture resolution, logical
   compilation, plan lowering, and execution;
3. retain report v1 temporarily as an explicit, one-way adapter;
4. reduce the package-root facade to the four common entry-point types and
   functions; and
5. add strict v2 codecs, validation, fixtures, and packaging coverage.

This boundary does not move the internal ISA/state files, design a general
gadget DSL, or add external artifact references. Those concerns remain
separate from the Report/API contract.

## 2. Native Python contract

`run_evaluation()` keeps its current call signature:

```python
run_evaluation(
    circuit: FTCircuit,
    config: EvaluationConfig | None = None,
    *,
    magic_sizing_circuit: FTCircuit | None = None,
) -> EvaluationReport
```

The human-facing `EvaluationConfig` constructor is keyword-only and names its
run label and architecture-policy inputs `run_label` and
`architecture_overrides`. The frozen v1 config wire embedded in Report v2
continues to encode those values as `workflow_id` and
`layout_policy_overrides`; the wire schema is not renamed. The named
`canonical_reference_v1` fidelity preset and
`protocol_aware_reference_v1` footprint preset are explicit defaults.
`fidelity_profile=None` disables fidelity estimation; footprint selection is a
named preset or explicit `PhysicalFootprintModel`, never a boolean. The default
`execution_policy` retains full observation and
`injection_lowering_mode="black_box"`; the config wire keeps the historical
`evaluation_policy` and `runtime_injection_mode` spellings.

The returned `EvaluationReport` is the native typed Report-v2 object. Its
construction must not invoke `ReportV1Renderer` or create any v1 profile,
policy, Plan-v5, Trace-v1, or resolved-system receipt. In particular, a
supported neutral-atom run containing finite runtime recipes must be able to
return normally; the unsupported superconducting finite path fails during
lowering rather than producing a partial report.

`EvaluationReport` is factory-only: callers obtain one from `run_evaluation()`,
`EvaluationReport.from_dict()`, or `EvaluationReport.from_json()`, never its
implementation constructor. It retains direct typed access to the canonical
run artifacts and adds `logical_compilation: LogicalCompilationResult`. The
typed `summary: EvaluationSummary` is the first-line result surface and is the
Python view of the unchanged `results.summary` wire record. Its `to_dict()` and
`to_json()` methods emit Report v2. `from_dict()` and `from_json()` parse Report
v2 strictly. The class name remains `EvaluationReport`; there will not be a
permanent `EvaluationReportV2` name alongside it. Equality and hashing use the
complete document's `report_hash`, so a live report equals its validated loaded
round trip.

A report returned directly by `run_evaluation()` retains its complete typed
`EvaluationResult`. A report reconstructed by `EvaluationReport.from_dict()` or
`from_json()` does not fabricate that mutable-runtime wrapper: it exposes the
same `summary`, validated `execution_trace`, `discrete_time_log`, footprint,
fidelity, analysis, resolved-input, and compiler-artifact surfaces, while
leaving the live-only `evaluation` compatibility cache as `None`. Parsing never
reruns compiler or runtime and a loaded v2 document cannot be projected to v1
because it has no native run-artifact bundle.

The owning direct codec is `ReportV2Codec` in `arqsim.report_v2`; it returns a
typed `ReportV2Document`. `validate_report_v2_document()` and
`load_report_v2_document()` expose that boundary directly. The convenience
loaders in `arqsim.api` are also v2-only and return a validated immutable
mapping. Report v1 deliberately has no public input codec and cannot flow back
into native evaluation or Report v2.

## 3. Exact Report-v2 topology

The outer schema is `arqsim.evaluation-report.v2` and has exactly these seven
top-level fields:

```text
schema_version
workflow_id
request
resolved_inputs
artifacts
results
report_hash
```

The required nested shape is:

```text
request
├── config                   # exact EvaluationConfig v1 request
├── workload                 # exact FTCircuit record
└── magic_sizing_workload    # exact FTCircuit record or null

resolved_inputs
├── architecture             # ArchitectureSpecification v3
├── latency_profile          # effective OperationLatencyProfile
├── footprint_model          # effective PhysicalFootprintModel
└── fidelity_profile         # effective FidelityProfile or null

artifacts
├── logical_compilation      # LogicalCompilationResult v1
├── execution_plan           # ExecutionPlan v8
└── execution_trace          # ExecutionTrace v4

results
├── observations
│   └── discrete_time_log    # checked non-authoritative cache; [] if absent
├── footprint                # PhysicalFootprintEstimate
├── fidelity                 # FidelityEstimate or null
├── analysis                 # EvaluationAnalysis
└── summary
    ├── total_latency_s
    ├── total_physical_qubits
    ├── success_probability
    ├── fidelity_complete_coverage
    ├── completed_program_instructions
    ├── event_count
    └── invariant_checks
```

`workflow_id` is a non-semantic run label. It may change the outer report hash,
but it never changes architecture, compilation, plan, or trace identity.

The effective compiler is already embedded in `logical_compilation`. Runtime
policy, runtime-component manifest, resource-protocol bindings, and their
source hashes are already bound by `execution_plan`. Report v2 must not emit a
third copy of those records.

## 4. Authority and validation

The unique authorities are:

| Fact | Authority |
| --- | --- |
| Resolved static architecture | embedded `ArchitectureSpecification` and `architecture_hash` |
| Logical mapping/routing/batching | embedded `LogicalCompilationResult` and `compilation_hash` |
| Program/Resource IR, policy, recipes, inventory, runtime manifest | embedded `ExecutionPlan` and `plan_hash` |
| Realized timing, outcomes, causal order, and state transitions | embedded `ExecutionTrace` and `trace_hash` |
| Complete document identity | `report_hash`, excluding the `report_hash` field itself |

The `results` section contains checked derived records only. It must never feed
compilation, lowering, runtime dispatch, or state transitions. If a derived
record disagrees with a canonical artifact, validation rejects the report; it
does not choose one side as preferred.

`LogicalCompilationResult` is required rather than represented only by the
Plan's `compilation_hash`. This avoids a dangling provenance reference and lets
the validator reproduce the deterministic
`LogicalCompilationResult -> ExecutionPlan` lowering without rerunning the
compiler.

Report-v2 validation performs, in order:

1. strict JSON shape validation;
2. nested typed parsing and each nested hash check;
3. request/resolved-input/artifact cross-hash checks;
4. deterministic lowering of the embedded `LogicalCompilationResult` and exact
   comparison with the embedded Plan;
5. strict Trace replay against that Plan;
6. validation of the optional diagnostic observation cache;
7. recomputation and exact comparison of footprint, fidelity, analysis, and
   summary; and
8. the outer `report_hash` check.

Loading a stored Report v2 does not rerun the compiler, scheduler, outcome
model, or Event Engine. Full reproduction from the request is a separate audit
operation, not the meaning of `from_json()`.

## 5. Serialization rules

Report v2 is fully embedded in its first version. It does not accept paths,
URLs, content-addressed external objects, or partially loaded artifacts.

The codec:

- accepts only exact JSON objects, arrays, strings, booleans, null, and finite
  numeric values;
- requires a plain dictionary at the `from_dict()` boundary and never
  normalizes tuples, arbitrary mappings, numeric strings, or booleans into a
  requested numeric type;
- detects duplicate keys and non-finite constants while decoding
  `from_json()` before delegating to the same strict typed validation;
- rejects unknown fields, missing fields, numeric or container coercion, and
  older schema versions;
- serializes with sorted keys, `allow_nan=False`, and one final newline; and
- computes `report_hash` from canonical JSON content excluding only the outer
  `report_hash` field.

Report v2 deliberately does not serialize:

- a top-level duplicate `hashes` table;
- report-v1 Plan-v5 or Trace-v1 projections;
- `evaluation.events` (completed spans are derived from Trace-v4 completion
  transitions);
- a duplicate final state, terminal cache, runtime-component receipt, or
  execution hash outside the Plan/Trace authorities;
- report-v1 resolved-system, QEC, layout, compiler-layout, or physical-layout
  compatibility receipts; or
- a flattened `EvaluationResult.to_dict()` record labeled as an execution
  trace.

## 6. Report-v1 coexistence

Report v1 remains a frozen, static-only compatibility adapter with exactly one
direction:

```text
native typed run artifacts -> ReportV1Renderer -> report-v1 plain JSON
```

There is no v1-JSON-to-v2-JSON or v2-JSON-to-v1-JSON conversion layer. V1 data
never flows back into compilation or evaluation. The adapter continues to
fail fast when a Plan contains dynamic implementation recipes.

During coexistence:

- Python `run_evaluation()` and `EvaluationReport.to_json()` default to v2;
- `arqsim evaluate` defaults to v2 and temporarily accepts
  `--report-version v1`;
- the existing frontend `/evaluate` endpoint explicitly uses the v1 adapter,
  while a v2 endpoint is introduced;
- frontend v1 and v2 parsers both produce version-neutral ViewModels; React
  state, caches, comparisons, and fidelity/timeline components stop holding raw
  `EvaluationReportV1` objects;
- the profile catalog moves from `render_profile_v2()` to canonical Profile
  v3; and
- the byte-stable public v1 fixture and v1 adapter tests remain unchanged.

The v1 adapter may be removed only after all of the following are true:

1. frontend endpoints, parsers, state, cache, and components no longer refer to
   raw `EvaluationReportV1`;
2. frontend adapter tests cover at least one static v2 report and one dynamic-T
   v2 report;
3. examples, CLI default output, packaging smoke, and the public current-report
   fixture use v2;
4. effective configuration and the frontend profile catalog no longer call
   `render_profile_v2()` or `render_policy_v1()`;
5. repository search finds v1 only in the adapter, compatibility tests,
   historical documentation, and forensic fixtures; and
6. v1 has been explicitly deprecated for at least one tagged minor release.

Historical behavior-baseline `report.v1.json` files may remain permanently as
forensic artifacts after executable v1 support is removed.

## 7. Frozen package-root facade

The stable package-root wildcard surface is:

```python
__all__ = [
    "EvaluationConfig",
    "EvaluationReport",
    "FTCircuit",
    "run_evaluation",
]
```

`arqsim.__version__` remains available as an attribute but is not added to
`__all__`. Architecture, compiler, evaluation, operation-profile, workload
authoring, schema, preset, and report-loader types are imported from their
owning subpackages. In particular, low-level DAG/runtime types never move to
the root.

For one tagged minor release, removed root names may be served by lazy
deprecation aliases that preserve the original class/function identity and
emit `DeprecationWarning`. They are absent from `__all__` and are not wrappers.
The release audit retains them in the untagged 0.2.0 release candidate;
they are deleted only in the first minor release after this tagged deprecation
window has actually occurred.

The wider subpackage `__all__` audit is separate and must not expand the
Report/API-v2 implementation.

## 8. Acceptance gate

The implemented boundary remains accepted only while these gates stay green:

- native `EvaluationReport` construction no longer imports or invokes the v1
  renderer;
- direct `EvaluationReport(...)` construction is rejected, while live and
  loaded factories expose equal typed `summary` values and report identity;
- `EvaluationConfig` keeps human-facing Python names separate from the frozen
  wire keys and serializes its named default fidelity/footprint selections
  explicitly;
- `run_evaluation()` returns a dynamic-T Report v2 successfully;
- the report embeds a strict `LogicalCompilationResult`, and deterministic
  re-lowering exactly reproduces the embedded Plan;
- strict v2 serialize/parse/hash/replay round trips pass for static and
  dynamic-T fixtures;
- a neutral-atom compute unit containing several T invocations records one
  shared entangle and one shared measurement event, fans out typed reactions,
  and derives each logical T parent span without adding a scheduled parent
  event;
- the reference additive split uses existing compiler/latency authorities and
  introduces no user timing field; superconducting Profiles 1.2 and 2.2 reject
  explicit finite mode while their `black_box` path remains valid;
- fidelity charges the source logical T and consumed resource token once,
  treats shared CX/measurement as non-duplicating implementation evidence,
  counts logical-data idle during measurement/reaction, and charges a realized
  logical S separately;
- tampering, duplicate keys, non-finite values, missing/unknown fields, nested
  hash mismatches, derived-view mismatches, and unsupported versions all fail
  closed;
- the static v1 fixture remains byte-identical, and the explicit v1 adapter
  still rejects dynamic recipes;
- semantic baselines read typed artifacts rather than report-v1 projections;
- CLI and wheel/sdist smoke tests cover default v2 and explicit v1 output; and
- root `__all__` is exactly the four-name facade above, with advanced imports
  documented from their owning subpackages.
