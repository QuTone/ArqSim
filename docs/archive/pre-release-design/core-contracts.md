# ArqSim Core Contracts

> Archived pre-release design record. This document is non-authoritative.
> Start from the current [documentation index](../../README.md).

Historical status at archival time: **Report/API-v2 boundary complete**

Framework contract target: open-source v1 (independent of Report schema v2)

Last updated: 2026-08-30

This document fixes the vocabulary, module boundaries, ownership rules, public
surface, and first-release scope for the ArqSim refactor. Native evaluation
uses Report v2; the retained v1 report is explicitly a one-way plain-JSON
compatibility projection whose field names are not part of the ground-up
architecture model.

## 1. Settled core decisions

The detailed contracts below record six decisions approved during the
ground-up architecture review.

### D1 — One canonical user workflow

The supported evaluation path is:

```text
source program -> Synthesizer -> SynthesisArtifact -> FTCircuit
pre-synthesized circuit ----------------------------> FTCircuit

FTCircuit + EvaluationConfig -> run_evaluation() -> EvaluationReport
                                                -> arqsim.evaluation-report.v2 JSON
```

`run_evaluation()` owns the normal orchestration. Direct calls to architecture
instantiation, compilation, plan lowering, or the event engine are extension
or internal APIs, not competing quickstart paths.

### D2 — Expose one architecture model

The public conceptual model has three objects:

```text
ArchitectureProfile -> ArchitectureSpecification -> ArchitectureState(t)
```

`ArchitectureProfile` is unsized authoring intent. Component IDs are
opaque stable references; code must never infer behavior from their spelling.
`CircuitStatistics` is an immutable Program-side view derived from `FTCircuit`;
it is not an architecture record. A `SizingPolicy` converts the Profile and
those statistics into a complete `SizingResult`. A `LogicalLayoutPolicy` then
converts the Profile, sizing result, and optional logical-layout request into a
`LogicalLayoutResult` covering every non-engine Submodule. QEC bindings and
selected QEC resource-protocol profiles are resolved independently.

`LogicalLayoutRequest` is a flat
`Mapping[SubmoduleKey, SubmoduleLayoutRequest]`; it has no Node-only wrapper or
secondary string-address convention. `LogicalLayoutPolicy.place(...,
request=...)` returns a `LogicalLayoutResult` containing complete
`SubmoduleLayoutResult` records. Bundled Profiles, sizing policies, and layout
policies are co-located by reference architecture in `architecture/gallery/`; the
runtime-only gallery catalog associates those three pieces. The resolver does
not import the gallery or dispatch on Profile IDs.
`NeutralAtomComputeFactoryLayoutPolicy` requires an explicit
`magic_output_origin`. The historical Profile 1.1 value
`(100, 40)` is a compatibility preset, not a canonical physical fact.

All six bundled Profiles use this seam and the same resolver. The multi-Node
recipes are explicit per family: Profile 1.3 uses
`RemoteMagicSizingPolicy`/`HybridRemoteMagicLayoutPolicy`; Profile 2.2 uses
`HybridMemoryComputeSizingPolicy`/`HybridMemoryComputeLayoutPolicy`; and
Profile 2.3 uses
`RemoteMagicMemoryComputeSizingPolicy`/
`RemoteMagicMemoryComputeLayoutPolicy`. Shared mechanical helpers do not turn
these recipes into a general rule language.

The generic `resolve_architecture()` function receives the Profile and those
already-effective results/bindings. It does not receive an `FTCircuit`, run a
policy, or dispatch on Profile IDs. All construction inputs are transient.
`ArchitectureSpecification` stores only the resulting immutable static graph:
`Node`, `Module`, `Submodule`,
`LocalConnection`, `Interconnect`, effective capacities and slots,
logical geometry, QEC bindings, and QEC resource-protocol references. It
stores no circuit facts, Profile or policy identity, source layout request,
construction provenance, footprint, timing, fidelity, physical calibration,
compiler route, or physical placement. `ArchitectureState(t)` alone owns mutable runtime
occupancy and locations.

The canonical serialized Specification root contains only `nodes`,
`interconnects`, and `architecture_hash`, plus its schema-version discriminator.
The hash depends on the effective static graph, not the inputs used to build
it. A circuit may influence sizing without becoming part of architecture
identity.

`Node` and `Interconnect` are peer resource owners. `Node.modality` selects a
logical capability/compiler family; it is not a physical microarchitecture
record. A `LocalConnection` connects two Submodules in distinct Modules owned
by the same Node or Interconnect. Its payload is derived from its endpoint
payloads and is not duplicated. In this name, *local* means owner-local: the
relative `Module/Submodule` endpoints never cross from one owner to another.
For a directed connection, they are ordered `(source, destination)`; a
bidirectional connection has no source or destination and its endpoints are
canonically sorted. Such connections are optional explicit inter-Module
topology, not required plumbing. Module-internal movement and
operation-specific interactions are encapsulated by the Module rather than
serialized into the architecture graph.

An `Interconnect` is an abstract distributed network/resource domain over its
endpoint Nodes, not a transfer event, single graph edge, physical line, or Bell
state. Its absolute `Node/Module/Submodule` endpoint references resolve to
existing Submodules and collectively span exactly two distinct Nodes. They declare
attachment only: they neither mirror capacity nor require the endpoint to carry
`bell_pair` payload. An endpoint may itself provide direct compute, memory, or
access. Any additional explicit path to a Submodule in another Module inside
its Node, when present, is a Node-owned `LocalConnection`. The Interconnect
itself may own distributed Bell Engine and Bell Storage Modules; an optional
Interconnect-owned `LocalConnection` expresses static inter-Module topology
only among its own Modules.

