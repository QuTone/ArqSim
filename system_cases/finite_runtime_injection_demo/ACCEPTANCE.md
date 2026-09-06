# Profile 2.3 UI acceptance matrix

This matrix is the review gate for the finite runtime-injection demonstration.
It maps one frozen Report v2 to the architectural and runtime concepts that a
reviewer must be able to identify. The reference report remains immutable; UI
changes must adapt its canonical facts rather than rewrite the evidence.

| Concern | Canonical evidence | Required presentation/adapter result | Automated gate |
| --- | --- | --- | --- |
| Memory | `na_compute_node/na_memory/memory_region` and STORE/LOAD targets | A stable NA Memory module exists; STORE/LOAD appear as transfers between Memory and Compute | `profile23Acceptance.test.ts` |
| Compute | `na_compute_node/na_compute/compute_region` | Compute work and the collapsed T parent use the single Compute Region locus | `profile23Acceptance.test.ts` |
| MSF factory | `sc_msf_node/sc_msf/factory_engine` and `PREPARE_MAGIC_STATE` | Preparation stays on the factory engine; the output buffer is not presented as an execution engine | `profile23Acceptance.test.ts` |
| Inter-module link | `compute_msf_link`, `PREPARE_LOGICAL_BELL`, and `TELEPORT_QUBITS` | Bell preparation and teleportation use the named link locus | `profile23Acceptance.test.ts` |
| Buffers/endpoints | `magic_compute`, `msf_output`, and `bell:compute_msf_link` state snapshots and milestones | State tracks remain attached to Compute, MSF, and link owners; delivery into Compute is observable | `profile23Acceptance.test.ts` |
| T gadget | Two implementation recipes with outcomes `[1, 0]` | Two T parents remain identifiable; each host runs from dispatch to terminal completion and expands in place | `profile23Acceptance.test.ts` |
| Classical reaction | Two `reaction` children and one conditional logical-S correction | Both sampled branches are visible on expansion; only outcome 1 materializes correction | `profile23Acceptance.test.ts` |
| Fidelity | Complete canonical fidelity result plus idle ledgers | Success probability is available; logical idling includes Compute, Store/Load, and Memory; resource idling includes the MSF output buffer | `profile23Acceptance.test.ts` |

The acceptance test deliberately checks semantic ownership, not visual pixel
geometry. A manual UI pass should additionally confirm that collapsing a T
gadget hides its implementation children, expanding it does not create an
anonymous Compute row, and vertical scrolling keeps every architecture locus
reachable.

Run the adapter acceptance gates from `frontend/`:

```bash
npm run test:adapter
```

Run the canonical replay and backend system-case gates from the repository
root:

```bash
python -m system_cases.finite_runtime_injection_demo.run
python -m pytest -q tests/test_finite_runtime_injection_system_case.py
```
