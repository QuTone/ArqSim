# ArqSim Documentation

For a first run, start with the [Python quickstart](../README.md#python-quickstart)
or the [executed notebook](../examples/notebooks/quickstart.ipynb). The
[examples index](../examples/README.md) connects the beginner demo to more
detailed compiler and runtime examples.

This reference directory is organized by integration depth and review order.
The numbered levels are **not** release phases or version numbers: they describe
how far a reader is crossing into ArqSim's implementation contracts.

## Main review path

1. Start with the Level-1 [Public Python API](01-public-api/public-api.md) to
   understand `FTCircuit + EvaluationConfig -> run_evaluation() ->
   EvaluationReport`.
2. Continue with the Level-2
   [Offline Pipeline API](02-offline-pipeline/offline-pipeline-api.md) for the
   compiler and plan-lowering boundary.
3. Finish with the Level-3
   [Evaluation Engine](03-runtime/evaluation-engine.md) for runtime state,
   scheduling, clock advancement, trace production, and the low-level result
   handoff to the public report facade.

## Document map

| Level | Document | Role | Intended audience | Status |
| --- | --- | --- | --- | --- |
| 0 | [Architecture Specification](00-foundations/architecture-specification.md) | Architecture foundation | Architecture authors and reviewers | Current for 0.2.0 |
| 0 | [Repository Structure](00-foundations/repository-structure.md) | Ownership and dependency map | Contributors | Current for 0.2.0 |
| 1 | [Public Python API](01-public-api/public-api.md) | Normative common API | Evaluation users and integrators | Current and frozen for 0.2.0 |
| 1 | [Report and Trace Schema](01-public-api/report-and-trace-schema.md) | Normative serialized-output reference | Report, UI, and tooling consumers | Current for 0.2.0 |
| 2 | [Offline Pipeline API](02-offline-pipeline/offline-pipeline-api.md) | Normative advanced API | Compiler and lower-level integrators | Current for 0.2.0 |
| 2 | [Compiler IR Walkthrough](02-offline-pipeline/compiler-ir-walkthrough.md) | Guided artifact example | Reviewers and compiler authors | Current for 0.2.0 |
| 2 | [ExecutionPlan Instruction Reference](02-offline-pipeline/execution-plan-instruction-reference.md) | Generated Plan/ISA field reference | Plan producers and runtime reviewers | Current for 0.2.0 |
| 3 | [Evaluation Engine](03-runtime/evaluation-engine.md) | Runtime kernel and result-handoff reference | Scheduler, realizer, and engine authors | Current and frozen for 0.2.0 |
| 4 | [Extension Guide](04-extension/extension-guide.md) | Supported extension seams | Architecture, compiler, and model authors | Current for 0.2.0 |
| Roadmap | [Known Limitations and Naming](roadmap/known-limitations.md) | Current limitations and deferred work | Users and contributors | Current for 0.2.0 |
| Provenance | [PPM Calibration](provenance/ppm/README.md) | Calibration table, fit, hashes and reproducible refit | Model authors and reviewers | Retained calibration evidence |

The Level-0 documents support the whole path rather than adding another
runtime stage. Level 4 is for authors extending a contract after they
understand its owning level. The roadmap records intentionally unsupported or
deferred behavior; it is not part of the current API promise.

The Level-3 freeze covers only the reviewed runtime contract and its handoff to
the Level-1 facade. `EvaluationReport` remains owned by the Level-1 API and
schema documents; the freeze does not make private kernel helpers or deferred
roadmap designs public.

## Authority and history

When a guide and a normative contract differ, the normative API or schema
document wins. Strict serialized schemas and their implementation validation
remain the final machine-readable authority.

Superseded pre-release design notes are retained under [archive](archive/) for
provenance. They are non-authoritative and are not part of the reading path.