There is no canonical `BellLink`, `InterconnectConnection`, or
`InterconnectAccess` object. The Interconnect replaces the old overloaded Bell
link, and its distributed Modules remain single logical resource identities
even when realized at several endpoint Nodes. A Bell-pair state is runtime
payload held in a Bell Storage slot, not a static connection or Module. It
counts once against shared logical capacity, while footprint evaluation counts
its two endpoint-local logical-qubit halves.

Bell Storage slots have stable identity without default geometry. An explicit
layout request may place the shared slots on the Interconnect's own logical
canvas. It does not create per-endpoint canonical slots, Node-local Bell
Modules, or physical Bell placement.

Persistent `region` and `buffer` Submodules own logical slots. Slot identity is
mandatory, but per-slot geometry is present only when the slot has operational
spatial meaning. In particular, the logical modes encoded by a BB/QLDPC memory
block remain Program-bindable slots without pretending that those non-local
modes occupy separate spatial sites. An `engine` Submodule exposes aggregate
copy capacity for an opaque protocol and does not expose its internal patches
as Program-bindable slots.

A slot-owning non-engine Submodule may have capacity zero. It then owns no
slots and cannot carry an origin or grid. Resource-engine capacity remains
strictly positive. Profile 2.1 uses this rule when its compute partition owns
the complete circuit width and the complementary BB memory is empty.

`logical_origin` places one Submodule on its owner-local logical canvas. Spatial
slot coordinates authored for that Submodule are local, and the resolved
compiler-facing coordinate is:

```text
Owner-global logical coordinate = logical_origin + Submodule-local coordinate
```

A requested `rows` by `columns` grid reserves that complete Submodule
envelope, even when capacity leaves some cells empty. The logical-layout policy
places derived buffers outside that envelope; effective-layout and canonical
Specification validation reject another same-owner spatial owner inside it.
Empty cells remain placement space; they are not fabricated logical slots.
Public slot IDs are opaque and local to their owning Submodule. Bundled layout
policies materialize them uniformly as `slot_0`, `slot_1`, and so on; semantic
meaning comes from the complete owner/Module/Submodule path, not from prefixes
inside the local ID. The report-v1 renderer may map them to historical IDs
such as `D0`, `SL0`, `M0`, or `MO0` in its frozen JSON receipt. Compiler and
evaluator paths use canonical absolute identities; the report mapping is not
architecture-source provenance.

This logical origin is not a LightStim patch offset and cannot be copied into a
physical circuit. The logical compiler derives operation-specific routes and
interaction intent. A code-aware LightStim lowering may later combine that
intent with the selected QEC code and logical placement, instantiate a
`LogicalCouplerProtocol`, and construct its QEC-lattice
`LogicalCouplerPatch` geometry alongside BB block/mode bindings, physical patch
placement, and other ancilla geometry. Its provider, version, input hash,
binding, and realized placement belong in that lowering receipt. The direct
ArqSim-to-LightStim bridge is not implemented yet. Until such a lowering
supplies a physical distance, heterogeneous
logical-coordinate differences must not be interpreted as physical latency.
Physical QEC-lattice coordinates, Stim circuits, and real chip coordinates are
therefore not duplicated in the Architecture Profile.

Analytical neutral-atom movement remains available without a physical
lowering. The versioned neutral-atom movement record in
`OperationLatencyProfile` owns calibrated site spacing, transfer overhead, and
reference distance/time. The logical compiler combines those inputs with
logical coordinates and its versioned formula to estimate duration. Neither
the calibration nor its estimate becomes a field of `Node`.

The public authoring input is limited to stable logical placement facts; users
cannot supply `interfaces`, `routing_interface`, `adjacent_to`, routing nodes,
or routing edges. The retained report-v1 `logical_architecture.slots` and
`architecture_slot_layout` receipts nevertheless retain some of those
compiler-derived fields for byte compatibility and replay. They are read-only
effective artifacts, not a second architecture-authoring interface. Removing
them requires a future versioned report/schema migration rather than an
in-place cleanup of report v1.

### D3 — Treat resource-production protocols as black boxes

A magic-state factory or logical-Bell production protocol is an end-to-end
profile. Evaluation needs its selected identity and final statistics: resource
kind and batch size, arrival/timing model, space cost, output fidelity/error,
and provenance. ArqSim does not require a public `cultivate -> grow -> deform`
stage graph.

Protocol selection is resolved once per evaluation. The catalog identity owns
intrinsic facts such as outputs per batch, protocol-cycle cost, per-copy space, raw-resource
consumption, and output error. Runtime production timing and canonical fidelity
consume one effective operational binding; they must not recover protocol facts
from unrelated dictionaries. The static architecture stores only the selected
`QECResourceProtocolRef` where needed, not arrival models, fidelity, footprint, or
the operational resolution receipt.

When no resource-arrival override is supplied, the effective arrival
distribution is derived from the selected protocol and hardware timing/rate
parameters. An explicit arrival distribution is a timing sensitivity override
only: it does not silently replace protocol identity, output fidelity, batch
size, or footprint. The execution/evaluation receipt, rather than the
Architecture Specification, records the binding's base distribution, effective
distribution, resolution source, catalog/profile hash, and binding hash.
Changing a legacy alias to its canonical catalog ID is also explicit in the
selection receipt.

The split is therefore:

- `QECBinding` records the effective QEC choice on a persistent architecture
  Submodule where that description is meaningful.
- A QEC resource-protocol profile describes an entire production protocol, even
  when it internally crosses several codes.
- Operation profiles contain simulation- or literature-derived latency,
  fidelity, and arrival facts used by evaluation.
- An implementation recipe describes how a Program operation is realized at
  runtime; it may contain typed symbolic resource references.

The current magic-state and Bell profile objects mix some structural and
measured fields. Moving those fields is not required for the short MVP; their
provenance and meaning must remain explicit.

### D4 — Separate realization, selection, and mutation

For each dependency-ready Program operation, the target runtime path is:

