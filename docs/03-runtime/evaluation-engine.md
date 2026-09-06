# Evaluation Engine

Level 3 · Role: runtime kernel reference · Status: current and frozen for ArqSim 0.2.0

The frozen scope is the reviewed Level-3 boundary: Plan acceptance, runtime
components, state transactions, clock/frontier semantics, Trace production,
and the handoff from the low-level runtime result to the Level-1 report
facade. It does not promote roadmap items or private kernel helpers into public
APIs, and it does not create a wire schema for `EvaluationResult`.

## The two independent DAGs

`ProgramDAG` is finite. Its nodes are architecture-facing instructions lowered
from conservative FT circuit layers. Explicit predecessor ids preserve program
dependence.

`ResourceDAG` is streaming. Its nodes are recurrent process templates such as
`PREPARE_MAGIC_STATE` and `PREPARE_LOGICAL_BELL`. The evaluator instantiates
them lazily, so an unbounded producer never becomes an unbounded in-memory DAG.

Both use the execution ISA in `architecture/isa.py`. Sharing an opcode or the
flat `OperationClaims` base does not merge the two dependency planes: the plane
owns the work; the opcode defines its state guards and state transition.
`OperationClaims` owns the common buffer consumption/production, forwarding,
engine, target-Module, and target-link fields plus their immutable validation.
It has no codec or hash of its own; `ArchitectureInstruction` and
`ResourceProcess` retain their existing flat wire fields and their
plane-specific operands, locations, recurrence policy, and receipts.

`architecture/state.py` remains mutable runtime code. Both modules are
internal execution support despite their current package location; neither is
a field or authoring input of the canonical static architecture. If they are
physically reorganized later, the coherent move is to relocate ISA/claims/
recipes and state together behind one execution-owned boundary, not to move
one file in isolation.

## Compiler-to-plan boundary

The offline `CompilerPipeline` returns one immutable `LogicalCompilationResult`, not
a `ProgramDAG`, tuple of private blocks, or metadata convention. Its strict
`arqsim.logical-compilation-result.v1` codec records the source circuit,
architecture, and latency-binding hashes; the effective compiler spec; typed
compute units and routes; effective magic-state consumption policy; initial
mapping state; deferred-capacity status; and the result's semantic hash. Before
lowering, the plan builder checks all three source identities, requires the
compilation policy to match `ExecutionPolicy.magic_state_batching`, and
requires the compute units to cover each source operation exactly once.
It also rejects a compiled partition wider than canonical compute capacity or
a compiled unit whose magic demand exceeds canonical input-buffer capacity.
An `incremental` compilation is invalid if any compute batch needs more than
one magic operation. `ComputePartition.active_qubits` is the union of qubits
touched by that partition's source operations; it is not a residency record.
The keys of `CompiledComputeUnit.mapping` are the compute-residency authority,
must include every active qubit, and may also contain inactive filler qubits.
Every unit has exactly `min(circuit logical width, compute capacity)` mapping
keys; a merely underfilled mapping is invalid even when all active operations
would fit.

`lower_compilation_result()` is the direct advanced boundary for a decoded or
externally produced, trusted result that already conforms to the layer-preserving
v1 contract. `compile_and_lower()` is the composition boundary
that invokes a `CompilerPipeline` and returns both the compilation artifact and
the Plan; the former Plan-only wrapper was removed so callers cannot
accidentally discard compilation provenance.
The default compiler chooses fillers deterministically: active qubits first,
then qubits retained from the previous unit, then the remaining circuit qubits
in index order. All mapping values must be canonical compute data-slot IDs,
and all batches split from one partition use the same mapping.

For a non-deferred compilation, the initial mapping covers the whole circuit
and every unit preserves that complete initial placement. For a
capacity-deferred compilation, the initial mapping is empty and adjacent unit
mappings may change residency, but a qubit in their overlap must keep its slot;
otherwise the result has an unexplained placement discontinuity. Each unit's
fixed-width resident set has the required cardinality, the
complementary nonresident set must fit logical-memory capacity, and the circuit
must fit their combined capacity.
The combined-capacity gate is evaluated even when the compiler result has no
compute units. `deferred_capacity_mapping` must agree with whether circuit
width exceeds compute capacity.

