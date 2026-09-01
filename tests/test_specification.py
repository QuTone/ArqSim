from __future__ import annotations

import math

import pytest

from heteqsys.architecture.specification import (
    ArchitectureSpecification,
    Module,
    Submodule,
)
from heteqsys.evaluation import (
    PhysicalFootprintModel,
    estimate_physical_footprint,
)
from heteqsys.program import FTCircuit, LogicalLayer, LogicalOperation
from heteqsys.qec import (
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
)
from heteqsys.specification import (
    build_architecture_specification,
    sweep_architecture_specifications,
)


def _sizing_circuit() -> FTCircuit:
    layers = []
    for index, width in enumerate((2, 4, 6, 8, 10)):
        operations = tuple(
            LogicalOperation(
                "gate",
                "t" if qubit < index else "h",
                qubits=(qubit,),
            )
            for qubit in range(width)
        )
        layers.append(LogicalLayer(index, operations))
    return FTCircuit(
        representation="clifford_t",
        num_qubits=10,
        num_clbits=0,
        layers=tuple(layers),
        provenance={"benchmark": "sizing-fixture"},
    )


def _stretched_pbc_circuit() -> FTCircuit:
    return FTCircuit(
        representation="pbc",
        num_qubits=10,
        num_clbits=0,
        layers=tuple(
            LogicalLayer(
                index,
                (
                    LogicalOperation(
                        "pauli_rotation",
                        "t_pauli",
                        qubits=(0,),
                        pauli="+XIIIIIIIII",
                    ),
                ),
            )
            for index in range(5)
        ),
        provenance={"benchmark": "stretched-pbc-fixture"},
    )


def _records(
    specification: ArchitectureSpecification,
) -> tuple[tuple[str, str | None, Module, Submodule], ...]:
    return tuple(
        (owner.id, getattr(owner, "modality", None), module, submodule)
        for owner in (*specification.nodes, *specification.interconnects)
        for module in owner.modules
        for submodule in module.submodules
    )


def _matching(
    specification: ArchitectureSpecification,
    *,
    module_type: str | None = None,
    submodule_type: str | None = None,
    payload: str | None = None,
) -> tuple[tuple[str, str | None, Module, Submodule], ...]:
    return tuple(
        record
        for record in _records(specification)
        if (module_type is None or record[2].type == module_type)
        and (submodule_type is None or record[3].type == submodule_type)
        and (payload is None or record[3].payload == payload)
    )


def _one(
    specification: ArchitectureSpecification,
    *,
    module_type: str | None = None,
    submodule_type: str | None = None,
    payload: str | None = None,
) -> tuple[str, str | None, Module, Submodule]:
    matches = _matching(
        specification,
        module_type=module_type,
        submodule_type=submodule_type,
        payload=payload,
    )
    assert len(matches) == 1
    return matches[0]


def _effective_coordinates(
    specification: ArchitectureSpecification,
    owner_id: str,
    module: Module,
    submodule: Submodule,
) -> tuple[tuple[int, int], ...]:
    coordinates = tuple(
        specification.effective_slot_coordinate(
            owner_id,
            module.id,
            submodule.id,
            slot.id,
        )
        for slot in submodule.slots
    )
    assert all(coordinate is not None for coordinate in coordinates)
    return tuple(coordinate for coordinate in coordinates if coordinate is not None)


def test_public_builder_returns_the_nested_canonical_specification() -> None:
    specification = build_architecture_specification(_sizing_circuit(), "2.3")

    assert isinstance(specification, ArchitectureSpecification)
    assert ArchitectureSpecification.from_dict(specification.to_dict()) == specification
    assert not hasattr(specification, "system")
    assert not hasattr(specification, "layout_plan")
    assert not hasattr(specification, "architecture_layout")
    assert not hasattr(specification, "footprint")

    _owner, _modality, _module, compute = _one(
        specification,
        module_type="compute",
        submodule_type="region",
        payload="logical_qubit",
    )
    _owner, _modality, _module, memory = _one(
        specification,
        module_type="memory",
        submodule_type="region",
        payload="logical_qubit",
    )
    _owner, _modality, _module, factory = _one(
        specification,
        module_type="resource_factory",
        submodule_type="engine",
        payload="magic_state",
    )

    assert (compute.capacity, memory.capacity) == (4, 6)
    assert len(compute.slots) == compute.capacity
    assert len(memory.slots) == memory.capacity
    assert compute.qec is not None
    assert compute.qec.to_dict() == {
        "code": "surface",
        "parameters": {"distance": 13},
    }
    assert memory.qec is not None
    assert memory.qec.to_dict() == {
        "code": "bb",
        "parameters": {"distance": 18, "k": 12, "n": 288},
    }
    assert factory.slots == ()
    assert factory.qec is None
    assert factory.resource_protocol is not None
    assert factory.resource_protocol.id == "cultivation-d5-d15-p1e3"


