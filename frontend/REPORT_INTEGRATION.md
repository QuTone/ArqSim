# Report integration

This document defines the public contract between the ArqSim evaluator, its
thin HTTP adapter, and the browser application. The frontend consumes
versioned evaluation reports; it does not implement a second compiler,
scheduler, timing model, or resource estimator.

## Runtime flow

```text
React UI
  -> EvaluationRequest
  -> POST /evaluate-v2
  -> FastAPI adapter
  -> arqsim.run_evaluation(FTCircuit, EvaluationConfig)
  -> arqsim.evaluation-report.v2
  -> strict report adapter
  -> version-neutral EvaluationReportModel
  -> pure presentation ViewModels
```

The evaluator is authoritative for routing, timing, footprint, occupancy, and
fidelity. The frontend validates and projects those facts into presentation
models. Executable timeline spans come from typed facts in
the canonical Trace-v4 ledger. Buffer-state presentation combines Plan-v9
ownership/capacity with evaluator-recorded `discrete_time_log` snapshots; it is
not a browser-side execution replay.

## HTTP surface

| Method | Path | Role |
| --- | --- | --- |
| `GET` | `/benchmarks` | Discover bundled workloads. |
| `GET` | `/architecture-profiles` | Read canonical capacity-free Profile-v3 documents. |
| `POST` | `/evaluate-v2` | Native Report-v2 evaluation boundary used by the UI. |
| `POST` | `/evaluate` | Frozen Report-v1 compatibility output during the deprecation window. |

The browser submits new evaluations to `/evaluate-v2`. There is no
frontend-specific result schema: the HTTP service returns the core report
contract directly.

## Report versions

Report v2 is the native integration boundary. The HTTP adapter renders Report
v1 only through the core's explicit one-way compatibility renderer. The shared
frontend adapter reduces v1 and v2 wire documents to the same
`EvaluationReportModel`; React state and components do not retain a raw report
document.

Report v1 is output-only compatibility data. It cannot flow back into
compilation or execution. Consumers integrating for the first time should use
Report v2 and `/evaluate-v2`.

## Typed program and runtime work

For Report v2, the normalized model retains the typed Plan-v9 Program-DAG
instruction/implementation-recipe subset and Trace-v4 `program_lineage`,
`measurements`, and `continuation` receipts. Plan-v9 continuation templates
are validated as frozen work definitions, but presentation timing always comes
from realized Trace events. Recipe cards show causal structure without
reconstructing durations; Dynamic Work and timeline views show actual timing.

A conditional logical correction is identified by
`ProgramWorkLineage.step=correction`. Its measurement is reached through the
typed `parent_event_id` chain and scoped by `recipe_members`, rather than
inferred from an opcode label or display string. A shared entangle or
measurement event may belong to several recipe invocations; the browser keeps
that runtime event once. The collapsed T host is keyed by
`source_instruction_id`, not by recipe invocation, so one source instruction
has one persistent presentation identity even when it owns several recipe
members. The host is a presentation identity, not an additional accounting
event. Its execution interval starts at dispatch and ends with its terminal
reaction or correction. Logical ready-to-dispatch time is a separate queue-wait
interval and is never added to execution duration. Expanding or collapsing the
host changes only presentation, not event identity or accounting.

Timeline row identity is an architecture/runtime-level logical-operation locus,
not an opcode, resource-state type, recipe step, process ID, Program/Resource
plane, or physical-pulse owner. The adapter resolves that locus in this frozen
order:

1. virtual Classical Decoder for classical control;
2. an explicit target interconnect;
3. an inter-module transfer locus;
4. the exact submodule owned by a named engine;
5. one unambiguous target module; and
6. an explicit control/resource fallback.

An engine claim is contention evidence. It selects an exact submodule only at
step 4 and cannot override an explicit interconnect or inter-module transfer.
The timeline displays operation loci with visible activity and their architecture
owner headers. Active submodules such as a factory engine appear beneath their
parent module. Idle modules have no execution row; their complete inventory and
memory ownership remain in the Architecture Specification and report model.
Passive slots and buffers remain Architecture State rather than becoming
operation loci.
Overlapping events are packed into sub-bands beneath the resolved locus; a
sub-band is not another module. Tooltip projection intentionally keeps only the
semantic label, execution timing, and immediately relevant wait/branch detail.
Reaction hover cards show the measurement bit separately from the committed
continuation receipt. An `activate` receipt with no child work IDs can close one
invocation while sibling invocations keep the source open; it does not mean
the source instruction completed.
Consumers needing internal IDs or raw lineage inspect the canonical Plan/Trace
artifacts.

