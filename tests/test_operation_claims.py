from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from heteqsys.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    OperationClaims,
)
from heteqsys.evaluation import ProgramDAG, ResourceProcess


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
