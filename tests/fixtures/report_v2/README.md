# Report-v2 fixtures

- `static-summary.v2.json` exercises a static Plan/Trace with canonical
  fidelity enabled and no diagnostic time log.
- `dynamic-t-full.v2.json` exercises finite-state T injection, measurement,
  classical reaction, materialized logical correction, dynamic work lineage,
  and the checked full diagnostic time log.

Check determinism and codec validity with:

```bash
python -m tests.generate_report_v2_fixtures
```

After an intentional schema or semantic review, refresh them with `--update`.
