# Known Limitations and Naming

Roadmap · Role: current limitations and deferred work · Status: current for ArqSim 0.2.0

The project/framework name is **ArqSim**. The installable Python distribution,
import package, and CLI are **`arqsim`**. Serialized schema names use the
`arqsim.*` namespace.

## Logical versus physical geometry

- `logical_origin` and slot coordinates live on independent Node-local logical
  canvases. They are not physical-qubit coordinates or LightStim offsets.
- Neutral-atom movement currently uses a versioned simple site-spacing formula
  in the logical compiler. Its physical calibration (site spacing, transfer
  overhead, and reference distance/time) comes from
  `OperationLatencyProfile`, so compiler-active coordinates can affect the
  analytical AOD duration without claiming a physical placement.
- The superconducting compatibility backend derives a dense logical routing
  canvas and rejects canvases above its explicit 100,000-site limit. Sparse
  routing and general LightStim lowering are future backends.
- BB/QLDPC logical modes have runtime slot identities but no meaningful
  per-mode spatial coordinates. The current footprint groups them into code
  blocks, while block/mode placement is deferred to a physical mapper.
- The physical-footprint result is an accounting estimate, not a placement.
  It does not assign physical qubits, patches, couplers, or ancillas to sites.
- BB-to-surface STORE/LOAD and inter-Node transfer use versioned operation
  profiles. ArqSim does not infer their physical latency from coarse logical
  coordinate differences.

## Architecture resolution

- `CircuitStatistics.magic_states_per_layer` currently counts each logical
  `t`/`tdg` operation and each Pauli rotation as one unit of global magic
  demand. That architecture-independent convention is sufficient for the six
  bundled baseline sizing policies; it is not a general resource-demand model.
  In particular, STAR-style `theta`/`2theta`/`4theta` chains can require
  architecture-, compiler-, and branch-specific resource demand. That demand
  must be produced by a dedicated architecture/compiler analysis and must not
  be added by widening the meaning of this generic statistics field.
- `FTCircuit.representation` is a parser/IR dialect identifier, not proof of a
  gate set. The current compiler compatibility checks are bounded by each
  backend's implemented operation capabilities; a new dialect or operation
  therefore needs an appropriate backend validation path rather than a new
  global inference from the representation string.
- `OperationLatencyProfile.gate_duration_s` is currently modality-level, not
  logical-operation-level. Missing active modalities fail closed, but this
  schema cannot express that an RZ/STAR rotation has a different service model
  from another gate on the same modality. The bundled fidelity gate prevents
  an unmodeled rotation from producing a default end-to-end success estimate;
  a real STAR path still requires its own compiler capability/lowering and an
  operation-aware timing extension. Disabling fidelity is not evidence that
  such an operation has been physically modeled.
- `CompilerPipeline.compile()` receives the complete `FTCircuit`, but the
  current program IR retains conservative normalized layers rather than the
  complete operation-dependency graph. The bundled compiler optimizes one
  source-layer partition/batch at a time, and `LogicalCompilationResult v1`
  cannot represent persistent cross-layer route continuity, compiler-selected
  SE/checkpoint placement, or a general scheduled action DAG. Those
  capabilities require a future compiler-facing Program IR, result contract,
  and bounded runtime-recourse interface.

- The architecture model has exactly three levels: `ArchitectureProfile ->
  ArchitectureSpecification -> ArchitectureState(t)`. `CircuitStatistics`,
  policies, policy results, layout requests, and QEC/protocol catalogs are
  transient construction inputs, not architecture levels or fields in the
  canonical static graph.
- Construction now has explicit seams:
  `CircuitStatistics -> SizingPolicy -> SizingResult`, followed by
  `ArchitectureProfile + SizingResult -> LogicalLayoutPolicy ->
  LogicalLayoutResult`. Exact QEC bindings and selected QEC resource-protocol
  profiles remain independent inputs. The generic `resolve_architecture()`
  receives only the Profile and these already-effective results/bindings; it
  never receives an `FTCircuit`, executes a policy, or dispatches on a Profile
  ID.
- All six bundled Profiles use that generic resolver and materialize their
  canonical `Node`/`Module`/`Submodule`/`LocalConnection`/`Interconnect`
  graphs. All six unsized authoring documents use Profile v3, and Profiles
  1.3, 2.2, and 2.3 have explicit sizing and logical-layout policies without
  adding per-Profile resolvers.
- Each reference architecture's Profile, sizing policy, and layout policy are
  grouped in one `architecture/gallery/<reference-name>/` bundle. The
  gallery catalog is an explicit built-in index, not a plugin registry or
  serialized architecture authority. Generic profile parsing, construction,
  and resolution do not depend on it.
