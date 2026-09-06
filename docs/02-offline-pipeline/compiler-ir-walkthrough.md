# Compiler IR Walkthrough

Level 2 · Role: guided offline example · Status: current for ArqSim 0.2.0

This guide opens the one-shot API and follows the three immutable offline
artifacts at the compiler boundary. The schemas and their strict codecs remain
authoritative; abbreviated examples below are for orientation. Exact supported
imports, signatures, ownership, and versioning are defined by the normative
[Offline Pipeline API](offline-pipeline-api.md).

## How this fits beneath the common API

The Level-1 user model is deliberately small:

```text
FTCircuit + EvaluationConfig
              |
              v
        run_evaluation()
              |
              v
       EvaluationReport
```

Inside that call, the relevant Level-2 flow is:

```text
EvaluationConfig
  -> ArchitectureSpecification + resolved latency/protocol/policy inputs

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
        |  ArqSim validation + plan lowering
        v
ExecutionPlan v9                         <- last fully offline artifact
        |
        |  evaluate(plan, runtime_components=<live RuntimeComponentSet>)
        v
EvaluationResult                         <- low-level live runtime result
        |
        |  analysis + Report-v2 rendering
        v
EvaluationReport                         <- formal public result
```

`EvaluationResult` and `EvaluationReport` are not alternatives. The former is
the lower-level result of one live runtime execution. The latter is the public,
self-contained result that adds the request, resolved authorities, compiler
artifact, Plan, Trace, analysis, fidelity, footprint, hashes, and strict
serialization.

Run the exact offline example from an installed source checkout:

```bash
python examples/compiler_ir_walkthrough.py
```

The script prints the canonical `to_dict()` records for all three artifacts.
The outer display object is not another schema.

## Toy source and `FTCircuit`

The example input is:

```qasm
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];

h q[0];
x q[1];
cx q[0],q[1];
t q[1];
```

For gate QASM, `load_ft_workload()` uses Qiskit to parse the already synthesized
circuit, converts it to a Qiskit DAG, and iterates `dag.layers()`. It does not
perform FT synthesis or architecture-aware compilation. Here Qiskit derives:

```text
layer 0: H(q0) || X(q1)
layer 1: CX(q0, q1)
layer 2: T(q1)
```

The first two gates may share a dependency layer because they have disjoint
operands. This says that their source dependencies permit overlap; it does not
by itself prove that the selected architecture can execute them in one
physical wave. The CX depends on both, and T follows the CX on `q1`.

The complete canonical `FTCircuit` record is:

```json
{
  "schema_version": "arqsim.ft-workload.v2",
  "representation": "gate",
  "num_qubits": 2,
  "num_clbits": 0,
  "layers": [
    {
      "index": 0,
      "active_qubits": [0, 1],
      "operations": [
        {
          "kind": "gate",
          "name": "h",
          "qubits": [0],
          "classical_bits": [],
          "parameters": []
        },
        {
          "kind": "gate",
          "name": "x",
          "qubits": [1],
          "classical_bits": [],
          "parameters": []
        }
      ]
    },
    {
      "index": 1,
      "active_qubits": [0, 1],
      "operations": [
        {
          "kind": "gate",
          "name": "cx",
          "qubits": [0, 1],
          "classical_bits": [],
          "parameters": []
        }
      ]
    },
    {
      "index": 2,
      "active_qubits": [1],
      "operations": [
        {
          "kind": "gate",
          "name": "t",
          "qubits": [1],
          "classical_bits": [],
          "parameters": []
        }
      ]
    }
  ],
  "semantic_hash": "9924fe63dc21978bf34623fc3efbeb156a0d2d496a83d484da22f7fa68efd7f0",
  "provenance": {
    "example": "compiler-ir-toy"
  }
}
```

The current layer sequence is a conservative dependency contract, not the
complete source DAG: exact predecessor edges are not retained. PBC ingestion is
even more conservative and currently emits sequential layers. A synthesis
adapter may also construct an `FTCircuit` directly, but it must supply valid
layers whose operations do not share quantum or classical operands.

