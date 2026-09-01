# ArqSim frontend backend

FastAPI service that powers the ArqSim web UI. It wraps the
[`heteqsys`](https://github.com/QuTone/ArqSim) Python package and exposes a small
HTTP API the React frontend calls.

This adapter is intended for the local research/development UI. It has no
authentication or production resource controls, so the default command binds
only to the loopback interface.

The app lives in [`src/api.py`](src/api.py). It is intentionally a thin adapter:
it normalizes a bundled benchmark or accepts an `FTCircuit` document, calls the
public `run_evaluation()` facade, and returns its canonical
Report v2 document from `/evaluate-v2`. The established `/evaluate` route
explicitly renders the frozen Report-v1 compatibility projection.
Frontend-specific timeline, breakdown, or footprint calculations do not live
in this service.

## Prerequisites

- Python 3.10+
- The ArqSim monorepo checkout, with this backend nested under `frontend/`:

  ```
  ArqSim/
  ├── heteqsys/             # core engine package
  └── frontend/backend/     # this adapter
  ```

  Bundled normalized Clifford+T and PBC workloads live under `backend/benchmark/`.

## Setup

```bash
cd frontend/backend
./setup.sh                 # creates ./venv and installs requirements (incl. -e ../..)
source venv/bin/activate
```

`setup.sh` always runs from the `backend/` directory so the editable install
(`-e ../..` in `requirements.txt`) resolves to the monorepo root.

## Run

```bash
source venv/bin/activate
uvicorn api:app --app-dir src --host 127.0.0.1 --port 8002 --reload
```

Smoke test:

```bash
curl http://localhost:8002/             # health and report schema
curl http://localhost:8002/benchmarks   # available benchmark list
curl http://localhost:8002/architecture-profiles # canonical Profile-v3 catalog
```

## API

| Method | Path          | Description                                                        |
|--------|---------------|--------------------------------------------------------------------|
| GET    | `/`           | Health check and supported report schema.                          |
| GET    | `/benchmarks` | Benchmarks discovered under `benchmark/original-circuit/*.qasm`.   |
| GET    | `/architecture-profiles` | Canonical unsized Architecture Profile-v3 catalog. |
| POST   | `/evaluate-v2` | Run the facade and return native `arqsim.evaluation-report.v2`. |
| POST   | `/evaluate`   | Explicit frozen Report-v1 compatibility route. |

The request declares `representation` (`clifford_t` or `pbc`), exactly one of
`benchmark_name` / `workload`, and a public `arqsim.evaluation-config.v1`
`config`. For example:

```json
{
  "benchmark_name": "adder_n64",
  "representation": "clifford_t",
  "config": {
    "schema_version": "arqsim.evaluation-config.v1",
    "profile_id": "1.1"
  }
}
```

## Project structure

```
backend/
├── src/
│   └── api.py              # public facade adapter (entry point)
├── benchmark/              # QASM catalog with source and derivation records
├── requirements.txt
├── setup.sh
└── README.md               # this file
```

The bundled benchmark assets are the HTTP adapter's workload catalog. Keep
their normalized Clifford+T and PBC forms aligned with the matching source
circuits when updating the catalog. Their per-family origin, transformation
status, and license boundary are recorded in
[`benchmark/README.md`](benchmark/README.md) and the repository-level
[`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md).

The UI's opt-in finite-injection demo crosses the same public API and
uses an exploratory `frontend:finite_t_injection_demo_v1:...` workflow
identity. Its policy and timeline-demo workload are behavior-equivalent to the
frozen core System Case, but a live HTTP result is not that reference artifact
and is not expected to be byte-identical to it.

## Notes

- `venv/` is gitignored — activate it before running.
- CORS is open (`*`) for local development. Keep the server on loopback; a
  network deployment must add authentication, explicit origins, request
  limits, and production server configuration.
