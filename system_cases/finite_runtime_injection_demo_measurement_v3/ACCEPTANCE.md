# Profile 2.3 measurement-v3 acceptance matrix

| Concern | Frozen acceptance fact |
| --- | --- |
| Architecture | Profile 2.3 retains distinct NA Compute, NA Memory, SC MSF, and interconnect loci. |
| Runtime identity | Plan v9 embeds Runtime Manifest v4 with `measurement_provider=measurement.seeded_bernoulli.v1`. |
| T gadget | Two source T parents each retain entangle, logical measurement, and reaction children. |
| Fixed-seed result | Seed 0 produces logical measurement bits `[1, 0]`; exactly one invocation materializes logical S. |
| Event boundary | The provider is called only for explicitly requested logical measurement registers, never for ordinary completion. |
| Fidelity | Coverage is complete and both logical-qubit and resource-state idling remain nonzero. |

Run the live backend gate from the repository root:

```bash
python -m system_cases.finite_runtime_injection_demo_measurement_v3.run
```

Run the adapter gate from `frontend/`:

```bash
npm run test:adapter
```
