from __future__ import annotations

from copy import deepcopy

import pytest

from arqsim.architecture.errors import ArchitectureValidationError
from arqsim.architecture.identifiers import SubmoduleKey
from arqsim.architecture.logical_layout import (
    LogicalLayoutGrid,
    LogicalLayoutRequest,
    SubmoduleLayoutRequest,
)
from arqsim.architecture.gallery import get_architecture_profile
from arqsim.architecture.gallery.na_mc_plus_sc_f import (
    RemoteMagicMemoryComputeLayoutPolicy,
    make_layout_policy,
)
from arqsim.architecture.profile import ArchitectureProfile
from arqsim.architecture.sizing import SizingResult


MEMORY = SubmoduleKey(
    "na_compute_node", "na_memory", "memory_region"
)
COMPUTE = SubmoduleKey(
    "na_compute_node", "na_compute", "compute_region"
)
STORE_LOAD = SubmoduleKey(
    "na_compute_node", "na_compute", "store_load_buffer"
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
            MEMORY: 6,
            COMPUTE: 4,
            STORE_LOAD: 2,
            MAGIC_INPUT: 2,
            FACTORY: 3,
            MAGIC_OUTPUT: 2,
            BELL_ENGINE: 3,
            BELL_BUFFER: 2,
        }
    )


def _coordinates(layout, target: SubmoduleKey):
    placement = layout.layout_for(target)
    return [
        placement.coordinate_for(slot_id) for slot_id in placement.slot_ids
    ]


def _policy() -> RemoteMagicMemoryComputeLayoutPolicy:
    return RemoteMagicMemoryComputeLayoutPolicy(magic_output_origin=(0, 40))


def _exact_slots(target: SubmoduleKey) -> dict[str, tuple[int, int]]:
    return {
        f"slot_{index}": (index, 0)
        for index in range(_sizing().capacity_for(target))
    }


def test_profile_23_layout_uses_three_independent_owner_canvases() -> None:
    result = _policy().place(get_architecture_profile("2.3"), _sizing())

    memory = result.layout_for(MEMORY)
    assert memory.slot_ids == tuple(f"slot_{index}" for index in range(6))
    assert dict(memory.coordinates) == {}
    assert memory.logical_origin is None
    assert memory.grid is None

    assert _coordinates(result, COMPUTE) == [
        (0, 0),
        (1, 0),
        (0, 1),
        (1, 1),
    ]
    assert _coordinates(result, STORE_LOAD) == [(-2, 0), (-2, 1)]
    assert _coordinates(result, MAGIC_INPUT) == [(3, 0), (3, 1)]
    assert _coordinates(result, MAGIC_OUTPUT) == [(0, 40), (1, 40)]

    bell = result.layout_for(BELL_BUFFER)
    assert bell.slot_ids == ("slot_0", "slot_1")
    assert dict(bell.coordinates) == {}
    assert bell.logical_origin is None
    assert bell.grid is None
    assert FACTORY not in result.submodules
    assert BELL_ENGINE not in result.submodules

    for placement in result.submodules.values():
        assert not hasattr(placement, "routing_nodes")
        assert not hasattr(placement, "routing_edges")
        assert not hasattr(placement, "logical_couplers")


def test_profile_23_catalog_selects_the_explicit_layout_recipe() -> None:
    policy = make_layout_policy()

    assert isinstance(policy, RemoteMagicMemoryComputeLayoutPolicy)
    assert policy.compute_origin == (0, 0)
    assert policy.magic_output_origin == (0, 40)
    assert policy.west_edge_offset == 2
    assert policy.right_edge_offset == 2


def test_empty_memory_keeps_zero_slots_and_no_geometry() -> None:
    capacities = dict(_sizing().capacities)
    capacities[MEMORY] = 0

    memory = _policy().place(
        get_architecture_profile("2.3"),
        SizingResult(capacities),
    ).layout_for(MEMORY)

    assert memory.slot_ids == ()
    assert dict(memory.coordinates) == {}
    assert memory.logical_origin is None
    assert memory.grid is None


