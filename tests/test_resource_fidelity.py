from __future__ import annotations

import math
from dataclasses import replace

import pytest

from arqsim.architecture.isa import ArchitectureInstruction, ArchitectureOpcode
from arqsim.evaluation import (
    BufferSpec,
    EngineSpec,
    EvaluationPolicy,
    ExecutionPlan,
    ProgramDAG,
    ResourceDAG,
    ResourceProcess,
    estimate_fidelity,
    evaluate,
    resource_token_ledger,
)
from arqsim.operation_profiles import (
    ArrivalDistribution,
    FidelityProfile,
    ResolvedResourceProtocolBinding,
    ResolvedResourceProtocolBindings,
    ResourceBufferFidelityModel,
    ResourceStateFidelityModel,
)


def _magic_buffer_model(location: str) -> ResourceBufferFidelityModel:
    return ResourceBufferFidelityModel(
        location=location,
        owner_kind="node",
        qec_code="surface",
        qec_parameters={"distance": 13},
        logical_qubits_per_token=1,
        idle_failure_probability_per_cycle=0.01,
        idle_cycle_time_s=1.0,
    )


def test_resource_fidelity_models_round_trip_with_hash_stability() -> None:
    location = "node/factory/output"
    profile = FidelityProfile(
        resource_state_models={
            "magic_state": ResourceStateFidelityModel(
                resource_kind="magic_state",
                output_failure_probability=0.125,
                protocol_id="magic-proto",
                protocol_profile_hash="a" * 64,
                buffer_idle_models={location: _magic_buffer_model(location)},
            )
        },
        provenance={"accounting": "consumed-resource-ledger"},
    )

    restored = FidelityProfile.from_dict(profile.to_dict())

    assert restored.to_dict() == profile.to_dict()
    assert restored.profile_hash == profile.profile_hash
    assert isinstance(
        restored.resource_state_models["magic_state"],
        ResourceStateFidelityModel,
    )
    assert isinstance(
        restored.resource_state_models["magic_state"].buffer_idle_models[location],
        ResourceBufferFidelityModel,
    )


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("location", 7),
        ("owner_kind", ["node"]),
        ("qec_code", None),
        ("logical_qubits_per_token", True),
        ("idle_failure_probability_per_cycle", False),
        ("idle_failure_probability_per_cycle", "0.01"),
        ("idle_cycle_time_s", True),
        ("idle_cycle_time_s", "1.0"),
    ),
)
def test_resource_buffer_fidelity_codec_rejects_coerced_primitives(
    field: str,
    invalid: object,
) -> None:
    record = _magic_buffer_model("node/factory/output").to_dict()
    record[field] = invalid

    with pytest.raises(TypeError):
        ResourceBufferFidelityModel.from_dict(record)


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("resource_kind", 7),
        ("output_failure_probability", False),
        ("output_failure_probability", "0.1"),
        ("protocol_id", 7),
        ("protocol_profile_hash", 7),
    ),
)
def test_resource_state_fidelity_codec_rejects_coerced_primitives(
    field: str,
    invalid: object,
) -> None:
    record = ResourceStateFidelityModel(
        resource_kind="magic_state",
        output_failure_probability=0.1,
    ).to_dict()
    record[field] = invalid

    with pytest.raises(TypeError):
        ResourceStateFidelityModel.from_dict(record)


def _forwarded_magic_plan() -> ExecutionPlan:
    return ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="summary"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=5.0,
                    engines={"compute": 1},
                    metadata={"gates": {"h": [0]}},
                ),
                ArchitectureInstruction(
                    1,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    predecessor_ids=(0,),
                    duration_s=0.5,
                    consumes={"magic_compute": 1},
                    engines={"compute": 1},
                    metadata={"gates": {"t": [0]}},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    duration_s=1.0,
                    produces={"msf_output": 1},
                    engines={"factory": 1},
                    output_overflow_policy="discard_excess",
                    protocol="magic-proto",
                ),
                ResourceProcess(
                    "deliver",
                    ArchitectureOpcode.MOVE_QUBITS,
                    duration_s=2.0,
                    consumes={"msf_output": 1},
                    produces={"magic_compute": 1},
                    forwards={"msf_output": "magic_compute"},
                    engines={"move": 1},
                    protocol="delivery",
                ),
            )
        ),
        (
            BufferSpec(
                "msf_output",
                1,
                "magic_state",
                module="factory_node/factory",
                submodule="factory_node/factory/output",
            ),
            BufferSpec(
                "magic_compute",
                1,
                "magic_state",
                module="compute_node/compute",
                submodule="compute_node/compute/input",
            ),
        ),
        (EngineSpec("compute"), EngineSpec("factory"), EngineSpec("move")),
    )


