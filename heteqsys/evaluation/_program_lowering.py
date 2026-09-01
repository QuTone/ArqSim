"""Program-DAG lowering from typed compiler output and explicit costs."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from heteqsys.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    MagicRouteDispatchRecipe,
    MoveOperands,
    ResourceMoveDispatchRecipe,
)
from heteqsys.architecture.recipes import (
    InjectionRecipe,
    InjectionStage,
    ResourceRef,
    ResourceStateKind,
)
from heteqsys.architecture.specification import ArchitectureSpecification
from heteqsys.compiler.layout import single_node_module
from heteqsys.compiler.movement import bind_program_move_costs
from heteqsys.compiler.output import LogicalCompilationResult, CompiledComputeUnit
from heteqsys.operation_profiles import OperationLatencyProfile
from heteqsys.program import FTCircuit
from heteqsys.program.statistics import operation_uses_magic_state

from ._plan_inputs import PlanCostInputs, RuntimeTopology
from .policy import EvaluationPolicy, RuntimeInjectionMode
from .program_dag import ProgramDAG


def _chunks(
    values: Iterable[int],
    capacity: int,
) -> tuple[tuple[int, ...], ...]:
    ordered = tuple(sorted(set(values)))
    return tuple(
        ordered[start : start + capacity]
        for start in range(0, len(ordered), capacity)
    )


@dataclass(frozen=True)
class _LoweredProgramPlan:
    """Finite Program DAG and its circuit-dependent initial locations."""

    dag: ProgramDAG
    initial_locations: Mapping[str, str]


def _summarize_gates(
    circuit: FTCircuit,
    layer_index: int,
    operation_indices: Iterable[int],
) -> dict[str, list[Any]]:
    """Project a compact diagnostic gate summary for the Program receipt."""

    grouped: dict[str, list[Any]] = {}
    operations = circuit.layers[layer_index].operations
    for operation_index in operation_indices:
        operation = operations[operation_index]
        target: Any = (
            operation.qubits[0]
            if len(operation.qubits) == 1
            else list(operation.qubits)
        )
        grouped.setdefault(operation.name.lower(), []).append(target)
    return grouped


def _compute_unit_metadata(
    circuit: FTCircuit,
    unit: CompiledComputeUnit,
    all_units: Iterable[CompiledComputeUnit],
    *,
    magic_state_consumption: str,
) -> dict[str, Any]:
    """Project typed compilation facts into diagnostic Plan metadata."""

    source = unit.source
    route_record = unit.route.to_instruction_metadata()
    consumed_through_batch = sum(
        candidate.source.magic_count
        for candidate in all_units
        if candidate.source.layer_index == source.layer_index
        and candidate.source.partition.partition_index
        == source.partition.partition_index
        and candidate.source.batch_index <= source.batch_index
    )
    return {
        "source_layer": source.layer_index,
        "compute_partition": source.partition.partition_index,
        "compute_partition_count": source.partition.partition_count,
        "magic_batch": source.batch_index,
        "magic_batch_count": source.batch_count,
        "operation_indices": list(source.operation_indices),
        "gates": _summarize_gates(
            circuit, source.layer_index, source.operation_indices
        ),
        "resident_qubits": sorted(unit.mapping),
        "operated_qubits": list(source.operated_qubits),
        "partition_magic_demand": source.partition.magic_count,
        "batch_magic_demand": source.magic_count,
        "magic_remaining_after_batch": max(
            0, source.partition.magic_count - consumed_through_batch
        ),
        "magic_consumption_policy": magic_state_consumption,
        "mapping": {
            str(key): value for key, value in sorted(unit.mapping.items())
        },
        "route_hash": route_record["route_hash"],
        "route_metrics": route_record["route_metrics"],
        "route_steps": route_record["route_steps"],
        "duration_components_s": route_record["duration_components_s"],
        "syndrome_protocol": route_record["syndrome_protocol"],
    }


def _lower_program_dag(
    circuit: FTCircuit,
    specification: ArchitectureSpecification,
    compilation: LogicalCompilationResult,
    topology: RuntimeTopology,
    cost_inputs: PlanCostInputs,
    model: OperationLatencyProfile,
    policy: EvaluationPolicy,
) -> _LoweredProgramPlan:
    """Lower typed compiler units into ordered Program-plane ISA commands."""

    execution_units = compilation.compute_units
    if not execution_units:
        return _LoweredProgramPlan(ProgramDAG(()), {})
    memory_entry = single_node_module(
        specification, "memory", required=False
    )
    memory = memory_entry[1] if memory_entry is not None else None
    compute_location = topology.compute_location
    memory_location = topology.memory_location
    compute_buffer_location = topology.compute_buffer_location
    memory_buffer_location = topology.memory_buffer_location
    recipe_mode_enabled = (
        policy.runtime_injection_mode
        == RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
    )
    if (
        recipe_mode_enabled
        and topology.compute_modality not in model.reaction_latency_by_modality_s
    ):
        raise ValueError(
            "finite_state_injection_v1 requires an explicit reaction latency "
            f"for compute modality {topology.compute_modality!r}"
        )
    if memory is not None and (
        memory_location is None or compute_buffer_location is None
    ):
        raise ValueError(
            "Memory lowering requires canonical Module/buffer locations"
        )
    if topology.memory_link is not None and memory_buffer_location is None:
        raise ValueError(
            "Remote memory lowering requires an endpoint-local memory buffer"
        )
    if memory is not None and cost_inputs.store_load is None:
        raise ValueError("Memory lowering requires a typed store/load cost")

    commands: list[ArchitectureInstruction] = []

    def append(
        opcode: ArchitectureOpcode,
        *,
        predecessors: Iterable[int] = (),
        duration_s: float = 0.0,
        layer: int | None = None,
        qubits: Iterable[int] = (),
        consumes: Mapping[str, int] | None = None,
        produces: Mapping[str, int] | None = None,
        forwards: Mapping[str, str] | None = None,
        engine_demands: Mapping[str, int] | None = None,
        required_locations: Mapping[str, str] | None = None,
        completion_locations: Mapping[str, str] | None = None,
        target_modules: Iterable[str] = (),
        target_links: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
        move_operands: MoveOperands | None = None,
        deferred_dispatch: (
            MagicRouteDispatchRecipe | ResourceMoveDispatchRecipe | None
        ) = None,
        implementation_recipes: Iterable[InjectionRecipe] = (),
    ) -> int:
        command_id = len(commands)
        commands.append(
            ArchitectureInstruction(
                id=command_id,
                opcode=opcode,
                predecessor_ids=tuple(predecessors),
                duration_s=duration_s,
                layer_index=layer,
                qubits=tuple(qubits),
                consumes=consumes or {},
                produces=produces or {},
                forwards=forwards or {},
                engines=engine_demands or {},
                required_locations=required_locations or {},
                completion_locations=completion_locations or {},
                target_modules=tuple(target_modules),
                target_links=tuple(target_links),
                metadata=metadata or {},
                move_operands=move_operands,
                deferred_dispatch=deferred_dispatch,
                implementation_recipes=tuple(implementation_recipes),
            )
        )
        return command_id

    def qloc(qubits: Iterable[int], location: str) -> dict[str, str]:
        return {f"q:{qubit}": location for qubit in qubits}

    metadata_by_unit = {
        id(unit): _compute_unit_metadata(
            circuit,
            unit,
            execution_units,
            magic_state_consumption=compilation.magic_state_consumption,
        )
        for unit in execution_units
    }
    first_resident = tuple(sorted(execution_units[0].mapping))
    initial_locations = {
        f"q:{qubit}": (
            compute_location
            if qubit in first_resident or memory is None
            else memory_location
        )
        for qubit in range(circuit.num_qubits)
    }
    previous_resident = set(first_resident)
    previous_mapping = {
        int(key): str(value)
        for key, value in execution_units[0].mapping.items()
    }
    previous_exec: int | None = None
    previous_layer: int | None = None
    previous_fence: int | None = None
    previous_reaction: int | None = None
    transfer_capacity = topology.store_load_buffer_capacity
    store_load_service_s = cost_inputs.store_load_duration_s
    store_load_receipt = (
        cost_inputs.store_load.receipt()
        if cost_inputs.store_load is not None
        else None
    )

    grouped: defaultdict[int, list[Any]] = defaultdict(list)
    for unit in execution_units:
        grouped[unit.source.layer_index].append(unit)

    ordered_layers = sorted(grouped)
    for layer in ordered_layers:
        units = grouped[layer]
        layer_tail = previous_fence
        reaction_dependency = previous_reaction
        for unit_index, unit in enumerate(units):
            unit_metadata = metadata_by_unit[id(unit)]
            resident = set(unit.mapping)
            current_mapping = dict(unit.mapping)
            partition = unit.source.partition.partition_index
            batch = unit.source.batch_index
            same_partition_as_previous = (
                previous_layer == layer
                and unit_index > 0
                and units[unit_index - 1].source.partition.partition_index
                == partition
            )
            tail = previous_exec if same_partition_as_previous else layer_tail

            if memory is not None and resident != previous_resident:
                outgoing_qubits = previous_resident - resident
                incoming_qubits = resident - previous_resident
                if len(outgoing_qubits) != len(incoming_qubits):
                    raise ValueError(
                        "Deferred residency transitions must be balanced "
                        "Store/Load exchanges: "
                        f"outgoing={len(outgoing_qubits)}, "
                        f"incoming={len(incoming_qubits)}"
                    )
                outgoing_chunks = _chunks(
                    outgoing_qubits,
                    transfer_capacity,
                )
                incoming_chunks = _chunks(
                    incoming_qubits,
                    transfer_capacity,
                )
                if len(outgoing_chunks) != len(incoming_chunks):
                    raise ValueError(
                        "Balanced residency exchange chunking produced an "
                        "inconsistent round count"
                    )
                for round_index, (outgoing, incoming) in enumerate(
                    zip(outgoing_chunks, incoming_chunks, strict=True)
                ):
                    if (
                        len(outgoing) != len(incoming)
                        or len(outgoing) > transfer_capacity
                    ):
                        raise ValueError(
                            "Each Store/Load exchange round must replace one "
                            "bounded endpoint wave"
                        )
                    outgoing_buffer_slots = {
                        qubit: topology.store_load_buffer_slots[index]
                        for index, qubit in enumerate(outgoing)
                    }
                    tail = append(
                        ArchitectureOpcode.MOVE_QUBITS,
                        predecessors=(() if tail is None else (tail,)),
                        layer=layer,
                        qubits=outgoing,
                        engine_demands={"program_move": len(outgoing)},
                        required_locations=qloc(outgoing, compute_location),
                        completion_locations=qloc(
                            outgoing, compute_buffer_location
                        ),
                        target_modules=(compute_location,),
                        move_operands=MoveOperands(
                            source_slots={
                                qubit: previous_mapping[qubit]
                                for qubit in outgoing
                            },
                            destination_slots=outgoing_buffer_slots,
                        ),
                        metadata={
                            "direction": "compute_to_logical_buffer",
                            "amount": len(outgoing),
                        },
                    )
                    if topology.memory_link is not None:
                        link_id = topology.memory_link
                        tail = append(
                            ArchitectureOpcode.TELEPORT_QUBITS,
                            predecessors=(tail,),
                            duration_s=(
                                len(outgoing)
                                * cost_inputs.logical_link_item_s
                            ),
                            layer=layer,
                            qubits=outgoing,
                            consumes={f"bell:{link_id}": len(outgoing)},
                            required_locations=qloc(
                                outgoing, compute_buffer_location
                            ),
                            completion_locations=qloc(
                                outgoing, memory_buffer_location
                            ),
                            target_modules=(
                                compute_location,
                                memory_location,
                            ),
                            target_links=(link_id,),
                            metadata={
                                "direction": "compute_to_memory",
                                "amount": len(outgoing),
                                "link_item_s": (
                                    cost_inputs.logical_link_item_s
                                ),
                                "duration_components_s": {
                                    "logical_link_service": (
                                        len(outgoing)
                                        * cost_inputs.logical_link_item_s
                                    )
                                },
                            },
                        )
                        exchange_buffer = memory_buffer_location
                    else:
                        exchange_buffer = compute_buffer_location

                    # STORE performs the outgoing syndrome service while the
                    # qubits remain staged in the endpoint.  LOAD then commits
                    # one atomic O->I replacement: the outgoing wave fills the
                    # memory slots vacated by the incoming wave, and the
                    # incoming wave takes those same endpoint slots.  Splitting
                    # that replacement into two location completions would
                    # transiently exceed the canonical memory capacity.
                    tail = append(
                        ArchitectureOpcode.STORE_QUBITS,
                        predecessors=(tail,),
                        duration_s=store_load_service_s,
                        layer=layer,
                        qubits=outgoing,
                        engine_demands={
                            "store_load_buffer": len(outgoing)
                        },
                        required_locations=qloc(outgoing, exchange_buffer),
                        target_modules=(
                            compute_location,
                            memory_location,
                        ),
                        metadata={
                            "direction": "store_prepare_exchange",
                            "amount": len(outgoing),
                            "stream_round": round_index,
                            "duration_components_s": {
                                "syndrome_service": store_load_service_s
                            },
                            "syndrome_protocol": store_load_receipt,
                        },
                    )
                    incoming_buffer_slots = {
                        qubit: topology.store_load_buffer_slots[index]
                        for index, qubit in enumerate(incoming)
                    }
                    exchange_required = {
                        **qloc(outgoing, exchange_buffer),
                        **qloc(incoming, memory_location),
                    }
                    exchange_completion = {
                        **qloc(outgoing, memory_location),
                        **qloc(incoming, exchange_buffer),
                    }
                    tail = append(
                        ArchitectureOpcode.LOAD_QUBITS,
                        predecessors=(tail,),
                        duration_s=store_load_service_s,
                        layer=layer,
                        # LOAD's operational payload is the incoming wave.
                        # The wider required/completion maps below form one
                        # atomic state exchange without double-counting the
                        # outgoing wave in fidelity or exposure analysis.
                        qubits=incoming,
                        engine_demands={
                            "store_load_buffer": len(incoming)
                        },
                        required_locations=exchange_required,
                        completion_locations=exchange_completion,
                        target_modules=(
                            memory_location,
                            compute_location,
                        ),
                        metadata={
                            "direction": "store_load_exchange",
                            "amount": len(incoming),
                            "outgoing_amount": len(outgoing),
                            "stream_round": round_index,
                            "duration_components_s": {
                                "syndrome_service": store_load_service_s
                            },
                            "syndrome_protocol": store_load_receipt,
                        },
                    )
                    if topology.memory_link is not None:
                        link_id = topology.memory_link
                        tail = append(
                            ArchitectureOpcode.TELEPORT_QUBITS,
                            predecessors=(tail,),
                            duration_s=(
                                len(incoming)
                                * cost_inputs.logical_link_item_s
                            ),
                            layer=layer,
                            qubits=incoming,
                            consumes={f"bell:{link_id}": len(incoming)},
                            required_locations=qloc(
                                incoming, memory_buffer_location
                            ),
                            completion_locations=qloc(
                                incoming, compute_buffer_location
                            ),
                            target_modules=(
                                memory_location,
                                compute_location,
                            ),
                            target_links=(link_id,),
                            metadata={
                                "direction": "memory_to_compute",
                                "amount": len(incoming),
                                "link_item_s": (
                                    cost_inputs.logical_link_item_s
                                ),
                                "duration_components_s": {
                                    "logical_link_service": (
                                        len(incoming)
                                        * cost_inputs.logical_link_item_s
                                    )
                                },
                            },
                        )
                    tail = append(
                        ArchitectureOpcode.MOVE_QUBITS,
                        predecessors=(tail,),
                        layer=layer,
                        qubits=incoming,
                        engine_demands={"program_move": len(incoming)},
                        required_locations=qloc(
                            incoming, compute_buffer_location
                        ),
                        completion_locations=qloc(
                            incoming, compute_location
                        ),
                        target_modules=(compute_location,),
                        move_operands=MoveOperands(
                            source_slots=incoming_buffer_slots,
                            destination_slots={
                                qubit: current_mapping[qubit]
                                for qubit in incoming
                            },
                        ),
                        metadata={
                            "direction": "logical_buffer_to_compute",
                            "amount": len(incoming),
                        },
                    )

            exec_predecessors = set(() if tail is None else (tail,))
            if reaction_dependency is not None:
                exec_predecessors.add(reaction_dependency)
            magic = unit.source.magic_count
            operated = tuple(unit.source.operated_qubits)
            deferred_recipe = (
                MagicRouteDispatchRecipe(
                    operation_indices=tuple(unit.source.operation_indices),
                    data_mapping=unit.mapping,
                )
                if unit.route.dispatch_deferred
                else None
            )
            source_operations = circuit.layers[layer].operations
            magic_operation_indices = tuple(
                operation_index
                for operation_index in unit.source.operation_indices
                if operation_uses_magic_state(source_operations[operation_index])
            )
            recipe_supported = magic_operation_indices and all(
                source_operations[operation_index].kind == "gate"
                and source_operations[operation_index].name.lower() == "t"
                for operation_index in magic_operation_indices
            )
            if recipe_mode_enabled and magic_operation_indices and not recipe_supported:
                unsupported = sorted(
                    {
                        source_operations[operation_index].name.lower()
                        for operation_index in magic_operation_indices
                    }
                )
                raise ValueError(
                    "Explicit runtime injection recipes currently support only "
                    f"the T convention; unsupported magic operations: {unsupported}"
                )
            implementation_recipes = (
                tuple(
                    InjectionRecipe(
                        invocation_id=(
                            f"instruction:{len(commands)}:source:{layer}:"
                            f"operation:{operation_index}"
                        ),
                        recipe_id="surface_code.t_injection.v1",
                        source_layer_index=layer,
                        source_operation_index=operation_index,
                        qubits=tuple(source_operations[operation_index].qubits),
                        stages=(
                            InjectionStage(
                                index=0,
                                resource=ResourceRef(
                                    ref_id="t_magic_state",
                                    state_kind=ResourceStateKind.T_MAGIC,
                                    buffer_id="magic_compute",
                                    token_kind="magic_state",
                                ),
                                attempt_duration_s=unit.duration_s,
                                failure_correction="s",
                            ),
                        ),
                        data_mapping=unit.mapping,
                        compute_location=compute_location,
                        compute_engine=f"compute:{compute_location}",
                        reaction_duration_s=cost_inputs.classical_reaction_s,
                        correction_duration_s=(
                            unit.route.duration.primitive_service_s
                        ),
                    )
                    for operation_index in magic_operation_indices
                )
                if recipe_mode_enabled and recipe_supported
                else ()
            )
            if deferred_recipe is not None:
                unit_metadata = {
                    key: value
                    for key, value in unit_metadata.items()
                    if key not in {"operation_indices", "mapping"}
                }
            exec_id = append(
                ArchitectureOpcode.EXECUTE_COMPUTE,
                predecessors=exec_predecessors,
                duration_s=unit.duration_s,
                layer=layer,
                qubits=operated,
                consumes=({"magic_compute": magic} if magic else {}),
                engine_demands={f"compute:{compute_location}": 1},
                required_locations=qloc(resident, compute_location),
                target_modules=(compute_location,),
                deferred_dispatch=deferred_recipe,
                implementation_recipes=implementation_recipes,
                metadata={
                    **unit_metadata,
                    "source_layer": layer,
                    "compute_partition": partition,
                    "magic_batch": batch,
                    "magic_demand": magic,
                    "resource_policy": (
                        "consume_ready_magic_states_at_dispatch"
                    ),
                },
            )
            previous_exec = exec_id
            layer_tail = exec_id
            previous_resident = resident
            previous_mapping = current_mapping
            previous_layer = layer
            reaction_dependency = None

        assert previous_exec is not None
        previous_fence = append(
            ArchitectureOpcode.FENCE,
            predecessors=(previous_exec,),
            layer=layer,
            metadata={
                "source_layer": layer,
                "scope": "dag_derived_circuit_layer",
            },
        )
        # Measurement-conditioned reaction/correction belongs to the typed
        # runtime recipe.  The former unconditional next-layer delay was a
        # coarse analytical proxy and is intentionally not emitted.
        previous_reaction = None

    compiled_commands = bind_program_move_costs(
        tuple(commands),
        specification,
        compilation.compiler_spec,
        model,
    )
    return _LoweredProgramPlan(
        ProgramDAG(compiled_commands),
        initial_locations,
    )



__all__ = ["_LoweredProgramPlan", "_lower_program_dag"]
