from __future__ import annotations

from copy import deepcopy

import pytest

from heteqsys.architecture.errors import ArchitectureValidationError
from heteqsys.architecture.identifiers import SubmoduleKey
from heteqsys.architecture.logical_layout import (
    LogicalLayoutGrid,
    LogicalLayoutRequest,
    SubmoduleLayoutRequest,
)
from heteqsys.architecture.logical_layout_policy import LogicalLayoutPolicy
from heteqsys.architecture.gallery import get_architecture_profile
from heteqsys.architecture.gallery.na_mcf import (
    NeutralAtomMemoryComputeLayoutPolicy,
    make_layout_policy,
)
from heteqsys.architecture.profile import ArchitectureProfile
from heteqsys.architecture.sizing import SizingResult


MEMORY = SubmoduleKey("na_node", "na_memory", "memory_region")
COMPUTE = SubmoduleKey("na_node", "na_compute", "compute_region")
STORE_LOAD = SubmoduleKey("na_node", "na_compute", "store_load_buffer")
MAGIC_INPUT = SubmoduleKey(
    "na_node", "na_compute", "magic_state_input_buffer"
)
FACTORY = SubmoduleKey("na_node", "na_msf", "factory_engine")
MAGIC_OUTPUT = SubmoduleKey(
    "na_node", "na_msf", "magic_state_output_buffer"
)


def _sizing(
    *,
    memory: int = 2,
    compute: int = 2,
    store_load: int = 2,
    magic: int = 1,
    copies: int = 1,
) -> SizingResult:
    return SizingResult(
        {
            MEMORY: memory,
            COMPUTE: compute,
            STORE_LOAD: store_load,
            MAGIC_INPUT: magic,
            FACTORY: copies,
            MAGIC_OUTPUT: magic,
        }
    )


def _policy() -> NeutralAtomMemoryComputeLayoutPolicy:
    return NeutralAtomMemoryComputeLayoutPolicy(magic_output_origin=(200, 40))


def _request(
    submodules: dict[SubmoduleKey, SubmoduleLayoutRequest],
) -> LogicalLayoutRequest:
    return LogicalLayoutRequest(submodules=submodules)


def test_memory_compute_policy_materializes_the_compact_21_recipe() -> None:
    policy = _policy()
    assert isinstance(policy, LogicalLayoutPolicy)
    assert NeutralAtomMemoryComputeLayoutPolicy.__bases__ == (
        LogicalLayoutPolicy,
    )

    result = policy.place(get_architecture_profile("2.1"), _sizing())

    memory = result.layout_for(MEMORY)
    assert memory.slot_ids == ("slot_0", "slot_1")
    assert dict(memory.coordinates) == {}
    assert memory.logical_origin is None
    assert memory.grid is None

    compute = result.layout_for(COMPUTE)
    assert compute.grid == LogicalLayoutGrid(rows=1, columns=2)
    assert [compute.coordinate_for(slot) for slot in compute.slot_ids] == [
        (0, 0),
        (1, 0),
    ]
    store_load = result.layout_for(STORE_LOAD)
    assert [
        store_load.coordinate_for(slot) for slot in store_load.slot_ids
    ] == [(-3, 0), (-2, 0)]
    magic_input = result.layout_for(MAGIC_INPUT)
    assert [
        magic_input.coordinate_for(slot) for slot in magic_input.slot_ids
    ] == [(3, 0)]
    output = result.layout_for(MAGIC_OUTPUT)
    assert output.coordinate_for("slot_0") == (200, 40)
    assert FACTORY not in result.submodules

    for layout in result.submodules.values():
        assert not hasattr(layout, "routing_nodes")
        assert not hasattr(layout, "routing_edges")
        assert not hasattr(layout, "logical_couplers")


