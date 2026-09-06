from __future__ import annotations

from dataclasses import replace
import math
from types import SimpleNamespace

import pytest

from arqsim.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    ResourceMoveDispatchRecipe,
)
from arqsim.evaluation import (
    BufferSpec,
    EngineSpec,
    EvaluationPolicy,
    ExecutionPlan,
    ExecutionTrace,
    ProgramDAG,
    ResourceDAG,
    ResourceProcess,
    RuntimeInjectionMode,
    buffer_occupancy_statistics,
    compile_and_lower,
    estimate_compiler_circuit_lower_bound,
    estimate_fidelity,
    estimate_static_layerwise_aggregation,
    engine_utilization,
    evaluate,
    exclusive_time_breakdown,
)
from arqsim.evaluation.components import (
    RuntimeComponentDescriptor,
    StateBoundRuntimeRealizer,
    build_runtime_component_set,
    default_direct_runtime_component_manifest,
    default_runtime_component_manifest,
)
from arqsim.compiler import canonical_compiler_spec
from arqsim.operation_profiles import (
    ArrivalDistribution,
    FidelityProfile,
    OperationLatencyProfile,
    ResourceStateFidelityModel,
    resolve_resource_protocol_bindings,
    with_effective_arrivals,
)
from arqsim.program import FTCircuit, LogicalLayer, LogicalOperation
from arqsim.schema import normalize_json
from arqsim.specification import build_architecture_specification


def _compile_plan(*args, **kwargs) -> ExecutionPlan:
    return compile_and_lower(*args, **kwargs)[1]


def _cold_start_plan() -> ExecutionPlan:
    program = ProgramDAG(
        (
            ArchitectureInstruction(
                0,
                ArchitectureOpcode.EXECUTE_COMPUTE,
                duration_s=0.5,
                layer_index=0,
                qubits=(0,),
                consumes={"magic": 1},
                engines={"compute": 1},
                target_modules=("compute",),
                metadata={"gates": {"h": [0]}},
            ),
        )
    )
    resources = ResourceDAG(
        (
            ResourceProcess(
                "factory",
                ArchitectureOpcode.PREPARE_MAGIC_STATE,
                produces={"magic": 1},
                engines={"factory": 1},
                duration_s=1.0,
                target_modules=("msf",),
            ),
        )
    )
    return ExecutionPlan(
        "circuit", "architecture", "latency",
        EvaluationPolicy(seed=3, trace_level="full"),
        program,
        resources,
        (BufferSpec("magic", 1, "magic_state", module="compute"),),
        (EngineSpec("compute"), EngineSpec("factory")),
        initial_locations={"q:0": "compute"},
    )


def test_cold_start_couples_resource_and_program_dags_through_state() -> None:
    result = evaluate(_cold_start_plan())
    assert result.total_latency_s == pytest.approx(1.5)
    assert [event.plane.value for event in result.events] == ["resource", "program"]
    assert result.events[1].consumed_tokens["magic"]
    assert result.program_state_blocked_s == pytest.approx(1.0)
    assert all(result.invariant_checks.values())
    assert exclusive_time_breakdown(result) == pytest.approx(
        {"compute": 0.5, "magic_state_supply_stall": 1.0}
    )
    assert engine_utilization(result, _cold_start_plan()) == pytest.approx(
        # The second factory reservation is still running when the Program
        # horizon closes. Its observed 1.0--1.5 prefix still occupies the
        # factory, so causal dispatch accounting gives full utilization.
        {"compute": 1 / 3, "factory": 1.0}
    )
    assert buffer_occupancy_statistics(result, _cold_start_plan())["magic"] == pytest.approx(
        {
            "capacity": 1.0,
            "mean_ready": 0.0,
            "mean_committed": 1.0,
            "ready_utilization": 0.0,
            "committed_utilization": 1.0,
            "ready_empty_fraction": 1.0,
            "committed_full_fraction": 1.0,
        }
    )


