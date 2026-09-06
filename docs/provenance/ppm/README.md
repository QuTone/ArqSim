# PPM calibration provenance

This bundle supplies the calibration table, existing fit and standalone fitting
script behind ArqSim's unrotated surface-code PPM model. The three artifacts
were contributed by Xiang Fang from LightStim work and are redistributed under
[Apache-2.0](../../../LICENSE), with attribution to LightStim Contributors.
Their contents are unchanged. See also the
[Third-Party Notices](../../../THIRD_PARTY_NOTICES.md).

## Artifact identity

| Artifact | Receipt ID | SHA-256 |
| --- | --- | --- |
| [Calibration table](multi_patch_ppm_results.csv) | `lightstim.multi_patch_ppm_results.p1e3.v1` | `e7bab6d9a611366f456efcaf6dc7da71049a161f8d935a032b12826cf3a2e326` |
| [Existing fit](multi_patch_ppm_fit_p1e-3.json) | `lightstim.multi_patch_ppm_fit.p1e3.v1` | `503e43415dbb41f4b9a27f485a8258a7cc0b400ca81c8bb5df9af5fb7f6385cc` |
| [Fitting script](fit_multi_patch_ppm.py) | identified by its file hash | `96eb351c3bdd87a940e2c0372d4e2a7159a5d52de1ab91a4bc09e01389cbeaf9` |

The table contains 30 aggregate simulation results, not individual sampled
shots: code distances 3, 5 and 7; weights 1, 2, 3, 4, 5, 6, 7, 8, 12 and 16;
physical error probability `1e-3`; seed `20260720`; and PyMatching decoding.
Each row retains shots, errors, logical error rate, circuit-size metadata and
elapsed execution seconds. The failure metric is the parity of decoded
logical-Z residuals.

The coupled protocol uses `d` syndrome rounds before coupling and `d` during
coupling. Weight one is a separate no-coupler control with `2d` rounds. The
selected multi-patch fit uses the corridor-specific effective weight
`2*ceil(weight/2)` and excludes the weight-one control. Distances or weights
outside the stated calibration grid are extrapolations.

## Refit the retained table

The fitter imports no LightStim code. Use a separate environment with the
following versions to reproduce the verified numerical environment:

| Dependency | Version |
| --- | --- |
| Python | 3.10.12 |
| NumPy | 2.2.6 |
| Pandas | 2.3.3 |
| SciPy | 1.15.3 |
| Matplotlib | 3.10.8 |

From this directory, with an existing output directory of your choice:

```bash
python fit_multi_patch_ppm.py \
  --input multi_patch_ppm_results.csv \
  --p 1e-3 \
  --output-json /tmp/arqsim-ppm-refit.json \
  --output-plot /tmp/arqsim-ppm-refit.svg
sha256sum /tmp/arqsim-ppm-refit.json
```

The independently rerun JSON has the exact fit hash above. All 654 numerical
values, the structure and the text match the retained fit. The packaged
[model YAML](../../../arqsim/operation_profiles/fidelity_profiles/unrotated_surface_ppm_p1e3.yaml)
uses the same coefficients, R-squared, AIC comparison, calibrated domain and
weight-one control points. The optional plot is diagnostic and has no frozen
byte identity. Bitwise fit reproduction across other numerical-library
versions is not claimed.

## Scope and historical receipt

This is a reproducible **table-to-fit** bundle. The original Monte Carlo
generation implementation and complete run environment were not frozen as a
public source artifact. The retained CSV records the calibration configuration
and aggregate observations; this bundle does not claim to regenerate those
observations from a public commit. In particular, the LightStim
[experiment script](https://github.com/QuTone/LightStim/blob/59e5ba569dc10a721c46888ba0e098d60fb41419/benchmarks/logical_ops/run_logical_ops.py)
and [notebook](https://github.com/QuTone/LightStim/blob/59e5ba569dc10a721c46888ba0e098d60fb41419/notebooks/LogicalOps/multi_patch_LS.ipynb)
in the original receipt provide protocol background; that commit does not
contain this fitter or the complete PPM generation path used locally.

The YAML's parsed `source` mapping is included in the frozen
`canonical_reference_v1` fidelity identity and in stored reports. Its artifact
IDs and hashes resolve to the files above. Its original `availability` text
says the data and fit are not bundled with ArqSim; that statement predates this
bundle and is now outdated for the source checkout and source distribution.
It is retained as historical receipt text to preserve existing profile and
report identities, not as the current distribution manifest. This README
records current availability: the repository and Python sdist include this
bundle; the wheel includes the existing model YAML but excludes `docs/`.
