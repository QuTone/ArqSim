"""Matched analytic references for state-coupled evaluation.

The baselines deliberately reuse the exact Program DAG, compiler bindings,
resource protocols, and architecture specification used by ArqSim.  They
change only how component costs are composed:

``compiler_circuit_lower_bound`` assumes every architecture resource is ready;
``static_layerwise_aggregation`` replaces resource processes by fluid average
rates and independently summarizes each logical layer.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from arqsim.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    MagicRouteDispatchRecipe,
    ResourceMoveDispatchRecipe,
)
from arqsim.schema import normalize_json

from .plan import ExecutionPlan
from .components import DeferredDispatchRequest, RuntimeOperationView
from .resource_dag import ResourceProcess


RuntimeInstructionCompiler = Callable[
    [DeferredDispatchRequest],
    Mapping[str, Any],
]
RuntimeResourceCompiler = Callable[
    [DeferredDispatchRequest],
    Mapping[str, Any],
]


@dataclass(frozen=True)
class LayerEstimate:
    layer_index: int
    total_latency_s: float
    exclusive_breakdown_s: Mapping[str, float]
    inclusive_resource_production_s: Mapping[str, float]
    resource_demand: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "layer_index": self.layer_index,
            "total_latency_s": self.total_latency_s,
            "exclusive_breakdown_s": normalize_json(
                self.exclusive_breakdown_s
            ),
            "inclusive_resource_production_s": normalize_json(
                self.inclusive_resource_production_s
            ),
            "resource_demand": normalize_json(self.resource_demand),
        }


@dataclass(frozen=True)
class AnalyticEstimate:
    estimator_id: str
    total_latency_s: float
    exclusive_breakdown_s: Mapping[str, float]
    layers: tuple[LayerEstimate, ...]
    assumptions: Mapping[str, Any]
    plan_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimator_id": self.estimator_id,
            "total_latency_s": self.total_latency_s,
            "exclusive_breakdown_s": normalize_json(
                self.exclusive_breakdown_s
            ),
            "layers": [layer.to_dict() for layer in self.layers],
            "assumptions": normalize_json(self.assumptions),
            "plan_hash": self.plan_hash,
        }


def _buffers(plan: ExecutionPlan) -> dict[str, Any]:
    return {buffer.id: buffer for buffer in plan.buffers}


def _nominal_command_duration(
    command: ArchitectureInstruction,
    plan: ExecutionPlan,
    compiler: RuntimeInstructionCompiler | None,
) -> float:
    """Compile against canonical buffer slots without observing runtime state."""

    duration = command.duration_s
    if compiler is None or not isinstance(
        command.deferred_dispatch,
        MagicRouteDispatchRecipe,
    ):
        return duration
    buffers = _buffers(plan)
    consumed_tokens: dict[str, tuple[str, ...]] = {}
    consumed_slots: dict[str, tuple[str, ...]] = {}
    for name, amount in command.consumes.items():
        buffer = buffers[name]
        if amount > buffer.capacity:
            raise ValueError(
                f"Static compiler demand {amount} exceeds {name} capacity "
                f"{buffer.capacity}"
            )
        consumed_tokens[name] = tuple(
            f"static:{name}:{index}" for index in range(amount)
        )
        consumed_slots[name] = tuple(buffer.slots[:amount])
    operation = RuntimeOperationView.from_operation(command, plane="program")
    binding = compiler(
        DeferredDispatchRequest(
            operation=operation,
            recipe=command.deferred_dispatch,
            consumed_tokens=consumed_tokens,
            consumed_slots=consumed_slots,
        )
    )
    compiled = float(binding.get("duration_s", duration))
    if not math.isfinite(compiled) or compiled < 0:
        raise ValueError("Runtime compiler returned an invalid static duration")
    return compiled


def _resource_rate(process: ResourceProcess) -> float:
    distribution = process.arrival_distribution
    outputs = sum(int(value) for value in process.produces.values())
    if distribution is None:
        if process.duration_s <= 0:
            return math.inf
        return process.parallelism * outputs / process.duration_s
    return process.parallelism * outputs / (
        distribution.mean_interval_s + process.duration_s
    )


def _resource_process(
    plan: ExecutionPlan, opcode: ArchitectureOpcode
) -> ResourceProcess | None:
    matches = [
        process for process in plan.resource_dag.processes
        if process.opcode == opcode
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(
            f"Static estimator needs one {opcode.value} producer, found "
            f"{len(matches)}"
        )
    return matches[0]


def _magic_delivery_process(
    plan: ExecutionPlan,
    magic_input_buffers: set[str],
) -> ResourceProcess | None:
    matches = [
        process
        for process in plan.resource_dag.processes
        if set(process.produces) & magic_input_buffers
        and process.opcode
        in {ArchitectureOpcode.MOVE_QUBITS, ArchitectureOpcode.TELEPORT_QUBITS}
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("Static estimator found multiple magic-delivery paths")
    return matches[0]


def _nominal_delivery_duration(
    process: ResourceProcess | None,
    demand: int,
    plan: ExecutionPlan,
    compiler: RuntimeResourceCompiler | None,
) -> float:
    if process is None or demand <= 0:
        return 0.0
    if (
        process.opcode != ArchitectureOpcode.MOVE_QUBITS
        or compiler is None
        or not isinstance(
            process.deferred_dispatch,
            ResourceMoveDispatchRecipe,
        )
    ):
        outputs = max(1, sum(process.produces.values()))
        return demand / outputs * process.duration_s

    if len(process.forwards) != 1:
        raise ValueError("Static MOVE delivery requires one forwarded flow")
    source, destination = next(iter(process.forwards.items()))
    buffers = _buffers(plan)
    source_buffer = buffers[source]
    destination_buffer = buffers[destination]
    batch_capacity = min(source_buffer.capacity, destination_buffer.capacity)
    if batch_capacity <= 0:
        raise ValueError("Static MOVE delivery has no usable buffer capacity")

    total = 0.0
    remaining = demand
    while remaining:
        amount = min(remaining, batch_capacity)
        tokens = tuple(f"static:magic:{index}" for index in range(amount))
        source_slots = tuple(source_buffer.slots[:amount])
        destination_slots = tuple(destination_buffer.slots[:amount])
        operation = RuntimeOperationView.from_operation(process, plane="resource")
        binding = compiler(
            DeferredDispatchRequest(
                operation=operation,
                recipe=process.deferred_dispatch,
                consumed_tokens={source: tokens},
                consumed_slots={source: source_slots},
                produced_slots={destination: destination_slots},
            )
        )
        duration = float(binding.get("duration_s", process.duration_s))
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("Resource compiler returned an invalid duration")
        total += duration
        remaining -= amount
    return total


def _program_category(opcode: ArchitectureOpcode) -> str | None:
    return {
        ArchitectureOpcode.EXECUTE_COMPUTE: "compute",
        ArchitectureOpcode.MOVE_QUBITS: "program_move",
        ArchitectureOpcode.STORE_QUBITS: "store_load",
        ArchitectureOpcode.LOAD_QUBITS: "store_load",
        ArchitectureOpcode.TELEPORT_QUBITS: "program_teleport",
        ArchitectureOpcode.CLASSICAL_REACTION: "classical_reaction",
        ArchitectureOpcode.FENCE: None,
    }[opcode]


def estimate_compiler_circuit_lower_bound(
    plan: ExecutionPlan,
    *,
    runtime_instruction_compiler: RuntimeInstructionCompiler | None = None,
) -> AnalyticEstimate:
    """Compute-only circuit latency with all architecture resources ready."""

    per_layer: defaultdict[int, defaultdict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for command in plan.program_dag.instructions:
        if command.layer_index is None or command.opcode not in {
            ArchitectureOpcode.EXECUTE_COMPUTE,
            ArchitectureOpcode.CLASSICAL_REACTION,
        }:
            continue
        category = (
            "compute"
            if command.opcode == ArchitectureOpcode.EXECUTE_COMPUTE
            else "classical_reaction"
        )
        per_layer[command.layer_index][category] += _nominal_command_duration(
            command, plan, runtime_instruction_compiler
        )

    layers = tuple(
        LayerEstimate(
            layer_index=layer,
            total_latency_s=sum(parts.values()),
            exclusive_breakdown_s=dict(sorted(parts.items())),
            inclusive_resource_production_s={},
            resource_demand={},
        )
        for layer, parts in sorted(per_layer.items())
    )
    totals: defaultdict[str, float] = defaultdict(float)
    for layer in layers:
        for name, value in layer.exclusive_breakdown_s.items():
            totals[name] += value
    return AnalyticEstimate(
        estimator_id="compiler_circuit_lower_bound",
        total_latency_s=sum(totals.values()),
        exclusive_breakdown_s=dict(sorted(totals.items())),
        layers=layers,
        assumptions={
            "architecture_resources": "instantly_ready",
            "included_program_operations": [
                "EXECUTE_COMPUTE",
                "CLASSICAL_REACTION",
            ],
            "compiler_binding": "canonical_buffer_slots_without_runtime_state",
            "role": "reference_lower_bound_not_primary_baseline",
        },
        plan_hash=plan.plan_hash,
    )


def estimate_static_layerwise_aggregation(
    plan: ExecutionPlan,
    *,
    runtime_instruction_compiler: RuntimeInstructionCompiler | None = None,
    runtime_resource_compiler: RuntimeResourceCompiler | None = None,
) -> AnalyticEstimate:
    """Aggregate matched component costs independently within each FT layer."""

    program: defaultdict[int, defaultdict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    buffer_kinds = {buffer.id: buffer.token_kind for buffer in plan.buffers}
    magic_input_buffers = {
        name for name, kind in buffer_kinds.items() if kind == "magic_state"
    }
    magic_demand: defaultdict[int, int] = defaultdict(int)
    program_bell_demand: defaultdict[int, int] = defaultdict(int)
    for command in plan.program_dag.instructions:
        if command.layer_index is None:
            continue
        category = _program_category(command.opcode)
        if category is not None:
            program[command.layer_index][category] += _nominal_command_duration(
                command, plan, runtime_instruction_compiler
            )
        if command.opcode == ArchitectureOpcode.EXECUTE_COMPUTE:
            magic_demand[command.layer_index] += sum(
                int(amount)
                for name, amount in command.consumes.items()
                if name in magic_input_buffers
            )
        if command.opcode == ArchitectureOpcode.TELEPORT_QUBITS:
            program_bell_demand[command.layer_index] += sum(
                int(amount)
                for name, amount in command.consumes.items()
                if name.startswith("bell:")
            )

    magic_producer = _resource_process(
        plan, ArchitectureOpcode.PREPARE_MAGIC_STATE
    )
    bell_producer = _resource_process(
        plan, ArchitectureOpcode.PREPARE_LOGICAL_BELL
    )
    delivery = _magic_delivery_process(plan, magic_input_buffers)
    magic_rate = _resource_rate(magic_producer) if magic_producer else math.inf
    bell_rate = _resource_rate(bell_producer) if bell_producer else math.inf
    remote_magic = bool(
        delivery is not None
        and delivery.opcode == ArchitectureOpcode.TELEPORT_QUBITS
        and any(name.startswith("bell:") for name in delivery.consumes)
    )

    layers: list[LayerEstimate] = []
    all_layers = sorted(set(program) | set(magic_demand) | set(program_bell_demand))
    for layer in all_layers:
        magic = magic_demand[layer]
        bell = program_bell_demand[layer] + (magic if remote_magic else 0)
        if magic and magic_producer is None:
            raise ValueError("Program demands magic states but no producer exists")
        if bell and bell_producer is None:
            raise ValueError("Program demands Bell pairs but no producer exists")
        magic_time = magic / magic_rate if magic else 0.0
        bell_time = bell / bell_rate if bell else 0.0

        parts = defaultdict(float, program[layer])
        acquisition = max(magic_time, bell_time)
        if acquisition:
            if bell_time > magic_time:
                parts["resource_acquisition_bell"] += acquisition
            elif magic_time > bell_time:
                parts["resource_acquisition_magic"] += acquisition
            else:
                parts["resource_acquisition_tied"] += acquisition
        delivery_time = _nominal_delivery_duration(
            delivery,
            magic,
            plan,
            runtime_resource_compiler,
        )
        if delivery_time:
            parts["magic_delivery"] += delivery_time
        layers.append(
            LayerEstimate(
                layer_index=layer,
                total_latency_s=sum(parts.values()),
                exclusive_breakdown_s=dict(sorted(parts.items())),
                inclusive_resource_production_s={
                    "magic_state_production": magic_time,
                    "logical_bell_production": bell_time,
                },
                resource_demand={
                    "magic_states": magic,
                    "logical_bell_pairs": bell,
                    "logical_bell_pairs_for_program_data": program_bell_demand[
                        layer
                    ],
                    "logical_bell_pairs_for_remote_magic": (
                        magic if remote_magic else 0
                    ),
                },
            )
        )

    totals: defaultdict[str, float] = defaultdict(float)
    for layer in layers:
        for name, value in layer.exclusive_breakdown_s.items():
            totals[name] += value
    total = sum(totals.values())
    if not math.isclose(
        total,
        sum(layer.total_latency_s for layer in layers),
        rel_tol=1e-12,
        abs_tol=1e-12,
    ):
        raise AssertionError("Static layer aggregation does not conserve latency")
    return AnalyticEstimate(
        estimator_id="static_layerwise_aggregation",
        total_latency_s=total,
        exclusive_breakdown_s=dict(sorted(totals.items())),
        layers=tuple(layers),
        assumptions={
            "component_coverage": "matched_to_arqsim_execution_plan",
            "resource_production": "fluid_mean_rate",
            "resource_state": "not_tracked",
            "inter_layer_inventory": "not_carried",
            "buffer_backpressure": "not_modeled",
            "resource_production_overlap": "per_layer_max_magic_vs_bell",
            "program_actions": "compiler_grounded_deterministic_costs",
            "compiler_binding": "canonical_buffer_slots_without_runtime_state",
            "layer_order": "strict_ft_layer_sequence",
        },
        plan_hash=plan.plan_hash,
    )
