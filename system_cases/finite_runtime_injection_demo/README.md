# Finite runtime-injection demonstration System Case

This System Case is a small vertical slice for reviewing dynamic
Program expansion and location-aware fidelity. It opts into
`finite_state_injection_v1` explicitly and does not change ArqSim's
`black_box` default.

The owned four-qubit Clifford+T workload runs on profile 2.3 with the named
`reference_reaction_latency_profile_v1`, canonical fidelity, full trace, and seed
5. Its two source T operations deterministically cover both runtime branches:

- outcome `1`: source injection attempt, classical reaction, then logical S;
- outcome `0`: source injection attempt and classical reaction, with no S.

The typed recipe convention
`cx_data_magic_measure_magic_z_v1` freezes the data-to-magic CX and magic-Z
measurement semantics. The Report-v2 trace freezes the measurement outcomes,
continuation lineage, and conditional correction as runtime evidence.

Run the complete reference gate from a source checkout:

```bash
python system_cases/finite_runtime_injection_demo/run.py
```

That command hash-checks the manifest inputs and reference files, strictly
loads and replays Report v2, recomputes invariants and fidelity, verifies the
typed gadget lineage, and compares a fresh fixed-seed public-API run byte for
byte. The request and receipt bind a canonical semantic-manifest hash that
excludes only the three self-referential reference-file digests. Use
`--no-rerun` for the strict reload/replay half only; that path still requires
canonical request, report, and receipt bytes.

The frozen case intentionally provides no in-place reference-regeneration
command. Changing a checked-in request, report, or receipt is a semantic review
action: create a successor System Case, then review its manifest identity,
request, Report-v2 identity, receipt, and visual trace together. Do not
overwrite this evidence.
