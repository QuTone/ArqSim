# Architecture Specification

ArqSim has exactly three architecture levels:

```text
ArchitectureProfile -> ArchitectureSpecification -> ArchitectureState(t)
```

- `ArchitectureProfile` is reusable, unsized authoring intent.
- `ArchitectureSpecification` is one immutable, fully materialized static
  logical/evaluation architecture.
- `ArchitectureState(t)` is its mutable runtime instance.

Resolver inputs and build records are not extra architecture levels.

## Construction boundary

```text
FTCircuit -> CircuitStatistics -> SizingPolicy -> SizingResult

ArchitectureProfile + SizingResult
  -> LogicalLayoutPolicy -> LogicalLayoutResult

ArchitectureProfile
  + SizingResult
  + LogicalLayoutResult
  + exact QEC bindings
  + selected resource-protocol profiles
  -> resolve_architecture()
  -> ArchitectureSpecification
```

`CircuitStatistics` belongs to `FTCircuit` and the `heteqsys.program` package.
It contains architecture-independent circuit facts such as logical width,
active width, operation operands, and magic-state demand. A sizing policy may
consume those facts and choose capacities. It returns a complete
`SizingResult`, keyed by exact Profile Submodules. It does not choose QEC or
placement, and the generic resolver never receives the circuit or its
statistics.

The current sizing-policy boundary is summarized below. Profile 2.1 now uses an
explicit `MemoryComputeSizingPolicy`; the review intentionally introduced no
public rule DSL or sizing planner.

That policy matches one unambiguous memory/compute/factory family by semantic
type and payload. Compute and memory form an atomic partition of circuit width.
A pinned compute or memory capacity recomputes its complement, while pinning
both requires the exact total. Store/Load demand is derived after that
partition. Optional reference statistics affect only magic-state sizing, and
factory-copy inference reads the selected resource protocol's output
multiplicity. Memory may validly resolve to zero when compute owns the complete
circuit width; resource engines remain strictly positive.

The three multi-Node Profiles use explicit family policies over the same
construction seam:

- Profile 1.3 uses `RemoteMagicSizingPolicy` for neutral-atom compute supplied
  by a remote superconducting factory.
- Profile 2.2 uses `HybridMemoryComputeSizingPolicy` for remote neutral-atom
  memory and superconducting compute with local magic production.
- Profile 2.3 uses `RemoteMagicMemoryComputeSizingPolicy` for a local
  neutral-atom memory/compute partition supplied by a remote superconducting
  factory.

Each policy matches controlled component semantics and required topology, not
component IDs or declaration order. Bell-engine capacity is a protocol copy
count. Bell-buffer capacity is one shared transfer/storage wave owned by the
Interconnect; it is never duplicated into endpoint capacities in the canonical
Specification. These policies remain separate concrete recipes even where
their arithmetic overlaps. Only mechanical validation and geometry helpers are
shared; there is no general sizing-rule DSL or per-Profile resolver.

A logical-layout policy consumes the Profile and completed sizing result. It
materializes stable slot identities and optional logical geometry for every
non-engine Submodule as a `LogicalLayoutResult`. Resource engines are absent
because they own no logical slots. An optional `LogicalLayoutRequest`, keyed
directly by `SubmoduleKey`, is applied at this policy boundary through
`LogicalLayoutPolicy.place(..., request=...)`, not inside the resolver. Each
partial `SubmoduleLayoutRequest` becomes a complete
`SubmoduleLayoutResult`; request and result records live together in
`architecture/logical_layout.py`. The shared interface and mechanics live in
`architecture/logical_layout_policy.py`. Each concrete bundled recipe lives
beside its Profile and sizing policy in the reference architecture's
`architecture/gallery/` bundle; the runtime-only gallery catalog owns their
association.

Bundled policies use uniform owner-local identities (`slot_0`, `slot_1`, ...).
The full owner/Module/Submodule path carries resource meaning. Historical
`D`/`SL`/`M`/`MO` prefixes are generated only by the report-v1 plain-JSON
renderer; the compiler consumes canonical absolute slot identities.

