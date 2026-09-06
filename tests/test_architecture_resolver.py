from __future__ import annotations

from copy import deepcopy

import pytest

from arqsim.architecture import (
    LogicalLayoutGrid,
    LogicalLayoutRequest,
    SubmoduleLayoutRequest,
    get_architecture_profile,
)
from arqsim.architecture.errors import ArchitectureValidationError
from arqsim.architecture.gallery.na_cf import (
    make_layout_policy,
    make_sizing_policy,
)
from arqsim.architecture.gallery.quantile import QuantileSizingConfig
from arqsim.architecture.identifiers import SubmoduleKey
from arqsim.architecture.logical_layout import LogicalLayoutResult
from arqsim.architecture.profile import ArchitectureProfile
from arqsim.architecture.resolver import resolve_architecture
from arqsim.architecture.sizing import SizingResult
from arqsim.architecture.specification import (
    ArchitectureSpecification,
    QECBinding,
)
from arqsim.qec import (
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
)
from .architecture_semantic_oracle import oracle_circuit


def _targets(profile: ArchitectureProfile) -> tuple[SubmoduleKey, ...]:
    return tuple(
        SubmoduleKey(node.id, module.id, submodule.id)
        for node in profile.nodes
        for module in node.modules
        for submodule in module.submodules
    )


def _selected_qec_protocols(profile: ArchitectureProfile):
    protocol = get_magic_state_factory_profile("cultivation-d5-d15-p1e3")
    return {
        SubmoduleKey(node.id, module.id, submodule.id): protocol
        for node in profile.nodes
        for module in node.modules
        for submodule in module.submodules
        if (submodule.type, submodule.payload) == ("engine", "magic_state")
    }


def _qec_bindings(
    profile: ArchitectureProfile,
) -> dict[SubmoduleKey, QECBinding]:
    return {
        SubmoduleKey(node.id, module.id, submodule.id): QECBinding(
            code="surface",
            parameters={"distance": 13},
        )
        for node in profile.nodes
        for module in node.modules
        for submodule in module.submodules
        if submodule.type != "engine"
    }


def _resolve_generic(
    profile: ArchitectureProfile,
    *,
    logical_layout: LogicalLayoutRequest | None = None,
):
    circuit = oracle_circuit()
    selected_qec_protocols = _selected_qec_protocols(profile)
    sizing = make_sizing_policy(
        QuantileSizingConfig(
            compute_quantile=0.50,
            magic_state_quantile=0.60,
            default_store_load_quantile=0.95,
        )
    ).size(
        profile,
        circuit.statistics,
        selected_qec_protocols=selected_qec_protocols,
    )
    layout = make_layout_policy().place(
        profile,
        sizing,
        request=logical_layout,
    )
    specification = resolve_architecture(
        profile,
        sizing,
        layout,
        qec_bindings=_qec_bindings(profile),
        selected_qec_protocols=selected_qec_protocols,
    )
    return specification, sizing, layout, selected_qec_protocols


def test_profile_11_resolves_directly_into_minimal_nested_core() -> None:
    profile = get_architecture_profile("1.1")
    result, _sizing, _layout, _protocols = _resolve_generic(profile)

    assert ArchitectureSpecification.from_dict(result.to_dict()) == result
    assert set(result.to_dict()) == {
        "schema_version",
        "nodes",
        "interconnects",
        "architecture_hash",
    }
    node = result.node("na_node")
    assert node.modality == "neutral_atom"
    assert set(node.to_dict()) == {
        "id",
        "modality",
        "modules",
        "connections",
    }
    assert result.interconnects == ()

    compute = result.submodule("na_node", "na_compute", "compute_region")
    magic_input = result.submodule(
        "na_node", "na_compute", "magic_state_input_buffer"
    )
    factory = result.submodule("na_node", "na_msf", "factory_engine")
    output = result.submodule(
        "na_node", "na_msf", "magic_state_output_buffer"
    )
    assert compute.capacity == 4
    assert compute.qec is not None and compute.qec.to_dict() == {
        "code": "surface",
        "parameters": {"distance": 13},
    }
    assert [
        result.effective_slot_coordinate(
            "na_node", "na_compute", compute.id, slot.id
        )
        for slot in compute.slots
    ] == [(0, 0), (1, 0), (0, 1), (1, 1)]
    assert result.effective_slot_coordinate(
        "na_node", "na_compute", magic_input.id, "slot_0"
    ) == (3, 0)
    assert factory.qec is None
    assert factory.logical_origin is None
    assert factory.resource_protocol is not None
    assert factory.resource_protocol.id == "cultivation-d5-d15-p1e3"
    assert result.effective_slot_coordinate(
        "na_node", "na_msf", output.id, "slot_0"
    ) == (100, 40)


