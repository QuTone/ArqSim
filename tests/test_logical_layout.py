from __future__ import annotations

import pytest

from arqsim import (
    EvaluationConfig,
    run_evaluation,
)
from arqsim.api import load_evaluation_report_document
from arqsim.architecture import (
    ArchitectureValidationError,
    LogicalLayoutGrid,
    LogicalLayoutRequest,
    LogicalLayoutResult,
    SubmoduleKey,
    SubmoduleLayoutRequest,
    SubmoduleLayoutResult,
)
from arqsim.architecture.specification import (
    ArchitectureSpecification,
    Submodule,
)
from arqsim.compiler.layout import materialize_compute_layout
from arqsim.evaluation import (
    PhysicalFootprintModel,
    estimate_physical_footprint,
)
from arqsim.program import FTCircuit, LogicalLayer, LogicalOperation
from arqsim.report_v1 import render_evaluation_report_v1
from arqsim.schema import normalize_json
from arqsim.specification import build_architecture_specification


PROFILE_COMPUTE_OWNERS = {
    "1.1": ("na_node", "na_compute", 1),
    "1.2": ("sc_node", "sc_compute", 2),
    "1.3": ("na_compute_node", "na_compute", 1),
    "2.1": ("na_node", "na_compute", 1),
    "2.2": ("sc_compute_node", "sc_compute", 2),
    "2.3": ("na_compute_node", "na_compute", 1),
}

PROFILE_MEMORY_OWNERS = {
    "2.1": ("na_node", "na_memory"),
    "2.2": ("na_memory_node", "na_memory"),
    "2.3": ("na_compute_node", "na_memory"),
}


def _circuit(*, logical_qubits: int = 4, include_t: bool = True) -> FTCircuit:
    operations = [LogicalOperation("gate", "h", qubits=(0,))]
    if include_t:
        operations.append(LogicalOperation("gate", "t", qubits=(1,)))
    return FTCircuit(
        representation="clifford_t",
        num_qubits=logical_qubits,
        num_clbits=0,
        layers=(LogicalLayer(0, tuple(operations)),),
        provenance={"benchmark": f"logical-layout-{logical_qubits}"},
    )


def _request(
    node_id: str,
    submodules: dict[str, SubmoduleLayoutRequest],
) -> LogicalLayoutRequest:
    return LogicalLayoutRequest(
        submodules={
            SubmoduleKey(node_id, *owner.split("/")): request
            for owner, request in submodules.items()
        }
    )


def test_layout_request_uses_flat_absolute_submodule_keys_on_wire() -> None:
    target = SubmoduleKey("node", "compute", "region")
    request = LogicalLayoutRequest(
        submodules={
            target: SubmoduleLayoutRequest(
                logical_origin=(-4, 3),
                grid=LogicalLayoutGrid(rows=2, columns=3),
            )
        }
    )

    expected = {
        "submodules": {
            "node/compute/region": {
                "logical_origin": [-4, 3],
                "grid": {"rows": 2, "columns": 3},
            }
        }
    }
    assert request.to_dict() == expected
    assert LogicalLayoutRequest.from_dict(expected).to_dict() == expected
    with pytest.raises(TypeError):
        request.submodules[target] = SubmoduleLayoutRequest(  # type: ignore[index]
            logical_origin=(0, 0)
        )


@pytest.mark.parametrize(
    "target",
    (
        "module/submodule",
        "owner/module/submodule/extra",
        "owner//submodule",
    ),
)
def test_layout_request_rejects_noncanonical_submodule_paths(target: str) -> None:
    with pytest.raises(ValueError, match="Submodule layout target"):
        LogicalLayoutRequest.from_dict(
            {
                "submodules": {
                    target: {"logical_origin": [0, 0]},
                }
            }
        )


