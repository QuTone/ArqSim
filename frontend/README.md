# ArqSim frontend

The optional React UI configures evaluations and displays real backend
reports: Program lineage, execution timelines, architecture state, physical
space, and fidelity. For a first Python-only run, use the
[quickstart notebook](../examples/notebooks/quickstart.ipynb).

## Run locally

Use Python 3.10+ and Node.js 20.19+ on the 20.x line, or Node.js 22.12+.
The commands below start the API and frontend in separate terminals from the
repository root.

Terminal 1:

```bash
cd server
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
uvicorn api:app --app-dir src --host 127.0.0.1 --port 8002 --reload
```

Terminal 2:

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Open <http://127.0.0.1:5174>. The Vite development server proxies API requests
to port 8002. Set `VITE_API_URL` before starting Vite to select another API
address. See the [server guide](../server/README.md) for API requests and
setup details. These are local development services; the API has no
authentication or production resource controls.

## Try the timeline demo

1. Keep **ArqSim Timeline Demo** and Architecture Profile **2.3**, the initial
   choices.
2. Under **Experiment Setup → Runtime semantics**, keep **Finite T injection
   demo · Profile 2.3** and run the evaluation.
3. Inspect **Program / Dynamic Work**, **Timeline Trace**, **Architecture
   State**, and **Resource Estimates → Fidelity**.

This preset uses full tracing, seed zero, explicit finite T injection,
reference reaction latency, and `canonical_reference_v1` fidelity. It ignores
other experiment/device overrides and rejects a different workload or profile.
Its two T operations exercise both measurement outcomes, including one
materialized logical-S correction. The core Python default remains `black_box`
T evaluation with fidelity enabled.

Expand a T parent to inspect its realized children. The timeline shows active
operation loci under their architecture owners; buffer snapshots appear in
Architecture State. The browser projects typed Report/Trace facts and never
replays execution or infers missing runtime state. Its live demo is an
exploratory run, separate from the frozen
[measurement-v3 System Case](../system_cases/finite_runtime_injection_demo_measurement_v3/README.md).

## Build and check

Run these from `frontend/`:

```bash
npm run typecheck
npm run lint
npm run test:adapter
npm run build
```

Server tests additionally need `pytest` in the active Python environment:
`python -m pytest -q server/tests` from the repository root, or
`npm run test:server` from `frontend/`.

`npm run preview -- --host 127.0.0.1` serves the built bundle with the same
local API proxy. For static hosting elsewhere, set `VITE_API_URL` when
building or provide an equivalent same-origin API proxy.

## Integration and ownership

The app uses Vite, React, TypeScript, Tailwind, and shadcn-ui. New requests go
to `/evaluate-v2`; the adapter also accepts stored Report-v1 data during its
compatibility window. The server's `/evaluate` route explicitly produces v1.

See [Report integration](REPORT_INTEGRATION.md) for the authoritative adapter
contract, timeline ownership rules, and demo policy, and the
[fixture guide](../tests/fixtures/report_v2/README.md) for fixture maintenance.
`src/services/reportAdapter.ts` owns report-to-view projection;
`src/types/evaluationReport.ts` defines the frontend contracts.

## License

Copyright 2024-2026 Xiang Fang. Apache-2.0; see [LICENSE](LICENSE) and
[Third-Party Notices](../THIRD_PARTY_NOTICES.md) for separately licensed
benchmark and UI material.
