# Finite runtime-injection demonstration System Case

This System Case is a small vertical slice for reviewing dynamic
Program expansion and location-aware fidelity. It opts into
`finite_state_injection_v1` explicitly and does not change ArqSim's
`black_box` default.

The owned four-qubit Clifford+T workload runs on profile 2.3 with the named
`reference_reaction_latency_profile_v1`, canonical fidelity, full trace, and seed
5. Its two source T operations deterministically cover both runtime branches:

- outcome `1`: logical CX entangle, magic-Z measurement, classical reaction,
  then logical S;
- outcome `0`: the same entangle/measurement/reaction path, with no S.

The typed recipe convention
`cx_data_magic_measure_magic_z_v1` freezes the data-to-magic CX and magic-Z
measurement semantics. The source T remains the logical parent and fidelity
authority; the trace schedules entangle and measurement as distinct runtime
implementation children, emits the bit only when measurement completes, and
freezes the classical reaction and conditional logical correction as runtime
evidence.

For UI acceptance, the source-instruction T identity stays on the Compute
locus. Its execution interval is dispatch through terminal reaction or
correction; ready-to-dispatch delay is reported separately as queue wait.
Expanding the host exposes Compute-side CX, measurement, and optional logical-S
work, with reaction on the virtual Classical Decoder locus. These are
architecture/runtime-level logical-operation loci, not physical pulse lanes.
The row skeleton is derived from Profile 2.3's specification: modules and
interconnects stay stable, exact active engine submodules nest under their
module, and passive buffers/slots remain state rather than execution rows.
Engine claims describe contention and cannot displace an operation from its
explicit interconnect or inter-module transfer locus. Default hover details are
kept minimal; the Report-v2 artifact is the authority for full IDs and lineage.

Replay this immutable historical reference from a source checkout:

```bash
python -m system_cases.finite_runtime_injection_demo.run --no-rerun
```

That command hash-checks the manifest inputs and reference files, validates
canonical JSON, verifies the archived Plan and Trace content hashes and their
binding, and checks the Report-v2/receipt identity chain. The request and
receipt bind a canonical semantic-manifest hash that
excludes only the three self-referential reference-file digests. It does not
re-lower or rerun the case with current code, because corrected execution-locus
metadata intentionally changes Plan and Report identity.

Current-code strict Report-v2 replay and live byte-identical acceptance belong
to its explicit successor:

```bash
python -m system_cases.finite_runtime_injection_demo_measurement_v3.run
```

The locus-v2 intermediate successor is also archive-only. The frozen cases
intentionally provide no in-place reference-regeneration command. Changing a
checked-in request, report, or receipt is a semantic review
action: create a successor System Case, then review its manifest identity,
request, Report-v2 identity, receipt, and visual trace together. Do not
overwrite this evidence. The current successor lineage records both the
locus-metadata change and the later Plan-v9 logical-measurement-provider change.
