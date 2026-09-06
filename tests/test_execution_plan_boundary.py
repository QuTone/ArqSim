from __future__ import annotations

from dataclasses import replace

import pytest

from arqsim.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    MagicRouteDispatchRecipe,
    MoveOperands,
    ResourceMoveDispatchRecipe,
)
from arqsim.architecture.gallery import QuantileSizingConfig
from arqsim.compiler import (
    BackendSpec,
    CompiledRouteStep,
    LogicalCompilerValidationError,
    canonical_compiler_spec,
)
from arqsim.compiler.layout import primary_qec_submodule, single_node_module
from arqsim.compiler.pipeline import DefaultCompilerPipeline
from arqsim.evaluation import (
    BufferSpec,
    EngineSpec,
    EvaluationPolicy,
    ExecutionPlan,
    ProgramDAG,
    ResourceDAG,
    ResourceProcess,
    compile_and_lower,
    default_runtime_component_manifest,
    evaluate,
)
from arqsim.evaluation.components import (
    build_runtime_component_set,
    default_direct_runtime_component_manifest,
)
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
from arqsim.schema import normalize_json, semantic_hash
from arqsim.specification import build_architecture_specification


def _compile_plan(*args, **kwargs) -> ExecutionPlan:
    return compile_and_lower(*args, **kwargs)[1]


def _remote_magic_circuit() -> FTCircuit:
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
        provenance={"test": "serialized-explicit-execution"},
    )


def _codec_plan() -> ExecutionPlan:
    return ExecutionPlan(
        circuit_hash="circuit",
        architecture_hash="architecture",
        latency_profile_hash="latency",
        policy=EvaluationPolicy(),
        program_dag=ProgramDAG(
            (
                ArchitectureInstruction(
                    0,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    required_locations={"0": "compute"},
                    metadata={"nested": {"values": [1, 2]}},
                ),
            )
        ),
        resource_dag=ResourceDAG(
            (
                ResourceProcess(
                    "prepare",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    produces={"buffer": 1},
                    engines={"engine": 1},
                    duration_s=1.0,
                    metadata={"nested": {"values": [3, 4]}},
                ),
            )
        ),
        buffers=(BufferSpec("buffer", 1, "token"),),
        engines=(EngineSpec("engine"),),
    )


def _rehash_plan_document(document: dict) -> None:
    unsigned = dict(document)
    unsigned.pop("plan_hash", None)
    document["plan_hash"] = semantic_hash(unsigned)


def test_plan_dag_contents_are_recursively_immutable_after_hashing() -> None:
    plan = _codec_plan()
    instruction = plan.program_dag.instructions[0]
    process = plan.resource_dag.processes[0]
    original_hash = plan.plan_hash
    original_document = plan.to_dict()

    with pytest.raises(TypeError):
        instruction.required_locations["0"] = "memory"
    with pytest.raises(TypeError):
        instruction.metadata["nested"]["extra"] = True
    with pytest.raises(TypeError):
        process.produces["buffer"] = 2
    with pytest.raises(TypeError):
        process.metadata["nested"]["extra"] = True

    assert instruction.metadata["nested"]["values"] == (1, 2)
    assert process.metadata["nested"]["values"] == (3, 4)
    assert plan.plan_hash == original_hash
    assert plan.to_dict() == original_document


def test_plan_dag_contents_detach_from_mutable_constructor_inputs() -> None:
    instruction_metadata = {"nested": {"values": [1]}}
    required_locations = {"0": "compute"}
    process_metadata = {"nested": {"values": [2]}}
    produced = {"buffer": 1}
    instruction = ArchitectureInstruction(
        0,
        ArchitectureOpcode.EXECUTE_COMPUTE,
        required_locations=required_locations,
        metadata=instruction_metadata,
    )
    process = ResourceProcess(
        "prepare",
        ArchitectureOpcode.PREPARE_MAGIC_STATE,
        produces=produced,
        engines={"engine": 1},
        duration_s=1.0,
        metadata=process_metadata,
    )
    process_list = [process]
    plan = ExecutionPlan(
        circuit_hash="circuit",
        architecture_hash="architecture",
        latency_profile_hash="latency",
        policy=EvaluationPolicy(),
        program_dag=ProgramDAG((instruction,)),
        resource_dag=ResourceDAG(process_list),
        buffers=(BufferSpec("buffer", 1, "token"),),
        engines=(EngineSpec("engine"),),
    )
    original_hash = plan.plan_hash
    original_document = plan.to_dict()

    instruction_metadata["nested"]["values"].append(99)
    required_locations["0"] = "memory"
    process_metadata["nested"]["values"].append(99)
    produced["buffer"] = 99
    process_list.clear()

    assert plan.plan_hash == original_hash
    assert plan.to_dict() == original_document
    assert len(plan.resource_dag.processes) == 1


@pytest.mark.parametrize(
    "invalid_locations",
    ({0: "compute"}, {"0": 123}, {"0": True}),
)
def test_plan_rejects_non_string_initial_locations(
    invalid_locations: object,
) -> None:
    with pytest.raises(TypeError, match="must map strings to strings"):
        replace(_codec_plan(), initial_locations=invalid_locations)

    document = normalize_json(_codec_plan().to_dict())
    document["architectural_state"]["initial_locations"] = invalid_locations
    with pytest.raises(TypeError, match="must map strings to strings"):
        ExecutionPlan.from_dict(document)


def test_plan_rejects_initial_resource_and_logical_entity_id_collision() -> None:
    plan = replace(_codec_plan(), initial_locations={"q:0": "compute"})
    with pytest.raises(ValueError, match="conflict with initial location entities"):
        replace(
            plan,
            buffers=(replace(plan.buffers[0], initial_contents=("q:0",)),),
        )

    document = plan.to_dict()
    document["architectural_state"]["buffers"][0]["initial_contents"] = ["q:0"]
    _rehash_plan_document(document)
    with pytest.raises(ValueError, match="conflict with initial location entities"):
        ExecutionPlan.from_dict(document)


def test_plan_rejects_claims_larger_than_installed_capacity() -> None:
    plan = _codec_plan()
    instruction = plan.program_dag.instructions[0]
    process = plan.resource_dag.processes[0]

    with pytest.raises(ValueError, match="buffer claims exceed"):
        replace(
            plan,
            program_dag=ProgramDAG(
                (replace(instruction, consumes={"buffer": 2}),)
            ),
        )
    with pytest.raises(ValueError, match="engine claims exceed"):
        replace(
            plan,
            program_dag=ProgramDAG(
                (replace(instruction, engines={"engine": 2}),)
            ),
        )
    with pytest.raises(ValueError, match="buffer claims exceed"):
        replace(
            plan,
            resource_dag=ResourceDAG(
                (replace(process, produces={"buffer": 2}),)
            ),
        )

    discard = replace(
        plan,
        resource_dag=ResourceDAG(
            (
                replace(
                    process,
                    produces={"buffer": 2},
                    output_overflow_policy="discard_excess",
                ),
            )
        ),
    )
    assert discard.resource_dag.processes[0].produces["buffer"] == 2


