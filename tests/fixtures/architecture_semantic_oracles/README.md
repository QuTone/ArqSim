# Architecture semantic oracles

These six fixtures preserve gallery behavior across internal class or wire
format changes. They record:

- resource ownership, connectivity, capacities, QEC bindings, slot identities,
  and logical geometry, including identity-only BB memory slots;
- resource-protocol provisioning and compiler backend selection;
- Program dependencies, claims, and costs, plus recurrent Resource processes;
- deterministic schedules, latency, occupancy, blocking, and token metrics;
- physical-footprint estimates under `results`, separate from static resources.

For Profiles 1.3, 2.2, and 2.3, the shared Interconnect owns one Bell-pair token
with an encoded logical state at each endpoint. Runtime counts pair capacity
once; endpoint state and physical footprint are accounted at both endpoints.

Hashes, wrapper classes, routing-node IDs, manifests, and event/reservation IDs
are deliberately omitted. The owning contracts are described in
[Architecture Specification](../../../docs/00-foundations/architecture-specification.md)
and [Offline Pipeline API](../../../docs/02-offline-pipeline/offline-pipeline-api.md).

From the repository root, verify without writing:

```bash
python -m tests.generate_architecture_semantic_oracles
```

For a reviewed semantic change, the [generator](../../generate_architecture_semantic_oracles.py)
accepts `--update`. Classify a difference as a bug fix, an approved model change,
or a regression before updating fixtures; do not refresh them to make a
refactor pass.
