# ArqSim frontend

Interactive frontend for the [ArqSim core](https://github.com/QuTone/ArqSim).
It lets you configure heterogeneous quantum architectures, run resource
estimation against real benchmarks, and inspect timelines, space treemaps, and
cross-architecture comparisons. It lives under `frontend/` in the ArqSim
monorepo.

## Stack

- **Frontend**: Vite + React + TypeScript + Tailwind + shadcn-ui
- **Backend**: thin FastAPI adapter (`backend/`) that calls the public `heteqsys.run_evaluation` facade and exposes `/benchmarks`, canonical Profile v3, native `/evaluate-v2`, and an explicit v1 compatibility route.

## Repository layout

```
ArqSim/frontend/
├── src/                  # React app
│   ├── components/
│   │   ├── canvas/       # architecture canvas
│   │   ├── architecture/ # resolved Node → Module → Submodule → Slot hierarchy
│   │   ├── config/       # config panels (DeviceLibrary, ExperimentSetup, ...)
│   │   ├── layout/       # TopBar / LeftPanel / BottomPanel / MainCanvas
│   │   ├── program/      # Output Program + typed Dynamic Work
│   │   ├── resources/    # ResourceEstimates, SpaceTreemap, TimeBreakdown, CompareScatter
│   │   ├── statistics/   # GBC / PBC panels
│   │   └── timeline/     # TimelineTrace
│   ├── services/api.ts   # backend HTTP client
│   ├── services/evaluationPresets.ts # explicit opt-in demo request policies
│   ├── services/reportAdapter.ts # strict report v1/v2 → version-neutral ViewModels
│   ├── types/evaluationReport.ts # canonical report/view contracts
│   ├── pages/Index.tsx
│   └── ...
├── backend/              # FastAPI service (separately installable)
│   ├── src/
│   │   └── api.py             # public FTCircuit/config → versioned reports
│   ├── benchmark/        # QASM workload catalog and provenance
│   ├── requirements.txt  # installs heteqsys editable + fastapi/uvicorn/...
│   └── setup.sh
└── REPORT_INTEGRATION.md # public Report-v2 frontend integration contract
```

The profile catalog consumes canonical `arqsim.architecture-profile.v3`.
The browser accepts both `arqsim.evaluation-report.v1` and the frozen
`arqsim.evaluation-report.v2` topology. Both are reduced at the HTTP boundary
to one version-neutral frontend model; React state, caches, comparisons, and
components never retain a raw v1 document. For v2, timeline spans are projected
only from Trace-v3 completion transitions. The browser does not validate hashes,
replay execution, or recompute evaluator-owned facts—those remain backend
authorities.

The UI posts new work to `/evaluate-v2`. During the compatibility window,
`/evaluate` explicitly renders Report v1; it never inherits a report version
implicitly from the core default. The shared adapter still accepts v1 for
stored or compatibility responses.

## Local development

This frontend and its FastAPI adapter are a local research/development UI. The
commands below bind the API to loopback and are not a production deployment
recipe.

### Backend (port 8002)

```bash
cd backend
./setup.sh                     # creates ./venv, installs requirements (incl. -e ../..)
source venv/bin/activate
uvicorn api:app --app-dir src --host 127.0.0.1 --port 8002 --reload
```

You can also reuse a virtual environment from the monorepo root if it already
contains the same dependencies; the backend imports `heteqsys` either way.

Smoke test:

```bash
curl http://localhost:8002/             # health + canonical report schema
curl http://localhost:8002/benchmarks   # available benchmark list
```

### Frontend (port 5174)

```bash
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
npm run test:backend           # v2/v1/profile route contract smoke
npm run preview                # serve dist/ locally
```

The adapter test reads the codec-validated static and dynamic-T fixtures from
the monorepo's `tests/fixtures/report_v2/` directory and the
finite-runtime-injection System Case reference. Refresh the codec fixtures only
through ArqSim's `python -m tests.generate_report_v2_fixtures --update`
workflow. The frozen System Case has no in-place regeneration command: a
semantic change creates a successor case instead of overwriting its evidence.

## Finite-injection demo

The global evaluation path remains `black_box` with fidelity disabled. To run
the explicit vertical-slice demo:

1. Select **ArqSim Timeline Demo** and Architecture Profile **2.3** (both are
   the initial UI choices).
2. Under **Experiment Setup → Runtime semantics**, select **Finite T
   injection demo**.
3. Run the evaluation, then inspect **Program / Dynamic Work**, **Timeline
   Trace**, and **Resource Estimates → Fidelity**.

That preset sends full Trace v3, seed 5, the reference reaction-latency values with
their provenance, and `canonical_reference_v1` fidelity. The two T operations
deterministically exercise both outcomes: one path materializes classical
reaction plus logical-S correction, while the other completes after reaction.
The browser reads lineage, measurements, continuation receipts, and correction
identity from typed Report-v2/Trace-v3 fields; it does not reconstruct the
gadget from display labels.

The browser run is behavior-equivalent to the frozen core System Case for this
workload and profile, but it is not the frozen reference artifact and is not
promised to be byte-identical. It always uses the exploratory workflow ID
`frontend:finite_t_injection_demo_v1:arqsim_timeline_demo__2.3`;
only the core System Case runner owns the
`finite_runtime_injection_demo:clifford_t_toy__2.3` identity.

## Notes

- Default port is `5174` (configured in `vite.config.ts`) so it doesn't clash with other Vite projects on `5173`.
- The `@/` import alias resolves to `src/`.
- Path expectations: `frontend/` is nested in the ArqSim monorepo, so
  `backend/requirements.txt` installs the core with `-e ../..`. For a different
  layout, install `heteqsys` separately and replace that editable requirement.

## License

Copyright 2024-2026 Xiang Fang. ArqSim is distributed under the Apache
License 2.0; see [LICENSE](LICENSE). Bundled benchmark inputs and generated UI
components include third-party material governed by their own terms; see
[Third-Party Notices](../THIRD_PARTY_NOTICES.md).
