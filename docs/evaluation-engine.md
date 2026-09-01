# Evaluation Engine

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
`heteqsys.logical-compilation-result.v1` codec records the source circuit,
architecture, and latency-binding hashes; the effective compiler spec; typed
compute units and routes; effective magic-state consumption policy; initial
mapping state; deferred-capacity status; and the result's semantic hash. Before
lowering, the plan builder checks all three source identities, requires the
compilation policy to match `EvaluationPolicy.magic_state_consumption`, and
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
externally produced result. `compile_and_lower()` is the composition boundary
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

`EvaluationPolicy.runtime_injection_mode` is the explicit semantic switch for
state injection. Its default, `black_box`, leaves T-family compute operations
monolithic and emits no synthetic unconditional reaction node. Selecting
`finite_state_injection_v1` enables typed runtime expansion. This choice is
independent of timing data: the selected compute modality must then have a key
in `OperationLatencyProfile.reaction_latency_by_modality_s`; an explicit zero
is valid, while a missing key fails closed. The named
`reference_reaction_latency_profile_v1` supplies named reference assumptions,
but does not activate recipe semantics by itself. The effective mode is serialized in
the Plan-v6 policy and plan-builder provenance.

The current automatic lowering slice supports T injection using the frozen
convention CX(data, magic), measurement of the magic operand, and a
materialized logical S correction on outcome one. Each host instruction carries zero or more finite
`InjectionRecipe` records. An invocation ID is globally unique in its plan;
each stage names a typed `ResourceRef`, its own injection-attempt duration, and
exactly one failure continuation. T recipes must close in S. Exact dyadic
rotation recipes validate the angle-doubling chain and its terminal Clifford;
different angles must use distinguishable installed resource pools because a
symbolic angle alone cannot constrain FIFO token binding. The helper
`build_angle_doubling_recipe()` constructs this finite chain without exposing
a general gadget DSL.

The host instruction is the static Program-DAG node and therefore the root
completion authority. Its completion records the first seeded Bernoulli
measurement and activates one reaction work item per recipe. A later injection
attempt likewise records its measurement before activating its reaction;
reaction completion then either terminates that invocation or activates the
typed next-stage injection or materialized logical Clifford correction. Static
successors are released only after all invocations under the host have terminated.
Dynamic work is ordered by stable
`ProgramWorkLineage.work_id`; outcome seeds also derive from this stable work
identity, so unrelated event insertion cannot perturb a branch. Trace lineage
retains the parent event only as causal evidence, never as RNG identity.

## Runtime components and the fixed kernel

The Event Engine is a fixed orchestration kernel. It owns the clock, Program
and Resource frontiers, in-flight heap, Resource RNG, live
`ArchitectureState`, resource fit/batch compatibility behavior, transactional
commit, and trace recording. The v3 runtime manifest has four advanced pure
roles:

| Component | v2 responsibility |
|---|---|
| `RuntimeRealizer` | from one detached operation and immutable snapshot, propose the tentative FIFO/first-free binding and apply the supported Program or Resource deferred callback; return one typed uncommitted candidate |
| `RuntimeScheduler` | return a type-strict exact permutation of the presented Program or Resource IDs |
| `ProfileExecutionBackend` | prepare final duration plus an observational backend artifact before commit |
| `OutcomeModel` | derive a completion outcome from the event, backend artifact, and stable per-event seed |

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

The current Scheduler seam preserves the existing kernel rather than claiming
to be a general optimizer. Program-plane priority, eager same-time dispatch,
Resource fitting/batching, and the Program horizon remain Engine-owned.
Cross-plane arbitration, intentional delay, and atomic multi-candidate batches
are future contracts.

Every executable `ExecutionPlan` uses the strict
`arqsim.execution-plan.v6` contract and consumes the canonical
`ArchitectureSpecification` directly. It contains a canonical
`arqsim.runtime-manifest.v3` recording the fixed kernel plus the four
component IDs, contract and implementation versions, effective configuration,
provider, and semantic hash. The same manifest hash flows through the result
and report. The enclosing plan moved from v5 to v6 because
implementation recipes and dynamic-frontier semantics are now part of the
strict contract; older plans are rejected rather than reinterpreted.

Each nested descriptor uses `arqsim.runtime-component.v3`; older component,
manifest, and Plan versions fail closed with no automatic migration.

The canonical role IDs are `runtime_realizer.state_bound.v2`,
`scheduler.program_first_eager.v1`, `backend.profile.v1`, and
`outcome.seeded_bernoulli.v1`. A plan with no deferred-dispatch recipe may use
`runtime_realizer.direct_fifo_injection.v2`; its descriptor still binds the
finite continuation algorithm explicitly. The older Program/Resource callback IDs are
retained only as trusted callback provenance checked inside the built-in
realizer; they are not manifest roles.
There is no separate dependency on a removed QEC-configuration or resolved
system container. Built-in assembly accepts only the exact built-in manifest;
a custom trusted Python
component must supply an explicit descriptor and component set. Descriptor
hashes attest the declared algorithm identity/configuration, not Python source
code, captured source objects, or sandbox isolation. ArqSim's official
state-bound realizer carries immutable Program and Resource callback contexts
supplied by lowering. Immediately before state creation, evaluation checks
both contexts against the plan's circuit, architecture, effective
compiler-spec, and latency-profile hashes. Those hashes remain plan
authorities and are not duplicated in the component manifest.

The v6 codec strictly parses and recursively freezes the two DAGs, typed MOVE
operands, tagged deferred recipes, architectural inventory, provenance,
policy, and runtime-component manifest before validating the mandatory
`plan_hash`. The wire must contain the full exact canonical
`arqsim.runtime-manifest.v3` document and complete `BufferSpec` records,
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
runtime component set whose manifest matches the plan; Python callback code is
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
  -> ProfileExecutionBackend.prepare(...)
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

Completion has the corresponding two phases:

```text
ArchitectureState.propose_completion(reservation) -> StateDelta
ArchitectureState.apply_completion(delta)         -> committed completion
```

The delta resolves produced/forwarded token identity, output slots, engine
release, location changes, and future outcome payloads before any completion
mutation. Reservation ids are recorded across dispatch, the running frontier,
and completed events.

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

`arqsim.execution-trace.v3` has one serialized/hash authority:
`ExecutionTrace.transitions`, the append-only causal ledger retained at both
trace levels. It records each committed dispatch or completion immediately,
including reservation identity, concrete token/slot/engine claims, state
versions, typed Program lineage, measurements, continuation receipts,
outcomes, and the post-transition state projection. A zero-duration
dispatch and completion therefore retain their true interleaving instead of
being grouped by timestamp.

Completed `ExecutionTrace.events` are a Python compatibility property derived
from the initial-state counts and matching ordered dispatch/completion
transitions, then ordered by event ID. They are neither constructor input nor
part of the v3 trace wire/hash. `terminal_inflight` is the strictly checked
ledger-derived cache of unmatched Resource dispatches that cross the Program
horizon. The report-v1 renderer is the only boundary that serializes completed
event spans; its one-way output is hash-checked and byte-compared with the
frozen static fixture. Native Report v2 embeds Trace v3 without an event list.
The canonical trace parser accepts only the exact Trace-v3 record; it does not
parse report-v1 data or flattened `EvaluationResult` diagnostics.

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
