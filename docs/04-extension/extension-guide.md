# Extension Guide

Level 4 · Role: extension-author guide · Status: current for ArqSim 0.2.0

ArqSim extensions should enter through an owned, versioned contract instead
of branching on IDs or modifying evaluator internals. Every extension must be
deterministic for a fixed request and must leave a serializable receipt.

## Add an Architecture Profile

1. Add one folder under `arqsim/architecture/gallery/`, named from the
   architecture's reference name using lowercase Python-safe spelling. Keep `plus`
   when `+` separates Nodes. Put the strict Profile-v3 topology in that
   bundle's `profile.yaml`.
2. Put the architecture's concrete `SizingPolicy` in `sizing.py` and its
   `LogicalLayoutPolicy` in `layout.py`. Export `PROFILE`,
   `make_sizing_policy(config)`, and `make_layout_policy()` from the bundle.
   Do not add a Profile-specific resolver.
3. Define QEC selection and resource-protocol selection outside the Profile and
   project them to exact `SubmoduleKey` mappings at construction time.
4. If the architecture should be selectable through
   `EvaluationConfig(profile_id=...)`, add its `GalleryEntry` to the explicit
   catalog in `arqsim/architecture/gallery/catalog.py`. The value selected by
   `profile_id` is the bundled Profile's own opaque `PROFILE.id`; it need not be
   numeric. A caller that constructs the Profile, policies, bindings, and
   Specification directly through the generic architecture API does not need
   catalog registration.
5. Exercise Profile parsing/round-trip, exact sizing/layout coverage, resolved
   slot ownership, engine capacity, deterministic hashes, and one end-to-end
   report.

A Profile bundle establishes static topology, sizing, and logical layout. That
alone does not make the architecture executable. End-to-end support must also
provide or validate the applicable compiler capabilities, operation timing and
fidelity coverage, selected resource-protocol profiles, and any lowering or
runtime behavior required by its operations. Do not advertise a catalog entry
through the one-shot evaluation path until those boundaries fail closed and an
end-to-end test covers them.

Do not infer behavior from an ID such as `compute0` or `bell_link`. If the
current controlled vocabulary cannot describe the component, extend and
validate the vocabulary explicitly.

Profile v3 uses the same unsized owner hierarchy that the resolved
Specification materializes: Nodes and Interconnects own Modules and
owner-local connections, and Interconnect attachments use absolute
`Node/Module/Submodule` references. It contains no compatibility roles,
`InterconnectAccess`, capacities, QEC bindings, or layout. A multi-Node Profile
must express that shape directly; the resolver must never infer an expansion.

Author a `LocalConnection` only between Submodules in two distinct Modules
owned by the same Node or Interconnect. For a directed connection, list its
endpoints as `(source, destination)`; endpoint order is immaterial for a
bidirectional connection. Do not encode same-Module plumbing, routing graphs,
or logical couplers as architecture connections. A Module encapsulates those
internal behaviors. If connectivity must be exposed as an independent static
capacity, contention, routing, or scheduling boundary, model the resources as
separate Modules and connect them explicitly.

The logical compiler derives operation-specific paths and interaction intent.
A future code-aware LightStim lowering may instantiate a
`LogicalCouplerProtocol` from that intent and construct its QEC-lattice
`LogicalCouplerPatch` geometry. The direct bridge is not implemented yet, and
extensions must not pre-empt it by storing generated coupler geometry in a
Profile or Specification.

Architecture construction has one supported path: keep each concrete Profile,
sizing policy, and logical-layout policy together in its gallery bundle, then
join their typed results through the generic resolver. Do not introduce a
parallel specification-template catalog, a combined layout/sizing adapter, a
Profile-specific resolved container, or a Profile-specific resolver.

## Add or change a sizing policy

Implement `SizingPolicy.size(...)` and return a complete `SizingResult` keyed
by exact `SubmoduleKey(owner_id, module_id, submodule_id)` values. The policy
may consume:

- an `ArchitectureProfile`;
- Program-owned `CircuitStatistics`;
- full selected resource-protocol catalog profiles when their intrinsic facts,
  such as outputs per batch, affect capacity;
- optional reference statistics and exact per-Submodule overrides.