def _magic_protocol_bindings(
    *,
    profile_hash: str = "a" * 64,
    output_error_probability: float = 0.1,
) -> ResolvedResourceProtocolBindings:
    arrival = ArrivalDistribution(kind="deterministic", mean_interval_s=1.0)
    binding = ResolvedResourceProtocolBinding(
        resource_kind="magic_state",
        requested_protocol_id="magic-proto",
        protocol_id="magic-proto",
        protocol_family=None,
        protocol_profile_hash=profile_hash,
        copies=1,
        outputs_per_copy_per_batch=1,
        base_arrival_distribution=arrival,
        effective_arrival_distribution=arrival,
        arrival_source="protocol_profile",
        output_error_probability=output_error_probability,
        output_fidelity=1.0 - output_error_probability,
    )
    return ResolvedResourceProtocolBindings({"magic_state": binding})


def _with_magic_protocol_authority(
    plan: ExecutionPlan,
    *,
    bindings: ResolvedResourceProtocolBindings | None = None,
    recorded_hash: str | None = None,
) -> ExecutionPlan:
    selected = bindings or _magic_protocol_bindings()
    return replace(
        plan,
        provenance={
            "resource_protocol_bindings": selected.to_dict(),
            "resource_protocol_bindings_hash": (
                selected.bindings_hash if recorded_hash is None else recorded_hash
            ),
        },
    )


def test_consumed_magic_ledger_preserves_forwarding_and_excludes_active_transit() -> None:
    plan = _with_magic_protocol_authority(_forwarded_magic_plan())
    result = evaluate(plan)
    ledger = resource_token_ledger(result, plan)

    assert len(ledger.consumed_tokens) == 1
    token = ledger.consumed_tokens[0]
    assert token.resource_kind == "magic_state"
    assert token.producer_protocol_id == "magic-proto"
    assert [interval.buffer_id for interval in token.residence_intervals] == [
        "msf_output",
        "magic_compute",
    ]
    # Production [0,1] and delivery [1,3] are active.  Only ready-buffer
    # residence is idle: zero seconds at the source and two at compute input.
    assert [interval.duration_s for interval in token.residence_intervals] == (
        pytest.approx([0.0, 2.0])
    )

    profile = FidelityProfile(
        operation_failure_probability={
            "EXECUTE_COMPUTE": 0.0,
            "MOVE_QUBITS": 0.0,
            "PREPARE_MAGIC_STATE": 0.0,
        },
        logical_operation_failure_probability={"h": 0.0, "t": 0.0},
        resource_state_models={
            "magic_state": ResourceStateFidelityModel(
                resource_kind="magic_state",
                output_failure_probability=0.1,
                protocol_id="magic-proto",
                protocol_profile_hash="a" * 64,
                buffer_idle_models={
                    location: _magic_buffer_model(location)
                    for location in (
                        "factory_node/factory/output",
                        "compute_node/compute/input",
                    )
                },
            )
        },
    )
    estimate = estimate_fidelity(result, profile, plan=plan)
    assert estimate.consumed_resource_counts == {"magic_state": 1}
    assert estimate.log_success_by_resource_output == pytest.approx(
        {"magic_state": math.log(0.9)}
    )
    assert estimate.resource_idle_cycles_by_location == pytest.approx(
        {"compute_node/compute/input": 2.0}
    )
    assert estimate.success_probability == pytest.approx(0.9 * 0.99**2)
    assert estimate.complete_coverage


def _protocol_bound_magic_profile(
    *,
    protocol_id: str = "magic-proto",
    profile_hash: str = "a" * 64,
    output_failure_probability: float = 0.1,
) -> FidelityProfile:
    return FidelityProfile(
        operation_failure_probability={
            "EXECUTE_COMPUTE": 0.0,
            "MOVE_QUBITS": 0.0,
            "PREPARE_MAGIC_STATE": 0.0,
        },
        logical_operation_failure_probability={"h": 0.0, "t": 0.0},
        resource_state_models={
            "magic_state": ResourceStateFidelityModel(
                resource_kind="magic_state",
                output_failure_probability=output_failure_probability,
                protocol_id=protocol_id,
                protocol_profile_hash=profile_hash,
            )
        },
    )


