# Report and Trace Schema

Level 1 · Role: normative serialized-output reference · Status: current for ArqSim 0.2.0

The stable native integration boundary is
`arqsim.evaluation-report.v2`. `run_evaluation()`,
`EvaluationReport.to_dict()`/`to_json()`, and `arqsim evaluate` all emit v2 by
default. The canonical embedded stage boundaries are
`LogicalCompilationResult` v1, `ExecutionPlan` v9, and `ExecutionTrace` v4.
Consumers must reject unknown schema versions rather than infer a shape.

This contract was re-frozen before ArqSim's first public commit. The outer
Report v2 name remains unchanged, while its not-yet-published nested Plan and
Trace authorities advance to v9 and v4. This is a pre-release contract reset,
not a migration that accepts or rewrites older Plan or Trace documents.

Report v1 remains only as an explicit, frozen, static-only compatibility
projection. Python callers opt into it with
`arqsim.report_v1.render_evaluation_report_v1(report)`; the CLI uses
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
typed stored-report view without rerunning the compiler, scheduler, logical
measurement provider, or Event Engine. A loaded report exposes its canonical
`execution_trace` and validated `discrete_time_log`; it leaves `evaluation` as
`None` rather than fabricating a partial live `EvaluationResult`.

`ReportV2Codec`, `validate_report_v2_document()`, and
`load_report_v2_document()` in `arqsim.report_v2` expose the direct typed v2
codec. The facade conveniences in `arqsim.api`,
`validate_evaluation_report_document()` and
`load_evaluation_report_document()`, are also v2-only and return a validated
immutable mapping. Report v1 intentionally has no public input codec or
validator: it can only be rendered from a native run's typed artifacts, never
loaded or converted into v2.

V2 validation rejects duplicate keys, non-finite values, missing or unknown
fields, non-canonical nested records, hash/cross-authority mismatches, and
derived-result drift. It deterministically re-lowers the embedded logical
compilation and exact-compares the Plan, replays Trace v4, validates the
diagnostic log, recomputes footprint/fidelity/analysis/summary, and checks the
outer hash. Editing a request and recomputing only `report_hash` cannot pass.

The canonical direct execution-plan boundary is
`arqsim.execution-plan.v9`. It binds the canonical architecture hash and
independent compiler, latency, resource-protocol, policy, and
runtime-component receipts, and recursively freezes typed DAG contents before
checking the mandatory `plan_hash`. The v9 wire requires a full exact canonical
`arqsim.runtime-manifest.v4` with the four `runtime_realizer`, `scheduler`,
`execution_backend`, and `measurement_provider` roles, plus complete canonical buffer
records, including explicit slots and initial contents; parsing does not
restore omitted fields from constructor defaults. V9 retains typed many-to-one
recipe membership plus shared entangle and measurement phase receipts. It can
therefore represent one physical batch service shared by several logical T
invocations without paying that service per invocation. Older versions fail
closed rather than acquiring this meaning. Plans produced by the canonical
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

A direct v9 plan can be serialized, strictly parsed with
`ExecutionPlan.from_json()`, and then passed explicitly to `evaluate()` with a
live runtime component set matching the plan's planned manifest. Parsing never
starts execution, and Python runtime callback implementations are not embedded
in JSON. A plan containing
a deferred recipe rejects the direct/no-op manifest; canonical plan building
selects the callback-capable built-in manifest when the caller leaves the
manifest unspecified. The manifest describes declared component algorithm
identity/configuration; its hash does not attest Python source code.
ArqSim's official state-bound realizer carries separate immutable Program
and Resource callback contexts, which evaluation checks against the plan's
circuit, architecture, compiler-spec, and latency-profile authorities before
creating runtime state.
The built-in magic-route callback obtains concrete slots from all typed
magic-state buffer claims; it does not require a particular buffer ID.

