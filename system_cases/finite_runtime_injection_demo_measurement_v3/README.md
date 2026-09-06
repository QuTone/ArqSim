# Finite runtime-injection measurement-v3 successor

This immutable System Case succeeds
`finite_runtime_injection_demo_locus_v2`. It keeps the same ArqSim-owned
workload, profile 2.3 architecture, timing profile, runtime policy,
capacities, demands, and durations while freezing the following semantic
boundary:

- ExecutionPlan uses Plan v9;
- Runtime Manifest v4 selects `measurement_provider` rather than the retired
  generic `outcome_model` role;
- `measurement.seeded_bernoulli.v1` is invoked only for logical measurement
  steps with explicitly requested registers;
- ordinary event completion does not synthesize an outcome.

The explicit seed changes from 5 to 0 because the provider's new stable seed
identity changes the sampled stream. Seed 0 deterministically produces logical
bits `[1, 0]`, preserving review coverage of both continuation branches: both
gadgets contain entangle, measurement, and reaction children, and the first
also contains the conditional logical-S child. No physical or timing input was
changed to obtain that coverage.

Run the strict reference replay and live byte-identical acceptance gate with:

```bash
python -m system_cases.finite_runtime_injection_demo_measurement_v3.run
```

The [UI acceptance matrix](ACCEPTANCE.md) lists the backend facts consumed by
the frontend adapter. Both predecessors remain immutable, archive-only
evidence. This case exposes no in-place reference-regeneration command; a later
semantic change requires another named successor.
