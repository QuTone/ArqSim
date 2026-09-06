# ArqSim frontend

Interactive frontend for the [ArqSim core](https://github.com/QuTone/ArqSim).
It lets you configure heterogeneous quantum architectures, run resource
estimation against real benchmarks, and inspect timelines, space treemaps, and
cross-architecture comparisons. It lives under `frontend/` in the ArqSim
monorepo.

## Stack

- **Frontend**: Vite + React + TypeScript + Tailwind + shadcn-ui
- **Server**: thin FastAPI adapter ([`../server/`](../server/)) that calls the public `arqsim.run_evaluation` facade and exposes `/benchmarks`, canonical Profile v3, native `/evaluate-v2`, and an explicit v1 compatibility route.

## Repository layout

```
ArqSim/
├── frontend/
│   ├── src/                  # React app
│   │   ├── components/       # canvas, configuration, reports, and timeline
│   │   ├── services/api.ts   # HTTP client
│   │   ├── services/reportAdapter.ts # report v1/v2 → ViewModels
│   │   └── types/evaluationReport.ts # report/view contracts
│   └── REPORT_INTEGRATION.md # public Report-v2 integration contract
└── server/                   # thin FastAPI adapter
    ├── src/api.py            # FTCircuit/config → versioned reports
    ├── benchmark/             # QASM workload catalog and provenance
    ├── requirements.txt       # editable core + FastAPI/uvicorn
    └── setup.sh
```

The profile catalog consumes canonical `arqsim.architecture-profile.v3`.
The browser accepts both `arqsim.evaluation-report.v1` and the frozen
`arqsim.evaluation-report.v2` topology. Both are reduced at the HTTP boundary
to one version-neutral frontend model; React state, caches, comparisons, and
components never retain a raw v1 document. For v2, executable timeline spans
are projected from typed Trace-v4 ledger facts. Architecture
buffer tracks combine only Plan-v9 ownership/capacity with the evaluator's
`discrete_time_log` snapshots. The browser does not validate hashes, replay
execution, fill missing state, or recompute evaluator-owned facts—those remain
backend authorities.

The UI posts new work to `/evaluate-v2`. During the compatibility window,
`/evaluate` explicitly renders Report v1; it never inherits a report version
implicitly from the core default. The shared adapter still accepts v1 for
stored or compatibility responses.

## Local development

This frontend and its FastAPI adapter are a local research/development UI. The
commands below bind the API to loopback and are not a production deployment
recipe.

### Server (port 8002)

In terminal 1:

```bash
cd /path/to/ArqSim/server
./setup.sh                     # creates ./venv, installs requirements (incl. -e ..)
source venv/bin/activate
uvicorn api:app --app-dir src --host 127.0.0.1 --port 8002 --reload
```

You can also reuse a virtual environment from the monorepo root if it already
contains the same dependencies; the server imports `arqsim` either way.

Smoke test:

```bash
curl http://localhost:8002/             # health + canonical report schema
curl http://localhost:8002/benchmarks   # available benchmark list
```

### Frontend (port 5174)

In terminal 2:

```bash
cd /path/to/ArqSim/frontend
npm install
npm run dev                    # http://localhost:5174
```

Set `VITE_API_URL` to use a remote backend. When it is omitted, requests use
the current origin; the Vite development server proxies the API paths to
`http://localhost:8002`.

## Build

```bash
npm run build                  # production bundle into dist/
npm run test:adapter           # strict Report-v2 adapter contract tests
npm run test:server            # v2/v1/profile route contract smoke
npm run preview                # serve dist/ locally
```

The adapter test reads the codec-validated static and dynamic-T fixtures from
the monorepo's `tests/fixtures/report_v2/` directory and the
finite-runtime-injection System Case reference. Refresh the codec fixtures only
through ArqSim's `python -m tests.generate_report_v2_fixtures --update`
workflow. The frozen System Case has no in-place regeneration command: a
semantic change creates a successor case instead of overwriting its evidence.

## Finite-injection demo

The default evaluation path uses `black_box` runtime semantics and the core
`canonical_reference_v1` fidelity preset. Fidelity is disabled only by an
explicit opt-out. To run the finite-injection vertical-slice demo:

1. Select **ArqSim Timeline Demo** and Architecture Profile **2.3** (both are
   the initial UI choices).
2. Confirm that **Experiment Setup → Runtime semantics** shows **Finite T
   injection demo · Profile 2.3**; it is the browser's initial acceptance
   workflow, while the core API default remains black-box with
   `canonical_reference_v1` fidelity.
3. Run the evaluation, then inspect **Program / Dynamic Work**, **Timeline
   Trace**, and **Resource Estimates → Fidelity**.

That preset sends full Trace v4, seed 0, the reference reaction-latency values with
their provenance, and `canonical_reference_v1` fidelity. The two T operations
deterministically exercise both outcomes: one path materializes classical
reaction plus logical-S correction, while the other completes after reaction.
The acceptance preset rejects any other workload/profile selection and ignores
the other experiment and device overrides, so its workflow ID always denotes
the same request semantics.
The browser reads lineage, measurements, continuation receipts, and correction
identity from typed Report-v2/Trace-v4 fields; it does not reconstruct the
gadget from display labels.
The Program view preserves every source T instruction as a logical parent over
its realized CX, MZ, classical-reaction, and optional logical-S children. The
timeline is an architecture/runtime-level view of logical-operation loci, not
a physical-pulse trace. Displayed operation loci and their owner headers come
from the resolved Architecture Specification. An active engine submodule is
nested beneath its owning module; idle modules have no execution row. The full
module inventory and memory ownership remain in the canonical report. Passive
buffers and slots are state, so they appear in **Architecture State** instead
of becoming execution rows.

The locus rule is deterministic: Classical Decoder, explicit interconnect,
multi-module transfer, exact engine-owned submodule, single module, then an
explicit fallback. Engine claims remain contention evidence; they cannot move
an operation away from an explicit link or transfer locus. Event kind controls
color, not row identity. Concurrent events use unlabeled sub-bands under one
owner label, so one architecture module never appears as several fake modules.

In the collapsed timeline, a T host stays on NA Compute and represents the
source instruction from dispatch through its terminal child. Program-ready to
dispatch time is shown separately as queue wait and is never included in T
execution duration or module utilization. Expanding the host reveals its
implementation children in place; classical reaction appears on the virtual
Classical Decoder row. A shared child remains one timeline/accounting event
even when several recipe invocations reference it. Default hover cards are
deliberately small—semantic label, realized timing, and only the immediately
relevant wait or branch fact. Wire IDs and full lineage remain in the canonical
Report-v2 artifact.

The adapter retains typed Trace-ledger production, delivery, consumption, and
measurement facts, but the current UI does not draw them as milestone ticks.
Cross-hatched producer backpressure appears separately,
and only when Plan output requirements plus recorded buffer state prove
saturation; ordinary producer idle time stays blank. The collapsible
**Architecture State** section shows evaluator-recorded buffer snapshots on
tracks whose owner and capacity come from the Plan. In-flight destination slots
appear as **Reserved**; the browser does not replay token flow to invent missing
state. The result page gives the timeline its own tab with a shared time axis
and zoom control.

The browser run is behavior-equivalent to the frozen core System Case for this
workload and profile, but it is not the frozen reference artifact and is not
promised to be byte-identical. It always uses the exploratory workflow ID
`frontend:finite_t_injection_demo_v1:arqsim_timeline_demo__2.3`;
only the core System Case runner owns the
`finite_runtime_injection_demo_measurement_v3:clifford_t_toy__2.3` identity.

## Notes

- Default port is `5174` (configured in `vite.config.ts`) so it doesn't clash with other Vite projects on `5173`.
- The `@/` import alias resolves to `src/`.
- Path expectations: `frontend/` and `server/` are siblings in the ArqSim
  monorepo, so `server/requirements.txt` installs the core with `-e ..`. For a different
  layout, install `arqsim` separately and replace that editable requirement.

## License

Copyright 2024-2026 Xiang Fang. ArqSim is distributed under the Apache
License 2.0; see [LICENSE](LICENSE). Bundled benchmark inputs and generated UI
components include third-party material governed by their own terms; see
[Third-Party Notices](../THIRD_PARTY_NOTICES.md).