Every deferred residency transition is a balanced exchange: in each chunk,
the outgoing and incoming sets have the same nonzero size and fit the
canonical Store/Load endpoint capacity. Lowering moves the outgoing wave to
the compute endpoint and, for remote memory, teleports it to the memory
endpoint. `STORE_QUBITS` performs location-preserving syndrome preparation;
one `LOAD_QUBITS` completion then atomically exchanges outgoing endpoint
locations for memory-region locations and incoming memory-region locations
for that endpoint. The LOAD operation's `qubits` remain only the incoming
operational payload; its wider location claims carry the atomic exchange, so
fidelity and exposure accounting do not count the outgoing wave twice. Remote
lowering teleports the incoming wave back to the compute endpoint, after which
local and remote paths both use typed MOVE into canonical compute slots. This
avoids transiently overfilling memory or either endpoint; remote Bell-pair
claims occur in one direction at a time. The
location-completion reorder does not alter timing: each exchange round still
contains exactly one STORE syndrome service and one LOAD syndrome service,
plus one outbound and one inbound TELEPORT for remote memory. All remain on
one predecessor chain, so the service multiset and Program critical-path
duration are unchanged.

`dependency_aware_overlap` is the only supported Store/Load lowering policy.
Other historical policy labels fail closed; the plan never records a policy
choice that did not affect its lowering semantics.

Both empty and non-empty plans retain `compilation_hash` in provenance, so the
plan hash includes the exact compiler-output lineage.

Each `CompiledRouteResult` owns a plain boolean `dispatch_deferred`. `false`
means the offline result owns the route steps; `true` means supported routing
must be completed after state binding and the compiled route contains no eager
steps. Route metrics cannot contain `deferred_until_dispatch` or
`dispatch_deferred`, so an extensible metrics dictionary cannot silently steer
control flow. Circuit-coverage validation also rejects a dispatch-deferred
non-magic batch. The boolean is not projected into
`ArchitectureInstruction.metadata`; the compiler result remains its only
authority. Strict compilation JSON requires every canonical route-step field;
it does not fill omitted route geometry or metadata with parser defaults.

Plan lowering distinguishes known geometry from facts that depend on the
eventual state transaction:

- `MoveOperands` stores concrete per-qubit source and destination slot IDs for
  an eagerly compiled Program `MOVE_QUBITS`;
- `MagicRouteDispatchRecipe(kind="joint_magic_route")` stores the source
  operation slice and logical-to-slot mapping needed once concrete magic slots
  are consumed; Program lowering creates it only when
  `CompiledRouteResult.dispatch_deferred` is true, while an eager magic route
  receives no recipe and is not recompiled at dispatch; and
- `ResourceMoveDispatchRecipe(kind="state_bound_resource_move")` asks the
  runtime realizer's Resource callback to compile endpoints from the selected
  Resource batch.

The latter two serialize under
`arqsim.deferred-dispatch-recipe.v1`. At dispatch, a typed
`DeferredDispatchRequest` pairs the recipe with consumed token IDs, consumed
slots, and produced slots obtained only from the tentative binding. Recipe and
MOVE facts cannot also appear in generic operation metadata. Program MOVE
bindings are injective, use `entity_kind="logical_qubit"`, and name exactly the
same qubit entities as the instruction's required and completion locations;
no extra location claims are allowed. Their concrete slots must be nested
under those corresponding `ArchitectureState` locations. Canonical Program
and Resource metadata reject their legacy top-level control keys, plus
`deferred_until_dispatch` and `dispatch_deferred` inside `route_metrics`. Plan
validation also requires a magic-route recipe to consume only `magic_state`
buffers and a Resource-move recipe's `entity_kind`
to match both forwarded buffers' `token_kind`. The built-in runtime compiler
collects consumed magic slots from the operation's typed buffer claims in
deterministic buffer-ID order, so dispatch is not coupled to a special
magic-buffer ID.

Plan construction is staged: topology and cost values are resolved once,
architectural inventories and the Resource DAG are lowered independently, and
`_program_lowering.py` converts the `LogicalCompilationResult` into the Program DAG.
`PlanCostInputs`/`SyndromeCostInput` are transient immutable handoff records
with no codec or hash. The canonical architecture and latency profile remain
the facts' authorities; descriptive metadata is emitted only as a downstream
receipt where the operation or report-v1 boundary still needs it.

## Finite runtime injection recipes

`ExecutionPolicy.injection_lowering_mode` is the explicit semantic switch for
state injection. Its default, `black_box`, leaves T-family compute operations
monolithic and emits no synthetic unconditional reaction node. Selecting
`finite_state_injection_v1` enables typed runtime expansion. This choice is
independent of timing data: the selected compute modality must then have a key
in `OperationLatencyProfile.reaction_latency_by_modality_s`; an explicit zero
is valid, while a missing key fails closed. The named
`reference_reaction_latency_profile_v1` supplies named reference assumptions,
but does not activate recipe semantics by itself. The effective mode is serialized in
the Plan-v9 policy and plan-builder provenance.