```text
Program operation
  -> read implementation recipe
  -> expand local primitive operations and typed resource references
  -> propose concrete token/slot binding against StateSnapshot(version)
  -> run fast local route/group/duration compilation
  -> CandidateImplementation
  -> Scheduler selects a conflict-free batch
  -> Event Engine atomically commits it
  -> CommittedEvent / trace record
```

The same realizer boundary applies to recurrent Resource processes: it
produces tentative concrete candidates and cannot reserve state itself.

- Runtime realization owns candidate generation: binding policy and supported
  state-bound local compilation are one pure pre-commit seam in v2.
- Runtime compilation is local to the currently ready frontier and must be a
  fast heuristic. It does not recompile the whole Program.
- Scheduler selects, orders, and arbitrates candidates. It does not compile,
  bind by mutating state, or write the trace.
- Event Engine alone owns time, in-flight events, frontiers, and mutable state.
  It commits a selected batch atomically or not at all.
- Trace is passive and append-only. Analyzer only reads trace and resolved
  configuration.

The first implementation preserves current FIFO/first-free, Program-first,
eager-dispatch, and greedy-fill behavior through these cleaner interfaces.
New scheduling optimization comes later.

The implemented static-to-runtime seam does not claim
the future general recipe DSL. Offline compilation returns one immutable,
self-identifying `LogicalCompilationResult`, including its effective magic-state
consumption policy. Each `CompiledRouteResult` explicitly declares
`dispatch_deferred`; generic route metrics cannot act as a second control
channel. Plan lowering first verifies that the compilation policy matches the
evaluation policy, then represents eagerly known Program MOVE endpoints as
`MoveOperands` and creates a tagged `DeferredDispatchRecipe` only from a typed
deferred route. Only a `DeferredDispatchRequest` may pair that static recipe
with the tentative binding's concrete token and slot identities. The current runtime puts
binding proposal and both supported Program/Resource callback paths behind one
`RuntimeRealizer`; it may refine duration and attach an observational compiler
artifact, but cannot change operation claims or commit state.

### D5 — Reserve “Program–Resource ISA” for resolved work

The terms are:

- `FTCircuit`: architecture-independent synthesized logical circuit.
- `LogicalCompilationResult`: typed, hashed logical-compiler output. It binds the
  source circuit, architecture, latency profile, effective compiler spec,
  effective magic-state consumption policy (`bulk_wave` or `incremental`),
  compute units, initial mapping state, and deferred-capacity decision without
  constructing evaluation-plan objects. A source partition's `active_qubits`
  are the qubits touched by its operations; the keys of its compute unit's
  `mapping` are the separate compute-residency authority.
- `CompiledRouteResult.dispatch_deferred`: typed authority for whether a
  compute route is complete offline or needs supported dispatch-time route
  resolution. It is required by the compilation wire and hash. A deferred
  route has no eager route steps; metrics cannot contain legacy control keys.
  The field remains on the compiler result rather than being copied into
  instruction metadata.
- `ProgramDAG`: offline-compiled Program IR. Each node has logical operands,
  dependencies, mapped logical locations, and any resource-independent route
  skeleton. It does not identify a concrete magic state or Bell pair.
- `MoveOperands`: eagerly compiled per-qubit source/destination slot geometry
  for a Program `MOVE_QUBITS`; Module/Submodule location claims remain the
  runtime state authority. Source and destination maps cover the same non-empty
  logical-qubit set, slot IDs are injective within each map, and a Program plan
  requires `entity_kind="logical_qubit"` plus exact source/completion location
  claims for that set.
- `DeferredDispatchRecipe`: a strictly tagged static request for the small
  portion of routing or movement that depends on the eventual state binding.
- `ResourceRef`: an internal typed placeholder introduced by an implementation
  recipe, such as “one magic state satisfying these constraints.” It is not an
  executable trace instruction.
- `CandidateImplementation`: all primitive operands, token/slot choices,
  routes, engines, and duration are concrete against one state version, but the
  candidate is not committed.
- `CommittedEvent`: selected and atomically reserved work. This is the concrete
  Program–Resource ISA event recorded in the execution trace.

`ArchitectureInstruction` and `ResourceProcess` share one immutable
`OperationClaims` base for buffer consumption/production, forwarding, engines,
target Modules, and target links. This is an in-memory validation/ownership
contract only: their strict codecs keep the fields flat and the base owns no
independent schema or hash. Location claims, Program operands/dependencies,
Resource recurrence policy, and descriptive receipts remain plane-specific.
Candidates and prepared executions keep typed operation/binding/duration
fields and separate observational artifacts; generic metadata no longer
round-trips Engine control between runtime roles.

### D6 — Frontend consumes one versioned report

The frontend integration boundary is only:

```text
request adapter -> FTCircuit + EvaluationConfig
                -> run_evaluation()
                -> arqsim.evaluation-report.v2
                -> frontend view models
```

The frontend must not import evaluator state, DAG lowering, compiler helpers,
private synthesis modules, repository-local generated data, or Matplotlib
objects. Version-specific v1/v2 JSON adapters feed version-neutral view models;
the v1 path is temporary compatibility, not a second backend or state model.

## 2. Canonical module graph

```text
source -> Synthesizer -> FTCircuit
FTCircuit + ArchitectureSpecification + compiler/latency configuration
  -> Offline Logical Compiler -> LogicalCompilationResult

LogicalCompilationResult + ArchitectureSpecification + effective operational bindings
  -> staged plan lowering -> ExecutionPlan v8
       + ProgramDAG
       + ResourceDAG
       + architectural inventory and runtime-component manifest

ProgramDAG  -> Program frontier  --+
                                  +-> RuntimeRealizer
ResourceDAG -> Resource frontier --+         |
                                  CandidateImplementation(s)
                                            |
                                        Scheduler
                                            |
                                      SelectedBatch
                                            |
                           Event-Driven Engine <-> State(t)
                                            |
                   Program outcomes / Resource completions
                                            |
                           refresh frontiers and state
                                            |
                           ExecutionTrace -> Analyzer
                                            |
                                    EvaluationReport v2
```