def test_compute_grid_reserves_its_full_west_and_east_edges() -> None:
    result = _policy().place(
        get_architecture_profile("2.1"),
        _sizing(memory=1, compute=6, store_load=3, magic=4),
        request=_request(
            {
                COMPUTE: SubmoduleLayoutRequest(
                    logical_origin=(10, -3),
                    grid=LogicalLayoutGrid(rows=3, columns=4),
                )
            }
        ),
    )

    compute = result.layout_for(COMPUTE)
    assert compute.grid == LogicalLayoutGrid(rows=3, columns=4)
    assert [compute.coordinate_for(slot) for slot in compute.slot_ids] == [
        (10, -3),
        (11, -3),
        (12, -3),
        (13, -3),
        (10, -2),
        (11, -2),
    ]
    store_load = result.layout_for(STORE_LOAD)
    assert [
        store_load.coordinate_for(slot) for slot in store_load.slot_ids
    ] == [(8, -3), (8, -2), (8, -1)]
    magic_input = result.layout_for(MAGIC_INPUT)
    assert [
        magic_input.coordinate_for(slot) for slot in magic_input.slot_ids
    ] == [(15, -3), (15, -2), (15, -1), (16, -3)]


def test_sparse_exact_compute_extent_drives_dependent_buffer_rows() -> None:
    result = _policy().place(
        get_architecture_profile("2.1"),
        _sizing(memory=1, compute=4, store_load=4, magic=4),
        request=_request(
            {
                COMPUTE: SubmoduleLayoutRequest(
                    logical_origin=(10, -5),
                    slots={
                        "slot_0": (0, 0),
                        "slot_1": (3, 0),
                        "slot_2": (0, 2),
                        "slot_3": (3, 2),
                    },
                )
            }
        ),
    )

    store_load = result.layout_for(STORE_LOAD)
    assert [
        store_load.coordinate_for(slot) for slot in store_load.slot_ids
    ] == [(7, -5), (8, -5), (7, -4), (8, -4)]
    magic_input = result.layout_for(MAGIC_INPUT)
    assert [
        magic_input.coordinate_for(slot) for slot in magic_input.slot_ids
    ] == [(15, -5), (15, -4), (15, -3), (16, -5)]


def test_memory_accepts_only_an_origin_for_identity_only_slots() -> None:
    moved = _policy().place(
        get_architecture_profile("2.1"),
        _sizing(),
        request=_request(
            {MEMORY: SubmoduleLayoutRequest(logical_origin=(-20, 7))}
        ),
    ).layout_for(MEMORY)

    assert moved.logical_origin == (-20, 7)
    assert dict(moved.coordinates) == {}

    with pytest.raises(
        ArchitectureValidationError,
        match="identity but no operational coordinates",
    ):
        _policy().place(
            get_architecture_profile("2.1"),
            _sizing(),
            request=_request(
                {
                    MEMORY: SubmoduleLayoutRequest(
                        slots={"slot_0": (0, 0), "slot_1": (1, 0)}
                    )
                }
            ),
        )
    with pytest.raises(
        ArchitectureValidationError,
        match="identity but no operational coordinates",
    ):
        _policy().place(
            get_architecture_profile("2.1"),
            _sizing(),
            request=_request(
                {MEMORY: SubmoduleLayoutRequest(grid=LogicalLayoutGrid(1, 2))}
            ),
        )


def test_empty_memory_has_no_geometry_and_rejects_an_origin_request() -> None:
    memory = _policy().place(
        get_architecture_profile("2.1"),
        _sizing(memory=0),
    ).layout_for(MEMORY)

    assert memory.slot_ids == ()
    assert dict(memory.coordinates) == {}
    assert memory.logical_origin is None
    assert memory.grid is None

    with pytest.raises(
        ArchitectureValidationError,
        match="empty Submodule cannot have logical geometry",
    ):
        _policy().place(
            get_architecture_profile("2.1"),
            _sizing(memory=0),
            request=_request(
                {MEMORY: SubmoduleLayoutRequest(logical_origin=(1, 2))}
            ),
        )


