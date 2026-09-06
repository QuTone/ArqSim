from __future__ import annotations

from collections import Counter

import pytest

from arqsim.architecture.gallery import QuantileSizingConfig
from arqsim.architecture.isa import ArchitectureOpcode
from arqsim.compiler import canonical_compiler_spec
from arqsim.compiler.layout import (
    primary_qec_submodule,
    single_node_module,
)
from arqsim.evaluation import (
    EvaluationPolicy,
    ExecutionPlan,
    ExecutionTransitionKind,
    compile_and_lower,
    default_runtime_component_manifest,
    evaluate,
)
from arqsim.evaluation.components import build_runtime_component_set
from arqsim.evaluation.lowering import (
    build_runtime_instruction_compiler,
    build_runtime_resource_compiler,
)
from arqsim.operation_profiles import (
    OperationLatencyProfile,
    resolve_resource_protocol_bindings,
    with_effective_arrivals,
)
from arqsim.program import FTCircuit, LogicalLayer, LogicalOperation
from arqsim.specification import build_architecture_specification


def _compile_plan(*args, **kwargs) -> ExecutionPlan:
    return compile_and_lower(*args, **kwargs)[1]


def _underfull_wave_circuit() -> FTCircuit:
    return FTCircuit(
        representation="clifford_t",
        num_qubits=5,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "h", qubits=(0,)),),
            ),
            LogicalLayer(
                1,
                (LogicalOperation("gate", "cx", qubits=(3, 4)),),
            ),
        ),
    )


def _location_capacities(specification) -> dict[str, int]:
    result: dict[str, int] = {}
    for module_type in ("compute", "memory"):
        entry = single_node_module(specification, module_type)
        assert entry is not None
        node, module = entry
        result[f"{node.id}/{module.id}"] = primary_qec_submodule(
            module
        ).capacity
        for submodule in module.submodules:
            if (
                submodule.type == "buffer"
                and submodule.payload == "logical_qubit"
            ):
                result[
                    f"{node.id}/{module.id}/{submodule.id}"
                ] = submodule.capacity
    return result


def _assert_transition_capacities(result, plan, location_capacities) -> None:
    locations = {
        item: location
        for item, location in plan.initial_locations.items()
        if item.startswith("q:")
    }
    buffer_capacities = {buffer.id: buffer.capacity for buffer in plan.buffers}

    def assert_state_within_capacity() -> None:
        occupancy = Counter(locations.values())
        assert set(occupancy) <= set(location_capacities)
        assert all(
            occupancy[location] <= capacity
            for location, capacity in location_capacities.items()
        )

    assert_state_within_capacity()
    for transition in result.trace.transitions:
        if transition.kind == ExecutionTransitionKind.COMPLETION:
            for item, location in transition.completion_locations.items():
                if item.startswith("q:"):
                    locations[item] = location
        assert_state_within_capacity()
        assert all(
            transition.buffer_occupancy_after[buffer_id]
            + transition.pending_incoming_after[buffer_id]
            <= capacity
            for buffer_id, capacity in buffer_capacities.items()
        )

    terminal_locations = {
        item: location
        for item, location in result.trace.terminal_state.locations.items()
        if item.startswith("q:")
    }
    assert locations == terminal_locations