def test_protocol_bound_resource_model_requires_plan_binding_receipt() -> None:
    plan = _forwarded_magic_plan()

    with pytest.raises(ValueError, match="canonical resource_protocol_bindings"):
        estimate_fidelity(
            evaluate(plan),
            _protocol_bound_magic_profile(),
            plan=plan,
        )


@pytest.mark.parametrize(
    ("protocol_id", "profile_hash", "output_failure_probability"),
    (
        ("other-protocol", "a" * 64, 0.1),
        ("magic-proto", "b" * 64, 0.1),
        ("magic-proto", "a" * 64, 0.2),
    ),
)
def test_protocol_bound_resource_model_must_match_selected_binding(
    protocol_id: str,
    profile_hash: str,
    output_failure_probability: float,
) -> None:
    plan = _with_magic_protocol_authority(_forwarded_magic_plan())

    with pytest.raises(ValueError, match="disagrees with the Plan protocol authority"):
        estimate_fidelity(
            evaluate(plan),
            _protocol_bound_magic_profile(
                protocol_id=protocol_id,
                profile_hash=profile_hash,
                output_failure_probability=output_failure_probability,
            ),
            plan=plan,
        )


def test_protocol_binding_receipt_and_separate_hash_are_both_authoritative() -> None:
    bindings = _magic_protocol_bindings()
    raw_plan = _forwarded_magic_plan()
    stale_hash_plan = _with_magic_protocol_authority(
        raw_plan,
        bindings=bindings,
        recorded_hash="b" * 64,
    )
    with pytest.raises(ValueError, match="does not match its typed receipt"):
        estimate_fidelity(
            evaluate(stale_hash_plan),
            _protocol_bound_magic_profile(),
            plan=stale_hash_plan,
        )

    tampered_receipt = bindings.to_dict()
    tampered_receipt["bindings_hash"] = "b" * 64
    tampered_plan = replace(
        raw_plan,
        provenance={
            "resource_protocol_bindings": tampered_receipt,
            "resource_protocol_bindings_hash": bindings.bindings_hash,
        },
    )
    with pytest.raises(ValueError, match="receipt is invalid"):
        estimate_fidelity(
            evaluate(tampered_plan),
            _protocol_bound_magic_profile(),
            plan=tampered_plan,
        )


def test_unused_resource_output_has_no_application_fidelity_cost() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="summary"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=2.0,
                    metadata={"gates": {"h": [0]}},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    duration_s=1.0,
                    produces={"magic": 1},
                    engines={"factory": 1},
                    output_overflow_policy="discard_excess",
                ),
            )
        ),
        (
            BufferSpec(
                "magic",
                1,
                "magic_state",
                module="node/factory",
                submodule="node/factory/output",
            ),
        ),
        (EngineSpec("factory"),),
    )
    result = evaluate(plan)
    ledger = resource_token_ledger(result, plan)
    assert not ledger.consumed_tokens
    assert len(ledger.unconsumed_tokens) == 1
    assert ledger.unconsumed_tokens[0].residence_intervals[0].duration_s == (
        pytest.approx(1.0)
    )

    profile = FidelityProfile(
        operation_failure_probability={"EXECUTE_COMPUTE": 0.0},
        logical_operation_failure_probability={"h": 0.0},
        resource_state_models={
            "magic_state": ResourceStateFidelityModel(
                resource_kind="magic_state",
                output_failure_probability=0.5,
                buffer_idle_models={
                    "node/factory/output": _magic_buffer_model(
                        "node/factory/output"
                    )
                },
            )
        },
    )
    estimate = estimate_fidelity(result, profile, plan=plan)
    assert estimate.success_probability == 1.0
    assert not estimate.log_success_by_resource_output
    assert not estimate.log_success_by_resource_idle_location
    assert estimate.complete_coverage


