from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from arqsim.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    OperationClaims,
    ProgramWorkTemplate,
)
from arqsim.architecture.recipes import ProgramRecipeMember
from arqsim.evaluation import ProgramDAG, ResourceProcess


def test_program_and_resource_operations_share_one_flat_claim_contract() -> None:
    instruction = ArchitectureInstruction(
        0,
        ArchitectureOpcode.EXECUTE_COMPUTE,
        consumes={"magic": 1},
        engines={"compute": 1},
        target_modules=("node/compute",),
    )
    process = ResourceProcess(
        "factory",
        ArchitectureOpcode.PREPARE_MAGIC_STATE,
        produces={"magic": 1},
        engines={"factory": 1},
        target_modules=("node/factory",),
    )

    assert isinstance(instruction, OperationClaims)
    assert isinstance(process, OperationClaims)
    assert not hasattr(OperationClaims, "to_dict")
    assert "claims" not in instruction.to_dict()
    assert "claims" not in process.to_dict()
    assert ArchitectureInstruction.from_dict(instruction.to_dict()) == instruction
    assert ResourceProcess.from_dict(process.to_dict()) == process


@pytest.mark.parametrize(
    ("operation_type", "identifier", "opcode", "claim_name"),
    [
        (
            ArchitectureInstruction,
            0,
            ArchitectureOpcode.EXECUTE_COMPUTE,
            "consumes",
        ),
        (
            ResourceProcess,
            "factory",
            ArchitectureOpcode.PREPARE_MAGIC_STATE,
            "produces",
        ),
    ],
)
def test_shared_claim_contract_has_identical_strict_quantity_rules(
    operation_type: type[OperationClaims],
    identifier: int | str,
    opcode: ArchitectureOpcode,
    claim_name: str,
) -> None:
    with pytest.raises(TypeError, match="must map strings to integers"):
        operation_type(identifier, opcode, **{claim_name: {"buffer": True}})
    with pytest.raises(ValueError, match="quantities must be positive"):
        operation_type(identifier, opcode, **{claim_name: {"buffer": 0}})


def test_shared_claim_contract_detaches_and_normalizes_inputs() -> None:
    consumed = {"magic": 1}
    modules = ("node/z", "node/a", "node/z")
    instruction = ArchitectureInstruction(
        0,
        ArchitectureOpcode.EXECUTE_COMPUTE,
        consumes=consumed,
        target_modules=modules,
    )
    consumed["magic"] = 2

    assert dict(instruction.consumes) == {"magic": 1}
    assert instruction.target_modules == ("node/a", "node/z")
    with pytest.raises(TypeError):
        instruction.consumes["magic"] = 2  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        instruction.target_modules = ()  # type: ignore[misc]


def test_program_dag_rejects_structural_instruction_lookalikes() -> None:
    class InstructionLookalike:
        id = 0
        opcode = ArchitectureOpcode.FENCE

        @staticmethod
        def to_dict() -> dict[str, object]:
            return {}

    with pytest.raises(TypeError, match="ArchitectureInstruction values"):
        ProgramDAG((InstructionLookalike(),))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "claims",
    (
        {"qubits": (0,)},
        {"consumes": {"input": 1}},
        {"produces": {"output": 1}},
        {"forwards": {"input": "output"}},
        {"engines": {"compute": 1}},
        {"required_locations": {"q:0": "left"}},
        {"completion_locations": {"q:0": "right"}},
        {"target_modules": ("node/compute",)},
        {"target_links": ("link",)},
    ),
)
def test_fence_rejects_execution_state_capacity_and_locus_claims(
    claims: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="FENCE.*execution claims"):
        ArchitectureInstruction(0, ArchitectureOpcode.FENCE, **claims)


def test_fence_is_a_zero_duration_ordering_boundary() -> None:
    with pytest.raises(ValueError, match="duration_s must be zero"):
        ArchitectureInstruction(
            0,
            ArchitectureOpcode.FENCE,
            duration_s=1.0,
        )


