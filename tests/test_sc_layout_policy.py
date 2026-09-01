from __future__ import annotations

import pytest

from heteqsys.architecture.errors import ArchitectureValidationError
from heteqsys.architecture.gallery.na_cf import (
    PROFILE as NA_PROFILE,
    make_layout_policy as make_na_layout_policy,
)
from heteqsys.architecture.gallery.na_cf.layout import (
    NeutralAtomComputeFactoryLayoutPolicy,
)
from heteqsys.architecture.gallery.sc_cf import (
    PROFILE,
    make_layout_policy,
)
from heteqsys.architecture.gallery.sc_cf.layout import (
    SuperconductingCheckerboardLayoutPolicy,
)
from heteqsys.architecture.identifiers import SubmoduleKey
from heteqsys.architecture.logical_layout import (
    LogicalLayoutGrid,
    LogicalLayoutRequest,
    SubmoduleLayoutRequest,
)
from heteqsys.architecture.logical_layout_policy import LogicalLayoutPolicy
from heteqsys.architecture.sizing import SizingResult


COMPUTE = SubmoduleKey("sc_node", "sc_compute", "compute_region")
MAGIC_INPUT = SubmoduleKey(
    "sc_node", "sc_compute", "magic_state_input_buffer"
)
FACTORY = SubmoduleKey("sc_node", "sc_msf", "factory_engine")
MAGIC_OUTPUT = SubmoduleKey(
    "sc_node", "sc_msf", "magic_state_output_buffer"
)


def _sizing(*, data: int = 4, magic: int = 1, copies: int = 1) -> SizingResult:
    return SizingResult(
        {
            COMPUTE: data,
            MAGIC_INPUT: magic,
            FACTORY: copies,
            MAGIC_OUTPUT: magic,
        }
    )


def _request(
    submodules: dict[SubmoduleKey, SubmoduleLayoutRequest],
) -> LogicalLayoutRequest:
    return LogicalLayoutRequest(submodules=submodules)


def test_sc_policy_materializes_checkerboard_owned_patch_layout() -> None:
    policy = make_layout_policy()
    assert isinstance(policy, LogicalLayoutPolicy)
    assert SuperconductingCheckerboardLayoutPolicy.__bases__ == (
        LogicalLayoutPolicy,
    )

    result = policy.place(
        PROFILE,
        _sizing(magic=3, copies=2),
    )

    compute = result.layout_for(COMPUTE)
    assert compute.slot_ids == ("slot_0", "slot_1", "slot_2", "slot_3")
    assert compute.grid == LogicalLayoutGrid(rows=3, columns=3)
    assert [compute.coordinate_for(slot) for slot in compute.slot_ids] == [
        (2, 0),
        (4, 0),
        (2, 2),
        (4, 2),
    ]

    magic_input = result.layout_for(MAGIC_INPUT)
    assert magic_input.slot_ids == ("slot_0", "slot_1", "slot_2")
    assert [
        magic_input.coordinate_for(slot) for slot in magic_input.slot_ids
    ] == [(6, 0), (6, 2), (8, 0)]

    output = result.layout_for(MAGIC_OUTPUT)
    assert output.slot_ids == ("slot_0", "slot_1", "slot_2")
    assert [output.coordinate_for(slot) for slot in output.slot_ids] == [
        (10, 0),
        (10, 2),
        (12, 0),
    ]
    assert FACTORY not in result.submodules

    # The Architecture owns occupied checkerboard positions only. A compiler
    # derives the unoccupied routing sites, edges, and logical couplers later.
    for layout in result.submodules.values():
        assert not hasattr(layout, "routing_nodes")
        assert not hasattr(layout, "routing_edges")
        assert not hasattr(layout, "logical_couplers")


