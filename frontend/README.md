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

## Evaluate benchmarks and architecture presets

Choose bundled benchmarks and canonical architecture presets, then run the
evaluation. **General evaluation** is the initial runtime mode and uses the
core's black-box T semantics, full tracing, and canonical fidelity. All six
presets can produce runtime timelines. Profiles 1.2 and 2.2 prefer a benchmark's
PBC representation when available; otherwise they use Clifford+T. Other presets
use Clifford+T.

Use **Select** to add presets to a comparison. With no explicitly selected
presets, the currently previewed preset is evaluated. By default, evaluation
includes only the first **12 layers** of the chosen representation. Under
**Advanced → Evaluation scope**, choose 24, 48, or 96 layers, or explicitly
select **Full workload**. Prefix evaluation avoids running the remainder of
the benchmark; a full workload can take minutes.

The result banner records how many layers were evaluated. Latency, fidelity,
circuit statistics, and architecture sizing all describe that evaluated
prefix; they are not estimates for the full workload. The prefix is compiled
and evaluated independently, so its schedule need not match the beginning of
a separately compiled full run. PBC and Clifford+T layer counts cover different
work and must not be used as equivalent full-benchmark comparisons.

Prefix timelines initially include all evaluated layers, subject to the display
event cap. Full-workload timelines initially show 12 layers; **Show next**
reveals more layers already present in the returned report, up to the display
limit. To evaluate additional layers of a prefix, return to setup, increase the
evaluation scope, and run again.

## Try the finite-T timeline demo

1. Keep **ArqSim Timeline Demo** and Architecture Profile **2.3**, the initial
   choices.
2. Under **Advanced → Runtime semantics**, select **Finite T injection
   demo · Profile 2.3** and run the evaluation.
3. Inspect **Program / Dynamic Work**, **Timeline Trace**, **Architecture
   State**, and **Resource Estimates → Fidelity**.

This preset uses full tracing, seed zero, explicit finite T injection,
reference reaction latency, and `canonical_reference_v1` fidelity. It ignores
other experiment/device overrides and rejects a different workload or profile.
This small demo always runs in full; the prefix scope selector is disabled.
Switch back to **General evaluation** to run other combinations.
Its two T operations exercise both measurement outcomes, including one
materialized logical-S correction. The core Python default remains `black_box`
T evaluation with fidelity enabled.

Expand a T parent to inspect its realized children. Each execution event appears
once under a real Module or Submodule from the returned architecture. Transfers
retain their other participants in the tooltip; delay-only reaction follows its
causal parent's location. These display anchors do not change engine claims or
execution timing. Buffer snapshots appear separately in Architecture State. The browser projects typed Report/Trace facts and never
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
`npm run test:server` from `frontend/`. With the server dependencies installed,
`npm run test:previews` evaluates every bundled benchmark against all six presets
using 12-layer previews and checks that the frontend matches the returned reports.
This matrix is also part of CI.

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
`src/services/timelineOwnership.ts` resolves architecture-backed display anchors;
`src/types/evaluationReport.ts` defines the frontend contracts.

## License

Copyright 2024-2026 Xiang Fang. Apache-2.0; see [LICENSE](LICENSE) and
[Third-Party Notices](../THIRD_PARTY_NOTICES.md) for separately licensed
benchmark and UI material.
