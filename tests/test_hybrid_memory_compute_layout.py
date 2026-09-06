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
from arqsim.architecture.gallery.na_m_plus_sc_cf import (
    HybridMemoryComputeLayoutPolicy,
    make_layout_policy,
)
from arqsim.architecture.profile import ArchitectureProfile
from arqsim.architecture.sizing import SizingResult


MEMORY = SubmoduleKey(
    "na_memory_node", "na_memory", "memory_region"
)
MEMORY_STORE_LOAD = SubmoduleKey(
    "na_memory_node", "na_memory", "store_load_buffer"
)
COMPUTE = SubmoduleKey(
    "sc_compute_node", "sc_compute", "compute_region"
)
COMPUTE_STORE_LOAD = SubmoduleKey(
    "sc_compute_node", "sc_compute", "store_load_buffer"
)
MAGIC_INPUT = SubmoduleKey(
    "sc_compute_node", "sc_compute", "magic_state_input_buffer"
)
FACTORY = SubmoduleKey(
    "sc_compute_node", "sc_msf", "factory_engine"
)
MAGIC_OUTPUT = SubmoduleKey(
    "sc_compute_node", "sc_msf", "magic_state_output_buffer"
)
BELL_ENGINE = SubmoduleKey(
    "memory_compute_link", "bell_engine", "pair_generator"
)
BELL_BUFFER = SubmoduleKey(
    "memory_compute_link", "bell_storage", "bell_buffer"
)


def _sizing(
    *,
    memory: int = 2,
    store_load: int = 2,
    compute: int = 4,
    magic: int = 2,
    factory: int = 1,
    bell: int = 2,
    bell_engine: int = 2,
) -> SizingResult:
    return SizingResult(
        {
            MEMORY: memory,
            MEMORY_STORE_LOAD: store_load,
            COMPUTE: compute,
            COMPUTE_STORE_LOAD: store_load,
            MAGIC_INPUT: magic,
            FACTORY: factory,
            MAGIC_OUTPUT: magic,
            BELL_ENGINE: bell_engine,
            BELL_BUFFER: bell,
        }
    )


def _coordinates(result, target: SubmoduleKey):
    layout = result.layout_for(target)
    return [layout.coordinate_for(slot_id) for slot_id in layout.slot_ids]


def test_profile_22_catalog_selects_the_explicit_hybrid_recipe() -> None:
    policy = make_layout_policy()

    assert isinstance(policy, HybridMemoryComputeLayoutPolicy)
    assert policy.compute_origin == (2, 0)
    assert policy.west_edge_offset == 2


def test_hybrid_layout_separates_owner_local_canvases() -> None:
    result = make_layout_policy().place(
        get_architecture_profile("2.2"),
        _sizing(),
    )

    for target in (MEMORY, MEMORY_STORE_LOAD, BELL_BUFFER):
        layout = result.layout_for(target)
        assert layout.slot_ids == ("slot_0", "slot_1")
        assert dict(layout.coordinates) == {}
        assert layout.logical_origin is None
        assert layout.grid is None

    assert _coordinates(result, COMPUTE) == [
        (2, 0),
        (4, 0),
        (2, 2),
        (4, 2),
    ]
    assert result.layout_for(COMPUTE).grid == LogicalLayoutGrid(
        rows=3,
        columns=3,
    )
    assert _coordinates(result, COMPUTE_STORE_LOAD) == [(0, 0), (0, 1)]
    assert _coordinates(result, MAGIC_INPUT) == [(6, 0), (6, 2)]
    assert _coordinates(result, MAGIC_OUTPUT) == [(8, 0), (8, 2)]
    assert FACTORY not in result.submodules
    assert BELL_ENGINE not in result.submodules

    for layout in result.submodules.values():
        assert not hasattr(layout, "routing_nodes")
        assert not hasattr(layout, "routing_edges")
        assert not hasattr(layout, "logical_couplers")


def test_store_load_boundary_fills_canvas_height_before_growing_west() -> None:
    result = make_layout_policy().place(
        get_architecture_profile("2.2"),
        _sizing(compute=6, store_load=6),
    )

    assert _coordinates(result, COMPUTE_STORE_LOAD) == [
        (0, 0),
        (0, 1),
        (0, 2),
        (0, 3),
        (-1, 0),
        (-1, 1),
    ]


def test_compute_request_drives_its_checkerboard_boundaries() -> None:
    request = LogicalLayoutRequest(
        {
            COMPUTE: SubmoduleLayoutRequest(
                logical_origin=(10, -4),
                grid=LogicalLayoutGrid(rows=3, columns=4),
            )
        }
    )
    result = make_layout_policy().place(
        get_architecture_profile("2.2"),
        _sizing(compute=6, store_load=3, magic=4),
        request=request,
    )

    assert _coordinates(result, COMPUTE) == [
        (10, -4),
        (12, -4),
        (14, -4),
        (16, -4),
        (10, -2),
        (12, -2),
    ]
    assert _coordinates(result, COMPUTE_STORE_LOAD) == [
        (8, -4),
        (8, -3),
        (8, -2),
    ]
    assert _coordinates(result, MAGIC_INPUT) == [
        (18, -4),
        (18, -2),
        (18, 0),
        (20, -4),
    ]
    assert _coordinates(result, MAGIC_OUTPUT) == [
        (22, -4),
        (22, -2),
        (22, 0),
        (24, -4),
    ]


