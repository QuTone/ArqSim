# Report and Trace Schema

The stable native integration boundary is
`arqsim.evaluation-report.v2`. `run_evaluation()`,
`EvaluationReport.to_dict()`/`to_json()`, and `heteqsys evaluate` all emit v2 by
default. The canonical embedded stage boundaries are
`LogicalCompilationResult` v1, `ExecutionPlan` v6, and `ExecutionTrace` v3.
Consumers must reject unknown schema versions rather than infer a shape.

Report v1 remains only as an explicit, frozen, static-only compatibility
projection. Python callers opt into it with
`heteqsys.report_v1.render_evaluation_report_v1(report)`; the CLI uses
`--report-version v1`. It is not constructed during a native run, cannot
represent finite dynamic recipes, and never feeds data back into compilation
or evaluation.

## Report v2 structure

Report v2 has exactly seven top-level fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Exact value `arqsim.evaluation-report.v2` |
| `workflow_id` | Non-semantic run label; included in the outer report hash |
| `request` | Exact evaluation config, workload, and optional magic-sizing workload |
| `resolved_inputs` | Canonical architecture, effective latency profile, footprint model, and optional fidelity profile |
| `artifacts` | Logical compilation, execution plan, and execution trace authorities |
| `results` | Checked observation cache, footprint, fidelity, analysis, and summary |
| `report_hash` | Hash of the complete document excluding this field |

The exact nested topology is:

```text
request
├── config
├── workload
└── magic_sizing_workload

resolved_inputs
├── architecture
├── latency_profile
├── footprint_model
└── fidelity_profile

artifacts
├── logical_compilation
├── execution_plan
└── execution_trace

results
├── observations
│   └── discrete_time_log
├── footprint
├── fidelity
├── analysis
└── summary
    ├── total_latency_s
    ├── total_physical_qubits
    ├── success_probability
    ├── fidelity_complete_coverage
    ├── completed_program_instructions
    ├── event_count
    └── invariant_checks
```

The effective compiler lives once in `logical_compilation`. Runtime policy,
runtime-component manifest, resource-protocol bindings, and their source
hashes live under `execution_plan`. Report v2 deliberately has no duplicate
top-level hashes table, effective-configuration receipt, completed-event list,
final-state cache, or report-v1 layout/QEC projections.

`EvaluationReport.to_json()` serializes sorted finite JSON with one final
newline. `EvaluationReport.from_dict()` and `from_json()` accept Report v2
only. `from_dict()` requires a plain dictionary containing exact JSON wire
primitives; it does not coerce tuples, mapping proxies, numeric strings, or
booleans into another type. `from_json()` rejects duplicate object keys and
non-finite constants before applying the same validation. Both construct a
typed stored-report view without rerunning the compiler, scheduler, outcome
model, or Event Engine. A loaded report exposes its canonical
`execution_trace` and validated `discrete_time_log`; it leaves `evaluation` as
`None` rather than fabricating a partial live `EvaluationResult`.

`ReportV2Codec`, `validate_report_v2_document()`, and
`load_report_v2_document()` in `heteqsys.report_v2` expose the direct typed v2
codec. The facade conveniences in `heteqsys.api`,
`validate_evaluation_report_document()` and
`load_evaluation_report_document()`, are also v2-only and return a validated
immutable mapping. Report v1 intentionally has no public input codec or
validator: it can only be rendered from a native run's typed artifacts, never
loaded or converted into v2.

V2 validation rejects duplicate keys, non-finite values, missing or unknown
fields, non-canonical nested records, hash/cross-authority mismatches, and
derived-result drift. It deterministically re-lowers the embedded logical
compilation and exact-compares the Plan, replays Trace v3, validates the
diagnostic log, recomputes footprint/fidelity/analysis/summary, and checks the
outer hash. Editing a request and recomputing only `report_hash` cannot pass.