Program execution never calls Resource production. Resource production is
recurrent work derived from the architecture and protocol configuration. It
affects Program dispatch only by changing tokens, slots, occupancy, engines,
and locations in shared architectural state.

Installed factory copies, buffers, engines, and capacities belong to the
static architecture specification. A scheduler may decide when and how much
of declared capacity to use. Architecture/program co-design belongs in sizing
and layout policies plus design-space sweeps. A future scheduler may consume
demand
forecasts or activate predeclared copies, but it must not silently rewrite the
declared architecture.

## 3. Module contracts

| Module | Inputs | Outputs | Owns | Must not own |
|---|---|---|---|---|
| Synthesizer (optional) | source program, `SynthesisSpec`, backend | `SynthesisArtifact`, then `FTCircuit` | backend invocation, artifact provenance/cache | architecture mapping or runtime state |
| Program | synthesized artifact or supported circuit file | immutable `FTCircuit` and `CircuitStatistics` | logical operations, layers, architecture-independent statistics, source provenance | architecture resources or concrete produced tokens |
| Architecture sizing | Profile, `CircuitStatistics`, selected protocol profiles, optional reference statistics/overrides | complete `SizingResult` | installed logical capacity decisions | QEC choice, slot placement, physical footprint, or source provenance |
| Logical layout | Profile, `SizingResult`, optional `LogicalLayoutRequest` | `LogicalLayoutResult` covering every non-engine Submodule | slot identity and optional owner-local logical geometry | sizing, QEC/protocol choice, routing fabric, or physical placement |
| Architecture resolver | Profile, `SizingResult`, `LogicalLayoutResult`, exact QEC bindings, selected protocol profiles | `ArchitectureSpecification` | mechanical validation/join into the effective static graph | circuit/statistics, policy execution, Profile-ID dispatch, evaluation metrics, runtime state, or physical placement |
| Offline Logical Compiler | `FTCircuit`, resolved architecture, latency profile, `LogicalCompilerSpec`, magic-state consumption policy | immutable, strict-codec `LogicalCompilationResult` | logical allocation/mapping, typed compute partitions/batches/routes and eager/deferred route state, effective compilation policy, compiler provenance | evaluation-plan objects, concrete consumable-resource binding, or runtime outcomes |
| Implementation recipes (target) | Program operation kind, architecture/QEC/profile context | primitive template plus typed `ResourceRef`s and conditional continuations | declarative operation realization | selection, optimization, or state mutation |
| Program plan lowering | `LogicalCompilationResult`, resolved architecture, transient `RuntimeTopology`/`PlanCostInputs`, policy | immutable `ProgramDAG`, initial locations | policy/source/architecture-capacity validation, Program dependencies and architecture-facing claims, projection of typed eager/deferred movement facts | deriving dispatch control from route metrics, recovering compiler requests/costs from metadata, runtime token selection, or state mutation |

The advanced `lower_compilation_result()` API directly consumes that strict
compiler artifact. `compile_and_lower()` is the composition boundary that first
invokes a `CompilerPipeline`, delegates to the same lowering path, and returns
both the compilation result and Plan.
| Resource lowering | resolved architecture and selected protocol profiles | immutable recurrent `ResourceDAG` templates | producer topology and declared parallelism | Program dependency advancement |
| Program frontier (target) | `ProgramDAG`, completed Program events/outcomes | dependency-ready Program operations | Program readiness counters | architectural feasibility |
| Runtime realizer | detached ready Program/Resource operation, immutable state snapshot, and optional typed deferred recipe | validated `CandidateImplementation` with tentative binding, duration, and observational compiler artifact | FIFO/first-free binding plus supported Program/Resource state-bound realization; future recipe expansion and local joint routing/grouping | changing source claims, state commit, process counters, Program readiness, or global arbitration |
| Scheduler | v1 immutable Program/Resource ID windows; future concrete candidates/context/history | v1 exact ID permutation; future conflict-free `SelectedBatch` | v1 deterministic within-plane order; future selection/arbitration/delay | recipe expansion, state mutation, trace mutation |
| Profile execution backend | tentative candidate and current time | prepared duration and isolated observational backend artifact | pure pre-commit preparation | Engine/state claim authority, state commit, or side-effecting hardware submit |
| Outcome model | committed event, snapshot, backend artifact, stable per-event seed | completion outcome | completion-time result derivation | clock/frontier/state mutation |
| Event-Driven Engine | plan, policies, selected candidates, outcome model | committed/running/completed events and final state | clock, frontiers, in-flight heap, RNG, atomic state transitions | cost-model invention or visualization |
| Architecture State | initial plan, engine-owned transitions | snapshots, blockers, reservations, state deltas | buffers, concrete token/slot/location identity, engine usage | scheduling policy or Program dependency graph |
| Trace | engine dispatch/completion records | replayable `ExecutionTrace` | event and state-delta lineage | any feedback decision |
| Analyzer | trace, resolved specification, latency/fidelity/footprint profiles | latency, stall, utilization, bottleneck, fidelity, and space metrics | derived attribution | execution mutation |
| Public API | `FTCircuit`, `EvaluationConfig` | `EvaluationReport` | orchestration and versioned serialization | alternative hidden execution semantics |
| Frontend adapter | request payload and validated report v2 JSON, plus temporary v1 compatibility JSON | version-neutral view models | schema validation and presentation mapping | imports from core internals or recomputed execution semantics |

The exact `arqsim.runtime-component.v3` role IDs nested in
`arqsim.runtime-manifest.v3` are
`runtime_realizer.state_bound.v3` (or
`runtime_realizer.direct_fifo_injection.v2` for a
direct plan), `scheduler.program_first_eager.v1`, `backend.profile.v1`, and
`outcome.seeded_bernoulli.v2`. Former binding/compiler/Resource-resolver roles and request
types have no shim or manifest alias. The Program/Resource deferred callback
IDs remain internal provenance checked by the built-in realizer, not extra
roles.