def _remote_magic_delivery_plan(
    *, consume_delivered_magic: bool, bell_buffer_id: str = "bell:link"
) -> ExecutionPlan:
    program = [
        ArchitectureInstruction(
            0,
            ArchitectureOpcode.EXECUTE_COMPUTE,
            duration_s=2.0,
            qubits=(0,),
            metadata={"gates": {"h": [0]}},
        )
    ]
    if consume_delivered_magic:
        program.append(
            ArchitectureInstruction(
                1,
                ArchitectureOpcode.EXECUTE_COMPUTE,
                predecessor_ids=(0,),
                duration_s=0.5,
                qubits=(0,),
                consumes={"magic_compute": 1},
                metadata={"gates": {"t": [0]}},
            )
        )
    return ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="summary"),
        ProgramDAG(tuple(program)),
        ResourceDAG(
            (
                ResourceProcess(
                    "deliver_remote_magic",
                    ArchitectureOpcode.TELEPORT_QUBITS,
                    duration_s=1.0,
                    consumes={"msf_output": 1, bell_buffer_id: 1},
                    produces={"magic_compute": 1},
                    forwards={"msf_output": "magic_compute"},
                    protocol="logical_magic_teleportation",
                    target_links=("link",),
                ),
            )
        ),
        (
            BufferSpec(
                "msf_output",
                1,
                "magic_state",
                module="factory_node/factory",
                submodule="factory_node/factory/output",
                initial_contents=("magic:0",),
            ),
            BufferSpec(
                bell_buffer_id,
                1,
                "logical_bell_pair",
                module="link/bell_storage",
                submodule="link/bell_storage/buffer",
                initial_contents=("bell:0",),
            ),
            BufferSpec(
                "magic_compute",
                1,
                "magic_state",
                module="compute_node/compute",
                submodule="compute_node/compute/input",
            ),
        ),
        (),
        initial_locations={"q:0": "compute_node/compute"},
    )


def _remote_magic_fidelity_profile() -> FidelityProfile:
    magic_locations = (
        "factory_node/factory/output",
        "compute_node/compute/input",
    )
    bell_location = "link/bell_storage/buffer"
    return FidelityProfile(
        operation_failure_probability={"EXECUTE_COMPUTE": 0.0},
        operation_failure_models={
            "TELEPORT_QUBITS": {
                "kind": "independent_channels_per_teleported_item",
                "channels": {"transversal_cnot": 0.1},
            }
        },
        logical_operation_failure_probability={"h": 0.0, "t": 0.0},
        resource_state_models={
            "magic_state": ResourceStateFidelityModel(
                resource_kind="magic_state",
                output_failure_probability=0.3,
                buffer_idle_models={
                    location: ResourceBufferFidelityModel(
                        location=location,
                        owner_kind="node",
                        qec_code="surface",
                        qec_parameters={"distance": 13},
                        logical_qubits_per_token=1,
                        idle_failure_probability_per_cycle=0.0,
                        idle_cycle_time_s=1.0,
                    )
                    for location in magic_locations
                },
            ),
            "logical_bell_pair": ResourceStateFidelityModel(
                resource_kind="logical_bell_pair",
                output_failure_probability=0.2,
                buffer_idle_models={
                    bell_location: ResourceBufferFidelityModel(
                        location=bell_location,
                        owner_kind="interconnect",
                        qec_code="rotated_surface_code",
                        qec_parameters={"distance": 19},
                        logical_qubits_per_token=2,
                        idle_failure_probability_per_cycle=0.0,
                        idle_cycle_time_s=1.0,
                    )
                },
            ),
        },
    )


def test_unused_remote_magic_does_not_settle_upstream_bell_or_teleport_error() -> None:
    plan = _remote_magic_delivery_plan(consume_delivered_magic=False)
    result = evaluate(plan)
    ledger = resource_token_ledger(result, plan)

    # The background delivery physically consumes a Bell pair and forwards the
    # magic token, but neither fact can reach the completed Clifford Program.
    assert [token.token_id for token in ledger.consumed_tokens] == ["bell:0"]
    assert not ledger.settled_tokens
    assert not ledger.settled_resource_event_ids

    estimate = estimate_fidelity(
        result,
        _remote_magic_fidelity_profile(),
        plan=plan,
    )
    assert estimate.success_probability == 1.0
    assert not estimate.log_success_by_operation
    assert "TELEPORT_QUBITS:transversal_cnot" not in (
        estimate.log_success_by_operation
    )
    assert estimate.architecture_operation_counts == {"EXECUTE_COMPUTE": 1}
    assert not estimate.log_success_by_resource_output
    assert not estimate.log_success_by_resource_idle_location
    assert not estimate.consumed_resource_counts
    assert estimate.complete_coverage


