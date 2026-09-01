from __future__ import annotations

from collections import Counter
from dataclasses import replace

import pytest

from heteqsys.architecture.gallery import QuantileSizingConfig
from heteqsys.architecture.isa import ArchitectureOpcode
from heteqsys.compiler import (
    CompilerPipeline,
    DefaultCompilerPipeline,
    LogicalCompilationResult,
    canonical_compiler_spec,
)
from heteqsys.compiler.errors import LogicalCompilerValidationError
from heteqsys.compiler.layout import (
    primary_qec_submodule,
    single_node_module,
)
from heteqsys.evaluation import (
    EvaluationPolicy,
    ExecutionPlan,
    ExecutionTransitionKind,
    compile_and_lower,
    default_runtime_component_manifest,
    evaluate,
    lower_compilation_result,
)
from heteqsys.evaluation.components import build_runtime_component_set
from heteqsys.evaluation.lowering import (
    build_runtime_instruction_compiler,
    build_runtime_resource_compiler,
)
from heteqsys.operation_profiles import (
    OperationLatencyProfile,
    resolve_resource_protocol_bindings,
    with_effective_arrivals,
)
from heteqsys.program import FTCircuit, LogicalLayer, LogicalOperation
from heteqsys.specification import build_architecture_specification


_RELABELINGS = (
    (0, 1, 2, 3, 4),
    (4, 3, 2, 1, 0),
    (2, 4, 1, 0, 3),
)


class _StaticPipeline(CompilerPipeline):
    """Exercise the public custom-pipeline trust boundary."""

    def __init__(self, result: LogicalCompilationResult) -> None:
        self._result = result

    def compile(self, _circuit, _specification, _latency_profile):
        return self._result