## 4. Runtime transaction contract

At each simulation timestamp, the Step 3 engine performs:

1. Complete every event ending at this timestamp.
2. Atomically apply completion deltas and realized measurement/outcome data.
3. Refresh Program and Resource frontiers.
4. Give the `RuntimeRealizer` a fresh immutable `StateSnapshot(version)` and
   one detached operation.
5. Let it propose one fully concrete tentative candidate without mutating
   state.
6. Preserve the current Program-first/Resource insertion-order selection.
7. Revalidate the binding and complete operation contract against current state.
8. Atomically commit the candidate, append its dispatch record, and start it.
9. Repeat the same-time fixed point, including zero-duration work.
10. Advance to the next completion timestamp.

The tentative internal vocabulary is:

| Contract type | Step 3 status and purpose |
|---|---|
| `StateSnapshot` | implemented immutable versioned view used by binders and runtime compilers |
| `RuntimeCandidate` | Engine-private `_DispatchCandidate`; one realized but uncommitted Program/Resource event |
| `ResourceRef` | typed symbolic resource need introduced by a recipe |
| `TentativeBinding` | implemented concrete token, input/output slot, engine, and location claims tied to a state version |
| `DeferredDispatchRecipe` | implemented tagged static compiler request (`joint_magic_route` or `state_bound_resource_move`); serialized independently of generic metadata |
| `DeferredDispatchRequest` | implemented immutable pairing of one deferred recipe with only the tentative binding's consumed tokens/slots and produced slots |
| `CandidateImplementation` | implemented realizer result carrying immutable operation view, tentative binding, duration, and observational compiler artifact |
| `SelectedBatch` | deferred beyond first-release compatibility scheduling; v1 commits one candidate at a time |
| `DispatchDelta` | implemented as immutable `StateReservation` |
| `CommittedEvent` | fully resolved running work with start/end time and reservation identity |
| `CompletionDelta` | implemented as immutable `StateDelta`; deterministic/custom outcome seam exists, conditional continuation remains deferred |

These names are internal review targets, not public v1 API promises.

## 5. Required invariants

### Static and serialization

- Program IDs, predecessor references, plane opcodes, buffer/engine references,
  quantities, and durations are valid.
- A `LogicalCompilationResult` is recursively immutable and self-hashed; its circuit,
  architecture, and latency-binding hashes must match the plan-lowering inputs,
  its magic-state consumption must match the evaluation policy, and its compute
  units cover every source operation exactly once.
- Every compiled route owns a plain boolean `dispatch_deferred`. A deferred
  route contains no eager route steps, a non-magic source batch cannot defer,
  and route metrics cannot duplicate dispatch-control fields. Program lowering
  creates a magic-route recipe if and only if this typed state is true; eager
  magic routes receive no recipe or dispatch-time recompilation. Instruction
  metadata does not copy the boolean as a second authority.
- Compiled partition width and per-unit magic demand do not exceed the
  canonical compute and magic-input capacities. An `incremental` compilation
  contains at most one magic operation per compute batch. A partition's
  `active_qubits` are its operated source set; every compute-unit mapping must
  include that set, and its keys—not `active_qubits`—own residency. Each unit
  has exactly `min(circuit logical width, compute capacity)` residents, and its
  complement fits memory capacity; circuit width fits their combined capacity
  even when the result has no compute units. Non-deferred units preserve the
  full initial mapping.
  Deferred adjacent units may exchange residents, but overlap qubits retain
  their slots; every mapping uses canonical compute data slots. Every deferred
  exchange has equally sized outgoing/incoming waves bounded by the canonical
  Store/Load endpoint capacity. STORE leaves the outgoing wave staged; one
  LOAD completion atomically swaps it with the incoming memory wave, so the
  memory region and local/remote endpoints never transiently overflow. Remote
  LOAD `qubits` name only the incoming operational payload; the wider atomic
  state exchange lives in its location claims, preventing fidelity/exposure
  double-counting. Bell-pair movement runs one direction at a time. Reordering location
  completion preserves the per-round service multiset—one STORE and one LOAD
  syndrome service, plus outbound/inbound TELEPORT for remote memory—on one
  predecessor chain, so critical-path duration is unchanged. Every lowered
  plan records the compiler result's `compilation_hash` and effective
  `compiler_spec_hash` in provenance; it does not invent a compiler allocation
  policy marker.
- Typed MOVE operands and deferred recipes are opcode/plane appropriate and
  cannot be duplicated through generic operation metadata. MOVE endpoint maps
  are injective, cover the same logical-qubit set, use the Program-only
  `logical_qubit` entity kind, and exactly match their required/completion
  location claims; each slot is nested under its claimed location. Magic
  routes consume only magic-state buffers, without assuming a distinguished
  buffer ID; Resource-move recipe entity kinds match both forwarded buffer
  token kinds. Canonical instruction and Resource metadata reject their legacy
  top-level dispatch controls and reject `deferred_until_dispatch` or
  `dispatch_deferred` inside route metrics; report v1 alone reconstructs its
  historical view.
- Execution-plan initial locations map strings to strings. Operation buffer
  and engine claims do not exceed installed capacities, except Resource
  production explicitly declaring `discard_excess`; Program MOVE requires
  typed operands. The strict v8 wire recursively accepts only exact JSON
  object/array/scalar shapes with string keys and finite numbers; it requires
  complete canonical buffer records, a full exact runtime manifest, and a
  checked `plan_hash`, rejects duplicate JSON keys, and must round-trip to the
  same unsigned canonical document. The strict compiler wire applies the same
  text/canonical-shape rules and likewise requires complete route-step records.