def test_engine_owner_metadata_does_not_affect_scheduling_or_timing() -> None:
    unowned = _cold_start_plan()
    owned = replace(
        unowned,
        engines=tuple(
            replace(
                engine,
                module=f"node/{engine.id}_module",
                submodule=f"node/{engine.id}_module/{engine.id}_engine",
            )
            for engine in unowned.engines
        ),
    )

    assert owned.plan_hash != unowned.plan_hash
    unowned_result = evaluate(unowned)
    owned_result = evaluate(owned)
    assert owned_result.total_latency_s == unowned_result.total_latency_s
    assert normalize_json(owned_result.discrete_time_log) == normalize_json(
        unowned_result.discrete_time_log
    )
    assert exclusive_time_breakdown(owned_result) == exclusive_time_breakdown(
        unowned_result
    )
    assert engine_utilization(owned_result, owned) == engine_utilization(
        unowned_result,
        unowned,
    )


def test_remote_magic_wait_is_attributed_to_upstream_bell_and_delivery() -> None:
    def point(
        time_s: float,
        *,
        waiting: bool = False,
        running: tuple[dict[str, object], ...] = (),
        magic_ready: int = 0,
        bell_ready: int = 0,
    ) -> dict[str, object]:
        return {
            "time_s": time_s,
            "architecture_state_after": {
                "buffers": {
                    "magic_compute": {"ready": 0, "pending_incoming": 0},
                    "msf_output": {"ready": magic_ready, "pending_incoming": 0},
                    "bell:link": {"ready": bell_ready, "pending_incoming": 0},
                }
            },
            "frontier_after": {
                "waiting": (
                    [
                        {
                            "instruction_id": 0,
                            "opcode": "EXECUTE_COMPUTE",
                            "resource_blockers": [
                                "buffer_empty:magic_compute:0/1"
                            ],
                        }
                    ]
                    if waiting
                    else []
                ),
                "running": list(running),
            },
        }

    result = SimpleNamespace(
        total_latency_s=4.0,
        discrete_time_log=(
            point(
                0.0,
                waiting=True,
                magic_ready=1,
                running=(
                    {
                        "plane": "resource",
                        "opcode": "PREPARE_LOGICAL_BELL",
                        "process_id": "prepare_logical_bell:link",
                        "completes_s": 2.0,
                    },
                ),
            ),
            point(
                2.0,
                waiting=True,
                running=(
                    {
                        "plane": "resource",
                        "opcode": "TELEPORT_QUBITS",
                        "process_id": "deliver_magic_remote",
                        "completes_s": 3.0,
                    },
                ),
            ),
            point(
                3.0,
                running=(
                    {
                        "plane": "program",
                        "opcode": "EXECUTE_COMPUTE",
                        "process_id": None,
                        "completes_s": 4.0,
                    },
                ),
            ),
            point(4.0),
        ),
    )
    assert exclusive_time_breakdown(result) == pytest.approx(
        {
            "bell_pair_supply_stall": 2.0,
            "resource_delivery_stall": 1.0,
            "compute": 1.0,
        }
    )


def test_matched_analytic_references_separate_circuit_and_resource_cost() -> None:
    plan = _cold_start_plan()
    circuit = estimate_compiler_circuit_lower_bound(plan)
    static = estimate_static_layerwise_aggregation(plan)

    assert circuit.total_latency_s == pytest.approx(0.5)
    assert static.total_latency_s == pytest.approx(1.5)
    assert static.exclusive_breakdown_s == pytest.approx(
        {"compute": 0.5, "resource_acquisition_magic": 1.0}
    )
    assert static.layers[0].resource_demand["magic_states"] == 1