The current automatic lowering slice supports T injection using the frozen
convention CX(data, magic), measurement of the magic operand, and a
materialized logical S correction on outcome one. Each host instruction carries zero or more finite
`arqsim.injection-recipe.v2` records. An invocation ID is globally unique in
its plan; each stage names a typed `ResourceRef` and exactly one failure
continuation. Recipes contain no timing data. T recipes must close in S. Exact dyadic
rotation recipes validate the angle-doubling chain and its terminal Clifford;
different angles must use distinguishable installed resource pools because a
symbolic angle alone cannot constrain FIFO token binding. The helper
`build_angle_doubling_recipe()` constructs this finite chain without exposing
a general gadget DSL.

Timing keeps one upstream authority. Plan lowering resolves operation and
reaction costs from `OperationLatencyProfile`, plus route/movement costs from
the compiler result. Conditional work is frozen as a generated
`ProgramWorkTemplate.duration_s` receipt inside the Plan. This makes the Plan
self-contained without turning a recipe or template into another timing
profile, and it adds no per-gadget settings to the user API. The Event Engine
activates a template only when its recipe branch is selected; Trace v4 records
the realized dispatch/completion timing.

The host instruction is the static Program-DAG node and root completion
authority. For neutral-atom T expansion it is scheduled as one shared
`entangle` phase: it atomically consumes the batch's ready magic states,
performs the state-bound joint route, claims the compute engine once, and
executes the logical CX service once for the whole `CompiledComputeUnit`.
Entangle completion activates exactly one shared `measurement` work item.
That item claims the compute engine once, performs the magic-Z measurement
service, requests one logical bit for every typed recipe member from the
selected `LogicalMeasurementProvider`, and fans out one `reaction`
continuation per invocation. The built-in provider uses deterministic seeded
Bernoulli(1/2) sampling; custom providers need not. Reaction completion then
terminates that invocation or activates the typed next-stage injection or
materialized logical Clifford correction. Static successors are released only
after every invocation under the host has terminated.

`ProgramWorkTemplate.recipe_members` and
`ProgramWorkLineage.recipe_members` record the many-to-one relation between
logical recipe invocations and a shared physical event. Each member contains
the invocation ID and stage index. Entangle source work has no causal parent;
the initial measurement names the entangle event as its parent; each reaction
names its measurement; a selected later-stage injection names that reaction
and its measurement names the injection; and correction names its reaction.
Measurement outcomes live only on measurement completion and are not
duplicated on the reaction. Dynamic work is ordered by stable
`ProgramWorkLineage.work_id`;
outcome seeds also derive from stable work identity, so unrelated event
insertion cannot perturb a branch.

A logical T remains the recipe-level parent intent. It is not materialized as
another scheduled event: its span is derived from the earliest entangle event
containing that invocation through the invocation's terminal reaction or
correction. The same entangle or measurement event may therefore be a child of
several logical T parents while contributing its duration and engine claim to
the system only once.

The neutral-atom timing split is explicitly recorded as
`reference_additive_gadget_decomposition_v1`. Shared entangle receives the
compiler-routing duration plus the existing neutral-atom gate duration;
shared measurement receives the resolved syndrome-protocol service. Lowering
checks that these phases sum exactly to the compiler's previous aggregate
duration. This is a transparent reference decomposition, not an independently
calibrated magic-measurement experiment. It introduces no user timing field or
gadget-specific timing profile.

The superconducting compiler currently resolves compute time through an
overlap aggregate such as `max(gate, groups * syndrome)`. That receipt does not
contain a causal serial CX-then-measurement schedule. Consequently,
`finite_state_injection_v1` fails closed for superconducting-compute Profiles
1.2 and 2.2 rather than inventing zero-time or double-counted phases. Their
default `black_box` path remains supported. A future superconducting expansion
requires an explicit causal phase authority; users are not asked to configure
an ad hoc workaround.

## Runtime components and the fixed kernel

The Event Engine is a fixed orchestration kernel. It owns the clock, Program
and Resource frontiers, in-flight heap, Resource RNG, live
`ArchitectureState`, resource fit/batch compatibility behavior, transactional
commit, and trace recording. The v4 runtime manifest has four advanced
pure roles:

| Component | Current responsibility |
|---|---|
| `RuntimeRealizer` | from one detached operation and immutable snapshot, propose the tentative FIFO/first-free binding and apply the supported Program or Resource deferred callback; return one typed uncommitted candidate |
| `RuntimeScheduler` | return a type-strict exact permutation of the presented Program or Resource IDs |
| `ExecutionTimingBackend` | prepare final duration plus an observational backend artifact before commit |
| `LogicalMeasurementProvider` | provide the exact decoded logical bits requested by a measurement event; ordinary completions do not call this component |