The policy must cover every Profile Submodule exactly once. Slot-owning
non-engine Submodules may have zero capacity; resource-engine copy capacity
must remain positive. A concrete family policy may impose stricter relational
constraints. It must not receive an `FTCircuit`, choose QEC, place slots, copy
policy/provenance into the result, or inspect component-ID spelling.
`NA-CF` and `SC-CF` each expose their own concrete sizing policy while sharing
only the proven compute/factory mechanics. `MemoryComputeSizingPolicy`
implements the one-pool partition owned by the `NA-MCF` bundle. The
multi-Node policies are:

- `RemoteMagicSizingPolicy` for Profile 1.3;
- `HybridMemoryComputeSizingPolicy` for Profile 2.2; and
- `RemoteMagicMemoryComputeSizingPolicy` for Profile 2.3.

They size Interconnect-owned Bell Engine and Bell Storage Submodules directly.
Bell-engine capacity is an aggregate protocol copy count; Bell Storage owns one
shared logical pair capacity rather than two endpoint copies. A caller that
already has every concrete capacity can construct
`SizingResult` directly; exact overrides support per-Submodule sweeps without
another policy class.

Import a concrete implementation from its reference bundle, for example
`arqsim.architecture.gallery.na_mcf.sizing` or
`arqsim.architecture.gallery.na_mcf.layout`. Do not create another
horizontal sizing/layout catalog.

For a quantile experiment, create one
`gallery.QuantileSizingConfig` and pass it to each selected gallery entry's
`make_sizing_policy()`. The shared value is experiment input, not a universal
sizing algorithm: every bundle decides which quantiles apply to its own
resource relations. `QuantileSizingConfig.uniform(q)` deliberately applies one
probability to compute, magic-state, and Store/Load demand.

If protocol facts affect sizing, pass the same catalog profile object to
sizing and later to `resolve_architecture()`. Do not copy batch size or profile
facts into a second policy-owned dictionary. `SizingResult` records only the
profile hashes that actually participated in capacity derivation, and the
resolver rejects a different selected profile.

Treat `CircuitStatistics.magic_states_per_layer` as the narrow baseline
convention it currently is: one global magic-demand unit for each `t`/`tdg` or
Pauli-rotation operation. It is sufficient for the six bundled baseline
policies, but it is not the extension point for richer gadget demand. A STAR
`theta`/`2theta`/`4theta` chain, or any other architecture/compiler-dependent
resource expansion, must provide a separate typed demand analysis owned by the
architecture/compiler boundary. Do not encode those multiplicities by changing
the representation label or silently broadening this generic Program statistic.

## Add or change a logical-layout policy

Implement `LogicalLayoutPolicy.place(profile, sizing, request=...)` and return
a `LogicalLayoutResult` that covers every slot-owning Submodule.
Resource engines are intentionally absent because they have no logical slots
or geometry. A layout policy materializes stable
slot IDs and, only where operationally meaningful, owner-local logical
coordinates, origins, and grid envelopes.

Bundled policies use opaque Submodule-local IDs (`slot_0`, `slot_1`, ...).
Resource semantics belong to the owning path rather than an ID prefix. Legacy
names such as `D0`, `SL0`, `M0`, and `MO0` are reconstructed only by
the report-v1 plain-JSON renderer.

Sizing, QEC selection, resource-protocol selection, runtime state, and physical
placement are outside this interface. All numeric placement choices belong to
the concrete policy's typed configuration; the generic resolver contains no
grid shape, right-edge offset, or factory anchor. Keep concrete bundled
recipes in the architecture's gallery bundle; share only mechanical helpers.

`NeutralAtomComputeFactoryLayoutPolicy` implements the Profile 1.1 recipe and
applies an optional `LogicalLayoutRequest`. Its `magic_output_origin` is a
required constructor argument: placement policy must be explicit about this
compiler-active coordinate. The `(100, 40)` value used by the historical
Profile 1.1 baseline is a compatibility preset, not a canonical physical fact or a
general layout default.