def test_static_layerwise_aggregation_overlaps_magic_and_bell_production() -> None:
    plan = ExecutionPlan(
        "circuit", "architecture", "latency",
        EvaluationPolicy(),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=0.5,
                    layer_index=0,
                    consumes={"magic": 1},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    produces={"factory_output": 1},
                    arrival_distribution=ArrivalDistribution(
                        kind="deterministic", mean_interval_s=2.0
                    ),
                ),
                ResourceProcess(
                    "bell",
                    ArchitectureOpcode.PREPARE_LOGICAL_BELL,
                    produces={"bell:link": 1},
                    arrival_distribution=ArrivalDistribution(
                        kind="deterministic", mean_interval_s=3.0
                    ),
                ),
                ResourceProcess(
                    "delivery",
                    ArchitectureOpcode.TELEPORT_QUBITS,
                    consumes={"factory_output": 1, "bell:link": 1},
                    produces={"magic": 1},
                    forwards={"factory_output": "magic"},
                    duration_s=0.1,
                    target_links=("link",),
                ),
            )
        ),
        (
            BufferSpec("magic", 1, "magic_state"),
            BufferSpec("factory_output", 1, "magic_state"),
            BufferSpec("bell:link", 1, "logical_bell"),
        ),
        (),
    )

    static = estimate_static_layerwise_aggregation(plan)
    assert static.total_latency_s == pytest.approx(3.6)
    assert static.exclusive_breakdown_s == pytest.approx(
        {
            "compute": 0.5,
            "magic_delivery": 0.1,
            "resource_acquisition_bell": 3.0,
        }
    )


def test_multi_output_factory_completion_produces_one_synchronized_batch() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(seed=3, trace_level="full"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=0.5,
                    consumes={"magic": 4},
                    engines={"compute": 1},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory_20_to_4",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    produces={"magic": 4},
                    engines={"factory": 1},
                    duration_s=1.0,
                    parallelism=1,
                    metadata={"outputs_per_batch": 4},
                ),
            )
        ),
        (BufferSpec("magic", 4, "magic_state"),),
        (EngineSpec("compute"), EngineSpec("factory")),
    )

    result = evaluate(plan)
    preparations = [
        event
        for event in result.events
        if event.opcode == ArchitectureOpcode.PREPARE_MAGIC_STATE
    ]
    assert len(preparations) == 1
    assert len(preparations[0].produced_tokens["magic"]) == 4
    assert result.total_latency_s == pytest.approx(1.5)
    assert result.metrics["resource_items_started"]["factory_20_to_4"] == 2


def test_logical_bell_batch_output_does_not_force_buffer_capacity() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(0, (LogicalOperation("gate", "t", qubits=(0,)),)),
        ),
    )
    specification = build_architecture_specification(circuit, "1.3")
    bindings = resolve_resource_protocol_bindings(
        specification,
        OperationLatencyProfile(),
    )
    latency = with_effective_arrivals(
        OperationLatencyProfile(),
        bindings,
    )
    plan = _compile_plan(
        circuit,
        specification,
        latency,
        EvaluationPolicy(),
        compiler_spec=canonical_compiler_spec(specification),
        resource_protocol_bindings=bindings,
    )

    bell_process = next(
        process
        for process in plan.resource_dag.processes
        if process.opcode == ArchitectureOpcode.PREPARE_LOGICAL_BELL
    )
    bell_buffer_id = next(iter(bell_process.produces))
    bell_buffer = next(buffer for buffer in plan.buffers if buffer.id == bell_buffer_id)
    bell_engine_id = next(iter(bell_process.engines))
    bell_engine = next(engine for engine in plan.engines if engine.id == bell_engine_id)

    assert bell_process.produces[bell_buffer_id] == 1
    assert bell_process.metadata["outputs_per_batch"] == 1
    expected_copies = bindings.logical_bell_pair.copies
    canonical_bell_owner = next(
        (interconnect, module, submodule)
        for interconnect in specification.interconnects
        for module in interconnect.modules
        for submodule in module.submodules
        if submodule.type == "buffer" and submodule.payload == "bell_pair"
    )
    interconnect, module, canonical_bell_buffer = canonical_bell_owner
    assert expected_copies == bell_buffer.capacity
    assert bell_process.parallelism == expected_copies
    assert bell_process.output_overflow_policy == "discard_excess"
    assert bell_buffer.capacity == canonical_bell_buffer.capacity
    assert bell_buffer.module == f"{interconnect.id}/{module.id}"
    assert bell_buffer.submodule == (
        f"{interconnect.id}/{module.id}/{canonical_bell_buffer.id}"
    )
    assert bell_buffer.slots == tuple(
        f"{bell_buffer.submodule}/{slot.id}" for slot in canonical_bell_buffer.slots
    )
    assert bell_buffer.capacity >= 1
    assert bell_engine.capacity == expected_copies


