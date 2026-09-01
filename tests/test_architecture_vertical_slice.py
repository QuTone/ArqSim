from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

import heteqsys.specification as specification_module
from heteqsys.api import EvaluationConfig, run_evaluation
from heteqsys.architecture import (
    ARCHITECTURE_PROFILE_SCHEMA_VERSION,
    ArchitectureProfile,
    get_architecture_profile,
)
from heteqsys.architecture.isa import MagicRouteDispatchRecipe
from heteqsys.architecture.specification import ArchitectureSpecification
from heteqsys.compiler.layout import materialize_compute_layout
from heteqsys.evaluation import EvaluationPolicy
from heteqsys.operation_profiles import ArrivalDistribution, OperationLatencyProfile
from heteqsys.program import FTCircuit, LogicalLayer, LogicalOperation
from heteqsys.schema import normalize_json
from heteqsys.specification import build_architecture_specification


def _single_t_circuit() -> FTCircuit:
    return FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "t", qubits=(0,)),),
            ),
        ),
        provenance={"benchmark": "architecture-v3-vertical-slice"},
    )


def _latency_profile() -> OperationLatencyProfile:
    return OperationLatencyProfile(
        magic_state_arrival=ArrivalDistribution.from_rate(
            1_000.0,
            kind="deterministic",
        )
    )


def _qualified_slots(
    specification: ArchitectureSpecification,
    owner_id: str,
    module_id: str,
    submodule_id: str,
) -> tuple[str, ...]:
    return tuple(
        f"{owner_id}/{module_id}/{submodule_id}/{slot.id}"
        for slot in specification.submodule(
            owner_id, module_id, submodule_id
        ).slots
    )


def test_11_profile_resolves_directly_with_exact_topology() -> None:
    profile = get_architecture_profile("1.1")
    specification = build_architecture_specification(_single_t_circuit(), "1.1")

    assert isinstance(specification, ArchitectureSpecification)
    assert profile.to_dict()["schema_version"] == (
        ARCHITECTURE_PROFILE_SCHEMA_VERSION
    )
    assert [node.id for node in specification.nodes] == ["na_node"]
    assert specification.interconnects == ()

    profile_node = profile.nodes[0]
    resolved_node = specification.node(profile_node.id)
    assert resolved_node.modality == profile_node.modality
    assert {
        (module.id, module.type): {
            (submodule.id, submodule.type, submodule.payload)
            for submodule in module.submodules
        }
        for module in resolved_node.modules
    } == {
        (module.id, module.type): {
            (submodule.id, submodule.type, submodule.payload)
            for submodule in module.submodules
        }
        for module in profile_node.modules
    }
    assert [connection.to_dict() for connection in resolved_node.connections] == [
        {
            "id": "na_magic_bus",
            "direction": "directed",
            "endpoints": [
                "na_msf/magic_state_output_buffer",
                "na_compute/magic_state_input_buffer",
            ],
        }
    ]


def test_11_build_uses_the_public_profile_as_topology_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = deepcopy(get_architecture_profile("1.1").to_dict())
    connections = payload["nodes"]["na_node"]["connections"]
    connections["drifted_magic_bus"] = connections.pop("na_magic_bus")
    drifted = ArchitectureProfile.from_dict(payload)
    entry = specification_module.get_gallery_entry("1.1")
    monkeypatch.setattr(
        specification_module,
        "get_gallery_entry",
        lambda _profile_id: replace(entry, profile=drifted),
    )

    specification = specification_module.build_architecture_specification(
        _single_t_circuit(),
        "1.1",
    )
    assert specification.nodes[0].connections[0].id == "drifted_magic_bus"