- A plan containing a deferred recipe rejects the direct/no-op runtime
  manifest. Canonical plan construction selects its callback-capable manifest
  when the caller does not explicitly provide one. Component manifests
  identify algorithms; evaluation separately validates the immutable source
  context of ArqSim's official state-bound callbacks against the plan's
  circuit/architecture/compiler/latency authorities.
- Runtime-component manifests, plan provenance, initial locations, snapshots,
  requests, bindings and deltas included in semantic hashes are detached and
  deeply immutable. Remaining public-config hardening is tracked with Step 5.
- One effective config, plan, seed, and named policy set produces a
  deterministic result.
- Execution-plan provenance records effective runtime compiler, binding,
  resource resolver, scheduler, and outcome-model identities/hashes.
- A resource protocol ID, its catalog-owned intrinsic facts, runtime arrival
  model, output error, and protocol footprint are joined in one hashed binding.
  Explicit timing overrides preserve the catalog-derived base arrival and
  record why the effective arrival differs.

### Tentative resolution and commit

- Check, bind, compile, and schedule are pure: success or failure leaves state
  unchanged.
- Every binding satisfies kind, quantity, location, engine, and slot
  constraints.
- The current single-candidate commit claims each token, input slot, output
  slot, and engine capacity exactly once. A future selected batch must preserve
  that rule across all candidates.
- A binding carries a state version; stale bindings are rejected.
- The current candidate commit is all-or-nothing and callable only by the Event
  Engine. Future batch commit must preserve the same transaction boundary.

### Runtime state

- For every buffer, `0 <= ready + pending <= capacity`.
- Pending output count equals reserved output-slot count; ready and reserved
  slots do not overlap.
- Every ready token is globally unique and has exactly one buffer, slot, and
  location.
- Forwarding preserves token identity, kind, and count.
- For every engine, `0 <= users <= capacity`; every claim is released exactly
  once.
- Required locations still hold at commit. Completion locations change only
  during completion.
- Resource-process instance IDs are monotonic and in-flight instances never
  exceed declared parallelism.

### Control and trace

- Event time is monotonic, duration is non-negative, and
  `end = start + duration`.
- All simultaneous completions are applied before same-time dispatch.
- Program readiness advances only through Program completion or resolved
  Program outcomes. Resource completion changes only architectural readiness.
- Every committed event completes at most once and is linked to its
  reservation and source candidate.
- Dispatch and completion deltas replay to the reported final state, including
  Resource events still in flight when the Program horizon is reached.

## 6. Public surface policy

The stability levels mean:

- **Stable:** documented user dependency with compatibility tests.
- **Advanced:** supported composition/extension interface, but not the default
  path and subject to an explicit schema-major revision before 1.0.
- **Internal:** implementation detail; no compatibility promise.

The package root has exactly four
wildcard exports: `EvaluationConfig`, `EvaluationReport`, `FTCircuit`, and
`run_evaluation`. The list below records supported contracts at their owning
subpackages. Former root aliases remain identity-preserving, warning
compatibility names for one tagged minor release and are not in root `__all__`.
The public-surface audit removed internal ISA/state, low-level compiler helpers,
dead registry types, and paper-fit helpers from subpackage wildcard surfaces; owning modules
remain the only import path for implementation details that still exist.
The frozen migration contract is
[`report-api-v2-contract.md`](report-api-v2-contract.md).

### Stable service and data contracts

- Program: `FTCircuit`, `CircuitStatistics`, `LogicalLayer`,
  `LogicalOperation`, `make_layers`, and `circuit_statistics`. Existing
  `load_ft_workload`, `workload_stats`, `WorkloadParseError`, and workload
  schema names remain compatibility APIs during the rename boundary.
- Architecture: `ArchitectureProfile`, `ArchitectureSpecification`, profile
  loading/listing, and `build_architecture_specification`. Static contracts
  come from `arqsim.architecture`; the gallery convenience builder comes
  from `arqsim.specification`. Deprecated root aliases still denote the same
  objects during the one-release migration window.
- Profiles: `ArrivalDistribution`, `OperationLatencyProfile`, and
  `FidelityProfile`.
- Configuration: `LogicalCompilerSpec`, `BackendSpec`, `ExecutionPolicy`, and
  `PhysicalFootprintModel` because they are accepted by the facade config.
- Evaluation facade: `EvaluationConfig`, `EvaluationReport`,
  `run_evaluation`, and `FTCircuit` form the small root facade. Schema-version
  constants, named preset constants, the typed `EvaluationSummary`, and staged
  `EvaluationRunError` are imported from `arqsim.api`.
- Optional synthesis: `SynthesisSpec`, `SynthesisArtifact`, `Synthesizer`,
  `synthesize`, and `load_artifact_workload`.
- Visualization: public plot functions remain pure consumers; their inputs and
  return type are supported, while exact visual styling is not a compatibility
  contract.

### Advanced extension surface

- Canonical static graph records: `Node`, `Interconnect`, `Module`,
  `Submodule`, `LocalConnection`, `LogicalSlot`, `QECBinding`,
  and `QECResourceProtocolRef`.
- `SizingPolicy`/`SizingResult`, `LogicalLayoutPolicy`/
  `LogicalLayoutResult`, exact QEC resource-protocol selection, generic
  `resolve_architecture()`, and specification sweeps. Policies are construction
  services; only their effective results and bindings enter the resolver. None
  are additional architecture levels.
- QEC resource-protocol catalog profiles and their loaders.
- Synthesizer backend interface and concrete backend adapters.
- The whole `CompilerPipeline` replacement interface, immutable built-in
  mapping/routing selection records, and the strict `LogicalCompilationResult`
  direct-compilation boundary. Low-level allocator/mapper/router functions and
  backend registries remain internal until a second real extension requires a
  separately replaceable pass contract.
- Direct plan/DAG/trace construction, direct `evaluate()`, estimators, and
  analyzer functions.
