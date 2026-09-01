# Architecture semantic oracles

These six fixtures are the behavior gate for the ground-up refactor.  They are
not snapshots of `ArchitectureSpecification.to_dict()` or report v1.  Each
fixture keeps only facts that survive a change of Python classes or serialized
document shape:

- Node/Module/Submodule ownership, type, and payload;
- local connections and shared Interconnect endpoint access;
- resolved capacity, QEC binding, slot identity, and meaningful logical
  geometry (BB memory slots are explicitly identity-only);
- resource-protocol provisioning;
- compiler backend selection and the Program dependency/claim/cost semantics;
- recurring resource process timing and resource claims;
- deterministic completion schedule, analytical latency, occupancy, blocking,
  and core token metrics;
- analytical footprint results, kept under `results` rather than static
  architecture.

For profiles 1.3, 2.2, and 2.3, one Bell-pair token is owned by the shared
Interconnect.  It has two endpoint-local halves, one encoded logical-qubit
state per endpoint.  Pair capacity is therefore counted once by the runtime,
while endpoint state and physical footprint are accounted at both endpoints.

The fixtures intentionally omit hashes, schema/layout-plan wrappers, legacy
roles, ports, compiler routing-node IDs, runtime component manifests, report
section names, event/reservation IDs, and other implementation receipts.

Verification is the default and never writes:

```bash
python -m tests.generate_architecture_semantic_oracles
```

After an approved semantic change, refresh requires an explicit flag:

```bash
python -m tests.generate_architecture_semantic_oracles --update
```

Never update these fixtures merely to make a refactor pass.  First classify a
difference as an old bug, an owner-approved model change, or a regression.
