"""Architecture-independent statistics derived from an :class:`FTCircuit`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .circuit import FTCircuit, LogicalOperation


@dataclass(frozen=True)
class CircuitStatistics:
    """Immutable circuit facts that sizing policies may consume.

    This record contains observations about a circuit, never architecture
    choices such as capacities, placements, or QEC bindings.
    """

    representation: str
    logical_qubits: int
    magic_states_per_layer: tuple[int, ...]
    operation_qubits_by_layer: tuple[tuple[tuple[int, ...], ...], ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.representation, str)
            or not self.representation
            or self.representation != self.representation.strip()
        ):
            raise ValueError("CircuitStatistics needs a representation")
        if (
            isinstance(self.logical_qubits, bool)
            or not isinstance(self.logical_qubits, int)
            or self.logical_qubits < 0
        ):
            raise ValueError(
                "CircuitStatistics logical_qubits must be a non-negative integer"
            )
        magic = tuple(self.magic_states_per_layer)
        operations = tuple(
            tuple(tuple(operation) for operation in layer)
            for layer in self.operation_qubits_by_layer
        )
        if len(magic) != len(operations):
            raise ValueError("CircuitStatistics layer distributions must align")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in magic
        ):
            raise ValueError("CircuitStatistics magic-state counts must be integers")
        if any(
            isinstance(qubit, bool) or not isinstance(qubit, int)
            for layer in operations
            for operation in layer
            for qubit in operation
        ):
            raise ValueError("CircuitStatistics qubit indices must be integers")
        if any(
            qubit < 0 or qubit >= self.logical_qubits
            for layer in operations
            for operation in layer
            for qubit in operation
        ):
            raise ValueError("CircuitStatistics contains an out-of-range qubit")
        if any(
            value < 0 or value > len(operations[index])
            for index, value in enumerate(magic)
        ):
            raise ValueError("CircuitStatistics magic-state counts are invalid")
        object.__setattr__(self, "magic_states_per_layer", magic)
        object.__setattr__(self, "operation_qubits_by_layer", operations)

    @property
    def logical_layers(self) -> int:
        """Number of logical dependency layers in the circuit."""

        return len(self.operation_qubits_by_layer)

    @property
    def active_qubits_per_layer(self) -> tuple[int, ...]:
        """Active logical-qubit count in each layer."""

        return tuple(
            len({qubit for operation in layer for qubit in operation})
            for layer in self.operation_qubits_by_layer
        )

    @property
    def operation_widths(self) -> tuple[int, ...]:
        """Logical width of every operation in source order."""

        return tuple(
            len(operation)
            for layer in self.operation_qubits_by_layer
            for operation in layer
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible representation of these circuit facts."""

        return {
            "representation": self.representation,
            "logical_qubits": self.logical_qubits,
            "logical_layers": self.logical_layers,
            "active_qubits_per_layer": list(self.active_qubits_per_layer),
            "magic_states_per_layer": list(self.magic_states_per_layer),
            "operation_widths": list(self.operation_widths),
            "operation_qubits_by_layer": [
                [list(operation) for operation in layer]
                for layer in self.operation_qubits_by_layer
            ],
        }


def operation_uses_magic_state(operation: LogicalOperation) -> bool:
    """Return whether one canonical operation consumes one magic state."""

    return operation.kind == "pauli_rotation" or operation.name.lower() in {
        "t",
        "tdg",
    }


def circuit_statistics(circuit: FTCircuit) -> CircuitStatistics:
    """Compute the architecture-independent facts of one FT circuit."""

    if not isinstance(circuit, FTCircuit):
        raise TypeError("circuit must be an FTCircuit")
    operations = tuple(
        tuple(tuple(operation.qubits) for operation in layer.operations)
        for layer in circuit.layers
    )
    return CircuitStatistics(
        representation=circuit.representation,
        logical_qubits=circuit.num_qubits,
        magic_states_per_layer=tuple(
            sum(operation_uses_magic_state(operation) for operation in layer.operations)
            for layer in circuit.layers
        ),
        operation_qubits_by_layer=operations,
    )


__all__ = [
    "CircuitStatistics",
    "circuit_statistics",
    "operation_uses_magic_state",
]