The measurement seam is intentionally narrow. The Engine derives register
identity from measurement-step Program lineage plus the frozen
`InjectionRecipe`, then constructs a `LogicalMeasurementRequest`. A
`RuntimeRealizer` cannot invent or suppress measurement semantics. The
provider must return one
`LogicalMeasurementOutcome` for each requested register, in the same order,
inside a `LogicalMeasurementResult`; missing, extra, duplicate, or reordered
registers fail closed. All other completions bypass the provider and commit an
empty outcome payload. The built-in private implementation samples stable
Bernoulli(1/2) logical bits. In v1 these requests are created only for finite
runtime-gadget measurement children; source-circuit mid/final measurements do
not yet use this seam. A future decoder adapter can implement the same
protocol once that lowering exists. Decoder latency, asynchronous delivery,
and classical contention would require explicit scheduled work rather than
hidden provider time.

Terminology is strict at this boundary. The frozen Plan-v9 field
`ExecutionPlan.runtime_components` contains a **planned
`RuntimeComponentManifest` wire receipt**. The keyword accepted by
`evaluate()` contains a **live `RuntimeComponentSet` of Python
implementations**. The historical plural field name does not make these two
objects interchangeable. The planned/live manifest-hash equality check binds
the implementations' declared semantic identity and effective configuration;
it does not hash or verify their Python source code.

The Python protocol is named `ExecutionTimingBackend` because it receives an
already-realized candidate and owns only the final event-timing boundary.  The
serialized Runtime Manifest v4 role remains `execution_backend` (with the
built-in component id `backend.profile.v1`) for schema and fixture stability;
those wire names must not be interpreted as a second latency-profile lookup.

Components receive detached immutable request objects. They never receive the
live state, mutable frontier, heap, trace sink, or Resource RNG. The realizer
cannot rewrite source operation claims, and its `CandidateImplementation`
contains typed operation identity, binding, duration, and a strictly
observational compiler artifact. The backend returns duration plus a separate
artifact that is never merged into operation/Engine metadata. Compiler
artifacts explicitly reject fields that restate typed or Engine control
claims; backend artifacts remain isolated observations. The Engine revalidates
every result and calls the only state-commit path. This
replaces the former pass-through sequence of separate binding policy, Program
runtime compiler, Resource resolver, and generic metadata-merging records.
There are no compatibility shims for the removed policy/compiler/resolver
request types or component-set fields.

The immutable types needed to implement these protocols are exported from
`arqsim.evaluation`: `StateSnapshot`, `BufferSnapshot`, `EngineSnapshot`,
`TentativeBinding`, `StateReservation`, and their public blocked-transition
types. Mutable `ArchitectureState`, buffer/engine state, and commit deltas
remain kernel-owned implementation details.

The v1 Scheduler seam is deliberately narrow and frozen for the first release.
`order_program()` receives the IDs on the dependency-ready Program frontier;
those instructions may still be blocked by buffers, locations, destination
slots, or engines. `order_resources()` receives every recurrent Resource
process ID, not only the processes that are currently feasible. Each method
must return a type-strict exact permutation of the IDs it was given: a v1
Scheduler cannot filter, duplicate, add, intentionally defer, or schedule a
start time for work.

This ordering is still semantically meaningful. Within one plane, the Engine
visits work in the returned order, and every successful single-candidate
commit changes the state seen by later contenders. The order can therefore
decide which otherwise-ready operation obtains a contended resource first.
Feasibility checks, Program-first cross-plane priority, eager same-time
dispatch, Resource fitting/batching, the Program horizon, single-candidate
commit, the same-time fixed point, and clock advancement all remain
Event-Engine-owned. Cross-plane arbitration, intentional delay, and atomic
multi-candidate selection require a future versioned Scheduler contract.

The canonical v1 component ID remains
`scheduler.program_first_eager.v1` for frozen manifest and Plan compatibility,
even though Program-first and eager dispatch are kernel semantics rather than
Scheduler permissions. Code must not infer ownership from that spelling. A
future v2 contract can adopt an ID that describes within-plane ordering while
changing the manifest and serialized execution contracts explicitly.

Every executable `ExecutionPlan` uses the strict
`arqsim.execution-plan.v9` contract and consumes the canonical
`ArchitectureSpecification` directly. It contains a canonical
`arqsim.runtime-manifest.v4` recording the fixed kernel plus the four
component IDs, contract and implementation versions, effective configuration,
provider, and semantic hash. The same manifest hash flows through the result
and report. Plan v9 retains typed many-to-one recipe membership and the shared
entangle/measurement phase receipts needed to replay a batched gadget without
charging its physical service once per logical T. Recipe timing remains absent;
lowerer-generated templates remain the sole Plan-local timing receipts for
dynamic work. Older plans are rejected rather than reinterpreted.