def test_plan_move_operands_require_matching_architecture_state_claims() -> None:
    plan = _codec_plan()
    move = ArchitectureInstruction(
        id=0,
        opcode=ArchitectureOpcode.MOVE_QUBITS,
        qubits=(0,),
        required_locations={"q:0": "source"},
        completion_locations={"q:0": "destination"},
        move_operands=MoveOperands(
            source_slots={0: "source/slot_0"},
            destination_slots={0: "destination/slot_0"},
        ),
    )
    valid = replace(plan, program_dag=ProgramDAG((move,)))

    for field, message in (
        ("required_locations", "source ArchitectureState location"),
        ("completion_locations", "destination ArchitectureState location"),
    ):
        with pytest.raises(ValueError, match=message):
            replace(
                valid,
                program_dag=ProgramDAG((replace(move, **{field: {}}),)),
            )

        document = normalize_json(valid.to_dict())
        document["program_dag"]["instructions"][0][field] = {}
        _rehash_plan_document(document)
        with pytest.raises(ValueError, match=message):
            ExecutionPlan.from_dict(document)


def test_plan_program_move_requires_typed_operands() -> None:
    plan = _codec_plan()
    legacy_move = ArchitectureInstruction(
        id=0,
        opcode=ArchitectureOpcode.MOVE_QUBITS,
        qubits=(0,),
        required_locations={"q:0": "source"},
        completion_locations={"q:0": "destination"},
    )
    with pytest.raises(ValueError, match="requires typed MoveOperands"):
        replace(plan, program_dag=ProgramDAG((legacy_move,)))

    with pytest.raises(ValueError, match="Legacy dispatch control"):
        ArchitectureInstruction(
            id=0,
            opcode=ArchitectureOpcode.MOVE_QUBITS,
            qubits=(0,),
            metadata={
                "source_slots": {"0": "source/slot_0"},
                "destination_slots": {"0": "destination/slot_0"},
            },
        )


def test_plan_program_move_rejects_resource_flow_and_noop_location() -> None:
    source = "node/compute/source"
    destination = "node/compute/destination"
    move = ArchitectureInstruction(
        id=0,
        opcode=ArchitectureOpcode.MOVE_QUBITS,
        qubits=(0,),
        required_locations={"q:0": source},
        completion_locations={"q:0": destination},
        move_operands=MoveOperands(
            source_slots={0: f"{source}/S0"},
            destination_slots={0: f"{destination}/D0"},
        ),
    )
    plan = ExecutionPlan(
        circuit_hash="circuit",
        architecture_hash="architecture",
        latency_profile_hash="latency",
        policy=EvaluationPolicy(),
        program_dag=ProgramDAG((move,)),
        resource_dag=ResourceDAG(()),
        buffers=(),
        engines=(),
        initial_locations={"q:0": source},
    )
    with_buffer = replace(
        plan,
        buffers=(BufferSpec("resource", 1, "magic_state"),),
    )
    with pytest.raises(ValueError, match="cannot carry Resource-buffer"):
        replace(
            with_buffer,
            program_dag=ProgramDAG(
                (replace(move, consumes={"resource": 1}),)
            ),
        )

    assert move.move_operands is not None
    with pytest.raises(ValueError, match="must change each entity"):
        replace(
            plan,
            program_dag=ProgramDAG(
                (
                    replace(
                        move,
                        completion_locations={"q:0": source},
                        move_operands=MoveOperands(
                            source_slots=move.move_operands.source_slots,
                            destination_slots={0: f"{source}/D0"},
                        ),
                    ),
                )
            ),
        )


@pytest.mark.parametrize("invalid", (True, 1.5, "2"))
def test_direct_instruction_rejects_coerced_integer_fields(invalid: object) -> None:
    with pytest.raises(TypeError, match="id must be an integer"):
        ArchitectureInstruction(invalid, ArchitectureOpcode.FENCE)
    with pytest.raises(TypeError, match="predecessors must be integers"):
        ArchitectureInstruction(
            1,
            ArchitectureOpcode.FENCE,
            predecessor_ids=(invalid,),
        )
    with pytest.raises(TypeError, match="qubits must be integers"):
        ArchitectureInstruction(
            0,
            ArchitectureOpcode.FENCE,
            qubits=(invalid,),
        )
    with pytest.raises(TypeError, match="consumes must map strings to integers"):
        ArchitectureInstruction(
            0,
            ArchitectureOpcode.FENCE,
            consumes={"buffer": invalid},
        )


@pytest.mark.parametrize("invalid", (True, "1.5"))
def test_direct_dag_nodes_reject_coerced_durations(invalid: object) -> None:
    with pytest.raises(TypeError, match="duration_s must be a finite number"):
        ArchitectureInstruction(
            0,
            ArchitectureOpcode.FENCE,
            duration_s=invalid,
        )
    with pytest.raises(TypeError, match="duration_s must be a finite number"):
        ResourceProcess(
            "prepare",
            ArchitectureOpcode.PREPARE_MAGIC_STATE,
            duration_s=invalid,
        )


@pytest.mark.parametrize("invalid", (True, 1.5, "2"))
def test_direct_resource_process_rejects_coerced_integer_fields(
    invalid: object,
) -> None:
    with pytest.raises(TypeError, match="parallelism must be an integer"):
        ResourceProcess(
            "prepare",
            ArchitectureOpcode.PREPARE_MAGIC_STATE,
            parallelism=invalid,
        )
    with pytest.raises(TypeError, match="produces must map strings to integers"):
        ResourceProcess(
            "prepare",
            ArchitectureOpcode.PREPARE_MAGIC_STATE,
            produces={"buffer": invalid},
        )


