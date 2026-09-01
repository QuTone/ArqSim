# Repository Structure

Dependencies flow in one direction:

```text
schema
  ↓
architecture   qec   program   operation_profiles
      ↓         ↓      ↓
        compiler / synthesizer
                 ↓
             evaluation
                 ↓
        public API service
                 ↓
       visualization / CLI / HTTP
```

Key separation rules:

- architecture owns unsized Profiles, architecture-construction
  policies/results, and the immutable static Specification. The physically
  retained `isa.py`/`state.py` are quarantined internal execution support, not
  architecture authoring or extra static hierarchy;
- `architecture/gallery/` groups every bundled reference architecture by its
  published design name. Each bundle owns one `profile.yaml`, one concrete sizing policy,
  and one concrete layout policy; Profile IDs remain opaque lookup keys;
- `program.CircuitStatistics` contains architecture-independent circuit facts.
  A `SizingPolicy` consumes those facts and a Profile and returns a complete
  `SizingResult`; each gallery bundle owns its architecture-specific sizing
  relation;
- a `LogicalLayoutPolicy` consumes the Profile and `SizingResult` and returns a
  `LogicalLayoutResult` covering every non-engine Submodule. It owns slot
  identity and logical placement, not sizing or QEC selection;
- `architecture.resolver.resolve_architecture()` mechanically joins the
  Profile, `SizingResult`, `LogicalLayoutResult`, exact QEC bindings, and
  selected catalog protocol profiles into one `ArchitectureSpecification`. It
  does not receive an `FTCircuit`, run a policy, or dispatch on Profile IDs;
- QEC owns per-Submodule code/protocol choices, not latency measurements;
- operation profiles own simulation- or literature-derived data;
- compiler owns allocation, mapping, routing, movement cost, and the strict
  `LogicalCompilationResult` output contract, including the effective magic-state
  consumption policy and typed eager/deferred state of each compute route; it
  does not construct an `ExecutionPlan`;
- evaluation owns staged lowering from compiler/topology/cost inputs into the
  two DAG contracts, validates compilation policy/source agreement, projects
  deferred recipes from typed route state, revalidates architecture capacities
  plus canonical compute-slot/deferred-mapping/continuity coherence, preserves
  the compilation hash in plan provenance, and owns strict `ExecutionPlan` v6,
  the four-role runtime composition, typed-continuation ExecutionTrace v3, and
  co-execution;
- `api.py` owns the versioned one-shot orchestration/report contract;
- visualization reads public contracts and never changes semantics.

The CLI and the HTTP adapter at `frontend/backend/` are adapters over
`run_evaluation`; they do not assemble architecture, compiler, and evaluator
stages independently.

The architecture construction flow is:

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

The generic architecture contracts live in `architecture/sizing.py`,
`architecture/logical_layout.py`, and
`architecture/logical_layout_policy.py`. `logical_layout.py` owns the partial
request and complete result records; `logical_layout_policy.py` owns the
shared interface and mechanical helpers. Concrete bundled implementations
live under their design names in `architecture/gallery/`: `na_cf`, `sc_cf`,
`na_c_plus_sc_f`, `na_mcf`, `na_m_plus_sc_cf`, and
`na_mc_plus_sc_f`. The explicit gallery catalog is the sole association of a
bundled Profile with its two factories; it is runtime-only and has no codec or
hash. The whole-compiler extension point remains in
`compiler/interface.py`; low-level allocator functions remain internal, and
the descriptor-bearing runtime Scheduler contract lives in
`evaluation/components.py`.

All six bundled Profiles use this generic construction path. Profile 1.2 stores
occupied SC patch coordinates directly on its checkerboard logical canvas; the
unoccupied routing fabric is a compiler-derived artifact, not an Architecture
field. Profile 2.1 keeps memory modes identity-only and places its
compute-facing Store/Load buffer on the logical canvas.

The concrete multi-Node policies live inside their reference architecture bundle:

- `NA-C + SC-F`: `gallery.na_c_plus_sc_f`;
- `NA-M + SC-CF`: `gallery.na_m_plus_sc_cf`; and
- `NA-MC + SC-F`: `gallery.na_mc_plus_sc_f`.

Their Interconnect owns one shared Bell Engine and Bell Storage. Bell Storage
slots have identity-only layout by default and may be explicitly placed only
on the Interconnect's own logical canvas.

The downstream cutover is complete. Compiler, footprint accounting,
execution-plan lowering, evaluator, and the public API consume
`architecture.specification.ArchitectureSpecification` directly. The old
combined `ArchitectureLayoutPolicy`/`ArchitectureLayoutPlan`,
`architecture/layout_policy.py`, `quantile_layout.py`, `legacy_adapter.py`,
compatibility hierarchy/spec modules, `ResolvedFTSystemSpec`,
`ArchitectureSpec`, `QECConfiguration`, and the horizontal
`layout_policies/` package have been deleted. None is an extension point or a
serialization authority.

`report_v1.py` is the only retained v1 compatibility boundary. It projects the
canonical Specification plus independent compiler, footprint, protocol,
execution, and analysis artifacts directly into plain JSON. It imports and
instantiates no removed architecture/QEC model, and its output never flows
back into compilation or evaluation. Native Python and CLI output now use the
strict self-contained `report_v2.py` boundary; the v1 renderer remains only as
an explicit static-only compatibility adapter during its deprecation window.

Phases 4B and 5 make the next dependency edge explicit:

```text
compiler/pipeline.py
  -> compiler/output.py::LogicalCompilationResult
  -> evaluation/lowering.py (validation and orchestration)
       + evaluation/_plan_inputs.py (transient topology/cost values)
       + evaluation/_program_lowering.py (Program DAG)
       + Resource/state lowering helpers (Resource DAG and inventories)
  -> evaluation/plan.py::ExecutionPlan v6
  -> evaluation/engine.py::evaluate(...)
```

`architecture/isa.py` currently owns the internal flat `OperationClaims` base,
typed `MoveOperands`, and tagged deferred-dispatch records shared by the plan
and runtime boundaries; `architecture/state.py` owns mutable runtime state.
Neither is part of the static Specification. Their physical location remains
explicitly internal; a future organization-only move should relocate these
coupled execution contracts together behind an execution-owned package
boundary. The transient `RuntimeTopology`, `PlanCostInputs`, and
`SyndromeCostInput` records have no codec or hash: they detach one lowering
run's derived values without becoming a second architecture, latency, or cost
authority. Claim unification reduced the runtime
manifest to the four `runtime_realizer`, `scheduler`, `execution_backend`, and
`outcome_model` roles without moving these files.
`evaluation/plan.py` owns v6 wire completeness: its parser requires the checked
plan hash, exact JSON container/scalar shapes, full canonical runtime manifest,
complete buffer records, and exact logical-qubit MOVE/recipe coherence rather
than filling or normalizing wire fields from constructor defaults.
`compiler/output.py` distinguishes a source partition's operated
`active_qubits` from the compute residency owned by each unit mapping.
`evaluation/components.py` keeps component manifests as algorithm identities
and validates the separate Program/Resource source contexts held by the
official state-bound realizer against the plan authorities immediately before
execution. `evaluation/result.py` owns strict transition-only ExecutionTrace
v3; report v1 alone serializes its derived completed-event view.

The installable compiler keeps modality-specific primitives only when the
generic pipeline consumes them. Pre-mainline adapters and converted-input
artifacts are excluded from the public package and release artifacts.