`SuperconductingCheckerboardLayoutPolicy` implements Profile 1.2. It places
occupied patches directly on the checkerboard logical canvas, two canvas
units apart, while leaving the intervening sites implicit for routing. A
requested grid counts patch rows and columns; the materialized result expands
it into the complete canvas envelope, including the unoccupied sites. Exact
slot requests are preserved without scaling or snapping. The policy
never emits routing nodes, edges, interfaces, selected paths, or
logical-coupler objects. The SC compiler derives those artifacts from the
resolved occupied patch locations. The frozen report-v1 renderer alone retains
its historical default factory-output portal alias; this is documented as a
compatibility limitation and is not a canonical layout rule.

`NeutralAtomMemoryComputeLayoutPolicy` implements the local memory/compute
recipe used by Profile 2.1. Its bundled preset uses `compute_origin=(0, 0)`,
west/east offsets of two logical sites, and factory-output origin `(200, 40)`.
The BB memory owns stable slot identities but no operational coordinates; its
default origin is `None`. A nonempty memory accepts an origin-only request and
rejects grid or exact-slot geometry. An empty memory has no slots, origin, or
grid and rejects every geometry request, including an origin.

The compact compute grid owns the spatial envelope. The Store/Load buffer is
placed west of that envelope and the magic-state input buffer east of it. A
requested compute grid reserves its full envelope, including empty cells;
exact compute coordinates use their full bounding extent. Explicit
Store/Load, magic-input, and factory-output requests override derived/default
geometry. The policy emits no routing graph, interfaces, couplers, or physical
placement.

The bundled multi-Node layout recipes are
`HybridRemoteMagicLayoutPolicy` for Profile 1.3,
`HybridMemoryComputeLayoutPolicy` for Profile 2.2, and
`RemoteMagicMemoryComputeLayoutPolicy` for Profile 2.3. They preserve separate
logical canvases for each Node and Interconnect. A shared Bell Storage buffer
has stable identity-only slots by default. An explicit request may give those
slots coordinates on the Interconnect's own canvas; it must not create
endpoint-local Bell slots, routes, or physical placement.

The request is a flat
`Mapping[SubmoduleKey, SubmoduleLayoutRequest]`; it does not introduce a
Node-only wrapper or encode targets as `module/submodule` strings. The policy
returns a `LogicalLayoutResult` whose values are complete
`SubmoduleLayoutResult` records. Request types are partial user input; result
types are materialized policy output.

When changing a named/public placement preset, add a new preset version instead
of silently changing its meaning. The resulting effective geometry contributes
to `architecture_hash`. Public logical-layout overrides contain only
`logical_origin`, `grid`, or `slots`; routing fabric is derived privately.

## Resolve an Architecture Specification

`resolve_architecture()` is one generic, deliberately mechanical join:

```text
FTCircuit -> CircuitStatistics -> SizingPolicy -> SizingResult

ArchitectureProfile + SizingResult
  -> LogicalLayoutPolicy -> LogicalLayoutResult

ArchitectureProfile
  + SizingResult
  + LogicalLayoutResult
  + Mapping[SubmoduleKey, QECBinding]
  + Mapping[SubmoduleKey, QECResourceProtocolProfile]
  -> resolve_architecture()
  -> ArchitectureSpecification
```

Sizing must cover every Profile Submodule; layout must cover every non-engine
Submodule. QEC bindings are optional exact-target annotations, while selected
protocol profiles exactly cover the resource engines; unknown targets fail.
The resolver stores only the effective QEC binding and a
`QECResourceProtocolRef(id, profile_hash)`, not the policy, circuit statistics,
catalog object, or construction provenance.

New architecture families should be expressed through Profile data and policy
implementations. Do not subclass the resolver, create
`resolve_architecture_<profile>()`, or branch on Profile IDs. If a Profile
cannot be represented by the current Profile schema, extend that authoring
schema first and keep the resolver traversal generic.

## Replace the logical compiler pipeline

The supported replacement boundary is `CompilerPipeline.compile()`, which
returns one typed `LogicalCompilationResult` for plan lowering. Built-in mapping and
routing selection uses immutable `BackendSpec` records; the low-level backend
functions and registries are internal implementation details, not independently
injectable public pass interfaces.

`compile()` receives the complete `FTCircuit`, but the v1 output contract remains
source-layer-indexed and layer-preserving. It cannot express persistent
cross-layer route continuity, compiler-selected SE/checkpoint placement, or a
general scheduled action DAG. Replace the v1 pipeline only when the result can
faithfully project into those semantics; broader compilers require a future
compiler-facing IR and result version.

