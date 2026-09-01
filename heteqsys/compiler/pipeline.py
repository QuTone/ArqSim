"""Compile FT circuit layers into architecture-facing compute units.

This module stops at compiler output.  It does not create resource requests or
execution events; Program-DAG construction belongs to ``evaluation``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping

from heteqsys.architecture.specification import (
    ArchitectureSpecification,
    Module,
    Node,
)
from heteqsys.architecture.isa import MagicRouteDispatchRecipe
from .interface import CompilerPipeline
from heteqsys.compiler.layout import (
    materialize_compute_layout,
    primary_qec_binding,
    primary_qec_submodule,
    single_node_module,
    single_submodule,
)
from heteqsys.compiler.mapping import map_logical_qubits
from heteqsys.compiler.models import (
    BackendSpec,
    LogicalCompilerSpec,
    LogicalRoutePlan,
)
from heteqsys.compiler.output import (
    LogicalCompilationResult,
    CompiledComputeUnit,
    CompiledRouteResult,
    CompiledRouteStep,
    ComputeBatch,
    ComputeDuration,
    ComputePartition,
    SyndromeProtocolTiming,
)
from heteqsys.compiler.routing import route_logical_circuit
from heteqsys.program import FTCircuit, LogicalLayer, LogicalOperation
from heteqsys.program.statistics import operation_uses_magic_state
from heteqsys.operation_profiles import OperationLatencyProfile
from heteqsys.schema import semantic_hash

from .allocator import allocate_residency_first_fit
from .config import canonical_compiler_spec
from .errors import LogicalCompilerValidationError


LATENCY_LOWERING_VERSION = "10"


@dataclass
class RuntimeRouteCompiler:
    """Lazily compile one joint route after runtime MS-slot binding.

    The cache is populated only after the scheduler/event engine observes an
    actual slot assignment.  It therefore avoids constraining the reachable
    scheduler design space with an eagerly materialized route table.
    """

    circuit: FTCircuit
    specification: ArchitectureSpecification
    compiler_spec: LogicalCompilerSpec
    model: OperationLatencyProfile
    cache: dict[str, Mapping[str, Any]] = field(default_factory=dict)
    compile_calls: int = 0
    cache_hits: int = 0

    def __call__(
        self,
        block: Any,
        consumed_magic_slots: tuple[str, ...],
    ) -> Mapping[str, Any]:
        recipe = getattr(block, "deferred_dispatch", None)
        if not isinstance(recipe, MagicRouteDispatchRecipe):
            raise ValueError(
                f"Block {block.id} has no typed magic-route dispatch recipe"
            )
        operation_indices = recipe.operation_indices
        active_qubits = tuple(block.qubits)
        consumes = getattr(block, "consumes", {})
        magic_count = int(sum(consumes.values()))
        if len(consumed_magic_slots) != magic_count:
            raise ValueError(
                "Runtime route compilation received a magic-slot count that "
                f"does not match block {block.id}"
            )
        mapping = dict(recipe.data_mapping)
        if not set(active_qubits) <= set(mapping):
            raise ValueError(
                f"Runtime route request {block.id} has an incomplete resident mapping"
            )

        operation_slice = ComputeBatch(
            partition=ComputePartition(
                layer_index=block.layer_index,
                partition_index=0,
                partition_count=1,
                operation_indices=operation_indices,
                active_qubits=active_qubits,
                magic_count=magic_count,
            ),
            batch_index=0,
            batch_count=1,
            operation_indices=operation_indices,
            operated_qubits=active_qubits,
            magic_count=magic_count,
        )
        local_circuit, local_to_global = _localized_circuit(
            self.circuit,
            operation_slice,
        )
        cache_key = semantic_hash(
            {
                "localized_circuit": local_circuit.semantic_hash,
                "data_mapping": {
                    str(local_qubit): mapping[global_qubit]
                    for local_qubit, global_qubit in enumerate(local_to_global)
                },
                "magic_state_slots": list(consumed_magic_slots),
                "routing": self.compiler_spec.routing.to_dict(),
                "architecture_hash": self.specification.architecture_hash,
                "latency_model_hash": self.model.binding_hash,
            }
        )
        cached = self.cache.get(cache_key)
        if cached is not None:
            self.cache_hits += 1
            return cached

        owned_compute = single_node_module(self.specification, "compute")
        assert owned_compute is not None
        compute_node, _compute = owned_compute
        layout = materialize_compute_layout(self.specification)
        placement = map_logical_qubits(
            local_circuit,
            layout,
            BackendSpec(
                "fixed_mapping",
                {
                    "assignments": {
                        str(local_qubit): mapping[global_qubit]
                        for local_qubit, global_qubit in enumerate(local_to_global)
                    }
                },
            ),
        )
        routing_spec = BackendSpec(
            self.compiler_spec.routing.backend,
            {
                **dict(self.compiler_spec.routing.options),
                "magic_state_slot_order": list(consumed_magic_slots),
            },
        )
        route_plan = route_logical_circuit(
            local_circuit,
            layout,
            placement,
            routing_spec,
            specification=self.specification,
            movement_profile=self.model.neutral_atom_movement,
        )
        route_result = _compile_route_result(
            self.specification,
            compute_node,
            route_plan,
            self.model,
        )
        binding = {
            **route_result.to_instruction_metadata(),
            "runtime_route_cache_key": cache_key,
            "runtime_route_resolution": "dispatch_time_joint_compilation",
        }
        self.cache[cache_key] = binding
        self.compile_calls += 1
        return binding


def build_runtime_route_compiler(
    circuit: FTCircuit,
    specification: ArchitectureSpecification,
    compiler_spec: LogicalCompilerSpec,
    model: OperationLatencyProfile,
) -> RuntimeRouteCompiler:
    """Return a lazy state-conditioned compiler for one evaluation scenario."""

    return RuntimeRouteCompiler(circuit, specification, compiler_spec, model)


def compiler_view_hash(
    specification: ArchitectureSpecification,
    compiler_spec: LogicalCompilerSpec,
) -> str:
    """Hash the canonical architecture authority and compiler selection."""

    return semantic_hash(
        {
            "architecture_hash": specification.architecture_hash,
            "compiler": compiler_spec.semantic_dict(),
        }
    )


def _slot_sort_key(slot_id: str) -> tuple[str, int, str]:
    match = re.match(r"^(.*?)(\d+)$", slot_id)
    if match is None:
        return slot_id, -1, slot_id
    return match.group(1), int(match.group(2)), slot_id


def _split_compute_partitions(
    circuit: FTCircuit,
    *,
    compute_capacity: int,
) -> tuple[ComputePartition, ...]:
    """Greedily partition each FT layer using only compute residency capacity."""

    partitions: list[ComputePartition] = []
    for layer in circuit.layers:
        layer_partitions: list[tuple[tuple[int, ...], tuple[int, ...], int]] = []
        current: list[int] = []
        current_qubits: set[int] = set()
        current_magic = 0

        def flush() -> None:
            nonlocal current, current_qubits, current_magic
            if not current:
                return
            layer_partitions.append(
                (tuple(current), tuple(sorted(current_qubits)), current_magic)
            )
            current = []
            current_qubits = set()
            current_magic = 0

        for operation_index, operation in enumerate(layer.operations):
            operation_qubits = set(operation.qubits)
            if len(operation_qubits) > compute_capacity:
                raise ValueError(
                    f"Layer {layer.index} operation {operation_index} has weight "
                    f"{len(operation_qubits)}, exceeding compute capacity {compute_capacity}"
                )
            operation_magic = int(operation_uses_magic_state(operation))
            if current and len(current_qubits | operation_qubits) > compute_capacity:
                flush()
            current.append(operation_index)
            current_qubits.update(operation_qubits)
            current_magic += operation_magic
        flush()
        partition_count = len(layer_partitions)
        partitions.extend(
            ComputePartition(
                layer_index=layer.index,
                partition_index=partition_index,
                partition_count=partition_count,
                operation_indices=operation_indices,
                active_qubits=active_qubits,
                magic_count=magic_count,
            )
            for partition_index, (
                operation_indices,
                active_qubits,
                magic_count,
            ) in enumerate(layer_partitions)
        )
    return tuple(partitions)


def _split_magic_batches(
    circuit: FTCircuit,
    partitions: Iterable[ComputePartition],
    *,
    magic_capacity: int,
    consumption_policy: str,
) -> tuple[ComputeBatch, ...]:
    """Split execution inside a fixed residency partition by MS batch size.

    This second split never changes the resident-qubit set and therefore must
    never induce memory Store/Load traffic.
    """

    max_magic = 1 if consumption_policy == "incremental" else magic_capacity
    batches: list[ComputeBatch] = []
    for partition in partitions:
        operations = circuit.layers[partition.layer_index].operations
        raw_batches: list[tuple[int, ...]] = []
        current: list[int] = []
        current_magic = 0
        for operation_index in partition.operation_indices:
            operation_magic = int(
                operation_uses_magic_state(operations[operation_index])
            )
            if current and current_magic + operation_magic > max_magic:
                raw_batches.append(tuple(current))
                current = []
                current_magic = 0
            current.append(operation_index)
            current_magic += operation_magic
        if current:
            raw_batches.append(tuple(current))

        batch_count = len(raw_batches)
        for batch_index, operation_indices in enumerate(raw_batches):
            operated = sorted(
                {
                    qubit
                    for operation_index in operation_indices
                    for qubit in operations[operation_index].qubits
                }
            )
            batches.append(
                ComputeBatch(
                    partition=partition,
                    batch_index=batch_index,
                    batch_count=batch_count,
                    operation_indices=operation_indices,
                    operated_qubits=tuple(operated),
                    magic_count=sum(
                        int(operation_uses_magic_state(operations[index]))
                        for index in operation_indices
                    ),
                )
            )
    return tuple(batches)


def _localized_instruction(
    operation: LogicalOperation,
    global_to_local: Mapping[int, int],
) -> LogicalOperation:
    pauli = None
    if operation.pauli is not None:
        sign = operation.pauli[0]
        body = operation.pauli[1:]
        local_to_global = [
            global_qubit
            for global_qubit, _ in sorted(
                global_to_local.items(),
                key=lambda item: item[1],
            )
        ]
        pauli = sign + "".join(body[global_qubit] for global_qubit in local_to_global)
    return LogicalOperation(
        kind=operation.kind,
        name=operation.name,
        qubits=tuple(global_to_local[qubit] for qubit in operation.qubits),
        classical_bits=operation.classical_bits,
        parameters=operation.parameters,
        pauli=pauli,
    )


def _localized_circuit(
    circuit: FTCircuit,
    operation_slice: ComputeBatch | ComputePartition,
) -> tuple[FTCircuit, tuple[int, ...]]:
    local_to_global = operation_slice.active_qubits
    global_to_local = {
        global_qubit: local_qubit
        for local_qubit, global_qubit in enumerate(local_to_global)
    }
    operations = circuit.layers[operation_slice.layer_index].operations
    localized = tuple(
        _localized_instruction(operations[index], global_to_local)
        for index in operation_slice.operation_indices
    )
    return (
        FTCircuit(
            representation=circuit.representation,
            num_qubits=len(local_to_global),
            num_clbits=circuit.num_clbits,
            layers=(LogicalLayer(0, localized),),
            provenance={
                "source_circuit_hash": circuit.semantic_hash,
                "source_layer": operation_slice.layer_index,
                "source_operations": list(operation_slice.operation_indices),
            },
        ),
        local_to_global,
    )


def _resolve_syndrome_protocol_timing(
    specification: ArchitectureSpecification,
    model: OperationLatencyProfile,
    protocol_id: str,
) -> SyndromeProtocolTiming:
    binding = model.syndrome_profile(protocol_id)
    source_module: Module | None = None
    source_code: str | None = None
    source_distance: int | None = None
    if binding.round_mode == "fixed":
        assert binding.rounds is not None
        rounds = binding.rounds
    else:
        assert binding.distance_module_role is not None
        owned_source = single_node_module(
            specification,
            binding.distance_module_role,
            required=True,
        )
        assert owned_source is not None
        _source_node, source_module = owned_source
        qec_binding = primary_qec_binding(source_module)
        raw_distance = qec_binding.parameters.get("distance")
        if (
            isinstance(raw_distance, bool)
            or not isinstance(raw_distance, int)
            or raw_distance <= 0
        ):
            raise ValueError(
                f"Syndrome protocol {protocol_id} requires a positive integer "
                f"QEC distance on the {source_module.type} Module "
                f"{source_module.id}"
            )
        rounds = raw_distance
        source_code = qec_binding.code
        source_distance = raw_distance

    return SyndromeProtocolTiming(
        id=protocol_id,
        round_mode=binding.round_mode,
        rounds=rounds,
        cycle_time_s=binding.cycle_time_s,
        distance_module_role=(
            binding.distance_module_role if source_module is not None else None
        ),
        distance_module=(source_module.id if source_module is not None else None),
        distance_code=source_code,
        distance=source_distance,
        provenance=binding.provenance,
    )


def _route_duration_components_s(
    specification: ArchitectureSpecification,
    compute_node: Node,
    route_plan: LogicalRoutePlan,
    model: OperationLatencyProfile,
) -> tuple[ComputeDuration, SyndromeProtocolTiming]:
    gate = float(model.gate_duration_s.get(compute_node.modality, 0.0))
    syndrome_protocol = _resolve_syndrome_protocol_timing(
        specification,
        model,
        model.compute_protocol(compute_node.modality),
    )
    syndrome_service = syndrome_protocol.service_s
    if compute_node.modality == "neutral_atom":
        movement_us = float(route_plan.metrics.get("aod_pipeline_duration_us", 0.0))
        primitive = gate + syndrome_service
        compiler_routing = movement_us * 1e-6
    else:
        groups = max(1, len(route_plan.steps))
        primitive = max(gate, syndrome_service)
        realized = max(gate, groups * syndrome_service)
        compiler_routing = max(0.0, realized - primitive)
    return ComputeDuration(primitive, compiler_routing), syndrome_protocol


def _compile_route_result(
    specification: ArchitectureSpecification,
    compute_node: Node,
    route_plan: LogicalRoutePlan,
    model: OperationLatencyProfile,
) -> CompiledRouteResult:
    duration_components, syndrome_protocol = _route_duration_components_s(
        specification,
        compute_node,
        route_plan,
        model,
    )
    return CompiledRouteResult(
        route_hash=route_plan.route_hash,
        dispatch_deferred=False,
        metrics=route_plan.metrics,
        steps=tuple(
            CompiledRouteStep(
                kind=step.kind,
                group=step.group,
                operation_count=len(step.operation_indices),
                terminal_slots=step.terminal_slots,
                path_node_count=len(step.path_nodes),
                path_edge_count=len(step.path_edges),
                movement_count=len(step.movements),
                metadata=step.metadata,
            )
            for step in route_plan.steps
        ),
        duration=duration_components,
        syndrome_protocol=syndrome_protocol,
    )


def compile_compute_units(
    circuit: FTCircuit,
    compiler_spec: LogicalCompilerSpec,
    source_slots: Mapping[int, str],
    specification: ArchitectureSpecification,
    batches: Iterable[ComputeBatch],
    model: OperationLatencyProfile,
    *,
    defer_magic_routing: bool = False,
) -> tuple[CompiledComputeUnit, ...]:
    owned_compute = single_node_module(specification, "compute")
    assert owned_compute is not None
    compute_node, _compute = owned_compute
    layout = materialize_compute_layout(specification)
    units: list[CompiledComputeUnit] = []
    route_cache: dict[
        str,
        tuple[
            Mapping[int, str],
            CompiledRouteResult,
        ],
    ] = {}
    partition_mappings: dict[tuple[int, int], Mapping[int, str]] = {}
    previous_partition_mapping: Mapping[int, str] = {}
    ordered_data_slots = tuple(
        slot.id
        for slot in sorted(
            layout.slots_of_kind("data"),
            key=lambda slot: (slot.coordinate[1], slot.coordinate[0], slot.id),
        )
    )
    for execution_batch in batches:
        partition = execution_batch.partition
        partition_key = (partition.layer_index, partition.partition_index)
        mapping = partition_mappings.get(partition_key)
        if mapping is None:
            _partition_circuit, partition_local_to_global = _localized_circuit(
                circuit,
                partition,
            )
            if source_slots:
                missing_source_slots = sorted(
                    set(range(circuit.num_qubits)) - set(source_slots)
                )
                if missing_source_slots:
                    raise LogicalCompilerValidationError(
                        "Initial placement must cover every non-deferred "
                        "circuit qubit",
                        details={"logical_qubits": missing_source_slots},
                    )
                mapping = {
                    qubit: source_slots[qubit]
                    for qubit in range(circuit.num_qubits)
                }
            else:
                active_qubits = tuple(sorted(partition_local_to_global))
                active_qubit_set = frozenset(active_qubits)
                resident_order = (
                    active_qubits
                    + tuple(
                        qubit
                        for qubit in sorted(previous_partition_mapping)
                        if qubit not in active_qubit_set
                    )
                    + tuple(
                        qubit
                        for qubit in range(circuit.num_qubits)
                        if qubit not in active_qubit_set
                        and qubit not in previous_partition_mapping
                    )
                )
                resident_qubits = resident_order[
                    : min(len(ordered_data_slots), circuit.num_qubits)
                ]
                mapping = allocate_residency_first_fit(
                    resident_qubits,
                    ordered_data_slots,
                    previous_partition_mapping,
                )
            partition_mappings[partition_key] = mapping
            previous_partition_mapping = mapping

        local_circuit, local_to_global = _localized_circuit(circuit, execution_batch)
        if not set(local_to_global) <= set(mapping):
            raise ValueError(
                "Execution batch operates on a qubit outside its compute partition"
            )
        mapping_spec = BackendSpec(
            "fixed_mapping",
            {
                "assignments": {
                    str(local_qubit): mapping[global_qubit]
                    for local_qubit, global_qubit in enumerate(local_to_global)
                }
            },
        )
        if execution_batch.magic_count and defer_magic_routing:
            syndrome_protocol = _resolve_syndrome_protocol_timing(
                specification,
                model,
                model.compute_protocol(compute_node.modality),
            )
            gate = float(model.gate_duration_s.get(compute_node.modality, 0.0))
            syndrome_service = syndrome_protocol.service_s
            primitive_service = (
                gate + syndrome_service
                if compute_node.modality == "neutral_atom"
                else max(gate, syndrome_service)
            )
            units.append(
                CompiledComputeUnit(
                    source=execution_batch,
                    mapping=mapping,
                    route=CompiledRouteResult(
                        route_hash=semantic_hash(
                            {
                                "dispatch_deferred": True,
                                "circuit": local_circuit.semantic_hash,
                                "mapping": mapping_spec.to_dict(),
                                "routing": compiler_spec.routing.to_dict(),
                            }
                        ),
                        dispatch_deferred=True,
                        metrics={},
                        steps=(),
                        duration=ComputeDuration(primitive_service, 0.0),
                        syndrome_protocol=syndrome_protocol,
                    ),
                )
            )
            continue
        cache_key = semantic_hash(
            {
                "circuit": local_circuit.semantic_hash,
                "mapping": mapping_spec.to_dict(),
                "routing": compiler_spec.routing.to_dict(),
                "magic_count": execution_batch.magic_count,
            }
        )
        cached = route_cache.get(cache_key)
        if cached is not None:
            _local_mapping, route_result = cached
        else:
            placement = map_logical_qubits(local_circuit, layout, mapping_spec)
            route_plan = route_logical_circuit(
                local_circuit,
                layout,
                placement,
                compiler_spec.routing,
                specification=specification,
                movement_profile=model.neutral_atom_movement,
            )
            route_result = _compile_route_result(
                specification,
                compute_node,
                route_plan,
                model,
            )
            local_mapping = {
                entry.logical_qubit: entry.slot_id for entry in placement.entries
            }
            route_cache[cache_key] = (
                local_mapping,
                route_result,
            )
        units.append(
            CompiledComputeUnit(
                source=execution_batch,
                mapping=mapping,
                route=route_result,
            )
        )
    return tuple(units)


def compile_ft_circuit(
    circuit: FTCircuit,
    specification: ArchitectureSpecification,
    model: OperationLatencyProfile,
    *,
    compiler_spec: LogicalCompilerSpec | None = None,
    magic_state_consumption: str = "bulk_wave",
    defer_magic_routing: bool = True,
) -> LogicalCompilationResult:
    """Compile source layers without creating any evaluation-plan objects."""

    canonical = canonical_compiler_spec(specification)
    resolved = compiler_spec or canonical
    owned_compute = single_node_module(specification, "compute")
    memory = single_node_module(specification, "memory", required=False)
    assert owned_compute is not None
    _compute_node, compute = owned_compute
    capacity = primary_qec_submodule(compute).capacity
    deferred = circuit.num_qubits > capacity
    if deferred and resolved.mapping != canonical.mapping:
        raise LogicalCompilerValidationError(
            "Capacity-deferred compilation uses the fixed "
            "preserve-resident first-fit policy; a noncanonical logical "
            "mapping override would be ignored",
            details={
                "requested_mapping": resolved.mapping.to_dict(),
                "canonical_mapping": canonical.mapping.to_dict(),
                "residency_policy": "preserve_resident_first_fit.v1",
                "logical_qubits": circuit.num_qubits,
                "compute_capacity": capacity,
            },
        )
    if deferred and memory is None:
        raise ValueError(
            f"Circuit has {circuit.num_qubits} logical qubits but compute capacity is {capacity}"
        )
    for layer in circuit.layers:
        for operation_index, operation in enumerate(layer.operations):
            if len(set(operation.qubits)) > capacity:
                raise ValueError(
                    f"Layer {layer.index} operation {operation_index} cannot fit in "
                    f"compute capacity {capacity}"
                )

    initial_mapping: dict[int, str] = {}
    if not deferred:
        layout = materialize_compute_layout(specification)
        placement = map_logical_qubits(circuit, layout, resolved.mapping)
        initial_mapping = {
            entry.logical_qubit: entry.slot_id for entry in placement.entries
        }
    partitions = _split_compute_partitions(circuit, compute_capacity=capacity)
    magic_input = single_submodule(
        compute,
        submodule_type="buffer",
        payload="magic_state",
    )
    assert magic_input is not None
    magic_capacity = max(1, magic_input.capacity)
    batches = _split_magic_batches(
        circuit,
        partitions,
        magic_capacity=magic_capacity,
        consumption_policy=magic_state_consumption,
    )
    units = compile_compute_units(
        circuit,
        resolved,
        initial_mapping,
        specification,
        batches,
        model,
        defer_magic_routing=defer_magic_routing,
    )
    return LogicalCompilationResult(
        circuit_hash=circuit.semantic_hash,
        architecture_hash=specification.architecture_hash,
        latency_profile_hash=model.binding_hash,
        compiler_spec=resolved,
        magic_state_consumption=magic_state_consumption,
        compute_units=units,
        initial_mapping=initial_mapping,
        deferred_capacity_mapping=deferred,
    )


@dataclass(frozen=True)
class DefaultCompilerPipeline(CompilerPipeline):
    """Default replaceable compiler assembled from ArqSim's native stages."""

    compiler_spec: LogicalCompilerSpec | None = None
    magic_state_consumption: str = "bulk_wave"
    defer_magic_routing: bool = True

    def compile(
        self,
        circuit: FTCircuit,
        specification: ArchitectureSpecification,
        latency_profile: OperationLatencyProfile,
    ) -> LogicalCompilationResult:
        return compile_ft_circuit(
            circuit,
            specification,
            latency_profile,
            compiler_spec=self.compiler_spec,
            magic_state_consumption=self.magic_state_consumption,
            defer_magic_routing=self.defer_magic_routing,
        )
