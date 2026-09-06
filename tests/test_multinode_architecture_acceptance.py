"""Canonical-core acceptance for every bundled multi-Node Profile."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json

import pytest

import arqsim.architecture.construction as construction_pipeline
from arqsim.architecture.gallery import (
    QuantileSizingConfig,
    get_architecture_profile,
    get_gallery_entry,
)
from arqsim.architecture.identifiers import SubmoduleKey
from arqsim.architecture.profile import ArchitectureProfile
from arqsim.architecture.sizing import SizingPolicy
from arqsim.architecture.specification import (
    ArchitectureSpecification,
    QECBinding,
    Submodule,
)
from arqsim.program import FTCircuit, LogicalLayer, LogicalOperation
from arqsim.program.statistics import CircuitStatistics
from arqsim.qec import (
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
)
from arqsim.specification import build_architecture_specification
from tests.architecture_semantic_oracle import oracle_circuit


_SURFACE_QEC = QECBinding("surface", {"distance": 13})
_BB_MEMORY_QEC = QECBinding(
    "bb",
    {"n": 288, "k": 12, "distance": 18},
)
_MAGIC_PROTOCOL = get_magic_state_factory_profile(
    "cultivation-d5-d15-p1e3"
)
_BELL_PROTOCOL = get_entanglement_distillation_profile(
    "boosting-dbell9-ds19-pbell1e2"
)
_QUANTILES = QuantileSizingConfig(
    compute_quantile=0.50,
    magic_state_quantile=0.60,
    default_store_load_quantile=0.95,
    store_load_quantiles_by_representation={
        "clifford_t": 0.95,
        "pbc": 0.80,
    },
)


def _gallery_sizing(profile_id: str) -> Callable[[], SizingPolicy]:
    return lambda: get_gallery_entry(profile_id).make_sizing_policy(_QUANTILES)


@dataclass(frozen=True, slots=True)
class _Case:
    profile_id: str
    sizing_policy: Callable[[], SizingPolicy]
    interconnect_id: str
    endpoints: tuple[str, str]
    factory: SubmoduleKey
    bell_engine: SubmoduleKey
    bell_buffer: SubmoduleKey
    bell_engine_capacity: int
    bell_buffer_capacity: int
    coordinate_checks: tuple[
        tuple[SubmoduleKey, str, tuple[int, int] | None], ...
    ]


_CASES = (
    _Case(
        profile_id="1.3",
        sizing_policy=_gallery_sizing("1.3"),
        interconnect_id="compute_msf_link",
        endpoints=(
            "na_compute_node/na_compute/magic_state_input_buffer",
            "sc_msf_node/sc_msf/magic_state_output_buffer",
        ),
        factory=SubmoduleKey("sc_msf_node", "sc_msf", "factory_engine"),
        bell_engine=SubmoduleKey(
            "compute_msf_link", "bell_engine", "pair_generator"
        ),
        bell_buffer=SubmoduleKey(
            "compute_msf_link", "bell_storage", "bell_buffer"
        ),
        bell_engine_capacity=1,
        bell_buffer_capacity=1,
        coordinate_checks=(
            (
                SubmoduleKey(
                    "na_compute_node", "na_compute", "compute_region"
                ),
                "slot_3",
                (1, 1),
            ),
            (
                SubmoduleKey(
                    "na_compute_node",
                    "na_compute",
                    "magic_state_input_buffer",
                ),
                "slot_0",
                (3, 0),
            ),
            (
                SubmoduleKey(
                    "sc_msf_node", "sc_msf", "magic_state_output_buffer"
                ),
                "slot_0",
                (0, 40),
            ),
        ),
    ),
    _Case(
        profile_id="2.2",
        sizing_policy=_gallery_sizing("2.2"),
        interconnect_id="memory_compute_link",
        endpoints=(
            "na_memory_node/na_memory/store_load_buffer",
            "sc_compute_node/sc_compute/store_load_buffer",
        ),
        factory=SubmoduleKey(
            "sc_compute_node", "sc_msf", "factory_engine"
        ),
        bell_engine=SubmoduleKey(
            "memory_compute_link", "bell_engine", "pair_generator"
        ),
        bell_buffer=SubmoduleKey(
            "memory_compute_link", "bell_storage", "bell_buffer"
        ),
        bell_engine_capacity=2,
        bell_buffer_capacity=2,
        coordinate_checks=(
            (
                SubmoduleKey(
                    "na_memory_node", "na_memory", "memory_region"
                ),
                "slot_0",
                None,
            ),
            (
                SubmoduleKey(
                    "sc_compute_node", "sc_compute", "compute_region"
                ),
                "slot_1",
                (4, 0),
            ),
            (
                SubmoduleKey(
                    "sc_compute_node",
                    "sc_compute",
                    "store_load_buffer",
                ),
                "slot_1",
                (0, 1),
            ),
            (
                SubmoduleKey(
                    "sc_compute_node", "sc_msf", "magic_state_output_buffer"
                ),
                "slot_0",
                (8, 0),
            ),
        ),
    ),
    _Case(
        profile_id="2.3",
        sizing_policy=_gallery_sizing("2.3"),
        interconnect_id="compute_msf_link",
        endpoints=(
            "na_compute_node/na_compute/magic_state_input_buffer",
            "sc_msf_node/sc_msf/magic_state_output_buffer",
        ),
        factory=SubmoduleKey("sc_msf_node", "sc_msf", "factory_engine"),
        bell_engine=SubmoduleKey(
            "compute_msf_link", "bell_engine", "pair_generator"
        ),
        bell_buffer=SubmoduleKey(
            "compute_msf_link", "bell_storage", "bell_buffer"
        ),
        bell_engine_capacity=1,
        bell_buffer_capacity=1,
        coordinate_checks=(
            (
                SubmoduleKey(
                    "na_compute_node", "na_memory", "memory_region"
                ),
                "slot_0",
                None,
            ),
            (
                SubmoduleKey(
                    "na_compute_node", "na_compute", "compute_region"
                ),
                "slot_1",
                (1, 0),
            ),
            (
                SubmoduleKey(
                    "na_compute_node",
                    "na_compute",
                    "store_load_buffer",
                ),
                "slot_1",
                (-2, 0),
            ),
            (
                SubmoduleKey(
                    "sc_msf_node", "sc_msf", "magic_state_output_buffer"
                ),
                "slot_0",
                (0, 40),
            ),
        ),
    ),
)


def _statistics() -> CircuitStatistics:
    return CircuitStatistics(
        representation="clifford_t",
        logical_qubits=4,
        magic_states_per_layer=(0, 1, 0, 0, 1, 0),
        operation_qubits_by_layer=(
            ((0,), (1,)),
            ((0,),),
            ((0, 1),),
            ((2,), (3,)),
            ((2,),),
            ((2, 3),),
        ),
    )


def _node_qec_bindings(
    profile: ArchitectureProfile,
) -> dict[SubmoduleKey, QECBinding]:
    bindings: dict[SubmoduleKey, QECBinding] = {}
    for node in profile.nodes:
        for module in node.modules:
            for submodule in module.submodules:
                if submodule.type == "engine":
                    continue
                target = SubmoduleKey(node.id, module.id, submodule.id)
                bindings[target] = (
                    _BB_MEMORY_QEC
                    if module.type == "memory" and submodule.type == "region"
                    else _SURFACE_QEC
                )
    return bindings


def _protocols(case: _Case):
    return {
        case.factory: _MAGIC_PROTOCOL,
        case.bell_engine: _BELL_PROTOCOL,
    }


def _resolved_submodules(
    specification: ArchitectureSpecification,
) -> dict[SubmoduleKey, Submodule]:
    return {
        SubmoduleKey(owner.id, module.id, submodule.id): submodule
        for owner in (*specification.nodes, *specification.interconnects)
        for module in owner.modules
        for submodule in module.submodules
    }


def _effective_coordinate(
    submodule: Submodule,
    slot_id: str,
) -> tuple[int, int] | None:
    slot = next(item for item in submodule.slots if item.id == slot_id)
    if slot.coordinate is None:
        return None
    origin = submodule.logical_origin or (0, 0)
    return (
        origin[0] + slot.coordinate[0],
        origin[1] + slot.coordinate[1],
    )


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.profile_id)
def test_multinode_profiles_resolve_through_one_canonical_core(
    case: _Case,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    generic_resolver = construction_pipeline.resolve_architecture

    def record_generic_resolver(*args, **kwargs):
        calls.append(args[0].id)
        return generic_resolver(*args, **kwargs)

    monkeypatch.setattr(
        construction_pipeline,
        "resolve_architecture",
        record_generic_resolver,
    )
    profile = get_architecture_profile(case.profile_id)
    qec_bindings = _node_qec_bindings(profile)
    protocols = _protocols(case)
    specification = construction_pipeline.construct_architecture(
        profile,
        _statistics(),
        case.sizing_policy(),
        layout_policy=get_gallery_entry(case.profile_id).make_layout_policy(),
        qec_bindings=qec_bindings,
        selected_qec_protocols=protocols,
    )

    assert calls == [case.profile_id]
    assert len(specification.nodes) == 2
    assert len(specification.interconnects) == 1
    interconnect = specification.interconnect(case.interconnect_id)
    assert interconnect.endpoints == case.endpoints
    assert [(module.id, module.type) for module in interconnect.modules] == [
        ("bell_engine", "bell_engine"),
        ("bell_storage", "bell_storage"),
    ]
    assert [connection.to_dict() for connection in interconnect.connections] == [
        {
            "id": "engine_to_storage",
            "direction": "directed",
            "endpoints": [
                "bell_engine/pair_generator",
                "bell_storage/bell_buffer",
            ],
        }
    ]

    submodules = _resolved_submodules(specification)
    bell_engine = submodules[case.bell_engine]
    bell_buffer = submodules[case.bell_buffer]
    factory = submodules[case.factory]
    assert specification.module(case.interconnect_id, "bell_storage").type == (
        "bell_storage"
    )
    assert specification.submodule(
        case.interconnect_id,
        "bell_storage",
        "bell_buffer",
    ) is bell_buffer
    assert (
        bell_engine.type,
        bell_engine.payload,
        bell_engine.capacity,
        bell_engine.slots,
    ) == ("engine", "bell_pair", case.bell_engine_capacity, ())
    assert (
        bell_buffer.type,
        bell_buffer.payload,
        bell_buffer.capacity,
    ) == ("buffer", "bell_pair", case.bell_buffer_capacity)
    assert bell_buffer.qec is None
    assert bell_buffer.resource_protocol is None
    assert bell_buffer.logical_origin is None
    assert bell_buffer.grid_shape is None
    assert all(slot.coordinate is None for slot in bell_buffer.slots)
    assert specification.effective_slot_coordinate(
        case.interconnect_id,
        "bell_storage",
        "bell_buffer",
        "slot_0",
    ) is None

    bell_buffers = [
        (target, submodule)
        for target, submodule in submodules.items()
        if submodule.type == "buffer" and submodule.payload == "bell_pair"
    ]
    assert [(target, item.capacity) for target, item in bell_buffers] == [
        (case.bell_buffer, case.bell_buffer_capacity)
    ]
    node_ids = {node.id for node in specification.nodes}
    assert all(
        not (submodule.payload == "bell_pair")
        for target, submodule in submodules.items()
        if target.owner_id in node_ids
    )

    assert factory.resource_protocol is not None
    assert factory.resource_protocol.to_dict() == {
        "id": _MAGIC_PROTOCOL.id,
        "profile_hash": _MAGIC_PROTOCOL.profile_hash,
    }
    assert bell_engine.resource_protocol is not None
    assert bell_engine.resource_protocol.to_dict() == {
        "id": _BELL_PROTOCOL.id,
        "profile_hash": _BELL_PROTOCOL.profile_hash,
    }
    assert {
        target
        for target, submodule in submodules.items()
        if submodule.resource_protocol is not None
    } == {case.factory, case.bell_engine}

    for target, submodule in submodules.items():
        if submodule.type != "engine":
            assert tuple(slot.id for slot in submodule.slots) == tuple(
                f"slot_{index}" for index in range(submodule.capacity)
            )
        if target.owner_id in node_ids and submodule.type != "engine":
            assert submodule.qec == qec_bindings[target]
        else:
            assert submodule.qec is None
    for target, slot_id, expected in case.coordinate_checks:
        assert _effective_coordinate(submodules[target], slot_id) == expected

    document = specification.to_dict()
    restored = ArchitectureSpecification.from_dict(document)
    assert restored == specification
    assert restored.architecture_hash == specification.architecture_hash
    assert document["architecture_hash"] == specification.architecture_hash
    serialized = json.dumps(document, sort_keys=True).lower()
    assert "routing" not in serialized
    assert "coupler" not in serialized
    assert "endpoint_half" not in serialized


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.profile_id)
def test_multinode_sizing_fails_closed_without_the_bell_protocol(
    case: _Case,
) -> None:
    profile = get_architecture_profile(case.profile_id)

    with pytest.raises(
        ValueError,
        match="needs a selected QEC protocol profile",
    ):
        construction_pipeline.construct_architecture(
            profile,
            _statistics(),
            case.sizing_policy(),
            layout_policy=get_gallery_entry(case.profile_id).make_layout_policy(),
            qec_bindings=_node_qec_bindings(profile),
            selected_qec_protocols={case.factory: _MAGIC_PROTOCOL},
        )


@pytest.mark.parametrize("profile_id", ("2.2", "2.3"))
def test_zero_memory_remains_explicit_in_canonical_multinode_specification(
    profile_id: str,
) -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "h", qubits=(0,)),),
            ),
        ),
        provenance={"benchmark": f"multinode-empty-memory-{profile_id}"},
    )

    specification = build_architecture_specification(circuit, profile_id)
    memory_regions = [
        submodule
        for node in specification.nodes
        for module in node.modules
        if module.type == "memory"
        for submodule in module.submodules
        if (submodule.type, submodule.payload) == ("region", "logical_qubit")
    ]
    store_load_buffers = [
        submodule
        for node in specification.nodes
        for module in node.modules
        for submodule in module.submodules
        if (submodule.type, submodule.payload) == ("buffer", "logical_qubit")
    ]

    assert len(memory_regions) == 1
    assert memory_regions[0].capacity == 0
    assert memory_regions[0].slots == ()
    assert store_load_buffers
    assert {submodule.capacity for submodule in store_load_buffers} == {1}