`NeutralAtomComputeFactoryLayoutPolicy` requires an explicit
`magic_output_origin`.
The `(100, 40)` coordinate selected by the legacy ArqSim 1.1 baseline is a
versioned compatibility assumption, not a canonical physical coordinate or a
default implied by the architecture model.

`NeutralAtomMemoryComputeLayoutPolicy` gives Profile 2.1 an identity-only BB
memory, a compact compute grid, a Store/Load bank west of the effective
compute envelope, and a magic-state input bank east of it. Its bundled preset
uses `(200, 40)` as the explicit factory-output origin. A zero-capacity memory
has no slots, origin, or grid. A nonempty memory accepts only an origin-only
request; it never accepts per-mode grid or exact-coordinate geometry. The
policy emits no routing graph, interfaces, couplers, or physical placement.

The corresponding multi-Node layout recipes are
`HybridRemoteMagicLayoutPolicy` for Profile 1.3,
`HybridMemoryComputeLayoutPolicy` for Profile 2.2, and
`RemoteMagicMemoryComputeLayoutPolicy` for Profile 2.3. Each keeps Node and
Interconnect canvases independent. The shared Bell Storage slots are
identity-only by default. A caller may explicitly place them on the
Interconnect's own logical canvas through `LogicalLayoutRequest`; that request
does not manufacture endpoint halves, Node-local Bell buffers, routes, or
physical geometry.

QEC bindings and selected resource-protocol profiles are independent exact
Submodule mappings. A selected protocol's intrinsic catalog facts may also be
used by sizing—for example, outputs per batch—but the same catalog profile is
passed through rather than copying those facts into a second data source.
`SizingResult` retains the hash of each protocol that actually influenced a
capacity; the resolver verifies those hashes before storing protocol refs.

`resolve_architecture()` only validates and joins these already-effective
inputs to the unsized Profile hierarchy. It contains no sizing/layout
algorithm, no Profile-ID dispatch, and no architecture-specific constants. The
policies, policy results, statistics, original layout request, QEC catalog, and
protocol catalog are transient construction inputs; they are not copied into
the resulting Specification.

The Specification retains only effective static facts: Nodes, Modules,
Submodules, capacities, owned slots, effective logical geometry, QEC bindings,
resource-protocol references, owner-local connections, Interconnects, and their
Node endpoint references. It does not contain a circuit, profile, sizing policy,
resolution provenance, footprint result, timing model, fidelity result,
compiler route, physical calibration, or physical placement.

This means a Specification may be sized from one circuit but is not bound to
that circuit. Any circuit that fits its declared static capacity may use it.

## Canonical static graph

The canonical records are deliberately small:

| Record | Meaning |
| --- | --- |
| `LogicalSlot` | Stable runtime-bindable slot identity and optional local logical coordinate |
| `QECBinding` | Effective code name and code parameters for one Submodule |
| `QECResourceProtocolRef` | Effective QEC resource-protocol identity and catalog-profile hash |
| `Submodule` | Typed capacity owner: region, buffer, or opaque engine |
| `Module` | Ownership group of Submodules |
| `LocalConnection` | Directed or bidirectional static transfer path between Submodules in two distinct Modules owned by the same Node or Interconnect |
| `Node` | Logical capability/compiler family (`modality`), Modules, and local connections |
| `Interconnect` | Node-peer shared network/resource domain with absolute Node endpoint references, distributed Modules, and optional owner-local connections |
| `ArchitectureSpecification` | Immutable collection of Nodes and Interconnects |

`LocalConnection` stores inter-Module topology, direction, and endpoints. Its
payload is derived from its endpoint Submodules, which must agree; it is not
serialized a second time. For a directed connection, the endpoint tuple is
ordered `(source, destination)`. A bidirectional connection has no source or
destination and its endpoints are canonically sorted.

The serialized root is:

```text
schema_version
nodes
interconnects
architecture_hash
```

`architecture_hash` is the semantic hash of the schema version, `nodes`, and
`interconnects`. Source Profile identity, circuit statistics, policy identity,
and other construction provenance do not affect it unless they change the
static graph itself.

