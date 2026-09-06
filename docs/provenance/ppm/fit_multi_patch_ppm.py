#!/usr/bin/env python3
"""Fit bounded weight/distance models to the multi-patch PPM sweep."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.optimize import minimize


HERE = Path(__file__).resolve().parent
DEFAULT_RESULTS = HERE / "results"
DEFAULT_INPUT = DEFAULT_RESULTS / "multi_patch_ppm_results.csv"


def load_results(input_path: Path, physical_error_rate: float) -> pd.DataFrame:
    """Load the canonical logical-ops CSV and normalize names for fitting."""
    if not input_path.exists():
        raise FileNotFoundError(f"No PPM result CSV at {input_path}")
    frame = pd.read_csv(input_path).rename(
        columns={
            "ppm_weight": "weight",
            "d": "distance",
            "p": "physical_error_rate",
        }
    )
    required = {
        "weight", "distance", "physical_error_rate", "seed", "shots",
        "errors", "logical_error_rate",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(
            f"PPM result CSV is missing required columns: {sorted(missing)}"
        )
    frame = frame[np.isclose(frame["physical_error_rate"], physical_error_rate)]
    if frame.empty:
        raise ValueError(
            f"No rows at physical error rate p={physical_error_rate:g} in {input_path}"
        )
    frame = frame.drop_duplicates(
        ["weight", "distance", "physical_error_rate", "seed"], keep="last"
    )
    return frame.sort_values(["distance", "weight"]).reset_index(drop=True)


def _fit_bounded_hazard(
    frame: pd.DataFrame,
    *,
    error_column: str,
    rate_column: str,
    weight_definition: str,
) -> dict:
    # Weight one has no coupler and is a diagnostic control, not a sample from
    # the same multi-patch protocol family.
    fit_frame = frame[frame["weight"] >= 2].copy()
    errors = fit_frame[error_column].to_numpy(dtype=float)
    shots = fit_frame["shots"].to_numpy(dtype=float)
    raw_weights = fit_frame["weight"].to_numpy(dtype=float)
    if weight_definition == "raw":
        weights = raw_weights
        formula_weight = "w"
    elif weight_definition == "paired":
        # The audited corridor adds one left/right row per pair of patches.
        # An odd final patch therefore occupies the same corridor extent as the
        # next even weight, producing the observed odd/even staircase.
        weights = 2 * np.ceil(raw_weights / 2)
        formula_weight = "w_eff = 2*ceil(w/2)"
    else:
        raise ValueError(f"Unknown weight definition: {weight_definition}")
    distances = fit_frame["distance"].to_numpy(dtype=float)
    design = np.column_stack((distances, np.log(weights), np.ones(len(weights))))

    adjusted_rate = (errors + 0.5) / (shots + 1.0)
    observed_log_hazard = np.log(-np.log1p(-adjusted_rate))
    initial = np.linalg.lstsq(design, observed_log_hazard, rcond=None)[0]

    def objective(parameters: np.ndarray) -> float:
        log_hazard = design @ parameters
        hazard = np.exp(np.clip(log_hazard, -50, 30))
        probability = np.clip(-np.expm1(-hazard), 1e-15, 1 - 1e-15)
        return float(
            -(
                errors * np.log(probability)
                + (shots - errors) * np.log1p(-probability)
            ).sum()
        )

    def gradient(parameters: np.ndarray) -> np.ndarray:
        log_hazard = design @ parameters
        hazard = np.exp(np.clip(log_hazard, -50, 30))
        probability = np.clip(-np.expm1(-hazard), 1e-15, 1 - 1e-15)
        score_eta = (shots * probability - errors) * hazard / probability
        return design.T @ score_eta

    optimum = minimize(
        objective,
        initial,
        jac=gradient,
        method="L-BFGS-B",
        options={"ftol": 1e-14, "gtol": 1e-9, "maxiter": 10_000},
    )
    if not optimum.success:
        raise RuntimeError(f"Fit failed for {rate_column}: {optimum.message}")

    alpha, beta, gamma = optimum.x
    log_hazard = design @ optimum.x
    hazard = np.exp(log_hazard)
    predicted = -np.expm1(-hazard)
    observed = errors / shots

    # Expected Fisher information for the complementary-log-log binomial model.
    fisher_weights = shots * hazard * hazard * np.exp(-hazard) / predicted
    covariance_natural = np.linalg.inv(
        design.T @ (fisher_weights[:, np.newaxis] * design)
    )
    transform = np.diag((1 / math.log(10), 1.0, 1 / math.log(10)))
    covariance_formula = transform @ covariance_natural @ transform
    standard_errors = np.sqrt(np.diag(covariance_formula))

    # Formula form:
    # p(w,d) = 1-exp[-10^(a*d + b*log10(w) + c)].
    a = alpha / math.log(10)
    b = beta
    c = gamma / math.log(10)
    transformed_observed = np.log(-np.log1p(-observed))
    residual_sum = float(np.square(transformed_observed - log_hazard).sum())
    total_sum = float(
        np.square(transformed_observed - transformed_observed.mean()).sum()
    )
    transformed_r_squared = 1 - residual_sum / total_sum

    fitted_rows = fit_frame[
        ["weight", "distance", "shots", error_column, rate_column]
    ].copy()
    fitted_rows["predicted_ler"] = predicted
    fitted_rows["predicted_over_observed"] = predicted / observed

    return {
        "metric": rate_column,
        "weight_definition": weight_definition,
        "formula_weight": formula_weight,
        "fit_scope": {
            "minimum_weight": 2,
            "maximum_weight": int(weights.max()),
            "distances": sorted({int(value) for value in distances}),
            "physical_error_rate": float(frame["physical_error_rate"].iloc[0]),
            "initial_basis": "Z",
            "measurement_basis": "Z",
            "decoder": "pymatching",
        },
        "formula": (
            "p(w,d) = 1 - exp(-10^(a*d + b*log10(W(w)) + c)); "
            f"W(w) = {formula_weight}"
        ),
        "coefficients": {"a": float(a), "b": float(b), "c": float(c)},
        "coefficient_standard_errors": {
            "a": float(standard_errors[0]),
            "b": float(standard_errors[1]),
            "c": float(standard_errors[2]),
        },
        "coefficient_ci95": {
            "a": [float(a - 1.96 * standard_errors[0]), float(a + 1.96 * standard_errors[0])],
            "b": [float(b - 1.96 * standard_errors[1]), float(b + 1.96 * standard_errors[1])],
            "c": [float(c - 1.96 * standard_errors[2]), float(c + 1.96 * standard_errors[2])],
        },
        "transformed_hazard_r_squared": float(transformed_r_squared),
        "negative_log_likelihood": float(optimum.fun),
        "aic": float(2 * 3 + 2 * optimum.fun),
        "fitted_rows": fitted_rows.to_dict(orient="records"),
    }


def _fit_weight_one_control(
    frame: pd.DataFrame,
    *,
    error_column: str,
    rate_column: str,
) -> dict:
    """Fit the separate no-coupler, 2d-round single-Z control."""

    control = frame[frame["weight"] == 1].copy()
    errors = control[error_column].to_numpy(dtype=float)
    shots = control["shots"].to_numpy(dtype=float)
    distances = control["distance"].to_numpy(dtype=float)
    design = np.column_stack((distances, np.ones(len(distances))))
    observed = errors / shots
    initial = np.linalg.lstsq(
        design,
        np.log10(-np.log1p(-observed)),
        rcond=None,
    )[0]

    def objective(parameters: np.ndarray) -> float:
        hazard = np.power(10.0, np.clip(design @ parameters, -50, 30))
        probability = np.clip(-np.expm1(-hazard), 1e-15, 1 - 1e-15)
        return float(
            -(
                errors * np.log(probability)
                + (shots - errors) * np.log1p(-probability)
            ).sum()
        )

    optimum = minimize(objective, initial, method="Nelder-Mead")
    if not optimum.success:
        raise RuntimeError(f"Weight-one fit failed for {rate_column}: {optimum.message}")

    predicted = -np.expm1(-np.power(10.0, design @ optimum.x))
    transformed_observed = np.log10(-np.log1p(-observed))
    transformed_predicted = np.log10(-np.log1p(-predicted))
    residual_sum = float(
        np.square(transformed_observed - transformed_predicted).sum()
    )
    total_sum = float(
        np.square(transformed_observed - transformed_observed.mean()).sum()
    )
    fitted_rows = control[
        ["weight", "distance", "shots", error_column, rate_column]
    ].copy()
    fitted_rows["predicted_ler"] = predicted
    return {
        "metric": rate_column,
        "protocol": "single-patch Z memory/readout; no coupler; 2d SE rounds",
        "formula": "p_1(d) = 1 - exp(-10^(a*d + c))",
        "coefficients": {
            "a": float(optimum.x[0]),
            "c": float(optimum.x[1]),
        },
        "transformed_hazard_r_squared": 1 - residual_sum / total_sum,
        "negative_log_likelihood": float(optimum.fun),
        "fitted_rows": fitted_rows.to_dict(orient="records"),
    }


def _plot(frame: pd.DataFrame, fit: dict, output: Path) -> None:
    fig, axis = plt.subplots(figsize=(5.8, 4.2), constrained_layout=True)
    colors = {3: "#386cb0", 5: "#fdb462", 7: "#7fc97f"}

    coefficients = fit["coefficients"]
    for distance in sorted(frame["distance"].unique()):
        subset = frame[frame["distance"] == distance]
        coupled = subset[subset["weight"] >= 2]
        axis.scatter(
            coupled["weight"],
            coupled["logical_error_rate"],
            color=colors.get(int(distance)),
            label=f"d={int(distance)} simulation",
            zorder=3,
        )
        control = subset[subset["weight"] == 1]
        if not control.empty:
            axis.scatter(
                control["weight"],
                control["logical_error_rate"],
                color=colors.get(int(distance)),
                marker="x",
                s=50,
                zorder=4,
            )

        dense_weight = np.geomspace(2, frame["weight"].max(), 200)
        model_weight = (
            2 * np.ceil(dense_weight / 2)
            if fit["weight_definition"] == "paired"
            else dense_weight
        )
        exponent = (
            coefficients["a"] * distance
            + coefficients["b"] * np.log10(model_weight)
            + coefficients["c"]
        )
        prediction = -np.expm1(-np.power(10.0, exponent))
        axis.plot(dense_weight, prediction, color=colors.get(int(distance)))

    axis.set_xscale("log", base=2)
    axis.set_yscale("log")
    axis.set_xlabel("PPM weight / participating patches")
    axis.set_ylabel("Decoded PPM parity failure probability")
    axis.set_title("Multi-patch PPM circuit-level fit")
    axis.grid(True, which="both", alpha=0.22)
    axis.legend(fontsize=8)
    fig.savefig(output)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--p", type=float, default=1e-3)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_RESULTS / "multi_patch_ppm_fit_p1e-3.json",
    )
    parser.add_argument(
        "--output-plot",
        type=Path,
        default=DEFAULT_RESULTS / "multi_patch_ppm_fit_p1e-3.svg",
    )
    args = parser.parse_args()

    frame = load_results(args.input, args.p)
    candidates = [
        _fit_bounded_hazard(
            frame,
            error_column="errors",
            rate_column="logical_error_rate",
            weight_definition=weight_definition,
        )
        for weight_definition in ("raw", "paired")
    ]
    selected = min(candidates, key=lambda candidate: candidate["aic"])
    comparison = {
        "selected_weight_definition": selected["weight_definition"],
        "delta_aic_raw_minus_paired": candidates[0]["aic"] - candidates[1]["aic"],
        "candidates": candidates,
    }
    control = _fit_weight_one_control(
        frame,
        error_column="errors",
        rate_column="logical_error_rate",
    )
    payload = {
        "experiment": {
            "code": "unrotated surface code",
            "protocol": "multi-patch Z-product lattice surgery",
            "initial_state": "Z",
            "measurement_basis": "Z",
            "physical_error_rate": args.p,
            "syndrome_rounds_before_coupling": "d",
            "syndrome_rounds_with_coupler": "d",
            "weight_one_control": "no coupler; 2d syndrome rounds",
            "decoder": "PyMatching",
        },
        "fit": selected,
        "weight_one_control": control,
        "model_comparison": comparison,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_plot.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, indent=2) + "\n")
    _plot(frame, selected, args.output_plot)

    coeff = selected["coefficients"]
    print(
        f"decoded PPM parity [{selected['weight_definition']}]: "
        f"p(w,d)=1-exp(-10^("
        f"{coeff['a']:.9f} d + {coeff['b']:.9f} log10(W(w)) "
        f"+ {coeff['c']:.9f})); "
        f"R2_hazard={selected['transformed_hazard_r_squared']:.6f}"
    )
    print(
        "  delta AIC (raw - paired) = "
        f"{comparison['delta_aic_raw_minus_paired']:.3f}"
    )


if __name__ == "__main__":
    main()