def test_resource_delivery_channel_override_wires_engine_and_process() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(0, (LogicalOperation("gate", "t", qubits=(0,)),)),
        ),
    )
    specification = build_architecture_specification(circuit, "1.3")
    bindings = resolve_resource_protocol_bindings(
        specification,
        OperationLatencyProfile(),
    )
    latency = with_effective_arrivals(
        OperationLatencyProfile(),
        bindings,
    )
    plan = _compile_plan(
        circuit,
        specification,
        latency,
        EvaluationPolicy(),
        compiler_spec=canonical_compiler_spec(specification),
        resource_protocol_bindings=bindings,
        resource_delivery_channels=3,
    )

    delivery_engine = next(
        engine for engine in plan.engines if engine.id == "resource_move"
    )
    delivery_process = next(
        process
        for process in plan.resource_dag.processes
        if process.id == "deliver_magic_remote"
    )
    assert delivery_engine.capacity == 3
    assert delivery_process.parallelism == 3
    assert plan.provenance["resource_delivery_channels"] == 3


@pytest.mark.parametrize(
    "prepare_opcode",
    (
        ArchitectureOpcode.PREPARE_MAGIC_STATE,
        ArchitectureOpcode.PREPARE_LOGICAL_BELL,
    ),
)
def test_protocol_batch_fills_available_slots_and_discards_excess(
    prepare_opcode: ArchitectureOpcode,
) -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(seed=3, trace_level="full"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=0.5,
                    consumes={"magic": 2},
                    engines={"compute": 1},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory_20_to_4",
                    prepare_opcode,
                    produces={"magic": 4},
                    engines={"factory": 1},
                    duration_s=1.0,
                    output_overflow_policy="discard_excess",
                    metadata={"outputs_per_batch": 4},
                ),
            )
        ),
        (BufferSpec("magic", 2, "magic_state"),),
        (EngineSpec("compute"), EngineSpec("factory")),
    )

    original_plan = plan.to_dict()
    result = evaluate(plan)
    assert plan.to_dict() == original_plan
    preparation = next(
        event
        for event in result.events
        if event.opcode == prepare_opcode
    )
    assert len(preparation.produced_tokens["magic"]) == 2
    assert preparation.metadata["attempted_outputs"] == {"magic": 4}
    assert preparation.metadata["buffered_outputs"] == {"magic": 2}
    assert preparation.metadata["discarded_outputs"] == {"magic": 2}
    assert result.metrics["buffer_tokens_produced"] == {"magic": 2}
    assert result.metrics["buffer_tokens_discarded"] == {"magic": 2}
    assert all(result.invariant_checks.values())


def test_full_trace_records_discrete_program_and_resource_frontier() -> None:
    result = evaluate(_cold_start_plan())
    assert [item["time_s"] for item in result.discrete_time_log] == [0.0, 1.0, 1.5]

    initial = result.discrete_time_log[0]
    assert normalize_json(initial["frontier_after"]["waiting"]) == [
        {
            "instruction_id": 0,
            "opcode": "EXECUTE_COMPUTE",
            "program_ready": True,
            "resource_ready": False,
            "resource_blockers": ["buffer_empty:magic:0/1"],
            "program_ready_s": 0.0,
        }
    ]
    assert initial["frontier_after"]["running"][0]["process_id"] == "factory"

    resource_completion = result.discrete_time_log[1]
    assert resource_completion["completed"][0]["opcode"] == "PREPARE_MAGIC_STATE"
    assert resource_completion["dispatched"][0]["opcode"] == "EXECUTE_COMPUTE"
    assert resource_completion["dispatched"][0]["program_ready"] is True
    assert resource_completion["dispatched"][0]["resource_ready"] is True

    final = result.discrete_time_log[-1]
    assert final["frontier_after"]["running"][0]["process_id"] == "factory"
    assert final["frontier_after"]["running"][0]["completes_s"] == pytest.approx(2.0)
    assert final["frontier_after"]["completed_now"]


