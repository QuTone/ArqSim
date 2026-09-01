# Public Python API

Status: **frozen for the 0.2.0 release candidate**.

ArqSim uses a deliberately small common facade and owner-scoped advanced
interfaces. `from heteqsys import *` exposes exactly:

```python
EvaluationConfig
EvaluationReport
FTCircuit
run_evaluation
```

`__version__` remains a normal attribute. Temporary pre-0.2 root names are
lazy warning aliases outside `__all__`; their removal schedule is documented
as part of the v0.2 compatibility window described below.

## Owning subpackages

- `heteqsys.program` owns the immutable circuit/statistics input and workload
  loaders.
- `heteqsys.architecture` owns Profile, sizing/layout construction contracts,
  the canonical Specification, and typed finite injection recipes.
- `heteqsys.qec` owns resource-protocol profile records and catalog loaders.
- `heteqsys.operation_profiles` owns latency, movement, fidelity, and resolved
  resource-binding inputs. Paper-specific fit equations are imported from
  their concrete owner modules, not the subpackage facade.
- `heteqsys.compiler` owns whole-pipeline configuration, the replaceable
  `CompilerPipeline`, and `LogicalCompilationResult` records. Individual
  allocator/mapper/router functions remain internal.
- `heteqsys.evaluation` owns Plan/DAG/Trace contracts, analyzers, direct
  evaluation, strict replay, and descriptor-bearing four-role runtime
  composition.
- `heteqsys.synthesizer` owns optional synthesis services and artifacts.
- `heteqsys.visualization` owns pure plotting functions and requires the
  `visualization` installation extra.

The exact wildcard manifests are guarded by repository tests. New exports need
an owner, a documented stability level, and a real caller or extension use
case; adding a convenience re-export alone is not sufficient.

## Internal paths

`architecture.isa`, `architecture.state`, concrete compiler passes and
registries, transient lowering inputs, built-in runtime implementations, and
paper-fit helper equations are implementation modules. They may be inspected
or tested directly but carry no pre-1.0 import compatibility promise.

Report v1 is an output adapter, not a general Python API. Report v2 loaders are
v2-only, and `validate_execution_trace_document()` accepts only the exact
Trace-v3 record rather than flattened runtime diagnostics or compatibility
projections.
