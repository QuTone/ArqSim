"""Independent acceptance gates for the Profile-1.2 superconducting architecture."""

from __future__ import annotations

from typing import Any

import pytest

import heteqsys.architecture.construction as canonical_construction
from heteqsys.architecture import (
    LogicalLayoutGrid,
    LogicalLayoutRequest,
    SubmoduleKey,
    SubmoduleLayoutRequest,
    get_architecture_profile,
)
from heteqsys.architecture.specification import (
    ArchitectureSpecification as CoreArchitectureSpecification,
)
from heteqsys.compiler.layout import materialize_compute_layout
from heteqsys.specification import build_architecture_specification
from tests.architecture_semantic_oracle import oracle_circuit


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value))
    return set()


def test_12_build_is_direct_and_uses_the_generic_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Load the canonical authoring Profile before guarding private-template IO.
    get_architecture_profile("1.2")
    calls = 0
    generic_resolver = canonical_construction.resolve_architecture

    def resolve_spy(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return generic_resolver(*args, **kwargs)

    monkeypatch.setattr(canonical_construction, "resolve_architecture", resolve_spy)
    result = build_architecture_specification(
        oracle_circuit(),
        "1.2",
    )

    assert calls == 1
    assert isinstance(result, CoreArchitectureSpecification)


def test_12_core_owns_checkerboard_patches_but_no_routing_or_couplers() -> None:
    core = build_architecture_specification(
        oracle_circuit(),
        "1.2",
    )
    assert CoreArchitectureSpecification.from_dict(core.to_dict()) == core
    assert [node.id for node in core.nodes] == ["sc_node"]
    assert core.interconnects == ()

    compute = core.submodule("sc_node", "sc_compute", "compute_region")
    magic_input = core.submodule(
        "sc_node", "sc_compute", "magic_state_input_buffer"
    )
    factory = core.submodule("sc_node", "sc_msf", "factory_engine")
    magic_output = core.submodule(
        "sc_node", "sc_msf", "magic_state_output_buffer"
    )
    assert (
        compute.capacity,
        magic_input.capacity,
        factory.capacity,
        magic_output.capacity,
    ) == (4, 1, 1, 1)
    assert [
        core.effective_slot_coordinate(
            "sc_node", "sc_compute", "compute_region", slot.id
        )
        for slot in compute.slots
    ] == [(2, 0), (4, 0), (2, 2), (4, 2)]
    assert core.effective_slot_coordinate(
        "sc_node", "sc_compute", "magic_state_input_buffer", "slot_0"
    ) == (6, 0)
    assert core.effective_slot_coordinate(
        "sc_node", "sc_msf", "magic_state_output_buffer", "slot_0"
    ) == (8, 0)
    assert factory.slots == ()
    assert factory.resource_protocol is not None
    assert compute.qec is not None and compute.qec.to_dict() == {
        "code": "surface",
        "parameters": {"distance": 13},
    }
    assert core.node("sc_node").connections[0].to_dict() == {
        "id": "sc_magic_bus",
        "direction": "directed",
        "endpoints": [
            "sc_msf/magic_state_output_buffer",
            "sc_compute/magic_state_input_buffer",
        ],
    }

    forbidden = {
        "routing_fabric",
        "routing_nodes",
        "routing_edges",
        "routing_interfaces",
        "interfaces",
        "logical_couplers",
        "couplers",
    }
    assert forbidden.isdisjoint(_all_keys(core.to_dict()))


def test_12_compiler_derives_routing_as_the_checkerboard_complement() -> None:
    specification = build_architecture_specification(
        oracle_circuit(),
        "1.2",
    )
    layout = materialize_compute_layout(specification)

    assert [slot.coordinate for slot in layout.slots_of_kind("data")] == [
        (2.0, 0.0),
        (4.0, 0.0),
        (2.0, 2.0),
        (4.0, 2.0),
    ]
    assert [slot.coordinate for slot in layout.slots_of_kind("magic_state")] == [
        (6.0, 0.0)
    ]
    assert layout.routing_nodes
    assert layout.routing_edges
    assert layout.metadata["routing_derivation"] == "occupied_slot_complement"
    output_coordinate = specification.effective_slot_coordinate(
        "sc_node",
        "sc_msf",
        "magic_state_output_buffer",
        "slot_0",
    )
    assert output_coordinate == (8, 0)
    assert list(output_coordinate) in layout.metadata["routing_occupied_points"]


@pytest.mark.parametrize(
    ("input_request", "delta_x"),
    (
        (
            SubmoduleLayoutRequest(
                logical_origin=(0, 0),
                slots={"slot_0": (20, 30)},
            ),
            2,
        ),
        (
            SubmoduleLayoutRequest(
                logical_origin=(10, 7),
                grid=LogicalLayoutGrid(rows=2, columns=2),
            ),
            4,
        ),
    ),
    ids=("exact-input-slots", "input-grid-envelope"),
)
def test_12_output_tracks_an_explicitly_moved_magic_input(
    input_request: SubmoduleLayoutRequest,
    delta_x: int,
) -> None:
    request = LogicalLayoutRequest(
        {
            SubmoduleKey(
                "sc_node", "sc_compute", "magic_state_input_buffer"
            ): input_request
        }
    )
    core = build_architecture_specification(
        oracle_circuit(),
        "1.2",
        logical_layout=request,
    )
    core_input = core.effective_slot_coordinate(
        "sc_node", "sc_compute", "magic_state_input_buffer", "slot_0"
    )
    core_output = core.effective_slot_coordinate(
        "sc_node", "sc_msf", "magic_state_output_buffer", "slot_0"
    )
    assert core_input is not None and core_output is not None
    assert core_output == (core_input[0] + delta_x, core_input[1])


def test_12_sparse_exact_input_does_not_collapse_derived_output_slots() -> None:
    request = LogicalLayoutRequest(
        {
            SubmoduleKey(
                "sc_node", "sc_compute", "magic_state_input_buffer"
            ): SubmoduleLayoutRequest(
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
    )
    specification = build_architecture_specification(
        oracle_circuit(),
        "1.2",
        logical_layout=request,
        policy_overrides={"protocols.magic_state.buffer_capacity": 6},
    )
    output = specification.submodule(
        "sc_node", "sc_msf", "magic_state_output_buffer"
    )
    coordinates = [
        specification.effective_slot_coordinate(
            "sc_node", "sc_msf", output.id, slot.id
        )
        for slot in output.slots
    ]

    assert coordinates == [
        (24, 30),
        (24, 32),
        (24, 34),
        (26, 30),
        (26, 32),
        (26, 34),
    ]
    assert len(coordinates) == len(set(coordinates))


@pytest.mark.parametrize(
    ("output_request", "buffer_capacity"),
    (
        (SubmoduleLayoutRequest(logical_origin=(20, 20)), 1),
        (
            SubmoduleLayoutRequest(
                logical_origin=(20, 20),
                grid=LogicalLayoutGrid(rows=2, columns=2),
            ),
            4,
        ),
    ),
    ids=("origin", "grid"),
)
def test_12_any_explicit_output_layout_has_adjacent_derived_routing_sites(
    output_request: SubmoduleLayoutRequest,
    buffer_capacity: int,
) -> None:
    request = LogicalLayoutRequest(
        {
            SubmoduleKey(
                "sc_node", "sc_msf", "magic_state_output_buffer"
            ): output_request
        }
    )
    specification = build_architecture_specification(
        oracle_circuit(),
        "1.2",
        logical_layout=request,
        policy_overrides={
            "protocols.magic_state.buffer_capacity": buffer_capacity
        },
    )
    output = specification.submodule(
        "sc_node", "sc_msf", "magic_state_output_buffer"
    )
    compiler_layout = materialize_compute_layout(specification)
    routing = {
        node: tuple(coordinate)
        for node, coordinate in compiler_layout.metadata[
            "routing_coordinates"
        ].items()
    }
    routing_sites = set(routing.values())

    assert len(output.slots) == buffer_capacity
    for slot in output.slots:
        coordinate = specification.effective_slot_coordinate(
            "sc_node", "sc_msf", output.id, slot.id
        )
        assert coordinate is not None
        adjacent = [
            candidate
            for candidate in routing.values()
            if abs(coordinate[0] - candidate[0])
            + abs(coordinate[1] - candidate[1])
            == 1
        ]
        assert coordinate not in routing_sites
        assert adjacent
