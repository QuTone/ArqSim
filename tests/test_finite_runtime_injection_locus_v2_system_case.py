"""Contracts for the canonical-locus successor System Case."""

from __future__ import annotations

import pytest

from system_cases.finite_runtime_injection_demo_locus_v2 import run as locus_case


def test_locus_v2_manifest_records_immutable_predecessor() -> None:
    system_case = locus_case.load_system_case()

    assert system_case.id == "finite_runtime_injection_demo_locus_v2"
    assert system_case.document["lineage"] == {
        "predecessor_id": "finite_runtime_injection_demo",
        "change": "canonical_execution_locus_metadata_v2",
    }
    assert system_case.document["reference"]["mutation_policy"] == (
        "immutable_create_successor_do_not_overwrite"
    )
    assert locus_case.build_config(system_case).workflow_id == (
        "finite_runtime_injection_demo_locus_v2:clifford_t_toy__2.3"
    )


def test_locus_v2_reference_is_archived_after_measurement_v3() -> None:
    verified = locus_case.verify_reference(rerun=False)

    assert verified.report["report_hash"] == verified.receipt["hashes"]["report"]
    assert verified.report["artifacts"]["execution_plan"]["plan_hash"] == (
        verified.receipt["hashes"]["execution_plan"]
    )

    engines = {
        engine["id"]: engine
        for engine in verified.report["artifacts"]["execution_plan"][
            "architectural_state"
        ]["engines"]
    }
    assert engines["bell_engine:compute_msf_link"]["module"] == (
        "compute_msf_link/bell_engine"
    )
    assert engines["bell_engine:compute_msf_link"]["submodule"] == (
        "compute_msf_link/bell_engine/pair_generator"
    )
    assert engines["store_load_buffer"]["submodule"] == (
        "na_compute_node/na_compute/store_load_buffer"
    )
    assert "module" not in engines["resource_move"]
    assert "submodule" not in engines["resource_move"]

    with pytest.raises(
        locus_case.SystemCaseError,
        match="immutable predecessor is replay-only",
    ):
        locus_case.verify_reference(rerun=True)