def test_serialized_plan_can_be_parsed_and_executed_explicitly() -> None:
    circuit = _remote_magic_circuit()
    specification = build_architecture_specification(circuit, "1.3")
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification,
        requested_latency,
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    compiler = canonical_compiler_spec(specification)
    runtime_instruction_compiler = build_runtime_instruction_compiler(
        circuit,
        specification,
        compiler,
        latency,
    )
    runtime_resource_compiler = build_runtime_resource_compiler(
        specification,
        compiler,
        latency,
    )
    components = build_runtime_component_set(
        default_runtime_component_manifest(),
        runtime_instruction_compiler=runtime_instruction_compiler,
        runtime_resource_compiler=runtime_resource_compiler,
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

    restored = ExecutionPlan.from_json(plan.to_json())
    result = evaluate(restored, runtime_components=components)

    assert restored.plan_hash == plan.plan_hash
    assert result.plan_hash == restored.plan_hash
    assert any(
        event.opcode == ArchitectureOpcode.EXECUTE_COMPUTE
        for event in result.events
    )
    assert all(result.invariant_checks.values())


@pytest.mark.parametrize("invalid", (True, 1.5, "2"))
@pytest.mark.parametrize("component_kind", ("buffers", "engines"))
def test_plan_codec_rejects_non_plain_integer_capacities(
    invalid: object,
    component_kind: str,
) -> None:
    document = normalize_json(_codec_plan().to_dict())
    document["architectural_state"][component_kind][0]["capacity"] = invalid

    with pytest.raises(TypeError, match="positive plain integer"):
        ExecutionPlan.from_dict(document)


@pytest.mark.parametrize(
    ("field", "invalid", "message"),
    (
        ("slots", "slot_0", "slots must be a string array"),
        ("initial_contents", {"token": 1}, "initial_contents must be a string array"),
    ),
)
def test_plan_codec_rejects_non_array_buffer_sequences(
    field: str,
    invalid: object,
    message: str,
) -> None:
    document = normalize_json(_codec_plan().to_dict())
    document["architectural_state"]["buffers"][0][field] = invalid

    with pytest.raises(TypeError, match=message):
        ExecutionPlan.from_dict(document)


@pytest.mark.parametrize("field", ("buffers", "engines"))
def test_plan_codec_rejects_non_array_architectural_state(field: str) -> None:
    document = normalize_json(_codec_plan().to_dict())
    document["architectural_state"][field] = {}

    with pytest.raises(TypeError, match=f"{field} must be an array"):
        ExecutionPlan.from_dict(document)


@pytest.mark.parametrize("invalid", (True, 1.5, "2"))
def test_plan_codec_rejects_non_plain_policy_integers(invalid: object) -> None:
    document = normalize_json(_codec_plan().to_dict())
    document["policy"]["seed"] = invalid

    with pytest.raises(TypeError, match="seed must be an integer"):
        ExecutionPlan.from_dict(document)


@pytest.mark.parametrize("invalid", (True, 1.5, "2"))
@pytest.mark.parametrize(
    ("path", "message"),
    (
        (("program_dag", "instructions", 0, "id"), "id must be an integer"),
        (
            ("resource_dag", "processes", 0, "parallelism"),
            "parallelism must be an integer",
        ),
    ),
)
def test_plan_codec_rejects_nested_non_plain_integers(
    invalid: object,
    path: tuple[object, ...],
    message: str,
) -> None:
    document = normalize_json(_codec_plan().to_dict())
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = invalid

    with pytest.raises(ValueError, match=message):
        ExecutionPlan.from_dict(document)


@pytest.mark.parametrize("invalid", (True, "1.5"))
def test_plan_codec_rejects_non_numeric_nested_durations(invalid: object) -> None:
    document = normalize_json(_codec_plan().to_dict())
    document["program_dag"]["instructions"][0]["duration_s"] = invalid

    with pytest.raises(ValueError, match="duration_s must be a finite number"):
        ExecutionPlan.from_dict(document)


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("circuit_hash", "wrong-circuit"),
        ("architecture_hash", "wrong-architecture"),
        ("latency_profile_hash", "wrong-latency"),
    ),
)
def test_plan_builder_rejects_mismatched_compilation_sources(
    field: str,
    invalid: str,
) -> None:
    circuit = _remote_magic_circuit()
    specification = build_architecture_specification(circuit, "1.1")
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification,
        requested_latency,
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    compiler = canonical_compiler_spec(specification)
    result = DefaultCompilerPipeline(compiler_spec=compiler).compile(
        circuit,
        specification,
        latency,
    )

    class StaticPipeline:
        def compile(self, _circuit, _specification, _latency):
            return replace(result, **{field: invalid})

    with pytest.raises(
        LogicalCompilerValidationError,
        match="source hashes do not match",
    ) as exc_info:
        _compile_plan(
            circuit,
            specification,
            latency,
            EvaluationPolicy(),
            compiler_pipeline=StaticPipeline(),
            resource_protocol_bindings=bindings,
        )
    assert exc_info.value.details["actual_sources"][field] == invalid


def _coverage_boundary_fixture():
    circuit = _remote_magic_circuit()
    specification = build_architecture_specification(circuit, "1.1")
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification,
        requested_latency,
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    compiler = canonical_compiler_spec(specification)
    result = DefaultCompilerPipeline(compiler_spec=compiler).compile(
        circuit,
        specification,
        latency,
    )
    return circuit, specification, latency, bindings, result


def _build_with_static_compilation(
    circuit,
    specification,
    latency,
    bindings,
    compilation,
    *,
    policy: EvaluationPolicy | None = None,
) -> ExecutionPlan:
    class StaticPipeline:
        def compile(self, _circuit, _specification, _latency):
            return compilation

    return _compile_plan(
        circuit,
        specification,
        latency,
        policy or EvaluationPolicy(),
        compiler_pipeline=StaticPipeline(),
        resource_protocol_bindings=bindings,
    )


def test_plan_builder_rejects_empty_compilation_for_nonempty_circuit() -> None:
    circuit, specification, latency, bindings, result = (
        _coverage_boundary_fixture()
    )

    with pytest.raises(
        LogicalCompilerValidationError,
        match="does not exactly cover the source circuit",
    ):
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            replace(result, compute_units=()),
        )


def test_plan_builder_rejects_duplicate_compilation_operation() -> None:
    circuit, specification, latency, bindings, result = (
        _coverage_boundary_fixture()
    )
    unit = result.compute_units[0]
    first_source = replace(unit.source, batch_count=2)
    second_source = replace(unit.source, batch_index=1, batch_count=2)
    duplicated = replace(
        result,
        compute_units=(
            replace(unit, source=first_source),
            replace(unit, source=second_source),
        ),
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="exactly cover"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            duplicated,
        )
    assert exc_info.value.details["duplicate_operations"] == [[0, 0]]


@pytest.mark.parametrize("bad_layer", [1, 99])
def test_plan_builder_rejects_compilation_operation_on_wrong_layer(
    bad_layer: int,
) -> None:
    circuit, specification, latency, bindings, result = (
        _coverage_boundary_fixture()
    )
    unit = result.compute_units[0]
    bad_partition = replace(unit.source.partition, layer_index=bad_layer)
    bad_source = replace(unit.source, partition=bad_partition)
    wrong_layer = replace(
        result,
        compute_units=(replace(unit, source=bad_source),),
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="exactly cover"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            wrong_layer,
        )
    assert exc_info.value.details["missing_operations"] == [[0, 0]]
    assert exc_info.value.details["extra_operations"] == [[bad_layer, 0]]


def test_plan_builder_rejects_extra_compilation_operation_index() -> None:
    circuit, specification, latency, bindings, result = (
        _coverage_boundary_fixture()
    )
    unit = result.compute_units[0]
    bad_partition = replace(
        unit.source.partition,
        operation_indices=(0, 1),
    )
    bad_source = replace(
        unit.source,
        partition=bad_partition,
        operation_indices=(0, 1),
    )
    extra_operation = replace(
        result,
        compute_units=(replace(unit, source=bad_source),),
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="exactly cover"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            extra_operation,
        )
    assert exc_info.value.details["extra_operations"] == [[0, 1]]