def test_layout_result_is_complete_immutable_transient_data() -> None:
    target = SubmoduleKey("node", "compute", "region")
    layout = SubmoduleLayoutResult(
        slot_ids=("slot_0", "slot_1"),
        coordinates={"slot_0": (0, 0), "slot_1": (1, 0)},
        logical_origin=(10, 20),
        grid=LogicalLayoutGrid(rows=1, columns=2),
    )
    result = LogicalLayoutResult(submodules={target: layout})

    assert result.layout_for(target).coordinate_for("slot_1") == (11, 20)
    with pytest.raises(TypeError):
        result.submodules[SubmoduleKey("node", "compute", "other")] = layout  # type: ignore[index]
    with pytest.raises(KeyError, match="logical layout has no target"):
        result.layout_for(SubmoduleKey("node", "compute", "missing"))


def _canonical_submodule(
    specification: ArchitectureSpecification,
    submodule_id: str,
    *,
    module_id: str | None = None,
) -> tuple[str, str, Submodule]:
    matches = [
        (owner.id, module.id, submodule)
        for owner in (*specification.nodes, *specification.interconnects)
        for module in owner.modules
        for submodule in module.submodules
        if submodule.id == submodule_id
        and (module_id is None or module.id == module_id)
    ]
    assert len(matches) == 1
    return matches[0]


def _effective_coordinates(
    specification: ArchitectureSpecification,
    owner_id: str,
    module_id: str,
    submodule: Submodule,
) -> list[tuple[int, int] | None]:
    return [
        specification.effective_slot_coordinate(
            owner_id,
            module_id,
            submodule.id,
            slot.id,
        )
        for slot in submodule.slots
    ]


def _report_submodule(
    specification_receipt: dict,
    submodule_id: str,
    *,
    module_id: str | None = None,
) -> dict:
    manifest = specification_receipt["logical_architecture"]
    matches = [
        submodule
        for node in manifest["nodes"]
        for module in node["modules"]
        for submodule in module["submodules"]
        if submodule["id"] == submodule_id
        and (module_id is None or module["id"] == module_id)
    ]
    assert len(matches) == 1
    return matches[0]


def _report_slots(specification_receipt: dict) -> list[dict]:
    manifest = specification_receipt["logical_architecture"]
    return [
        slot
        for node in manifest["nodes"]
        for module in node["modules"]
        for submodule in module["submodules"]
        for slot in submodule.get("slots", ())
    ]


def test_11_custom_grid_origin_flows_through_report_validation() -> None:
    layout = _request(
        "na_node",
        {
            "na_compute/compute_region": SubmoduleLayoutRequest(
                logical_origin=(-4, 3),
                grid=LogicalLayoutGrid(rows=2, columns=2),
            )
        },
    )
    config = EvaluationConfig(
        profile_id="1.1",
        run_label="logical-layout-report-e2e",
        logical_layout=layout,
    )

    first = run_evaluation(_circuit(), config)
    second = run_evaluation(_circuit(), config)
    assert first.to_dict() == second.to_dict()
    assert first.config.to_dict()["logical_layout"] == layout.to_dict()
    assert [
        slot.coordinate
        for slot in first.compiler_layout.slots_of_kind("data")
    ] == [(-4, 3), (-3, 3), (-4, 4), (-3, 4)]

    compute = first.specification.submodule(
        "na_node", "na_compute", "compute_region"
    )
    assert compute.logical_origin == (-4, 3)
    assert _effective_coordinates(
        first.specification,
        "na_node",
        "na_compute",
        compute,
    ) == [
        (-4, 3),
        (-3, 3),
        (-4, 4),
        (-3, 4),
    ]
    report_specification = render_evaluation_report_v1(first)["specification"]
    report_compute = _report_submodule(
        report_specification, "compute_region"
    )
    assert [tuple(slot["coordinate"]) for slot in report_compute["slots"]] == [
        (-4, 3),
        (-3, 3),
        (-4, 4),
        (-3, 4),
    ]
    assert normalize_json(
        load_evaluation_report_document(first.to_json())
    ) == first.to_dict()