def test_compute_request_moves_only_its_dependent_local_buffers() -> None:
    request = LogicalLayoutRequest(
        {
            COMPUTE: SubmoduleLayoutRequest(
                logical_origin=(10, -3),
                grid=LogicalLayoutGrid(rows=3, columns=4),
            )
        }
    )
    result = _policy().place(
        get_architecture_profile("2.3"),
        _sizing(),
        request=request,
    )

    assert _coordinates(result, COMPUTE) == [
        (10, -3),
        (11, -3),
        (12, -3),
        (13, -3),
    ]
    assert _coordinates(result, STORE_LOAD) == [(8, -3), (8, -2)]
    assert _coordinates(result, MAGIC_INPUT) == [(15, -3), (15, -2)]
    assert _coordinates(result, MAGIC_OUTPUT) == [(0, 40), (1, 40)]
    assert dict(result.layout_for(BELL_BUFFER).coordinates) == {}


def test_identity_only_memory_rejects_operational_coordinates() -> None:
    with pytest.raises(
        ArchitectureValidationError,
        match="identity but no operational coordinates",
    ):
        _policy().place(
            get_architecture_profile("2.3"),
            _sizing(),
            request=LogicalLayoutRequest(
                {
                    MEMORY: SubmoduleLayoutRequest(
                        slots=_exact_slots(MEMORY)
                    )
                }
            ),
        )


def test_explicit_bell_request_spatializes_only_shared_interconnect_slots() -> None:
    result = _policy().place(
        get_architecture_profile("2.3"),
        _sizing(),
        request=LogicalLayoutRequest(
            {
                BELL_BUFFER: SubmoduleLayoutRequest(
                    logical_origin=(10, 20),
                    slots={"slot_0": (0, 0), "slot_1": (1, 0)},
                )
            }
        ),
    )

    assert _coordinates(result, BELL_BUFFER) == [(10, 20), (11, 20)]
    assert dict(result.layout_for(MEMORY).coordinates) == {}
    assert _coordinates(result, COMPUTE) == [
        (0, 0),
        (1, 0),
        (0, 1),
        (1, 1),
    ]


def test_layout_matches_semantics_after_component_renaming() -> None:
    document = deepcopy(get_architecture_profile("2.3").to_dict())
    document["id"] = "renamed-profile"
    local = document["nodes"].pop("na_compute_node")
    remote = document["nodes"].pop("sc_msf_node")
    document["nodes"] = {"remote-owner": remote, "local-owner": local}
    memory = local["modules"].pop("na_memory")
    compute = local["modules"].pop("na_compute")
    local["modules"] = {"processor": compute, "vault": memory}
    memory["submodules"] = {
        "stored-data": memory["submodules"].pop("memory_region")
    }
    compute["submodules"] = {
        "work": compute["submodules"].pop("compute_region"),
        "exchange": compute["submodules"].pop("store_load_buffer"),
        "magic-in": compute["submodules"].pop("magic_state_input_buffer"),
    }
    local["connections"]["na_memory_compute_bus"]["endpoints"] = [
        "vault/stored-data",
        "processor/exchange",
    ]
    source = remote["modules"].pop("sc_msf")
    remote["modules"] = {"source": source}
    source["submodules"] = {
        "producer": source["submodules"].pop("factory_engine"),
        "magic-out": source["submodules"].pop("magic_state_output_buffer"),
    }
    link = document["interconnects"].pop("compute_msf_link")
    document["interconnects"] = {"shared-domain": link}
    link["endpoints"] = [
        "local-owner/processor/magic-in",
        "remote-owner/source/magic-out",
    ]
    generator = link["modules"].pop("bell_engine")
    storage = link["modules"].pop("bell_storage")
    link["modules"] = {"pair-store": storage, "pair-source": generator}
    generator["submodules"] = {
        "produce": generator["submodules"].pop("pair_generator")
    }
    storage["submodules"] = {
        "hold": storage["submodules"].pop("bell_buffer")
    }
    link["connections"]["engine_to_storage"]["endpoints"] = [
        "pair-source/produce",
        "pair-store/hold",
    ]
    profile = ArchitectureProfile.from_dict(document)
    capacities = {
        SubmoduleKey(
            owner.id,
            module.id,
            submodule.id,
        ): (
            3 if submodule.type == "engine" else 2
        )
        for owner in (*profile.nodes, *profile.interconnects)
        for module in owner.modules
        for submodule in module.submodules
    }

    result = _policy().place(profile, SizingResult(capacities))

    assert _coordinates(
        result,
        SubmoduleKey("local-owner", "processor", "exchange"),
    ) == [(-3, 0), (-2, 0)]
    assert _coordinates(
        result,
        SubmoduleKey("remote-owner", "source", "magic-out"),
    ) == [(0, 40), (1, 40)]