def test_plan_builder_rejects_forged_magic_demand_with_exact_coverage() -> None:
    circuit, specification, latency, bindings, result = (
        _coverage_boundary_fixture()
    )
    unit = result.compute_units[0]
    forged_partition = replace(unit.source.partition, magic_count=0)
    forged_source = replace(
        unit.source,
        partition=forged_partition,
        magic_count=0,
    )
    forged = replace(
        result,
        compute_units=(replace(unit, source=forged_source),),
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="exactly cover"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            forged,
        )
    assert exc_info.value.details["missing_operations"] == []
    assert {
        mismatch["scope"]
        for mismatch in exc_info.value.details["magic_count_mismatches"]
    } == {"batch", "partition"}


def test_plan_builder_rejects_missing_declared_compute_batch() -> None:
    circuit, specification, latency, bindings, result = (
        _coverage_boundary_fixture()
    )
    unit = result.compute_units[0]
    incomplete = replace(
        result,
        compute_units=(
            replace(unit, source=replace(unit.source, batch_count=2)),
        ),
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="exactly cover"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            incomplete,
        )
    assert any(
        error["reason"] == "incomplete_batch_index_coverage"
        for error in exc_info.value.details["partition_errors"]
    )


def test_plan_builder_rejects_compiler_magic_consumption_mismatch() -> None:
    circuit, specification, latency, bindings, result = (
        _coverage_boundary_fixture()
    )
    forged = replace(result, magic_state_consumption="incremental")

    with pytest.raises(
        LogicalCompilerValidationError,
        match="magic-state consumption does not match",
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            forged,
        )
    assert exc_info.value.details == {
        "expected_magic_state_consumption": "bulk_wave",
        "actual_magic_state_consumption": "incremental",
    }


def test_custom_pipeline_route_state_controls_deferred_recipe_lowering() -> None:
    circuit, specification, latency, bindings, deferred = (
        _coverage_boundary_fixture()
    )
    deferred_route = deferred.compute_units[0].route
    assert deferred_route.dispatch_deferred is True
    assert "deferred_until_dispatch" not in deferred_route.metrics

    deferred_plan = _build_with_static_compilation(
        circuit,
        specification,
        latency,
        bindings,
        deferred,
    )
    deferred_execute = next(
        instruction
        for instruction in deferred_plan.program_dag.instructions
        if instruction.opcode == ArchitectureOpcode.EXECUTE_COMPUTE
    )
    assert isinstance(
        deferred_execute.deferred_dispatch, MagicRouteDispatchRecipe
    )

    eager = DefaultCompilerPipeline(
        compiler_spec=deferred.compiler_spec,
        defer_magic_routing=False,
    ).compile(circuit, specification, latency)
    eager_route = eager.compute_units[0].route
    assert eager_route.dispatch_deferred is False
    eager_plan = _build_with_static_compilation(
        circuit,
        specification,
        latency,
        bindings,
        eager,
    )
    eager_execute = next(
        instruction
        for instruction in eager_plan.program_dag.instructions
        if instruction.opcode == ArchitectureOpcode.EXECUTE_COMPUTE
    )
    assert eager_execute.deferred_dispatch is None


def test_compilation_lineage_is_recorded_for_empty_and_nonempty_plans() -> None:
    circuit, specification, latency, bindings, result = (
        _coverage_boundary_fixture()
    )
    plan = _build_with_static_compilation(
        circuit,
        specification,
        latency,
        bindings,
        result,
    )
    assert plan.provenance["compilation_hash"] == result.compilation_hash
    assert (
        plan.provenance["compiler_spec_hash"]
        == result.compiler_spec.compiler_hash
    )
    assert "compute_allocation_policy" not in plan.provenance

    empty_circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(),
    )
    empty_specification = build_architecture_specification(
        empty_circuit, "1.1"
    )
    empty_requested_latency = OperationLatencyProfile()
    empty_bindings = resolve_resource_protocol_bindings(
        empty_specification,
        empty_requested_latency,
    )
    empty_latency = with_effective_arrivals(
        empty_requested_latency, empty_bindings
    )
    empty_result = DefaultCompilerPipeline().compile(
        empty_circuit,
        empty_specification,
        empty_latency,
    )
    empty_plan = _build_with_static_compilation(
        empty_circuit,
        empty_specification,
        empty_latency,
        empty_bindings,
        empty_result,
    )
    assert empty_plan.provenance["compilation_hash"] == (
        empty_result.compilation_hash
    )
    assert empty_plan.provenance["compiler_spec_hash"] == (
        empty_result.compiler_spec.compiler_hash
    )


def _two_magic_one_layer_circuit() -> FTCircuit:
    return FTCircuit(
        representation="clifford_t",
        num_qubits=2,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (
                    LogicalOperation("gate", "t", qubits=(0,)),
                    LogicalOperation("gate", "tdg", qubits=(1,)),
                ),
            ),
        ),
    )


def test_incremental_compilation_rejects_multi_magic_batch() -> None:
    circuit = _two_magic_one_layer_circuit()
    specification = build_architecture_specification(circuit, "1.1")
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification, requested_latency
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    bulk = DefaultCompilerPipeline(
        compiler_spec=canonical_compiler_spec(specification),
        magic_state_consumption="bulk_wave",
    ).compile(circuit, specification, latency)
    assert bulk.compute_units[0].source.magic_count == 2
    forged = replace(bulk, magic_state_consumption="incremental")

    with pytest.raises(
        LogicalCompilerValidationError, match="exactly cover"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            forged,
            policy=EvaluationPolicy(magic_state_consumption="incremental"),
        )
    assert exc_info.value.details["consumption_policy_mismatches"] == [
        {
            "layer": 0,
            "partition": 0,
            "batch": 0,
            "magic_count": 2,
            "maximum": 1,
        }
    ]

    incremental = DefaultCompilerPipeline(
        compiler_spec=bulk.compiler_spec,
        magic_state_consumption="incremental",
    ).compile(circuit, specification, latency)
    incremental_plan = _build_with_static_compilation(
        circuit,
        specification,
        latency,
        bindings,
        incremental,
        policy=EvaluationPolicy(magic_state_consumption="incremental"),
    )
    execute_metadata = [
        instruction.metadata
        for instruction in incremental_plan.program_dag.instructions
        if instruction.opcode == ArchitectureOpcode.EXECUTE_COMPUTE
    ]
    assert execute_metadata
    assert all(
        metadata["magic_consumption_policy"] == "incremental"
        for metadata in execute_metadata
    )