This is sufficient for the layer-preserving v1 compiler. A future general
compiler contract should retain stable operation identities and exact
predecessor edges for whole-program optimization, with layers becoming a
validated derived view rather than the only dependency authority.

The current built-in compiler then performs an additional, implicit packing
step: it splits a source layer by compute-residency capacity and magic-input
capacity/policy, routes each resulting batch, and emits one
`CompiledComputeUnit`. Plan lowering turns that unit—not the source layer
itself—into one `EXECUTE_COMPUTE`. This current block boundary is described in
the [ExecutionPlan Instruction Reference](execution-plan-instruction-reference.md).

## `LogicalCompilationResult v1`

`CompilerPipeline.compile()` receives the complete `FTCircuit`, resolved
`ArchitectureSpecification`, and resolved `OperationLatencyProfile`. It returns
an immutable, architecture-aware offline compilation artifact.

The latency profile is the compiler's read-only cost authority, not another
description of the architecture. The current offline pipeline uses its active
compute-modality gate duration, compute/syndrome protocol timing, and
neutral-atom movement calibration to price compiled units and routes. The same
inputs are reused if a magic-state route is compiled after runtime slot binding.
Resource-arrival distributions, local/link delivery time, Store/Load service,
and classical-reaction time belong mainly to later plan lowering or runtime;
the default offline compiler does not currently optimize against them.

In v1, `LogicalCompilationResult.latency_profile_hash` conservatively binds the
complete profile, even though the default compiler reads only that subset. A
future untrusted compiler exchange should expose an evaluator-owned compilation
cost context and require ArqSim to recompute or verify submitted costs, rather
than trusting compiler-reported durations.

### Read the artifact as decisions plus a receipt

The v1 wire document is self-identifying, but it is not a minimal domain model.
Do not read every top-level field as an equally important compiler decision. A
more useful conceptual view is:

```text
LogicalCompilationResult v1
├── decisions                              <- semantic center
│   └── compute_units[]
│       ├── source work selected for this unit
│       ├── resident-qubit mapping
│       └── compile-time- or dispatch-time-resolved realization
└── receipt / assumptions                  <- validate, replay, explain
    ├── source hashes
    ├── compiler configuration
    ├── magic-consumption assumption
    └── compilation hash
```

The three source fields are hashes, not embedded copies of the circuit,
Architecture Specification, or latency profile. Plan lowering receives those
objects separately and checks the hashes before using the artifact. This is the
equivalent of a manifest or foreign-key check: it prevents a plausible-looking
compilation from being lowered against the wrong source or model.

The following audit separates essential decisions from current-v1 redundancy:

| Record | Current role | Design judgment |
| --- | --- | --- |
| `compute_units` | Ordered compiler decisions consumed by validation and Program lowering | The v1 semantic center |
| source hashes | Bind the detached artifact to its external authorities | Keep, but present in an outer receipt/envelope |
| `compiler_spec` | Records provenance; its routing options are also reused to price MOVE work introduced during lowering | Too broad for that dual role; split cost authority from provenance in v2 |
| `magic_state_consumption` | Seals a compilation assumption and is checked against `ExecutionPolicy` | Keep as an assumption in v1; do not present as the main result |
| `initial_mapping` | Repeats the first full-residency unit mapping, or is empty in deferred-capacity mode | Derivable under current v1 semantics |
| `deferred_capacity_mapping` | Restates `circuit width > compute capacity` | Derivable under current v1 semantics |
| `compilation_hash` | Integrity and lineage | Keep an envelope hash, but v2 should not let diagnostics change semantic identity |

Within a compute unit, `ComputePartition -> ComputeBatch` faithfully records two
different built-in compiler transformations: capacity partitioning followed by
magic-buffer batching. Its wire representation is nevertheless verbose. The
entire partition is copied into every batch, and several fields—partition/batch
counts, operation unions, active/operated qubits, and magic counts—can be
recomputed from the source circuit and unit membership. Likewise, current
`route_hash`, route metrics, and route steps are copied into diagnostic metadata;
the v1 lowerer does not schedule execution from them. The resident mapping,
compile-time-versus-dispatch-time resolution decision, and trusted timing values
do affect lowering.

