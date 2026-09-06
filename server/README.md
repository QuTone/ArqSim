# ArqSim HTTP server

This FastAPI adapter powers the optional [web UI](../frontend/README.md).
It loads a bundled benchmark or accepts an `FTCircuit` document, calls the
public `run_evaluation()` facade, and returns its report. Runtime timing,
footprint, and fidelity calculations remain in the core package.

## Setup and run

Use Python 3.10+ and run these commands from the repository root:

```bash
cd server
python3 -m venv venv
source venv/bin/activate
python -m pip install -r requirements.txt
uvicorn api:app --app-dir src --host 127.0.0.1 --port 8002 --reload
```

On Windows, activate with `venv\Scripts\activate` instead. Run the requirements
install from `server/`: its `-e ..` entry installs the adjacent ArqSim core.
The optional Bash helper `./setup.sh` installs the same requirements and
**replaces an existing `server/venv`**. Later sessions only need to activate
the environment and start Uvicorn.

The service is for local development. It has open CORS and no authentication
or production resource controls; keep it bound to loopback for this setup.

## Try the API

With the server running, use a second terminal:

```bash
curl http://127.0.0.1:8002/
curl http://127.0.0.1:8002/benchmarks
curl http://127.0.0.1:8002/architecture-profiles
curl -X POST http://127.0.0.1:8002/evaluate-v2 \
  -H 'Content-Type: application/json' \
  -d '{"benchmark_name":"arqsim_timeline_demo","representation":"gate","config":{"schema_version":"arqsim.evaluation-config.v1","profile_id":"2.3"}}' \
  -o report.json
```

The last command saves a real Report-v2 evaluation of the small timeline
workload. It uses the core defaults, including `black_box` injection and
canonical fidelity. The browser's explicit finite-injection preset is a
separate request policy described in [Report integration](../frontend/REPORT_INTEGRATION.md).

| Method | Path | Response |
| --- | --- | --- |
| GET | `/` | Health and supported report schemas |
| GET | `/benchmarks` | Bundled workload catalog |
| GET | `/architecture-profiles` | Unsized Architecture Profile-v3 catalog |
| POST | `/evaluate-v2` | Native `arqsim.evaluation-report.v2` |
| POST | `/evaluate` | Deprecated, explicit Report-v1 compatibility projection |

Evaluation requests require `representation` (`gate`, legacy `clifford_t`, or
`pbc`), exactly one of `benchmark_name` and `workload`, and a public
`arqsim.evaluation-config.v1` configuration. An inline `workload` must be a
canonical `FTCircuit` document with a matching representation. Invalid inputs
return HTTP 422. See the [public API guide](../docs/01-public-api/public-api.md)
for configuration, defaults, and error details.

## Checks and files

With `pytest` installed in the active Python environment, run
`python -m pytest -q server/tests` from the repository root.

The app is in [`src/api.py`](src/api.py). Bundled inputs live in
[`benchmark/`](benchmark/); their source hashes, derivation limits, and license
boundaries are recorded in the [catalog provenance](benchmark/README.md) and
[Third-Party Notices](../THIRD_PARTY_NOTICES.md).