def test_plan_builder_rejects_batch_above_magic_buffer_capacity() -> None:
    circuit = _two_magic_one_layer_circuit()
    specification = build_architecture_specification(
        circuit,
        "1.1",
        policy_overrides={"protocols.magic_state.buffer_capacity": 1},
    )
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification,
        requested_latency,
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    compiled = DefaultCompilerPipeline(
        compiler_spec=canonical_compiler_spec(specification),
    ).compile(circuit, specification, latency)
    assert len(compiled.compute_units) == 2
    first = compiled.compute_units[0]
    merged_source = replace(
        first.source,
        batch_index=0,
        batch_count=1,
        operation_indices=(0, 1),
        operated_qubits=(0, 1),
        magic_count=2,
    )
    forged = replace(
        compiled,
        compute_units=(replace(first, source=merged_source),),
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="execution capacities"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            forged,
        )
    assert exc_info.value.details["magic_buffer_mismatches"] == [
        {
            "layer": 0,
            "partition": 0,
            "batch": 0,
            "magic_count": 2,
            "capacity": 1,
        }
    ]


def test_plan_builder_rejects_partition_above_compute_capacity() -> None:
    _base_circuit, specification, latency, bindings, _base_result = (
        _coverage_boundary_fixture()
    )
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=2,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "cx", qubits=(0, 1)),),
            ),
        ),
    )
    roomy_specification = build_architecture_specification(circuit, "1.1")
    compiled = DefaultCompilerPipeline(
        compiler_spec=canonical_compiler_spec(roomy_specification),
        defer_magic_routing=False,
    ).compile(circuit, roomy_specification, latency)
    forged = replace(
        compiled,
        architecture_hash=specification.architecture_hash,
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="canonical compute layout"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            forged,
        )
    assert exc_info.value.details[
        "insufficient_total_logical_capacity"
    ] is True
    assert any(
        error == {
            "layer": 0,
            "partition": 0,
            "batch": 0,
            "reason": "compute_residency_exceeds_capacity",
            "resident_qubits": 2,
            "capacity": 1,
        }
        for error in exc_info.value.details["unit_mapping_errors"]
    )


def test_plan_wire_requires_its_self_hash() -> None:
    document = normalize_json(_codec_plan().to_dict())
    document.pop("plan_hash")

    with pytest.raises(ValueError, match="Missing execution-plan fields"):
        ExecutionPlan.from_dict(document)


def test_plan_wire_rejects_optional_null_aliases() -> None:
    document = normalize_json(_codec_plan().to_dict())
    document["architectural_state"]["buffers"][0]["module"] = None

    with pytest.raises(ValueError, match="exact canonical JSON wire shape"):
        ExecutionPlan.from_dict(document)


def test_plan_wire_rejects_constructor_injected_provenance_alias() -> None:
    document = normalize_json(_codec_plan().to_dict())
    document["provenance"].pop("runtime_manifest_hash")

    with pytest.raises(ValueError, match="exact canonical JSON wire shape"):
        ExecutionPlan.from_dict(document)


@pytest.mark.parametrize("alias", [b"{}", bytearray(b"{}")])
def test_plan_json_requires_a_string(alias: object) -> None:
    with pytest.raises(TypeError, match="must be a string"):
        ExecutionPlan.from_json(alias)  # type: ignore[arg-type]


@pytest.mark.parametrize("nested", [False, True])
def test_plan_json_rejects_duplicate_object_keys(nested: bool) -> None:
    plan = _codec_plan()
    document = plan.to_json(indent=None)
    if nested:
        needle = f'"circuit_hash": "{plan.circuit_hash}"'
        replacement = f'"circuit_hash": "evil", {needle}'
    else:
        schema_version = plan.to_dict()["schema_version"]
        needle = f'"schema_version": "{schema_version}"'
        replacement = f'"schema_version": "evil", {needle}'
    document = document.replace(needle, replacement, 1)

    with pytest.raises(ValueError, match="Duplicate JSON"):
        ExecutionPlan.from_json(document)


@pytest.mark.parametrize("field", ("slots", "initial_contents"))
def test_buffer_wire_requires_canonical_sequence_fields(field: str) -> None:
    document = _codec_plan().buffers[0].to_dict()
    document.pop(field)

    with pytest.raises(ValueError, match="Missing buffer fields"):
        BufferSpec.from_dict(document)


def test_compiled_route_step_wire_requires_metadata() -> None:
    step = CompiledRouteStep(
        kind="test",
        group=0,
        operation_count=1,
        terminal_slots=("node/compute/region/slot_0",),
        path_node_count=1,
        path_edge_count=0,
        movement_count=0,
        metadata={},
    )
    document = step.to_dict()
    document.pop("metadata")

    with pytest.raises(
        LogicalCompilerValidationError, match="Missing compiled route-step"
    ):
        CompiledRouteStep.from_dict(document)


def test_plan_requires_exact_canonical_runtime_manifest_record() -> None:
    canonical = default_runtime_component_manifest().to_dict()
    incomplete = normalize_json(canonical)
    incomplete.pop("manifest_hash")
    incomplete["event_engine"].pop("component_hash")
    scheduler = incomplete["components"]["scheduler"]
    scheduler.pop("component_hash")
    scheduler["contract_version"] = 1

    with pytest.raises(ValueError, match="Missing runtime-manifest"):
        replace(_codec_plan(), runtime_components=incomplete, provenance={})

    # The manifest is itself a strict serialization authority; direct parsing
    # does not materialize missing hashes or coerce scalar aliases.
    with pytest.raises(ValueError, match="Missing runtime-manifest"):
        default_runtime_component_manifest().from_dict(incomplete)


def test_plan_wire_rejects_empty_runtime_manifest_even_with_matching_hash() -> None:
    document = normalize_json(_codec_plan().to_dict())
    document["runtime_components"] = {}
    _rehash_plan_document(document)

    with pytest.raises(ValueError, match="full canonical runtime manifest"):
        ExecutionPlan.from_dict(document)


def test_plan_wire_rejects_runtime_manifest_container_aliases() -> None:
    document = normalize_json(_codec_plan().to_dict())
    phases = document["runtime_components"]["event_engine"][
        "effective_config"
    ]["timestamp_phases"]
    document["runtime_components"]["event_engine"]["effective_config"][
        "timestamp_phases"
    ] = tuple(phases)
    _rehash_plan_document(document)

    with pytest.raises(ValueError, match="exact canonical JSON wire shape"):
        ExecutionPlan.from_dict(document)


def _magic_recipe_plan(*, token_kind: str = "magic_state") -> ExecutionPlan:
    instruction = ArchitectureInstruction(
        id=0,
        opcode=ArchitectureOpcode.EXECUTE_COMPUTE,
        qubits=(0,),
        consumes={"magic": 1},
        deferred_dispatch=MagicRouteDispatchRecipe(
            operation_indices=(0,),
            data_mapping={0: "node/compute/region/slot_0"},
        ),
    )
    return ExecutionPlan(
        circuit_hash="circuit",
        architecture_hash="architecture",
        latency_profile_hash="latency",
        policy=EvaluationPolicy(),
        program_dag=ProgramDAG((instruction,)),
        resource_dag=ResourceDAG(()),
        buffers=(BufferSpec("magic", 1, token_kind),),
        engines=(),
        runtime_components=default_runtime_component_manifest().to_dict(),
    )


