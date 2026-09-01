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
  -> heteqsys.run_evaluation(FTCircuit, EvaluationConfig)
  -> arqsim.evaluation-report.v2
  -> strict report adapter
  -> version-neutral EvaluationReportModel
  -> pure presentation ViewModels
```

The evaluator is authoritative for routing, timing, footprint, occupancy, and
fidelity. The frontend validates and projects those facts into presentation
models. Timeline spans come only from completion transitions in the canonical
Trace-v3 ledger.

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

For Report v2, the normalized model retains the typed Program-v6
instruction/implementation-recipe subset and Trace-v3 `program_lineage`,
`measurements`, and `continuation` receipts. The Output Program, Dynamic Work,
and timeline views use those fields directly.

A conditional logical correction is identified by
`ProgramWorkLineage.step=correction`. Its measurement is joined through the
typed recipe invocation and source instruction, rather than inferred from an
opcode label or display string. Presentation components must preserve that
lineage instead of reconstructing runtime semantics.

## Explicit finite-injection demo policy

`src/services/evaluationPresets.ts` owns the opt-in finite-injection request
helper: full trace, finite-state injection v1, seed 5, canonical fidelity, and
the reference reaction-latency values plus provenance. The normal minimal
request does not call this helper, so core `black_box` semantics and disabled
fidelity remain the application default. The helper accepts a program ID and
derives an exploratory workflow identity. Browser runs use the
`frontend:finite_t_injection_demo_v1:<program>__<profile>` namespace.

The timeline-demo/Profile-2.3 run uses the same public policy as the frozen
System Case, but it neither claims the System Case workflow ID nor promises a
byte-identical reference report.

## Integration rules

- Accept only explicitly supported schema versions at the HTTP boundary.
- Keep raw wire documents out of React state after adaptation.
- Treat report hashes, evaluator results, and trace lineage as read-only.
- Add presentation fields by extending the version-neutral model and its
  adapter tests; do not derive new execution facts in components.
- Keep experimental request policies opt-in and record their provenance.

## Verification

From `frontend/`:

```bash
npm run test:adapter
npm run test:backend
npx tsc --noEmit
npm run lint
npm run build
```

The adapter tests consume codec-validated static and dynamic-T Report-v2
fixtures from the ArqSim monorepo root. Backend smoke tests cover Profile v3,
native v2 output, and explicit v1 compatibility output.
