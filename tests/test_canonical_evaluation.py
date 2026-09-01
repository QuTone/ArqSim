"""Acceptance gates for canonical evaluation inputs and derived artifacts."""

from __future__ import annotations

from dataclasses import replace

import pytest

from heteqsys.evaluation import (
    PhysicalFootprintModel,
    estimate_physical_footprint,
)
from heteqsys.operation_profiles import (
    OperationLatencyProfile,
    canonical_fidelity_profile,
    resolve_resource_protocol_bindings,
)
from heteqsys.program import FTCircuit, LogicalLayer, LogicalOperation
from heteqsys.specification import build_architecture_specification


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
    )


@pytest.mark.parametrize(
    ("profile_id", "expected_qubits", "expected_components"),
    (
        ("1.1", 6781.0, 4),
        ("1.2", 22957.0, 4),
        ("1.3", 19195.0, 8),
        ("2.1", 6841.0, 6),
        ("2.2", 33409.0, 11),
        ("2.3", 19255.0, 10),
    ),
)
def test_canonical_footprint_preserves_reference_baseline_totals(
    profile_id: str,
    expected_qubits: float,
    expected_components: int,
) -> None:
    specification = build_architecture_specification(
        _sizing_circuit(), profile_id
    )
    footprint = estimate_physical_footprint(
        specification,
        PhysicalFootprintModel.reference_v1(),
    )

    assert footprint.architecture_hash == specification.architecture_hash
    assert footprint.total_physical_qubits == expected_qubits
    assert len(footprint.components) == expected_components
    assert all(footprint.checks.values())
    assert all(component.owner_id for component in footprint.components)


def test_resource_bindings_name_qualified_canonical_engines() -> None:
    specification = build_architecture_specification(
        _sizing_circuit(), "1.3"
    )
    bindings = resolve_resource_protocol_bindings(
        specification,
        OperationLatencyProfile(),
    )

    assert bindings.magic_state is not None
    assert bindings.logical_bell_pair is not None
    assert bindings.magic_state.provenance["engine"] == (
        "sc_msf_node/sc_msf/factory_engine"
    )
    assert bindings.logical_bell_pair.provenance["engine"] == (
        "compute_msf_link/bell_engine/pair_generator"
    )
    assert {
        binding.provenance["architecture_hash"]
        for binding in bindings.bindings.values()
    } == {specification.architecture_hash}


def test_pinned_protocol_hash_is_checked_by_binding_and_footprint() -> None:
    specification = build_architecture_specification(
        _sizing_circuit(), "1.1"
    )
    node = specification.nodes[0]
    modules = list(node.modules)
    module_index = next(
        index for index, module in enumerate(modules)
        if module.type == "resource_factory"
    )
    module = modules[module_index]
    submodules = list(module.submodules)
    engine_index = next(
        index for index, submodule in enumerate(submodules)
        if submodule.type == "engine" and submodule.payload == "magic_state"
    )
    engine = submodules[engine_index]
    assert engine.resource_protocol is not None
    submodules[engine_index] = replace(
        engine,
        resource_protocol=replace(
            engine.resource_protocol,
            profile_hash="0" * 64,
        ),
    )
    modules[module_index] = replace(module, submodules=tuple(submodules))
    tampered = replace(
        specification,
        nodes=(replace(node, modules=tuple(modules)),),
    )

    with pytest.raises(ValueError, match="Protocol profile hash mismatch"):
        resolve_resource_protocol_bindings(
            tampered,
            OperationLatencyProfile(),
        )
    with pytest.raises(ValueError, match="Magic-state protocol hash mismatch"):
        estimate_physical_footprint(
            tampered,
            PhysicalFootprintModel.reference_v1(),
        )


def test_legacy_factory_model_field_cannot_override_protocol_intrinsic() -> None:
    specification = build_architecture_specification(
        _sizing_circuit(), "1.1"
    )

    with pytest.raises(ValueError, match="legacy codec field"):
        estimate_physical_footprint(
            specification,
            PhysicalFootprintModel.reference_v1(
                factory_qubits_per_copy=999.0
            ),
        )


def test_fidelity_locations_use_qualified_canonical_addresses() -> None:
    specification = build_architecture_specification(
        _sizing_circuit(), "2.1"
    )
    latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(specification, latency)
    profile = canonical_fidelity_profile(
        specification,
        latency,
        bindings,
    )

    locations = set(profile.idle_failure_probability_per_cycle)
    assert "na_node/na_compute" in locations
    assert "na_node/na_compute/store_load_buffer" in locations
    assert "na_node/na_memory" in locations
    assert all(location.count("/") in {1, 2} for location in locations)