def test_magic_route_recipe_requires_magic_state_buffer_kind() -> None:
    valid = _magic_recipe_plan()

    with pytest.raises(ValueError, match="installed magic_state buffer"):
        _magic_recipe_plan(token_kind="logical_qubit")

    document = normalize_json(valid.to_dict())
    document["architectural_state"]["buffers"][0]["token_kind"] = (
        "logical_qubit"
    )
    _rehash_plan_document(document)
    with pytest.raises(ValueError, match="installed magic_state buffer"):
        ExecutionPlan.from_dict(document)


def test_magic_route_recipe_rejects_mixed_resource_kinds() -> None:
    valid = _magic_recipe_plan()
    instruction = valid.program_dag.instructions[0]
    mixed_instruction = replace(
        instruction,
        consumes={"magic": 1, "bell": 1},
    )
    mixed_buffers = (
        *valid.buffers,
        BufferSpec("bell", 1, "bell_pair"),
    )

    with pytest.raises(ValueError, match="only installed magic_state buffers"):
        replace(
            valid,
            program_dag=ProgramDAG((mixed_instruction,)),
            buffers=mixed_buffers,
        )

    document = normalize_json(valid.to_dict())
    document["program_dag"]["instructions"][0]["consumes"]["bell"] = 1
    document["architectural_state"]["buffers"].append(
        BufferSpec("bell", 1, "bell_pair").to_dict()
    )
    _rehash_plan_document(document)
    with pytest.raises(ValueError, match="only installed magic_state buffers"):
        ExecutionPlan.from_dict(document)


def test_deferred_recipe_rejects_direct_noop_runtime_manifest() -> None:
    valid = _magic_recipe_plan()
    direct = default_direct_runtime_component_manifest().to_dict()

    with pytest.raises(
        ValueError, match="state-bound runtime-realizer component manifest"
    ):
        replace(valid, runtime_components=direct, provenance={})

    document = normalize_json(valid.to_dict())
    document["runtime_components"] = direct
    document["provenance"]["runtime_manifest_hash"] = direct["manifest_hash"]
    _rehash_plan_document(document)
    with pytest.raises(
        ValueError, match="state-bound runtime-realizer component manifest"
    ):
        ExecutionPlan.from_dict(document)


def test_plan_builder_selects_callback_manifest_for_deferred_recipes() -> None:
    circuit, specification, latency, bindings, result = (
        _coverage_boundary_fixture()
    )
    plan = _build_with_static_compilation(
        circuit,
        specification,
        latency,
        bindings,
        result,
    )
    assert plan.runtime_components["manifest_hash"] == (
        default_runtime_component_manifest().manifest_hash
    )

    class StaticPipeline:
        def compile(self, _circuit, _specification, _latency):
            return result

    with pytest.raises(
        ValueError, match="state-bound runtime-realizer component manifest"
    ):
        _compile_plan(
            circuit,
            specification,
            latency,
            EvaluationPolicy(),
            compiler_pipeline=StaticPipeline(),
            runtime_components={},
            resource_protocol_bindings=bindings,
        )


def _resource_move_recipe_plan(
    *, token_kind: str = "magic_state"
) -> ExecutionPlan:
    process = ResourceProcess(
        "move_magic",
        ArchitectureOpcode.MOVE_QUBITS,
        consumes={"source": 1},
        produces={"destination": 1},
        forwards={"source": "destination"},
        deferred_dispatch=ResourceMoveDispatchRecipe(
            entity_kind="magic_state"
        ),
    )
    return ExecutionPlan(
        circuit_hash="circuit",
        architecture_hash="architecture",
        latency_profile_hash="latency",
        policy=EvaluationPolicy(),
        program_dag=ProgramDAG(()),
        resource_dag=ResourceDAG((process,)),
        buffers=(
            BufferSpec("source", 1, token_kind),
            BufferSpec("destination", 1, token_kind),
        ),
        engines=(),
        runtime_components=default_runtime_component_manifest().to_dict(),
    )


def test_resource_move_recipe_requires_matching_forwarded_token_kinds() -> None:
    valid = _resource_move_recipe_plan()

    with pytest.raises(ValueError, match="must match recipe entity_kind"):
        _resource_move_recipe_plan(token_kind="logical_qubit")

    document = normalize_json(valid.to_dict())
    for buffer in document["architectural_state"]["buffers"]:
        buffer["token_kind"] = "logical_qubit"
    _rehash_plan_document(document)
    with pytest.raises(ValueError, match="must match recipe entity_kind"):
        ExecutionPlan.from_dict(document)


@pytest.mark.parametrize(
    ("operand_field", "slot", "message"),
    (
        ("source_slots", "elsewhere/slot_0", "required"),
        ("destination_slots", "elsewhere/slot_0", "completion"),
    ),
)
def test_program_move_slots_must_be_nested_under_state_locations(
    operand_field: str,
    slot: str,
    message: str,
) -> None:
    move = ArchitectureInstruction(
        id=0,
        opcode=ArchitectureOpcode.MOVE_QUBITS,
        qubits=(0,),
        required_locations={"q:0": "node/compute/source"},
        completion_locations={"q:0": "node/compute/destination"},
        move_operands=MoveOperands(
            source_slots={0: "node/compute/source/slot_0"},
            destination_slots={0: "node/compute/destination/slot_0"},
        ),
    )
    valid = replace(_codec_plan(), program_dag=ProgramDAG((move,)))
    document = normalize_json(valid.to_dict())
    document["program_dag"]["instructions"][0]["move_operands"][
        operand_field
    ]["0"] = slot
    _rehash_plan_document(document)

    with pytest.raises(ValueError, match=f"nested under its {message}"):
        ExecutionPlan.from_dict(document)


def test_plan_builder_rejects_noncanonical_initial_and_unit_slots() -> None:
    circuit, specification, latency, bindings, result = (
        _coverage_boundary_fixture()
    )
    unit = result.compute_units[0]
    forged = replace(
        result,
        initial_mapping={0: "forged/compute/data/slot"},
        compute_units=(
            replace(unit, mapping={0: "forged/compute/data/slot"}),
        ),
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="canonical compute layout"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            forged,
        )
    assert exc_info.value.details["invalid_initial_slots"] == [
        "forged/compute/data/slot"
    ]
    assert any(
        error["reason"] == "unknown_compute_data_slots"
        for error in exc_info.value.details["unit_mapping_errors"]
    )


def test_plan_builder_rejects_nondeferred_incomplete_initial_mapping() -> None:
    circuit, specification, latency, bindings, result = (
        _coverage_boundary_fixture()
    )
    forged = replace(result, initial_mapping={})

    with pytest.raises(
        LogicalCompilerValidationError, match="canonical compute layout"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            forged,
        )
    assert exc_info.value.details["expected_initial_qubits"] == [0]
    assert exc_info.value.details["actual_initial_qubits"] == []


