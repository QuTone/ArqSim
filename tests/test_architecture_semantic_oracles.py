from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.behavior_baseline_support import assert_semantic_subset
from tests.architecture_semantic_oracle import (
    MULTI_NODE_PROFILE_IDS,
    PROFILE_IDS,
    semantic_oracle,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "architecture_semantic_oracles"


def _expected(profile_id: str):
    return json.loads(
        (FIXTURE_ROOT / f"profile-{profile_id}.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.mark.parametrize("profile_id", PROFILE_IDS)
def test_six_profile_semantic_oracle(profile_id: str) -> None:
    """Gate domain behavior without comparing a legacy report projection."""

    actual = semantic_oracle(profile_id)
    expected = _expected(profile_id)
    # Use the tolerant comparator in both directions: it accepts insignificant
    # floating-point drift but rejects additive shape drift in the compact
    # semantic contract.
    assert_semantic_subset(actual, expected)
    assert_semantic_subset(expected, actual)


@pytest.mark.parametrize("profile_id", sorted(MULTI_NODE_PROFILE_IDS))
def test_bell_pair_is_shared_but_footprint_is_distributed(
    profile_id: str,
) -> None:
    """Encode the owner-approved Bell-pair ownership/placement semantics."""

    oracle = semantic_oracle(profile_id)
    architecture = oracle["architecture"]
    footprint = oracle["results"]["footprint"]

    assert len(architecture["interconnects"]) == 1
    link = architecture["interconnects"][0]
    model = link["bell_state_model"]
    assert link["ownership"] == "shared_interconnect"
    assert model["pair_token_identity"] == "single_shared_identity"
    assert model["endpoint_halves_per_pair"] == 2
    assert model["logical_qubit_states_total"] == 2 * model["pair_capacity"]

    assert len(footprint["interconnects"]) == 1
    accounted = footprint["interconnects"][0]
    assert accounted["shared_pair_capacity"] == model["pair_capacity"]
    assert len(accounted["endpoints"]) == 2
    assert {
        endpoint["logical_qubit_states"] for endpoint in accounted["endpoints"]
    } == {model["pair_capacity"]}
    assert all(
        endpoint["component_count"] == 2
        and endpoint["physical_qubits"] > 0
        for endpoint in accounted["endpoints"]
    )

    components = footprint["bell_endpoint_components"]
    assert len(components) == 4
    assert [component["kind"] for component in components].count("engine") == 2
    assert [component["kind"] for component in components].count("buffer_half") == 2
    assert sum(
        component["quantity"]
        for component in components
        if component["kind"] == "buffer_half"
    ) == 2 * model["pair_capacity"]


@pytest.mark.parametrize("profile_id", ("2.1", "2.2", "2.3"))
def test_bb_memory_slots_are_identity_only(profile_id: str) -> None:
    """BB slots keep logical identity; visualization coordinates are derived."""

    oracle = semantic_oracle(profile_id)
    memory_regions = [
        submodule
        for node in oracle["architecture"]["nodes"]
        for module in node["modules"]
        for submodule in module["submodules"]
        if module["type"] == "memory" and submodule["type"] == "region"
    ]

    assert len(memory_regions) == 1
    region = memory_regions[0]
    assert region["qec"]["code"] == "bb"
    assert region["qec"]["parameters"]["k"] == 12
    assert region["slot_semantics"] == "identity_only"
    assert all(set(slot) == {"id"} for slot in region["slots"])


@pytest.mark.parametrize("profile_id", PROFILE_IDS)
def test_compact_oracle_excludes_v1_projection_authorities(
    profile_id: str,
) -> None:
    serialized = json.dumps(_expected(profile_id), sort_keys=True)

    for legacy_name in (
        "layout_plan",
        "resolved_system",
        "construction_adapter",
        "architecture_slot_layout",
        "runtime_components",
        "logical_architecture_hash",
        "report_hash",
    ):
        assert legacy_name not in serialized


def test_six_profile_oracles_are_independent_of_python_hash_seed() -> None:
    command = [
        sys.executable,
        "-c",
        (
            "import json; "
            "from tests.architecture_semantic_oracle import "
            "PROFILE_IDS, semantic_oracle_digest; "
            "print(json.dumps({p: semantic_oracle_digest(p) "
            "for p in PROFILE_IDS}, sort_keys=True))"
        ),
    ]
    outputs = []
    for seed in ("1", "997"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            command,
            cwd=Path(__file__).parents[1],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(completed.stdout.strip())
    assert outputs[0] == outputs[1]