def test_fidelity_uses_realized_operations_and_idle_exposure() -> None:
    plan = _cold_start_plan()
    result = evaluate(plan)
    profile = FidelityProfile(
        operation_failure_probability={"EXECUTE_COMPUTE": 0.1},
        idle_failure_rate_per_s={"compute": 0.02},
    )
    estimate = estimate_fidelity(result, profile, plan=plan)
    assert 0.0 < estimate.success_probability < 0.9
    assert "EXECUTE_COMPUTE" in estimate.log_success_by_operation
    assert "compute" in estimate.log_success_by_idle_location


def test_fidelity_can_apply_architecture_failure_per_logical_qubit() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.STORE_QUBITS,
                    duration_s=0.5,
                    qubits=(3, 7),
                ),
            )
        ),
        ResourceDAG(()),
        (),
        (),
    )
    probability = 2.3e-6
    profile = FidelityProfile(
        operation_failure_models={
            "STORE_QUBITS": {
                "kind": "independent_per_logical_qubit",
                "failure_probability": probability,
            }
        }
    )
    result = evaluate(plan)
    estimate = estimate_fidelity(result, profile, plan=plan)
    assert estimate.log_success_by_operation["STORE_QUBITS"] == pytest.approx(
        2 * math.log1p(-probability)
    )
    assert estimate.success_probability == pytest.approx((1 - probability) ** 2)

    altered = replace(result, trace=replace(
        result.trace,
        transitions=tuple(
            replace(item, metadata={**dict(item.metadata), "qubits": [99], "amount": 100})
            for item in result.transitions
        ),
    ))
    assert estimate_fidelity(altered, profile, plan=plan).to_dict() == estimate.to_dict()
    with pytest.raises(ValueError, match="matching ExecutionPlan"):
        estimate_fidelity(result, profile)

    empty_payload = replace(plan, program_dag=ProgramDAG((
        replace(plan.program_dag.instructions[0], qubits=(), metadata={"amount": 2}),
    )))
    with pytest.raises(ValueError, match="non-empty typed item payload"):
        estimate_fidelity(evaluate(empty_payload), profile, plan=empty_payload)


def test_teleport_fidelity_keeps_bell_quality_and_cnot_as_named_channels() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.TELEPORT_QUBITS,
                    duration_s=0.5,
                    qubits=(3, 7),
                    consumes={"bell:link": 2},
                    required_locations={"q:3": "left", "q:7": "left"},
                    completion_locations={"q:3": "right", "q:7": "right"},
                    target_links=("link",),
                ),
            )
        ),
        ResourceDAG(()),
        (
            BufferSpec(
                "bell:link",
                2,
                "logical_bell_pair",
                initial_contents=("b0", "b1"),
            ),
        ),
        (),
        initial_locations={"q:3": "left", "q:7": "left"},
    )
    bell_probability = 1e-10
    cnot_probability = 2e-8
    profile = FidelityProfile(
        operation_failure_models={
            "TELEPORT_QUBITS": {
                "kind": "independent_channels_per_teleported_item",
                "channels": {
                    "logical_bell_output": bell_probability,
                    "transversal_cnot": cnot_probability,
                },
            }
        }
    )
    estimate = estimate_fidelity(evaluate(plan), profile, plan=plan)
    assert estimate.log_success_by_operation[
        "TELEPORT_QUBITS:logical_bell_output"
    ] == pytest.approx(2 * math.log1p(-bell_probability))
    assert estimate.log_success_by_operation[
        "TELEPORT_QUBITS:transversal_cnot"
    ] == pytest.approx(2 * math.log1p(-cnot_probability))


