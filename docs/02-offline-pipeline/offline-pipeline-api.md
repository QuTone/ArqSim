# Offline Pipeline API

Level 2 · Role: normative advanced API · Status: current for ArqSim 0.2.0

The supported Python orchestration boundary described here is paired with the
strict, schema-versioned
`arqsim.logical-compilation-result.v1` and `arqsim.execution-plan.v9` wire
contracts. It is intentionally absent from the four-name package-root facade.

This document is the normative Level-2 API contract beneath
`run_evaluation()`. For a complete toy artifact, use the
[Compiler IR Walkthrough](compiler-ir-walkthrough.md). For individual Plan-v9
instruction fields and opcode applicability, use the
[ExecutionPlan Instruction Reference](execution-plan-instruction-reference.md).

## Boundary at a glance

```text
post-synthesis FTCircuit
        +
ArchitectureSpecification
        +
OperationLatencyProfile
        |
        v
CompilerPipeline.compile(...)
        |
        v
LogicalCompilationResult v1
        |
        |  validation + deterministic lowering
        v
ExecutionPlan v9                         last fully offline artifact
        |
        |  evaluate(plan, ...)
        v
EvaluationResult                         one live runtime result
```

Compilation records architecture-aware decisions about logical work. Plan
lowering turns those decisions into Program instructions, recurrent Resource
processes, static runtime inventory, and symbolic state-transition claims. It
does **not** create live occupancy, reserve a slot, bind a deferred resource,
or mutate architectural state. `evaluate()` creates the mutable
`ArchitectureState` and performs those runtime actions.

## Supported imports

Import advanced contracts from the subpackage that owns them:

```python
from arqsim.architecture import ArchitectureSpecification
from arqsim.api import EvaluationRunError
from arqsim.compiler import (
    COMPILATION_RESULT_SCHEMA_VERSION,
    CompilerPipeline,
    DefaultCompilerPipeline,
    LogicalCompilationResult,
    LogicalCompilerError,
    LogicalCompilerSpec,
    LogicalCompilerValidationError,
    LogicalMappingError,
    LogicalRoutingError,
    UnsupportedLogicalBackendError,
)
from arqsim.evaluation import (
    EXECUTION_PLAN_SCHEMA_VERSION,
    EvaluationError,
    EvaluationResult,
    ExecutionPlan,
    ExecutionPolicy,
    RuntimeComponentSet,
    compile_and_lower,
    evaluate,
    lower_compilation_result,
)
from arqsim.operation_profiles import (
    OperationLatencyProfile,
    ResolvedResourceProtocolBindings,
)
from arqsim.program import FTCircuit
```

Do not import these names from `arqsim` or from private implementation modules.
Individual mapper, router, allocator, and movement passes are not supported
plugin contracts in this release.

## Exact call contracts

### Whole-pipeline compiler

```python
class CompilerPipeline(ABC):
    def compile(
        self,
        circuit: FTCircuit,
        specification: ArchitectureSpecification,
        latency_profile: OperationLatencyProfile,
    ) -> LogicalCompilationResult: ...
```

The implementation receives the complete circuit and read-only resolved
authorities. It must return one canonical `LogicalCompilationResult`, cover
every source operation exactly once, and preserve the v1 source-layer order
and dependency semantics. It must not mutate any input.

`DefaultCompilerPipeline` is ArqSim's reference implementation. Replacing the
entire `CompilerPipeline` is the only supported compiler replacement seam.

### Compile and lower in one call

```python
def compile_and_lower(
    circuit: FTCircuit,
    specification: ArchitectureSpecification,
    latency_profile: OperationLatencyProfile,
    execution_policy: ExecutionPolicy,
    *,
    compiler_spec: LogicalCompilerSpec | None = None,
    compiler_pipeline: CompilerPipeline | None = None,
    resource_delivery_channels: int | None = None,
    runtime_components: Mapping[str, Any] | None = None,
    resource_protocol_bindings: ResolvedResourceProtocolBindings | None = None,
) -> tuple[LogicalCompilationResult, ExecutionPlan]: ...
```

This is the normal Level-2 orchestration helper. When `compiler_pipeline` is
absent, it constructs `DefaultCompilerPipeline` from `compiler_spec` and the
execution policy. When a custom pipeline is present, that whole-pipeline
implementation is authoritative and `compiler_spec` is not a pass-injection
mechanism.

### Lower an existing compilation result

```python
def lower_compilation_result(
    circuit: FTCircuit,
    specification: ArchitectureSpecification,
    latency_profile: OperationLatencyProfile,
    execution_policy: ExecutionPolicy,
    compilation: LogicalCompilationResult,
    *,
    resource_delivery_channels: int | None = None,
    runtime_components: Mapping[str, Any] | None = None,
    resource_protocol_bindings: ResolvedResourceProtocolBindings | None = None,
) -> ExecutionPlan: ...
```

Use this form to reuse a decoded or externally produced v1 artifact. Lowering
revalidates the artifact against the supplied source authorities before it
constructs a Plan. It does not trust matching Python types alone.