def _residency_circuit(relabeling: tuple[int, ...]) -> FTCircuit:
    def relabel(qubits: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(relabeling[qubit] for qubit in qubits)

    operands = ((0,), (3, 4), (2,), (1, 3), (4,))
    return FTCircuit(
        representation="clifford_t",
        num_qubits=len(relabeling),
        num_clbits=0,
        layers=tuple(
            LogicalLayer(
                layer_index,
                (
                    LogicalOperation(
                        "gate",
                        "h" if len(qubits) == 1 else "cx",
                        qubits=relabel(qubits),
                    ),
                ),
            )
            for layer_index, qubits in enumerate(operands)
        ),
    )


def _compilation_fixture(
    profile_id: str,
    relabeling: tuple[int, ...],
):
    circuit = _residency_circuit(relabeling)
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
    compilation = DefaultCompilerPipeline(
        compiler_spec=compiler,
        defer_magic_routing=False,
    ).compile(circuit, specification, latency)
    return circuit, specification, latency, bindings, compiler, compilation


def _logical_location_capacities(specification) -> dict[str, int]:
    capacities: dict[str, int] = {}
    for module_type in ("compute", "memory"):
        entry = single_node_module(specification, module_type)
        assert entry is not None
        node, module = entry
        module_location = f"{node.id}/{module.id}"
        capacities[module_location] = primary_qec_submodule(module).capacity
        for submodule in module.submodules:
            if (
                submodule.type == "buffer"
                and submodule.payload == "logical_qubit"
            ):
                capacities[f"{module_location}/{submodule.id}"] = (
                    submodule.capacity
                )
    return capacities


@pytest.mark.parametrize("profile_id", ("2.1", "2.2", "2.3"))
@pytest.mark.parametrize("relabeling", _RELABELINGS)
def test_residency_width_is_invariant_under_logical_relabeling(
    profile_id: str,
    relabeling: tuple[int, ...],
) -> None:
    (
        circuit,
        specification,
        latency,
        bindings,
        _compiler,
        compilation,
    ) = _compilation_fixture(profile_id, relabeling)
    compute_entry = single_node_module(specification, "compute")
    memory_entry = single_node_module(specification, "memory")
    assert compute_entry is not None
    assert memory_entry is not None
    compute_capacity = primary_qec_submodule(compute_entry[1]).capacity
    memory_capacity = primary_qec_submodule(memory_entry[1]).capacity
    expected_residents = min(circuit.num_qubits, compute_capacity)

    assert compilation.deferred_capacity_mapping is True
    assert compilation.initial_mapping == {}
    for unit in compilation.compute_units:
        assert len(unit.mapping) == expected_residents
        assert set(unit.source.partition.active_qubits) <= set(unit.mapping)
        assert (
            circuit.num_qubits - len(unit.mapping) <= memory_capacity
        )
        assert len(set(unit.mapping.values())) == len(unit.mapping)
    for previous, current in zip(
        compilation.compute_units,
        compilation.compute_units[1:],
    ):
        for qubit in set(previous.mapping) & set(current.mapping):
            assert previous.mapping[qubit] == current.mapping[qubit]

    # A valid externally supplied result must survive the same independent
    # validator used for native compiler output.
    _, plan = compile_and_lower(
        circuit,
        specification,
        latency,
        EvaluationPolicy(),
        compiler_pipeline=_StaticPipeline(compilation),
        resource_protocol_bindings=bindings,
    )
    initial_occupancy = Counter(plan.initial_locations.values())
    compute_location = f"{compute_entry[0].id}/{compute_entry[1].id}"
    memory_location = f"{memory_entry[0].id}/{memory_entry[1].id}"
    assert initial_occupancy == {
        compute_location: expected_residents,
        memory_location: circuit.num_qubits - expected_residents,
    }

    # LOAD is the atomic commit of each balanced O->I exchange.  Its location
    # multiset must be unchanged, even when logical labels are permuted.
    stores = tuple(
        instruction
        for instruction in plan.program_dag.instructions
        if instruction.opcode == ArchitectureOpcode.STORE_QUBITS
    )
    loads = tuple(
        instruction
        for instruction in plan.program_dag.instructions
        if instruction.opcode == ArchitectureOpcode.LOAD_QUBITS
    )
    assert stores and len(stores) == len(loads)
    assert [len(instruction.qubits) for instruction in stores] == [
        len(instruction.qubits) for instruction in loads
    ]
    assert all(
        instruction.metadata["outgoing_amount"] == len(instruction.qubits)
        and len(instruction.required_locations)
        == len(instruction.completion_locations)
        == 2 * len(instruction.qubits)
        and Counter(instruction.required_locations.values())
        == Counter(instruction.completion_locations.values())
        for instruction in loads
    )


@pytest.mark.parametrize("profile_id", ("2.1", "2.2", "2.3"))
def test_custom_pipeline_cannot_reintroduce_active_only_residency(
    profile_id: str,
) -> None:
    (
        circuit,
        specification,
        latency,
        bindings,
        _compiler,
        compilation,
    ) = _compilation_fixture(profile_id, _RELABELINGS[0])
    first = compilation.compute_units[0]
    active_only = {
        qubit: first.mapping[qubit]
        for qubit in first.source.partition.active_qubits
    }
    assert len(active_only) < len(first.mapping)
    forged = replace(
        compilation,
        compute_units=(
            replace(first, mapping=active_only),
            *compilation.compute_units[1:],
        ),
    )

    failures: list[LogicalCompilerValidationError] = []
    for lower in (
        lambda: lower_compilation_result(
            circuit,
            specification,
            latency,
            EvaluationPolicy(),
            forged,
            resource_protocol_bindings=bindings,
        ),
        lambda: compile_and_lower(
            circuit,
            specification,
            latency,
            EvaluationPolicy(),
            compiler_pipeline=_StaticPipeline(forged),
            resource_protocol_bindings=bindings,
        ),
    ):
        with pytest.raises(
            LogicalCompilerValidationError,
            match="canonical compute layout",
        ) as exc_info:
            lower()
        failures.append(exc_info.value)

    for failure in failures:
        first_errors = {
            error["reason"]
            for error in failure.details["unit_mapping_errors"]
            if error["layer"] == 0
        }
        assert {
            "residency_cardinality_mismatch",
            "memory_residency_exceeds_capacity",
        } <= first_errors


def test_typed_compilation_unit_rejects_two_qubits_in_one_physical_slot() -> None:
    *_, compilation = _compilation_fixture("2.1", _RELABELINGS[0])
    unit = compilation.compute_units[0]
    first_qubit, second_qubit = tuple(unit.mapping)

    with pytest.raises(
        LogicalCompilerValidationError,
        match="must assign distinct slots",
    ):
        replace(
            unit,
            mapping={
                first_qubit: unit.mapping[first_qubit],
                second_qubit: unit.mapping[first_qubit],
            },
        )


@pytest.mark.parametrize("profile_id", ("2.1", "2.2", "2.3"))
@pytest.mark.parametrize("relabeling", _RELABELINGS)
def test_runtime_trace_conserves_logical_storage_and_transient_capacity(
    profile_id: str,
    relabeling: tuple[int, ...],
) -> None:
    (
        circuit,
        specification,
        latency,
        bindings,
        compiler,
        compilation,
    ) = _compilation_fixture(profile_id, relabeling)
    policy = EvaluationPolicy(trace_level="full")
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
    _, lowered = compile_and_lower(
        circuit,
        specification,
        latency,
        policy,
        compiler_pipeline=_StaticPipeline(compilation),
        runtime_components=components.manifest.to_dict(),
        resource_protocol_bindings=bindings,
    )
    plan = ExecutionPlan.from_json(lowered.to_json())
    result = evaluate(plan, runtime_components=components)

    logical_items = frozenset(f"q:{qubit}" for qubit in range(circuit.num_qubits))
    locations = {
        item: location
        for item, location in plan.initial_locations.items()
        if item.startswith("q:")
    }
    location_capacities = _logical_location_capacities(specification)
    buffer_capacities = {buffer.id: buffer.capacity for buffer in plan.buffers}

    def assert_capacity_invariants() -> None:
        assert frozenset(locations) == logical_items
        assert len(locations) == circuit.num_qubits
        occupancy = Counter(locations.values())
        assert set(occupancy) <= set(location_capacities)
        assert all(
            occupancy[location] <= capacity
            for location, capacity in location_capacities.items()
        )

    assert_capacity_invariants()
    for transition in result.trace.transitions:
        if transition.kind == ExecutionTransitionKind.COMPLETION:
            for item, location in transition.completion_locations.items():
                if item.startswith("q:"):
                    assert item in logical_items
                    locations[item] = location
        assert_capacity_invariants()
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
