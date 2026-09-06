# `arqsim` package

This directory contains ArqSim's installable Python core. Start with the
[project quickstart](../README.md) to run an evaluation or the
[documentation index](../docs/README.md) for the supported contracts.

The package-root facade has four names:

```text
FTCircuit + EvaluationConfig -> run_evaluation() -> EvaluationReport
```

Advanced authoring, compilation, and runtime interfaces live in their owning
subpackages. Their supported imports and compatibility aliases are listed in
[Public Python API](../docs/01-public-api/public-api.md).

## Source map

Read these modules in pipeline order:

| Stage | Entry points | Responsibility |
| --- | --- | --- |
| Public service | [api.py](api.py), [cli.py](cli.py) | Resolve a request, run the pipeline, and expose its report. |
| Program input | [program/circuit.py](program/circuit.py), [program/load.py](program/load.py), [synthesizer/](synthesizer/) | Load or synthesize the canonical `FTCircuit` and derive circuit statistics. |
| Architecture | [specification.py](specification.py), [architecture/construction.py](architecture/construction.py) | Select gallery policies and run sizing, logical layout, and canonical resolution. |
| Static resources | [architecture/specification.py](architecture/specification.py), [architecture/gallery/](architecture/gallery/) | Define resolved resource ownership and the bundled architecture designs. |
| Model inputs | [operation_profiles/](operation_profiles/), [qec/protocol.py](qec/protocol.py) | Bind timing, fidelity, movement, and resource-protocol inputs. |
| Logical compiler | [compiler/pipeline.py](compiler/pipeline.py), [compiler/output.py](compiler/output.py) | Produce typed compute units, placements, routes, and `LogicalCompilationResult`. |
| Plan lowering | [evaluation/lowering.py](evaluation/lowering.py), [evaluation/_program_lowering.py](evaluation/_program_lowering.py) | Validate compilation and build Program work, recurrent Resource work, and static inventory. |
| Runtime | [evaluation/engine.py](evaluation/engine.py), [evaluation/components.py](evaluation/components.py) | Bind, schedule, and transactionally execute the Plan. |
| Results | [evaluation/result.py](evaluation/result.py), [evaluation/analysis.py](evaluation/analysis.py), [report_v2.py](report_v2.py) | Record causal Trace, derive analysis, and validate the persisted Report. |
| Views | [visualization/](visualization/) | Render evaluation artifacts. |

`architecture/isa.py` and `architecture/state.py` support Plan/runtime execution;
their package location does not make them static architecture configuration.
[Repository Structure](../docs/00-foundations/repository-structure.md) documents
object ownership and the remaining source layout.

## Contracts to preserve

- Architecture construction follows `ArchitectureProfile ->
  ArchitectureSpecification -> ArchitectureState(t)`. The Specification owns
  effective static resources; timing models and mutable runtime state have
  separate owners. See
  [Architecture Specification](../docs/00-foundations/architecture-specification.md).
- The offline boundary accepts a post-synthesis `FTCircuit`, Specification, and
  `OperationLatencyProfile`. `CompilerPipeline` returns
  `LogicalCompilationResult`; `lower_compilation_result()` validates it and
  produces `ExecutionPlan`. The v1 compiler contract preserves source layers.
  See [Offline Pipeline API](../docs/02-offline-pipeline/offline-pipeline-api.md)
  and the [IR walkthrough](../docs/02-offline-pipeline/compiler-ir-walkthrough.md).
- Fidelity defaults to `canonical_reference_v1`; only explicit
  `fidelity_profile=None` disables it. Required coverage fails closed.
  `ExecutionPolicy.injection_lowering_mode` defaults to `black_box`; finite
  injection is an explicit choice with required reaction-timing inputs.
  See [Public Python API](../docs/01-public-api/public-api.md).
- Runtime owns availability, binding, measurement outcomes, conditional work,
  and state commits. Trace records those facts; visualization consumes them.
  See [Evaluation Engine](../docs/03-runtime/evaluation-engine.md) and the
  [measurement-v3 System Case](../system_cases/finite_runtime_injection_demo_measurement_v3/README.md).
- Persisted Report v2 embeds the canonical compilation, Plan, and Trace.
  Loading validates their identities and recomputes derived results without
  rerunning the compiler or runtime. [report_v1.py](report_v1.py) is a one-way
  compatibility renderer. See
  [Report and Trace Schema](../docs/01-public-api/report-and-trace-schema.md).

The core estimates logical execution and physical resource requirements;
physical-qubit placement and pulse-level simulation are outside this release.
Use the [Extension Guide](../docs/04-extension/extension-guide.md) before adding
a backend or architecture, and check the
[known limitations](../docs/known-limitations.md) for supported scope.

## Development

Run from the repository root:

```bash
python -m pip install -e '.[test]'
python -m pytest -q
python tests/run_packaging_smoke.py
```

New public package directories must be listed in [pyproject.toml](../pyproject.toml)
and their source files included in [MANIFEST.in](../MANIFEST.in). The packaging
check verifies the complete wheel and source distribution, including a clean
installed API/CLI run.