The canonical direct execution-plan boundary is
`arqsim.execution-plan.v6`. It binds the canonical architecture hash and
independent compiler, latency, resource-protocol, policy, and
runtime-component receipts, and recursively freezes typed DAG contents before
checking the mandatory `plan_hash`. The v6 wire requires a full exact canonical
`arqsim.runtime-manifest.v3` with the four `runtime_realizer`, `scheduler`,
`execution_backend`, and `outcome_model` roles, plus complete canonical buffer
records, including explicit slots and initial contents; parsing does not
restore omitted fields from constructor defaults. The enclosing plan version
changed from v5 to v6 because finite implementation recipes and dynamic
frontier semantics joined the strict contract; v5 fails closed rather than
acquiring a new meaning. Plans produced by the canonical
builder record the logical `compilation_hash` and `compiler_spec_hash` in
provenance; construction
revalidates installed buffer/engine claim capacities, initial-location wire
types, deferred-recipe/buffer token-kind coherence, and MOVE/state-claim
consistency. `ExecutionPlan.from_dict()` recursively accepts exact JSON
dict/list/scalar shapes only, including string object keys and finite numbers;
it does not normalize Python tuples or mapping objects before checking the
hash. Program MOVE slot maps are injective, use the logical-qubit entity kind,
exactly match both state-location claim sets, and nest each endpoint under its
corresponding state location. Program and Resource metadata reject legacy
top-level control keys plus `deferred_until_dispatch` or `dispatch_deferred`
inside `route_metrics`.

Program MOVE slot endpoints use `move_operands`. State-dependent Program and
Resource requests use tagged
`arqsim.deferred-dispatch-recipe.v1` records. It deliberately has no separate
hash for the removed QEC-configuration container. Older execution-plan schema
versions fail closed.

A direct v6 plan can be serialized, strictly parsed with
`ExecutionPlan.from_json()`, and then passed explicitly to `evaluate()` with a
matching runtime component set. Parsing never starts execution, and Python
runtime callback implementations are not embedded in JSON. A plan containing
a deferred recipe rejects the direct/no-op manifest; canonical plan building
selects the callback-capable built-in manifest when the caller leaves the
manifest unspecified. The manifest describes component algorithm identity.
ArqSim's official state-bound realizer carries separate immutable Program
and Resource callback contexts, which evaluation checks against the plan's
circuit, architecture, compiler-spec, and latency-profile authorities before
creating runtime state.
The built-in magic-route callback obtains concrete slots from all typed
magic-state buffer claims; it does not require a particular buffer ID.

Plan v6 serializes finite `InjectionRecipe` records and the explicit
`EvaluationPolicy.runtime_injection_mode`. `black_box` is the default semantic
mode. `finite_state_injection_v1` is an explicit opt-in and requires a
modality-specific reaction-latency key, including when the desired value is
zero. Timing-map presence does not select semantics. Recipe invocation IDs are
globally unique within a plan; stage resources, exact dyadic angles, attempt
durations, finite continuations, and terminal Clifford corrections are strict
typed fields. The stage-zero attempt duration must equal its host instruction
duration because the host event executes that first attempt.

Report v1 predates typed dynamic implementation recipes. It may down-project a
recipe-free Plan v6 into its frozen historical Plan-v5-shaped receipt, but it
fails fast when implementation recipes are present; it never strips dynamic
lineage or presents the projection as a self-loadable canonical Plan. For the
supported static path it projects deferred-dispatch fields back onto historical
metadata markers, including the old
`route_metrics.deferred_until_dispatch` marker. That marker never appears in
canonical compiler route metrics; both old dispatch markers are rejected from
canonical instruction metadata at top level and inside `route_metrics`. The
compatibility projection is not parsed back into the compiler/evaluator and
must not be used as a substitute for the canonical direct plan-serialization
path. Native Report v2 embeds the typed Plan v6 without this compatibility
projection.

## Request and resolved inputs

`request.config` is the exact independently parseable
`arqsim.evaluation-config.v1` request. For example, absence of
`logical_layout` selects the canonical default and remains absent rather than
becoming an authored `null` choice. `resolved_inputs` records only the
canonical resolved authorities that are not already owned by a later artifact:
the Architecture Specification, effective latency profile, footprint model,
and optional fidelity profile.

The compiler is embedded in `artifacts.logical_compilation`; policy, seed,
runtime components, and resource-protocol bindings are bound by
`artifacts.execution_plan`. This avoids the duplicate
`effective_configuration` manifest retained by Report v1 while preserving the
requested-versus-resolved distinction.