@pytest.mark.parametrize("profile_id", ("2.1", "2.2"))
def test_deferred_residency_exchange_never_exceeds_architecture_capacity(
    profile_id: str,
) -> None:
    circuit = _underfull_wave_circuit()
    specification = build_architecture_specification(
        circuit,
        profile_id,
        quantile_config=QuantileSizingConfig.uniform(0.0),
    )
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification,
        requested_latency,
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    compiler = canonical_compiler_spec(specification)
    components = build_runtime_component_set(
        default_runtime_component_manifest(),
        runtime_instruction_compiler=build_runtime_instruction_compiler(
            circuit,
            specification,
            compiler,
            latency,
        ),
        runtime_resource_compiler=build_runtime_resource_compiler(
            specification,
            compiler,
            latency,
        ),
    )
    plan = _compile_plan(
        circuit,
        specification,
        latency,
        EvaluationPolicy(trace_level="full"),
        compiler_spec=compiler,
        runtime_components=components.manifest.to_dict(),
        resource_protocol_bindings=bindings,
    )
    plan = ExecutionPlan.from_json(plan.to_json())
    result = evaluate(plan, runtime_components=components)

    stores = tuple(
        instruction
        for instruction in plan.program_dag.instructions
        if instruction.opcode == ArchitectureOpcode.STORE_QUBITS
    )
    exchanges = tuple(
        instruction
        for instruction in plan.program_dag.instructions
        if instruction.opcode == ArchitectureOpcode.LOAD_QUBITS
    )
    assert stores and exchanges
    assert all(not instruction.completion_locations for instruction in stores)
    assert all(
        len(instruction.qubits)
        == instruction.engines["store_load_buffer"]
        and set(instruction.required_locations)
        == set(instruction.completion_locations)
        and len(instruction.required_locations) == 2 * len(instruction.qubits)
        and {f"q:{qubit}" for qubit in instruction.qubits}
        <= set(instruction.required_locations)
        for instruction in exchanges
    )
    completed_loads = {
        event.instruction_id: event
        for event in result.events
        if event.opcode == ArchitectureOpcode.LOAD_QUBITS
    }
    assert set(completed_loads) == {instruction.id for instruction in exchanges}
    assert all(
        tuple(completed_loads[instruction.id].metadata["qubits"])
        == instruction.qubits
        for instruction in exchanges
    )

    transfer_opcodes = {
        ArchitectureOpcode.MOVE_QUBITS,
        ArchitectureOpcode.STORE_QUBITS,
        ArchitectureOpcode.LOAD_QUBITS,
        ArchitectureOpcode.TELEPORT_QUBITS,
    }
    transfers = tuple(
        instruction
        for instruction in plan.program_dag.instructions
        if instruction.opcode in transfer_opcodes
    )
    expected_round = (
        (
            ArchitectureOpcode.MOVE_QUBITS,
            ArchitectureOpcode.TELEPORT_QUBITS,
            ArchitectureOpcode.STORE_QUBITS,
            ArchitectureOpcode.LOAD_QUBITS,
            ArchitectureOpcode.TELEPORT_QUBITS,
            ArchitectureOpcode.MOVE_QUBITS,
        )
        if profile_id == "2.2"
        else (
            ArchitectureOpcode.MOVE_QUBITS,
            ArchitectureOpcode.STORE_QUBITS,
            ArchitectureOpcode.LOAD_QUBITS,
            ArchitectureOpcode.MOVE_QUBITS,
        )
    )
    assert tuple(item.opcode for item in transfers) == expected_round * 2
    assert all(
        current.predecessor_ids == (previous.id,)
        for previous, current in zip(transfers, transfers[1:])
    )
    for start in range(0, len(transfers), len(expected_round)):
        exchange_round = transfers[start : start + len(expected_round)]
        store = next(
            item
            for item in exchange_round
            if item.opcode == ArchitectureOpcode.STORE_QUBITS
        )
        load = next(
            item
            for item in exchange_round
            if item.opcode == ArchitectureOpcode.LOAD_QUBITS
        )
        assert store.duration_s == load.duration_s > 0
        assert (
            store.metadata["syndrome_protocol"]["duration_s"]
            == load.metadata["syndrome_protocol"]["duration_s"]
            == store.duration_s
        )
        teleports = tuple(
            item
            for item in exchange_round
            if item.opcode == ArchitectureOpcode.TELEPORT_QUBITS
        )
        assert len(teleports) == (2 if profile_id == "2.2" else 0)
        assert len({item.duration_s for item in teleports}) <= 1

    _assert_transition_capacities(
        result,
        plan,
        _location_capacities(specification),
    )