@pytest.mark.parametrize("profile_id", tuple(PROFILE_COMPUTE_OWNERS))
def test_all_six_profiles_accept_deterministic_custom_compute_grid(
    profile_id: str,
) -> None:
    node_id, module_id, step = PROFILE_COMPUTE_OWNERS[profile_id]
    layout = _request(
        node_id,
        {
            f"{module_id}/compute_region": SubmoduleLayoutRequest(
                logical_origin=(-100, 10),
                grid=LogicalLayoutGrid(rows=2, columns=2),
            )
        },
    )
    first = build_architecture_specification(
        _circuit(),
        profile_id,
        logical_layout=layout,
    )
    second = build_architecture_specification(
        _circuit(),
        profile_id,
        logical_layout=layout,
    )

    assert first.to_dict() == second.to_dict()
    assert first.architecture_hash == second.architecture_hash
    candidates = [
        (-100, 10),
        (-100 + step, 10),
        (-100, 10 + step),
        (-100 + step, 10 + step),
    ]
    actual = [
        tuple(slot.coordinate)
        for slot in materialize_compute_layout(first).slots_of_kind("data")
    ]
    assert actual == candidates[: len(actual)]
    owner_id, canonical_module_id, compute = _canonical_submodule(
        first, "compute_region"
    )
    assert (owner_id, canonical_module_id) == (node_id, module_id)
    assert compute.logical_origin == (-100, 10)
    assert _effective_coordinates(
        first,
        owner_id,
        canonical_module_id,
        compute,
    ) == candidates[: compute.capacity]


@pytest.mark.parametrize("profile_id", tuple(PROFILE_COMPUTE_OWNERS))
def test_all_six_profiles_inherit_integer_origin_for_grid_only(
    profile_id: str,
) -> None:
    node_id, module_id, _ = PROFILE_COMPUTE_OWNERS[profile_id]
    layout = _request(
        node_id,
        {
            f"{module_id}/compute_region": SubmoduleLayoutRequest(
                grid=LogicalLayoutGrid(rows=2, columns=2),
            )
        },
    )

    specification = build_architecture_specification(
        _circuit(),
        profile_id,
        logical_layout=layout,
    )

    _, _, compute = _canonical_submodule(specification, "compute_region")
    assert compute.logical_origin is not None
    assert all(type(value) is int for value in compute.logical_origin)


@pytest.mark.parametrize("profile_id", tuple(PROFILE_COMPUTE_OWNERS))
def test_all_six_default_layouts_remain_deterministic(profile_id: str) -> None:
    first = build_architecture_specification(
        _circuit(),
        profile_id,
    )
    second = build_architecture_specification(
        _circuit(),
        profile_id,
    )

    assert first.to_dict() == second.to_dict()
    assert first.architecture_hash == second.architecture_hash
    assert (
        materialize_compute_layout(first).to_dict()
        == materialize_compute_layout(second).to_dict()
    )


@pytest.mark.parametrize("profile_id", tuple(PROFILE_MEMORY_OWNERS))
def test_bb_memory_slots_are_identity_only_and_blocks_are_derived(
    profile_id: str,
) -> None:
    circuit = _circuit(logical_qubits=36, include_t=False)
    specification = build_architecture_specification(circuit, profile_id)
    node_id, module_id = PROFILE_MEMORY_OWNERS[profile_id]
    memory = specification.submodule(
        node_id, module_id, "memory_region"
    )

    assert memory.qec is not None
    assert memory.qec.code == "bb"
    assert memory.logical_origin is None
    assert memory.capacity == 35
    assert len(memory.slots) == 35
    assert all(slot.coordinate is None for slot in memory.slots)
    assert _effective_coordinates(
        specification, node_id, module_id, memory
    ) == [None] * 35

    model = PhysicalFootprintModel.reference_v1()
    footprint = estimate_physical_footprint(specification, model)
    component = next(
        item
        for item in footprint.components
        if item.qualified_submodule
        == f"{node_id}/{module_id}/memory_region"
    )
    assert component.capacity == 35
    assert component.details["k"] == 12
    assert component.details["blocks"] == 3

    origin_only = _request(
        node_id,
        {
            f"{module_id}/memory_region": SubmoduleLayoutRequest(
                logical_origin=(-20, 7),
            )
        },
    )
    moved = build_architecture_specification(
        circuit,
        profile_id,
        logical_layout=origin_only,
    )
    moved_memory = moved.submodule(node_id, module_id, "memory_region")
    assert moved_memory.logical_origin == (-20, 7)
    assert all(slot.coordinate is None for slot in moved_memory.slots)
    assert _effective_coordinates(
        moved, node_id, module_id, moved_memory
    ) == [None] * 35
    moved_footprint = estimate_physical_footprint(moved, model)
    moved_component = next(
        item
        for item in moved_footprint.components
        if item.qualified_submodule
        == f"{node_id}/{module_id}/memory_region"
    )
    assert moved_component.to_dict() == component.to_dict()
    assert (
        materialize_compute_layout(moved).to_dict()
        == materialize_compute_layout(specification).to_dict()
    )
    assert moved.architecture_hash != specification.architecture_hash


