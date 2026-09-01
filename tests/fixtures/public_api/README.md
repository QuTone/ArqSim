# Public report fixtures

`report-v2.json` is the current native public contract shipped by the package.
`report-v1.json` is the byte-stable, one-way compatibility projection. Both are
separate from the locked Step-2 forensic reports, which preserve pre-refactor
evidence.

The default command is read-only and fails on any difference:

```bash
python -m tests.generate_public_report_fixture
```

After reviewing an intentional v2 schema or contract change, refresh it
explicitly:

```bash
python -m tests.generate_public_report_fixture --update
```

The generator runs the fixed input twice, strictly validates the v2 report and
causal trace ledger, and byte-compares the explicit one-way v1 rendering with
its frozen fixture. It only writes v2 in `--update` mode and never rewrites the
v1 fixture.
