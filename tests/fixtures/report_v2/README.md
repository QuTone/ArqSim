# Report-v2 fixtures

- `static-summary.v2.json` covers a static Plan/Trace with canonical fidelity
  and no diagnostic time log.
- `dynamic-t-full.v2.json` covers finite T injection, measurement, reaction,
  conditional logical correction, dynamic lineage, and the checked full log.

From the repository root, check determinism and codec validity without writing:

```bash
python -m tests.generate_report_v2_fixtures
```

For a reviewed schema or semantic change, the
[generator](../../generate_report_v2_fixtures.py) accepts `--update`.
See [Report and Trace Schema](../../../docs/01-public-api/report-and-trace-schema.md)
for the contract these fixtures exercise.