In implementation names, a compile-time-resolved route is sometimes called an
"eager route." This does **not** mean that runtime executes it immediately. It
means only that compilation already knows the relevant endpoints and can fix
the route decision and estimated routing duration. A dispatch-time-resolved
route instead carries `dispatch_deferred=true`; runtime first binds the concrete
resource token/slot and then invokes the realizer. A zero-movement route can be
compile-time-resolved while containing no route steps.

Consequently, v1 keeps its strict wire schema for compatibility, while the
documentation puts `compute_units` first and treats the rest as a collapsed
receipt. A future general compiler result should be a coexisting v2 rather than
an accumulation of more optional v1 fields. Its irreducible compiled block asks
only:

```text
CompiledBlock
├── source_operation_refs                 # what source work is implemented?
├── predecessors / order                  # when may it execute?
├── entry and exit residency              # where is logical state?
└── realization
    ├── selected protocol/actions, or
    └── typed unresolved runtime choice
```

Partitioning, batching labels, qubit sets, resource demand, counts, metrics, and
other diagnostics should be derived where possible or placed in an optional
receipt. This is also the shape needed for future cross-layer schedules; the
current `compute_units` are the center of the layer-preserving v1 artifact, not
a permanent claim that every compiler must organize output that way.

For reference, the exact v1 wire fields decompose as follows. This is the codec
shape, not the recommended first-reading order:

```text
LogicalCompilationResult
├── source hashes
│   ├── circuit_hash
│   ├── architecture_hash
│   └── latency_profile_hash
├── compiler_spec and compiler_hash
├── magic_state_consumption
├── initial_mapping
├── deferred_capacity_mapping
├── compute_units[]
│   ├── source partition/batch
│   │   ├── source layer and operation indices
│   │   ├── operated qubits
│   │   └── magic-state demand
│   ├── resident logical-qubit -> canonical compute-slot mapping
│   └── route
│       ├── compile-time route receipt, or dispatch_deferred
│       ├── duration components
│       ├── syndrome protocol timing
│       └── route metrics/hash
└── compilation_hash
```

For the toy circuit, the following is an **abridged inspection view**, not a
canonical or directly parseable wire document:

```text
{
  "schema_version": "arqsim.logical-compilation-result.v1",
  "source": {
    "circuit_hash": "9924fe...",
    "architecture_hash": "38832e...",
    "latency_profile_hash": "71b2ae..."
  },
  "compiler": {
    "instruction_set": "arqsim.hqisa.v1",
    "issue_policy": "conservative_layers",
    "mapping": {"backend": "sabre_na", "options": {"seed": 42}},
    "routing": {
      "backend": "powermove_na",
      "options": {"distance_metric": "euclidean"}
    }
  },
  "magic_state_consumption": "bulk_wave",
  "initial_mapping": {
    "0": "na_node/na_compute/compute_region/slot_0",
    "1": "na_node/na_compute/compute_region/slot_1"
  },
  "deferred_capacity_mapping": false,
  "compute_units": [
    {
      "source": {
        "layer": 0,
        "operation_indices": [0, 1],
        "operated_qubits": [0, 1],
        "magic_count": 0
      },
      "mapping": {"0": ".../slot_0", "1": ".../slot_1"},
      "route": {
        "dispatch_deferred": false,
        "route_steps": [],
        "duration_components_s": {
          "primitive_service": 0.002,
          "compiler_routing": 0.0
        }
      }
    },
    {
      "source": {
        "layer": 1,
        "operation_indices": [0],
        "operated_qubits": [0, 1],
        "magic_count": 0
      },
      "mapping": {"0": ".../slot_0", "1": ".../slot_1"},
      "route": {
        "dispatch_deferred": false,
        "route_steps": [{"kind": "na_round_trip_movement", "...": "..."}],
        "duration_components_s": {
          "primitive_service": 0.002,
          "compiler_routing": 0.0001982418829187267
        }
      }
    },
    {
      "source": {
        "layer": 2,
        "operation_indices": [0],
        "operated_qubits": [1],
        "magic_count": 1
      },
      "mapping": {"0": ".../slot_0", "1": ".../slot_1"},
      "route": {
        "dispatch_deferred": true,
        "route_steps": [],
        "duration_components_s": {
          "primitive_service": 0.002,
          "compiler_routing": 0.0
        }
      }
    }
  ],
  "compilation_hash": "9ca48f..."
}
```