def test_11_specification_has_qualified_slots_and_black_box_engines() -> None:
    first = build_architecture_specification(_single_t_circuit(), "1.1")
    second = build_architecture_specification(_single_t_circuit(), "1.1")
    assert first == second
    assert first.architecture_hash == second.architecture_hash

    compute = first.submodule("na_node", "na_compute", "compute_region")
    magic_input = first.submodule(
        "na_node", "na_compute", "magic_state_input_buffer"
    )
    factory = first.submodule("na_node", "na_msf", "factory_engine")
    magic_output = first.submodule(
        "na_node", "na_msf", "magic_state_output_buffer"
    )
    qualified = (
        *_qualified_slots(first, "na_node", "na_compute", compute.id),
        *_qualified_slots(first, "na_node", "na_compute", magic_input.id),
        *_qualified_slots(first, "na_node", "na_msf", magic_output.id),
    )
    assert len(qualified) == len(set(qualified))
    assert all(
        submodule.qec is not None
        for submodule in (compute, magic_input, magic_output)
    )
    assert factory.type == "engine"
    assert factory.slots == ()
    assert factory.qec is None
    assert factory.resource_protocol is not None

    compiler_layout = materialize_compute_layout(first)
    assert {slot.id for slot in compiler_layout.slots_of_kind("data")} == set(
        _qualified_slots(first, "na_node", "na_compute", compute.id)
    )
    assert {
        slot.id for slot in compiler_layout.slots_of_kind("magic_state")
    } == set(
        _qualified_slots(first, "na_node", "na_compute", magic_input.id)
    )
    assert set(
        _qualified_slots(first, "na_node", "na_msf", magic_output.id)
    ).isdisjoint(slot.id for slot in compiler_layout.slots)


def test_11_single_t_trace_preserves_plan_ownership_and_token_lineage() -> None:
    report = run_evaluation(
        _single_t_circuit(),
        EvaluationConfig(
            profile_id="1.1",
            workflow_id="vertical-slice-runtime",
            latency_profile=_latency_profile(),
            evaluation_policy=EvaluationPolicy(trace_level="full", seed=0),
        ),
    )
    assert isinstance(report.specification, ArchitectureSpecification)
    plan = report.execution_plan
    buffers = {buffer.id: buffer for buffer in plan.buffers}
    engines = {engine.id: engine for engine in plan.engines}

    assert (
        buffers["magic_compute"].module,
        buffers["magic_compute"].submodule,
    ) == (
        "na_node/na_compute",
        "na_node/na_compute/magic_state_input_buffer",
    )
    assert (buffers["msf_output"].module, buffers["msf_output"].submodule) == (
        "na_node/na_msf",
        "na_node/na_msf/magic_state_output_buffer",
    )
    assert buffers["magic_compute"].slots == _qualified_slots(
        report.specification,
        "na_node",
        "na_compute",
        "magic_state_input_buffer",
    )
    assert buffers["msf_output"].slots == _qualified_slots(
        report.specification,
        "na_node",
        "na_msf",
        "magic_state_output_buffer",
    )
    compute_engine = engines["compute:na_node/na_compute"]
    assert (compute_engine.module, compute_engine.submodule) == (
        "na_node/na_compute",
        "na_node/na_compute/compute_region",
    )
    factory_engine = engines["msf:na_node/na_msf"]
    assert (factory_engine.module, factory_engine.submodule) == (
        "na_node/na_msf",
        "na_node/na_msf/factory_engine",
    )
    assert factory_engine.capacity == report.specification.submodule(
        "na_node", "na_msf", "factory_engine"
    ).capacity
    assert engines["resource_move"].submodule is None
    assert normalize_json(plan.provenance["derived_engine_sources"]) == {
        "resource_move": {
            "scope": "local_connection",
            "id": "na_node/na_magic_bus",
        }
    }

    execute_events = [
        event
        for event in report.evaluation.events
        if event.opcode.value == "EXECUTE_COMPUTE"
        and event.consumed_tokens.get("magic_compute")
    ]
    assert len(execute_events) == 1
    execute = execute_events[0]
    token = execute.consumed_tokens["magic_compute"][0]
    prepare = next(
        event
        for event in report.evaluation.events
        if event.process_id == "prepare_magic"
        and token in event.produced_tokens.get("msf_output", ())
    )
    delivery = next(
        event
        for event in report.evaluation.events
        if event.process_id == "deliver_magic_local"
        and token in event.produced_tokens.get("magic_compute", ())
    )

    assert prepare.produced_tokens["msf_output"] == (token,)
    assert delivery.consumed_tokens["msf_output"] == (token,)
    assert delivery.produced_tokens["magic_compute"] == (token,)
    assert execute.consumed_tokens["magic_compute"] == (token,)
    assert prepare.produced_slots["msf_output"] == delivery.consumed_slots[
        "msf_output"
    ]
    assert delivery.produced_slots["magic_compute"] == execute.consumed_slots[
        "magic_compute"
    ]
    assert set(prepare.produced_slots["msf_output"]) <= set(
        buffers["msf_output"].slots
    )
    assert set(delivery.produced_slots["magic_compute"]) <= set(
        buffers["magic_compute"].slots
    )
    assert prepare.end_s <= delivery.start_s <= execute.start_s
    assert execute.instruction_id is not None
    execute_instruction = plan.program_dag.instructions[execute.instruction_id]
    recipe = execute_instruction.deferred_dispatch
    assert isinstance(recipe, MagicRouteDispatchRecipe)
    assert set(recipe.data_mapping.values()) <= set(
        _qualified_slots(
            report.specification,
            "na_node",
            "na_compute",
            "compute_region",
        )
    )
    assert all(report.evaluation.invariant_checks.values())


