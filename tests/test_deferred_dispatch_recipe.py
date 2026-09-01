from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType

import pytest

from heteqsys.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    MagicRouteDispatchRecipe,
    MoveOperands,
    ResourceMoveDispatchRecipe,
    deferred_dispatch_recipe_from_dict,
)
from heteqsys.compiler import canonical_compiler_spec
from heteqsys.evaluation import (
    EvaluationError,
    EvaluationPolicy,
    ExecutionPlan,
    ProgramDAG,
    ResourceDAG,
    ResourceProcess,
    RuntimeComponentManifest,
    compile_and_lower,
    default_runtime_component_manifest,
    evaluate,
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
from heteqsys.schema import semantic_hash
from heteqsys.specification import build_architecture_specification


def _compile_plan(*args, **kwargs) -> ExecutionPlan:
    return compile_and_lower(*args, **kwargs)[1]


def _empty_plan_wire_document() -> dict:
    return ExecutionPlan(
        circuit_hash="circuit",
        architecture_hash="architecture",
        latency_profile_hash="latency",
        policy=EvaluationPolicy(),
        program_dag=ProgramDAG(()),
        resource_dag=ResourceDAG(()),
        buffers=(),
        engines=(),
    ).to_dict()


def _rehash_plan_wire_document(document: dict) -> None:
    document["plan_hash"] = semantic_hash(
        {key: value for key, value in document.items() if key != "plan_hash"}
    )


def test_tagged_dispatch_recipe_codec_is_strict_and_type_preserving() -> None:
    magic = MagicRouteDispatchRecipe(
        operation_indices=(0, 2),
        data_mapping={0: "node/compute/region/data0"},
    )
    move = ResourceMoveDispatchRecipe()

    assert deferred_dispatch_recipe_from_dict(magic.to_dict()) == magic
    assert deferred_dispatch_recipe_from_dict(move.to_dict()) == move

    invalid_documents = []
    unknown = magic.to_dict()
    unknown["extra"] = True
    invalid_documents.append(unknown)
    wrong_tag = magic.to_dict()
    wrong_tag["kind"] = "future_recipe"
    invalid_documents.append(wrong_tag)
    boolean_index = magic.to_dict()
    boolean_index["operation_indices"] = [True]
    invalid_documents.append(boolean_index)
    string_index = magic.to_dict()
    string_index["operation_indices"] = ["0"]
    invalid_documents.append(string_index)
    malformed_mapping = magic.to_dict()
    malformed_mapping["data_mapping"] = {"00": "slot"}
    invalid_documents.append(malformed_mapping)

    for document in invalid_documents:
        with pytest.raises(ValueError):
            deferred_dispatch_recipe_from_dict(document)


def test_plan_wire_rejects_rehashed_recursive_array_aliases() -> None:
    provenance_alias = _empty_plan_wire_document()
    provenance_alias["provenance"]["labels"] = ("deferred-dispatch",)
    _rehash_plan_wire_document(provenance_alias)
    with pytest.raises(ValueError, match="JSON arrays must be plain lists"):
        ExecutionPlan.from_dict(provenance_alias)

    policy_alias = _empty_plan_wire_document()
    policy_alias["policy"]["selected_layers"] = (0,)
    _rehash_plan_wire_document(policy_alias)
    with pytest.raises(ValueError, match="JSON arrays must be plain lists"):
        ExecutionPlan.from_dict(policy_alias)


def test_plan_wire_rejects_non_json_objects_keys_and_numbers() -> None:
    mapping_alias = _empty_plan_wire_document()
    mapping_alias["provenance"] = MappingProxyType(
        dict(mapping_alias["provenance"])
    )
    _rehash_plan_wire_document(mapping_alias)
    with pytest.raises(ValueError, match="JSON objects must be plain dictionaries"):
        ExecutionPlan.from_dict(mapping_alias)

    non_string_key = _empty_plan_wire_document()
    non_string_key["provenance"][1] = "alias"
    _rehash_plan_wire_document(non_string_key)
    with pytest.raises(TypeError, match="JSON object keys must be strings"):
        ExecutionPlan.from_dict(non_string_key)

    for invalid_number in (float("nan"), float("inf"), float("-inf")):
        non_finite = _empty_plan_wire_document()
        non_finite["provenance"]["invalid_number"] = invalid_number
        with pytest.raises(ValueError, match="finite JSON numbers"):
            ExecutionPlan.from_dict(non_finite)

    root_alias = MappingProxyType(_empty_plan_wire_document())
    with pytest.raises(ValueError, match="JSON objects must be plain dictionaries"):
        ExecutionPlan.from_dict(root_alias)


def _program_move_plan() -> ExecutionPlan:
    source = "node/compute/source"
    destination = "node/compute/destination"
    instruction = ArchitectureInstruction(
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
    return ExecutionPlan(
        circuit_hash="circuit",
        architecture_hash="architecture",
        latency_profile_hash="latency",
        policy=EvaluationPolicy(),
        program_dag=ProgramDAG((instruction,)),
        resource_dag=ResourceDAG(()),
        buffers=(),
        engines=(),
        initial_locations={"q:0": source},
    )


def test_plan_program_move_requires_logical_qubit_operands() -> None:
    valid = _program_move_plan()
    instruction = valid.program_dag.instructions[0]
    magic_operands = MoveOperands(
        source_slots=instruction.move_operands.source_slots,
        destination_slots=instruction.move_operands.destination_slots,
        entity_kind="magic_state",
    )
    with pytest.raises(ValueError, match="entity_kind must be logical_qubit"):
        replace(
            valid,
            program_dag=ProgramDAG(
                (replace(instruction, move_operands=magic_operands),)
            ),
        )

    document = valid.to_dict()
    document["program_dag"]["instructions"][0]["move_operands"][
        "entity_kind"
    ] = "magic_state"
    _rehash_plan_wire_document(document)
    with pytest.raises(ValueError, match="entity_kind must be logical_qubit"):
        ExecutionPlan.from_dict(document)


@pytest.mark.parametrize(
    "claim_field",
    ("required_locations", "completion_locations"),
)
def test_plan_program_move_location_claims_exactly_match_operands(
    claim_field: str,
) -> None:
    valid = _program_move_plan()
    instruction = valid.program_dag.instructions[0]
    claims = dict(getattr(instruction, claim_field))
    claims["q:1"] = "node/compute/extra"
    with pytest.raises(ValueError, match="no extra claims"):
        replace(
            valid,
            program_dag=ProgramDAG(
                (replace(instruction, **{claim_field: claims}),)
            ),
        )

    document = valid.to_dict()
    document["program_dag"]["instructions"][0][claim_field]["q:1"] = (
        "node/compute/extra"
    )
    _rehash_plan_wire_document(document)
    with pytest.raises(ValueError, match="no extra claims"):
        ExecutionPlan.from_dict(document)


@pytest.mark.parametrize(
    "legacy_key",
    ("deferred_until_dispatch", "dispatch_deferred"),
)
def test_resource_process_rejects_nested_legacy_dispatch_metadata(
    legacy_key: str,
) -> None:
    with pytest.raises(ValueError, match="metadata.route_metrics"):
        ResourceProcess(
            id="prepare",
            opcode=ArchitectureOpcode.PREPARE_MAGIC_STATE,
            produces={"magic": 1},
            metadata={"route_metrics": {legacy_key: True}},
        )

    document = ResourceProcess(
        id="prepare",
        opcode=ArchitectureOpcode.PREPARE_MAGIC_STATE,
        produces={"magic": 1},
    ).to_dict()
    document["metadata"] = {"route_metrics": {legacy_key: True}}
    with pytest.raises(ValueError, match="metadata.route_metrics"):
        ResourceProcess.from_dict(document)


def test_move_operands_are_not_hidden_in_instruction_metadata() -> None:
    instruction = ArchitectureInstruction(
        id=0,
        opcode=ArchitectureOpcode.MOVE_QUBITS,
        qubits=(1,),
        move_operands=MoveOperands(
            source_slots={1: "source/slot0"},
            destination_slots={1: "destination/slot0"},
        ),
        metadata={"direction": "out"},
    )
    document = instruction.to_dict()
    restored = ArchitectureInstruction.from_dict(document)

    assert restored == instruction
    assert "source_slots" not in restored.metadata
    assert "destination_slots" not in restored.metadata
    assert restored.move_operands is not None
    assert restored.move_operands.source_slots == {1: "source/slot0"}

    with pytest.raises(
        ValueError, match="Legacy dispatch control|cannot be duplicated"
    ):
        ArchitectureInstruction(
            id=0,
            opcode=ArchitectureOpcode.MOVE_QUBITS,
            qubits=(1,),
            move_operands=instruction.move_operands,
            metadata={"source_slots": {"1": "legacy"}},
        )

    forged = deepcopy(document)
    forged["id"] = True
    with pytest.raises(ValueError, match="id must be an integer"):
        ArchitectureInstruction.from_dict(forged)
    forged = deepcopy(document)
    forged["unknown"] = None
    with pytest.raises(ValueError, match="Unknown architecture instruction"):
        ArchitectureInstruction.from_dict(forged)


def test_resource_process_v4_codec_rejects_aliases_and_scalar_coercion() -> None:
    process = ResourceProcess(
        id="move",
        opcode=ArchitectureOpcode.MOVE_QUBITS,
        consumes={"source": 1},
        produces={"destination": 1},
        forwards={"source": "destination"},
        deferred_dispatch=ResourceMoveDispatchRecipe(),
    )
    document = process.to_dict()
    assert ResourceProcess.from_dict(document) == process

    alias = deepcopy(document)
    alias["arrival_model"] = alias.pop("arrival_distribution")
    with pytest.raises(ValueError, match="Unknown resource-process"):
        ResourceProcess.from_dict(alias)
    boolean_parallelism = deepcopy(document)
    boolean_parallelism["parallelism"] = True
    with pytest.raises(ValueError, match="parallelism must be an integer"):
        ResourceProcess.from_dict(boolean_parallelism)


def test_typed_deferred_recipes_reject_incoherent_operation_claims() -> None:
    with pytest.raises(ValueError, match="slot ids must be unique"):
        MoveOperands(
            source_slots={0: "slot_0", 1: "slot_0"},
            destination_slots={0: "slot_1", 1: "slot_2"},
        )
    with pytest.raises(ValueError, match="slot ids must be unique"):
        MagicRouteDispatchRecipe(
            operation_indices=(0,),
            data_mapping={0: "slot_0", 1: "slot_0"},
        )

    with pytest.raises(ValueError, match="must cover every operated"):
        ArchitectureInstruction(
            id=0,
            opcode=ArchitectureOpcode.EXECUTE_COMPUTE,
            qubits=(0, 1),
            consumes={"magic": 1},
            deferred_dispatch=MagicRouteDispatchRecipe(
                operation_indices=(0,),
                data_mapping={0: "node/compute/region/slot_0"},
            ),
        )

    for forwards in ({}, {"source": "destination", "other": "extra"}):
        with pytest.raises(ValueError, match="exactly one forwarded flow"):
            ResourceProcess(
                id="move",
                opcode=ArchitectureOpcode.MOVE_QUBITS,
                consumes={"source": 1, "other": 1},
                produces={"destination": 1, "extra": 1},
                forwards=forwards,
                deferred_dispatch=ResourceMoveDispatchRecipe(),
            )

    with pytest.raises(ValueError, match="must preserve token count"):
        ResourceProcess(
            id="move",
            opcode=ArchitectureOpcode.MOVE_QUBITS,
            consumes={"source": 2},
            produces={"destination": 1},
            forwards={"source": "destination"},
            deferred_dispatch=ResourceMoveDispatchRecipe(),
        )


@pytest.mark.parametrize(
    "duplicate",
    (
        "data_mapping",
        "deferred_until_dispatch",
        "dispatch_deferred",
    ),
)
def test_magic_recipe_rejects_generic_metadata_duplicates(
    duplicate: str,
) -> None:
    with pytest.raises(
        ValueError, match="dispatch control|cannot be duplicated in metadata"
    ):
        ArchitectureInstruction(
            id=0,
            opcode=ArchitectureOpcode.EXECUTE_COMPUTE,
            qubits=(0,),
            consumes={"magic": 1},
            metadata={duplicate: False},
            deferred_dispatch=MagicRouteDispatchRecipe(
                operation_indices=(0,),
                data_mapping={0: "node/compute/region/slot_0"},
            ),
        )


@pytest.mark.parametrize(
    "duplicate",
    ("entity_kind", "deferred_until_dispatch", "dispatch_deferred"),
)
def test_resource_move_recipe_rejects_generic_metadata_duplicates(
    duplicate: str,
) -> None:
    with pytest.raises(
        ValueError, match="dispatch control|cannot be duplicated in metadata"
    ):
        ResourceProcess(
            id="move",
            opcode=ArchitectureOpcode.MOVE_QUBITS,
            consumes={"source": 1},
            produces={"destination": 1},
            forwards={"source": "destination"},
            metadata={duplicate: False},
            deferred_dispatch=ResourceMoveDispatchRecipe(),
        )


def test_legacy_dispatch_markers_are_rejected_without_a_typed_recipe() -> None:
    with pytest.raises(ValueError, match="Legacy dispatch control"):
        ArchitectureInstruction(
            id=0,
            opcode=ArchitectureOpcode.FENCE,
            metadata={"runtime_route_request": {}},
        )
    with pytest.raises(ValueError, match="Legacy dispatch control"):
        ResourceProcess(
            id="prepare",
            opcode=ArchitectureOpcode.PREPARE_MAGIC_STATE,
            metadata={"runtime_move_compilation": True},
        )
    with pytest.raises(ValueError, match="route_metrics"):
        ArchitectureInstruction(
            id=0,
            opcode=ArchitectureOpcode.FENCE,
            metadata={
                "route_metrics": {"deferred_until_dispatch": True}
            },
        )
def test_serialized_plan_executes_both_typed_deferred_recipes() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "t", qubits=(0,)),),
            ),
        ),
    )
    specification = build_architecture_specification(circuit, "1.1")
    latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(specification, latency)
    latency = with_effective_arrivals(latency, bindings)
    compiler_spec = canonical_compiler_spec(specification)
    runtime_instruction_compiler = build_runtime_instruction_compiler(
        circuit,
        specification,
        compiler_spec,
        latency,
    )
    runtime_resource_compiler = build_runtime_resource_compiler(
        specification,
        compiler_spec,
        latency,
    )
    manifest = RuntimeComponentManifest.from_dict(
        default_runtime_component_manifest().to_dict()
    )
    components = build_runtime_component_set(
        manifest,
        runtime_instruction_compiler=runtime_instruction_compiler,
        runtime_resource_compiler=runtime_resource_compiler,
    )
    plan = _compile_plan(
        circuit,
        specification,
        latency,
        EvaluationPolicy(trace_level="full"),
        compiler_spec=compiler_spec,
        runtime_components=components.manifest.to_dict(),
        resource_protocol_bindings=bindings,
    )

    assert any(
        isinstance(instruction.deferred_dispatch, MagicRouteDispatchRecipe)
        for instruction in plan.program_dag.instructions
    )
    for instruction in plan.program_dag.instructions:
        if isinstance(instruction.deferred_dispatch, MagicRouteDispatchRecipe):
            assert "operation_indices" not in instruction.metadata
            assert "mapping" not in instruction.metadata
            assert "runtime_route_request" not in instruction.metadata
            with pytest.raises(ValueError, match="cannot be duplicated"):
                ArchitectureInstruction(
                    id=0,
                    opcode=ArchitectureOpcode.EXECUTE_COMPUTE,
                    layer_index=0,
                    qubits=(0,),
                    consumes={"magic_compute": 1},
                    deferred_dispatch=instruction.deferred_dispatch,
                    metadata={"operation_indices": [0]},
                )
    assert any(
        isinstance(process.deferred_dispatch, ResourceMoveDispatchRecipe)
        for process in plan.resource_dag.processes
    )

    restored = ExecutionPlan.from_json(plan.to_json())
    assert restored.to_dict() == plan.to_dict()
    result = evaluate(restored, runtime_components=components)

    assert result.completed_program_instructions == len(
        restored.program_dag.instructions
    )
    assert all(result.invariant_checks.values())
    compute = next(
        event
        for event in result.events
        if event.opcode == ArchitectureOpcode.EXECUTE_COMPUTE
    )
    delivery = next(
        event
        for event in result.events
        if event.process_id == "deliver_magic_local"
    )
    assert "route_binding_status" not in compute.metadata
    assert (
        compute.metadata["runtime_route_resolution"]
        == "dispatch_time_joint_compilation"
    )
    assert "compiler_binding_time" not in delivery.metadata
    assert "moved_tokens" not in delivery.metadata
    assert "runtime_route_request" not in compute.metadata
    assert "runtime_move_compilation" not in delivery.metadata


