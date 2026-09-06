"""Versioned contracts for the canonical, compiler-facing logical IR."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, TYPE_CHECKING

from arqsim.schema import deep_freeze_json

from .errors import WorkloadParseError

if TYPE_CHECKING:
    from .statistics import CircuitStatistics


WORKLOAD_SCHEMA_VERSION = "arqsim.ft-workload.v2"
_OPERATION_KINDS = frozenset(
    {"gate", "measurement", "pauli_rotation", "pauli_measurement"}
)


def _canonical_string(
    value: Any,
    *,
    field_name: str,
    lowercase: bool = False,
) -> str:
    if (
        type(value) is not str
        or not value
        or value != value.strip()
        or (lowercase and value != value.lower())
    ):
        qualifier = "lowercase, " if lowercase else ""
        raise WorkloadParseError(
            f"{field_name} must be a non-empty {qualifier}trimmed string",
            details={"field": field_name, "value": value},
        )
    return value


def _non_negative_integer(value: Any, *, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise WorkloadParseError(
            f"{field_name} must be a non-negative integer",
            details={"field": field_name, "value": value},
        )
    return value


def _sequence(value: Any, *, field_name: str) -> tuple[Any, ...]:
    if type(value) not in {list, tuple}:
        raise WorkloadParseError(
            f"{field_name} must be an array",
            details={"field": field_name, "type": type(value).__name__},
        )
    return tuple(value)


def _indices(value: Any, *, field_name: str) -> tuple[int, ...]:
    values = _sequence(value, field_name=field_name)
    for index, item in enumerate(values):
        if type(item) is not int or item < 0:
            raise WorkloadParseError(
                f"{field_name} must contain non-negative integers",
                details={
                    "field": field_name,
                    "index": index,
                    "value": item,
                },
            )
    if len(set(values)) != len(values):
        raise WorkloadParseError(
            f"{field_name} cannot contain duplicate indices",
            details={"field": field_name, "values": list(values)},
        )
    return values


def _mapping_fields(
    data: Any,
    *,
    label: str,
    required: frozenset[str],
    allowed: frozenset[str] | None = None,
) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise WorkloadParseError(
            f"{label} must be an object",
            details={"type": type(data).__name__},
        )
    keys = set(data)
    if any(type(key) is not str for key in keys):
        raise WorkloadParseError(f"{label} field names must be strings")
    missing = required - keys
    unknown = keys - (allowed or required)
    if missing or unknown:
        raise WorkloadParseError(
            f"{label} does not match the canonical wire contract",
            details={
                "missing_fields": sorted(missing),
                "unknown_fields": sorted(unknown),
            },
        )
    return data


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
        kind = _canonical_string(
            self.kind,
            field_name="LogicalOperation.kind",
            lowercase=True,
        )
        name = _canonical_string(
            self.name,
            field_name="LogicalOperation.name",
            lowercase=True,
        )
        if kind not in _OPERATION_KINDS:
            raise WorkloadParseError(
                f"Unsupported FT instruction kind: {kind}",
                details={"kind": kind, "supported": sorted(_OPERATION_KINDS)},
            )
        if kind in {"pauli_rotation", "pauli_measurement"} and self.pauli is None:
            raise WorkloadParseError("A Pauli instruction needs a Pauli string")
        if (
            kind not in {"pauli_rotation", "pauli_measurement"}
            and self.pauli is not None
        ):
            raise WorkloadParseError(
                "Only a Pauli instruction can carry a Pauli string",
                details={"kind": kind, "name": name},
            )
        if self.pauli is not None and type(self.pauli) is not str:
            raise WorkloadParseError("LogicalOperation.pauli must be a string or null")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "name", name)
        object.__setattr__(
            self,
            "qubits",
            _indices(self.qubits, field_name="LogicalOperation.qubits"),
        )
        object.__setattr__(
            self,
            "classical_bits",
            _indices(
                self.classical_bits,
                field_name="LogicalOperation.classical_bits",
            ),
        )
        parameters = _sequence(
            self.parameters,
            field_name="LogicalOperation.parameters",
        )
        try:
            frozen_parameters = tuple(
                deep_freeze_json(normalize_json_value(parameter))
                for parameter in parameters
            )
        except (TypeError, ValueError) as exc:
            raise WorkloadParseError(
                "LogicalOperation.parameters must contain finite JSON values"
            ) from exc
        object.__setattr__(self, "parameters", frozen_parameters)

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
        base_fields = frozenset(
            {"kind", "name", "qubits", "classical_bits", "parameters"}
        )
        record = _mapping_fields(
            data,
            label="LogicalOperation",
            required=base_fields,
            allowed=base_fields | {"pauli", "weight"},
        )
        raw_kind = record["kind"]
        pauli_kind = type(raw_kind) is str and raw_kind in {
            "pauli_rotation",
            "pauli_measurement",
        }
        conditional_fields = {"pauli", "weight"}
        present_conditional = conditional_fields & set(record)
        if pauli_kind and present_conditional != conditional_fields:
            raise WorkloadParseError(
                "A canonical Pauli operation needs pauli and weight fields",
                details={
                    "missing_fields": sorted(
                        conditional_fields - present_conditional
                    )
                },
            )
        if not pauli_kind and present_conditional:
            raise WorkloadParseError(
                "A non-Pauli operation cannot contain pauli or weight fields",
                details={"fields": sorted(present_conditional)},
            )
        operation = cls(
            kind=record["kind"],
            name=record["name"],
            qubits=record["qubits"],
            classical_bits=record["classical_bits"],
            parameters=record["parameters"],
            pauli=record.get("pauli"),
        )
        if pauli_kind and (
            type(record["weight"]) is not int
            or record["weight"] != operation.weight
        ):
            raise WorkloadParseError(
                "LogicalOperation weight does not match its Pauli string",
                details={
                    "expected": operation.weight,
                    "actual": record["weight"],
                },
            )
        return operation


@dataclass(frozen=True)
class LogicalLayer:
    """Operations that share one conservative logical dependency layer.

    Parsers and synthesis adapters derive these layers from source-program
    dependencies.  The explicit layer record is part of the serialized IR so
    compilers do not need to reconstruct (and potentially reinterpret) the
    source DAG.
    """

    index: int
    operations: tuple[LogicalOperation, ...]

    def __post_init__(self) -> None:
        index = _non_negative_integer(
            self.index,
            field_name="LogicalLayer.index",
        )
        operations = _sequence(
            self.operations,
            field_name="LogicalLayer.operations",
        )
        for operation_index, operation in enumerate(operations):
            if not isinstance(operation, LogicalOperation):
                raise WorkloadParseError(
                    "LogicalLayer.operations must contain LogicalOperation records",
                    details={
                        "operation": operation_index,
                        "type": type(operation).__name__,
                    },
                )

        qubit_owner: dict[int, int] = {}
        classical_owner: dict[int, int] = {}
        for operation_index, operation in enumerate(operations):
            shared_qubits = sorted(set(operation.qubits) & set(qubit_owner))
            shared_classical = sorted(
                set(operation.classical_bits) & set(classical_owner)
            )
            if shared_qubits or shared_classical:
                raise WorkloadParseError(
                    "Operations in one logical DAG layer cannot share quantum "
                    "or classical dependencies",
                    details={
                        "layer": index,
                        "operation": operation_index,
                        "shared_qubits": shared_qubits,
                        "shared_classical_bits": shared_classical,
                    },
                )
            qubit_owner.update(
                {qubit: operation_index for qubit in operation.qubits}
            )
            classical_owner.update(
                {bit: operation_index for bit in operation.classical_bits}
            )

        object.__setattr__(self, "index", index)
        object.__setattr__(self, "operations", operations)

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
        fields = frozenset({"index", "active_qubits", "operations"})
        record = _mapping_fields(
            data,
            label="LogicalLayer",
            required=fields,
        )
        raw_operations = _sequence(
            record["operations"],
            field_name="LogicalLayer.operations",
        )
        layer = cls(
            index=record["index"],
            operations=tuple(
                LogicalOperation.from_dict(operation)
                for operation in raw_operations
            ),
        )
        active_qubits = _indices(
            record["active_qubits"],
            field_name="LogicalLayer.active_qubits",
        )
        if active_qubits != layer.active_qubits:
            raise WorkloadParseError(
                "LogicalLayer active_qubits does not match its operations",
                details={
                    "expected": list(layer.active_qubits),
                    "actual": list(active_qubits),
                },
            )
        return layer


@dataclass(frozen=True)
class FTCircuit:
    """Canonical logical circuit consumed by architecture-aware compilers.

    An ``FTCircuit`` sits *after* synthesis/normalization and *before* logical
    mapping and routing.  Synthesis may be a non-trivial external pass (for
    example NWQEC), or an identity normalization pass when a source program is
    already in the operation vocabulary expected by a compiler.

    ``representation`` identifies the IR dialect used to encode operations; it
    is not a closed declaration of the circuit's gate set.  The circuit
    contract intentionally accepts new dialect identifiers so a compiler can
    decide whether it supports the contained logical operations.  The legacy
    ``"clifford_t"`` and ``"pbc"`` identifiers remain stable in serialized
    Report v2 artifacts.
    """

    representation: str
    num_qubits: int
    num_clbits: int
    layers: tuple[LogicalLayer, ...]
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        representation = _canonical_string(
            self.representation,
            field_name="FTCircuit.representation",
        )
        num_qubits = _non_negative_integer(
            self.num_qubits,
            field_name="FTCircuit.num_qubits",
        )
        num_clbits = _non_negative_integer(
            self.num_clbits,
            field_name="FTCircuit.num_clbits",
        )
        layers = _sequence(self.layers, field_name="FTCircuit.layers")
        for layer_index, layer in enumerate(layers):
            if not isinstance(layer, LogicalLayer):
                raise WorkloadParseError(
                    "FTCircuit.layers must contain LogicalLayer records",
                    details={
                        "layer": layer_index,
                        "type": type(layer).__name__,
                    },
                )
        if not isinstance(self.provenance, Mapping):
            raise WorkloadParseError("FTCircuit provenance must be a mapping")
        try:
            provenance = deep_freeze_json(
                normalize_json_value(self.provenance)
            )
        except (TypeError, ValueError) as exc:
            raise WorkloadParseError(
                "FTCircuit provenance must contain finite JSON values"
            ) from exc
        object.__setattr__(self, "representation", representation)
        object.__setattr__(self, "num_qubits", num_qubits)
        object.__setattr__(self, "num_clbits", num_clbits)
        object.__setattr__(self, "layers", layers)
        object.__setattr__(self, "provenance", provenance)
        for expected_index, layer in enumerate(layers):
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
        fields = frozenset(
            {
                "schema_version",
                "representation",
                "num_qubits",
                "num_clbits",
                "layers",
                "semantic_hash",
                "provenance",
            }
        )
        record = _mapping_fields(
            data,
            label="FTCircuit",
            required=fields,
        )
        schema = record["schema_version"]
        if schema != WORKLOAD_SCHEMA_VERSION:
            raise WorkloadParseError(
                f"Unsupported FTCircuit schema: {schema}",
                details={
                    "schema_version": schema,
                    "supported": WORKLOAD_SCHEMA_VERSION,
                },
            )
        raw_layers = _sequence(
            record["layers"],
            field_name="FTCircuit.layers",
        )
        workload = cls(
            representation=record["representation"],
            num_qubits=record["num_qubits"],
            num_clbits=record["num_clbits"],
            layers=tuple(LogicalLayer.from_dict(layer) for layer in raw_layers),
            provenance=record["provenance"],
        )
        expected_hash = record["semantic_hash"]
        if type(expected_hash) is not str or expected_hash != workload.semantic_hash:
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
        if type(text) is not str:
            raise WorkloadParseError("FTCircuit JSON input must be a string")

        def reject_constant(value: str) -> None:
            raise WorkloadParseError(
                f"Non-finite JSON constant is not allowed in FTCircuit: {value}"
            )

        def reject_duplicate_keys(
            pairs: list[tuple[str, Any]],
        ) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise WorkloadParseError(
                        f"Duplicate JSON object key in FTCircuit: {key!r}"
                    )
                result[key] = value
            return result

        try:
            data = json.loads(
                text,
                parse_constant=reject_constant,
                object_pairs_hook=reject_duplicate_keys,
            )
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