def test_11_custom_grid_remains_submodule_local() -> None:
    profile = get_architecture_profile("1.1")
    request = LogicalLayoutRequest(
        submodules={
            SubmoduleKey("na_node", "na_compute", "compute_region"): (
                SubmoduleLayoutRequest(
                    logical_origin=(-4, 3),
                    grid=LogicalLayoutGrid(rows=2, columns=2),
                )
            )
        }
    )
    core, _sizing, _layout, _protocols = _resolve_generic(
        profile,
        logical_layout=request,
    )
    compute = core.submodule("na_node", "na_compute", "compute_region")
    assert [slot.coordinate for slot in compute.slots] == [
        (0, 0),
        (1, 0),
        (0, 1),
        (1, 1),
    ]
    assert compute.logical_origin == (-4, 3)
    assert core.effective_slot_coordinate(
        "na_node", "na_compute", "magic_state_input_buffer", "slot_0"
    ) == (-1, 3)


def test_11_partially_filled_grid_reserves_full_envelope_for_magic() -> None:
    profile = get_architecture_profile("1.1")
    request = LogicalLayoutRequest(
        submodules={
            SubmoduleKey("na_node", "na_compute", "compute_region"): (
                SubmoduleLayoutRequest(
                    logical_origin=(10, -3),
                    grid=LogicalLayoutGrid(rows=3, columns=4),
                )
            )
        }
    )
    core, _sizing, _layout, _protocols = _resolve_generic(
        profile,
        logical_layout=request,
    )
    compute = core.submodule("na_node", "na_compute", "compute_region")
    assert compute.grid_shape == (3, 4)
    assert core.effective_slot_coordinate(
        "na_node", "na_compute", "magic_state_input_buffer", "slot_0"
    ) == (15, -3)


def test_core_document_rejects_unknown_fields() -> None:
    result, _sizing, _layout, _protocols = _resolve_generic(
        get_architecture_profile("1.1")
    )
    document = deepcopy(result.to_dict())
    document["legacy_layout_plan"] = {}

    try:
        ArchitectureSpecification.from_dict(document)
    except ValueError as error:
        assert "Unknown fields" in str(error)
    else:  # pragma: no cover - assertion reads more clearly than pytest.raises here
        raise AssertionError("unknown architecture fields must be rejected")


def test_generic_resolver_applies_exact_submodule_qec_bindings() -> None:
    profile = get_architecture_profile("1.1")
    _resolved, sizing, layout, protocols = _resolve_generic(profile)
    bindings = _qec_bindings(profile)
    output_target = SubmoduleKey(
        "na_node", "na_msf", "magic_state_output_buffer"
    )
    bindings[output_target] = QECBinding(
        code="surface",
        parameters={"distance": 7},
    )
    resolved = resolve_architecture(
        profile,
        sizing,
        layout,
        qec_bindings=bindings,
        selected_qec_protocols=protocols,
    )

    qec = resolved.submodule(
        "na_node", "na_msf", "magic_state_output_buffer"
    ).qec
    assert qec is not None
    assert qec.to_dict() == {
        "code": "surface",
        "parameters": {"distance": 7},
    }


def test_generic_resolver_validates_engine_annotations_independently() -> None:
    profile = get_architecture_profile("1.1")
    _resolved, sizing, layout, protocols = _resolve_generic(profile)
    factory = next(iter(protocols))
    magic_input = SubmoduleKey(
        "na_node", "na_compute", "magic_state_input_buffer"
    )

    with pytest.raises(
        ArchitectureValidationError,
        match="cover resource engines exactly",
    ):
        resolve_architecture(
            profile,
            sizing,
            layout,
            qec_bindings=_qec_bindings(profile),
            selected_qec_protocols={},
        )
    with pytest.raises(
        ArchitectureValidationError,
        match="must target a resource engine",
    ):
        resolve_architecture(
            profile,
            sizing,
            layout,
            qec_bindings=_qec_bindings(profile),
            selected_qec_protocols={magic_input: protocols[factory]},
        )
    with pytest.raises(
        ArchitectureValidationError,
        match="payload does not match",
    ):
        resolve_architecture(
            profile,
            sizing,
            layout,
            qec_bindings=_qec_bindings(profile),
            selected_qec_protocols={
                factory: get_entanglement_distillation_profile(
                    "boosting-dbell9-ds19-pbell1e2"
                )
            },
        )
    with pytest.raises(
        ArchitectureValidationError,
        match="QEC protocol differs from the one used for sizing",
    ):
        resolve_architecture(
            profile,
            sizing,
            layout,
            qec_bindings=_qec_bindings(profile),
            selected_qec_protocols={
                factory: get_magic_state_factory_profile(
                    "litinski-15to1x20to4-13-5-5-23-11-13-p1e3"
                )
            },
        )
    with pytest.raises(
        ArchitectureValidationError,
        match="cannot target a resource engine",
    ):
        resolve_architecture(
            profile,
            sizing,
            layout,
            qec_bindings={
                **_qec_bindings(profile),
                factory: QECBinding(code="surface", parameters={"distance": 13}),
            },
            selected_qec_protocols=protocols,
        )