def test_fidelity_counts_logical_gates_and_idle_qec_cycles_exactly() -> None:
    plan = _cold_start_plan()
    result = evaluate(plan)
    profile = FidelityProfile(
        operation_failure_probability={
            "EXECUTE_COMPUTE": 0.0,
            "PREPARE_MAGIC_STATE": 0.0,
        },
        logical_operation_failure_probability={"h": 0.1},
        idle_failure_probability_per_cycle={"compute": 0.01},
        idle_cycle_time_s={"compute": 0.25},
        resource_state_models={
            "magic_state": ResourceStateFidelityModel(
                resource_kind="magic_state",
                output_failure_probability=0.0,
            )
        },
    )
    estimate = estimate_fidelity(result, profile, plan=plan)
    assert estimate.logical_operation_counts == {"h": 1}
    assert estimate.idle_cycles_by_location["compute"] == pytest.approx(4.0)
    assert estimate.success_probability == pytest.approx(0.9 * 0.99**4)
    assert estimate.complete_coverage


def test_fidelity_applies_parameterized_ppm_failure_to_each_realized_weight() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=0.5,
                    metadata={"gates": {"m_pauli": [0, [1, 2, 3]]}},
                ),
            )
        ),
        ResourceDAG(()),
        (),
        (),
    )
    profile = FidelityProfile(
        operation_failure_probability={"EXECUTE_COMPUTE": 0.0},
        logical_operation_failure_models={
            "m_pauli": {
                "kind": "unrotated_surface_ppm_parity",
                "distance": 1,
                "weight_one_fit": {
                    "log10_distance_coefficient": 0.0,
                    "log10_intercept": -3.0,
                },
                "multi_patch_fit": {
                    "log10_distance_coefficient": 0.0,
                    "log10_effective_weight_coefficient": 1.0,
                    "log10_intercept": -2.0,
                },
            }
        }
    )
    estimate = estimate_fidelity(evaluate(plan), profile)
    assert estimate.logical_operation_counts == {"m_pauli": 2}
    # Weight one contributes hazard 1e-3; weight three maps to paired weight
    # four and contributes hazard 4e-2.  The bounded-hazard model makes their
    # joint log success exactly the negative sum of those hazards.
    assert estimate.log_success_by_logical_operation["m_pauli"] == pytest.approx(
        -0.041
    )
    assert estimate.complete_coverage


def test_plan_round_trip_preserves_semantic_hash() -> None:
    plan = _cold_start_plan()
    payload = plan.to_dict()
    assert payload["schema_version"] == "arqsim.execution-plan.v9"
    assert payload["architecture_hash"] == plan.architecture_hash
    assert "system" not in payload
    assert "qec_hash" not in payload

    restored = ExecutionPlan.from_json(plan.to_json())
    assert restored.plan_hash == plan.plan_hash

    obsolete = normalize_json(payload)
    obsolete["schema_version"] = "arqsim.execution-plan.v5"
    with pytest.raises(ValueError, match="Unsupported execution-plan schema"):
        ExecutionPlan.from_dict(obsolete)

    widened = normalize_json(payload)
    widened["legacy_system"] = {}
    with pytest.raises(ValueError, match="Unknown execution-plan fields"):
        ExecutionPlan.from_dict(widened)

    with pytest.raises(ValueError, match="must contain an object"):
        ExecutionPlan.from_json("[]")

    with pytest.raises(ValueError, match="Non-finite JSON constant"):
        ExecutionPlan.from_json('{"duration": NaN}')


def test_immutable_plan_and_trace_cache_semantic_hashes(monkeypatch) -> None:
    import arqsim.evaluation.plan as plan_module
    import arqsim.evaluation.result as result_module

    plan_hash_calls = 0
    original_plan_hash = plan_module.semantic_hash

    def count_plan_hash(value) -> str:
        nonlocal plan_hash_calls
        plan_hash_calls += 1
        return original_plan_hash(value)

    monkeypatch.setattr(plan_module, "semantic_hash", count_plan_hash)
    plan = _cold_start_plan()
    assert plan_hash_calls == 1
    assert plan.plan_hash == plan.plan_hash
    plan.to_dict()
    assert plan_hash_calls == 1

    trace_hash_calls = 0
    original_trace_hash = result_module.semantic_hash

    def count_trace_hash(value) -> str:
        nonlocal trace_hash_calls
        trace_hash_calls += 1
        return original_trace_hash(value)

    monkeypatch.setattr(result_module, "semantic_hash", count_trace_hash)
    trace = ExecutionTrace(
        plan_hash="plan-hash",
        seed=0,
        total_latency_s=0.0,
    )
    assert trace_hash_calls == 1
    assert trace.trace_hash == trace.trace_hash
    trace.to_dict()
    assert trace_hash_calls == 1


