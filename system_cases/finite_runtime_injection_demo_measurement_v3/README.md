# Finite runtime-injection measurement-v3 System Case

This is the current live acceptance case for finite runtime injection. It
succeeds the archived [locus-v2 case](../finite_runtime_injection_demo_locus_v2/README.md),
retaining the ArqSim-owned workload, Profile 2.3 architecture, timing inputs,
capacities, demands, and durations while recording these semantic changes:

- ExecutionPlan uses Plan v9;
- Runtime Manifest v4 selects `measurement_provider` instead of the retired
  generic `outcome_model` role;
- `measurement.seeded_bernoulli.v1` runs only for logical measurement steps
  with explicitly requested registers; ordinary completion emits no outcome.

The explicit seed changes from 5 to 0 because the provider's stable seed
identity changes the sampled stream. Seed 0 produces logical bits `[1, 0]`:
both T parents have entangle, measurement, and reaction children, and the first
also has a conditional logical-S child. No physical or timing input was changed
to obtain this branch coverage.

From the repository root, run strict reference replay and live byte-identical
acceptance:

```bash
python -m system_cases.finite_runtime_injection_demo_measurement_v3.run
```

The [UI acceptance matrix](ACCEPTANCE.md) identifies the backend facts consumed
by the frontend adapter; the [runtime contract](../../docs/03-runtime/evaluation-engine.md)
explains measurement and continuation semantics. Both predecessors remain
immutable archived evidence. This case has no in-place reference-regeneration
command; a semantic change requires another named successor.