def test_program_consumed_remote_magic_settles_magic_bell_and_delivery_once() -> None:
    plan = _remote_magic_delivery_plan(consume_delivered_magic=True)
    result = evaluate(plan)
    ledger = resource_token_ledger(result, plan)

    assert {token.token_id for token in ledger.consumed_tokens} == {
        "magic:0",
        "bell:0",
    }
    assert {token.token_id for token in ledger.settled_tokens} == {
        "magic:0",
        "bell:0",
    }
    assert len(ledger.settled_resource_event_ids) == 1

    estimate = estimate_fidelity(
        result,
        _remote_magic_fidelity_profile(),
        plan=plan,
    )
    assert estimate.consumed_resource_counts == {
        "logical_bell_pair": 1,
        "magic_state": 1,
    }
    assert estimate.log_success_by_operation == pytest.approx(
        {"TELEPORT_QUBITS:transversal_cnot": math.log(0.9)}
    )
    assert estimate.architecture_operation_counts == {
        "EXECUTE_COMPUTE": 2,
        "TELEPORT_QUBITS": 1,
    }
    assert estimate.log_success_by_resource_output == pytest.approx(
        {
            "logical_bell_pair": math.log(0.8),
            "magic_state": math.log(0.7),
        }
    )
    assert estimate.success_probability == pytest.approx(0.9 * 0.8 * 0.7)
    assert estimate.complete_coverage


@pytest.mark.parametrize("bell_buffer_id", ("bell:link", "pair_pool"))
@pytest.mark.parametrize("diagnostic_amount", (1, 100, "diagnostic only"))
def test_teleport_fidelity_ignores_buffer_spelling_and_diagnostic_quantities(
    bell_buffer_id: str, diagnostic_amount: object
) -> None:
    plan = _remote_magic_delivery_plan(
        consume_delivered_magic=True, bell_buffer_id=bell_buffer_id
    )
    process = replace(
        plan.resource_dag.processes[0], metadata={"amount": diagnostic_amount}
    )
    plan = replace(plan, resource_dag=ResourceDAG((process,)))
    result = evaluate(plan)
    # ResourceProcess rejects metadata.qubits at construction. Also test a
    # re-signed diagnostic Trace projection carrying that obsolete receipt:
    # Resource work has no typed logical-qubit tuple to replace with it.
    transitions = tuple(
        replace(item, metadata={**dict(item.metadata), "qubits": [7] * 13})
        if item.process_id == process.id else item
        for item in result.transitions
    )
    result = replace(result, trace=replace(result.trace, transitions=transitions))

    estimate = estimate_fidelity(result, _remote_magic_fidelity_profile(), plan=plan)

    assert result.total_latency_s == 2.5
    assert estimate.log_success_by_operation == pytest.approx(
        {"TELEPORT_QUBITS:transversal_cnot": math.log(0.9)}
    )
    assert estimate.success_probability == pytest.approx(0.504)
    assert estimate.complete_coverage