def test_logical_layout_rejects_unknown_submodule_target() -> None:
    layout = _request(
        "na_node",
        {"missing/region": SubmoduleLayoutRequest(logical_origin=(0, 0))},
    )

    with pytest.raises(
        ArchitectureValidationError,
        match="unknown Submodule",
    ):
        build_architecture_specification(_circuit(), "1.1", logical_layout=layout)


def test_logical_layout_rejects_engine_geometry() -> None:
    layout = _request(
        "na_node",
        {
            "na_msf/factory_engine": SubmoduleLayoutRequest(
                logical_origin=(0, 0)
            )
        },
    )

    with pytest.raises(
        ArchitectureValidationError,
        match="Engine Submodules cannot have logical slot geometry",
    ):
        build_architecture_specification(_circuit(), "1.1", logical_layout=layout)


def test_logical_layout_rejects_geometry_for_bb_memory_modes() -> None:
    layout = _request(
        "na_node",
        {
            "na_memory/memory_region": SubmoduleLayoutRequest(
                slots={"slot_0": (0, 0)},
            )
        },
    )

    with pytest.raises(
        ArchitectureValidationError,
        match="identity but no operational coordinates",
    ):
        build_architecture_specification(
            _circuit(logical_qubits=36, include_t=False),
            "2.1",
            logical_layout=layout,
        )


def test_logical_layout_requires_the_exact_resolved_slot_ids() -> None:
    layout = _request(
        "na_node",
        {
            "na_compute/compute_region": SubmoduleLayoutRequest(
                slots={
                    "slot_0": (0, 0),
                    "slot_1": (1, 0),
                    "slot_2": (0, 1),
                    "renamed": (1, 1),
                }
            )
        },
    )

    with pytest.raises(
        ArchitectureValidationError,
        match="must exactly match",
    ) as exc_info:
        build_architecture_specification(_circuit(), "1.1", logical_layout=layout)
    assert exc_info.value.details["missing"] == ["slot_3"]
    assert exc_info.value.details["unknown"] == ["renamed"]


def test_effective_spatial_slots_cannot_collide_on_one_node_canvas() -> None:
    layout = _request(
        "na_node",
        {
            "na_compute/compute_region": SubmoduleLayoutRequest(
                logical_origin=(0, 0),
                grid=LogicalLayoutGrid(rows=2, columns=2),
            ),
            "na_msf/magic_state_output_buffer": SubmoduleLayoutRequest(
                logical_origin=(0, 0),
                slots={"slot_0": (0, 0)},
            ),
        },
    )

    with pytest.raises(
        ArchitectureValidationError,
        match="collide on a Node canvas",
    ) as exc_info:
        build_architecture_specification(_circuit(), "1.1", logical_layout=layout)
    assert exc_info.value.details["node"] == "na_node"
    assert exc_info.value.details["coordinate"] == [0.0, 0.0]
    assert len(exc_info.value.details["slots"]) == 2