def test_builtin_magic_route_accepts_an_alternate_magic_buffer_id() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "t", qubits=(0,)),),
            ),
        ),
    )
    specification = build_architecture_specification(circuit, "1.1")
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification,
        requested_latency,
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    compiler_spec = canonical_compiler_spec(specification)
    components = build_runtime_component_set(
        default_runtime_component_manifest(),
        runtime_instruction_compiler=build_runtime_instruction_compiler(
            circuit,
            specification,
            compiler_spec,
            latency,
        ),
        runtime_resource_compiler=build_runtime_resource_compiler(
            specification,
            compiler_spec,
            latency,
        ),
    )
    plan = _compile_plan(
        circuit,
        specification,
        latency,
        EvaluationPolicy(trace_level="full"),
        compiler_spec=compiler_spec,
        runtime_components=components.manifest.to_dict(),
        resource_protocol_bindings=bindings,
    )

    replacement_id = "alternate_magic"

    def renamed_claims(claims):
        return {
            replacement_id if key == "magic_compute" else key: amount
            for key, amount in claims.items()
        }

    def renamed_forwards(forwards):
        return {
            replacement_id if source == "magic_compute" else source: (
                replacement_id if destination == "magic_compute" else destination
            )
            for source, destination in forwards.items()
        }

    renamed_program = ProgramDAG(
        tuple(
            replace(
                instruction,
                consumes=renamed_claims(instruction.consumes),
                produces=renamed_claims(instruction.produces),
                forwards=renamed_forwards(instruction.forwards),
            )
            for instruction in plan.program_dag.instructions
        )
    )
    renamed_resources = ResourceDAG(
        tuple(
            replace(
                process,
                consumes=renamed_claims(process.consumes),
                produces=renamed_claims(process.produces),
                forwards=renamed_forwards(process.forwards),
            )
            for process in plan.resource_dag.processes
        )
    )
    renamed_buffers = tuple(
        replace(buffer, id=replacement_id)
        if buffer.id == "magic_compute"
        else buffer
        for buffer in plan.buffers
    )
    alternate_plan = replace(
        plan,
        program_dag=renamed_program,
        resource_dag=renamed_resources,
        buffers=renamed_buffers,
    )

    restored = ExecutionPlan.from_json(alternate_plan.to_json())
    result = evaluate(restored, runtime_components=components)
    compute = next(
        event
        for event in result.events
        if event.opcode == ArchitectureOpcode.EXECUTE_COMPUTE
    )
    assert compute.consumed_slots[replacement_id]
    assert "route_binding_status" not in compute.metadata
    assert (
        compute.metadata["runtime_route_resolution"]
        == "dispatch_time_joint_compilation"
    )
    assert all(result.invariant_checks.values())