def test_plan_builder_rejects_incomplete_nondeferred_residency() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=2,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "h", qubits=(0,)),),
            ),
        ),
    )
    specification = build_architecture_specification(circuit, "1.1")
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification, requested_latency
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    result = DefaultCompilerPipeline(defer_magic_routing=False).compile(
        circuit, specification, latency
    )
    unit = result.compute_units[0]
    forged = replace(
        result,
        compute_units=(
            replace(
                unit,
                mapping={0: result.initial_mapping[0]},
            ),
        ),
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="canonical compute layout"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            forged,
        )
    reasons = {
        error["reason"]
        for error in exc_info.value.details["unit_mapping_errors"]
    }
    assert "no_memory_requires_full_compute_residency" in reasons
    assert "nondeferred_requires_full_residency" in reasons


def _nondeferred_mapping_reentry_fixture():
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=3,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "h", qubits=(0,)),),
            ),
            LogicalLayer(
                1,
                (LogicalOperation("gate", "h", qubits=(2,)),),
            ),
        ),
    )
    specification = build_architecture_specification(circuit, "1.1")
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification, requested_latency
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    result = DefaultCompilerPipeline(defer_magic_routing=False).compile(
        circuit, specification, latency
    )
    assert len(result.compute_units) == 2
    return circuit, specification, latency, bindings, result


def test_default_nondeferred_units_keep_the_full_initial_mapping() -> None:
    circuit, specification, latency, bindings, result = (
        _nondeferred_mapping_reentry_fixture()
    )
    assert result.deferred_capacity_mapping is False
    assert [dict(unit.mapping) for unit in result.compute_units] == [
        dict(result.initial_mapping),
        dict(result.initial_mapping),
    ]

    # The complete result must pass the independent plan-boundary validator.
    _build_with_static_compilation(
        circuit,
        specification,
        latency,
        bindings,
        result,
    )


def test_plan_builder_rejects_nondeferred_reentry_slot_remap() -> None:
    circuit, specification, latency, bindings, result = (
        _nondeferred_mapping_reentry_fixture()
    )
    units = list(result.compute_units)
    remapped = dict(result.initial_mapping)
    remapped[0], remapped[2] = remapped[2], remapped[0]
    units[1] = replace(
        units[1],
        mapping=remapped,
    )
    forged = replace(result, compute_units=tuple(units))

    with pytest.raises(
        LogicalCompilerValidationError, match="canonical compute layout"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            forged,
        )
    mismatch = next(
        error
        for error in exc_info.value.details["unit_mapping_errors"]
        if error["reason"] == "unit_initial_mapping_mismatch"
        and error["layer"] == 1
    )
    assert mismatch["qubits"] == {
        0: {
            "initial": result.initial_mapping[0],
            "current": result.initial_mapping[2],
        },
        2: {
            "initial": result.initial_mapping[2],
            "current": result.initial_mapping[0],
        }
    }


def test_plan_builder_rejects_deferred_overlap_slot_discontinuity() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=3,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "cx", qubits=(0, 1)),),
            ),
            LogicalLayer(
                1,
                (LogicalOperation("gate", "cx", qubits=(1, 2)),),
            ),
        ),
    )
    specification = build_architecture_specification(
        circuit,
        "2.1",
        quantile_config=QuantileSizingConfig.uniform(0.0),
    )
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification, requested_latency
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    result = DefaultCompilerPipeline(defer_magic_routing=False).compile(
        circuit, specification, latency
    )
    assert result.deferred_capacity_mapping is True
    first, second = result.compute_units
    assert first.mapping[1] == second.mapping[1]
    forged_second = replace(
        second,
        mapping={1: second.mapping[2], 2: second.mapping[1]},
    )
    forged = replace(result, compute_units=(first, forged_second))

    with pytest.raises(
        LogicalCompilerValidationError, match="canonical compute layout"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            forged,
        )
    mismatch = next(
        error
        for error in exc_info.value.details["unit_mapping_errors"]
        if error["reason"] == "resident_mapping_discontinuity"
    )
    assert mismatch["previous_unit"] == {
        "layer": 0,
        "partition": 0,
        "batch": 0,
    }
    assert set(mismatch["qubits"]) == {1}


def test_plan_builder_requires_full_capacity_residency_in_every_unit() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=4,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "cx", qubits=(0, 1)),),
            ),
            LogicalLayer(
                1,
                (LogicalOperation("gate", "h", qubits=(2,)),),
            ),
        ),
    )
    specification = build_architecture_specification(
        circuit,
        "2.1",
        quantile_config=QuantileSizingConfig.uniform(1.0),
    )
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification, requested_latency
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    result = DefaultCompilerPipeline(defer_magic_routing=False).compile(
        circuit, specification, latency
    )
    assert [len(unit.mapping) for unit in result.compute_units] == [2, 2]
    second = result.compute_units[1]
    active_qubit = second.source.operated_qubits[0]
    forged = replace(
        result,
        compute_units=(
            result.compute_units[0],
            replace(
                second,
                mapping={active_qubit: second.mapping[active_qubit]},
            ),
        ),
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="canonical compute layout"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            forged,
        )
    mismatch = next(
        error
        for error in exc_info.value.details["unit_mapping_errors"]
        if error["reason"] == "residency_cardinality_mismatch"
    )
    assert mismatch["resident_qubits"] == 1
    assert mismatch["expected"] == 2


def _deferred_mapping_boundary_fixture():
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=3,
        num_clbits=0,
        layers=tuple(
            LogicalLayer(
                qubit,
                (LogicalOperation("gate", "h", qubits=(qubit,)),),
            )
            for qubit in range(3)
        ),
    )
    specification = build_architecture_specification(
        circuit,
        "2.1",
        quantile_config=QuantileSizingConfig.uniform(0.0),
    )
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification, requested_latency
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    result = DefaultCompilerPipeline(defer_magic_routing=False).compile(
        circuit, specification, latency
    )
    assert result.deferred_capacity_mapping is True
    return circuit, specification, latency, bindings, result


def test_default_compiler_rejects_ignored_deferred_mapping_override() -> None:
    circuit, specification, latency, _bindings, _result = (
        _deferred_mapping_boundary_fixture()
    )
    canonical = canonical_compiler_spec(specification)
    requested = replace(
        canonical,
        mapping=BackendSpec("sabre_na", {"seed": 7}),
    )

    with pytest.raises(
        LogicalCompilerValidationError,
        match="noncanonical logical mapping override would be ignored",
    ) as exc_info:
        DefaultCompilerPipeline(
            compiler_spec=requested,
            defer_magic_routing=False,
        ).compile(circuit, specification, latency)

    owned_compute = single_node_module(specification, "compute")
    assert owned_compute is not None
    assert exc_info.value.details == {
        "requested_mapping": requested.mapping.to_dict(),
        "canonical_mapping": canonical.mapping.to_dict(),
        "residency_policy": "preserve_resident_first_fit.v1",
        "logical_qubits": circuit.num_qubits,
        "compute_capacity": primary_qec_submodule(owned_compute[1]).capacity,
    }


