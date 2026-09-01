# Step 2 behavior baselines

These fixtures preserve the observable behavior immediately before the
transactional Event Engine refactor.

Each case contains five artifacts:

- `workload.qasm`: small, human-readable source;
- `workload.json`: normalized `FTCircuit` and the executable baseline input;
- `config.json`: fully materialized public evaluation configuration;
- `semantic-baseline.json`: the stable regression contract used by tests;
- `report.v1.json`: a complete forensic report snapshot for inspection and
  later frontend-contract work.

The Engine baseline runs from `workload.json`, not directly from QASM. This
keeps parser or Qiskit layering changes separate from runtime behavior. Tests
still verify that each readable QASM source normalizes to the committed
workload.

Verify the committed baseline without writing (this is deliberately the
default):

```bash
python -m tests.generate_behavior_baselines
```

The explicit alias is:

```bash
python -m tests.generate_behavior_baselines --check
```

Regenerating semantic/report output after an approved model change requires an
explicit write flag:

```bash
python -m tests.generate_behavior_baselines --update-outputs
```

Refreshing `workload.json` or `config.json` is an intentional baseline-input
change and must be explicit:

```bash
python -m tests.generate_behavior_baselines --refresh-inputs
```

`--refresh-inputs` also updates the dependent outputs. Never use either write
mode merely to make a regression test pass; first classify the difference as
an old bug, an owner-approved model change, or a regression.

The semantic baseline is the Step 3 compatibility gate. The complete report
is diagnostic evidence, not a byte-for-byte cross-platform gate: equivalent
floating-point results are compared with a tight tolerance, and additive
report fields are allowed. `report.v1.json` intentionally remains the locked
pre-refactor forensic snapshot; after Step 3 it must not be silently refreshed
merely to make a changed run pass.