- The one-shot builder accepts strict dotted sensitivity overrides and an
  optional `LogicalLayoutRequest`, then instantiates the selected gallery
  architecture's own sizing and layout policies. It does not accept or hydrate
  the removed combined `QuantileLayoutPolicy`. Independent policy objects and
  exact `SizingResult`/`LogicalLayoutResult` values remain available through
  the direct construction API.
- The new request is a flat
  `Mapping[SubmoduleKey, SubmoduleLayoutRequest]`, and
  `LogicalLayoutPolicy.place(..., request=...)` returns a complete
  `LogicalLayoutResult` of `SubmoduleLayoutResult` values. The bundled
  `NeutralAtomComputeFactoryLayoutPolicy` requires an explicit
  `magic_output_origin`.
  Its historical Profile 1.1 preset uses `(100, 40)` only to preserve baseline
  behavior; that coordinate is not a canonical physical fact. The
  `SuperconductingCheckerboardLayoutPolicy` stores final occupied-patch
  coordinates directly on its logical checkerboard lattice. The SC compiler
  compatibility backend derives routing nodes, edges, and interfaces from
  those coordinates; none of that routing fabric is copied into the canonical
  Specification.
- The frozen report-v1 SC projection retains one historical default portal
  alias: the default factory-output patch at `(8, 0)` is paired with the
  adjacent `R7_0` interface while the legacy routing graph still contains its
  old `R8_0` node. Explicit output placement is lowered from the requested
  occupied sites and does not use this alias. This exception belongs only to
  the one-way report renderer. The canonical compiler already forms its
  routing complement directly from occupied Specification slots.
- Profile v3 expresses canonical Interconnect-owned Modules, owner-local
  connections, and absolute attachment endpoints directly. The bundled
  multi-Node Profiles now complete sizing, layout, QEC resource-protocol
  selection, and canonical construction through that graph. Compiler,
  footprint accounting, execution-plan lowering, evaluator, and API consume
  the canonical graph directly.
- The canonical architecture cutover deleted `WorkflowLayoutContext`, `ArchitectureLayoutPlan`,
  `ArchitectureSpec`, `QECConfiguration`, `ResolvedFTSystemSpec`, the old
  `ArchitectureLayoutPolicy`/`QuantileLayoutPolicy`, `legacy_adapter.py`,
  compatibility hierarchy/spec modules, horizontal `layout_policies/`, and
  all private specification templates/wrappers. None is a second authority,
  extension surface, or supported import.
- The retained report-v1 serializer is a pure one-way renderer from canonical
  and independent downstream artifacts into plain JSON. It may emit the
  frozen old field names and hashes, but it imports/instantiates no removed
  object and its output never becomes compiler/evaluator input. Native Python,
  CLI, and integration output now use strict Report v2; v1 remains temporarily
  for static legacy consumers and fails fast on dynamic recipes.
- New policies implement the interface from
  `architecture/logical_layout_policy.py`; bundled concrete recipes belong in
  their reference architecture's `architecture/gallery/` bundle.
- The current compiler targets exactly one compute Module per evaluation.
- The canonical `Interconnect` is a Node-peer network/resource domain. It may
  own distributed Bell Engine and Bell Storage Modules plus owner-local
  connections among those Modules. Its absolute endpoint references attach the
  domain to existing Submodules and collectively span exactly two distinct
  Nodes. Attachment does not require `bell_pair` payload or mirror capacity; an
  endpoint may itself be a direct consumer/access Submodule. Any additional
  explicit path inside that Node is an optional Node-owned `LocalConnection`.
  There is no separate `BellLink`, `InterconnectConnection`, or
  `InterconnectAccess` record. Profiles 1.3, 2.2, and 2.3 are resolved through
  this canonical shape. Detailed channel allocation, repeaters, swapping
  protocols, and physical endpoint placement are not modeled.
- One Bell-pair state is one shared runtime-capacity item stored by Bell
  Storage, while footprint accounting counts its two endpoint-local logical
  qubits. Concrete placement of those halves remains physical-lowering work.
- Bell Storage slots are identity-only by default. An explicit layout request
  may place those shared slots on the Interconnect's logical canvas, but it
  cannot author endpoint halves, Node-local mirrors, or physical placement.
- Factory and Bell protocols are black boxes. Their internal patches, code
  transitions, and ancillas do not appear as runtime logical slots.