def test_all_six_profiles_resolve_their_exact_v3_owner_graph() -> None:
    for profile_id in ("1.1", "1.2", "1.3", "2.1", "2.2", "2.3"):
        profile = get_architecture_profile(profile_id)
        specification = build_architecture_specification(
            _single_t_circuit(), profile_id
        )

        assert profile.to_dict()["schema_version"] == (
            ARCHITECTURE_PROFILE_SCHEMA_VERSION
        )
        assert [node.id for node in specification.nodes] == [
            node.id for node in profile.nodes
        ]
        assert [item.id for item in specification.interconnects] == [
            item.id for item in profile.interconnects
        ]
        for profile_owner in (*profile.nodes, *profile.interconnects):
            resolved_owner = (
                specification.node(profile_owner.id)
                if profile_owner in profile.nodes
                else specification.interconnect(profile_owner.id)
            )
            assert {
                (module.id, module.type): {
                    (submodule.id, submodule.type, submodule.payload)
                    for submodule in module.submodules
                }
                for module in resolved_owner.modules
            } == {
                (module.id, module.type): {
                    (submodule.id, submodule.type, submodule.payload)
                    for submodule in module.submodules
                }
                for module in profile_owner.modules
            }
            assert [
                connection.to_dict() for connection in resolved_owner.connections
            ] == [
                {
                    "id": connection.id,
                    "direction": connection.direction,
                    "endpoints": list(connection.endpoints),
                }
                for connection in profile_owner.connections
            ]
            if profile_owner in profile.interconnects:
                assert resolved_owner.endpoints == profile_owner.endpoints

            for module in resolved_owner.modules:
                for submodule in module.submodules:
                    if submodule.type == "engine":
                        assert submodule.slots == ()
                        assert submodule.resource_protocol is not None
                    else:
                        assert len(submodule.slots) == submodule.capacity

        serialized = str(specification.to_dict()).lower()
        assert "routing" not in serialized
        assert "coupler" not in serialized


def test_21_bidirectional_profile_is_resolved_without_losing_direction() -> None:
    profile = get_architecture_profile("2.1")
    profile_movement = next(
        item
        for item in profile.nodes[0].connections
        if item.id == "na_memory_compute_bus"
    )
    assert profile_movement.direction == "bidirectional"

    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=4,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "t", qubits=(0,)),),
            ),
        ),
        provenance={"benchmark": "profile-2.1-pressure-test"},
    )
    specification = build_architecture_specification(circuit, "2.1")
    resolved_movement = next(
        connection
        for connection in specification.node("na_node").connections
        if connection.id == "na_memory_compute_bus"
    )
    assert resolved_movement.direction == "bidirectional"
    assert resolved_movement.endpoints == profile_movement.endpoints
