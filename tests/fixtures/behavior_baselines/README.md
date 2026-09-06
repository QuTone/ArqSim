# Runtime behavior baselines

These fixtures preserve behavior and forensic snapshots from before the
transactional Event Engine refactor. Each case contains:

| Artifact | Purpose |
| --- | --- |
| `workload.qasm` | Human-readable source. |
| `workload.json` | Frozen normalized `FTCircuit` used for execution. |
| `config.json` | Frozen evaluation inputs. |
| `semantic-baseline.json` | Regression contract checked with numerical tolerances. |
| `report.v1.json` | Locked pre-refactor forensic snapshot. |

Tests also check that QASM normalizes to the committed workload, keeping parser
layering changes distinct from runtime changes. Current public-report fixtures
live in [public_api](../public_api/README.md).

From the repository root, verify without writing (`--check` is equivalent):

```bash
python -m tests.generate_behavior_baselines
```

The [generator](../../generate_behavior_baselines.py) has three explicit write
modes for reviewed changes:

| Flag | Changes |
| --- | --- |
| `--update-semantic` | Semantic baseline only; preserves the frozen forensic report identity. |
| `--update-outputs` | Semantic baseline and forensic report. |
| `--refresh-inputs` | Normalized workload/configuration and their dependent outputs. |

The full forensic report is diagnostic evidence, not a byte-for-byte
cross-platform gate. Do not rewrite it as part of routine semantic maintenance.
Classify differences as bug fixes, approved model changes, or regressions
before selecting a write mode; do not update fixtures merely to pass tests.
