# Known limitations

These boundaries apply to ArqSim 0.2.0. Results are analytical research
estimates under the assumptions retained in each report.

## Inputs and compilation

- Inputs are post-synthesis logical circuits. Parsing into `FTCircuit` does not
  perform fault-tolerant synthesis, and its representation identifies a dialect,
  not proof that a backend supports every operation.
- The reference compiler preserves conservative source-layer order and works
  within layer partitions and magic-consumption batches. It does not model a
  general operation-dependency schedule, persistent cross-layer routing, or
  compiler-selected checkpoints. The PBC parser places one operation per layer.
- Each evaluation targets one compute Module. Capacity partitions control
  residency; magic batches control resource consumption. Unsupported operations,
  insufficient capacity and incompatible compilation artifacts fail closed.
- Generic magic-demand statistics count each T/Tdg or Pauli rotation as one
  resource unit. This is the sizing convention of the bundled architectures,
  not a branch-dependent demand model for arbitrary rotation gadgets.
- Deferred dispatch supports joint magic routing and state-bound Resource MOVE.
  Compute blocks retain aggregate timing and source-operation receipts rather
  than a typed schedule of every internal action. Custom compiler and runtime
  integrations must respect the [supported contracts](02-offline-pipeline/offline-pipeline-api.md).

## Geometry and timing

- Specification coordinates describe owner-local logical placement, not physical
  qubit coordinates or LightStim offsets. Footprint estimates count installed
  resources without placing physical qubits, couplers, patches or ancillas.
  ArqSim does not generate a physical QEC circuit.
- Neutral-atom movement uses a reference site-spacing/distance-time model.
  BB/QLDPC memory modes have runtime slot identities without per-mode geometry;
  STORE/LOAD and inter-Node transfer use operation profiles.
- The superconducting backend derives a dense logical routing canvas and rejects
  canvases above 100,000 sites. PPM timing uses syndrome-service waves, not exact
  routed edge length. Its aggregate timing trusts the selected backend's
  conflict-group labels: disjoint work may share a wave; conflicting groups and
  separate source layers remain serial.
- Gate timing is modality-level, not operation-specific. Disabling fidelity does
  not supply a missing timing or lowering model for an arbitrary rotation.
- Factory and Bell protocols are black boxes; their internal patches, transitions
  and ancillas are not runtime slots. Interconnects attach exactly two Nodes.
  One Bell-pair state occupies one shared runtime slot; footprint accounting
  counts both endpoint-local logical states. Detailed channel allocation,
  repeaters, swapping and physical endpoint placement are not modeled.
- Bell production uses a shared physical-pair supply rate and pays an explicit
  cold-start service component before steady-state arrivals. Explicit arrival
  overrides retain the chosen protocol identity and output quality; they are
  sensitivity inputs, not new protocol calibrations.

## Scheduling and observations

- `RuntimeScheduler` orders work within each plane. The Engine owns Program-first
  eager dispatch, feasibility, resource batching, the same-time fixed point,
  single-candidate transactional commit and clock advancement. The scheduler
  must return each presented ID exactly once; it cannot filter work,
  intentionally defer it, arbitrate across planes or commit a batch.
- Plan `engines` include service engines and movement/interface contention
  domains. `target_modules` and `target_links` identify operation ownership;
  required/completion locations track logical-qubit state.
- Summary tracing retains the causal transition ledger. Exact wait-cause and
  buffer-occupancy views require the validated full diagnostic log. Frontends
  must consume those records rather than reconstruct runtime facts.
- A fixed seed reproduces the implemented stochastic choices. The simulation is
  not cycle-accurate hardware execution.

## Fidelity and calibration

- `canonical_reference_v1` is enabled by default. Only explicit
  `fidelity_profile=None` disables it. Incomplete operation or idle coverage
  makes the common API reject the evaluation. Complete coverage means that every
  implemented operation, resource-output and idle-exposure domain has a model;
  it does not establish complete physical-noise coverage or experimental calibration.
- The [PPM provenance bundle](provenance/ppm/README.md) includes the exact table,
  fit and fitter, with calibrated ranges and extrapolation limits. Table-to-fit
  reproduction was verified; the complete original Monte Carlo generation
  implementation/environment is not a frozen public artifact. The README also
  explains the model YAML's historical availability receipt, retained to
  preserve existing profile/report identities.
- Fidelity combines the reference channels in log-survival space. Consumed
  resource error and ready-buffer idle are charged once along the token ancestry
  that reaches Program work. Unused background production contributes to
  utilization and waste, not application success probability. Production and
  delivery are active protocol intervals rather than buffered idle.
- Resource-aware fidelity requires the matching typed Plan and resolved protocol
  bindings. Magic-buffer idle uses the architecture's QEC binding; shared Bell
  storage uses its protocol's stored-state QEC calibration. Missing calibration
  for nonzero residence is uncovered, not assigned a guessed rate.

## Finite T injection

- The default `black_box` mode keeps T-family operations monolithic. Explicit
  `finite_state_injection_v1` supports the neutral-atom T convention using shared
  entangle and magic-Z measurement phases, followed by per-invocation reactions
  and outcome-one materialized logical S corrections. Tdg and unsupported forms
  fail closed. Direct angle-doubling recipes can execute, but there is no
  automatic STAR architecture/compiler lowering path.
- Finite lowering rejects a compute unit that mixes T injections with Clifford
  work. The compiler must separate `H || T` or `CX || T`; a pure `T || T` batch
  is supported. Superconducting Profiles 1.2 and 2.2 expose aggregate overlap
  timing without a causal phase decomposition, so finite mode is unsupported;
  their default black-box evaluations remain supported.
- Finite mode requires an explicit `reaction_latency_by_modality_s` entry for
  the compute modality. Zero is valid; an absent entry is rejected.
- The logical measurement provider samples Bernoulli(1/2), not a quantum state.
  Reaction latency is fixed with no contended classical engine. Logical S
  corrections claim compute capacity and may serialize. This is not a calibrated
  multi-T decoder/correction throughput or frame-update model.
- The shared phases use `reference_additive_gadget_decomposition_v1`: routing
  plus gate service for entangle, then syndrome service for measurement. This
  preserves compiled timing but is not independently calibrated primitive timing.
- Fidelity retains the source logical T as its aggregate operation channel;
  implementation CX/measurement events do not charge another logical gate.
  Measurement/reaction intervals accrue logical-data idle exposure, and realized
  logical S corrections are charged separately.
- Plan v9/Trace v4 retain the consumed resource batch without a per-invocation
  token/slot allocation receipt. Homogeneous states support current aggregate
  fidelity; a UI must not invent a mapping by zipping invocation and token order.

## Integration and distribution

- Report v2 is the supported portable report. Report v1 is a temporary, one-way,
  static-only compatibility output and cannot represent finite runtime recipes.
  Old Plan schema versions are not silently accepted.
- A runtime manifest identifies component algorithms, not executable callback
  code. Deferred Plan execution needs the matching live component set. Official
  callback contexts are validated against Plan identities; custom trusted
  callbacks remain the embedding application's responsibility.
- The wheel contains the Python package, runtime YAML and CLI. The source
  distribution also includes maintained documentation, examples, tests and
  System Cases. Third-party workloads and calibration material retain their
  [source and license notices](../THIRD_PARTY_NOTICES.md).