@pytest.mark.parametrize("custom", [False, True], ids=("default", "custom"))
def test_sc_report_v1_slot_receipt_retains_compiler_routing_metadata(
    custom: bool,
) -> None:
    logical_layout = (
        _request(
            "sc_node",
            {
                "sc_compute/compute_region": SubmoduleLayoutRequest(
                    logical_origin=(-10, 4),
                    grid=LogicalLayoutGrid(rows=2, columns=2),
                )
            },
        )
        if custom
        else None
    )
    report = run_evaluation(
        _circuit(),
        EvaluationConfig(
            profile_id="1.2",
            run_label=f"sc-report-layout-{custom}",
            logical_layout=logical_layout,
        ),
    )

    assert report.compiler_layout.routing_nodes
    assert report.compiler_layout.routing_edges
    assert all(
        slot.interfaces
        for slot in report.compiler_layout.slots_of_kind("data")
    )

    specification_receipt = render_evaluation_report_v1(report)["specification"]
    slots = _report_slots(specification_receipt)
    compute_slots = [
        slot
        for slot in slots
        if "/sc_compute/compute_region/" in slot["ref"]
    ]
    assert compute_slots
    assert all(slot.get("interfaces") for slot in compute_slots)

    # Both report-v1 views deliberately retain compiler-derived routing facts.
    # They are effective compatibility receipts, not authoring inputs.
    compatibility = specification_receipt["architecture_slot_layout"]
    assert compatibility["routing_nodes"]
    assert compatibility["routing_edges"]
    assert any(slot.get("interfaces") for slot in compatibility["slots"])

    if logical_layout is not None:
        authored = report.config.to_dict()["logical_layout"]["submodules"]
        assert authored == {
            "sc_node/sc_compute/compute_region": {
                "logical_origin": [-10, 4],
                "grid": {"rows": 2, "columns": 2},
            }
        }


def test_report_v1_buffer_projection_preserves_canonical_coordinates() -> None:
    report = run_evaluation(
        _circuit(),
        EvaluationConfig(profile_id="2.2", run_label="buffer-projection"),
    )
    specification = report.specification
    receipt = render_evaluation_report_v1(report)["specification"]
    store_load = _report_submodule(
        receipt,
        "store_load_buffer",
        module_id="sc_compute",
    )
    magic_output = _report_submodule(
        receipt,
        "magic_state_output_buffer",
    )

    assert store_load["slots"]
    assert all("coordinate" in slot for slot in store_load["slots"])
    assert magic_output["slots"]
    assert all("coordinate" in slot for slot in magic_output["slots"])

    canonical_store_load = specification.submodule(
        "sc_compute_node", "sc_compute", "store_load_buffer"
    )
    canonical_magic_output = specification.submodule(
        "sc_compute_node", "sc_msf", "magic_state_output_buffer"
    )
    assert [tuple(slot["coordinate"]) for slot in store_load["slots"]] == (
        _effective_coordinates(
            specification,
            "sc_compute_node",
            "sc_compute",
            canonical_store_load,
        )
    )
    assert [tuple(slot["coordinate"]) for slot in magic_output["slots"]] == (
        _effective_coordinates(
            specification,
            "sc_compute_node",
            "sc_msf",
            canonical_magic_output,
        )
    )


@pytest.mark.parametrize(
    "field",
    (
        "interfaces",
        "routing_interface",
        "adjacent_to",
        "routing_nodes",
        "routing_edges",
    ),
)
def test_compiler_receipt_fields_are_not_logical_layout_authoring_inputs(
    field: str,
) -> None:
    with pytest.raises(ValueError, match="Unknown Submodule layout request fields"):
        SubmoduleLayoutRequest.from_dict(
            {"logical_origin": [0, 0], field: ["compiler-derived"]}
        )


