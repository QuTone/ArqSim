# Finite runtime-injection System Case — archived

This immutable predecessor preserves a four-qubit Clifford+T run on Profile
2.3 with canonical fidelity, full trace, named reaction timing, and seed 5.
Its two T operations exercise both measurement branches under
`finite_state_injection_v1`; ArqSim's default remains `black_box`.

The source T remains the logical parent and fidelity authority. Runtime
children record logical CX entangle, magic-Z measurement, reaction, and
conditional logical S under `cx_data_magic_measure_magic_z_v1`. Historical UI
expectations are retained in [ACCEPTANCE.md](ACCEPTANCE.md).

From the repository root, verify the frozen evidence without rerunning it:

```bash
python -m system_cases.finite_runtime_injection_demo.run --no-rerun
```

The command checks manifest/input digests, canonical JSON, archived Plan/Trace
hashes and binding, and the request/report/receipt identity chain. It does not
re-lower the archived Plan or execute it with current semantics.

The [locus-v2 successor](../finite_runtime_injection_demo_locus_v2/README.md)
records corrected execution ownership and is also archived. Use the
[measurement-v3 successor](../finite_runtime_injection_demo_measurement_v3/README.md)
for current-code strict replay and live acceptance.

No case exposes an in-place reference-regeneration command. A semantic change
requires a named successor and review of its manifest, request, Report, receipt,
and visual trace; preserve these existing reference files.
