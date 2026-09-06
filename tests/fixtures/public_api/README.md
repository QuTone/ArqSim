# Public report fixtures

`report-v2.json` exercises the current native public-report contract.
`report-v1.json` is its byte-stable, one-way compatibility projection. The
[behavior baselines](../behavior_baselines/README.md) separately retain locked
pre-refactor forensic reports.

From the repository root, check without writing:

```bash
python -m tests.generate_public_report_fixture
```

The [generator](../../generate_public_report_fixture.py) runs fixed inputs
twice, strictly validates Report v2 and its causal Trace, and byte-compares the
v1 rendering with its frozen fixture. After an intentional contract review,
`--update` writes only v2; the generator never rewrites v1.

See [Report and Trace Schema](../../../docs/01-public-api/report-and-trace-schema.md)
for the persisted contract and validation rules.