The T source-instruction host is anchored on its Compute locus. When collapsed,
it shows one execution bar from dispatch to the terminal child. When expanded,
Compute-side CX, measurement, and optional logical-S children are revealed in
place, while reaction is revealed on Classical Decoder. The ready-to-dispatch
ghost remains a separate non-occupying wait interval.

Program events may also carry a hatched ready-to-dispatch waiting ghost. The
ghost is non-occupying demand, not an execution span or inferred module-idle
time; reported wait reasons describe encountered blockers rather than a
browser-derived duration breakdown. The adapter retains typed Trace-ledger
production, delivery, consumption, and measurement milestone facts. These are
not additional accounting events, and the current UI does not render them as
timeline ticks.

Resource-producer backpressure is deliberately separate from Program waiting.
The adapter projects a backpressure span only when Plan-declared output demand,
recorded buffer capacity/state, and the process's realized active-instance
count together prove that output saturation stopped otherwise available process
capacity. Ordinary producer idle time remains blank.

The optional **Architecture State** section contains one compact track per
reported buffer. Track ownership and capacity come only from the Plan, while
piecewise state segments come only from `discrete_time_log` snapshots. Missing
snapshots remain missing: the adapter and React components do not replay token
flow to reconstruct state, parse ownership from IDs, or infer same-time
intermediate states. `pending_incoming` is rendered as **Reserved**, because it
is a committed destination slot for in-flight production rather than a ready
token.
The section is collapsible. The result page provides a dedicated timeline tab
with a shared time axis and zoom control. Default hover cards expose a small
semantic summary; the canonical report remains the inspection surface for
wire-level details.

## Explicit finite-injection demo policy

`src/services/evaluationPresets.ts` owns the finite-injection request helper:
full trace, finite-state injection v1, seed 0, canonical fidelity, and the
reference reaction-latency values plus provenance. The browser initially
selects this acceptance demo for ArqSim Timeline Demo and Profile 2.3. The
normal minimal request does not call this helper and preserves the core
`black_box` semantics with `canonical_reference_v1` fidelity; only explicit
`fidelity_profile: null` disables fidelity. The helper accepts no arguments and
fixes the workload/profile pair, ignoring other experiment and device
overrides. Browser runs use the exploratory workflow identity
`frontend:finite_t_injection_demo_v1:arqsim_timeline_demo__2.3`.

The timeline-demo/Profile-2.3 run uses the same public policy as the frozen
System Case, but it neither claims the System Case workflow ID nor promises a
byte-identical reference report.

## Integration rules

- Accept only explicitly supported schema versions at the HTTP boundary.
- Keep raw wire documents out of React state after adaptation.
- Treat report hashes, evaluator results, and trace lineage as read-only.
- Add presentation fields by extending the version-neutral model and its
  adapter tests; do not derive new execution facts in components.
- Derive displayed architecture-locus rows from typed Plan/Trace ownership
  using the frozen locus priority, waiting ghosts from Program ready/dispatch
  facts, and buffer-state tracks only from Plan metadata plus recorded snapshots.
  Retain typed ledger milestone facts in the adapter without adding accounting
  events or requiring a tick visualization.
- Treat engine claims as contention evidence, never as authority to override
  an explicit interconnect or transfer locus.
- Keep a T host's source-instruction identity on Compute; measure execution
  dispatch-to-terminal and queue wait ready-to-dispatch as separate intervals.
- Keep default tooltips minimal; wire IDs and full provenance belong in the
  canonical report inspection surface.
- Keep experimental request policies opt-in and record their provenance.

## Verification

From `frontend/`:

```bash
npm run test:adapter
npm run test:server
npm run typecheck
npm run lint
npm run build
```

The adapter tests consume codec-validated static and dynamic-T Report-v2
fixtures from the ArqSim monorepo root. Server smoke tests cover Profile v3,
native v2 output, and explicit v1 compatibility output.