### Cross the runtime boundary

```python
def evaluate(
    plan: ExecutionPlan,
    *,
    runtime_components: RuntimeComponentSet | None = None,
) -> EvaluationResult: ...
```

`ExecutionPlan.runtime_components` is the frozen Plan-v9 field containing the
planned `RuntimeComponentManifest` wire receipt. It is not a live component
collection. An explicitly supplied live `RuntimeComponentSet` must match that
planned receipt.
The resulting `EvaluationResult` is a low-level result from one execution; the
Level-1 facade subsequently analyzes and renders it as `EvaluationReport`.

## Authorities, inputs, and ownership

| Value | Authority for | Read by | Owned output / receipt |
| --- | --- | --- | --- |
| `circuit` | Logical operations, source layers, operands, semantic identity | Compiler and lowering | Both artifacts bind `circuit.semantic_hash`; neither embeds the circuit |
| `specification` | Resolved topology, capacities, modules, submodules, links, and protocols | Compiler and lowering | Both artifacts bind `architecture_hash`; Plan installs the derived static inventory |
| `latency_profile` | Gate, protocol, movement, delivery, arrival, and reaction cost inputs | Compiler reads the subset it needs; lowering resolves Plan work | Compilation binds `binding_hash`; Plan binds the resolved `profile_hash` |
| `execution_policy` | Magic batching, gadget lowering, observation, seed, and safety bounds | Compiler orchestration, lowering, and runtime | Compilation records its magic-consumption assumption; Plan embeds the full policy |
| `compilation` | Compiler-selected units, mapping, routing, and aggregate timing | Lowering | Plan provenance binds `compilation_hash` |
| `resource_protocol_bindings` | Optional already-resolved resource protocol/arrival authority | Lowering | Plan provenance records the resolved binding and hash when supplied |
| `resource_delivery_channels` | Optional low-level delivery-concurrency override | Lowering | Plan provenance records its value and source; omission derives it from architecture buffers |
| `runtime_components` | Planned `RuntimeComponentManifest` wire record, despite the frozen plural Plan-v9 field name | Lowering records it; runtime validates it | Plan embeds one canonical planned manifest; it never embeds live Python components |

### No second configuration step

In `run_evaluation()`, ArqSim resolves these values once from
`EvaluationConfig` and passes the same authorities through compilation and
lowering. A user does not configure the architecture, timing, or execution
policy again between the two artifacts.

The three keyword-only lowering inputs are low-level integration controls, not
a new normal-user configuration layer. An adapter that calls the Level-2 API
directly is responsible for preserving one coherent resolved context. Omitted
delivery concurrency is derived from the architecture; omitted
`runtime_components` selects a planned manifest from the required
deferred-dispatch behavior, not a live `RuntimeComponentSet`.

## `LogicalCompilationResult v1`

The semantic center is `compute_units`; the remaining top-level fields bind
the decisions to their source authorities and compilation assumptions.

| Member | Meaning |
| --- | --- |
| `circuit_hash`, `architecture_hash`, `latency_profile_hash` | Source-authority identities checked again during lowering |
| `compiler_spec` | Canonical receipt for the compiler configuration represented by the artifact |
| `magic_state_consumption` | Compiler/lowering batching assumption: `bulk_wave` or `incremental` |
| `compute_units` | Ordered `CompiledComputeUnit` decisions |
| `initial_mapping` | Eager logical-qubit-to-slot mapping, when mapping is not deferred |
| `deferred_capacity_mapping` | Whether initial placement is intentionally deferred to runtime-capacity handling |
| `compilation_hash` | Canonical semantic hash of the complete v1 record |

Each compute unit contains:

- `source`: the layer/partition/batch identity and exact source operation
  indices, active/operated qubits, and magic-state count;
- `mapping`: the resident logical-qubit-to-slot decision; and
- `route`: compiled route steps, duration components, and syndrome-protocol
  timing/provenance.

The strict codec is
`LogicalCompilationResult.to_dict()` / `to_json()` and
`LogicalCompilationResult.from_dict()` / `from_json()`. Decoding rejects
unknown or missing fields, duplicate JSON keys, non-finite values, noncanonical
wire shapes, unsupported schema versions, and hash mismatches.

## `ExecutionPlan v9`

`ExecutionPlan` is an immutable executable model, not an architectural-state
snapshot and not a runtime trace.

| Member | Meaning |
| --- | --- |
| `program_dag` | Finite, one-shot Program instructions and dependencies |
| `resource_dag` | Recurrent resource-production and delivery templates |
| `buffers`, `engines`, `initial_locations` | Static runtime inventory and bootstrap locations |
| `policy` | Frozen execution semantics used by the Event Engine |
| `runtime_components` | Canonical planned component-manifest wire record (the frozen Plan-v9 field name) |
| source hashes and `provenance` | Circuit, architecture, latency, compiler, protocol, and lowering receipts |
| `plan_hash` | Canonical semantic hash of the complete Plan-v9 record |