def test_plan_builder_requires_capacity_derived_deferred_mapping_flag() -> None:
    circuit, specification, latency, bindings, result = (
        _deferred_mapping_boundary_fixture()
    )
    forged = replace(result, deferred_capacity_mapping=False)

    with pytest.raises(
        LogicalCompilerValidationError, match="canonical compute layout"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            forged,
        )
    assert exc_info.value.details[
        "expected_deferred_capacity_mapping"
    ] is True
    assert exc_info.value.details[
        "actual_deferred_capacity_mapping"
    ] is False


def test_plan_builder_rejects_deferred_mapping_without_memory_module() -> None:
    _base_circuit, no_memory_specification, _latency, _bindings, _result = (
        _coverage_boundary_fixture()
    )
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=2,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "h", qubits=(0,)),),
            ),
            LogicalLayer(
                1,
                (LogicalOperation("gate", "h", qubits=(1,)),),
            ),
        ),
    )
    memory_specification = build_architecture_specification(
        circuit,
        "2.1",
        quantile_config=QuantileSizingConfig.uniform(0.0),
    )
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        no_memory_specification, requested_latency
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    compiled = DefaultCompilerPipeline(defer_magic_routing=False).compile(
        circuit,
        memory_specification,
        latency,
    )
    assert compiled.deferred_capacity_mapping is True
    forged = replace(
        compiled,
        architecture_hash=no_memory_specification.architecture_hash,
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="canonical compute layout"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            no_memory_specification,
            latency,
            bindings,
            forged,
        )
    assert exc_info.value.details["deferred_mapping_requires_memory"] is True


def test_plan_builder_rejects_invalid_deferred_residency_claims() -> None:
    circuit, specification, latency, bindings, result = (
        _deferred_mapping_boundary_fixture()
    )
    unit = result.compute_units[0]
    forged = replace(
        result,
        compute_units=(
            replace(
                unit,
                mapping={
                    **dict(unit.mapping),
                    99: "forged/compute/data/slot",
                },
            ),
            *result.compute_units[1:],
        ),
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="canonical compute layout"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            specification,
            latency,
            bindings,
            forged,
        )
    reasons = {
        error["reason"]
        for error in exc_info.value.details["unit_mapping_errors"]
    }
    assert "compute_residency_exceeds_capacity" in reasons
    assert "mapping_qubits_outside_circuit" in reasons
    assert "unknown_compute_data_slots" in reasons


def test_underfull_deferred_wave_keeps_compute_and_memory_capacity_safe() -> None:
    circuit = FTCircuit(
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
    specification = build_architecture_specification(
        circuit,
        "2.1",
        quantile_config=QuantileSizingConfig.uniform(0.0),
    )
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification, requested_latency
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    result = DefaultCompilerPipeline(defer_magic_routing=False).compile(
        circuit, specification, latency
    )

    compute_entry = single_node_module(specification, "compute")
    memory_entry = single_node_module(specification, "memory")
    assert compute_entry is not None
    assert memory_entry is not None
    compute_capacity = primary_qec_submodule(compute_entry[1]).capacity
    memory_capacity = primary_qec_submodule(memory_entry[1]).capacity
    assert (compute_capacity, memory_capacity) == (2, 3)
    assert result.compute_units[0].source.partition.active_qubits == (0,)
    assert all(
        len(unit.mapping) == compute_capacity
        and circuit.num_qubits - len(unit.mapping) <= memory_capacity
        for unit in result.compute_units
    )

    plan = _build_with_static_compilation(
        circuit,
        specification,
        latency,
        bindings,
        result,
    )
    compute_location = f"{compute_entry[0].id}/{compute_entry[1].id}"
    memory_location = f"{memory_entry[0].id}/{memory_entry[1].id}"
    assert list(plan.initial_locations.values()).count(compute_location) == 2
    assert list(plan.initial_locations.values()).count(memory_location) == 3
    move_instructions = [
        instruction
        for instruction in plan.program_dag.instructions
        if instruction.opcode == ArchitectureOpcode.MOVE_QUBITS
    ]
    assert move_instructions
    assert all(
        instruction.move_operands is not None
        for instruction in move_instructions
    )
    assert {
        instruction.opcode
        for instruction in plan.program_dag.instructions
    } >= {
        ArchitectureOpcode.STORE_QUBITS,
        ArchitectureOpcode.LOAD_QUBITS,
    }


def test_plan_builder_rejects_insufficient_combined_compute_memory_capacity() -> None:
    def sequential_h_circuit(num_qubits: int) -> FTCircuit:
        return FTCircuit(
            representation="clifford_t",
            num_qubits=num_qubits,
            num_clbits=0,
            layers=tuple(
                LogicalLayer(
                    qubit,
                    (LogicalOperation("gate", "h", qubits=(qubit,)),),
                )
                for qubit in range(num_qubits)
            ),
        )

    circuit = sequential_h_circuit(4)
    target_specification = build_architecture_specification(
        sequential_h_circuit(3),
        "2.1",
        quantile_config=QuantileSizingConfig.uniform(0.0),
    )
    source_specification = build_architecture_specification(
        circuit,
        "2.1",
        quantile_config=QuantileSizingConfig.uniform(0.0),
    )
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        target_specification, requested_latency
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    compiled = DefaultCompilerPipeline(defer_magic_routing=False).compile(
        circuit,
        source_specification,
        latency,
    )
    forged = replace(
        compiled,
        architecture_hash=target_specification.architecture_hash,
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="canonical compute layout"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            target_specification,
            latency,
            bindings,
            forged,
        )
    assert exc_info.value.details["compute_capacity"] == 1
    assert exc_info.value.details["memory_capacity"] == 2
    assert exc_info.value.details["total_logical_capacity"] == 3
    assert exc_info.value.details[
        "insufficient_total_logical_capacity"
    ] is True
    assert all(
        error["reason"] == "memory_residency_exceeds_capacity"
        for error in exc_info.value.details["unit_mapping_errors"]
    )


def test_total_capacity_guard_does_not_depend_on_compute_units() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=4,
        num_clbits=0,
        layers=(),
    )
    target_sizing_circuit = replace(circuit, num_qubits=3)
    target_specification = build_architecture_specification(
        target_sizing_circuit,
        "2.1",
        quantile_config=QuantileSizingConfig.uniform(0.0),
    )
    source_specification = build_architecture_specification(
        circuit,
        "2.1",
        quantile_config=QuantileSizingConfig.uniform(0.0),
    )
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        target_specification, requested_latency
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    compiled = DefaultCompilerPipeline(defer_magic_routing=False).compile(
        circuit,
        source_specification,
        latency,
    )
    assert compiled.compute_units == ()
    forged = replace(
        compiled,
        architecture_hash=target_specification.architecture_hash,
    )

    with pytest.raises(
        LogicalCompilerValidationError, match="canonical compute layout"
    ) as exc_info:
        _build_with_static_compilation(
            circuit,
            target_specification,
            latency,
            bindings,
            forged,
        )
    assert exc_info.value.details[
        "insufficient_total_logical_capacity"
    ] is True
    assert exc_info.value.details["unit_mapping_errors"] == []
