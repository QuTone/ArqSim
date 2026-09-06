# Finite runtime-injection canonical-locus successor

This immutable System Case succeeds `finite_runtime_injection_demo`. It keeps
the same owned workload, architecture, seed, runtime policy, capacities,
demands, and durations, while freezing corrected execution-locus metadata:

- Bell production belongs to the interconnect Bell engine;
- store/load contention belongs to the Compute store/load buffer;
- synthetic resource-delivery concurrency has no false MSF owner.

Replay its hash-checked historical evidence with:

```bash
python -m system_cases.finite_runtime_injection_demo_locus_v2.run
```

This case and its predecessor are replay-only historical evidence. Current-code
strict replay and live byte-identical acceptance belong to
`finite_runtime_injection_demo_measurement_v3`, which records the Plan-v9 and
logical-measurement-provider semantic change. No case exposes an in-place
reference-regeneration command.
