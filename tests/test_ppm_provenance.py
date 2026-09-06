"""Keep the published calibration artifacts tied to the canonical fit receipt."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from arqsim.operation_profiles.fidelity_model_config import load_fidelity_fit


_BUNDLE = Path(__file__).resolve().parents[1] / "docs" / "provenance" / "ppm"


def test_ppm_artifacts_match_the_canonical_source_hashes() -> None:
    source = load_fidelity_fit("unrotated_surface_ppm_p1e3")["source"]
    for filename, receipt_key in (
        ("multi_patch_ppm_results.csv", "data_sha256"),
        ("multi_patch_ppm_fit_p1e-3.json", "fit_sha256"),
    ):
        assert hashlib.sha256((_BUNDLE / filename).read_bytes()).hexdigest() == (
            source[receipt_key]
        )


def test_ppm_fit_and_source_rows_support_the_packaged_coefficients() -> None:
    config = load_fidelity_fit("unrotated_surface_ppm_p1e3")
    fit = json.loads((_BUNDLE / "multi_patch_ppm_fit_p1e-3.json").read_text())
    with (_BUNDLE / "multi_patch_ppm_results.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    samples = {(int(row["d"]), int(row["ppm_weight"])): row for row in rows}
    assert len(samples) == len(rows) == 30
    assert {float(row["p"]) for row in rows} == {0.001}
    assert {row["failure_metric"] for row in rows} == {"decoded_Z_product_parity"}

    multi = config["multi_patch_fit"]
    assert fit["fit"]["fit_scope"]["distances"] == multi["calibrated_distances"]
    assert sorted({weight for _, weight in samples if weight >= 2}) == (
        multi["calibrated_weights"]
    )
    assert fit["model_comparison"]["delta_aic_raw_minus_paired"] == (
        multi["delta_aic_raw_minus_paired"]
    )
    fitted_samples = set()
    for fit_key, config_key in (
        ("fit", "multi_patch_fit"),
        ("weight_one_control", "weight_one_control"),
    ):
        record = fit[fit_key]
        assert record["coefficients"] == config[config_key]["coefficients"]
        assert record["transformed_hazard_r_squared"] == (
            config[config_key]["transformed_hazard_r_squared"]
        )
        for fitted in record["fitted_rows"]:
            key = (fitted["distance"], fitted["weight"])
            assert key not in fitted_samples
            fitted_samples.add(key)
            row = samples[key]
            assert fitted["shots"] == int(row["shots"])
            assert fitted["errors"] == int(row["errors"])
            assert fitted["logical_error_rate"] == pytest.approx(
                float(row["logical_error_rate"]), rel=1e-12, abs=1e-16
            )
    assert fitted_samples == set(samples)
    for point in config["weight_one_control"]["source_points"]:
        row = samples[(point["distance"], 1)]
        assert point["operation_ler"] == int(row["errors"]) / int(row["shots"])