@pytest.mark.parametrize(
    ("profile_id", "node_id", "owner"),
    [
        ("1.1", "na_node", "na_msf/magic_state_output_buffer"),
        ("2.2", "na_memory_node", "na_memory/store_load_buffer"),
    ],
)
def test_origin_only_override_preserves_resolved_slot_identity(
    profile_id: str,
    node_id: str,
    owner: str,
) -> None:
    circuit = _circuit()
    baseline = build_architecture_specification(circuit, profile_id)
    module_id, submodule_id = owner.split("/")
    baseline_submodule = baseline.submodule(
        node_id, module_id, submodule_id
    )
    request = _request(
        node_id,
        {owner: SubmoduleLayoutRequest(logical_origin=(50, 50))},
    )

    moved = build_architecture_specification(
        circuit,
        profile_id,
        logical_layout=request,
    )
    moved_submodule = moved.submodule(
        node_id, module_id, submodule_id
    )

    assert [item.id for item in moved_submodule.slots] == [
        item.id for item in baseline_submodule.slots
    ]
    assert [item.coordinate for item in moved_submodule.slots] == [
        item.coordinate for item in baseline_submodule.slots
    ]
    assert moved_submodule.logical_origin == (50, 50)


def test_na_partial_grid_reserves_full_west_store_load_edge() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=6,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                tuple(
                    LogicalOperation("gate", "h", qubits=(qubit,))
                    for qubit in range(6)
                ),
            ),
        ),
        provenance={"benchmark": "partial-grid-store-load"},
    )
    request = _request(
        "na_node",
        {
            "na_compute/compute_region": SubmoduleLayoutRequest(
                logical_origin=(0, 0),
                grid=LogicalLayoutGrid(rows=3, columns=4),
            )
        },
    )

    specification = build_architecture_specification(
        circuit,
        "2.1",
        logical_layout=request,
        policy_overrides={
            "limits.compute_fraction": 1.0,
            "protocols.store_load.buffer_capacity": 3,
        },
    )

    store_load = specification.submodule(
        "na_node", "na_compute", "store_load_buffer"
    )
    assert _effective_coordinates(
        specification,
        "na_node",
        "na_compute",
        store_load,
    ) == [
        (-2.0, 0.0),
        (-2.0, 1.0),
        (-2.0, 2.0),
    ]
    compute_region = specification.submodule(
        "na_node", "na_compute", "compute_region"
    )
    assert compute_region.logical_origin == (0, 0)
    assert compute_region.grid_shape == (3, 4)
    compiler_layout = materialize_compute_layout(specification)
    assert all(
        slot.coordinate[0] > 3
        for slot in compiler_layout.slots_of_kind("magic_state")
    )


def test_reserved_grid_empty_cells_reject_other_owner_slots() -> None:
    request = _request(
        "na_node",
        {
            "na_compute/compute_region": SubmoduleLayoutRequest(
                logical_origin=(0, 0),
                grid=LogicalLayoutGrid(rows=3, columns=4),
            ),
            "na_msf/magic_state_output_buffer": SubmoduleLayoutRequest(
                logical_origin=(0, 0),
                slots={"slot_0": (2, 2)},
            ),
        },
    )

    with pytest.raises(
        ArchitectureValidationError,
        match="reserved grid envelope",
    ):
        build_architecture_specification(_circuit(), "1.1", logical_layout=request)


@pytest.mark.parametrize(
    ("profile_id", "node_id", "owner", "module_id", "slot_id"),
    [
        (
            "1.2",
            "sc_node",
            "sc_msf/magic_state_output_buffer",
            "sc_msf",
            "slot_0",
        ),
        (
            "2.2",
            "sc_compute_node",
            "sc_compute/store_load_buffer",
            "sc_compute",
            "slot_0",
        ),
    ],
)
def test_sc_external_exact_slot_is_an_adjacent_routing_anchor(
    profile_id: str,
    node_id: str,
    owner: str,
    module_id: str,
    slot_id: str,
) -> None:
    request = _request(
        node_id,
        {owner: SubmoduleLayoutRequest(slots={slot_id: (20, 20)})},
    )
    specification = build_architecture_specification(
        _circuit(),
        profile_id,
        logical_layout=request,
    )
    submodule_id = owner.split("/")[1]
    submodule = specification.submodule(
        node_id, module_id, submodule_id
    )
    anchor = specification.effective_slot_coordinate(
        node_id, module_id, submodule_id, slot_id
    )
    assert anchor is not None
    compiler_layout = materialize_compute_layout(specification)
    routing = {
        node: tuple(coordinate)
        for node, coordinate in compiler_layout.metadata[
            "routing_coordinates"
        ].items()
    }
    assert anchor not in set(routing.values())
    adjacent = [
        node
        for node, terminal in routing.items()
        if sum(abs(left - right) for left, right in zip(anchor, terminal)) == 1
    ]
    assert adjacent
    assert set(adjacent) <= set(compiler_layout.routing_nodes)