def test_physical_footprint_is_an_independent_derived_artifact() -> None:
    model = PhysicalFootprintModel.reference_v1()
    neutral_atom = build_architecture_specification(_sizing_circuit(), "1.1")
    superconducting = build_architecture_specification(_sizing_circuit(), "1.2")

    na_footprint = estimate_physical_footprint(neutral_atom, model)
    sc_footprint = estimate_physical_footprint(superconducting, model)
    assert na_footprint.architecture_hash == neutral_atom.architecture_hash
    assert sc_footprint.architecture_hash == superconducting.architecture_hash
    assert na_footprint.model_hash == model.model_hash
    assert na_footprint.total_physical_qubits > 0
    assert all(na_footprint.checks.values())
    assert not hasattr(neutral_atom, "footprint")

    na_surface = next(
        component
        for component in na_footprint.components
        if component.rule == "surface_patch"
        and component.module_type == "compute"
        and component.submodule_type == "region"
    )
    sc_surface = next(
        component
        for component in sc_footprint.components
        if component.rule == "surface_patch"
        and component.module_type == "compute"
        and component.submodule_type == "region"
    )
    assert na_surface.details["base_patch_physical_qubits"] == 2 * 13**2 - 1
    assert na_surface.details["physical_qubits_per_unit"] == 337
    assert sc_surface.details["physical_qubits_per_unit"] == 4 * 337


def test_quantile_grid_changes_canonical_capacities_and_hash() -> None:
    results = sweep_architecture_specifications(
        _sizing_circuit(),
        "2.2",
        {
            "limits.compute_fraction": [1.0],
            "quantiles.compute": [0.25, 0.75],
            "quantiles.magic_state": [0.25, 0.75],
        },
    )

    assert len(results) == 4
    points = {
        (
            _one(
                item,
                module_type="compute",
                submodule_type="region",
                payload="logical_qubit",
            )[3].capacity,
            _one(
                item,
                module_type="compute",
                submodule_type="buffer",
                payload="magic_state",
            )[3].capacity,
        )
        for item in results
    }
    assert {compute for compute, _magic in points} == {4, 8}
    assert {magic for _compute, magic in points} == {1, 3}
    assert len({item.architecture_hash for item in results}) == 4


def test_neutral_atom_compute_places_store_load_west_and_magic_east() -> None:
    specification = build_architecture_specification(_sizing_circuit(), "2.3")
    owner, _modality, compute_module, compute = _one(
        specification,
        module_type="compute",
        submodule_type="region",
        payload="logical_qubit",
    )
    store_load = next(
        item
        for item in compute_module.submodules
        if (item.type, item.payload) == ("buffer", "logical_qubit")
    )
    magic = next(
        item
        for item in compute_module.submodules
        if (item.type, item.payload) == ("buffer", "magic_state")
    )
    data_coordinates = _effective_coordinates(
        specification, owner, compute_module, compute
    )
    store_load_coordinates = _effective_coordinates(
        specification, owner, compute_module, store_load
    )
    magic_coordinates = _effective_coordinates(
        specification, owner, compute_module, magic
    )

    assert max(x for x, _y in store_load_coordinates) < min(
        x for x, _y in data_coordinates
    )
    assert min(x for x, _y in magic_coordinates) > max(
        x for x, _y in data_coordinates
    )
    assert list(store_load_coordinates) == sorted(
        store_load_coordinates,
        key=lambda coordinate: (coordinate[1], coordinate[0]),
    )