Do not infer compiler compatibility from `FTCircuit.representation`. That value
identifies the parser/IR dialect, not a proved gate set. A compiler backend must
validate the logical operations and features it implements. The current
compatibility boundary is only as broad as those backend capability checks; a
new dialect must extend or add the responsible backend rather than teach the
shared IR to make architecture-specific promises.

For a workload wider than compute capacity, the default pipeline uses its fixed
preserve-resident first-fit policy to allocate each residency partition. The
`mapping` backend controls the initial placement only when the full workload
fits in compute. A noncanonical mapping override in capacity-deferred mode is
rejected instead of being accepted as a silent no-op.

The pipeline result is a strict `arqsim.logical-compilation-result.v1`
boundary, not an open mapping. A custom pipeline must populate the circuit,
architecture, and latency-binding source hashes, effective compiler spec,
effective magic-state consumption policy, typed compute units, mapping state,
and deferred-capacity flag. Every `CompiledRouteResult` must explicitly set
`dispatch_deferred`; a deferred route has no eager route steps, and route
metrics may not duplicate either old or new dispatch-control keys. The result
must round-trip through the codec and cover every source operation exactly
once; plan lowering rejects a stale, incomplete, duplicated, wrongly sourced,
policy-inconsistent, or architecture-capacity-incompatible result. It creates
a magic-route recipe from the typed route state, never from backend metrics,
does not copy that state into instruction metadata, and records the result's
`compilation_hash` in plan provenance. The `incremental` policy requires at
most one magic operation per compute batch. Treat
`ComputePartition.active_qubits` as the operated source set, not the residency
set. A `CompiledComputeUnit.mapping` must use canonical compute data-slot IDs;
its keys own residency, include all active qubits, number exactly
`min(circuit logical width, compute capacity)`, and leave a complement that
fits logical memory. The whole circuit must fit their combined capacity,
including when a result has no compute units. A
non-deferred result uses its full initial mapping in every unit. In deferred
mode an overlap qubit cannot change slots between adjacent units, and all
batches of one partition use the same mapping. The default compiler may add
deterministically selected filler residents. Every serialized route step must
contain the complete canonical field set, including `metadata`. Deferred
residency changes must also be balanced: chunk equal outgoing/incoming sets by
the canonical Store/Load endpoint capacity, leave STORE location-preserving,
and apply the outgoing-to-memory plus incoming-to-endpoint locations in one
LOAD completion. For remote memory, teleport outgoing before that exchange
and incoming afterward, so Bell-pair claims are never bidirectional in one
wave. Do not split the atomic exchange into STORE and LOAD location changes;
that would transiently exceed canonical memory capacity. Preserve one STORE
and one LOAD syndrome service in every exchange round and, remotely, one
outbound and one inbound TELEPORT on the same predecessor chain. The atomic
location update must not change the service multiset or Program critical-path
duration.

Use `arqsim.evaluation.lower_compilation_result()` to validate and lower an
already serialized or externally produced, trusted, v1-compatible result. Use
`arqsim.evaluation.compile_and_lower()` when the framework should run a
`CompilerPipeline`; it returns both canonical artifacts rather than discarding
the `LogicalCompilationResult`.

For plan-facing movement, use `MoveOperands` when source and destination slots
are already known. Use one of the existing tagged deferred-dispatch recipes
only when the tentative runtime binding genuinely owns an endpoint. Do not add
an untagged metadata request or duplicate a typed operand/recipe in metadata.
A Program MOVE requires typed operands whose endpoint maps are injective and
nested under its source/completion state locations. At the plan boundary its
`entity_kind` must be `logical_qubit`, and its moved entities must equal both
location-claim sets exactly. Resource-move `entity_kind` must match both
forwarded buffers' `token_kind`; every buffer consumed by a magic-route recipe
must have `token_kind="magic_state"`. Do not give the runtime realizer's
Program callback a special magic-buffer ID: discover all consumed slots from
the typed operation claims.
Reject each plane's legacy top-level control keys, and reject
`deferred_until_dispatch` or `dispatch_deferred` inside `route_metrics`. A plan
with either deferred recipe must bind a
state-bound-realizer manifest, not the direct/no-op manifest. A planned
component manifest identifies the declared algorithm and effective
configuration; its hash does not identify Python source code. Both official
built-in callback contexts must also match the plan authorities.
A new recipe kind changes the strict plan/runtime contract and requires parser,
plane/opcode validation, explicit-execution, and compatibility-renderer tests.

