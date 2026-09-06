from __future__ import annotations

from dataclasses import replace

import pytest

from arqsim.evaluation import (
    FidelityCoverageGaps,
    FidelityEstimate,
    IncompleteFidelityCoverageError,
    fidelity_coverage_gaps,
    require_complete_fidelity,
)


def _complete_estimate() -> FidelityEstimate:
    return FidelityEstimate(
        success_probability=1.0,
        failure_probability=0.0,
        log_success_by_operation={},
        log_success_by_logical_operation={},
        log_success_by_idle_location={},
        log_success_by_resource_output={},
        log_success_by_resource_idle_location={},
        architecture_operation_counts={},
        logical_operation_counts={},
        idle_cycles_by_location={},
        consumed_resource_counts={},
        resource_idle_cycles_by_location={},
        unprofiled_operation_counts={},
        unprofiled_logical_operation_counts={},
        unprofiled_idle_exposure_s={},
        unprofiled_resource_output_counts={},
        unprofiled_resource_idle_exposure_s={},
        complete_coverage=True,
        profile_hash="a" * 64,
    )


def test_require_complete_fidelity_returns_the_complete_estimate() -> None:
    estimate = _complete_estimate()

    gaps = fidelity_coverage_gaps(estimate)

    assert isinstance(gaps, FidelityCoverageGaps)
    assert gaps.is_empty
    assert gaps.nonempty_fields == ()
    assert all(not values for values in gaps.to_dict().values())
    assert require_complete_fidelity(estimate) is estimate


@pytest.mark.parametrize(
    ("field", "missing"),
    (
        ("unprofiled_operation_counts", {"MOVE_QUBITS": 2}),
        ("unprofiled_logical_operation_counts", {"rz": 1}),
        ("unprofiled_idle_exposure_s", {"node/compute": 0.5}),
        ("unprofiled_resource_output_counts", {"theta_state": 1}),
        (
            "unprofiled_resource_idle_exposure_s",
            {"link/bell_storage/output": 0.25},
        ),
    ),
)
def test_require_complete_fidelity_identifies_each_gap_dimension(
    field: str,
    missing: dict[str, int | float],
) -> None:
    estimate = replace(
        _complete_estimate(),
        **{field: missing, "complete_coverage": False},
    )

    gaps = fidelity_coverage_gaps(estimate)

    assert not gaps.is_empty
    assert gaps.nonempty_fields == (field,)
    assert gaps.to_dict()[field] == missing
    with pytest.raises(IncompleteFidelityCoverageError) as raised:
        require_complete_fidelity(estimate)
    assert raised.value.code == "fidelity_coverage_incomplete"
    assert raised.value.gaps == gaps
    assert raised.value.details["coverage_gaps"] == gaps.to_dict()
    assert field in str(raised.value)


def test_require_complete_fidelity_rejects_an_inconsistent_coverage_flag() -> None:
    estimate = replace(_complete_estimate(), complete_coverage=False)

    with pytest.raises(ValueError, match="disagrees with its coverage gaps"):
        require_complete_fidelity(estimate)


def test_fidelity_coverage_gaps_rejects_the_wrong_object_type() -> None:
    with pytest.raises(TypeError, match="estimate must be a FidelityEstimate"):
        fidelity_coverage_gaps(object())  # type: ignore[arg-type]