Plan v9 serializes timing-free `arqsim.injection-recipe.v2` records and the explicit
`ExecutionPolicy.injection_lowering_mode`. `black_box` is the default semantic
mode. `finite_state_injection_v1` is an explicit opt-in and requires a
modality-specific reaction-latency key, including when the desired value is
zero. Timing-map presence does not select semantics. Recipe invocation IDs are
globally unique within a plan; stage resources, exact dyadic angles, finite
continuations, and terminal Clifford corrections are strict typed fields.
Recipes never own attempt, reaction, or correction durations. Plan lowering
resolves the existing `OperationLatencyProfile` and compiler route authorities
into generated Program instructions and `ProgramWorkTemplate.duration_s`
continuation receipts. Runtime activates those receipts; Trace v4 records the
realized timing. They are not user-authored settings or a new gadget timing
profile.

For neutral-atom compute, a recipe-bearing `CompiledComputeUnit` lowers to one
shared `entangle` source phase and one shared `measurement` work item. Both
carry typed `recipe_members`, whose entries contain
`recipe_invocation_id` and `stage_index`. Shared entangle pays the state-bound
joint route plus logical CX service once; shared measurement pays the magic-Z
measurement service once, emits one outcome bit per member, and fans out the
per-invocation reactions. Outcome one may then activate a materialized logical
S correction. Static successors remain blocked until every invocation under
the host closes.

The neutral-atom timing split is recorded as
`reference_additive_gadget_decomposition_v1`. Entangle receives compiler
routing plus the existing neutral-atom gate duration, measurement receives the
resolved syndrome-protocol service, and lowering checks that their sum equals
the compiled aggregate duration. This is a transparent reference assumption,
not an independently calibrated magic-measurement model, and it adds no user
timing configuration. Superconducting Profiles 1.2 and 2.2 expose only an
overlap aggregate such as `max(gate, syndrome)`, so explicit finite mode fails
closed for them; their default `black_box` mode remains supported.

Fidelity keeps one authority per effect. The source host's logical `t` payload
is the sole aggregate T-operation channel, consumed magic states are charged
through the token ledger, and shared CX/measurement children do not introduce
duplicate logical-operation channels. Measurement and reaction time contribute
normal logical-data idle exposure; a selected logical S is charged separately.
Any future primitive CX/measurement fidelity decomposition must replace the
aggregate T authority under an explicit versioned policy, not supplement it.

Report v1 predates typed dynamic implementation recipes. It may down-project a
recipe-free Plan v9 into its frozen historical Plan-v5-shaped receipt, but it
fails fast when implementation recipes are present; it never strips dynamic
lineage or presents the projection as a self-loadable canonical Plan. For the
supported static path it projects deferred-dispatch fields back onto historical
metadata markers, including the old
`route_metrics.deferred_until_dispatch` marker. That marker never appears in
canonical compiler route metrics; both old dispatch markers are rejected from
canonical instruction metadata at top level and inside `route_metrics`. The
compatibility projection is not parsed back into the compiler/evaluator and
must not be used as a substitute for the canonical direct plan-serialization
path. Native Report v2 embeds the typed Plan v9 without this compatibility
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

Direct execution uses strict `arqsim.execution-trace.v4`. Its exact
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

Trace v4 records typed `ProgramWorkLineage`, measurement receipts, and
completion-only `ProgramContinuationReceipt` records. The exact lineage wire
fields are `work_id`, `source_instruction_id`, `parent_event_id`,
`recipe_members`, and `step`; legacy top-level recipe, invocation, and stage
fields are not accepted. A member contains `recipe_invocation_id` and
`stage_index`, allowing one entangle or measurement event to belong to several
logical T invocations. The step is one of `source`, `entangle`, `injection`,
`measurement`, `reaction`, or `correction`. A receipt either activates exact
child work IDs or marks its static source complete. Entangle has no causal
parent; the initial measurement points to entangle; each reaction points to
its measurement; a selected later-stage injection points to reaction and its
measurement points to injection; and correction points to reaction.
Measurement outcomes appear only on measurement completion and are not
repeated on reaction. Replay
validates each stable work ID and typed membership against the Plan-v9 recipes
and Program-work templates, checks measurement-selected branches and
materialized logical Clifford corrections, and rejects open recipe invocations.
Completed Program counts are root-instruction completions, not the number of dynamic
reaction, injection, or correction events.