When extending the v9 plan codec, preserve its exact-JSON gate. `from_dict()`
must reject tuples, mapping proxies, non-string object keys, non-finite
numbers, or other Python-only conveniences rather than normalizing them before
hash validation.

Expose backend selection through immutable `BackendSpec`/
`LogicalCompilerSpec` records and include the backend name, normalized
parameters, and compiler hash in the report. Validate modality and capability
before compilation. A backend-generated route, terminal, ancilla region, or
LogicalCoupler is a compiler artifact—not a field that Profile authors must
provide.

New physical or LightStim lowering should consume the resolved logical
architecture and produce a separate hashed receipt. It must never silently
reinterpret a BB/QLDPC mode coordinate or use a heterogeneous logical distance
as calibrated physical time.

## Replace a runtime component (advanced)

ArqSim exposes four source-level runtime protocols for trusted integrations.
Their complete contracts and fixed-kernel boundary are defined in the
[Level 3 runtime guide](../03-runtime/evaluation-engine.md#runtime-components-and-the-fixed-kernel).

| Protocol | Extension may decide | V1 boundary it may not cross |
|---|---|---|
| `RuntimeRealizer` | Propose a tentative binding and a typed deferred continuation | Mutate live state, rewrite operation claims, or invent/suppress measurement semantics |
| `RuntimeScheduler` | Order the exact Program or Resource IDs presented on one frontier | Filter work, choose start times, arbitrate across planes, or atomically select multiple candidates |
| `ExecutionTimingBackend` | Finalize one realized candidate's duration and attach an observational backend artifact | Change binding, dependencies, state transitions, or operation semantics |
| `LogicalMeasurementProvider` | Return the requested logical bits in exact register order | Add registers, model decoder latency, schedule classical work, or handle general source-circuit measurements in v1 |

A custom implementation must carry a `RuntimeComponentDescriptor` for its
declared role and be supplied in a live `RuntimeComponentSet` to the lower-level
`evaluate()` API. The `ExecutionPlan` separately stores a
`RuntimeComponentManifest`; evaluation requires the live set's manifest hash to
match that planned receipt exactly. The hash binds declared identity and
effective configuration, not Python source code or sandbox isolation. The
one-shot `run_evaluation()` facade reserves its `runtime_components` field for
the planned built-in receipt and does not accept arbitrary live implementations.

These protocols are narrow seams inside a fixed Event Engine, not a general
runtime plugin registry. A change that needs new scheduled work, state
authority, cross-plane arbitration, or a wider measurement contract requires a
new versioned runtime/Plan contract rather than an undocumented callback.

## Add an operation or resource model (framework maintainer)

Adding an ISA operation, resource process, deferred recipe, or state transition
is a framework-maintainer source extension, not an ordinary plugin seam. It can
change the strict ExecutionPlan, runtime component, Trace, or Report contracts;
make the affected schema-version changes explicit and keep older documents
fail-closed unless a separately tested compatibility reader exists.

Architecture ISA records describe resource requirements and state transitions;
operation profiles provide timing/fidelity assumptions. Keep the two separate.
Factories and Bell engines are streaming resource processes with aggregate
copy capacity. If a new resource is bindable at runtime, give its buffer stable
slot identities and enforce capacity through `ArchitectureState`.

An implementation is complete only when it has:

- strict schema and unknown-field rejection;
- stable serialization and semantic hashing;
- failure-path tests as well as a success case;
- transaction/invariant coverage in the execution engine;
- report validation/replay coverage;
- package-data declarations if it adds YAML or other runtime assets.

## Dependency boundary

The public one-shot path is `FTCircuit + EvaluationConfig -> run_evaluation() ->
EvaluationReport`. Frontends and integrations should depend on that facade or
on versioned serialized documents. They must not import mutable runtime state,
private DAG builders, construction adapters, or compiler routing internals.