- Resource-protocol identity and intrinsic batch/space/error facts come from
  one catalog record. Runtime arrival and canonical fidelity use the resulting
  resolved binding. Explicit arrival distributions are supported sensitivity
  overrides and are recorded separately from the catalog-derived base arrival;
  they do not change protocol identity or output fidelity.
- Bell-production timing currently models one physical-pair supply rate shared
  across installed protocol copies and takes the slower of local protocol work
  and raw-pair supply in steady state. Because those stages cannot overlap
  before a copy's first batch, each copy's first producer sample additionally
  pays the smaller service component; this cold-start fact is preserved by
  derived arrival-kind overrides. More detailed channel allocation,
  contention, repeaters, and swapping remain future resource backends.

## Runtime scheduling

- `RuntimeScheduler` v1 is a frozen within-plane ordering seam. Its Program
  request contains dependency-ready IDs that may still be resource-blocked,
  while its Resource request contains every recurrent process ID. It must
  return each presented ID exactly once. Ordering can affect which same-plane
  contender acquires capacity first, but the Scheduler cannot filter work,
  intentionally defer it, select across planes, or commit several candidates
  atomically. Feasibility, Program-first priority, eager dispatch, Resource
  fitting/batching, the same-time fixed point, single-candidate commit, and
  clock advancement remain fixed Event-Engine semantics. The historical
  component ID `scheduler.program_first_eager.v1` remains unchanged for v1
  compatibility; its spelling does not transfer those kernel responsibilities
  to the Scheduler.
- A future Scheduler v2 may receive one unified Program/Resource candidate
  window, express intentional defer and cross-plane arbitration, return a
  conflict-free `SelectedBatch` for atomic commit, and consume bounded
  look-ahead, execution history, or demand forecasts. That capability must use
  a new component/contract identity and corresponding Runtime Manifest,
  `ExecutionPlan`, and `ExecutionTrace` contract versions. It must not silently
  broaden or reinterpret v1 plans, manifests, traces, or component IDs.

## Evaluation fidelity

- The [PPM provenance bundle](../provenance/ppm/README.md) supplies the exact
  calibration table, fit artifact and standalone fitter in the source checkout
  and sdist. The retained table reproduces the fit byte-for-byte in the recorded
  environment; a complete public Monte Carlo generation artifact is still
  unavailable. The original pinned LightStim script/notebook links provide
  protocol background, not the exact generation source. The YAML's frozen
  `source` receipt retains its original, now-outdated availability text to
  preserve existing profile/report identities; the bundle README resolves its
  artifact IDs/hashes and states current distribution availability. Coefficients
  and calibrated domain are unchanged. Entanglement-boosting assumptions cite
  the publication, DOI, arXiv version and exact location without claiming that
  its implementation is bundled here.

- `ExecutionPlan` uses `arqsim.execution-plan.v9` and binds the canonical
  architecture hash plus independent compiler, runtime-component, latency, and
  resource-protocol receipts. Canonically built plans include exact
  `compilation_hash` lineage. Older plan schema versions are not silently
  accepted. Direct plan JSON records component descriptors, not trusted Python
  callback code; explicit execution of a deferred plan still needs the
  matching runtime component set, and the direct/no-op manifest is rejected
  when a deferred recipe is present. A manifest identifies component
  algorithms, not the source objects captured by a callback. Evaluation checks
  the separate immutable Program/Resource callback contexts held by
  ArqSim's official state-bound realizer against the plan's
  circuit/architecture/compiler/latency hashes, but custom trusted callbacks
  remain the embedding application's responsibility.
- The offline compiler result is strict and typed, and eager MOVE operands plus
  the supported state-bound dispatch recipes no longer round-trip through
  metadata. It also owns the effective magic-state consumption policy and a
  typed `dispatch_deferred` field for every compute route; plan lowering rejects
  policy disagreement and never infers deferred routing from route metrics or
  instruction metadata. Eager magic routes are not recompiled at dispatch, and
  `incremental` compilation is currently defined as at most one magic
  operation per compute batch. A partition's `active_qubits` means operated
  source qubits, while its unit mapping keys own compute residency and may add
  fillers to the required `min(circuit width, compute capacity)` cardinality.
  Non-deferred mappings remain fully resident; deferred mappings may rotate
  residency while preserving the slots of overlap qubits. Static
  memory-complement and combined-capacity gates are enforced.
  This coverage is intentionally narrow: deferred dispatch currently supports
  joint magic routing and state-bound Resource MOVE compilation. Finite
  injection continuations use a separate typed `InjectionRecipe`; neither
  mechanism is a general gadget DSL.