The first unit bundles the source-independent H and X under the current
reference compiler. Their common source layer permits overlap, while the
compiler's aggregate unit duration—not the layer label—is the execution-time
claim. The second unit records a compile-time-resolved CX route. The T unit
knows its logical data mapping and magic-state demand but not which concrete
runtime token/slot will satisfy that demand, so its route is explicitly
deferred. Duration components are offline model receipts, not actual runtime
start/end timestamps.

Replacing the whole pipeline means implementing
`CompilerPipeline.compile(circuit, specification, latency_profile)` and
returning this strict type. For trusted artifact exchange, an external compiler
may instead serialize this v1 document and ArqSim can parse and lower it. The
external compiler therefore needs the exact `FTCircuit`, Architecture
Specification, and timing authority—not only the circuit.

This v1 exchange remains source-layer-indexed and layer-preserving. It is not
the future fairness boundary for arbitrary team compilers: it cannot express a
general action DAG or cross-layer placement continuity, and it contains timing
receipts supplied by the compiler side. A future untrusted exchange should
submit structural decisions that ArqSim validates and prices independently.

## Compilation-result lowering and `ExecutionPlan`

`lower_compilation_result()` performs **plan lowering**. It is more than
renaming logical gates into opcodes:

1. Validate source hashes, operation coverage, policy agreement, mapping,
   routes, and architecture capacities.
2. Project compiled compute units and route decisions into a finite
   `ProgramDAG` of architecture/runtime-ISA instructions.
3. Project architecture resource protocols into a recurrent `ResourceDAG`.
4. Materialize the runtime buffer/engine inventory and initial logical-qubit
   locations from the Architecture Specification.
5. Encode any unresolved choice as a typed deferred-dispatch recipe.
6. Bind the execution policy and runtime-component manifest into a hashed
   `ExecutionPlan`.

### Read the Plan as a closed runtime contract

Unlike `LogicalCompilationResult`, the Plan is intentionally broad: the Event
Engine can execute it without reopening the Architecture Specification or
latency profile. Its first-reading structure is:

```text
ExecutionPlan v9
├── executable model
│   ├── program_dag                  # finite, one-shot Program work
│   ├── resource_dag                 # recurrent production/delivery templates
│   └── architectural_state
│       ├── buffers
│       ├── engines
│       └── initial logical locations
├── execution contract
│   ├── policy
│   └── runtime_components           # planned manifest wire receipt
└── receipt
    ├── circuit / architecture / latency hashes
    ├── provenance
    └── plan_hash
```

Review `architectural_state` first, then one ordinary and one deferred Program
instruction, then one Resource process. Hashes, provenance, and the detailed
runtime-component manifest can remain collapsed during a structural review.

Each `ArchitectureInstruction` answers: when may it run (`id`, predecessors),
what work does it represent (`opcode`, qubits, duration), what state and
contention does it claim (buffers, engines, required/completion locations), and
what remains unresolved (`deferred_dispatch`). Injection recipes and
continuation templates encode possible runtime-activated gadget children. Each
`ResourceProcess` is instead a recurrent factory or delivery template; named
buffers provide the producer/consumer connection between processes.

The complete current field glossary, opcode-applicability matrix, overloaded
`engines` terminology, and recipe/template distinction are in the
[ExecutionPlan Instruction Reference](execution-plan-instruction-reference.md).

### Who supplies the lowering inputs?

The low-level signature is conceptually:

```text
lower_compilation_result(
    circuit,
    architecture_specification,
    latency_profile,
    execution_policy,
    logical_compilation_result,
    *,
    resource_protocol_bindings=None,
    resource_delivery_channels=None,
    runtime_components=None,
)
```

This does not create a second normal-user configuration step. In the root
`run_evaluation()` path, ArqSim passes the original resolved authorities and
automatically derives the remaining inputs:

| Input | Source in the one-shot API | User configures again after compilation? |
| --- | --- | --- |
| circuit, Architecture Specification, latency profile | Existing workload and resolved `EvaluationConfig` | No |
| execution policy | Existing `EvaluationConfig.execution_policy` | No |
| resource-protocol bindings | Resolved from the Architecture Specification and latency/profile authorities | No |
| resource-delivery concurrency | Derived from installed architecture buffers | No |
| runtime-component manifest | Built automatically from the selected built-in runtime | No |

`ExecutionPolicy` is an additional formal lowering input relative to the
three-argument compiler interface, but it was selected before compilation. Its
injection-lowering mode chooses black-box versus finite gadget recipes; its
magic-state batching must agree with the compiler artifact. Other fields mainly
govern runtime observation and execution limits.

There is no v1 external lowering plugin and no separate lowering specification.
The `compiler_spec` embedded in the compilation does not select a different
lowerer: it records the compiler and currently supplies routing options when
ArqSim prices MOVE instructions introduced during plan lowering. External
compiler decisions enter through `LogicalCompilationResult`; ArqSim owns the
validation, architecture/runtime-ISA projection, resource graph, inventory, and
authoritative Plan construction.

The three optional keyword arguments are lower-level integration seams, not
root-facade requirements. A direct advanced caller may provide already-resolved
protocol bindings, an explicit delivery-concurrency override, or a compatible
planned runtime-manifest wire record. This is not the live Python component
set used by `evaluate()`. The one-shot API resolves both sides automatically.

The opcodes are ArqSim's logical system/runtime ISA. They are not calibrated
physical pulses or a physical-gate netlist. Source logical operations remain
available through instruction lineage/metadata, while an instruction such as
`EXECUTE_COMPUTE` may represent a compiled batch.

The following is an **abridged inspection view** of the toy Plan, not a
canonical or directly parseable wire document:

```text
{
  "schema_version": "arqsim.execution-plan.v9",
  "source": {"circuit_hash": "9924fe..."},
  "architecture_hash": "38832e...",
  "latency_profile_hash": "71b2ae...",
  "policy": {
    "magic_state_consumption": "bulk_wave",
    "runtime_injection_mode": "black_box",
    "trace_level": "full",
    "seed": 0
  },
  "program_dag": {
    "instructions": [
      {"id": 0, "opcode": "EXECUTE_COMPUTE", "predecessors": []},
      {"id": 1, "opcode": "FENCE", "predecessors": [0]},
      {"id": 2, "opcode": "EXECUTE_COMPUTE", "predecessors": [1]},
      {"id": 3, "opcode": "FENCE", "predecessors": [2]},
      {
        "id": 4,
        "opcode": "EXECUTE_COMPUTE",
        "predecessors": [3],
        "qubits": [1],
        "consumes": {"magic_compute": 1},
        "deferred_dispatch": {
          "schema_version": "arqsim.deferred-dispatch-recipe.v1",
          "kind": "joint_magic_route",
          "operation_indices": [0],
          "data_mapping": {"0": ".../slot_0", "1": ".../slot_1"}
        }
      },
      {"id": 5, "opcode": "FENCE", "predecessors": [4]}
    ]
  },
  "resource_dag": {
    "fill_policy": "greedy_fill_to_capacity",
    "processes": [
      {
        "id": "prepare_magic",
        "opcode": "PREPARE_MAGIC_STATE",
        "produces": {"msf_output": 1},
        "engines": {"msf:na_node/na_msf": 1},
        "arrival_distribution": {
          "kind": "exponential",
          "mean_interval_s": 2.187680929963632
        },
        "parallelism": 1,
        "output_overflow_policy": "discard_excess"
      },
      {
        "id": "deliver_magic_local",
        "opcode": "MOVE_QUBITS",
        "consumes": {"msf_output": 1},
        "produces": {"magic_compute": 1},
        "forwards": {"msf_output": "magic_compute"},
        "engines": {"resource_move": 1},
        "dispatch_policy": "eager_available",
        "deferred_dispatch": {"kind": "state_bound_resource_move"}
      }
    ]
  },
  "architectural_state": {
    "buffers": [
      {
        "id": "magic_compute",
        "capacity": 1,
        "token_kind": "magic_state",
        "module": "na_node/na_compute"
      },
      {
        "id": "msf_output",
        "capacity": 1,
        "token_kind": "magic_state",
        "module": "na_node/na_msf"
      }
    ],
    "engines": [
      {"id": "compute:na_node/na_compute", "capacity": 1},
      {"id": "msf:na_node/na_msf", "capacity": 1},
      {"id": "resource_move", "capacity": 1}
    ],
    "initial_locations": {
      "q:0": "na_node/na_compute",
      "q:1": "na_node/na_compute"
    }
  },
  "runtime_components": {"...": "..."},
  "plan_hash": "37b08f..."
}
```