Logical T is a derived presentation parent, not another scheduled or serialized
event. A UI keys the host by `source_instruction_id`, keeps it on the Compute
operation locus, and derives its execution interval from dispatch through the
terminal reaction or correction. Ready-to-dispatch delay is a separate queue
wait and is not part of execution duration or module utilization. Shared
entangle or measurement work remains one accounting event even when several
recipe members refer to it; expanding a host reveals those implementation
children without duplicating their latency or engine claim.

`ExecutionTrace.events` and `EvaluationResult.events` derive completed spans
from initial-state counts plus matching ordered dispatch/completion
transitions, and order the result by event ID. They are neither v4 constructor
input nor hash authority. Native Report v2 does not serialize them; its
`results.summary.event_count` is recomputed from the derived view. The
report-v1 renderer is the only serializer that injects the list into its
`arqsim.execution-trace.v1` compatibility receipt. The direct
`validate_execution_trace_document()` parser accepts only the exact Trace-v4
record; it rejects report-v1 data, the explicitly non-wire
`EvaluationResult.diagnostic_dict()` projection, and duplicate `events` or
`final_state` projections. `EvaluationResult` is an in-memory Engine result and
has no wire schema; persist `ExecutionTrace` or `EvaluationReport` instead.

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

A shared source transition exposes the full typed batch of recipe members,
consumed tokens, and consumed slots, but it does not define a pairwise
invocation-to-token/slot allocation. A frontend may render those as shared
batch facts; it must not zip their array order into a per-T assignment.

Timeline rows represent architecture/runtime-level logical-operation loci, not
physical pulse execution. The frozen locus priority is: Classical Decoder,
explicit interconnect, inter-module transfer, exact engine-owned submodule,
single target module, then explicit fallback. Engine claims are contention
evidence; they do not override an explicit interconnect or transfer locus.
Resolved Architecture Specification modules and interconnects determine the
owner identities and hierarchy of displayed activity. The current timeline
shows active operation loci and their owner headers; idle modules have no
execution row. The complete architecture inventory, including memory ownership,
remains in the Specification and report. Exact active submodules nest under
their parent, while passive buffers and slots remain state tracks populated only
from Plan metadata and recorded snapshots. Default tooltips expose only a
compact semantic and timing summary; canonical Plan/Trace data remains the
detailed inspection surface.

## Compatibility policy

Report-v2 outer and listed nested fields are exact; adding report fields is a
structural change requiring a new schema version and migration path.
Nested canonical receipts version independently; Report v2 embeds their exact
current wire records and never silently reinterprets older nested versions.
Likewise, the outer `arqsim.evaluation-config.v1` request shape remains stable
while its embedded runtime manifest advances independently to v4. This is a
pre-public re-freeze: a config-v1 record containing runtime-manifest v3 fails
closed instead of being silently upgraded.
The repository keeps guarded static and dynamic v2 fixtures plus a byte-level
v1 compatibility fixture and behavioral baselines to detect accidental changes
in serialization and execution meaning. The v1 fixture is checked only by
byte/hash stability and comparison with fresh explicit renderer output; it is
not parsed as an input document. It follows the one-way
Plan-v5-shaped/runtime-manifest-v3 projection, while the canonical direct
boundary remains Plan v9.
The two Step-2 `report.v1.json` behavior snapshots are deliberately frozen
forensic artifacts and may contain historical nested plan/component schemas;
they are inspection baselines, not documents that the current strict direct
plan or trace parsers reinterpret. Current semantic behavior is checked by the
separate generated semantic baselines.