@pytest.mark.parametrize("dispatch_policy", ("single", "eager_available"))
def test_teleport_fidelity_counts_forwarded_items_in_typed_batches(
    dispatch_policy: str,
) -> None:
    plan = _remote_magic_delivery_plan(
        consume_delivered_magic=True, bell_buffer_id="pair_pool"
    )
    # Deliberately distinguish teleported payload from consumed Bell ancillas.
    # Eager mode scales a one-item template into a two-item realized batch.
    item_quantity = 2 if dispatch_policy == "single" else 1
    bell_quantity = 3 if dispatch_policy == "single" else 2
    bell_count = bell_quantity * (2 // item_quantity)
    process = replace(
        plan.resource_dag.processes[0],
        consumes={"msf_output": item_quantity, "pair_pool": bell_quantity},
        produces={"magic_compute": item_quantity},
        dispatch_policy=dispatch_policy,
        metadata={"amount": 999},
    )
    buffers = tuple(
        replace(
            buffer,
            capacity=bell_count if buffer.id == "pair_pool" else 2,
            slots=(),
            initial_contents=(
                tuple(f"bell:{index}" for index in range(bell_count))
                if buffer.id == "pair_pool"
                else ("magic:0", "magic:1") if buffer.id == "msf_output" else ()
            ),
        )
        for buffer in plan.buffers
    )
    first, consume = plan.program_dag.instructions
    plan = replace(
        plan,
        buffers=buffers,
        resource_dag=ResourceDAG((process,)),
        program_dag=ProgramDAG((
            first,
            replace(consume, qubits=(0, 1), consumes={"magic_compute": 2},
                    metadata={"gates": {"t": [0, 1]}}),
        )),
        initial_locations={"q:0": "compute_node/compute", "q:1": "compute_node/compute"},
    )
    profile = replace(
        _remote_magic_fidelity_profile(),
        idle_failure_rate_per_s={"compute_node/compute": 0.0},
    )

    result = evaluate(plan)
    estimate = estimate_fidelity(result, profile, plan=plan)

    assert estimate.architecture_operation_counts["TELEPORT_QUBITS"] == 1
    assert estimate.consumed_resource_counts == {
        "magic_state": 2, "logical_bell_pair": bell_count,
    }
    assert estimate.log_success_by_operation == pytest.approx(
        {"TELEPORT_QUBITS:transversal_cnot": 2 * math.log(0.9)}
    )
    assert estimate.success_probability == pytest.approx(0.9**2 * 0.7**2 * 0.8**bell_count)
    assert estimate.complete_coverage


def test_teleport_fidelity_rejects_missing_typed_process_context() -> None:
    plan = _remote_magic_delivery_plan(consume_delivered_magic=True)
    result = evaluate(plan)
    other_plan = replace(
        plan,
        resource_dag=ResourceDAG((
            replace(plan.resource_dag.processes[0], id="different_process"),
        )),
    )
    rebound = replace(result, trace=replace(result.trace, plan_hash=other_plan.plan_hash))

    with pytest.raises(ValueError, match="typed Plan teleport flow"):
        estimate_fidelity(rebound, _remote_magic_fidelity_profile(), plan=other_plan)


def test_relevant_multi_forward_event_does_not_settle_unused_sibling() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="summary"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=2.0,
                    qubits=(0,),
                    metadata={"gates": {"h": [0]}},
                ),
                ArchitectureInstruction(
                    1,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    predecessor_ids=(0,),
                    duration_s=0.5,
                    qubits=(0,),
                    consumes={"used_out": 1},
                    metadata={"gates": {"t": [0]}},
                ),
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "multi_forward",
                    ArchitectureOpcode.MOVE_QUBITS,
                    duration_s=1.0,
                    consumes={"used_in": 1, "unused_in": 1},
                    produces={"used_out": 1, "unused_out": 1},
                    forwards={
                        "used_in": "used_out",
                        "unused_in": "unused_out",
                    },
                ),
            )
        ),
        (
            BufferSpec(
                "used_in",
                1,
                "magic_state",
                initial_contents=("magic:used",),
            ),
            BufferSpec(
                "unused_in",
                1,
                "magic_state",
                initial_contents=("magic:unused",),
            ),
            BufferSpec("used_out", 1, "magic_state"),
            BufferSpec("unused_out", 1, "magic_state"),
        ),
        (),
        initial_locations={"q:0": "compute"},
    )
    result = evaluate(plan)
    ledger = resource_token_ledger(result, plan)

    assert [token.token_id for token in ledger.settled_tokens] == ["magic:used"]
    assert [token.token_id for token in ledger.unconsumed_tokens] == [
        "magic:unused"
    ]
    assert len(ledger.settled_resource_event_ids) == 1

    idle_models = {
        location: ResourceBufferFidelityModel(
            location=location,
            owner_kind="node",
            qec_code="surface",
            qec_parameters={"distance": 13},
            logical_qubits_per_token=1,
            idle_failure_probability_per_cycle=0.0,
            idle_cycle_time_s=1.0,
        )
        for location in ("used_in", "unused_in", "used_out", "unused_out")
    }
    estimate = estimate_fidelity(
        result,
        FidelityProfile(
            operation_failure_probability={
                "EXECUTE_COMPUTE": 0.0,
                "MOVE_QUBITS": 0.0,
            },
            logical_operation_failure_probability={"h": 0.0, "t": 0.0},
            resource_state_models={
                "magic_state": ResourceStateFidelityModel(
                    resource_kind="magic_state",
                    output_failure_probability=0.1,
                    buffer_idle_models=idle_models,
                )
            },
        ),
        plan=plan,
    )
    assert estimate.consumed_resource_counts == {"magic_state": 1}
    assert estimate.success_probability == pytest.approx(0.9)
    assert estimate.complete_coverage


