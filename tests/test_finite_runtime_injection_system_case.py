"""Contracts for the finite runtime-injection System Case."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from heteqsys import EvaluationConfig
from heteqsys.evaluation import RuntimeInjectionMode
import pytest

from system_cases.finite_runtime_injection_demo import run as injection_case


def test_manifest_strictly_freezes_the_owned_profile_23_request() -> None:
    system_case = injection_case.load_system_case()
    request = injection_case.build_request(system_case)
    config = injection_case.build_config(system_case)

    assert system_case.id == "finite_runtime_injection_demo"
    assert system_case.document["ownership"] == {
        "source": "arqsim_owned_toy_circuit",
        "license": "Apache-2.0",
        "contributors": ["ArqSim authors"],
    }
    assert system_case.document["workload"]["expected"] == {
        "num_qubits": 4,
        "depth": 6,
        "operation_count": 8,
        "t_count": 2,
    }
    assert request["case_id"] == injection_case.CASE_ID
    assert request["semantic_manifest_hash"] == (
        system_case.semantic_manifest_hash
    )
    assert request["workload"]["semantic_hash"]
    assert config.profile_id == "2.3"
    assert config.workflow_id == (
        "finite_runtime_injection_demo:clifford_t_toy__2.3"
    )
    assert config.fidelity_profile == "canonical_reference_v1"
    assert config.latency_profile.provenance["reaction_latency_profile"] == (
        "reference_reaction_latency_profile_v1"
    )
    assert config.evaluation_policy.seed == 5
    assert config.evaluation_policy.trace_level == "full"
    assert config.evaluation_policy.runtime_injection_mode == (
        RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
    )


def test_finite_mode_is_explicit_and_black_box_remains_the_global_default() -> None:
    assert EvaluationConfig(profile_id="2.3").evaluation_policy.runtime_injection_mode == (
        RuntimeInjectionMode.BLACK_BOX
    )
    system_case = injection_case.load_system_case()
    assert injection_case.build_config(
        system_case
    ).evaluation_policy.runtime_injection_mode == (
        RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
    )


def test_checked_in_report_strictly_replays_both_t_branches_and_fidelity() -> None:
    verified = injection_case.verify_reference(rerun=False)
    receipt = verified.receipt
    injection = receipt["runtime_injection"]
    results = receipt["results"]

    system_case = injection_case.load_system_case()
    assert receipt["hashes"]["semantic_manifest"] == (
        system_case.semantic_manifest_hash
    )

    assert injection["recipe_id"] == "surface_code.t_injection.v1"
    assert injection["recipe_convention"] == (
        "cx_data_magic_measure_magic_z_v1"
    )
    assert [
        item["outcome_bit"] for item in injection["invocations"]
    ] == [1, 0]
    assert [
        item["steps"] for item in injection["invocations"]
    ] == [
        ["source", "reaction", "correction"],
        ["source", "reaction"],
    ]
    assert [
        item["logical_s_materialized"]
        for item in injection["invocations"]
    ] == [True, False]
    assert results["all_invariants_passed"] is True
    assert results["fidelity_complete_coverage"] is True
    assert results["logical_operation_counts"] == {
        "cx": 2,
        "h": 4,
        "s": 1,
        "t": 2,
    }
    assert results["consumed_resource_counts"] == {
        "logical_bell_pair": 2,
        "magic_state": 2,
    }
    assert not any(results["unprofiled"].values())
    assert results["logical_idle_cycles_total"] > 0
    assert results["resource_idle_cycles_total"] > 0


def test_live_public_api_run_is_byte_identical_to_the_reference() -> None:
    system_case = injection_case.load_system_case()
    reference = injection_case.verify_reference(rerun=False)
    live = injection_case.run_case(system_case)

    assert live.request == reference.request
    assert live.report.to_json(indent=2) == reference.report.to_json(indent=2)
    assert live.receipt == reference.receipt
    assert live.report.report_hash == live.receipt["hashes"]["report"]
    assert live.report.execution_trace.trace_hash == live.receipt[
        "hashes"
    ]["execution_trace"]


def test_manifest_rejects_an_unknown_contract_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_load = injection_case._load_manifest_document

    def load_with_unknown_field(payload: bytes) -> object:
        document = original_load(payload)
        document["implicit_semantic_change"] = True
        return document

    monkeypatch.setattr(
        injection_case,
        "_load_manifest_document",
        load_with_unknown_field,
    )
    with pytest.raises(
        injection_case.SystemCaseError,
        match="implicit_semantic_change",
    ):
        injection_case.load_system_case()


def test_manifest_rejects_duplicate_yaml_keys(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_bytes(
        injection_case.MANIFEST_PATH.read_bytes()
        + b"\nid: duplicate_finite_runtime_injection_demo\n"
    )
    monkeypatch.setattr(injection_case, "MANIFEST_PATH", manifest)

    with pytest.raises(injection_case.SystemCaseError, match="duplicate key 'id'"):
        injection_case.load_system_case()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_outcomes", [True, False]),
        ("expected_logical_s_correction_count", True),
    ],
)
def test_manifest_rejects_boolean_values_for_integer_acceptance_fields(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    original_load = injection_case._load_manifest_document

    def load_with_boolean_count(payload: bytes) -> object:
        document = original_load(payload)
        document["acceptance"][field] = value
        return document

    monkeypatch.setattr(
        injection_case,
        "_load_manifest_document",
        load_with_boolean_count,
    )
    with pytest.raises(injection_case.SystemCaseError, match="must be an integer"):
        injection_case.load_system_case()


def test_semantic_manifest_hash_excludes_only_reference_file_digests() -> None:
    system_case = injection_case.load_system_case()
    changed_digests = deepcopy(system_case.document)
    for record in changed_digests["reference"]["files"].values():
        record["sha256"] = "0" * 64

    assert injection_case._semantic_manifest_hash(changed_digests) == (
        system_case.semantic_manifest_hash
    )

    changed_acceptance = deepcopy(system_case.document)
    changed_acceptance["acceptance"]["require_all_invariants"] = False
    assert injection_case._semantic_manifest_hash(changed_acceptance) != (
        system_case.semantic_manifest_hash
    )


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        ("request", "Frozen request is not canonical JSON"),
        ("receipt", "Frozen receipt is not canonical JSON"),
    ],
)
def test_no_rerun_rejects_noncanonical_reference_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    artifact: str,
    message: str,
) -> None:
    system_case = injection_case.load_system_case()
    reference_paths = dict(injection_case._reference_paths(system_case))
    source = reference_paths[artifact]
    noncanonical = tmp_path / source.name
    noncanonical.write_text(
        json.dumps(json.loads(source.read_text(encoding="utf-8"))),
        encoding="utf-8",
    )
    reference_paths[artifact] = noncanonical

    monkeypatch.setattr(injection_case, "load_system_case", lambda: system_case)
    monkeypatch.setattr(
        injection_case,
        "_reference_paths",
        lambda _system_case: reference_paths,
    )
    with pytest.raises(injection_case.SystemCaseError, match=message):
        injection_case.verify_reference(rerun=False)