@pytest.mark.parametrize("profile_id", ("1.2", "2.2"))
def test_superconducting_compute_stores_only_occupied_checkerboard_sites(
    profile_id: str,
) -> None:
    specification = build_architecture_specification(_sizing_circuit(), profile_id)
    owner, _modality, compute_module, compute = _one(
        specification,
        module_type="compute",
        submodule_type="region",
        payload="logical_qubit",
    )
    magic = next(
        item
        for item in compute_module.submodules
        if (item.type, item.payload) == ("buffer", "magic_state")
    )
    data_coordinates = _effective_coordinates(
        specification, owner, compute_module, compute
    )
    magic_coordinates = _effective_coordinates(
        specification, owner, compute_module, magic
    )

    assert all(x % 2 == 0 and y % 2 == 0 for x, y in data_coordinates)
    assert min(x for x, _y in magic_coordinates) > max(
        x for x, _y in data_coordinates
    )
    assert all(x % 2 == 0 and y % 2 == 0 for x, y in magic_coordinates)

    output_owner, _output_modality, factory_module, output = _one(
        specification,
        module_type="resource_factory",
        submodule_type="buffer",
        payload="magic_state",
    )
    output_coordinates = _effective_coordinates(
        specification, output_owner, factory_module, output
    )
    assert min(x for x, _y in output_coordinates) > max(
        x for x, _y in magic_coordinates
    )
    factory = next(item for item in factory_module.submodules if item.type == "engine")
    assert factory.capacity > 0
    assert factory.slots == ()
    assert factory.logical_origin is None
    assert factory.grid_shape is None


def test_magic_factory_copy_count_uses_selected_protocol_multiplicity() -> None:
    protocol_id = "litinski-15to1x20to4-13-5-5-23-11-13-p1e3"
    protocol = get_magic_state_factory_profile(protocol_id)
    specification = build_architecture_specification(
        _sizing_circuit(),
        "1.1",
        policy_overrides={"protocols.magic_state.id": protocol_id},
    )
    output = _one(
        specification,
        module_type="resource_factory",
        submodule_type="buffer",
        payload="magic_state",
    )[3]
    factory = _one(
        specification,
        module_type="resource_factory",
        submodule_type="engine",
        payload="magic_state",
    )[3]

    assert factory.capacity == math.ceil(output.capacity / protocol.outputs_per_batch)
    assert factory.resource_protocol is not None
    assert factory.resource_protocol.id == protocol.id
    assert factory.resource_protocol.profile_hash == protocol.profile_hash


def test_magic_output_buffer_capacity_is_independent_of_batch_size() -> None:
    specification = build_architecture_specification(
        _sizing_circuit(),
        "1.1",
        policy_overrides={
            "quantiles.magic_state": 0.0,
            "protocols.magic_state.id": (
                "litinski-15to1x20to4-13-5-5-23-11-13-p1e3"
            ),
        },
    )
    magic_buffers = _matching(
        specification,
        submodule_type="buffer",
        payload="magic_state",
    )
    factory = _one(
        specification,
        module_type="resource_factory",
        submodule_type="engine",
        payload="magic_state",
    )[3]

    assert {record[3].capacity for record in magic_buffers} == {1}
    assert factory.capacity == 1


def test_magic_sizing_reference_is_shared_across_modalities() -> None:
    reference = _sizing_circuit()
    neutral_atom = build_architecture_specification(
        reference,
        "1.1",
        magic_sizing_circuit=reference,
    )
    superconducting = build_architecture_specification(
        _stretched_pbc_circuit(),
        "1.2",
        magic_sizing_circuit=reference,
    )

    for specification in (neutral_atom, superconducting):
        magic_input = _one(
            specification,
            module_type="compute",
            submodule_type="buffer",
            payload="magic_state",
        )[3]
        assert magic_input.capacity == 3


def test_bell_capacity_is_shared_once_and_tracks_transfer_payload() -> None:
    magic_remote = build_architecture_specification(_sizing_circuit(), "1.3")
    hybrid_remote = build_architecture_specification(_sizing_circuit(), "2.3")
    logical_data_remote = build_architecture_specification(_sizing_circuit(), "2.2")
    bell_protocol = get_entanglement_distillation_profile(
        "boosting-dbell9-ds19-pbell1e2"
    )

    for specification in (magic_remote, hybrid_remote):
        bell_buffer = _one(
            specification,
            module_type="bell_storage",
            submodule_type="buffer",
            payload="bell_pair",
        )[3]
        magic_input = _one(
            specification,
            module_type="compute",
            submodule_type="buffer",
            payload="magic_state",
        )[3]
        assert bell_buffer.capacity == magic_input.capacity
        assert len(bell_buffer.slots) == bell_buffer.capacity

    bell_buffer = _one(
        logical_data_remote,
        module_type="bell_storage",
        submodule_type="buffer",
        payload="bell_pair",
    )[3]
    compute_store_load = _one(
        logical_data_remote,
        module_type="compute",
        submodule_type="buffer",
        payload="logical_qubit",
    )[3]
    assert bell_buffer.capacity == compute_store_load.capacity

    for specification in (magic_remote, hybrid_remote, logical_data_remote):
        assert len(specification.interconnects) == 1
        engine = _one(
            specification,
            module_type="bell_engine",
            submodule_type="engine",
            payload="bell_pair",
        )[3]
        assert engine.capacity > 0
        assert engine.slots == ()
        assert engine.resource_protocol is not None
        assert engine.resource_protocol.id == bell_protocol.id
        assert engine.resource_protocol.profile_hash == bell_protocol.profile_hash


