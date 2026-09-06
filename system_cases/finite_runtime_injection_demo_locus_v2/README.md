# Finite runtime-injection locus-v2 System Case — archived

This immutable successor to the
[original case](../finite_runtime_injection_demo/README.md) preserves its
workload, architecture, seed, policy, capacities, demands, and durations while
correcting execution-locus metadata:

- Bell production belongs to the interconnect Bell engine;
- store/load contention belongs to the Compute store/load buffer;
- synthetic resource-delivery concurrency has no false MSF owner.

Verify the archived evidence from the repository root:

```bash
python -m system_cases.finite_runtime_injection_demo_locus_v2.run
```

This command verifies frozen evidence without rerunning the historical case.
Use the [measurement-v3 successor](../finite_runtime_injection_demo_measurement_v3/README.md)
for current-code strict replay and live acceptance. No case exposes an in-place
reference-regeneration command; a semantic change requires a named successor.