def test_builtin_callbacks_cannot_execute_a_plan_from_another_latency_context() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "t", qubits=(0,)),),
            ),
        ),
    )
    specification = build_architecture_specification(circuit, "1.1")
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification,
        requested_latency,
    )
    latency = with_effective_arrivals(
        requested_latency,
        bindings,
    )
    compiler_spec = canonical_compiler_spec(specification)
    correct_components = build_runtime_component_set(
        default_runtime_component_manifest(),
        runtime_instruction_compiler=build_runtime_instruction_compiler(
            circuit,
            specification,
            compiler_spec,
            latency,
        ),
        runtime_resource_compiler=build_runtime_resource_compiler(
            specification,
            compiler_spec,
            latency,
        ),
    )
    plan = _compile_plan(
        circuit,
        specification,
        latency,
        EvaluationPolicy(trace_level="full"),
        compiler_spec=compiler_spec,
        runtime_components=correct_components.manifest.to_dict(),
        resource_protocol_bindings=bindings,
    )

    wrong_latency = replace(
        latency,
        local_magic_delivery_s=latency.local_magic_delivery_s + 1e-9,
    )
    wrong_instruction_compiler = build_runtime_instruction_compiler(
        circuit,
        specification,
        compiler_spec,
        wrong_latency,
    )
    wrong_components = build_runtime_component_set(
        default_runtime_component_manifest(),
        runtime_instruction_compiler=wrong_instruction_compiler,
        runtime_resource_compiler=build_runtime_resource_compiler(
            specification,
            compiler_spec,
            wrong_latency,
        ),
    )

    assert wrong_components.manifest.manifest_hash == (
        correct_components.manifest.manifest_hash
    )
    source_context = getattr(
        wrong_instruction_compiler,
        "__arqsim_source_context__",
    )
    with pytest.raises(TypeError):
        source_context["latency_profile_hash"] = plan.latency_profile_hash
    with pytest.raises(EvaluationError, match="source context.*authorities"):
        evaluate(plan, runtime_components=wrong_components)