Each nested descriptor uses `arqsim.runtime-component.v4`; older component,
manifest, and Plan versions fail closed with no automatic migration.

The canonical role IDs are `runtime_realizer.state_bound.v3`,
`scheduler.program_first_eager.v1`, `backend.profile.v1`, and
`measurement.seeded_bernoulli.v1`. A plan with no deferred-dispatch recipe may use
`runtime_realizer.direct_fifo_injection.v2`; its descriptor still binds the
finite continuation algorithm explicitly. The older Program/Resource callback IDs are
retained only as trusted callback provenance checked inside the built-in
realizer; they are not manifest roles.
There is no separate dependency on a removed QEC-configuration or resolved
system container. Built-in assembly accepts only the exact built-in manifest;
a custom trusted Python
component must supply an explicit descriptor and live component set. Descriptor
hashes attest the declared algorithm identity/configuration, not Python source
code, captured source objects, or sandbox isolation. ArqSim's official
state-bound realizer carries immutable Program and Resource callback contexts
supplied by lowering. Immediately before state creation, evaluation checks
both contexts against the plan's circuit, architecture, effective
compiler-spec, and latency-profile hashes. Those hashes remain plan
authorities and are not duplicated in the component manifest.

The v9 codec strictly parses and recursively freezes the two DAGs, typed MOVE
operands, tagged deferred recipes, continuation templates, architectural
inventory, provenance, policy, and runtime-component manifest before
validating the mandatory `plan_hash`. The wire must contain the full exact canonical
`arqsim.runtime-manifest.v4` document and complete `BufferSpec` records,
including explicit slot and initial-content
arrays; parsing does not synthesize those fields. `from_dict()` recursively
requires exact JSON dictionaries, lists, strings, booleans, finite numbers,
and null values: tuples, mapping proxies, non-string object keys, and
non-finite floats fail closed rather than being normalized. Text parsing also
rejects duplicate object keys, and the parsed unsigned document must equal the
canonical typed projection exactly. A plan also rejects
non-string initial-location entries; buffer or engine claims larger than
installed capacity (except explicitly discardable Resource production); and a
Program MOVE without logical-qubit typed operands or whose exact moved-entity
set disagrees with either its required or completion state locations. A
serialized plan is executable only through an explicit call to `evaluate()`.
When deferred recipes are present, the caller must also supply the trusted
live runtime component set whose manifest matches the planned manifest; Python callback code is
not serialized into the plan. A deferred plan rejects the built-in direct/no-op
manifest; the canonical builder selects the callback-capable built-in manifest
when a deferred recipe exists and no manifest is explicitly supplied. The
acceptance gate therefore performs the full
sequence `plan.to_json() -> ExecutionPlan.from_json() -> evaluate(restored,
runtime_components=...)`.

## Runtime state

`ArchitectureState` owns:

- buffer capacity, ready tokens, reserved incoming slots, and concrete slots;
- engine capacity and current users;
- logical-qubit and resource-token locations;
- a monotonic state version and active reservation registry.

Dispatch is a two-phase transaction:

```text
ArchitectureState.snapshot()
  -> immutable StateSnapshot(version)
  -> RuntimeRealizer reads the operation and StateSnapshot
  -> immutable TentativeBinding plus realized CandidateImplementation
  -> ExecutionTimingBackend.prepare(...)
  -> ArchitectureState.commit(binding, operation)
  -> StateReservation
```

The built-in realizer implements FIFO-token/first-free-slot binding and invokes
the appropriate trusted deferred Program/Resource callback when a typed recipe
is present.
`commit()` does not re-run that policy: it verifies the full operation contract,
location and capacity constraints, and state version, then applies the claims
atomically. A stale, malformed, or compiler-rejected candidate leaves state
unchanged. Both Program and Resource dispatch use this same path.

Completion has all-or-nothing semantics across architecture state, Program
control, and trace:

```text
measurement child only:
  LogicalMeasurementProvider.resolve(request) -> LogicalMeasurementResult
all other work:
  empty measurement payload
then:
  ArchitectureState.propose_completion(reservation, payload) -> StateDelta
  propose + validate every conditional continuation          -> Program plan
  construct + validate completion transition                 -> Trace record
  publish StateDelta + Program plan + Trace record            -> callback-free phase
```