def test_generic_resolver_keys_submodules_by_qualified_owner_path() -> None:
    base_profile = get_architecture_profile("1.1")
    document = deepcopy(base_profile.to_dict())
    modules = document["nodes"]["na_node"]["modules"]
    compute_submodules = modules["na_compute"]["submodules"]
    factory_submodules = modules["na_msf"]["submodules"]
    compute_submodules["shared"] = compute_submodules.pop("compute_region")
    factory_submodules["shared"] = factory_submodules.pop("factory_engine")
    profile = type(base_profile).from_dict(document)

    resolved, _sizing, _layout, _protocols = _resolve_generic(profile)

    compute = resolved.submodule("na_node", "na_compute", "shared")
    factory = resolved.submodule("na_node", "na_msf", "shared")
    assert (compute.type, compute.payload, compute.capacity) == (
        "region",
        "logical_qubit",
        oracle_circuit().num_qubits,
    )
    assert (factory.type, factory.payload) == ("engine", "magic_state")


def test_generic_resolver_rejects_incomplete_policy_results() -> None:
    profile = get_architecture_profile("1.1")
    _resolved, sizing, layout, protocols = _resolve_generic(profile)
    missing_target = _targets(profile)[0]
    incomplete_sizing = SizingResult(
        {
            target: capacity
            for target, capacity in sizing.capacities.items()
            if target != missing_target
        }
    )
    incomplete_layout = LogicalLayoutResult(
        {
            target: placement
            for target, placement in layout.submodules.items()
            if target != missing_target
        }
    )
    unknown_target = SubmoduleKey("unknown_owner", "unknown_module", "unknown_slot")
    oversized_sizing = SizingResult(
        {**dict(sizing.capacities), unknown_target: 1}
    )
    oversized_layout = LogicalLayoutResult(
        {
            **dict(layout.submodules),
            unknown_target: next(iter(layout.submodules.values())),
        }
    )

    with pytest.raises(
        ArchitectureValidationError,
        match="SizingResult does not cover the Profile exactly",
    ):
        resolve_architecture(
            profile,
            incomplete_sizing,
            layout,
            qec_bindings=_qec_bindings(profile),
            selected_qec_protocols=protocols,
        )
    with pytest.raises(
        ArchitectureValidationError,
        match="LogicalLayoutResult does not cover the Profile exactly",
    ):
        resolve_architecture(
            profile,
            sizing,
            incomplete_layout,
            qec_bindings=_qec_bindings(profile),
            selected_qec_protocols=protocols,
        )
    with pytest.raises(
        ArchitectureValidationError,
        match="SizingResult does not cover the Profile exactly",
    ):
        resolve_architecture(profile, oversized_sizing, layout)
    with pytest.raises(
        ArchitectureValidationError,
        match="LogicalLayoutResult does not cover the Profile exactly",
    ):
        resolve_architecture(profile, sizing, oversized_layout)


def test_generic_resolver_does_not_dispatch_on_profile_or_component_ids() -> None:
    document = deepcopy(get_architecture_profile("1.1").to_dict())
    document["id"] = "opaque-star-profile"
    document["name"] = "Opaque synthetic profile"
    node = document["nodes"].pop("na_node")
    document["nodes"]["owner_z"] = node
    modules = node["modules"]
    compute = modules.pop("na_compute")
    factory = modules.pop("na_msf")
    modules["module_a"] = compute
    modules["module_b"] = factory
    compute_submodules = compute["submodules"]
    compute_submodules["slot_a"] = compute_submodules.pop("compute_region")
    compute_submodules["slot_b"] = compute_submodules.pop(
        "magic_state_input_buffer"
    )
    factory_submodules = factory["submodules"]
    factory_submodules["slot_c"] = factory_submodules.pop("factory_engine")
    factory_submodules["slot_d"] = factory_submodules.pop(
        "magic_state_output_buffer"
    )
    connection = node["connections"].pop("na_magic_bus")
    connection["endpoints"] = ["module_b/slot_d", "module_a/slot_b"]
    node["connections"]["path_x"] = connection
    profile = ArchitectureProfile.from_dict(document)

    resolved, sizing, layout, protocols = _resolve_generic(profile)

    assert resolved.node("owner_z").modality == "neutral_atom"
    assert resolved.submodule("owner_z", "module_a", "slot_a").capacity == 4
    assert resolved.submodule("owner_z", "module_b", "slot_c").resource_protocol
    assert set(sizing.capacities) == set(_targets(profile))
    assert set(layout.submodules) == {
        target for target in _targets(profile) if target not in protocols
    }
    assert set(protocols) == {SubmoduleKey("owner_z", "module_b", "slot_c")}
