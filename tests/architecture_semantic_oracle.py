"""Small, representation-neutral architecture semantic projections.

The current implementation carries several compatibility views of the same
architecture.  Refactor characterization tests must not turn those views into
the new contract.  This module therefore reads the current public behavior
and projects only domain facts that a ground-up implementation must preserve.

The returned documents intentionally omit schema versions, hashes,
construction adapters, legacy roles, ports, compiler routing-node IDs, report
section names, and other v1 serialization details.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

from arqsim.api import EvaluationConfig, EvaluationReport, run_evaluation
from arqsim.evaluation import EvaluationPolicy
from arqsim.operation_profiles import (
    ArrivalDistribution,
    OperationLatencyProfile,
)
from arqsim.program import FTCircuit, LogicalLayer, LogicalOperation
from arqsim.schema import normalize_json, semantic_hash
from arqsim.specification import (
    ArchitectureSpecification,
    build_architecture_specification,
)


ORACLE_SCHEMA_VERSION = "arqsim.architecture-semantic-oracle.v1"
PROFILE_IDS = ("1.1", "1.2", "1.3", "2.1", "2.2", "2.3")
MULTI_NODE_PROFILE_IDS = frozenset({"1.3", "2.2", "2.3"})


@dataclass(frozen=True)
class OracleCase:
    """One compact workload shared by the six architecture profiles."""

    id: str = "six-profile-clifford-t"
    workflow_id: str = "six-profile-architecture-oracle"


CASE = OracleCase()


def oracle_circuit() -> FTCircuit:
    """Exercise compute, magic, memory spilling, and two-wide transfer.

    Four qubits arrive in two disjoint active waves.  Architectures with a
    separate memory therefore resolve a two-slot compute region and a two-slot
    store/load wave, while unified-compute architectures resolve four data
    slots.  Two T gates make both local and remote magic delivery observable.
    """

    return FTCircuit(
        representation="clifford_t",
        num_qubits=4,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (
                    LogicalOperation("gate", "h", qubits=(0,)),
                    LogicalOperation("gate", "h", qubits=(1,)),
                ),
            ),
            LogicalLayer(1, (LogicalOperation("gate", "t", qubits=(0,)),)),
            LogicalLayer(
                2,
                (LogicalOperation("gate", "cx", qubits=(0, 1)),),
            ),
            LogicalLayer(
                3,
                (
                    LogicalOperation("gate", "h", qubits=(2,)),
                    LogicalOperation("gate", "h", qubits=(3,)),
                ),
            ),
            LogicalLayer(4, (LogicalOperation("gate", "t", qubits=(2,)),)),
            LogicalLayer(
                5,
                (LogicalOperation("gate", "cx", qubits=(2, 3)),),
            ),
        ),
        provenance={"benchmark": CASE.id},
    )


def oracle_latency_profile() -> OperationLatencyProfile:
    arrival = ArrivalDistribution.from_rate(1_000.0, kind="deterministic")
    return OperationLatencyProfile(
        magic_state_arrival=arrival,
        bell_pair_arrival=arrival,
    )


def build_oracle_specification(profile_id: str) -> ArchitectureSpecification:
    return build_architecture_specification(oracle_circuit(), profile_id)


def run_oracle(profile_id: str) -> EvaluationReport:
    return run_evaluation(
        oracle_circuit(),
        EvaluationConfig(
            profile_id=profile_id,
            run_label=CASE.workflow_id,
            latency_profile=oracle_latency_profile(),
            execution_policy=EvaluationPolicy(trace_level="full", seed=0),
        ),
    )


def _capacity_projection(submodule: Any) -> dict[str, int]:
    unit = (
        "copies"
        if submodule.type == "engine"
        else {
            "logical_qubit": "logical_patches",
            "magic_state": "logical_magic_states",
            "bell_pair": "logical_bell_pairs",
        }[submodule.payload]
    )
    return {unit: submodule.capacity}


def _submodule_projection(
    specification: ArchitectureSpecification,
    owner_id: str,
    module: Any,
    submodule: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": str(submodule.id),
        "type": str(submodule.type),
        "payload": str(submodule.payload),
        "capacity": _capacity_projection(submodule),
    }
    if submodule.qec is not None:
        result["qec"] = submodule.qec.to_dict()
    if submodule.resource_protocol is not None:
        result["resource_protocol"] = {"id": submodule.resource_protocol.id}
    if submodule.slots:
        slots = []
        for slot in submodule.slots:
            projected: dict[str, Any] = {
                "id": f"{owner_id}/{module.id}/{submodule.id}/{slot.id}"
            }
            coordinate = specification.effective_slot_coordinate(
                owner_id, module.id, submodule.id, slot.id
            )
            if coordinate is not None:
                projected["logical_coordinate"] = list(coordinate)
            slots.append(projected)
        result["slots"] = slots
        if all(slot.coordinate is None for slot in submodule.slots):
            result["slot_semantics"] = "identity_only"
    return result


def _connection_projection(owner: Any, connection: Any) -> dict[str, Any]:
    module_id, submodule_id = connection.endpoints[0].split("/")
    payload = next(
        submodule.payload
        for module in owner.modules
        if module.id == module_id
        for submodule in module.submodules
        if submodule.id == submodule_id
    )
    return {
        "id": connection.id,
        "payload": payload,
        "direction": connection.direction,
        "endpoints": list(connection.endpoints),
    }


def _protocol_projection(bindings: Any) -> list[dict[str, Any]]:
    result = []
    for resource_kind, binding in sorted(
        bindings.bindings.items()
    ):
        record: dict[str, Any] = {
            "resource_kind": resource_kind,
            "protocol_id": binding.protocol_id,
            "copies": binding.copies,
            "outputs_per_copy_per_batch": (
                binding.outputs_per_copy_per_batch
            ),
        }
        if resource_kind == "logical_bell_pair":
            record["shared_resource"] = True
            record["logical_qubits_per_pair"] = 2
            record["logical_qubits_per_copy_per_endpoint"] = int(
                binding.physical_footprint[
                    "logical_qubits_per_copy_per_endpoint"
                ]
            )
            record["logical_qubits_total_per_endpoint"] = int(
                binding.physical_footprint[
                    "logical_qubits_total_per_endpoint"
                ]
            )
        result.append(record)
    return result


def architecture_semantic_projection(
    specification: ArchitectureSpecification,
    *,
    profile_id: str,
    logical_qubits: int,
    resource_protocol_bindings: Any,
) -> dict[str, Any]:
    """Return the architecture facts the canonical model must preserve."""

    nodes = []
    for node in specification.nodes:
        nodes.append(
            {
                "id": node.id,
                "modality": node.modality,
                "modules": [
                    {
                        "id": module.id,
                        "type": module.type,
                        "submodules": [
                            _submodule_projection(
                                specification, node.id, module, submodule
                            )
                            for submodule in module.submodules
                        ],
                    }
                    for module in node.modules
                ],
                "connections": [
                    _connection_projection(node, connection)
                    for connection in node.connections
                ],
            }
        )

    interconnects = []
    for interconnect in specification.interconnects:
        buffer_module, buffer = next(
            (module, submodule)
            for module in interconnect.modules
            for submodule in module.submodules
            if submodule.type == "buffer" and submodule.payload == "bell_pair"
        )
        pair_capacity = buffer.capacity
        endpoint_nodes = sorted(
            {endpoint.split("/", 1)[0] for endpoint in interconnect.endpoints}
        )
        interconnects.append(
            {
                "id": interconnect.id,
                "ownership": "shared_interconnect",
                "endpoints": endpoint_nodes,
                "submodules": [
                    _submodule_projection(
                        specification, interconnect.id, module, submodule
                    )
                    for module in interconnect.modules
                    for submodule in module.submodules
                ],
                "access": [
                    {
                        "node": endpoint.split("/", 1)[0],
                        "target_submodule": (
                            f"{interconnect.id}/{buffer_module.id}/{buffer.id}"
                        ),
                        "local_submodules": [endpoint.split("/", 1)[1]],
                    }
                    for endpoint in sorted(interconnect.endpoints)
                ],
                "bell_state_model": {
                    "pair_capacity": pair_capacity,
                    "pair_token_identity": "single_shared_identity",
                    "endpoint_halves_per_pair": 2,
                    "logical_qubit_states_total": 2 * pair_capacity,
                    "footprint_accounting": "one_half_per_endpoint",
                },
            }
        )

    return {
        "profile_id": profile_id,
        "workload": {"logical_qubits": logical_qubits},
        "nodes": nodes,
        "interconnects": interconnects,
        "resource_protocols": _protocol_projection(resource_protocol_bindings),
    }


def footprint_semantic_projection(
    specification: ArchitectureSpecification,
    footprint: Any,
) -> dict[str, Any]:
    """Project analytical area results, separate from architecture authority."""

    node_modalities = {
        node.id: node.modality for node in specification.nodes
    }
    interconnect_results = []
    for interconnect in specification.interconnects:
        buffer = next(
            submodule
            for module in interconnect.modules
            for submodule in module.submodules
            if submodule.type == "buffer" and submodule.payload == "bell_pair"
        )
        pair_capacity = buffer.capacity
        endpoints = []
        for endpoint in sorted(interconnect.endpoints):
            endpoint_node = endpoint.split("/", 1)[0]
            modality = node_modalities[endpoint_node]
            components = [
                component
                for component in footprint.components
                if component.owner_id == interconnect.id
                and component.endpoint_node_id == endpoint_node
            ]
            endpoints.append(
                {
                    "endpoint": endpoint_node,
                    "modality": modality,
                    "logical_qubit_states": pair_capacity,
                    "component_count": len(components),
                    "physical_qubits": sum(
                        component.physical_qubits for component in components
                    ),
                }
            )
        interconnect_results.append(
            {
                "id": interconnect.id,
                "shared_pair_capacity": pair_capacity,
                "endpoints": endpoints,
            }
        )
    return {
        "model_id": footprint.model_id,
        "total_physical_qubits": footprint.total_physical_qubits,
        "bell_endpoint_components": [
            {
                "kind": (
                    "engine"
                    if component.rule == "logical_bell_protocol_endpoint"
                    else "buffer_half"
                ),
                "modality": component.modality,
                "quantity": component.capacity,
                "physical_qubits": component.physical_qubits,
            }
            for component in footprint.components
            if component.module_type in {"bell_engine", "bell_storage"}
        ],
        "interconnects": interconnect_results,
    }


def _operation_claims(operation: Any) -> dict[str, Any]:
    return {
        "opcode": operation.opcode.value,
        "consumes": normalize_json(operation.consumes),
        "produces": normalize_json(operation.produces),
        "forwards": normalize_json(operation.forwards),
        "engines": normalize_json(operation.engines),
        "target_modules": list(operation.target_modules),
        "target_links": list(operation.target_links),
    }


def _program_semantic_signature(instruction: Any) -> str:
    qubits = ",".join(str(value) for value in instruction.qubits) or "-"
    layer = instruction.layer_index
    return f"L{layer if layer is not None else '-'}:{instruction.opcode.value}:q{qubits}"


def execution_semantic_projection(report: EvaluationReport) -> dict[str, Any]:
    """Project compiler/plan/trace behavior without freezing v1 document shape."""

    plan = report.execution_plan
    result = report.evaluation
    program_opcode_counts = Counter(
        instruction.opcode.value for instruction in plan.program_dag.instructions
    )
    completed_opcode_counts = Counter(event.opcode.value for event in result.events)
    completed_work_s: dict[str, float] = {}
    for event in result.events:
        opcode = event.opcode.value
        completed_work_s[opcode] = completed_work_s.get(opcode, 0.0) + (
            event.end_s - event.start_s
        )
    program_signatures = {
        instruction.id: _program_semantic_signature(instruction)
        for instruction in plan.program_dag.instructions
    }

    return {
        "compiler": {
            "mapping_backend": report.compiler_spec.mapping.backend,
            "routing_backend": report.compiler_spec.routing.backend,
        },
        "plan": {
            "program_opcode_counts": dict(sorted(program_opcode_counts.items())),
            "program": [
                {
                    "signature": program_signatures[instruction.id],
                    "predecessors": [
                        program_signatures[predecessor]
                        for predecessor in instruction.predecessor_ids
                    ],
                    "duration_s": instruction.duration_s,
                    **_operation_claims(instruction),
                }
                for instruction in plan.program_dag.instructions
            ],
            "resource_processes": [
                {
                    "id": process.id,
                    **_operation_claims(process),
                    "protocol": process.protocol,
                    "duration_s": process.duration_s,
                    "arrival_distribution": (
                        process.arrival_distribution.to_dict()
                        if process.arrival_distribution is not None
                        else None
                    ),
                    "parallelism": process.parallelism,
                    "dispatch_policy": process.dispatch_policy,
                    "output_overflow_policy": (
                        process.output_overflow_policy
                    ),
                }
                for process in plan.resource_dag.processes
            ],
            "buffers": [
                {
                    "id": buffer.id,
                    "capacity": buffer.capacity,
                    "token_kind": buffer.token_kind,
                    "owner": {
                        "module": buffer.module,
                        "submodule": buffer.submodule,
                    },
                    "slots": list(buffer.slots),
                }
                for buffer in plan.buffers
            ],
            "engines": [
                {
                    "id": engine.id,
                    "capacity": engine.capacity,
                    "owner": {
                        "module": engine.module,
                        "submodule": engine.submodule,
                    },
                }
                for engine in plan.engines
            ],
        },
        "trace": {
            "total_latency_s": result.total_latency_s,
            "completed_opcode_counts": dict(
                sorted(completed_opcode_counts.items())
            ),
            "completed_work_s_by_opcode": dict(sorted(completed_work_s.items())),
            "completion_schedule": [
                {
                    "plane": event.plane.value,
                    "opcode": event.opcode.value,
                    "operation": (
                        program_signatures[event.instruction_id]
                        if event.instruction_id is not None
                        else event.process_id
                    ),
                    "start_s": event.start_s,
                    "end_s": event.end_s,
                    "duration_s": event.end_s - event.start_s,
                }
                for event in result.events
            ],
            "completed_program_instructions": (
                result.completed_program_instructions
            ),
            "program_state_blocked_s": result.program_state_blocked_s,
            "producer_blocked_s": normalize_json(
                result.producer_blocked_s
            ),
            "buffer_peaks": normalize_json(result.buffer_peaks),
            "resource_items_started": normalize_json(
                result.metrics["resource_items_started"]
            ),
            "buffer_tokens_produced": normalize_json(
                result.metrics["buffer_tokens_produced"]
            ),
            "buffer_tokens_consumed": normalize_json(
                result.metrics["buffer_tokens_consumed"]
            ),
            "inflight_resource_events_at_program_completion": (
                result.metrics[
                    "inflight_resource_events_at_program_completion"
                ]
            ),
            "all_invariants_hold": all(result.invariant_checks.values()),
            "terminal_buffer_tokens": {
                name: list(tokens)
                for name, tokens in sorted(
                    result.trace.terminal_state.buffers.items()
                )
            },
        },
    }


def semantic_oracle(profile_id: str) -> dict[str, Any]:
    report = run_oracle(profile_id)
    return {
        "schema_version": ORACLE_SCHEMA_VERSION,
        "case_id": CASE.id,
        "architecture": architecture_semantic_projection(
            report.specification,
            profile_id=profile_id,
            logical_qubits=report.circuit.num_qubits,
            resource_protocol_bindings=report.resource_protocol_bindings,
        ),
        "execution": execution_semantic_projection(report),
        "results": {
            "footprint": footprint_semantic_projection(
                report.specification, report.footprint
            )
        },
    }


def semantic_oracle_digest(profile_id: str) -> str:
    """Stable diagnostic digest; fixture fields, not the digest, are authority."""

    return semantic_hash(semantic_oracle(profile_id))