The delta resolves produced/forwarded token identity, output slots, engine
release, location changes, and logical measurement payloads before any completion
mutation. Reservation ids are recorded across dispatch, the running frontier,
and completed events. This is a staged transaction, not one database-style
commit call: every extension callback and every fallible continuation/trace
validation runs before the short publish phase, and the publish phase invokes
no extension callback. A
rejected logical-measurement result, malformed continuation decision, missing
frozen child template, or invalid trace record leaves the reservation active,
the engine held, the Program frontier unchanged, and no completion transition
published. Shared measurement validates every recipe member before publishing
any reaction child.

## Discrete feedback over continuous time

At each completion timestamp the engine:

1. completes all simultaneous events and updates architectural state;
2. applies each typed continuation receipt, activating dynamic recipe work or
   marking a static source complete only after every child invocation closes;
3. marks successor instructions **program-ready** when all Program-DAG
   predecessors are complete;
4. evaluates whether each program-ready instruction is **resource-ready**
   under the current buffer, location, destination-slot, and engine state;
5. dispatches instructions that are both program-ready and resource-ready;
6. greedily starts resource processes while their inputs, outputs, and engines
   are resource-ready;
7. repeats to a same-time fixed point, then advances to the next completion.

All timestamp comparisons use an absolute tolerance only; a later event is not
completed early merely because the absolute clock value is large. Zero-duration
Program work is drained with Program-first priority, while a transition budget
terminates a zero-duration recurrent Resource cycle. Once the final Program
instruction completes, the Program horizon closes immediately: already-running
Resource reservations remain visible, but no new Resource work is dispatched.

Static Program readiness is monotonic. Dynamic recipe work is admitted only by
the typed completion receipt of its causal parent and is deterministically
ordered by stable work ID. Resource readiness is evaluated against the
current architectural state and may change when another dispatch consumes or
reserves a resource. A program instruction is **waiting** precisely when it is
program-ready but not resource-ready. A **running** event has already reserved
its resources and has a concrete completion timestamp.

Blocked work is not represented by synthetic `WAIT`, `REQUEST`, or `STOP`
nodes. A program-ready instruction remains undispatched until a relevant state
transition makes it resource-ready.

`arqsim.execution-trace.v4` has one serialized/hash authority:
`ExecutionTrace.transitions`, the append-only causal ledger retained at both
observation levels. It records each committed dispatch or completion immediately,
including reservation identity, concrete token/slot/engine claims, state
versions, typed Program lineage, measurements, continuation receipts,
outcomes, and the post-transition state projection. A zero-duration
dispatch and completion therefore retain their true interleaving instead of
being grouped by timestamp.

Completed `ExecutionTrace.events` are a Python compatibility property derived
from the initial-state counts and matching ordered dispatch/completion
transitions, then ordered by event ID. They are neither constructor input nor
part of the v4 trace wire/hash. `terminal_inflight` is the strictly checked
ledger-derived cache of unmatched Resource dispatches that cross the Program
horizon. The report-v1 renderer is the only boundary that serializes completed
event spans; its one-way output is hash-checked and byte-compared with the
frozen static fixture. Native Report v2 embeds Trace v4 without an event list.
The canonical trace parser accepts only the exact Trace-v4 record; it does not
parse report-v1 data or the explicitly non-wire
`EvaluationResult.diagnostic_dict()` projection. `EvaluationResult` has no
`to_dict()` or `to_json()` method and does not claim a schema of its own: save
`result.trace` for the runtime artifact, or save `EvaluationReport` for the
complete public result.

Replaying a trace means applying these recorded architectural transitions
against the referenced `ExecutionPlan` and checking the exact terminal state.
It does not rerun the compiler or Scheduler, simulate a quantum state, or claim
physical-gate replay. The optional full `discrete_time_log` remains an
`EvaluationResult` diagnostic cache for waiting and occupancy attribution;
Report v2 stores it under `results.observations`, while Report v1 retains its
historical location. It is not part of `trace_hash`, and loading a full report
validates it exactly against the Plan and ledger. `summary` and `full` both
retain the causal ledger; `summary` deliberately omits those diagnostic
observations.

## Runtime result and public-report handoff

`evaluate()` returns one in-memory `EvaluationResult`. It closes the runtime
kernel call, but it is not the public persisted result and has no wire schema.
The authority and cache boundary is:

```text
EvaluationResult
├─ ExecutionTrace.transitions     sole serialized/hash runtime authority
└─ discrete_time_log             optional validated diagnostic cache
```

The cache contains the heavier observations needed for exact waiting-time and
buffer-occupancy attribution, including observations that are not causal state
transitions. It never overrides the Trace. A full Report-v2 loader accepts the
cache only after validating it against the referenced Plan and transition
ledger; omission under `observation_level="summary"` does not weaken or replace
the causal Trace.

The Level-1 facade then performs a read-only result stage:

