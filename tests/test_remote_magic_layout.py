from __future__ import annotations

from copy import deepcopy

import pytest

from arqsim.architecture.errors import ArchitectureValidationError
from arqsim.architecture.gallery.na_c_plus_sc_f import (
    PROFILE,
    make_layout_policy,
)
from arqsim.architecture.gallery.na_c_plus_sc_f.layout import (
    HybridRemoteMagicLayoutPolicy,
)
from arqsim.architecture.identifiers import SubmoduleKey
from arqsim.architecture.logical_layout import (
    LogicalLayoutGrid,
    LogicalLayoutRequest,
    SubmoduleLayoutRequest,
)
from arqsim.architecture.profile import ArchitectureProfile
from arqsim.architecture.sizing import SizingResult


COMPUTE = SubmoduleKey(
    "na_compute_node", "na_compute", "compute_region"
)
MAGIC_INPUT = SubmoduleKey(
    "na_compute_node", "na_compute", "magic_state_input_buffer"
)
FACTORY = SubmoduleKey("sc_msf_node", "sc_msf", "factory_engine")
MAGIC_OUTPUT = SubmoduleKey(
    "sc_msf_node", "sc_msf", "magic_state_output_buffer"
)
BELL_ENGINE = SubmoduleKey(
    "compute_msf_link", "bell_engine", "pair_generator"
)
BELL_BUFFER = SubmoduleKey(
    "compute_msf_link", "bell_storage", "bell_buffer"
)


def _sizing() -> SizingResult:
    return SizingResult(
        {
            COMPUTE: 4,
            MAGIC_INPUT: 2,
            FACTORY: 1,
            MAGIC_OUTPUT: 2,
            BELL_ENGINE: 1,
            BELL_BUFFER: 2,
        }
    )


def _coordinates(layout, target: SubmoduleKey):
    placement = layout.layout_for(target)
    return [
        placement.coordinate_for(slot_id) for slot_id in placement.slot_ids
    ]


def _sizing_for(profile: ArchitectureProfile) -> SizingResult:
    return SizingResult(
        {
            SubmoduleKey(owner.id, module.id, submodule.id): (
                1 if submodule.type == "engine" else 2
            )
            for owner in (*profile.nodes, *profile.interconnects)
            for module in owner.modules
            for submodule in module.submodules
        }
    )


def test_profile_13_bundle_selects_the_explicit_hybrid_recipe() -> None:
    policy = make_layout_policy()

    assert isinstance(policy, HybridRemoteMagicLayoutPolicy)
    assert policy.compute_origin == (0, 0)
    assert policy.magic_output_origin == (0, 40)
    assert policy.right_edge_offset == 2


def test_hybrid_layout_uses_three_independent_owner_canvases() -> None:
    result = make_layout_policy().place(
        PROFILE,
        _sizing(),
    )

    assert _coordinates(result, COMPUTE) == [
        (0, 0),
        (1, 0),
        (0, 1),
        (1, 1),
    ]
    assert _coordinates(result, MAGIC_INPUT) == [(3, 0), (3, 1)]
    assert _coordinates(result, MAGIC_OUTPUT) == [(0, 40), (1, 40)]
    bell = result.layout_for(BELL_BUFFER)
    assert bell.slot_ids == ("slot_0", "slot_1")
    assert dict(bell.coordinates) == {}
    assert bell.logical_origin is None
    assert bell.grid is None
    assert FACTORY not in result.submodules
    assert BELL_ENGINE not in result.submodules


def test_compute_request_drives_only_its_local_input_edge() -> None:
    request = LogicalLayoutRequest(
        {
            COMPUTE: SubmoduleLayoutRequest(
                logical_origin=(5, 6),
                grid=LogicalLayoutGrid(rows=3, columns=4),
            ),
            BELL_BUFFER: SubmoduleLayoutRequest(
                logical_origin=(10, 20),
                slots={"slot_0": (0, 0), "slot_1": (1, 0)},
            ),
        }
    )
    result = make_layout_policy().place(
        PROFILE,
        _sizing(),
        request=request,
    )

    assert _coordinates(result, COMPUTE) == [
        (5, 6),
        (6, 6),
        (7, 6),
        (8, 6),
    ]
    assert _coordinates(result, MAGIC_INPUT) == [(10, 6), (10, 7)]
    assert _coordinates(result, MAGIC_OUTPUT) == [(0, 40), (1, 40)]
    assert _coordinates(result, BELL_BUFFER) == [(10, 20), (11, 20)]


@pytest.mark.parametrize(
    "malformation",
    ("split_compute_input", "missing_factory", "reverse_bell", "duplicate_bell"),
)
def test_remote_magic_layout_fails_closed_on_family_topology(
    malformation: str,
) -> None:
    document = deepcopy(PROFILE.to_dict())
    document["id"] = "malformed_remote_magic_layout"
    if malformation == "split_compute_input":
        compute = document["nodes"]["na_compute_node"]["modules"]["na_compute"]
        magic_input = compute["submodules"].pop("magic_state_input_buffer")
        document["nodes"]["na_compute_node"]["modules"]["input_port"] = {
            "type": "compute",
            "submodules": {"magic_state_input_buffer": magic_input},
        }
        document["interconnects"]["compute_msf_link"]["endpoints"] = [
            "na_compute_node/input_port/magic_state_input_buffer",
            "sc_msf_node/sc_msf/magic_state_output_buffer",
        ]
    elif malformation == "missing_factory":
        document["nodes"]["sc_msf_node"]["modules"]["sc_msf"][
            "submodules"
        ].pop("factory_engine")
    elif malformation == "reverse_bell":
        document["interconnects"]["compute_msf_link"]["connections"][
            "engine_to_storage"
        ]["endpoints"].reverse()
    else:
        connection = document["interconnects"]["compute_msf_link"][
            "connections"
        ]["engine_to_storage"]
        document["interconnects"]["compute_msf_link"]["connections"][
            "duplicate_delivery"
        ] = deepcopy(connection)
    profile = ArchitectureProfile.from_dict(document)

    with pytest.raises(ArchitectureValidationError):
        make_layout_policy().place(
            profile,
            _sizing_for(profile),
        )