def test_t_reaction_is_a_runtime_recipe_not_an_unconditional_static_node() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(0, (LogicalOperation("gate", "t", qubits=(0,)),)),
            LogicalLayer(1, (LogicalOperation("gate", "t", qubits=(0,)),)),
        ),
    )
    specification = build_architecture_specification(circuit, "1.1")
    latency = OperationLatencyProfile(
        reaction_latency_by_modality_s={"neutral_atom": 10e-6}
    )
    bindings = resolve_resource_protocol_bindings(specification, latency)
    latency = with_effective_arrivals(
        latency,
        bindings,
    )
    compiler = canonical_compiler_spec(specification)

    plan = _compile_plan(
        circuit,
        specification,
        latency,
        EvaluationPolicy(
            runtime_injection_mode=RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
        ),
        compiler_spec=compiler,
        resource_protocol_bindings=bindings,
    )
    reactions = [
        item
        for item in plan.program_dag.instructions
        if item.opcode == ArchitectureOpcode.CLASSICAL_REACTION
    ]
    assert reactions == []
    reaction_templates = [
        template
        for instruction in plan.program_dag.instructions
        for template in instruction.continuation_templates
        if template.step == "reaction"
    ]
    assert len(reaction_templates) == 2
    assert all(
        template.duration_s == pytest.approx(10e-6)
        for template in reaction_templates
    )


def test_eager_resource_move_batches_ready_tokens_and_reserves_destinations() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(seed=0, trace_level="full"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=0.5,
                    consumes={"destination": 3},
                    engines={"compute": 1},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "move_magic",
                    ArchitectureOpcode.MOVE_QUBITS,
                    consumes={"source": 1},
                    produces={"destination": 1},
                    forwards={"source": "destination"},
                    engines={"move": 1},
                    dispatch_policy="eager_available",
                    deferred_dispatch=ResourceMoveDispatchRecipe(),
                ),
            )
        ),
        (
            BufferSpec(
                "source",
                3,
                "magic_state",
                slots=("S0", "S1", "S2"),
                initial_contents=("m0", "m1", "m2"),
            ),
            BufferSpec(
                "destination",
                3,
                "magic_state",
                slots=("D0", "D1", "D2"),
            ),
        ),
        (EngineSpec("compute"), EngineSpec("move")),
        runtime_components=default_runtime_component_manifest().to_dict(),
    )

    compiler_calls = []

    def compile_move(request):
        compiler_calls.append(request)
        return {"duration_s": 0.25, "schedule": "test_batch"}

    components = build_runtime_component_set(
        default_direct_runtime_component_manifest(),
        runtime_instruction_compiler=None,
        runtime_resource_compiler=None,
    )
    components = replace(
        components,
        runtime_realizer=StateBoundRuntimeRealizer(
            RuntimeComponentDescriptor(
                "runtime_realizer",
                "tests.runtime_realizer.batch_move.v1",
                provider="tests",
            ),
            None,
            compile_move,
        ),
    )
    plan = replace(
        plan,
        provenance={},
        runtime_components=components.manifest.to_dict(),
    )
    result = evaluate(plan, runtime_components=components)
    move = result.events[0]
    assert move.opcode == ArchitectureOpcode.MOVE_QUBITS
    assert move.end_s - move.start_s == pytest.approx(0.25)
    assert move.metadata["batch_amount"] == 3
    assert move.metadata["schedule"] == "test_batch"
    assert move.consumed_slots["source"] == ("S0", "S1", "S2")
    assert move.produced_slots["destination"] == ("D0", "D1", "D2")
    assert len(compiler_calls) == 1
    assert result.metrics["resource_instances_started"]["move_magic"] == 1
    assert result.metrics["resource_items_started"]["move_magic"] == 3
    assert result.total_latency_s == pytest.approx(0.75)
    assert all(result.invariant_checks.values())