def _bell_plan(delay_s: float) -> ExecutionPlan:
    return ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="summary"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=delay_s,
                    metadata={"gates": {"h": [0]}},
                ),
                ArchitectureInstruction(
                    1,
                    ArchitectureOpcode.TELEPORT_QUBITS,
                    predecessor_ids=(0,),
                    duration_s=0.5,
                    qubits=(0,),
                    consumes={"bell": 1},
                    required_locations={"q:0": "left"},
                    completion_locations={"q:0": "right"},
                    target_links=("link",),
                ),
            )
        ),
        ResourceDAG(()),
        (
            BufferSpec(
                "bell",
                1,
                "logical_bell_pair",
                module="link/bell_storage",
                submodule="link/bell_storage/buffer",
                initial_contents=("bell:0",),
            ),
        ),
        (),
        initial_locations={"q:0": "left"},
    )


def test_bell_output_is_charged_once_and_idle_counts_both_endpoints() -> None:
    location = "link/bell_storage/buffer"
    profile = FidelityProfile(
        logical_operation_failure_probability={"h": 0.0},
        resource_state_models={
            "logical_bell_pair": ResourceStateFidelityModel(
                resource_kind="logical_bell_pair",
                output_failure_probability=0.2,
                buffer_idle_models={
                    location: ResourceBufferFidelityModel(
                        location=location,
                        owner_kind="interconnect",
                        qec_code="rotated_surface_code",
                        qec_parameters={"distance": 19},
                        logical_qubits_per_token=2,
                        idle_failure_probability_per_cycle=0.01,
                        idle_cycle_time_s=1.0,
                    )
                },
            )
        },
    )

    zero_plan = _bell_plan(0.0)
    zero = estimate_fidelity(evaluate(zero_plan), profile, plan=zero_plan)
    assert zero.resource_idle_cycles_by_location == {}
    assert zero.success_probability == pytest.approx(0.8)

    delayed_plan = _bell_plan(2.0)
    delayed = estimate_fidelity(
        evaluate(delayed_plan),
        profile,
        plan=delayed_plan,
    )
    assert delayed.consumed_resource_counts == {"logical_bell_pair": 1}
    assert delayed.resource_idle_cycles_by_location == pytest.approx(
        {location: 4.0}
    )
    assert delayed.success_probability == pytest.approx(0.8 * 0.99**4)


def test_resource_models_require_typed_plan_context() -> None:
    plan = _bell_plan(0.0)
    profile = FidelityProfile(
        resource_state_models={
            "logical_bell_pair": ResourceStateFidelityModel(
                resource_kind="logical_bell_pair",
                output_failure_probability=0.0,
            )
        }
    )
    with pytest.raises(ValueError, match="matching ExecutionPlan"):
        estimate_fidelity(evaluate(plan), profile)


def test_resource_lifecycle_requires_plan_even_without_resource_models() -> None:
    plan = _forwarded_magic_plan()

    with pytest.raises(ValueError, match="trace lifecycle facts"):
        estimate_fidelity(evaluate(plan), FidelityProfile())


def test_opcode_omission_and_explicit_zero_have_distinct_coverage() -> None:
    plan = ExecutionPlan(
        "circuit",
        "architecture",
        "latency",
        EvaluationPolicy(trace_level="summary"),
        ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    duration_s=0.5,
                ),
            )
        ),
        ResourceDAG(()),
        (),
        (),
    )

    result = evaluate(plan)
    omitted = estimate_fidelity(result, FidelityProfile())
    explicit_zero = estimate_fidelity(
        result,
        FidelityProfile(
            operation_failure_probability={"EXECUTE_COMPUTE": 0.0}
        ),
    )

    assert omitted.success_probability == 1.0
    assert omitted.architecture_operation_counts == {"EXECUTE_COMPUTE": 1}
    assert omitted.unprofiled_operation_counts == {"EXECUTE_COMPUTE": 1}
    assert not omitted.complete_coverage

    assert explicit_zero.success_probability == 1.0
    assert not explicit_zero.log_success_by_operation
    assert explicit_zero.architecture_operation_counts == {"EXECUTE_COMPUTE": 1}
    assert not explicit_zero.unprofiled_operation_counts
    assert explicit_zero.complete_coverage