```text
ExecutionPlan -> evaluate() -> EvaluationResult

ArchitectureSpecification + PhysicalFootprintModel
  -> PhysicalFootprintEstimate

EvaluationResult + ExecutionPlan + ArchitectureSpecification
  + PhysicalFootprintEstimate + optional FidelityProfile
  -> EvaluationAnalysis + optional FidelityEstimate

request + resolved inputs + LogicalCompilationResult + ExecutionPlan
  + ExecutionTrace + observations + derived results
  -> EvaluationReport v2
```

Footprint is static with respect to a run: it is computed from the resolved
Architecture Specification and the selected physical-footprint model. It
describes installed capacity and QEC/protocol bindings, not peak runtime use,
and therefore does not consume `EvaluationResult` or infer capacity from the
timeline.

Fidelity is enabled by default in `EvaluationConfig` through the named
`canonical_reference_v1` selection. The facade resolves that selection after
architecture, latency, and resource-protocol binding, then derives fidelity
from the realized Trace and matching Plan. `fidelity_profile=None` is an
explicit opt-out, not the default. When a profile is selected,
`run_evaluation()` requires complete coverage of every realized operation and
idle exposure; incomplete coverage raises `EvaluationRunError` during
`result_analysis` instead of publishing a partial success probability. The
lower-level analysis functions may still expose an incomplete estimate for
diagnostic use, so that fail-closed rule belongs specifically to the public
facade.

`EvaluationAnalysis` always derives engine utilization and installed-space
breakdown. With `observation_level="full"`, it also derives exact exclusive
time attribution and buffer occupancy from the validated diagnostic log. With
`"summary"`, those two fields are `None` and `analysis.unavailable` explains
why; the causal Trace, footprint, and trace-derived fidelity remain available.

`EvaluationReport` belongs to the Level-1 public API. Level 3 promises only
the handoff described above: `EvaluationResult` closes one live runtime call,
while the facade packages its authoritative Trace, validated optional
observations, resolved inputs, and derived results into the report. The
normative persisted shape and loader guarantees are defined by the
[Report and Trace Schema](../01-public-api/report-and-trace-schema.md).

For same-node magic-state delivery, the eager dispatch policy batches every
currently ready factory output that fits in the destination buffer. Destination
slots are reserved at dispatch, and the state-bound batch is passed to the
modality-specific movement compiler before entering the running-event heap.

## Cost synthesis

Plan-time cost synthesis first resolves typed transient inputs: Store/Load
syndrome rounds and cycle time, per-item logical-link service, local
magic-delivery service, and modality-specific classical reaction latency.
Program and Resource lowering consume those values directly. They do not parse
their own previously emitted metadata to recover a cost. The current runtime removes the
runtime candidate/backend control-metadata chain. Typed candidate/binding/
duration fields and observational compiler/backend artifacts now remain
separate; descriptive instruction receipts are still allowed but cannot steer
binding, dispatch, state mutation, or trace authority.

Time is the final completion timestamp of the Program DAG. The read-only
Analyzer derives timeline, utilization, qubit exposure, and backpressure facts
from the immutable trace, plan, and specification; exact stall/occupancy
attribution additionally requires the full diagnostic log. In-flight Resource
engine time is clipped at the Program horizon instead of being silently
discarded. Physical space is computed from the instantiated architecture and
QEC bindings. T injection no longer creates a coarse unconditional
`CLASSICAL_REACTION` dependency between logical layers. In explicit finite
recipe mode, each realized attempt activates its own reaction work, which adds
only decoding, classical control, and feed-forward delay before the measured
branch is selected. `reference_reaction_latency_profile_v1` records 500 us for
neutral-atom compute and 10 us for superconducting compute as named reference
assumptions, not decoder-independent defaults and not an implicit request to
enable runtime expansion.

Fidelity is synthesized from the same realized trace. The Analyzer reconstructs
every logical-qubit second as active or idle: qubits named by a running Program
operation are active, while all others remain idle at their current
architectural location. Consequently, every logical qubit accrues idle
exposure while the Program plane is resource-blocked. The fidelity estimator:

1. counts every completed Program opcode plus only application-relevant
   Resource opcodes, and reports an omitted opcode model as uncovered rather
   than silently treating it as zero;
2. counts the logical operations in each completed `EXECUTE_COMPUTE` node and
   applies the selected operation-specific failure channels;
3. converts idle exposure at each location to QEC cycles using that location's
   cycle time;
4. reconstructs each resource token's identity from production through
   forwarding and terminal consumption using the causal trace plus its typed
   `ExecutionPlan` buffer inventory;
5. starts at terminal Program resource consumption and follows Resource-event
   token provenance backwards, charging output error, supporting Resource
   operation channels, and location-specific buffered idle exposure only in
   that application-relevant closure; and