- Descriptor-bearing runtime component contracts and explicit
  four-role `RuntimeComponentSet` composition. Custom Python components are
  trusted extensions, not sandboxed code.

### Internal or reserved

- `ArchitectureState`, reservations/deltas/block reasons, event-loop state,
  running-event records, frontier counters, and runtime-realizer builders.
  `architecture/isa.py` currently houses `OperationClaims`, typed
  `MoveOperands`, and deferred-recipe records used by plan/runtime internals;
  `architecture/state.py` houses mutable runtime state. Their location does not
  make either part of the canonical static Specification. If import paths are
  reorganized later, move these coupled execution contracts together behind an
  execution-owned boundary rather than splitting them across static and
  runtime packages.
- Concrete low-level mapping, placement, route, movement, and hierarchy helper
  functions unless explicitly promoted by an extension contract.
- The unused `QECProtocolSpec`, `QECCodeSpec`, and `OperationProfileCatalog`
  registry abstractions were deleted.
- Historical aliases, private presets, paper-specific fit helpers, and backend
  cache/registry details.
- The removed `CompatibilityArchitecturePackage`, `WorkflowLayoutContext`,
  `ArchitectureLayoutPlan`, `ArchitectureSpec`, `QECConfiguration`,
  `ResolvedFTSystemSpec`, private construction templates, combined layout
  policy, and legacy adapter have no supported import or extension contract.

The former `architecture/layout_policy.py`, `quantile_layout.py`, horizontal
`layout_policies/`, compatibility hierarchy/spec modules, and shared adapter
were deleted during the canonical architecture cutover. Report-v1 compatibility now exists only as a pure
one-way plain-JSON renderer and is not an extension point for layout work.

Documentation promotes only the small stable facade; advanced types should be
imported from their owning subpackage in new code.
`ArchitectureState`, DAG lowering, and runtime builders must not be advertised
as root-level user configuration.

## 7. Current implementation to target mapping

| Current implementation | Target interpretation | Gap / migration rule |
|---|---|---|
| `FTCircuit` and `CircuitStatistics` | architecture-independent Program input and derived sizing facts | retain; policies consume `CircuitStatistics` directly |
| `SizingPolicy -> SizingResult` and `LogicalLayoutPolicy -> LogicalLayoutResult` | independent capacity and logical-placement seams | all six bundled Profiles are connected through explicit family policies; add new policies, not per-Profile resolvers |
| generic `resolve_architecture()` | mechanical join into the canonical static graph | complete for Node- and Interconnect-owned resources across all six bundled Profiles |
| canonical `ArchitectureSpecification(nodes, interconnects)` | immutable effective static graph | direct compiler, footprint, plan-lowering, evaluator, and API authority |
| removed compatibility package, combined layout policy, and resolved containers | historical migration scaffolding | deleted after the canonical consumer cutover; no production import or reverse projection remains |
| `LogicalCompilationResult` | immutable logical-compiler output and serialization boundary | complete strict `arqsim.logical-compilation-result.v1` codec, source hashes, effective magic-consumption policy, semantic hash, typed route dispatch state, complete route-step records, exact circuit coverage, operated-partition versus resident-mapping semantics, canonical compute-slot validation, and compute/memory capacity gates; it does not own plan/runtime state |
| `ProgramDAG[ArchitectureInstruction]` | architecture-facing Program IR lowered from `LogicalCompilationResult` | eager MOVE slot geometry and deferred dispatch are typed; shared state/topology claims inherit the flat `OperationClaims` base |
| flat `OperationClaims` on Program/Resource operations | common immutable state/topology needs without a second wire layer | complete in-memory base with no independent codec/hash; location/operand/recurrence/receipt fields remain plane-specific, and static values are not proof of a runtime binding |
| `ResourceDAG[ResourceProcess]` | recurrent producer templates | retained behind the same pure `RuntimeRealizer` boundary as Program work |
| transient `RuntimeTopology`, `PlanCostInputs`, and `SyndromeCostInput` | explicit values passed between plan-lowering stages | intentionally no codec/hash; canonical architecture and latency profile remain the authorities |
| `ExecutionPlan` v8 | immutable reproducibility envelope | strict exact-JSON `arqsim.execution-plan.v8`; requires a checked plan hash, full exact `arqsim.runtime-manifest.v3`, complete buffer records, an explicit injection mode, timing-free recipe-v2 records, typed shared-phase recipe membership, and lowerer-generated Program-work templates; recursively freezes and validates typed DAG contents/claims, exact logical-qubit MOVE location sets, recipe resource pools/finite closure, template timing receipts, and globally unique invocation identity; carries `compilation_hash` lineage; older plans fail closed |
| `StateSnapshot.check_start()` | immutable feasibility query used by realization, runtime, and replay | canonical; the unused mutable-state forwarding wrapper is deleted |
| removed `ArchitectureState.start()` | formerly mixed FIFO/first-free selection with immediate mutation | replaced by snapshot `propose()` plus `commit(binding, operation)` |
| state-bound Program/Resource callbacks | partial slot-dependent Program routing and Resource movement | wrapped together behind descriptor-bearing `RuntimeRealizer`; official callbacks carry separate source contexts checked against plan authorities, while finite injection continuation is an explicit manifest algorithm; a general gadget DSL remains deferred |
| `RuntimeScheduler` over typed Program/Resource scheduling requests | deterministic within-plane tie breaking | canonical four-role component seam; the superseded scheduler/policy hierarchy is deleted, while general candidate selection remains future |
| Engine dispatch loops | fixed phase kernel plus shared private candidate/commit path | now call four pure component seams while retaining Program-first/eager/resource-fit/horizon ownership |
| `ExecutionTransition` causal ledger plus derived `ExecutionEvent` spans | every trace level retains ordered dispatch/completion deltas, state versions, concrete claims, typed Program lineage with shared recipe membership, measurements/continuations, and checked terminal in-flight Resource work | strict `arqsim.execution-trace.v4` serializes/hashes transitions and projections only; events are derived and injected only by report v1; stream/filter the ledger later for scale |
| read-only Analyzer service | derives utilization, qubit exposure, fidelity, time and space facts from immutable result/spec/profile inputs | full diagnostic observations remain required for exact wait and occupancy attribution |
| native `EvaluationReport` v2 | factory-only, self-contained stable evaluation boundary with typed headline `summary`, exact request, resolved authorities, logical compilation, Plan v8, Trace v4, and checked results | complete and re-frozen before the first public commit; live and loaded reports share the stable result surface and identity, while strict parsing re-lowers compilation, replays Trace, and recomputes all derived results without rerunning compiler or runtime |
| explicit Report-v1 renderer | frozen static-only compatibility boundary | retain temporarily as a one-way typed-artifacts-to-JSON adapter; it fails fast on dynamic recipes and is never a native execution or parsing authority |

