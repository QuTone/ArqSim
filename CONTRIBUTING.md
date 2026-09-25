# Contributing to ArqSim

For a bug, open an [issue](https://github.com/QuTone/ArqSim/issues) with the
version, a small reproducible input, expected behavior, and the actual result.
Discuss changes to scientific models or public contracts before implementing
them; the [documentation index](docs/README.md) describes these boundaries.

## Pull requests

1. Create a feature branch in your fork or this repository; do not push to `main`.
2. Keep the change focused and add regression tests for changed behavior.
3. Open a pull request explaining the problem, solution, and validation.
4. Resolve review conversations and keep the branch up to date with `main`.
5. After all required checks pass, a maintainer can squash-merge the PR.

The required checks are `core (3.10)`, `core (3.11)`, `core (3.12)`, and
`frontend`. The minimum approving-review count is zero, so a sole maintainer
can merge their own PR after checks pass. Direct pushes, force pushes, and
deleting `main` are blocked; there is no bypass list.

## Local checks

With a Python 3.10+ virtual environment activated, run from the repository root:

```bash
python -m pip install -e '.[test]'
python -m pytest -q
python tests/run_packaging_smoke.py
python -m system_cases.finite_runtime_injection_demo_measurement_v3.run
```

For HTTP or frontend changes, also install the server dependencies from
`server/`, then run the UI checks with Node.js 20.19 or 22.12+:

```bash
cd server
python -m pip install -r requirements.txt
cd ../frontend
npm ci
npm run typecheck
npm run lint
npm run test:adapter
npm run test:server
npm run test:previews
npm run build
```

The preview check evaluates every bundled benchmark and canonical preset and
validates the resulting timelines. See the [frontend guide](https://github.com/QuTone/ArqSim/tree/main/frontend#readme)
for interactive setup and [CI workflow](https://github.com/QuTone/ArqSim/blob/main/.github/workflows/ci.yml) for hosted
checks. Include only material you have permission to share.
