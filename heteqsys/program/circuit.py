"""Versioned data contracts for synthesized FT workloads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, TYPE_CHECKING

from heteqsys.schema import deep_freeze_json

from .errors import WorkloadParseError

if TYPE_CHECKING:
    from .statistics import CircuitStatistics


WORKLOAD_SCHEMA_VERSION = "heteqsys.ft-workload.v2"
SUPPORTED_REPRESENTATIONS = frozenset({"clifford_t", "pbc"})


def canonical_json(data: Any) -> str:
    """Serialize JSON data deterministically for hashes and cache keys."""

    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_json_value(value: Any) -> Any:
    """Convert backend-owned values into stable, JSON-compatible values."""

    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("Non-finite values are not valid artifact metadata")
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): normalize_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [normalize_json_value(item) for item in value]
    if hasattr(value, "item"):
        return normalize_json_value(value.item())
    return str(value)


@dataclass(frozen=True)
class LogicalOperation:
    """One canonical logical operation in a fault-tolerant workload."""

    kind: str
    name: str
    qubits: tuple[int, ...] = field(default_factory=tuple)
    classical_bits: tuple[int, ...] = field(default_factory=tuple)
    parameters: tuple[Any, ...] = field(default_factory=tuple)
    pauli: Optional[str] = None

    def __post_init__(self) -> None:
        if self.kind not in {
            "gate",
            "measurement",
            "pauli_rotation",
            "pauli_measurement",
        }:
            raise WorkloadParseError(
                f"Unsupported FT instruction kind: {self.kind}",
                details={"kind": self.kind},
            )
        if self.kind in {"pauli_rotation", "pauli_measurement"} and self.pauli is None:
            raise WorkloadParseError("A Pauli instruction needs a Pauli string")
        if self.kind == "gate" and self.pauli is not None:
            raise WorkloadParseError("A gate instruction cannot carry a Pauli string")
        object.__setattr__(self, "qubits", tuple(int(qubit) for qubit in self.qubits))
        object.__setattr__(
            self,
            "classical_bits",
            tuple(int(bit) for bit in self.classical_bits),
        )
        if any(qubit < 0 for qubit in self.qubits):
            raise WorkloadParseError(
                "FT instruction qubit indices cannot be negative",
                details={"qubits": list(self.qubits)},
            )
        if any(bit < 0 for bit in self.classical_bits):
            raise WorkloadParseError(
                "FT instruction classical-bit indices cannot be negative",
                details={"classical_bits": list(self.classical_bits)},
            )
        object.__setattr__(
            self,
            "parameters",
            tuple(
                deep_freeze_json(normalize_json_value(parameter))
                for parameter in self.parameters
            ),
        )

    @property
    def weight(self) -> Optional[int]:
        if self.pauli is None:
            return None
        body = self.pauli[1:] if self.pauli[:1] in {"+", "-"} else self.pauli
        return sum(symbol != "I" for symbol in body)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": self.kind,
            "name": self.name,
            "qubits": list(self.qubits),
            "classical_bits": list(self.classical_bits),
            "parameters": [
                normalize_json_value(parameter) for parameter in self.parameters
            ],
        }
        if self.pauli is not None:
            result["pauli"] = self.pauli
            result["weight"] = self.weight
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogicalOperation":
        return cls(
            kind=str(data["kind"]),
            name=str(data["name"]),
            qubits=tuple(data.get("qubits", ())),
            classical_bits=tuple(data.get("classical_bits", ())),
            parameters=tuple(data.get("parameters", ())),
            pauli=data.get("pauli"),
        )


@dataclass(frozen=True)
class LogicalLayer:
    """Operations that share one conservative logical dependency layer."""

    index: int
    operations: tuple[LogicalOperation, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "index", int(self.index))
        object.__setattr__(self, "operations", tuple(self.operations))

    @property
    def active_qubits(self) -> tuple[int, ...]:
        return tuple(sorted({qubit for op in self.operations for qubit in op.qubits}))

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "active_qubits": list(self.active_qubits),
            "operations": [operation.to_dict() for operation in self.operations],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogicalLayer":
        return cls(
            index=int(data["index"]),
            operations=tuple(
                LogicalOperation.from_dict(operation)
                for operation in data.get("operations", ())
            ),
        )


@dataclass(frozen=True)
class FTCircuit:
    """Synthesized, compiler-facing FT circuit representation."""

    representation: str
    num_qubits: int
    num_clbits: int
    layers: tuple[LogicalLayer, ...]
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.representation not in SUPPORTED_REPRESENTATIONS:
            raise WorkloadParseError(
                f"Unsupported FT workload representation: {self.representation}",
                details={"representation": self.representation},
            )
        if self.num_qubits < 0 or self.num_clbits < 0:
            raise WorkloadParseError(
                "The number of quantum and classical bits cannot be negative"
            )
        object.__setattr__(self, "num_qubits", int(self.num_qubits))
        object.__setattr__(self, "num_clbits", int(self.num_clbits))
        object.__setattr__(self, "layers", tuple(self.layers))
        if not isinstance(self.provenance, Mapping):
            raise WorkloadParseError("FTCircuit provenance must be a mapping")
        object.__setattr__(
            self,
            "provenance",
            deep_freeze_json(normalize_json_value(self.provenance)),
        )
        for expected_index, layer in enumerate(self.layers):
            if layer.index != expected_index:
                raise WorkloadParseError(
                    "FTCircuit layer indices must be contiguous and zero-based",
                    details={"expected": expected_index, "actual": layer.index},
                )
            for operation in layer.operations:
                invalid_qubits = [
                    qubit for qubit in operation.qubits if qubit >= self.num_qubits
                ]
                if invalid_qubits:
                    raise WorkloadParseError(
                        "An FT instruction references an out-of-range qubit",
                        details={
                            "layer": layer.index,
                            "qubits": invalid_qubits,
                            "num_qubits": self.num_qubits,
                        },
                    )
                invalid_clbits = [
                    bit for bit in operation.classical_bits if bit >= self.num_clbits
                ]
                if invalid_clbits:
                    raise WorkloadParseError(
                        "An FT instruction references an out-of-range classical bit",
                        details={
                            "layer": layer.index,
                            "classical_bits": invalid_clbits,
                            "num_clbits": self.num_clbits,
                        },
                    )
                if operation.pauli is not None:
                    if (
                        operation.pauli[:1] not in {"+", "-"}
                        or len(operation.pauli) != self.num_qubits + 1
                        or set(operation.pauli[1:]) - {"I", "X", "Y", "Z"}
                    ):
                        raise WorkloadParseError(
                            "An FT Pauli instruction is not in canonical form",
                            details={
                                "layer": layer.index,
                                "pauli": operation.pauli,
                                "num_qubits": self.num_qubits,
                            },
                        )
                    active_qubits = tuple(
                        index
                        for index, symbol in enumerate(operation.pauli[1:])
                        if symbol != "I"
                    )
                    if operation.qubits != active_qubits:
                        raise WorkloadParseError(
                            "A Pauli instruction's active qubits do not match its string",
                            details={
                                "layer": layer.index,
                                "pauli": operation.pauli,
                                "qubits": list(operation.qubits),
                                "expected_qubits": list(active_qubits),
                            },
                        )

    @property
    def operation_count(self) -> int:
        return sum(len(layer.operations) for layer in self.layers)

    @cached_property
    def statistics(self) -> "CircuitStatistics":
        """Return cached, architecture-independent facts about this circuit."""

        from .statistics import circuit_statistics

        return circuit_statistics(self)

    def semantic_dict(self) -> dict[str, Any]:
        return {
            "schema_version": WORKLOAD_SCHEMA_VERSION,
            "representation": self.representation,
            "num_qubits": self.num_qubits,
            "num_clbits": self.num_clbits,
            "layers": [layer.to_dict() for layer in self.layers],
        }

    @cached_property
    def semantic_hash(self) -> str:
        """Return the immutable workload hash without re-encoding every layer."""

        return sha256_bytes(canonical_json(self.semantic_dict()).encode("ascii"))

    def to_dict(self) -> dict[str, Any]:
        result = self.semantic_dict()
        result["semantic_hash"] = self.semantic_hash
        result["provenance"] = normalize_json_value(self.provenance)
        return result

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FTCircuit":
        schema = data.get("schema_version")
        if schema != WORKLOAD_SCHEMA_VERSION:
            raise WorkloadParseError(
                f"Unsupported FTCircuit schema: {schema}",
                details={
                    "schema_version": schema,
                    "supported": WORKLOAD_SCHEMA_VERSION,
                },
            )
        workload = cls(
            representation=str(data["representation"]),
            num_qubits=int(data["num_qubits"]),
            num_clbits=int(data["num_clbits"]),
            layers=tuple(LogicalLayer.from_dict(layer) for layer in data.get("layers", ())),
            provenance=data.get("provenance", {}),
        )
        expected_hash = data.get("semantic_hash")
        if expected_hash is not None and expected_hash != workload.semantic_hash:
            raise WorkloadParseError(
                "FTCircuit semantic hash does not match its content",
                details={
                    "expected": expected_hash,
                    "actual": workload.semantic_hash,
                },
            )
        return workload

    @classmethod
    def from_json(cls, text: str) -> "FTCircuit":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise WorkloadParseError(
                "FTCircuit JSON is invalid",
                details={"line": exc.lineno, "column": exc.colno},
            ) from exc
        return cls.from_dict(data)


def make_layers(layer_operations: Iterable[Iterable[LogicalOperation]]) -> tuple[LogicalLayer, ...]:
    return tuple(
        LogicalLayer(index=index, operations=tuple(operations))
        for index, operations in enumerate(layer_operations)
    )