## Resource ownership and connectivity

A `Node` and an `Interconnect` are peer resource owners. Each may own Modules,
Submodules, and optional `LocalConnection` records. Here, *local* means local to
the record's owner, not necessarily local to one physical chip. A local
connection therefore uses relative `Module/Submodule` endpoints, never crosses
an owner boundary, and always connects two distinct Modules. The endpoint is a
Submodule reference because it identifies the resource-facing interface of its
Module. For example, a Node-owned bus may connect an Interconnect endpoint
Submodule to a Submodule exposed by another compute or memory Module, while an
Interconnect-owned local connection may connect a Bell Engine Module to a Bell
Storage Module.

A Module is also an architecture encapsulation boundary. State movement,
plumbing, and operation-specific interactions among Submodules within one
Module are not separate architecture connections. Their omission does not mean
that no internal movement occurs; it means that the Module owns how the
operation is realized. If a path must be exposed as an independent static
capacity, routing, contention, or scheduling boundary, its endpoints should be
modeled as separate Modules and connected by a `LocalConnection`.

An `Interconnect` is the abstract distributed network/resource domain that
enables state transfer among its endpoint Nodes. It is not a transfer event, a
single graph edge, one physical line, or one Bell-pair state.
Its `endpoints` are absolute `Node/Module/Submodule` references that resolve to
existing Submodules and collectively span exactly two distinct Nodes. These
references define where the distributed domain is attached; they are attachment
declarations, not connections, mirrored capacity, or Bell-state storage. They
impose no `bell_pair` payload requirement. An endpoint may itself be the direct
consumer or access Submodule. Any additional explicit path from it to a
Submodule in another Node-owned Module, when present, is expressed by a
`LocalConnection` owned by that Node.

The Interconnect owns ordinary distributed Modules such as Bell Engine and
Bell Storage. One such Module may be spatially realized across several endpoint
Nodes while remaining one logical architecture resource. An Interconnect may
own multiple generation/storage Modules, and its owner-local connections may
describe their inter-Module static topology. No `BellLink`,
`InterconnectConnection`, or `InterconnectAccess` record is needed: the
Interconnect itself replaces the old overloaded Bell-link concept.

A Bell-pair state is runtime data held in a Bell Storage slot, not a static
Module or connection. One pair is one shared logical-capacity item even though
it has two endpoint-local logical-qubit halves. Runtime capacity therefore
counts the pair once; physical-footprint accounting counts both halves. That
accounting is an evaluation result and is not stored in the canonical
Specification.

The architecture graph stops at these static resource and transfer boundaries.
For a concrete operation, the logical compiler derives its routing path and
interaction intent, including any need for a logical coupler. A code-aware
LightStim lowering can then combine that intent with the selected QEC code and
logical layout, instantiate a `LogicalCouplerProtocol`, and construct its
QEC-lattice `LogicalCouplerPatch` geometry. That generated geometry is a
lowering artifact, not a Profile or Specification record. The direct
ArqSim-to-LightStim bridge for this lowering is not implemented yet.

## Profile authoring

Profile IDs and component IDs are opaque stable reference keys. Their spelling
does not determine behavior. The Profile expresses unsized ownership and
topology; the resolver supplies concrete capacity, bindings, and effective
placement.

Profile v3 mirrors the same ownership shape while omitting resolved facts:
Nodes and Interconnects own Modules, Submodules, and optional owner-local
connections, and Interconnect attachments are absolute
`Node/Module/Submodule` references. It does not contain capacity, QEC binding,
slot identity, or layout. The canonical parser accepts only this strict v3
shape; v1/v2 documents and their role/access projections belong solely to the
one-way compatibility boundary.

## Slots, copies, and geometry

A persistent region or buffer owns exactly its declared number of logical
slots. An engine exposes aggregate copy capacity and does not expose internal
protocol patches as runtime-bindable slots.