- `FTCircuit` logical layers are conservative dependency antichains, not
  architecture execution waves. The current reference compiler implicitly
  forms one executable unit through capacity partitioning and magic batching,
  then lowers that whole unit to one `EXECUTE_COMPUTE`. The Plan does not yet
  carry a typed compute-work operand or a block-internal action schedule;
  source logical operations are retained through the historical
  `metadata.gates` receipt. This is a trusted built-in boundary, not a general
  external-backend execution contract. A future compiler-facing contract must
  make block formation, operation lineage, and block critical-path timing
  explicit.
- Superconducting aggregate route timing trusts the selected routing backend's
  conflict-group labels. The first-release path counts distinct
  `(source layer, conflict group)` pairs, so disjoint work in one layer shares
  a syndrome-service wave while conflicting groups and separate layers remain
  serial; executable regressions cover all three cases. This remains an
  aggregate compute-block model rather than a typed block-internal action
  schedule, and the conservative PBC parser currently places one operation per
  layer.
- `PlanCostInputs` and `SyndromeCostInput` isolate one lowering run's derived
  cost inputs but deliberately have no codec or hash. The latency profile and
  canonical architecture remain authoritative. Timing-free
  `arqsim.injection-recipe.v2` records do not duplicate those values. The
  lowerer freezes resolved conditional work in
  `ProgramWorkTemplate.duration_s`; this is a self-contained Plan receipt, not
  user configuration or an additional timing profile. Runtime activates the
  selected template, while Trace records realized timing.
- `implementation_recipes` and `continuation_templates` are separate control
  and execution authorities: recipes choose measurement-conditioned branches,
  while templates freeze the possible child work and its resolved cost. They
  are both generated by lowering and are not user timing configuration.
  Their current parallel-list representation repeats source, locus, qubit,
  stage, and correction facts; a later Plan schema should replace it with one
  finite conditional-work graph rather than add more peer fields.
- Current Resource-process metadata retains the derived receipt keys
  `outputs_per_batch` and `consumes_one_logical_bell_pair` solely for frozen
  Report-v1 and named system-case compatibility. Runtime authority is already
  the typed `produces`/`consumes` transition. Removing the duplicate receipts
  therefore belongs to the reviewed v1.5 Plan/report migration, not an
  unversioned first-release cleanup.
- Generic descriptive instruction receipts and observational compiler/backend
  artifacts still exist. They are not binding, dispatch, state, or trace
  authorities: shared operation claims and candidate binding/duration are
  typed, compiler-artifact fields that restate Engine controls are rejected,
  and backend artifacts remain isolated from operation/Engine metadata. The
  built-in four-role runtime realizer remains deliberately narrow and supports
  only joint magic routing plus state-bound Resource MOVE callbacks.
- The Plan-v9 `engines` vocabulary is a compatibility spelling for two
  different kinds of capacity domain: architecture-owned compute/factory/Bell
  service engines and synthesized movement/interface semaphores such as
  `resource_move`. An operation's `engines` map is a contention claim, not its
  execution location; `target_modules`/`target_links` own locus and
  required/completion maps own logical location state. A future schema should
  introduce typed contention-domain kinds instead of merely renaming this
  overloaded field.
- Direct `arqsim.execution-trace.v4` omits completed event spans and hashes
  its transition ledger/projections, including typed Program lineage,
  measurements, and continuation receipts. Event spans are derived, while
  exact wait-cause and buffer-occupancy attribution still requires the full
  plan/ledger-validated diagnostic log; summary mode cannot provide those
  views.
- `architecture/isa.py` and `architecture/state.py` still house internal
  plan/runtime instruction and mutable-state machinery. The ISA contains the
  shared flat claims plus typed movement/recipe contracts. This physical
  location is not another static architecture level; if reorganized later,
  these coupled contracts should move together behind an execution-owned
  package boundary rather than be split solely for directory aesthetics.
- STORE/LOAD, factory, and link costs are profile-based analytical estimates
  rather than results of a full physical mapper. This supports scalable latency
  evaluation but does not produce a physical circuit.
- Superconducting PPM routes depend on logical layout and Pauli support, but the
  current duration proxy is based on syndrome-service rounds rather than exact
  routed edge length.
- The one-shot facade now selects the named canonical reference fidelity
  profile explicitly by default; callers can opt out with
  `fidelity_profile=None`. The bundled profile contains explicit
  literature-derived assumptions and is not a hardware guarantee. Its presence
  as a default does not turn those assumptions into hardware guarantees.