Remaining capability risks:

1. The implemented Scheduler only reorders within each plane. Program-first,
   eager dispatch, Resource fitting/batching, and cross-plane arbitration remain
   fixed kernel semantics; atomic selected batches are future work.
2. Finite neutral-atom T injection schedules one entangle and one measurement
   phase shared by every recipe member in a `CompiledComputeUnit`, then fans out
   per-invocation reactions/corrections. Cross-unit gadget fusion, automatic
   STAR lowering, and arbitrary gadgets remain future work. The additive phase
   split is a provenance-recorded reference assumption built from existing
   compiler/latency authorities; it is not a new user timing profile.
3. State is versioned but still lacks some concrete engine/link identity.
4. The finite injection contract carries typed measurement-driven
   continuations and materialized logical Clifford correction, but a general
   classical store, arbitrary guards, and open-ended conditional control are not modeled.
5. Exact wait-cause and buffer-occupancy attribution still needs the full
   diagnostic observation log; summary retains causal replay but not those
   derived views.
6. Component descriptors attest self-reported semantic identity/configuration,
   not source-code identity or adversarial isolation. Plugin discovery remains
   deferred.
7. Superconducting Profiles 1.2 and 2.2 expose overlap-aggregated compute
   timing rather than a causal CX-then-measurement authority. Explicit finite
   mode fails closed there; `black_box` remains supported.
8. Gadget fidelity retains aggregate logical T plus resource-token authority.
   Shared implementation phases cannot add duplicate logical channels; a
   primitive fidelity decomposition would require an explicit replacement
   policy.

## 8. Frontend contract

The frontend adapter must validate `schema_version` and prefer the serialized
`arqsim.evaluation-report.v2` object. Its exact top-level fields are:

```text
schema_version
workflow_id
request
resolved_inputs
artifacts
results
report_hash
```

`request.config` remains an independently parseable
`arqsim.evaluation-config.v1` request. `resolved_inputs` contains the
canonical Architecture Specification, effective latency profile, footprint
model, and optional fidelity profile. `artifacts` contains exactly the logical
compilation, Plan v8, and Trace v4 authorities. `results` contains only checked
observations and derived footprint/fidelity/analysis/summary records. Compiler,
runtime policy/components, resource-protocol bindings, and hashes are not
duplicated at the top level; their owning compilation, Plan, and Trace records
bind them. Public JSON rejects duplicate keys, non-finite values, missing or
unknown fields, coercion, hash mismatches, and derived-result drift.

Direct evaluation and Report v2 use `arqsim.execution-trace.v4`, whose exact wire/hash
authority is the ordered `transitions` ledger plus initial/terminal projections
and checked ledger-derived `terminal_inflight`. The report-v1 renderer alone
adds derived completed `events` and labels that compatibility receipt
`arqsim.execution-trace.v1`; native v2 timeline adapters derive completion
spans from Trace-v4 transitions. The canonical v4 trace parser does not accept
report-v1 evaluation data. `trace_hash` attests the canonical
transition-only record independently of the outer report hash. The Python
v2 codec treats nested and outer hashes as self-hashes, not signatures. It
rebuilds the requested resolved architecture and operational bindings,
deterministically re-lowers the embedded `LogicalCompilationResult`, replays
the ledger, validates the diagnostic-log projection, and recomputes footprint,
fidelity, analysis, and summary. It does not rerun the logical compiler,
scheduler, outcome model, or Event Engine. The full diagnostic log remains
outside `trace_hash` and is accepted only after exact Plan/ledger validation.
Frontend v1 and v2 adapters may map validated documents into common UI view
models, but they cannot recompute evaluation semantics.

Report v1 is available only through the explicit one-way renderer (or CLI
`--report-version v1`) during its deprecation window. There is no v1-to-v2 or
v2-to-v1 document converter, and dynamic recipes fail at the v1 boundary.

Do not restore imports from removed historical packages such as
`arqsim.des`, old `arqsim.synthesis`, or `architecture.config`. Do not make
the frontend depend on `compiler_data` or other repository-relative artifacts.

## 9. Deferred beyond the first open-source release

The current design leaves interfaces for these ideas without implementing them
now:

- a general gadget/implementation-recipe DSL and registry;
- general measurement-conditioned branching, a classical store, and arbitrary
  feed-forward correction sequences beyond the finite injection contract;
- explicit protocol stage/FSM descriptions;
- scheduler optimization beyond behavior-compatible policies;
- adaptive, look-ahead, or trace-trained scheduling;
- dynamic factory-copy activation beyond predeclared architecture capacity;
- dynamic qubit recycling and module reinitialization;
- joint architecture/program co-design optimizers;
- explicit compiler wall-clock overhead in simulated time;
- concrete engine-lane and link-channel identity where not required by the
  current evaluator;
- plugin discovery and third-party registry APIs;
- broad renaming of existing architecture/QEC types.