@pytest.mark.parametrize(
    ("operation", "metadata"),
    (
        (
            ArchitectureInstruction,
            {"engines": {"hidden": 1}},
        ),
        (
            ResourceProcess,
            {"dispatch_policy": "eager_available"},
        ),
    ),
)
def test_typed_operation_semantics_cannot_be_hidden_in_metadata(
    operation: type[ArchitectureInstruction] | type[ResourceProcess],
    metadata: dict[str, object],
) -> None:
    identifier: int | str
    opcode: ArchitectureOpcode
    if operation is ArchitectureInstruction:
        identifier = 0
        opcode = ArchitectureOpcode.EXECUTE_COMPUTE
    else:
        identifier = "move"
        opcode = ArchitectureOpcode.MOVE_QUBITS
    with pytest.raises(ValueError, match="Typed operation semantics"):
        operation(identifier, opcode, metadata=metadata)


def test_historical_gate_metadata_exception_is_compute_only() -> None:
    compute = ArchitectureInstruction(
        0,
        ArchitectureOpcode.EXECUTE_COMPUTE,
        metadata={"gates": {"h": [0]}},
    )
    assert compute.metadata["gates"] == {"h": (0,)}

    with pytest.raises(ValueError, match="metadata.gates.*EXECUTE_COMPUTE"):
        ResourceProcess(
            "move",
            ArchitectureOpcode.MOVE_QUBITS,
            metadata={"gates": {"h": [0]}},
        )


def test_gadget_continuation_fields_are_compute_only() -> None:
    reaction = ProgramWorkTemplate(
        recipe_members=(ProgramRecipeMember("recipe", 0),),
        step="reaction",
        opcode=ArchitectureOpcode.CLASSICAL_REACTION,
        duration_s=0.1,
    )
    with pytest.raises(ValueError, match="valid only for EXECUTE_COMPUTE"):
        ArchitectureInstruction(
            0,
            ArchitectureOpcode.FENCE,
            continuation_templates=(reaction,),
        )


def test_prepare_process_requires_output_and_nonempty_protocol() -> None:
    with pytest.raises(ValueError, match="produced output"):
        ResourceProcess(
            "factory",
            ArchitectureOpcode.PREPARE_MAGIC_STATE,
            duration_s=1.0,
        )
    with pytest.raises(ValueError, match="protocol must be non-empty"):
        ResourceProcess(
            "factory",
            ArchitectureOpcode.PREPARE_MAGIC_STATE,
            duration_s=1.0,
            produces={"magic": 1},
            protocol=" ",
        )
    with pytest.raises(ValueError, match="cannot forward"):
        ResourceProcess(
            "factory",
            ArchitectureOpcode.PREPARE_MAGIC_STATE,
            duration_s=1.0,
            consumes={"raw": 1},
            produces={"magic": 1},
            forwards={"raw": "magic"},
        )


def test_program_teleport_requires_link_resource_and_location_transition() -> None:
    valid = {
        "qubits": (0,),
        "consumes": {"bell:link": 1},
        "required_locations": {"q:0": "left/output"},
        "completion_locations": {"q:0": "right/input"},
        "target_links": ("link",),
    }
    ArchitectureInstruction(0, ArchitectureOpcode.TELEPORT_QUBITS, **valid)

    for field, value, message in (
        ("target_links", (), "target link"),
        ("consumes", {}, "teleportation resource"),
        ("required_locations", {}, "exactly cover"),
        ("completion_locations", {"q:0": "left/output"}, "change every"),
    ):
        with pytest.raises(ValueError, match=message):
            ArchitectureInstruction(
                0,
                ArchitectureOpcode.TELEPORT_QUBITS,
                **{**valid, field: value},
            )


def test_resource_teleport_requires_link_and_forwarded_token_flow() -> None:
    valid = {
        "consumes": {"factory": 1, "bell:link": 1},
        "produces": {"compute": 1},
        "forwards": {"factory": "compute"},
        "target_links": ("link",),
    }
    ResourceProcess("delivery", ArchitectureOpcode.TELEPORT_QUBITS, **valid)

    for field, value, message in (
        ("target_links", (), "target link"),
        ("consumes", {}, "consume input resources"),
        ("produces", {}, "consume input resources"),
        ("forwards", {}, "forward the teleported token"),
    ):
        with pytest.raises(ValueError, match=message):
            ResourceProcess(
                "delivery",
                ArchitectureOpcode.TELEPORT_QUBITS,
                **{**valid, field: value},
            )

    with pytest.raises(ValueError, match="different buffer"):
        ResourceProcess(
            "delivery",
            ArchitectureOpcode.TELEPORT_QUBITS,
            consumes={"state": 1},
            produces={"state": 1},
            forwards={"state": "state"},
            target_links=("link",),
        )