- Fidelity composition uses the reference evaluation's additive log-survival arithmetic and a
  trace-grounded resource-token ledger. Physical consumption remains visible,
  but application settlement starts at terminal Program consumption and walks
  resource-token provenance backwards through supporting Resource events.
  Output error and ready-buffer idle exposure are charged once only inside that
  closure. Thus a Bell pair physically consumed while delivering magic is
  charged only if the delivered magic eventually reaches Program work;
  dead-end background production/delivery affects utilization and waste, not
  application fidelity. Production and delivery intervals are active protocol
  work rather than buffered idle. Resource-aware estimation requires the
  matching typed `ExecutionPlan`; trace buffer names are not parsed to infer
  owner, location, or QEC.
- Node-owned magic-buffer idle calibration comes from the architecture
  Submodule QEC binding. Interconnect-owned Bell storage has no architecture
  QEC binding by design, so its stored-state QEC comes from the selected Bell
  protocol. Bell protocols without a stored-state QEC calibration can still
  contribute output error, but nonzero buffered residence is reported as
  uncovered instead of silently assigned a guessed idle rate.
- The reference numerical fidelity tables still need regeneration and
  reconciliation after the application-relevant resource-accounting split;
  the code-level P0 acceptance tests do not rewrite those published-number
  artifacts.
- Resource output error for canonical fidelity comes from the same resolved
  protocol binding used by runtime production. The estimator rejects a missing
  or stale binding receipt, profile hash, protocol ID, or output-error value;
  arbitrary latency provenance is not treated as an independent fidelity
  source. Architecture-opcode coverage is also explicit: omission is reported
  as unprofiled, while `0.0` records an intentional zero additional channel.
- Runtime injection defaults to `black_box`. Explicit
  `finite_state_injection_v1` fully schedules a neutral-atom compute unit as one
  shared entangle phase and one shared magic-Z measurement phase, then fans out
  per-invocation reactions and outcome-one materialized logical S corrections.
  Typed recipe membership ensures that several T invocations pay shared route,
  CX, and measurement service only once. The logical T parent is a derived span,
  not another scheduled event. Automatic lowering currently supports only this
  T convention; Tdg and other unsupported T-family forms fail closed. Exact
  STAR angle-doubling recipes can execute and replay when supplied directly,
  but no STAR architecture/compiler path lowers them automatically yet. The
  finite lowerer also rejects a compute unit that mixes supported T injections
  with Clifford work, because the current aggregate unit timing cannot justify
  silently sharing one T entangle/measurement schedule with that work. The
  compiler must split `H || T` or `CX || T`; a pure `T || T` batch remains
  supported, and `black_box` mode is unaffected. The
  built-in logical measurement provider is reproducible Bernoulli(1/2), not
  quantum-state simulation. Reaction latency is fixed and reactions claim no contended
  classical engine, so same-measurement reactions may run concurrently;
  conditional corrections are independent capacity-one compute work and may
  serialize. Neither behavior is yet a calibrated multi-T decoder/correction
  throughput model, and materialized logical Clifford correction has no
  frame-update policy.
- The shared neutral-atom phases use
  `reference_additive_gadget_decomposition_v1`: compiler routing plus existing
  gate duration for entangle and resolved syndrome service for measurement.
  This preserves the compiled total and adds no user timing profile, but it is
  a reference decomposition rather than independently calibrated primitive
  timing. Superconducting Profiles 1.2 and 2.2 expose only an overlap aggregate
  such as `max(gate, syndrome)`; explicit finite mode therefore fails closed
  until the compiler provides a causal phase authority. Their default
  `black_box` evaluations remain supported.
- Fidelity intentionally retains the source logical `t` payload as the sole
  aggregate T-operation channel and charges consumed magic through token
  provenance. Shared CX/measurement events are implementation evidence rather
  than duplicate logical-operation channels; measurement/reaction time accrues
  logical-data idle exposure, and a realized logical S is charged separately.
  Independently calibrated primitive gadget fidelity remains future work and
  must replace, not supplement, the aggregate T authority.
- The shared source transition records the complete consumed magic-token and
  slot batch with typed claims, but Plan v9/Trace v4 do not yet freeze an
  invocation-to-specific-token/slot allocation receipt. Homogeneous resource
  states make the batch ledger sufficient for current overall fidelity. A
  future per-invocation attribution view needs an explicit typed mapping; UIs
  must not infer one by zipping recipe members with token or slot order.
- A fixed seed makes the implemented stochastic choices reproducible, but the
  model does not claim cycle-accurate hardware behavior.

## Release boundaries

The wheel includes the Python package, runtime YAML data, and CLI. The sdist
also carries the maintained tests, docs, and examples needed for a source-build
audit. Private research data and unpublished evaluation workflows are not
release assets. Any redistributed third-party workload must carry explicit
source and license provenance.
