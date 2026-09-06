# Documentation

Start with the [Python quickstart](../README.md#python-quickstart) or
[executed notebook](../examples/notebooks/quickstart.ipynb).
The [examples guide](../examples/README.md) covers results, configuration,
save/load, and the CLI.

## Reference map

Levels describe integration depth, not release phases. All references below
apply to ArqSim 0.2.0. Read Level 1 to use the package, Level 2 to inspect
compilation, and Level 3 to understand runtime behavior.

| Level | Document | Purpose |
| --- | --- | --- |
| 0 | [Architecture Specification](00-foundations/architecture-specification.md) | Static architecture model and construction |
| 0 | [Repository Structure](00-foundations/repository-structure.md) | Code ownership and dependencies |
| 1 | [Public Python API](01-public-api/public-api.md) | Common evaluation interface and errors |
| 1 | [Report and Trace Schema](01-public-api/report-and-trace-schema.md) | Serialized results and validation |
| 2 | [Offline Pipeline API](02-offline-pipeline/offline-pipeline-api.md) | Compilation and plan lowering |
| 2 | [Compiler IR Walkthrough](02-offline-pipeline/compiler-ir-walkthrough.md) | Worked artifact example |
| 2 | [ExecutionPlan Instruction Reference](02-offline-pipeline/execution-plan-instruction-reference.md) | Plan/ISA fields and applicability |
| 3 | [Evaluation Engine](03-runtime/evaluation-engine.md) | Scheduling, state transactions, trace, and Result-to-Report handoff |
| 4 | [Extension Guide](04-extension/extension-guide.md) | Supported architecture, compiler, and model extensions |
| — | [Known Limitations](known-limitations.md) | Scientific assumptions and unsupported behavior |
| — | [PPM Calibration Provenance](provenance/ppm/README.md) | Source artifacts, hashes, and reproduction limits |

The Level-1 and Level-3 contracts are frozen for 0.2.0. That freeze does not
make private helpers or unsupported designs public APIs. `EvaluationReport`
remains owned by Level 1.

Normative API and schema references take precedence over walkthroughs.
Strict serialized schemas and their validation define the machine-readable
contract. For package navigation and contributor commands, see the
[core package guide](../arqsim/README.md).