Slot identity and slot geometry are separate. Surface-code and neutral-atom
compute/buffer slots may have compiler-active logical coordinates. A BB/QLDPC
block may encode several logical modes, so those modes retain slot identity
without invented per-mode coordinates. A future physical mapper records
`slot -> block/mode` placement. Visualization may derive display positions,
but those positions are not architecture semantics.

Spatial coordinates are hierarchical within each resource owner:

```text
Owner-global logical coordinate = Submodule logical_origin + slot local coordinate
```

Node and Interconnect canvases are independent. Same-owner slot collisions and
reserved-grid collisions are invalid; equal coordinates on different owners
are valid.

## Optional logical layout

An optional logical-layout request is an input to resolution. It may choose a
Submodule origin, a complete grid envelope, or exact local slot coordinates.
The request itself is not retained. Only the resulting `logical_origin`,
`grid_shape`, slots, and slot coordinates appear in the Specification.

These are logical/evaluation coordinates, not LightStim patch offsets or
physical-qubit sites. Users do not author routing nodes, routing edges,
ancilla patches, couplers, or a physical circuit in an Architecture Profile.
Those operation-specific artifacts are derived by the compiler and a future
code-aware lowering rather than persisted as architecture facts.

Superconducting occupied-patch coordinates therefore live directly on the
checkerboard logical canvas. Adjacent occupied patches differ by two canvas
units; the intervening sites are implicit and available to routing. A
`grid_shape` is the full reserved canvas envelope, including those sites.
Routing nodes/edges, selected paths, interfaces, and logical couplers are
derived during lowering rather than stored in the resolved
`ArchitectureSpecification`.

## Operational data and evaluation

The canonical architecture graph may identify which QEC code or resource
protocol a Submodule uses, but it does not own operational estimates. Timing,
fidelity, and footprint profiles are evaluation inputs. Runtime bindings,
token occupancy, current qubit locations, engine usage, and in-flight events
belong to `ArchitectureState` and execution records. Derived metrics belong to
`EvaluationReport`.

`Node.modality` selects the logical capability and compiler family; it is not a
physical microarchitecture record. Calibrated site spacing, transfer overhead,
and reference movement distance/time belong to the versioned neutral-atom
movement profile inside `OperationLatencyProfile`. The logical compiler combines
those calibration inputs with the Specification's logical coordinates and its
versioned movement formula to estimate instruction duration.

This separation permits scalable analytical latency estimation without
claiming a physical lowering. A later mapper or LightStim backend may emit a
physical circuit and its own versioned placement/lowering receipt.

## Downstream and report boundaries

The canonical `ArchitectureSpecification` is the direct input to
the compiler, footprint estimator, execution-plan lowerer, evaluator, and
public API. The old combined `ArchitectureLayoutPolicy`/
`QuantileLayoutPolicy`, compatibility package, hierarchy/spec containers,
`ArchitectureLayoutPlan`, `ArchitectureSpec`, `QECConfiguration`,
`ResolvedFTSystemSpec`, shared legacy adapter, private templates, and
template-only wrappers have been deleted. No bundled Profile or downstream
consumer retains a second construction or resolved-architecture path.

Native Report v2 embeds the canonical Specification directly under
`resolved_inputs.architecture`. Report v1 remains only as an explicit pure
one-way compatibility renderer. It receives the canonical Specification and
separate compiler layout, physical-footprint model/result, resource-protocol
bindings, execution plan/result, and analysis artifacts; projects the frozen
v1 field names and compatibility hashes to plain JSON; imports and instantiates
none of the removed architecture/QEC classes; and is never parsed back into
compilation or evaluation. It is therefore not a second architecture or
serialization authority.

`architecture/isa.py` and `architecture/state.py` remain internal plan/runtime
modules. Eager MOVE and deferred-dispatch records are typed at the ISA
boundary, and the shared flat `OperationClaims` contract defines the
four-role runtime realizer chain, and transition-only trace authority. Their
package location does not add dynamic state or ISA events to the static
Specification. If a later source reorganization is worthwhile, move the
coupled ISA/claim/recipe and state contracts together behind an execution-owned
boundary instead of treating either as static architecture authoring.
