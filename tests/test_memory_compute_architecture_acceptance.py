"""Independent acceptance gates for the Profile-2.1 memory-compute architecture."""

from __future__ import annotations

from typing import Any

import pytest

import heteqsys.architecture.construction as canonical_construction
from heteqsys.architecture import (
    LogicalLayoutRequest,
    SubmoduleKey,
    SubmoduleLayoutRequest,
    get_architecture_profile,
)
from heteqsys.architecture.specification import (
    ArchitectureSpecification as CoreArchitectureSpecification,
)
from heteqsys.program import FTCircuit, LogicalLayer, LogicalOperation
from heteqsys.specification import build_architecture_specification
from tests.architecture_semantic_oracle import oracle_circuit


def test_21_build_is_direct_and_uses_the_generic_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_architecture_profile("2.1")
    calls = 0
    generic_resolver = canonical_construction.resolve_architecture

    def resolve_spy(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return generic_resolver(*args, **kwargs)

    monkeypatch.setattr(canonical_construction, "resolve_architecture", resolve_spy)
    result = build_architecture_specification(
        oracle_circuit(),
        "2.1",
    )

    assert calls == 1
    assert isinstance(result, CoreArchitectureSpecification)


def test_21_core_owns_only_effective_capacity_and_logical_layout() -> None:
    core = build_architecture_specification(
        oracle_circuit(),
        "2.1",
    )
    assert CoreArchitectureSpecification.from_dict(core.to_dict()) == core
    assert core.interconnects == ()

    memory = core.submodule("na_node", "na_memory", "memory_region")
    compute = core.submodule("na_node", "na_compute", "compute_region")
    store_load = core.submodule(
        "na_node", "na_compute", "store_load_buffer"
    )
    magic_input = core.submodule(
        "na_node", "na_compute", "magic_state_input_buffer"
    )
    factory = core.submodule("na_node", "na_msf", "factory_engine")
    magic_output = core.submodule(
        "na_node", "na_msf", "magic_state_output_buffer"
    )

    assert (
        memory.capacity,
        compute.capacity,
        store_load.capacity,
        magic_input.capacity,
        factory.capacity,
        magic_output.capacity,
    ) == (2, 2, 2, 1, 1, 1)
    for submodule in (memory, compute, store_load, magic_input, magic_output):
        assert tuple(slot.id for slot in submodule.slots) == tuple(
            f"slot_{index}" for index in range(submodule.capacity)
        )
    assert all(slot.coordinate is None for slot in memory.slots)
    assert memory.logical_origin is None
    assert [
        core.effective_slot_coordinate(
            "na_node", "na_compute", "store_load_buffer", slot.id
        )
        for slot in store_load.slots
    ] == [(-3, 0), (-2, 0)]
    assert core.effective_slot_coordinate(
        "na_node", "na_compute", "magic_state_input_buffer", "slot_0"
    ) == (3, 0)
    assert core.effective_slot_coordinate(
        "na_node", "na_msf", "magic_state_output_buffer", "slot_0"
    ) == (200, 40)
    assert factory.slots == ()
    assert [
        connection.to_dict() for connection in core.node("na_node").connections
    ] == [
        {
            "id": "na_magic_bus",
            "direction": "directed",
            "endpoints": [
                "na_msf/magic_state_output_buffer",
                "na_compute/magic_state_input_buffer",
            ],
        },
        {
            "id": "na_memory_compute_bus",
            "direction": "bidirectional",
            "endpoints": [
                "na_compute/store_load_buffer",
                "na_memory/memory_region",
            ],
        },
    ]


def test_21_exact_layout_request_uses_canonical_slot_names() -> None:
    request = LogicalLayoutRequest(
        {
            SubmoduleKey(
                "na_node", "na_compute", "store_load_buffer"
            ): SubmoduleLayoutRequest(
                logical_origin=(-10, 5),
                slots={"slot_0": (0, 0), "slot_1": (1, 0)},
            )
        }
    )
    core = build_architecture_specification(
        oracle_circuit(),
        "2.1",
        logical_layout=request,
    )
    assert [
        core.effective_slot_coordinate(
            "na_node", "na_compute", "store_load_buffer", f"slot_{index}"
        )
        for index in range(2)
    ] == [(-10, 5), (-9, 5)]

def test_21_one_qubit_build_keeps_an_empty_memory_as_a_valid_owner() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "t", qubits=(0,)),),
            ),
        ),
        provenance={"benchmark": "memory-compute-empty-memory"},
    )
    core = build_architecture_specification(circuit, "2.1")
    assert CoreArchitectureSpecification.from_dict(core.to_dict()) == core
    memory = core.submodule("na_node", "na_memory", "memory_region")
    assert memory.capacity == 0
    assert memory.slots == ()
    assert memory.logical_origin is None
    assert memory.grid_shape is None

@pytest.mark.parametrize(
    "field",
    (
        "protocols.store_load.buffer_capacity",
        "protocols.magic_state.buffer_capacity",
        "protocols.magic_state.copies",
    ),
)
@pytest.mark.parametrize("value", (True, 1.5, "2"))
def test_21_public_capacity_overrides_reject_non_integer_types(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match="plain integer"):
        build_architecture_specification(
            oracle_circuit(),
            "2.1",
            policy_overrides={field: value},
        )
