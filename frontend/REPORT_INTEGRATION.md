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

The benchmark catalog declares the available `representations` for each
workload. General evaluations prefer PBC for Profiles 1.2 and 2.2 when available
and otherwise use Clifford+T; other profiles require Clifford+T. Selection is
made before execution from catalog facts, with no retry under different
semantics after a failed evaluation. An incompatible representation or a backend
error is shown with the affected workload and architecture.

General evaluation is the browser default. Explicitly selected presets define
the comparison; with none selected, the currently previewed canonical profile
is used consistently for the request and result selector.

## Prefix evaluation scope

General browser requests default to `preview_max_layers: 12`; the setup offers
12, 24, 48, 96, or a full workload. Omission of `preview_max_layers` preserves
the existing full-evaluation API behavior. The finite-injection demo always
omits this field and retains its full frozen workload.

The HTTP adapter takes the first N layers of the resolved representation,
preserving the source qubit and classical-bit widths, then compiles and evaluates
that input normally. Architecture sizing, latency, fidelity, and all statistics
belong to this prefix. This is a new evaluation of a smaller input, not a paused
full run; compiler decisions and its schedule may differ from a full run.
Equal PBC and Clifford+T layer counts do not represent equivalent algorithm work.

Scope authority is the returned workload's `provenance.evaluation_scope` receipt:
`kind: "prefix_preview"`, `requested_max_layers`, `source_workload_hash`,
`source_layer_count`, `source_operation_count`, `evaluated_layer_count`,
`evaluated_operation_count`, and `truncated`. The adapter validates this receipt,
including counts against the evaluated circuit, and supplies the persistent
result banner and per-point comparison labels. Current selector state never
defines the scope of an existing report. Absent scope metadata denotes a full
workload. A prefix limit that covers the whole input still records a preview
receipt with `truncated: false`.

Request-keyed caches include the prefix limit. Changing evaluation scope clears
the visible result selection. Prefix timelines start at the requested layer
limit, while full-workload timelines initially show 12 layers. The independent
timeline display and event caps only limit presentation of the returned report;
evaluating additional prefix layers requires a new request. HTTP preview limits
must be integers from 1 through 256. Neither time nor fidelity is extrapolated
to the full source.

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

Every execution bar has one primary display anchor: a Module or Submodule
present in the returned Architecture Specification. The adapter resolves this
anchor from typed Plan/Trace ownership, without checking a preset profile ID:

1. For movement or teleportation, use a recorded source endpoint or consumed
   payload buffer when the evidence identifies one unambiguously.
2. Use a unique claimed, targeted compute region, then an engine's declared
   Module/Submodule owner. Multiple owners use a stable canonical participant.
3. Use a single target Module, or a stable canonical participant from known
   engines, buffers and recorded locations.
4. For delay-only Classical Reaction without its own ownership evidence, follow
   its recorded causal parent to the same display anchor.

Missing or invalid ownership evidence fails explicitly. The adapter does not
invent a Teleportation Module, Classical Decoder, pairwise transfer row or
unbound execution row. An existing report without enough ownership metadata
cannot be drawn by guessing a location. FENCE remains a control dependency and
has no hardware execution bar. Unbound request/control buffers remain explicitly
unbound Architecture State; they do not become Modules.

A primary anchor is a presentation convention, not a claim that all physical
work occurs inside it. A transfer involving several participants appears once,
with its real participating owners retained in the tooltip. Anchoring a resource
transfer at its source buffer does not make that passive buffer a transfer
engine. Delay-only reaction retains its empty engine claim. Row selection never
changes scheduler claims, timing, token flow, utilization or report accounting.

For the Profile 2.3 reference, resource teleportation is anchored at the MSF
output buffer, Store/Load at the declared Store/Load Buffer, and intra-module
movement at the claimed Compute Module. Interconnect-hosted Submodules appear
beneath their real owning Modules, such as the link's Bell Engine.
Only owners with activity receive execution rows. Idle owners remain visible in
the Architecture Specification and report model. Overlapping events are packed
into sub-bands beneath their owner; a sub-band is not another Module.

The T source-instruction host remains on its Compute anchor. When collapsed,
it shows one execution bar from dispatch to the terminal child. When expanded,
CX, measurement, reaction and optional logical-S children retain their individual
Trace identities and owners. A delay-only reaction follows its causal parent's
anchor; declared reaction hardware uses its own owner. The ready-to-dispatch
ghost remains a separate non-occupying wait interval.

Reaction hover cards show the measurement bit separately from the committed
continuation receipt. An `activate` receipt with no child work IDs can close one
invocation while sibling invocations keep the source open; it does not mean
the source instruction completed. Consumers needing full claims or raw lineage
inspect the canonical Plan/Trace artifacts.

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
reference reaction-latency values plus provenance. The browser offers this
acceptance demo as an explicit opt-in for ArqSim Timeline Demo and Profile 2.3. The
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
- Give each execution event one real Module/Submodule display anchor from typed
  Plan/Trace ownership. Retain other participants without duplicating event bars
  or inventing hardware rows; reject missing or contradictory ownership evidence.
- Project waiting ghosts from Program ready/dispatch facts and buffer-state
  tracks only from Plan metadata plus recorded snapshots. Retain typed ledger
  milestones without adding accounting events or requiring a tick visualization.
- Treat engine claims as contention facts. Resolving a display anchor must not
  add, remove or redistribute those claims, timing or resource accounting.
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
npm run test:previews
npm run typecheck
npm run lint
npm run build
```

The adapter tests consume codec-validated static and dynamic-T Report-v2
fixtures from the ArqSim monorepo root. Ownership regressions check real owners,
one bar per runtime event, unchanged report facts, both transfer directions,
causal reaction ownership and stable row counts for pairwise interactions.
Server smoke tests cover Profile v3, native v2 output, explicit v1 compatibility
output and prefix scope receipts. `npm run test:previews` runs real 12-layer
previews for every bundled benchmark and canonical preset, then checks their
frontend projections against the returned reports. It requires the server's
Python dependencies and is also run by CI.