Program and Resource DAGs connect symbolically through buffer IDs, engine
claims, locations, predecessors, recipes, and typed state-transition claims.
Concrete availability, occupancy, reservations, token identities, bindings,
outcomes, and completion mutations are runtime state.

The strict codec is `ExecutionPlan.to_dict()` / `to_json()` and
`ExecutionPlan.from_dict()` / `from_json()`. Decoding validates the exact v9
wire shape, schema version, hash, installed buffer/engine references,
capacities, typed moves, resource forwarding, finite-gadget consistency, and
runtime component manifest.

## Validation and failures

Validation occurs at every trust boundary:

1. result constructors and codecs validate local shape and canonical identity;
2. lowering matches circuit, architecture, and latency hashes, policy
   assumptions, exact source-operation coverage, placement, capacity, routing,
   protocol, and gadget constraints;
3. `ExecutionPlan` validates cross-record references and executable claims; and
4. `evaluate()` validates the planned/live runtime component match before
   creating mutable state.

Callers must treat a validation failure as a rejected artifact, not as a
recoverable partial Plan. The advanced boundary deliberately preserves the
owner-specific exception rather than wrapping every failure in a second
offline error type:

| Exception | Meaning at this boundary |
| --- | --- |
| `LogicalCompilerError` | Base class for active logical-compiler failures |
| `LogicalCompilerValidationError` | Pipeline returned the wrong result type, or its artifact is malformed/noncanonical, source/policy-mismatched, or invalid in coverage, route, residency, or gadget structure |
| `UnsupportedLogicalBackendError` | Selected logical compiler backend is not implemented |
| `LogicalMappingError` | Built-in compiler cannot produce a valid logical placement |
| `LogicalRoutingError` | Built-in compiler cannot produce a valid logical route |
| `TypeError` | Caller supplied the wrong public Python object type |
| `ValueError` | Invalid lowering override, unsupported lowering capability, missing model/profile input, or incompatible architecture capability outside the compiler artifact itself |
| `EvaluationError` | Failure after `evaluate()` has crossed into live runtime execution |

The Level-1 `run_evaluation()` facade translates staged failures to
`EvaluationRunError(stage, code, details)` from `arqsim.api`. Direct Level-2
callers should catch only the owner exception they can meaningfully handle;
catching `LogicalCompilerError` is appropriate when all compiler failures have
the same integration response.

## Minimal trusted compiler adapter

The smallest external adapter can decode a previously generated, trusted v1
document and let ArqSim perform source binding and all Plan validation:

```python
from arqsim.compiler import CompilerPipeline, LogicalCompilationResult
from arqsim.evaluation import ExecutionPolicy, compile_and_lower


class SubmittedLayerCompiler(CompilerPipeline):
    def __init__(self, document: str) -> None:
        self._document = document

    def compile(self, circuit, specification, latency_profile):
        # Decoding checks canonical shape and compilation_hash. The lowering
        # boundary below checks the artifact against these exact authorities.
        return LogicalCompilationResult.from_json(self._document)


compilation, plan = compile_and_lower(
    circuit,
    specification,
    latency_profile,
    ExecutionPolicy(),
    compiler_pipeline=SubmittedLayerCompiler(submitted_json),
    resource_protocol_bindings=resolved_resource_protocol_bindings,
)
```

This is a trusted Python integration seam, not a security sandbox or a general
foreign-team submission protocol. Never accept compiler-reported source
hashes, costs, or mappings without ArqSim's codec and lowering validation.

## v1 limitation: layer-preserving only

Although `compile()` receives the complete circuit, v1 compute units reference
source layer and operation indices. The contract can split a source layer into
capacity/magic batches and preserve mappings/routes across those units, but it
cannot faithfully express a general compiler-selected action DAG, arbitrary
cross-layer regrouping, compiler-inserted SE/checkpoint actions, or exact
within-block schedules for mixed gadget work.

External compilers that require those semantics need a future schema. Do not
encode them in `metadata`, fabricate source layers, or reinterpret
`compute_units`. The current finite-gadget lowerer therefore rejects mixed
Clifford/T units whose internal scheduling cannot be proven from v1.

## Stability and versioning

- The whole-`CompilerPipeline` replacement seam, the two lowering functions,
  and the `evaluate()` handoff are supported advanced Python APIs for 0.2.0.
- `LogicalCompilationResult v1` and `ExecutionPlan v9` are strict wire
  contracts. A semantic wire change requires a new schema-version identifier;
  decoders do not guess or silently migrate unknown versions.
- Individual compiler passes and backend registries remain internal. Their
  signatures may change without defining a new public compiler contract.
- A future general compiler/action-DAG exchange will use a new schema and may
  coexist with v1; it will not silently widen the meaning of v1 fields.
- `EvaluationReport` remains the Level-1 public result. Persisted integrations
  should prefer its self-contained Report-v2 record unless they specifically
  need to exchange one of these advanced offline artifacts.