6. composes exact survival probabilities in log space.

A completed token is idle only while resident and ready in a buffer.
Production and forwarding/delivery intervals are active protocol work and do
not also accrue buffered-idle error. Forwarding closes the source residence and
reopens the destination residence under the same token identity. The ledger
keeps all physical consumptions for diagnostics, but an unused output or a
dead-end delivery chain makes no contribution to application fidelity. If a
delivered magic state is consumed by Program work, its upstream Bell state and
supporting teleport channel enter the closure exactly once. Node-owned magic
buffers take QEC identity from their architecture Submodule; Interconnect-owned
Bell storage takes the stored-state QEC identity from the selected Bell
protocol. These calibrations live in `FidelityProfile`, while `BufferSpec`
supplies the existing typed Module/Submodule location. Consequently
resource-aware fidelity analysis requires the matching `ExecutionPlan`;
buffer-name parsing is not a fallback contract. A protocol-bound resource model
must exactly match the Plan's typed resource-protocol receipt, its independent
bindings hash, protocol/profile identity, and output-error value.

For BB-memory/surface-compute exchange, each realized multi-qubit
`STORE_QUBITS` or `LOAD_QUBITS` batch adds one event-level failure channel,
independent of its logical-qubit count.
The canonical value is $2.3\times10^{-6}$, the requested optimistic endpoint
of the 95% interval reported for an inter-module logical measurement in Table 3
of Wills et al. (arXiv:2605.21898v2).  Because that experiment joins two
`[[144,12,12]]` gross-code modules rather than the exact
`[[288,12,18]]`-BB/surface-code adapter, the profile labels this value an
optimistic inter-module proxy rather than a direct circuit simulation.
Treating the reported value as batch-level is an additional optimistic
assumption; the source does not characterize error scaling with batch width.

For T operations, the canonical audit profile keeps the selected factory's
literature-reported output error in the Program-reachable resource model and
keeps the current logical consumption proxy in the T-family logical-operation
model.
They remain independent survival channels, but their separate ledgers prevent
double charging and make unused factory outputs free of application-fidelity
cost. Thus changing the magic-state protocol affects space, production timing,
batch multiplicity, and fidelity through one QEC protocol profile.

In finite neutral-atom expansion, the source host's logical `t` gate payload is
the sole logical-operation fidelity authority for the T parent. The shared CX
and magic-Z measurement phases are implementation evidence and must not add a
second logical-operation channel; consumed magic states remain charged through
the resource-token ledger. Because shared measurement and reaction do not name
the logical data qubit as active, their elapsed time contributes normal
location-specific idle exposure. An outcome-one logical S correction is a
separate realized logical operation and is charged normally. If independently
calibrated primitive CX/measurement channels are introduced later, they must
replace the aggregate T channel under a versioned policy rather than supplement
it.

For logical teleportation, `PREPARE_LOGICAL_BELL` obtains endpoint space,
raw-pair consumption, throughput, and output error from one selectable
entanglement-distillation profile. Preparation itself carries no application
failure term. Every Bell pair actually consumed is charged once by token
identity for selected Bell-output infidelity, plus buffered residence for its
two encoded endpoint qubits when that storage QEC is calibrated.
`TELEPORT_QUBITS` independently carries the LightStim transversal-CNOT
operation channel. No additional cross-modality idling channel is folded into
this operation proxy; normal trace-level idle exposure of other program qubits
remains active.

Parameterized architecture-operation fidelity requires the matching typed
`ExecutionPlan`. Program multiplicity is the `qubits` payload of its source
instruction or activated continuation template. For a relevant Resource
`TELEPORT_QUBITS` event, multiplicity is the number of distinct token identities
actually forwarded along the Plan process's typed flows, including its realized
batch size. Consumed Bell ancillas are charged by the resource ledger and do
not supply the teleported-item count. Buffer names and diagnostic `amount` or
`qubits` receipts cannot change either count; missing or inconsistent typed
work/flow context fails closed. This changes no channel probabilities or the
existing rule that a relevant Resource event contributes its whole event cost.

Pauli-frame X/Z updates have zero direct failure cost.  SX is not Pauli and is
therefore retained as an explicit H-S-H compiler-decomposition surrogate.

The profile reports uncovered gates or idle locations instead of silently
assuming zero error. `canonical_fidelity_profile` binds LightStim circuit-level
data and literature-backed proxies to the current architecture; its provenance
explicitly marks every extrapolation, surrogate, and currently idealized
channel.

The trace therefore replaces the old five-estimator overhead waterfall as the
primary attribution object. Counterfactual baselines should change a clearly
specified state or dependency rule and then rerun the same evaluator.