def test_explicit_resource_capacities_override_each_dimension_independently() -> None:
    magic = build_architecture_specification(
        _sizing_circuit(),
        "1.3",
        policy_overrides={
            "protocols.magic_state.buffer_capacity": 5,
            "protocols.magic_state.copies": 2,
        },
    )
    assert {
        record[3].capacity
        for record in _matching(magic, submodule_type="buffer", payload="magic_state")
    } == {5}
    assert _one(
        magic,
        module_type="resource_factory",
        submodule_type="engine",
        payload="magic_state",
    )[3].capacity == 2

    store_load = build_architecture_specification(
        _sizing_circuit(),
        "2.3",
        policy_overrides={"protocols.store_load.buffer_capacity": 2},
    )
    store_load_buffer = _one(
        store_load,
        module_type="compute",
        submodule_type="buffer",
        payload="logical_qubit",
    )[3]
    assert store_load_buffer.capacity == len(store_load_buffer.slots) == 2

    bell = build_architecture_specification(
        _sizing_circuit(),
        "1.3",
        policy_overrides={
            "protocols.entanglement_distillation.buffer_capacity": 7,
            "protocols.entanglement_distillation.copies": 2,
        },
    )
    assert _one(
        bell,
        module_type="bell_storage",
        submodule_type="buffer",
        payload="bell_pair",
    )[3].capacity == 7
    assert _one(
        bell,
        module_type="bell_engine",
        submodule_type="engine",
        payload="bell_pair",
    )[3].capacity == 2


def test_catalog_owned_bell_workspace_cannot_be_overridden() -> None:
    with pytest.raises(ValueError, match="is intrinsic to protocol"):
        build_architecture_specification(
            _sizing_circuit(),
            "2.3",
            policy_overrides={
                "protocols.entanglement_distillation."
                "logical_qubits_per_copy_per_endpoint": 3,
            },
        )


def test_bell_footprint_expands_one_shared_engine_into_endpoint_costs() -> None:
    protocol = get_entanglement_distillation_profile(
        "boosting-dbell9-ds19-pbell1e2"
    )
    specification = build_architecture_specification(
        _sizing_circuit(),
        "1.3",
        policy_overrides={
            **protocol.layout_policy_overrides(),
            "protocols.entanglement_distillation.copies": 2,
        },
    )
    footprint = estimate_physical_footprint(
        specification,
        PhysicalFootprintModel.reference_v1(),
    )
    engines = tuple(
        component
        for component in footprint.components
        if component.module_type == "bell_engine"
        and component.submodule_type == "engine"
        and component.payload == "bell_pair"
    )

    assert len(engines) == len(specification.interconnects[0].endpoints) == 2
    assert {item.endpoint_node_id for item in engines} == {
        endpoint.split("/", 1)[0]
        for endpoint in specification.interconnects[0].endpoints
    }
    assert {item.rule for item in engines} == {"logical_bell_protocol_endpoint"}
    assert {item.physical_qubits for item in engines} == {2 * 721}


@pytest.mark.parametrize("profile_id", ("1.3", "2.2", "2.3"))
def test_slot_identity_is_unique_only_after_owner_qualification(
    profile_id: str,
) -> None:
    specification = build_architecture_specification(_sizing_circuit(), profile_id)
    qualified = [
        f"{owner_id}/{module.id}/{submodule.id}/{slot.id}"
        for owner_id, _modality, module, submodule in _records(specification)
        for slot in submodule.slots
    ]

    assert qualified
    assert len(qualified) == len(set(qualified))
    for address in qualified:
        owner_id, module_id, submodule_id, slot_id = address.split("/")
        submodule = specification.submodule(owner_id, module_id, submodule_id)
        assert slot_id in {slot.id for slot in submodule.slots}
