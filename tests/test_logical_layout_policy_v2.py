from __future__ import annotations

from copy import deepcopy

import pytest

from heteqsys.architecture.errors import ArchitectureValidationError
from heteqsys.architecture.gallery.na_c_plus_sc_f import (
    PROFILE as REMOTE_MAGIC_PROFILE,
)
from heteqsys.architecture.gallery.na_cf import (
    PROFILE,
    make_layout_policy,
)
from heteqsys.architecture.gallery.na_cf.layout import (
    NeutralAtomComputeFactoryLayoutPolicy,
)
from heteqsys.architecture.identifiers import SubmoduleKey
from heteqsys.architecture.logical_layout import (
    LogicalLayoutGrid,
    LogicalLayoutRequest,
    SubmoduleLayoutRequest,
)
from heteqsys.architecture.logical_layout_policy import LogicalLayoutPolicy
from heteqsys.architecture.profile import ArchitectureProfile
from heteqsys.architecture.sizing import SizingResult


COMPUTE = SubmoduleKey("na_node", "na_compute", "compute_region")
MAGIC_INPUT = SubmoduleKey(
    "na_node", "na_compute", "magic_state_input_buffer"
)
FACTORY = SubmoduleKey("na_node", "na_msf", "factory_engine")
MAGIC_OUTPUT = SubmoduleKey(
    "na_node", "na_msf", "magic_state_output_buffer"
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


def _policy() -> NeutralAtomComputeFactoryLayoutPolicy:
    """Return the concrete NA-CF gallery placement."""

    return make_layout_policy()


def test_compute_factory_policy_implements_the_interface_and_reproduces_11() -> None:
    policy = _policy()
    assert isinstance(policy, LogicalLayoutPolicy)
    assert NeutralAtomComputeFactoryLayoutPolicy.__bases__ == (
        LogicalLayoutPolicy,
    )
    with pytest.raises(TypeError, match="abstract"):
        LogicalLayoutPolicy()
    with pytest.raises(TypeError, match="magic_output_origin"):
        NeutralAtomComputeFactoryLayoutPolicy()

    result = policy.place(
        PROFILE,
        _sizing(magic=3, copies=2),
    )

    compute = result.layout_for(COMPUTE)
    assert compute.slot_ids == ("slot_0", "slot_1", "slot_2", "slot_3")
    assert compute.grid == LogicalLayoutGrid(rows=2, columns=2)
    assert [compute.coordinate_for(slot) for slot in compute.slot_ids] == [
        (0, 0),
        (1, 0),
        (0, 1),
        (1, 1),
    ]

    magic_input = result.layout_for(MAGIC_INPUT)
    assert magic_input.slot_ids == ("slot_0", "slot_1", "slot_2")
    assert [
        magic_input.coordinate_for(slot) for slot in magic_input.slot_ids
    ] == [(3, 0), (3, 1), (4, 0)]

    assert FACTORY not in result.submodules

    output = result.layout_for(MAGIC_OUTPUT)
    assert output.slot_ids == ("slot_0", "slot_1", "slot_2")
    assert output.logical_origin == (100, 40)
    assert [output.coordinate_for(slot) for slot in output.slot_ids] == [
        (100, 40),
        (101, 40),
        (100, 41),
    ]


def test_layout_requires_sizing_to_cover_interconnect_resources_exactly() -> None:
    with pytest.raises(ValueError, match="does not cover the Profile exactly"):
        _policy().place(
            REMOTE_MAGIC_PROFILE,
            SizingResult({}),
        )


def test_authored_compute_grid_moves_the_dependent_right_edge_buffer() -> None:
    request = _request(
        {
            COMPUTE: SubmoduleLayoutRequest(
                logical_origin=(10, -3),
                grid=LogicalLayoutGrid(rows=3, columns=4),
            )
        }
    )

    result = _policy().place(
        PROFILE,
        _sizing(),
        request=request,
    )

    compute = result.layout_for(COMPUTE)
    assert compute.grid == LogicalLayoutGrid(rows=3, columns=4)
    assert compute.logical_origin == (10, -3)
    assert result.layout_for(MAGIC_INPUT).coordinate_for("slot_0") == (15, -3)


def test_compute_origin_override_moves_the_dependent_right_edge_buffer() -> None:
    request = _request(
        {
            COMPUTE: SubmoduleLayoutRequest(
                logical_origin=(10, -3)
            )
        }
    )

    result = _policy().place(
        PROFILE,
        _sizing(),
        request=request,
    )

    assert result.layout_for(COMPUTE).logical_origin == (10, -3)
    assert result.layout_for(MAGIC_INPUT).coordinate_for("slot_0") == (13, -3)


def test_policy_parameters_hold_all_11_geometry_constants() -> None:
    result = NeutralAtomComputeFactoryLayoutPolicy(
        compute_origin=(-2, 5),
        right_edge_offset=5,
        magic_output_origin=(12, 14),
    ).place(PROFILE, _sizing())

    assert result.layout_for(COMPUTE).logical_origin == (-2, 5)
    assert result.layout_for(MAGIC_INPUT).coordinate_for("slot_0") == (4, 5)
    assert result.layout_for(MAGIC_OUTPUT).logical_origin == (12, 14)


def test_profile_declaration_order_does_not_change_logical_layout() -> None:
    profile = PROFILE
    document = profile.to_dict()
    node = document["nodes"]["na_node"]
    node["modules"] = dict(reversed(tuple(node["modules"].items())))
    for module in node["modules"].values():
        module["submodules"] = dict(
            reversed(tuple(module["submodules"].items()))
        )
    reordered = ArchitectureProfile.from_dict(document)

    assert reordered.profile_hash == profile.profile_hash
    baseline = _policy().place(profile, _sizing())
    result = _policy().place(reordered, _sizing())
    assert result.submodules == baseline.submodules


def test_slot_override_changes_coordinates_but_not_materialized_identity() -> None:
    request = _request(
        {
            MAGIC_OUTPUT: SubmoduleLayoutRequest(
                logical_origin=(-10, 8),
                slots={"slot_0": (3, 4)},
            )
        }
    )

    output = _policy().place(
        PROFILE,
        _sizing(),
        request=request,
    ).layout_for(MAGIC_OUTPUT)

    assert output.slot_ids == ("slot_0",)
    assert dict(output.coordinates) == {"slot_0": (3, 4)}
    assert output.coordinate_for("slot_0") == (-7, 12)


def test_explicit_zero_origin_is_not_treated_as_missing() -> None:
    request = _request(
        {
            MAGIC_OUTPUT: SubmoduleLayoutRequest(
                logical_origin=(0, 0),
                slots={"slot_0": (200, 0)},
            )
        }
    )

    output = _policy().place(
        PROFILE,
        _sizing(),
        request=request,
    ).layout_for(MAGIC_OUTPUT)

    assert output.logical_origin == (0, 0)
    assert output.coordinate_for("slot_0") == (200, 0)


def test_explicit_coordinates_default_an_identity_only_origin_to_zero() -> None:
    document = deepcopy(PROFILE.to_dict())
    document["nodes"]["na_node"]["modules"]["na_compute"]["submodules"][
        "scratch_buffer"
    ] = {"type": "buffer", "payload": "logical_qubit"}
    profile = ArchitectureProfile.from_dict(document)
    scratch = SubmoduleKey("na_node", "na_compute", "scratch_buffer")
    capacities = dict(_sizing().capacities)
    capacities[scratch] = 1
    request = _request(
        {
            scratch: SubmoduleLayoutRequest(
                slots={"slot_0": (50, 50)}
            )
        }
    )

    layout = _policy().place(
        profile,
        SizingResult(capacities),
        request=request,
    ).layout_for(scratch)

    assert layout.logical_origin == (0, 0)
    assert layout.coordinate_for("slot_0") == (50, 50)


def test_layout_policy_requires_exact_sizing_coverage() -> None:
    incomplete = SizingResult(
        {
            COMPUTE: 4,
            MAGIC_INPUT: 1,
            FACTORY: 1,
        }
    )

    with pytest.raises(
        ArchitectureValidationError,
        match="does not cover the Profile exactly",
    ):
        _policy().place(
            PROFILE, incomplete
        )


def test_layout_policy_rejects_geometry_for_an_engine() -> None:
    request = _request(
        {
            FACTORY: SubmoduleLayoutRequest(
                logical_origin=(0, 0)
            )
        }
    )

    with pytest.raises(
        ArchitectureValidationError,
        match="Engine Submodules cannot have logical slot geometry",
    ):
        _policy().place(
            PROFILE,
            _sizing(),
            request=request,
        )


def test_layout_policy_rejects_an_unknown_submodule_request() -> None:
    unknown = SubmoduleKey("na_node", "na_compute", "unknown_buffer")
    request = _request(
        {unknown: SubmoduleLayoutRequest(logical_origin=(0, 0))}
    )

    with pytest.raises(
        ArchitectureValidationError,
        match="unknown Submodule",
    ):
        _policy().place(
            PROFILE,
            _sizing(),
            request=request,
        )
