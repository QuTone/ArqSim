# Known Limitations and Naming

The project/framework name is **ArqSim**. The installable Python distribution,
import package, and CLI remain **`heteqsys`** for compatibility. Serialized
schema names use the `arqsim.*` namespace.

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
  Its legacy ArqSim 1.1 preset uses `(100, 40)` only to preserve baseline
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

## Evaluation fidelity

- `ExecutionPlan` uses `arqsim.execution-plan.v6` and binds the canonical
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
- `PlanCostInputs` and `SyndromeCostInput` isolate one lowering run's derived
  cost inputs but deliberately have no codec or hash. The latency profile and
  canonical architecture remain authoritative.
- Generic descriptive instruction receipts and observational compiler/backend
  artifacts still exist. They are not binding, dispatch, state, or trace
  authorities: shared operation claims and candidate binding/duration are
  typed, compiler-artifact fields that restate Engine controls are rejected,
  and backend artifacts remain isolated from operation/Engine metadata. The
  built-in four-role runtime realizer remains deliberately narrow and supports
  only joint magic routing plus state-bound Resource MOVE callbacks.
- Direct `arqsim.execution-trace.v3` omits completed event spans and hashes
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
- Fidelity estimation is opt-in. The bundled canonical reference profile
  contains explicit literature-derived assumptions and is not a hardware
  guarantee.
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
  `finite_state_injection_v1` materializes T's measurement, per-attempt
  classical reaction, and outcome-one materialized logical S correction, and
  delays static successors until every invocation closes. Automatic lowering currently
  supports only this T convention; Tdg and other unsupported T-family forms
  fail closed. Exact STAR angle-doubling recipes can already execute and replay,
  but no STAR architecture/compiler path lowers them automatically yet. The
  built-in outcome model is reproducible Bernoulli(1/2), not quantum-state
  simulation, and materialized logical Clifford correction has no frame-update
  policy yet.
- A fixed seed makes the implemented stochastic choices reproducible, but the
  model does not claim cycle-accurate hardware behavior.

## Release boundaries

The wheel includes the Python package, runtime YAML data, and CLI. The sdist
also carries the maintained tests, docs, and examples needed for a source-build
audit. Private research data and unpublished evaluation workflows are not
release assets. Any redistributed third-party workload must carry explicit
source and license provenance.