def test_explicit_buffer_requests_override_derived_geometry() -> None:
    result = _policy().place(
        get_architecture_profile("2.1"),
        _sizing(),
        request=_request(
            {
                STORE_LOAD: SubmoduleLayoutRequest(
                    logical_origin=(-20, 30),
                    slots={"slot_0": (0, 0), "slot_1": (0, 1)},
                ),
                MAGIC_INPUT: SubmoduleLayoutRequest(
                    logical_origin=(20, 30),
                    slots={"slot_0": (4, 5)},
                ),
                MAGIC_OUTPUT: SubmoduleLayoutRequest(
                    logical_origin=(40, 50),
                    slots={"slot_0": (6, 7)},
                ),
            }
        ),
    )

    assert result.layout_for(STORE_LOAD).coordinate_for("slot_1") == (-20, 31)
    assert result.layout_for(MAGIC_INPUT).coordinate_for("slot_0") == (24, 35)
    assert result.layout_for(MAGIC_OUTPUT).coordinate_for("slot_0") == (46, 57)


def test_policy_configuration_owns_all_numeric_geometry_choices() -> None:
    result = NeutralAtomMemoryComputeLayoutPolicy(
        magic_output_origin=(50, 60),
        compute_origin=(10, 20),
        west_edge_offset=4,
        right_edge_offset=5,
    ).place(get_architecture_profile("2.1"), _sizing())

    assert result.layout_for(STORE_LOAD).coordinate_for("slot_1") == (6, 20)
    assert result.layout_for(MAGIC_INPUT).coordinate_for("slot_0") == (16, 20)
    assert result.layout_for(MAGIC_OUTPUT).coordinate_for("slot_0") == (50, 60)


def test_policy_fails_closed_when_memory_compute_semantics_are_missing() -> None:
    profile = get_architecture_profile("1.1")
    sizing = SizingResult(
        {
            SubmoduleKey(node.id, module.id, submodule.id): 1
            for node in profile.nodes
            for module in node.modules
            for submodule in module.submodules
        }
    )

    with pytest.raises(
        ArchitectureValidationError,
        match="exactly one memory region",
    ):
        _policy().place(profile, sizing)


def test_policy_matches_semantics_instead_of_profile_or_component_ids() -> None:
    document = deepcopy(get_architecture_profile("2.1").to_dict())
    document["id"] = "renamed-memory-compute"
    node = document["nodes"].pop("na_node")
    document["nodes"]["owner"] = node
    modules = node["modules"]
    modules["memory_bank"] = modules.pop("na_memory")
    modules["processor"] = modules.pop("na_compute")
    modules["factory"] = modules.pop("na_msf")
    modules["memory_bank"]["submodules"]["storage"] = modules[
        "memory_bank"
    ]["submodules"].pop("memory_region")
    processor = modules["processor"]["submodules"]
    processor["data"] = processor.pop("compute_region")
    processor["exchange"] = processor.pop("store_load_buffer")
    processor["magic_in"] = processor.pop("magic_state_input_buffer")
    factory = modules["factory"]["submodules"]
    factory["producer"] = factory.pop("factory_engine")
    factory["magic_out"] = factory.pop("magic_state_output_buffer")
    node["connections"]["na_memory_compute_bus"]["endpoints"] = [
        "memory_bank/storage",
        "processor/exchange",
    ]
    node["connections"]["na_magic_bus"]["endpoints"] = [
        "factory/magic_out",
        "processor/magic_in",
    ]
    profile = ArchitectureProfile.from_dict(document)
    capacities = {
        SubmoduleKey("owner", "memory_bank", "storage"): 2,
        SubmoduleKey("owner", "processor", "data"): 2,
        SubmoduleKey("owner", "processor", "exchange"): 2,
        SubmoduleKey("owner", "processor", "magic_in"): 1,
        SubmoduleKey("owner", "factory", "producer"): 1,
        SubmoduleKey("owner", "factory", "magic_out"): 1,
    }

    result = _policy().place(profile, SizingResult(capacities))

    assert result.layout_for(
        SubmoduleKey("owner", "processor", "exchange")
    ).coordinate_for("slot_1") == (-2, 0)
    assert result.layout_for(
        SubmoduleKey("owner", "processor", "magic_in")
    ).coordinate_for("slot_0") == (3, 0)


def test_bundled_catalog_selects_the_explicit_21_preset() -> None:
    policy = make_layout_policy()
    assert isinstance(policy, NeutralAtomMemoryComputeLayoutPolicy)
    assert policy.magic_output_origin == (200, 40)