## Execution trace

Direct execution uses strict `arqsim.execution-trace.v3`. Its exact
serialized/hash fields are `schema_version`, `plan_hash`, `seed`,
`total_latency_s`, `transitions`, `initial_state`, `terminal_state`,
`terminal_inflight`, and `trace_hash`. In particular, it has no serialized
`events` field:

- `transitions` are the sole append-order dispatch/completion authority;
- `initial_state` and `terminal_state` are immutable projections;
- `terminal_inflight` is the checked ledger-derived cache of unmatched
  Resource dispatches that legitimately extend beyond the final Program
  completion; and
- `plan_hash`, `seed`, `total_latency_s`, and `trace_hash` bind the trace to its
  inputs and causal horizon.

Each transition records contiguous state versions. A completion must match its
dispatch identity and reservation facts. Replaying the ledger must reproduce
the terminal state; unmatched Program dispatches are invalid. The Program
horizon is the last Program completion, so background Resource production may
remain in flight.

Trace v3 adds typed `ProgramWorkLineage`, measurement receipts, and
completion-only `ProgramContinuationReceipt` records. A receipt either
activates exact child work IDs or marks its static source complete. Source work
has no parent or recipe stage; continuation work names its frozen recipe,
stage, source instruction, and causal parent. Replay validates each stable work
ID against the Plan-v6 recipe, checks measurement-selected branches and
materialized logical Clifford corrections, and rejects open recipe invocations.
Completed Program counts are root-instruction completions, not the number of dynamic
reaction, injection, or correction events.

`ExecutionTrace.events` and `EvaluationResult.events` derive completed spans
from initial-state counts plus matching ordered dispatch/completion
transitions, and order the result by event ID. They are neither v3 constructor
input nor hash authority. Native Report v2 does not serialize them; its
`results.summary.event_count` is recomputed from the derived view. The
report-v1 renderer is the only serializer that injects the list into its
`arqsim.execution-trace.v1` compatibility receipt. The direct
`validate_execution_trace_document()` parser accepts only the exact Trace-v3
record; it rejects report-v1 data, flattened `EvaluationResult` diagnostics,
and duplicate `events` or `final_state` projections.

The full `discrete_time_log` is a separate non-authoritative report diagnostic
cache at `results.observations.discrete_time_log` for exact wait and occupancy
attribution. It is excluded from `trace_hash` and is accepted only when exact
validation against the referenced Plan and transition ledger succeeds;
summary traces store an empty list.

## Frontend contract

A frontend may render:

- `resolved_inputs.architecture` for hierarchy and resolved slots;
- `results.summary` and `results.analysis` for headline metrics and
  breakdowns;
- completion spans derived from
  `artifacts.execution_trace.transitions` for the timeline;
- nested artifact hashes and provenance for reproducibility.

It must not recompute mapping, routing, resource readiness, occupancy, timing,
or footprint semantics. Some report-v1 layout records retain derived routing
nodes/interfaces for compatibility with existing evaluations. Treat them as
read-only compiler receipts; they are not Profile fields and should not become
frontend-authored configuration.

## Compatibility policy

Report-v2 outer and listed nested fields are exact; adding report fields is a
structural change requiring a new schema version and migration path.
Nested canonical receipts version independently; Report v2 embeds their exact
current wire records and never silently reinterprets older nested versions.
The repository keeps guarded static and dynamic v2 fixtures plus a byte-level
v1 compatibility fixture and behavioral baselines to detect accidental changes
in serialization and execution meaning. The v1 fixture is checked only by
byte/hash stability and comparison with fresh explicit renderer output; it is
not parsed as an input document. It follows the one-way
Plan-v5-shaped/runtime-manifest-v3 projection, while the canonical direct
boundary remains Plan v6.
The two Step-2 `report.v1.json` behavior snapshots are deliberately frozen
forensic artifacts and may contain historical nested plan/component schemas;
they are inspection baselines, not documents that the current strict direct
plan or trace parsers reinterpret. Current semantic behavior is checked by the
separate generated semantic baselines.