def test_sc_grid_request_reserves_rows_used_by_dependent_buffers() -> None:
    result = make_layout_policy().place(
        PROFILE,
        _sizing(magic=4),
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
    assert compute.grid == LogicalLayoutGrid(rows=5, columns=7)
    assert compute.logical_origin == (10, -3)
    assert [compute.coordinate_for(slot) for slot in compute.slot_ids] == [
        (10, -3),
        (12, -3),
        (14, -3),
        (16, -3),
    ]
    assert [
        result.layout_for(MAGIC_INPUT).coordinate_for(f"slot_{index}")
        for index in range(4)
    ] == [(18, -3), (18, -1), (18, 1), (20, -3)]
    assert [
        result.layout_for(MAGIC_OUTPUT).coordinate_for(f"slot_{index}")
        for index in range(4)
    ] == [(22, -3), (22, -1), (22, 1), (24, -3)]


def test_sc_exact_compute_slots_drive_default_east_edge_placement() -> None:
    result = make_layout_policy().place(
        PROFILE,
        _sizing(),
        request=_request(
            {
                COMPUTE: SubmoduleLayoutRequest(
                    logical_origin=(-5, 7),
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

    compute = result.layout_for(COMPUTE)
    assert dict(compute.coordinates) == {
        "slot_0": (0, 0),
        "slot_1": (3, 0),
        "slot_2": (0, 2),
        "slot_3": (3, 2),
    }
    assert compute.coordinate_for("slot_1") == (-2, 7)
    assert result.layout_for(MAGIC_INPUT).coordinate_for("slot_0") == (0, 7)
    assert result.layout_for(MAGIC_OUTPUT).coordinate_for("slot_0") == (2, 7)


def test_explicit_magic_input_layout_moves_derived_factory_output() -> None:
    result = make_layout_policy().place(
        PROFILE,
        _sizing(magic=6),
        request=_request(
            {
                MAGIC_INPUT: SubmoduleLayoutRequest(
                    logical_origin=(20, 30),
                    slots={
                        "slot_0": (0, 0),
                        "slot_1": (0, 4),
                        "slot_2": (1, 0),
                        "slot_3": (1, 4),
                        "slot_4": (2, 0),
                        "slot_5": (2, 4),
                    },
                )
            }
        ),
    )

    assert result.layout_for(MAGIC_INPUT).coordinate_for("slot_1") == (20, 34)
    assert [
        result.layout_for(MAGIC_OUTPUT).coordinate_for(f"slot_{index}")
        for index in range(6)
    ] == [
        (24, 30),
        (24, 32),
        (24, 34),
        (26, 30),
        (26, 32),
        (26, 34),
    ]


def test_sc_compute_origin_is_explicit_policy_state() -> None:
    result = SuperconductingCheckerboardLayoutPolicy(
        compute_origin=(-2, 5),
    ).place(PROFILE, _sizing())

    assert result.layout_for(COMPUTE).coordinate_for("slot_0") == (-2, 5)
    assert result.layout_for(MAGIC_INPUT).coordinate_for("slot_0") == (2, 5)
    assert result.layout_for(MAGIC_OUTPUT).coordinate_for("slot_0") == (4, 5)


def test_sc_grid_request_expands_into_a_checkerboard_canvas_envelope() -> None:
    result = make_layout_policy().place(
        PROFILE,
        _sizing(),
        request=_request(
            {
                COMPUTE: SubmoduleLayoutRequest(
                    grid=LogicalLayoutGrid(rows=2, columns=2)
                )
            }
        ),
    )

    compute = result.layout_for(COMPUTE)
    assert compute.grid == LogicalLayoutGrid(rows=3, columns=3)
    assert dict(compute.coordinates) == {
        "slot_0": (0, 0),
        "slot_1": (2, 0),
        "slot_2": (0, 2),
        "slot_3": (2, 2),
    }


def test_each_compute_factory_bundle_owns_its_layout_recipe() -> None:
    neutral_atom = make_na_layout_policy()
    assert isinstance(neutral_atom, NeutralAtomComputeFactoryLayoutPolicy)
    assert neutral_atom.magic_output_origin == (100, 40)
    assert isinstance(
        make_layout_policy(),
        SuperconductingCheckerboardLayoutPolicy,
    )


def test_sc_policy_fails_closed_for_a_non_sc_compute_factory_profile() -> None:
    profile = NA_PROFILE
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
        match="requires a superconducting Node",
    ):
        make_layout_policy().place(profile, sizing)


def test_na_policy_fails_closed_for_an_sc_compute_factory_profile() -> None:
    profile = PROFILE
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
        match="requires a neutral-atom Node",
    ):
        make_na_layout_policy().place(profile, sizing)
