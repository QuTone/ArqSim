"""Build Program and Resource DAGs from compiler and architecture contracts."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from heteqsys.architecture.specification import (
    ArchitectureSpecification,
    Module,
    Node,
    Submodule,
)
from heteqsys.architecture.isa import (
    ArchitectureOpcode,
    MagicRouteDispatchRecipe,
    ResourceMoveDispatchRecipe,
)
from heteqsys.compiler.layout import (
    absolute_module_id,
    absolute_slot_id,
    absolute_submodule_id,
    materialize_compute_layout,
    primary_qec_binding,
    primary_qec_submodule,
    single_node_module,
    single_submodule,
)
from heteqsys.compiler.models import LogicalCompilerSpec
from heteqsys.compiler.errors import LogicalCompilerValidationError
from heteqsys.compiler.output import (
    LogicalCompilationResult,
    validate_compilation_coverage,
)
from heteqsys.compiler.movement import compile_resource_move
from heteqsys.compiler.pipeline import (
    DefaultCompilerPipeline,
    build_runtime_route_compiler,
)
from heteqsys.compiler.interface import CompilerPipeline
from heteqsys.operation_profiles import (
    ArrivalDistribution,
    OperationLatencyProfile,
    ResolvedResourceProtocolBindings,
)
from heteqsys.program import FTCircuit
from heteqsys.qec.protocol import (
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
)

from .plan import BufferSpec, EngineSpec, ExecutionPlan
from .components import (
    DeferredDispatchRequest,
    default_runtime_component_manifest,
)
from ._plan_inputs import (
    PlanCostInputs as _PlanCostInputs,
    RuntimeTopology as _RuntimeTopology,
    SyndromeCostInput as _SyndromeCostInput,
)
from ._program_lowering import _lower_program_dag
from .policy import EvaluationPolicy
from .program_dag import ProgramDAG
from .resource_dag import ResourceDAG, ResourceProcess


PLAN_BUILDER_VERSION = "arqsim-plan-builder-v4"


def build_runtime_instruction_compiler(
    circuit: FTCircuit,
    specification: ArchitectureSpecification,
    compiler_spec: LogicalCompilerSpec,
    model: OperationLatencyProfile,
) -> Callable[[DeferredDispatchRequest], Mapping[str, Any]]:
    """Compile position-dependent routes only after buffer-state binding."""

    route_compiler = build_runtime_route_compiler(
        circuit,
        specification,
        compiler_spec,
        model,
    )

    def compile_at_dispatch(
        request: DeferredDispatchRequest,
    ) -> Mapping[str, Any]:
        if not isinstance(request.recipe, MagicRouteDispatchRecipe):
            raise ValueError("Program compiler requires a magic-route recipe")
        binding = dict(
            route_compiler(
                request.operation,
                tuple(
                    slot
                    for buffer_id in sorted(request.operation.consumes)
                    for slot in request.consumed_slots.get(buffer_id, ())
                ),
            )
        )
        return binding

    setattr(
        compile_at_dispatch,
        "__arqsim_builtin_component_id__",
        "runtime_compiler.state_bound_local.v1",
    )
    setattr(
        compile_at_dispatch,
        "__arqsim_source_context__",
        MappingProxyType(
            {
                "circuit_hash": circuit.semantic_hash,
                "architecture_hash": specification.architecture_hash,
                "compiler_spec_hash": compiler_spec.compiler_hash,
                "latency_profile_hash": model.profile_hash,
            }
        ),
    )
    return compile_at_dispatch


def build_runtime_resource_compiler(
    specification: ArchitectureSpecification,
    compiler_spec: LogicalCompilerSpec,
    model: OperationLatencyProfile,
) -> Callable[
    [DeferredDispatchRequest],
    Mapping[str, Any],
]:
    """Compile state-bound Resource-plane movement batches at dispatch."""

    move_binding_cache: dict[tuple[Any, ...], Mapping[str, Any]] = {}

    def compile_at_dispatch(
        request: DeferredDispatchRequest,
    ) -> Mapping[str, Any]:
        if not isinstance(request.recipe, ResourceMoveDispatchRecipe):
            raise ValueError("Resource resolver requires a resource-move recipe")
        return compile_resource_move(
            request.operation,
            request.consumed_tokens,
            request.consumed_slots,
            request.produced_slots,
            specification,
            compiler_spec,
            model,
            binding_cache=move_binding_cache,
        )

    setattr(
        compile_at_dispatch,
        "__arqsim_builtin_component_id__",
        "resource_resolver.state_bound.v1",
    )
    setattr(
        compile_at_dispatch,
        "__arqsim_source_context__",
        MappingProxyType(
            {
                "architecture_hash": specification.architecture_hash,
                "compiler_spec_hash": compiler_spec.compiler_hash,
                "latency_profile_hash": model.profile_hash,
            }
        ),
    )
    return compile_at_dispatch


def _module_location(entry: tuple[Node, Module]) -> str:
    return absolute_module_id(entry[0].id, entry[1].id)


def _submodule_location(entry: tuple[Node, Module], submodule: Submodule) -> str:
    return absolute_submodule_id(entry[0].id, entry[1].id, submodule.id)


def _connection_between(
    specification: ArchitectureSpecification,
    left: tuple[Node, Module] | None,
    right: tuple[Node, Module] | None,
) -> tuple[str, str] | None:
    if left is None or right is None:
        return None
    left_node, left_module = left
    right_node, right_module = right
    if left_node.id == right_node.id:
        for connection in left_node.connections:
            endpoint_modules = {
                endpoint.split("/", 1)[0] for endpoint in connection.endpoints
            }
            if {left_module.id, right_module.id} <= endpoint_modules:
                return "local_connection", f"{left_node.id}/{connection.id}"
        return None
    targets = {
        (left_node.id, left_module.id),
        (right_node.id, right_module.id),
    }
    for interconnect in specification.interconnects:
        endpoint_modules = {
            tuple(endpoint.split("/")[:2]) for endpoint in interconnect.endpoints
        }
        if targets <= endpoint_modules:
            return "interconnect", interconnect.id
    return None


def _canonical_slots(
    owner: Node,
    module: Module,
    submodule: Submodule,
) -> tuple[str, ...]:
    return tuple(
        absolute_slot_id(owner.id, module.id, submodule.id, slot.id)
        for slot in submodule.slots
    )


def _bell_topology(
    specification: ArchitectureSpecification,
) -> tuple[dict[str, int], dict[str, dict[str, Any]]]:
    capacities: dict[str, int] = {}
    profiles: dict[str, dict[str, Any]] = {}
    for interconnect in specification.interconnects:
        engines = tuple(
            submodule
            for module in interconnect.modules
            for submodule in module.submodules
            if submodule.type == "engine" and submodule.payload == "bell_pair"
        )
        buffers = tuple(
            submodule
            for module in interconnect.modules
            for submodule in module.submodules
            if submodule.type == "buffer" and submodule.payload == "bell_pair"
        )
        if len(engines) != 1 or len(buffers) != 1:
            raise ValueError(
                f"Interconnect {interconnect.id} needs one Bell engine and one buffer"
            )
        engine = engines[0]
        protocol_ref = engine.resource_protocol
        if protocol_ref is None:
            raise ValueError(
                f"Interconnect {interconnect.id} Bell engine needs a protocol"
            )
        profile = get_entanglement_distillation_profile(protocol_ref.id)
        if protocol_ref.profile_hash != profile.profile_hash:
            raise ValueError(
                f"Interconnect {interconnect.id} Bell protocol hash mismatch"
            )
        capacities[interconnect.id] = max(1, buffers[0].capacity)
        profiles[interconnect.id] = {
            "protocol": profile.id,
            "copies": engine.capacity,
            "outputs_per_copy_per_batch": profile.outputs_per_batch,
            "logical_qubits_per_copy_per_endpoint": (
                profile.logical_qubits_per_copy_per_endpoint
            ),
            "physical_qubits_per_copy_per_endpoint": (
                profile.physical_qubits_per_copy_per_endpoint
            ),
        }
    return capacities, profiles


def _runtime_topology(
    specification: ArchitectureSpecification,
) -> _RuntimeTopology:
    compute_entry = single_node_module(specification, "compute")
    memory_entry = single_node_module(
        specification, "memory", required=False
    )
    msf_entry = single_node_module(
        specification, "resource_factory", required=False
    )
    assert compute_entry is not None
    compute_node, compute = compute_entry
    layout = materialize_compute_layout(specification)
    magic_slots = tuple(
        slot.id
        for slot in sorted(
            layout.slots_of_kind("magic_state"),
            key=lambda item: (item.coordinate[1], item.coordinate[0], item.id),
        )
    )
    memory_connection = _connection_between(
        specification, memory_entry, compute_entry
    )
    magic_connection = _connection_between(
        specification, msf_entry, compute_entry
    )
    magic_input = single_submodule(
        compute,
        submodule_type="buffer",
        payload="magic_state",
    )
    compute_store_load = single_submodule(
        compute,
        submodule_type="buffer",
        payload="logical_qubit",
        required=memory_entry is not None,
    )
    memory_store_load = (
        single_submodule(
            memory_entry[1],
            submodule_type="buffer",
            payload="logical_qubit",
            required=False,
        )
        if memory_entry is not None
        else None
    )
    msf_output = (
        single_submodule(
            msf_entry[1],
            submodule_type="buffer",
            payload="magic_state",
        )
        if msf_entry is not None
        else None
    )
    factory_engine = (
        single_submodule(
            msf_entry[1],
            submodule_type="engine",
            payload="magic_state",
        )
        if msf_entry is not None
        else None
    )
    magic_input_location = _submodule_location(compute_entry, magic_input)
    msf_location = _module_location(msf_entry) if msf_entry is not None else None
    magic_output_location = (
        _submodule_location(msf_entry, msf_output)
        if msf_entry is not None and msf_output is not None
        else None
    )
    factory_engine_location = (
        _submodule_location(msf_entry, factory_engine)
        if msf_entry is not None and factory_engine is not None
        else None
    )
    if factory_engine is not None:
        protocol_ref = factory_engine.resource_protocol
        if protocol_ref is None:
            raise ValueError("Magic-state factory engine needs a protocol")
        magic_profile = get_magic_state_factory_profile(protocol_ref.id)
        if protocol_ref.profile_hash != magic_profile.profile_hash:
            raise ValueError("Magic-state factory protocol hash mismatch")
    else:
        magic_profile = None
    bell_capacities, bell_profiles = _bell_topology(specification)
    transfer_capacities = tuple(
        submodule.capacity
        for submodule in (compute_store_load, memory_store_load)
        if submodule is not None
    )
    store_load_capacity = min(transfer_capacities, default=1)
    return _RuntimeTopology(
        compute_modality=compute_node.modality,
        compute_location=_module_location(compute_entry),
        memory_location=(
            _module_location(memory_entry) if memory_entry is not None else None
        ),
        compute_buffer_location=(
            _submodule_location(compute_entry, compute_store_load)
            if compute_store_load is not None
            else None
        ),
        memory_buffer_location=(
            _submodule_location(memory_entry, memory_store_load)
            if memory_entry is not None and memory_store_load is not None
            else None
        ),
        magic_input_location=magic_input_location,
        msf_location=msf_location,
        magic_output_location=magic_output_location,
        factory_engine_location=factory_engine_location,
        store_load_buffer_capacity=max(1, store_load_capacity),
        store_load_buffer_slots=(
            _canonical_slots(compute_node, compute, compute_store_load)
            if compute_store_load is not None
            else ()
        ),
        magic_buffer_capacity=max(1, magic_input.capacity),
        magic_buffer_slots=magic_slots,
        msf_output_buffer_capacity=(
            msf_output.capacity if msf_output is not None else 0
        ),
        msf_output_buffer_slots=(
            _canonical_slots(msf_entry[0], msf_entry[1], msf_output)
            if msf_entry is not None and msf_output is not None
            else ()
        ),
        remote_magic_link=(
            magic_connection[1]
            if magic_connection is not None
            and magic_connection[0] == "interconnect"
            else None
        ),
        memory_link=(
            memory_connection[1]
            if memory_connection is not None
            and memory_connection[0] == "interconnect"
            else None
        ),
        bell_buffer_capacities=bell_capacities,
        bell_engine_profiles=bell_profiles,
        magic_outputs_per_copy_per_batch=(
            magic_profile.outputs_per_batch if magic_profile is not None else 1
        ),
        magic_protocol=(
            magic_profile.id
            if magic_profile is not None
            else "configured_magic_state_protocol"
        ),
    )



def _resolve_store_load_cost(
    specification: ArchitectureSpecification,
    model: OperationLatencyProfile,
) -> _SyndromeCostInput | None:
    memory_entry = single_node_module(
        specification, "memory", required=False
    )
    if memory_entry is None:
        return None
    profile = model.syndrome_profile(model.store_load_protocol)
    if profile.round_mode == "fixed":
        assert profile.rounds is not None
        rounds = profile.rounds
    else:
        assert profile.distance_module_role is not None
        source = single_node_module(
            specification,
            profile.distance_module_role,
            required=True,
        )
        assert source is not None
        rounds = int(primary_qec_binding(source[1]).parameters["distance"])
    return _SyndromeCostInput(
        protocol=model.store_load_protocol,
        rounds=rounds,
        cycle_time_s=profile.cycle_time_s,
        provenance=profile.provenance,
    )


def _resolve_plan_cost_inputs(
    specification: ArchitectureSpecification,
    topology: _RuntimeTopology,
    model: OperationLatencyProfile,
) -> _PlanCostInputs:
    return _PlanCostInputs(
        store_load=_resolve_store_load_cost(specification, model),
        logical_link_item_s=model.link_item_s,
        local_magic_delivery_s=model.local_magic_delivery_s,
        classical_reaction_s=model.reaction_latency(topology.compute_modality),
    )


def _engine_copies(engine: Submodule | None) -> int:
    if engine is None:
        return 0
    return max(1, engine.capacity)


def _available_delivery_concurrency(topology: _RuntimeTopology) -> int:
    """Return the concurrency exposed by active interface-buffer slots.

    Each logical Bell-buffer slot is modeled as one active teleportation slot.
    Magic-state input and MSF-output capacities can still reduce the usable
    wave width.  Actual concurrency is further limited at runtime by ready
    tokens; there is no separate physical ``delivery channel`` primitive.
    """

    capacities = [
        topology.magic_buffer_capacity,
        topology.msf_output_buffer_capacity,
    ]
    if topology.remote_magic_link is not None:
        capacities.append(
            min(topology.bell_buffer_capacities.values(), default=1)
        )
    return max(1, min(capacities))


@dataclass(frozen=True)
class _LoweredArchitectureState:
    """Buffers and engines exposed to one execution plan."""

    buffers: tuple[BufferSpec, ...]
    engines: tuple[EngineSpec, ...]


def _lower_architectural_state(
    specification: ArchitectureSpecification,
    topology: _RuntimeTopology,
    *,
    resource_delivery_channels: int,
) -> _LoweredArchitectureState:
    """Lower canonical capacities into runtime buffer and engine inventories."""

    compute_entry = single_node_module(specification, "compute")
    memory_entry = single_node_module(
        specification, "memory", required=False
    )
    msf_entry = single_node_module(
        specification, "resource_factory", required=False
    )
    assert compute_entry is not None
    compute = compute_entry[1]
    memory = memory_entry[1] if memory_entry is not None else None
    msf = msf_entry[1] if msf_entry is not None else None
    compute_region = single_submodule(
        compute,
        submodule_type="region",
        payload="logical_qubit",
    )
    magic_input = single_submodule(
        compute,
        submodule_type="buffer",
        payload="magic_state",
    )
    magic_output = (
        single_submodule(
            msf,
            submodule_type="buffer",
            payload="magic_state",
        )
        if msf is not None
        else None
    )
    factory_engine = (
        single_submodule(
            msf,
            submodule_type="engine",
            payload="magic_state",
        )
        if msf is not None
        else None
    )

    buffers = [
        BufferSpec(
            "magic_compute",
            topology.magic_buffer_capacity,
            "magic_state",
            module=topology.compute_location,
            submodule=topology.magic_input_location,
            slots=topology.magic_buffer_slots,
        )
    ]
    if msf is not None:
        buffers.append(
            BufferSpec(
                "msf_output",
                max(1, topology.msf_output_buffer_capacity),
                "magic_state",
                module=topology.msf_location,
                submodule=topology.magic_output_location,
                slots=topology.msf_output_buffer_slots,
            )
        )
    for interconnect in specification.interconnects:
        bell_buffers = tuple(
            (module, submodule)
            for module in interconnect.modules
            for submodule in module.submodules
            if submodule.type == "buffer" and submodule.payload == "bell_pair"
        )
        if len(bell_buffers) != 1:
            raise ValueError(
                f"Interconnect {interconnect.id} needs one Bell buffer"
            )
        module, submodule = bell_buffers[0]
        capacity = topology.bell_buffer_capacities[interconnect.id]
        buffers.append(
            BufferSpec(
                f"bell:{interconnect.id}",
                capacity,
                "logical_bell_pair",
                module=absolute_module_id(interconnect.id, module.id),
                submodule=absolute_submodule_id(
                    interconnect.id,
                    module.id,
                    submodule.id,
                ),
                slots=tuple(
                    absolute_slot_id(
                        interconnect.id,
                        module.id,
                        submodule.id,
                        slot.id,
                    )
                    for slot in submodule.slots
                ),
            )
        )

    engines = [
        EngineSpec(
            f"compute:{topology.compute_location}",
            1,
            module=topology.compute_location,
            submodule=_submodule_location(compute_entry, compute_region),
        )
    ]
    engines.extend(
        EngineSpec(
            f"bell_engine:{link_id}",
            int(profile["copies"]),
        )
        for link_id, profile in topology.bell_engine_profiles.items()
    )
    if memory is not None:
        engines.extend(
            (
                EngineSpec(
                    "program_move",
                    topology.store_load_buffer_capacity,
                    module=topology.compute_location,
                ),
                EngineSpec(
                    "store_load_buffer",
                    topology.store_load_buffer_capacity,
                    module=topology.compute_location,
                ),
            )
        )
    if msf is not None:
        engines.extend(
            (
                EngineSpec(
                    f"msf:{topology.msf_location}",
                    _engine_copies(factory_engine),
                    module=topology.msf_location,
                    submodule=topology.factory_engine_location,
                ),
                # Program and resource moves intentionally use separate domains.
                EngineSpec(
                    "resource_move",
                    resource_delivery_channels,
                    module=topology.msf_location,
                ),
            )
        )
    return _LoweredArchitectureState(tuple(buffers), tuple(engines))


@dataclass(frozen=True)
class _LoweredResourcePlan:
    """Recurrent resource DAG plus its optional local movement source."""

    dag: ResourceDAG
    local_move_source: Mapping[str, str] | None


def _lower_resource_dag(
    specification: ArchitectureSpecification,
    topology: _RuntimeTopology,
    cost_inputs: _PlanCostInputs,
    *,
    resource_delivery_channels: int,
    magic_arrival: ArrivalDistribution | None,
    bell_arrival: ArrivalDistribution | None,
    resource_protocol_bindings: ResolvedResourceProtocolBindings | None,
) -> _LoweredResourcePlan:
    """Lower installed factories and links into recurrent Resource work."""

    compute_entry = single_node_module(specification, "compute")
    msf_entry = single_node_module(
        specification, "resource_factory", required=False
    )
    assert compute_entry is not None
    msf = msf_entry[1] if msf_entry is not None else None
    magic_binding = (
        resource_protocol_bindings.magic_state
        if resource_protocol_bindings is not None
        else None
    )
    bell_binding = (
        resource_protocol_bindings.logical_bell_pair
        if resource_protocol_bindings is not None
        else None
    )
    factory_engine = (
        single_submodule(
            msf,
            submodule_type="engine",
            payload="magic_state",
        )
        if msf is not None
        else None
    )
    msf_engine_lanes = _engine_copies(factory_engine)
    processes: list[ResourceProcess] = []

    if msf is not None:
        if magic_arrival is None:
            raise ValueError(
                "Magic-state production needs a resolved arrival distribution"
            )
        if magic_binding is not None and (
            magic_binding.protocol_id != topology.magic_protocol
            or magic_binding.copies != msf_engine_lanes
            or magic_binding.outputs_per_copy_per_batch
            != topology.magic_outputs_per_copy_per_batch
        ):
            raise ValueError(
                "Resolved magic-state binding disagrees with runtime topology"
            )
        processes.append(
            ResourceProcess(
                id="prepare_magic",
                opcode=ArchitectureOpcode.PREPARE_MAGIC_STATE,
                produces={
                    "msf_output": topology.magic_outputs_per_copy_per_batch
                },
                engines={f"msf:{topology.msf_location}": 1},
                arrival_distribution=magic_arrival,
                parallelism=msf_engine_lanes,
                output_overflow_policy="discard_excess",
                protocol=topology.magic_protocol,
                target_modules=(topology.msf_location,),
                metadata={
                    "outputs_per_batch": (
                        topology.magic_outputs_per_copy_per_batch
                    )
                },
            )
        )
        if topology.remote_magic_link is None:
            processes.append(
                ResourceProcess(
                    id="deliver_magic_local",
                    opcode=ArchitectureOpcode.MOVE_QUBITS,
                    consumes={"msf_output": 1},
                    produces={"magic_compute": 1},
                    forwards={"msf_output": "magic_compute"},
                    engines={"resource_move": 1},
                    duration_s=cost_inputs.local_magic_delivery_s,
                    dispatch_policy="eager_available",
                    protocol="local_magic_delivery",
                    target_modules=(
                        topology.msf_location,
                        topology.compute_location,
                    ),
                    deferred_dispatch=ResourceMoveDispatchRecipe(),
                )
            )
        else:
            link_id = topology.remote_magic_link
            bell_buffer = f"bell:{link_id}"
            bell_profile = topology.bell_engine_profiles[link_id]
            bell_outputs_per_batch = int(
                bell_profile["outputs_per_copy_per_batch"]
            )
            if bell_arrival is None:
                raise ValueError(
                    "Logical-Bell production needs a resolved arrival distribution"
                )
            if bell_binding is not None and (
                bell_binding.protocol_id != str(bell_profile["protocol"])
                or bell_binding.copies != int(bell_profile["copies"])
                or bell_binding.outputs_per_copy_per_batch
                != bell_outputs_per_batch
            ):
                raise ValueError(
                    "Resolved logical-Bell binding disagrees with runtime topology"
                )
            processes.extend(
                (
                    ResourceProcess(
                        id=f"prepare_logical_bell:{link_id}",
                        opcode=ArchitectureOpcode.PREPARE_LOGICAL_BELL,
                        produces={bell_buffer: bell_outputs_per_batch},
                        engines={f"bell_engine:{link_id}": 1},
                        arrival_distribution=bell_arrival,
                        parallelism=int(bell_profile["copies"]),
                        output_overflow_policy="discard_excess",
                        protocol=str(bell_profile["protocol"]),
                        target_links=(link_id,),
                        metadata={"outputs_per_batch": bell_outputs_per_batch},
                    ),
                    ResourceProcess(
                        id="deliver_magic_remote",
                        opcode=ArchitectureOpcode.TELEPORT_QUBITS,
                        consumes={"msf_output": 1, bell_buffer: 1},
                        produces={"magic_compute": 1},
                        forwards={"msf_output": "magic_compute"},
                        engines={"resource_move": 1},
                        duration_s=cost_inputs.logical_link_item_s,
                        parallelism=resource_delivery_channels,
                        protocol="logical_magic_teleportation",
                        target_modules=(
                            topology.msf_location,
                            topology.compute_location,
                        ),
                        target_links=(link_id,),
                        metadata={"consumes_one_logical_bell_pair": True},
                    ),
                )
            )

    existing_bell_buffers = {
        next(iter(process.produces))
        for process in processes
        if process.opcode == ArchitectureOpcode.PREPARE_LOGICAL_BELL
    }
    for link_id in topology.bell_buffer_capacities:
        buffer_id = f"bell:{link_id}"
        if buffer_id in existing_bell_buffers:
            continue
        bell_profile = topology.bell_engine_profiles[link_id]
        bell_outputs_per_batch = int(
            bell_profile["outputs_per_copy_per_batch"]
        )
        if bell_arrival is None:
            raise ValueError(
                "Logical-Bell production needs a resolved arrival distribution"
            )
        if bell_binding is not None and (
            bell_binding.protocol_id != str(bell_profile["protocol"])
            or bell_binding.copies != int(bell_profile["copies"])
            or bell_binding.outputs_per_copy_per_batch != bell_outputs_per_batch
        ):
            raise ValueError(
                "Resolved logical-Bell binding disagrees with runtime topology"
            )
        processes.append(
            ResourceProcess(
                id=f"prepare_logical_bell:{link_id}",
                opcode=ArchitectureOpcode.PREPARE_LOGICAL_BELL,
                produces={buffer_id: bell_outputs_per_batch},
                engines={f"bell_engine:{link_id}": 1},
                arrival_distribution=bell_arrival,
                parallelism=int(bell_profile["copies"]),
                output_overflow_policy="discard_excess",
                protocol=str(bell_profile["protocol"]),
                target_links=(link_id,),
                metadata={"outputs_per_batch": bell_outputs_per_batch},
            )
        )

    local_move_source = None
    if msf_entry is not None:
        connection = _connection_between(
            specification,
            msf_entry,
            compute_entry,
        )
        if connection is not None and connection[0] == "local_connection":
            local_move_source = {
                "scope": "local_connection",
                "id": connection[1],
            }
    return _LoweredResourcePlan(ResourceDAG(tuple(processes)), local_move_source)


def _validate_compilation_mapping(
    compilation: LogicalCompilationResult,
    circuit: FTCircuit,
    specification: ArchitectureSpecification,
    *,
    compute_capacity: int,
) -> None:
    """Bind compiler placement claims to canonical compute data slots.

    A custom compiler pipeline may choose placement and routing algorithms, but
    it cannot invent architecture slot identities or disagree with the
    capacity-driven residency mode owned by the plan builder.
    """

    layout = materialize_compute_layout(specification)
    data_slots = frozenset(slot.id for slot in layout.slots_of_kind("data"))
    circuit_qubits = frozenset(range(circuit.num_qubits))
    expected_deferred = circuit.num_qubits > compute_capacity
    memory_entry = single_node_module(
        specification,
        "memory",
        required=False,
    )
    memory_capacity = (
        primary_qec_submodule(memory_entry[1]).capacity
        if memory_entry is not None
        else 0
    )
    total_logical_capacity = compute_capacity + memory_capacity
    expected_resident_count = min(circuit.num_qubits, compute_capacity)
    insufficient_total_capacity = (
        circuit.num_qubits > total_logical_capacity
    )
    deferred_without_memory = (
        expected_deferred
        and memory_entry is None
    )
    flag_mismatch = (
        compilation.deferred_capacity_mapping != expected_deferred
    )

    initial_keys = frozenset(compilation.initial_mapping)
    invalid_initial_slots = sorted(
        set(compilation.initial_mapping.values()) - data_slots
    )
    initial_key_mismatch = (
        initial_keys != (frozenset() if expected_deferred else circuit_qubits)
    )

    unit_errors: list[dict[str, Any]] = []
    partition_mappings: dict[tuple[int, int], Mapping[int, str]] = {}
    previous_mapping: Mapping[int, str] | None = None
    previous_identity: dict[str, int] | None = None
    for unit in compilation.compute_units:
        identity = {
            "layer": unit.source.layer_index,
            "partition": unit.source.partition.partition_index,
            "batch": unit.source.batch_index,
        }
        active = frozenset(unit.source.partition.active_qubits)
        mapping_keys = frozenset(unit.mapping)
        if not active <= mapping_keys:
            unit_errors.append(
                {
                    **identity,
                    "reason": "active_qubits_not_resident",
                    "active_qubits": sorted(active),
                    "resident_qubits": sorted(mapping_keys),
                }
            )
        if len(mapping_keys) > compute_capacity:
            unit_errors.append(
                {
                    **identity,
                    "reason": "compute_residency_exceeds_capacity",
                    "resident_qubits": len(mapping_keys),
                    "capacity": compute_capacity,
                }
            )
        if len(mapping_keys) != expected_resident_count:
            unit_errors.append(
                {
                    **identity,
                    "reason": "residency_cardinality_mismatch",
                    "resident_qubits": len(mapping_keys),
                    "expected": expected_resident_count,
                }
            )
        memory_qubits = len(circuit_qubits - mapping_keys)
        if memory_entry is None and mapping_keys != circuit_qubits:
            unit_errors.append(
                {
                    **identity,
                    "reason": "no_memory_requires_full_compute_residency",
                    "expected": sorted(circuit_qubits),
                    "actual": sorted(mapping_keys),
                }
            )
        elif memory_entry is not None and memory_qubits > memory_capacity:
            unit_errors.append(
                {
                    **identity,
                    "reason": "memory_residency_exceeds_capacity",
                    "memory_qubits": memory_qubits,
                    "capacity": memory_capacity,
                }
            )
        invalid_slots = sorted(set(unit.mapping.values()) - data_slots)
        if invalid_slots:
            unit_errors.append(
                {
                    **identity,
                    "reason": "unknown_compute_data_slots",
                    "slots": invalid_slots,
                }
            )
        invalid_qubits = sorted(mapping_keys - circuit_qubits)
        if invalid_qubits:
            unit_errors.append(
                {
                    **identity,
                    "reason": "mapping_qubits_outside_circuit",
                    "qubits": invalid_qubits,
                }
            )
        if not expected_deferred:
            if mapping_keys != circuit_qubits:
                unit_errors.append(
                    {
                        **identity,
                        "reason": "nondeferred_requires_full_residency",
                        "expected": sorted(circuit_qubits),
                        "actual": sorted(mapping_keys),
                    }
                )
            initial_discontinuities = {
                qubit: {
                    "initial": compilation.initial_mapping.get(qubit),
                    "current": unit.mapping[qubit],
                }
                for qubit in sorted(mapping_keys & circuit_qubits)
                if compilation.initial_mapping.get(qubit)
                != unit.mapping[qubit]
            }
            if initial_discontinuities:
                unit_errors.append(
                    {
                        **identity,
                        "reason": "unit_initial_mapping_mismatch",
                        "qubits": initial_discontinuities,
                    }
                )
        elif previous_mapping is not None:
            shared_qubits = set(previous_mapping) & set(unit.mapping)
            discontinuities = {
                qubit: {
                    "previous": previous_mapping[qubit],
                    "current": unit.mapping[qubit],
                }
                for qubit in sorted(shared_qubits)
                if previous_mapping[qubit] != unit.mapping[qubit]
            }
            if discontinuities:
                unit_errors.append(
                    {
                        **identity,
                        "reason": "resident_mapping_discontinuity",
                        "previous_unit": previous_identity,
                        "qubits": discontinuities,
                    }
                )
        partition_key = (
            unit.source.layer_index,
            unit.source.partition.partition_index,
        )
        prior_mapping = partition_mappings.setdefault(partition_key, unit.mapping)
        if dict(prior_mapping) != dict(unit.mapping):
            unit_errors.append(
                {
                    **identity,
                    "reason": "inconsistent_partition_batch_mapping",
                }
            )
        previous_mapping = unit.mapping
        previous_identity = identity

    if (
        flag_mismatch
        or deferred_without_memory
        or insufficient_total_capacity
        or initial_key_mismatch
        or invalid_initial_slots
        or unit_errors
    ):
        raise LogicalCompilerValidationError(
            "LogicalCompilationResult mapping does not match the canonical "
            "compute layout",
            details={
                "expected_deferred_capacity_mapping": expected_deferred,
                "actual_deferred_capacity_mapping": (
                    compilation.deferred_capacity_mapping
                ),
                "deferred_mapping_requires_memory": deferred_without_memory,
                "compute_capacity": compute_capacity,
                "memory_capacity": memory_capacity,
                "expected_resident_qubits_per_unit": expected_resident_count,
                "total_logical_capacity": total_logical_capacity,
                "insufficient_total_logical_capacity": (
                    insufficient_total_capacity
                ),
                "expected_initial_qubits": (
                    [] if expected_deferred else sorted(circuit_qubits)
                ),
                "actual_initial_qubits": sorted(initial_keys),
                "invalid_initial_slots": invalid_initial_slots,
                "unit_mapping_errors": unit_errors,
            },
        )


def lower_compilation_result(
    circuit: FTCircuit,
    specification: ArchitectureSpecification,
    model: OperationLatencyProfile,
    policy: EvaluationPolicy,
    compilation: LogicalCompilationResult,
    *,
    resource_delivery_channels: int | None = None,
    runtime_components: Mapping[str, Any] | None = None,
    resource_protocol_bindings: ResolvedResourceProtocolBindings | None = None,
) -> ExecutionPlan:
    """Lower one verified logical-compilation result into an execution plan.

    The compilation remains an independently serializable boundary.  Plan
    lowering validates it against the supplied source authorities before
    projecting Program work and independently lowering Resource work and the
    architectural inventory.
    """

    if not isinstance(specification, ArchitectureSpecification):
        raise TypeError("specification must be an ArchitectureSpecification")
    if not isinstance(compilation, LogicalCompilationResult):
        raise TypeError("compilation must be a LogicalCompilationResult")
    expected_compilation_sources = {
        "circuit_hash": circuit.semantic_hash,
        "architecture_hash": specification.architecture_hash,
        "latency_profile_hash": model.binding_hash,
    }
    actual_compilation_sources = {
        "circuit_hash": compilation.circuit_hash,
        "architecture_hash": compilation.architecture_hash,
        "latency_profile_hash": compilation.latency_profile_hash,
    }
    if actual_compilation_sources != expected_compilation_sources:
        raise ValueError(
            "LogicalCompilationResult source hashes do not match the plan "
            "inputs: "
            f"expected {expected_compilation_sources}, got "
            f"{actual_compilation_sources}"
        )
    if compilation.magic_state_consumption != policy.magic_state_consumption:
        raise ValueError(
            "LogicalCompilationResult magic-state consumption does not match the "
            "evaluation policy: expected "
            f"{policy.magic_state_consumption!r}, got "
            f"{compilation.magic_state_consumption!r}"
        )
    validate_compilation_coverage(compilation, circuit)
    resolved_compiler = compilation.compiler_spec
    compute_entry = single_node_module(specification, "compute")
    assert compute_entry is not None
    compute_capacity = primary_qec_submodule(compute_entry[1]).capacity
    topology = _runtime_topology(specification)
    magic_capacity_mismatches = [
        {
            "layer": unit.source.layer_index,
            "partition": unit.source.partition.partition_index,
            "batch": unit.source.batch_index,
            "magic_count": unit.source.magic_count,
            "capacity": topology.magic_buffer_capacity,
        }
        for unit in compilation.compute_units
        if unit.source.magic_count > topology.magic_buffer_capacity
    ]
    if magic_capacity_mismatches:
        raise LogicalCompilerValidationError(
            "LogicalCompilationResult exceeds architecture execution "
            "capacities",
            details={
                "magic_buffer_mismatches": magic_capacity_mismatches,
            },
        )
    _validate_compilation_mapping(
        compilation,
        circuit,
        specification,
        compute_capacity=compute_capacity,
    )
    cost_inputs = _resolve_plan_cost_inputs(specification, topology, model)
    if resource_protocol_bindings is not None and not isinstance(
        resource_protocol_bindings,
        ResolvedResourceProtocolBindings,
    ):
        raise TypeError(
            "resource_protocol_bindings must be ResolvedResourceProtocolBindings or None"
        )
    magic_binding = (
        resource_protocol_bindings.magic_state
        if resource_protocol_bindings is not None
        else None
    )
    bell_binding = (
        resource_protocol_bindings.logical_bell_pair
        if resource_protocol_bindings is not None
        else None
    )
    delivery_concurrency_source = "explicit_runtime_override"
    if resource_delivery_channels is None:
        resource_delivery_channels = _available_delivery_concurrency(topology)
        delivery_concurrency_source = "buffer_available_concurrency"
    elif resource_delivery_channels <= 0:
        raise ValueError("Resource-delivery parallelism must be positive")

    execution_units = compilation.compute_units
    if not execution_units:
        protocol_provenance = (
            {
                "resource_protocol_bindings": resource_protocol_bindings.to_dict(),
                "resource_protocol_bindings_hash": (
                    resource_protocol_bindings.bindings_hash
                ),
            }
            if resource_protocol_bindings is not None
            else {}
        )
        return ExecutionPlan(
            circuit_hash=circuit.semantic_hash,
            architecture_hash=specification.architecture_hash,
            latency_profile_hash=model.profile_hash,
            policy=policy,
            program_dag=ProgramDAG(()),
            resource_dag=ResourceDAG(()),
            buffers=(),
            engines=(),
            provenance={
                "plan_builder_version": PLAN_BUILDER_VERSION,
                "compiler_spec_hash": resolved_compiler.compiler_hash,
                "compilation_hash": compilation.compilation_hash,
                **protocol_provenance,
            },
            runtime_components=(
                runtime_components if runtime_components is not None else {}
            ),
        )

    lowered_state = _lower_architectural_state(
        specification,
        topology,
        resource_delivery_channels=resource_delivery_channels,
    )
    lowered_resources = _lower_resource_dag(
        specification,
        topology,
        cost_inputs,
        resource_delivery_channels=resource_delivery_channels,
        magic_arrival=(
            magic_binding.effective_arrival_distribution
            if magic_binding is not None
            else model.magic_state_arrival
        ),
        bell_arrival=(
            bell_binding.effective_arrival_distribution
            if bell_binding is not None
            else model.bell_pair_arrival
        ),
        resource_protocol_bindings=resource_protocol_bindings,
    )
    lowered_program = _lower_program_dag(
        circuit,
        specification,
        compilation,
        topology,
        cost_inputs,
        model,
        policy,
    )
    has_deferred_dispatch = any(
        instruction.deferred_dispatch is not None
        for instruction in lowered_program.dag.instructions
    ) or any(
        process.deferred_dispatch is not None
        for process in lowered_resources.dag.processes
    )
    selected_runtime_components = runtime_components
    if selected_runtime_components is None and has_deferred_dispatch:
        selected_runtime_components = (
            default_runtime_component_manifest().to_dict()
        )

    return ExecutionPlan(
        circuit_hash=circuit.semantic_hash,
        architecture_hash=specification.architecture_hash,
        latency_profile_hash=model.profile_hash,
        policy=policy,
        program_dag=lowered_program.dag,
        resource_dag=lowered_resources.dag,
        buffers=lowered_state.buffers,
        engines=lowered_state.engines,
        initial_locations=lowered_program.initial_locations,
        provenance={
            "plan_builder_version": PLAN_BUILDER_VERSION,
            "compiler_spec_hash": resolved_compiler.compiler_hash,
            "compilation_hash": compilation.compilation_hash,
            "initialization": "cold_start",
            "program_dependency": "dag_derived_ft_layers",
            "resource_policy": "greedy_fill_to_capacity",
            "program_resource_move_contention": "separate_domains",
            "runtime_injection_recipe_mode": policy.runtime_injection_mode.value,
            "resource_delivery_channels": int(resource_delivery_channels),
            "resource_delivery_concurrency_source": delivery_concurrency_source,
            "resource_delivery_channels_footprint_model": (
                "derived_from_architecture_buffers"
                if delivery_concurrency_source == "buffer_available_concurrency"
                else "runtime_only_not_charged"
            ),
            **(
                {
                    "resource_protocol_bindings": (
                        resource_protocol_bindings.to_dict()
                    ),
                    "resource_protocol_bindings_hash": (
                        resource_protocol_bindings.bindings_hash
                    ),
                }
                if resource_protocol_bindings is not None
                else {}
            ),
            **(
                {
                    "derived_engine_sources": {
                        "resource_move": lowered_resources.local_move_source
                    }
                }
                if lowered_resources.local_move_source is not None
                else {}
            ),
        },
        runtime_components=(
            selected_runtime_components
            if selected_runtime_components is not None
            else {}
        ),
    )


def compile_and_lower(
    circuit: FTCircuit,
    specification: ArchitectureSpecification,
    model: OperationLatencyProfile,
    policy: EvaluationPolicy,
    *,
    compiler_spec: LogicalCompilerSpec | None = None,
    compiler_pipeline: CompilerPipeline | None = None,
    resource_delivery_channels: int | None = None,
    runtime_components: Mapping[str, Any] | None = None,
    resource_protocol_bindings: ResolvedResourceProtocolBindings | None = None,
) -> tuple[LogicalCompilationResult, ExecutionPlan]:
    """Return both canonical artifacts from logical compilation and lowering.

    This is the orchestration composition boundary.  Call
    :func:`lower_compilation_result` directly when reusing a serialized or
    externally produced logical-compilation result.  The helper is exported
    from :mod:`heteqsys.evaluation`, but deliberately remains outside the
    package-root facade.
    """

    if not isinstance(specification, ArchitectureSpecification):
        raise TypeError("specification must be an ArchitectureSpecification")
    pipeline = compiler_pipeline or DefaultCompilerPipeline(
        compiler_spec=compiler_spec,
        magic_state_consumption=policy.magic_state_consumption,
        defer_magic_routing=True,
    )
    compilation = pipeline.compile(circuit, specification, model)
    if not isinstance(compilation, LogicalCompilationResult):
        raise TypeError(
            "CompilerPipeline must return a LogicalCompilationResult"
        )
    plan = lower_compilation_result(
        circuit,
        specification,
        model,
        policy,
        compilation,
        resource_delivery_channels=resource_delivery_channels,
        runtime_components=runtime_components,
        resource_protocol_bindings=resource_protocol_bindings,
    )
    return compilation, plan