The canonical Plan contains complete claims, durations, metadata, buffer and
engine records, resource arrival distributions, component descriptors, and
hashes; the example above intentionally omits those details. Use the executable
example for the exact wire document.

### How the Program and Resource planes meet

There is deliberately no fixed predecessor edge from a particular Resource
instance to the T instruction. The Plan instead binds both planes symbolically
through the same installed `ArchitectureState` names:

```text
Resource template: prepare_magic
  produces msf_output
           |
           v
Resource template: deliver_magic_local
  consumes msf_output
  forwards the same token to magic_compute
           |
           v
Program instruction: T
  consumes magic_compute
```

The two named buffers above are declared once under `architectural_state`, with
their capacities, token kinds, slots, and architectural owners. Both Program
instructions and Resource processes also claim engines from the same installed
engine inventory. Plan validation rejects an unknown buffer/engine, a claim
above capacity, or a forwarding edge whose token kind/count changes.

This is already a cross-plane binding; what remains unknown offline is the
concrete token identity and slot. A fixed graph edge would be the wrong model,
because the T instruction may consume whichever compatible token is available
from any runtime instance of the producer.

Recurrence is a semantic property of every `ResourceProcess`, not a
`recurrent=true` field or a cycle in the serialized graph. `ResourceDAG` stores
one immutable process template. Runtime maintains an instance counter and,
while inputs, output slots, and engines are feasible and
`inflight < parallelism`, lazily dispatches instances 0, 1, 2, and so on. The
arrival distribution is sampled per instance; a full destination buffer blocks
further fill, and the program-completion horizon stops resource production.
`fill_policy="greedy_fill_to_capacity"` and each process's dispatch,
parallelism, arrival, and overflow fields define this streaming behavior.

The toy record above uses black-box injection. Finite injection keeps the same
cross-plane magic-buffer binding and additionally places the T parent recipe and
its measurement, reaction, and conditional-correction templates in the Program
instruction.

## Exact offline/runtime boundary

Construction of the `ExecutionPlan` is still offline. It specifies everything
the runtime is allowed to execute, but it need not know every realized fact.
Depending on the Plan, the following may remain unresolved until runtime:

- concrete resource-token identity and selected buffer slot;
- an endpoint-dependent deferred route;
- sampled resource-arrival intervals;
- instruction dispatch/start/completion timestamps after contention and stalls;
- measurement outcomes and conditional gadget continuations.

Runtime begins at `evaluate(plan, runtime_components=<live
RuntimeComponentSet>)`. The Event Engine
creates and mutates `ArchitectureState`, asks the realizer to bind deferred
operands, asks the scheduler to order ready candidates, advances the clock,
samples outcomes, and emits `ExecutionTrace` plus the low-level
`EvaluationResult`.
