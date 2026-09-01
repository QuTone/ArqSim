"""Finite program-dependence graph lowered to the architecture ISA."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from heteqsys.architecture.isa import (
    PROGRAM_OPCODES,
    ArchitectureInstruction,
)


@dataclass(frozen=True)
class ProgramDAG:
    instructions: tuple[ArchitectureInstruction, ...]

    def __post_init__(self) -> None:
        instructions = tuple(self.instructions)
        if any(
            not isinstance(instruction, ArchitectureInstruction)
            for instruction in instructions
        ):
            raise TypeError(
                "Program DAG instructions must be ArchitectureInstruction values"
            )
        instructions = tuple(sorted(instructions, key=lambda item: item.id))
        if [item.id for item in instructions] != list(range(len(instructions))):
            raise ValueError("Program instruction ids must be contiguous and zero-based")
        invalid = [
            item.opcode.value for item in instructions if item.opcode not in PROGRAM_OPCODES
        ]
        if invalid:
            raise ValueError(f"Resource-only opcodes in Program DAG: {invalid}")
        object.__setattr__(self, "instructions", instructions)

    def to_dict(self) -> dict[str, Any]:
        return {"instructions": [item.to_dict() for item in self.instructions]}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProgramDAG":
        if not isinstance(data, Mapping) or set(data) != {"instructions"}:
            raise ValueError("Program DAG must contain only 'instructions'")
        raw = data["instructions"]
        if type(raw) is not list:
            raise ValueError("Program DAG instructions must be an array")
        return cls(tuple(ArchitectureInstruction.from_dict(item) for item in raw))