def test_endpoint_request_can_spatialize_only_the_node_endpoint() -> None:
    request = LogicalLayoutRequest(
        {
            MEMORY_STORE_LOAD: SubmoduleLayoutRequest(
                logical_origin=(-20, 7),
                slots={"slot_0": (0, 0), "slot_1": (1, 0)},
            ),
            COMPUTE_STORE_LOAD: SubmoduleLayoutRequest(
                logical_origin=(30, 9),
                slots={"slot_0": (0, 0), "slot_1": (0, 2)},
            ),
        }
    )
    result = make_layout_policy().place(
        get_architecture_profile("2.2"),
        _sizing(),
        request=request,
    )

    assert _coordinates(result, MEMORY_STORE_LOAD) == [(-20, 7), (-19, 7)]
    assert _coordinates(result, COMPUTE_STORE_LOAD) == [(30, 9), (30, 11)]
    assert dict(result.layout_for(MEMORY).coordinates) == {}
    assert dict(result.layout_for(BELL_BUFFER).coordinates) == {}


def test_identity_only_memory_rejects_operational_geometry() -> None:
    policy = make_layout_policy()
    profile = get_architecture_profile("2.2")

    moved = policy.place(
        profile,
        _sizing(),
        request=LogicalLayoutRequest(
            {MEMORY: SubmoduleLayoutRequest(logical_origin=(10, 20))}
        ),
    ).layout_for(MEMORY)
    assert moved.logical_origin == (10, 20)
    assert dict(moved.coordinates) == {}

    with pytest.raises(
        ArchitectureValidationError,
        match="identity but no operational coordinates",
    ):
        policy.place(
            profile,
            _sizing(),
            request=LogicalLayoutRequest(
                {
                    MEMORY: SubmoduleLayoutRequest(
                        slots={"slot_0": (0, 0), "slot_1": (1, 0)}
                    )
                }
            ),
        )


def test_explicit_bell_request_spatializes_only_shared_interconnect_slots() -> None:
    result = make_layout_policy().place(
        get_architecture_profile("2.2"),
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
        (2, 0),
        (4, 0),
        (2, 2),
        (4, 2),
    ]


@pytest.mark.parametrize("connection_kind", ("bell", "magic"))
def test_hybrid_layout_rejects_duplicate_required_connections(
    connection_kind: str,
) -> None:
    document = deepcopy(get_architecture_profile("2.2").to_dict())
    if connection_kind == "bell":
        connections = document["interconnects"]["memory_compute_link"][
            "connections"
        ]
        connections["duplicate_delivery"] = deepcopy(
            connections["engine_to_storage"]
        )
    else:
        connections = document["nodes"]["sc_compute_node"]["connections"]
        connections["duplicate_magic_bus"] = deepcopy(
            connections["sc_magic_bus"]
        )
    profile = ArchitectureProfile.from_dict(document)

    with pytest.raises(ArchitectureValidationError, match="exactly one"):
        make_layout_policy().place(profile, _sizing())


def test_empty_memory_has_no_geometry() -> None:
    result = make_layout_policy().place(
        get_architecture_profile("2.2"),
        _sizing(memory=0),
    )
    memory = result.layout_for(MEMORY)

    assert memory.slot_ids == ()
    assert dict(memory.coordinates) == {}
    assert memory.logical_origin is None
    assert memory.grid is None


def test_layout_matches_semantics_after_owner_and_module_renames() -> None:
    document = deepcopy(get_architecture_profile("2.2").to_dict())
    document["id"] = "renamed_hybrid_layout"
    memory_node = document["nodes"].pop("na_memory_node")
    compute_node = document["nodes"].pop("sc_compute_node")
    document["nodes"] = {"vault_owner": memory_node, "processor_owner": compute_node}

    memory = memory_node["modules"].pop("na_memory")
    memory_node["modules"]["vault"] = memory
    compute = compute_node["modules"].pop("sc_compute")
    factory = compute_node["modules"].pop("sc_msf")
    compute_node["modules"] = {"processor": compute, "source": factory}
    compute_node["connections"]["sc_magic_bus"]["endpoints"] = [
        "source/magic_state_output_buffer",
        "processor/magic_state_input_buffer",
    ]

    interconnect = document["interconnects"].pop("memory_compute_link")
    document["interconnects"]["fabric"] = interconnect
    interconnect["endpoints"] = [
        "vault_owner/vault/store_load_buffer",
        "processor_owner/processor/store_load_buffer",
    ]
    profile = ArchitectureProfile.from_dict(document)
    capacities = {
        SubmoduleKey("vault_owner", "vault", "memory_region"): 2,
        SubmoduleKey("vault_owner", "vault", "store_load_buffer"): 2,
        SubmoduleKey("processor_owner", "processor", "compute_region"): 4,
        SubmoduleKey("processor_owner", "processor", "store_load_buffer"): 2,
        SubmoduleKey(
            "processor_owner", "processor", "magic_state_input_buffer"
        ): 2,
        SubmoduleKey("processor_owner", "source", "factory_engine"): 1,
        SubmoduleKey(
            "processor_owner", "source", "magic_state_output_buffer"
        ): 2,
        SubmoduleKey("fabric", "bell_engine", "pair_generator"): 2,
        SubmoduleKey("fabric", "bell_storage", "bell_buffer"): 2,
    }

    result = HybridMemoryComputeLayoutPolicy().place(
        profile,
        SizingResult(capacities),
    )

    assert _coordinates(
        result,
        SubmoduleKey("processor_owner", "processor", "store_load_buffer"),
    ) == [(0, 0), (0, 1)]
    assert dict(
        result.layout_for(
            SubmoduleKey("vault_owner", "vault", "memory_region")
        ).coordinates
    ) == {}