def test_sc_origin_only_move_preserves_canonical_slot_identity() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=16,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                tuple(
                    LogicalOperation("gate", "h", qubits=(qubit,))
                    for qubit in range(16)
                ),
            ),
        ),
        provenance={"benchmark": "store-load-alias-origin"},
    )
    request = _request(
        "sc_compute_node",
        {
            "sc_compute/store_load_buffer": SubmoduleLayoutRequest(
                logical_origin=(50, 50)
            )
        },
    )

    specification = build_architecture_specification(
        circuit,
        "2.2",
        logical_layout=request,
    )
    slots = specification.submodule(
        "sc_compute_node", "sc_compute", "store_load_buffer"
    ).slots
    assert len(slots) == 6
    assert {slot.id for slot in slots} == {
        f"slot_{index}" for index in range(6)
    }
    assert len(
        {
            specification.effective_slot_coordinate(
                "sc_compute_node",
                "sc_compute",
                "store_load_buffer",
                slot.id,
            )
            for slot in slots
        }
    ) == 6
    assert specification.submodule(
        "sc_compute_node", "sc_compute", "store_load_buffer"
    ).logical_origin == (50, 50)


def test_22_both_store_load_overrides_keep_absolute_canonical_identity() -> None:
    request = LogicalLayoutRequest(
        submodules={
            SubmoduleKey(
                "na_memory_node", "na_memory", "store_load_buffer"
            ): SubmoduleLayoutRequest(slots={"slot_0": (10, 10)}),
            SubmoduleKey(
                "sc_compute_node", "sc_compute", "store_load_buffer"
            ): SubmoduleLayoutRequest(slots={"slot_0": (20, 20)}),
        }
    )
    report = run_evaluation(
        _circuit(),
        EvaluationConfig(
            profile_id="2.2",
            run_label="both-store-load-overrides",
            logical_layout=request,
        ),
    )
    specification = report.specification
    na_slot = specification.submodule(
        "na_memory_node", "na_memory", "store_load_buffer"
    ).slots[0]
    sc_slot = specification.submodule(
        "sc_compute_node", "sc_compute", "store_load_buffer"
    ).slots[0]
    assert na_slot.id == "slot_0"
    assert sc_slot.id == "slot_0"
    assert specification.effective_slot_coordinate(
        "na_memory_node", "na_memory", "store_load_buffer", na_slot.id
    ) == (10, 10)
    assert specification.effective_slot_coordinate(
        "sc_compute_node", "sc_compute", "store_load_buffer", sc_slot.id
    ) == (20, 20)
    assert normalize_json(
        load_evaluation_report_document(report.to_json())
    ) == report.to_dict()


def test_specification_builder_rejects_untyped_logical_layout() -> None:
    with pytest.raises(
        TypeError,
        match="logical_layout must be a LogicalLayoutRequest or None",
    ):
        build_architecture_specification(
            _circuit(),
            "1.1",
            logical_layout={},  # type: ignore[arg-type]
        )


def test_exact_slot_translation_respects_global_coordinate_limit() -> None:
    request = _request(
        "na_node",
        {
            "na_compute/compute_region": SubmoduleLayoutRequest(
                logical_origin=(1_000_000_000, 0),
                slots={
                    "slot_0": (1_000_000_000, 0),
                    "slot_1": (0, 0),
                    "slot_2": (0, 1),
                    "slot_3": (1, 1),
                },
            )
        },
    )

    with pytest.raises(
        ValueError,
        match="Resolved logical slot coordinate exceeds the coordinate limit",
    ):
        build_architecture_specification(
            _circuit(),
            "1.1",
            logical_layout=request,
        )
