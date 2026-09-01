"""Parsers that normalize synthesized circuits into :class:`FTCircuit`."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Optional

from .errors import WorkloadParseError
from .circuit import LogicalOperation, FTCircuit, make_layers, normalize_json_value


_QREG_RE = re.compile(
    r"^\s*qreg\s+(?P<register>[A-Za-z_]\w*)\[(?P<size>\d+)\]\s*;\s*$",
    re.IGNORECASE,
)
_PAULI_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_]\w*_pauli)\s+"
    r"(?P<pauli>[+-]?[IXYZixyz*]+)\s*;\s*$"
)
_GATE_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_]\w*)"
    r"(?:\((?P<parameters>[^)]*)\))?\s+"
    r"(?P<arguments>[A-Za-z_]\w*\[\d+\]"
    r"(?:\s*,\s*[A-Za-z_]\w*\[\d+\])*)\s*;\s*$"
)
_QUBIT_RE = re.compile(r"^(?P<register>[A-Za-z_]\w*)\[(?P<index>\d+)\]$")


def normalize_pauli_string(
    pauli: str,
    *,
    expected_length: Optional[int] = None,
) -> str:
    """Normalize both legacy ``*`` and NWQEC ``I`` identity symbols to ``I``."""

    value = pauli.strip().upper()
    if not value:
        raise WorkloadParseError("A Pauli string cannot be empty")
    if value[0] not in {"+", "-"}:
        value = "+" + value
    sign, body = value[0], value[1:].replace("*", "I")
    invalid = sorted(set(body) - {"I", "X", "Y", "Z"})
    if invalid:
        raise WorkloadParseError(
            "A Pauli string contains unsupported symbols",
            details={"pauli": pauli, "invalid_symbols": invalid},
        )
    if expected_length is not None and len(body) != expected_length:
        raise WorkloadParseError(
            "A Pauli string does not match the workload qubit count",
            details={
                "pauli": pauli,
                "pauli_length": len(body),
                "num_qubits": expected_length,
            },
        )
    return sign + body


def _parameter_value(parameter: Any) -> Any:
    try:
        return float(parameter)
    except (TypeError, ValueError):
        return str(parameter)


def parse_clifford_t_qasm(
    path: Path,
    *,
    provenance: Optional[Mapping[str, Any]] = None,
) -> FTCircuit:
    """Load a gate-based OpenQASM 2 circuit and preserve its DAG layers."""

    try:
        from qiskit import QuantumCircuit
        from qiskit.converters import circuit_to_dag

        circuit = QuantumCircuit.from_qasm_file(str(path))
        dag = circuit_to_dag(circuit)
    except Exception as exc:
        raise WorkloadParseError(
            "The gate-based QASM input could not be parsed",
            details={"path": str(path), "reason": str(exc)},
        ) from exc

    qubit_indices = {qubit: index for index, qubit in enumerate(circuit.qubits)}
    classical_indices = {bit: index for index, bit in enumerate(circuit.clbits)}
    layers: list[list[LogicalOperation]] = []
    for layer in dag.layers():
        operations: list[LogicalOperation] = []
        for node in layer["graph"].op_nodes():
            # Barriers constrain transpiler reordering but are not fault-
            # tolerant execution primitives.  Retaining a whole-register
            # barrier would misrepresent it as an N-qubit interaction.
            if node.op.name == "barrier":
                continue
            operations.append(
                LogicalOperation(
                    kind=("measurement" if node.op.name == "measure" else "gate"),
                    name=node.op.name.lower(),
                    qubits=tuple(qubit_indices[qubit] for qubit in node.qargs),
                    classical_bits=tuple(
                        classical_indices[bit] for bit in node.cargs
                    ),
                    parameters=tuple(_parameter_value(value) for value in node.op.params),
                )
            )
        if operations:
            layers.append(operations)

    return FTCircuit(
        representation="clifford_t",
        num_qubits=circuit.num_qubits,
        num_clbits=circuit.num_clbits,
        layers=make_layers(layers),
        provenance=provenance or {},
    )


def parse_pbc_qasm(
    path: Path,
    *,
    provenance: Optional[Mapping[str, Any]] = None,
) -> FTCircuit:
    """Load NWQEC/legacy PBC QASM into conservative sequential layers."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise WorkloadParseError(
            "The PBC QASM input could not be read",
            details={"path": str(path), "reason": str(exc)},
        ) from exc

    register: Optional[str] = None
    num_qubits: Optional[int] = None
    layers: list[list[LogicalOperation]] = []
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.split("//", maxsplit=1)[0].strip()
        if not line:
            continue

        qreg_match = _QREG_RE.match(line)
        if qreg_match:
            if register is not None:
                raise WorkloadParseError(
                    "PBC QASM currently supports one quantum register",
                    details={"path": str(path), "line": line_number},
                )
            register = qreg_match.group("register")
            num_qubits = int(qreg_match.group("size"))
            continue

        lowered = line.lower()
        if lowered.startswith(("openqasm ", "include ", "creg ")):
            continue

        if register is None or num_qubits is None:
            raise WorkloadParseError(
                "A qreg declaration must precede PBC operations",
                details={"path": str(path), "line": line_number},
            )

        pauli_match = _PAULI_RE.match(line)
        if pauli_match:
            pauli = normalize_pauli_string(
                pauli_match.group("pauli"),
                expected_length=num_qubits,
            )
            active_qubits = tuple(
                index for index, symbol in enumerate(pauli[1:]) if symbol != "I"
            )
            layers.append(
                [
                    LogicalOperation(
                        kind=(
                            "pauli_measurement"
                            if pauli_match.group("name").lower() == "m_pauli"
                            else "pauli_rotation"
                        ),
                        name=pauli_match.group("name").lower(),
                        qubits=active_qubits,
                        pauli=pauli,
                    )
                ]
            )
            continue

        gate_match = _GATE_RE.match(line)
        if gate_match:
            qubits: list[int] = []
            for raw_argument in gate_match.group("arguments").split(","):
                argument = _QUBIT_RE.match(raw_argument.strip())
                if argument is None or argument.group("register") != register:
                    raise WorkloadParseError(
                        "A PBC gate references an unknown quantum register",
                        details={
                            "path": str(path),
                            "line": line_number,
                            "operation": line,
                        },
                    )
                qubit = int(argument.group("index"))
                if qubit >= num_qubits:
                    raise WorkloadParseError(
                        "A PBC gate references an out-of-range qubit",
                        details={
                            "path": str(path),
                            "line": line_number,
                            "qubit": qubit,
                            "num_qubits": num_qubits,
                        },
                    )
                qubits.append(qubit)
            raw_parameters = gate_match.group("parameters")
            parameters = (
                tuple(part.strip() for part in raw_parameters.split(","))
                if raw_parameters
                else ()
            )
            layers.append(
                [
                    LogicalOperation(
                        kind="gate",
                        name=gate_match.group("name").lower(),
                        qubits=tuple(qubits),
                        parameters=parameters,
                    )
                ]
            )
            continue

        raise WorkloadParseError(
            "The PBC QASM input contains an unsupported statement",
            details={"path": str(path), "line": line_number, "statement": line},
        )

    if register is None or num_qubits is None:
        raise WorkloadParseError(
            "The PBC QASM input does not declare a quantum register",
            details={"path": str(path)},
        )

    return FTCircuit(
        representation="pbc",
        num_qubits=num_qubits,
        num_clbits=0,
        layers=make_layers(layers),
        provenance=provenance or {},
    )


def load_ft_workload(
    path: Path | str,
    representation: str,
    *,
    provenance: Optional[Mapping[str, Any]] = None,
) -> FTCircuit:
    """Parse either supported circuit representation through one public API."""

    source = Path(path)
    if not source.is_file():
        raise WorkloadParseError(
            "The workload file does not exist",
            details={"path": str(source)},
        )
    if representation == "clifford_t":
        return parse_clifford_t_qasm(source, provenance=provenance)
    if representation == "pbc":
        return parse_pbc_qasm(source, provenance=provenance)
    raise WorkloadParseError(
        f"Unsupported FT workload representation: {representation}",
        details={"representation": representation},
    )


def workload_stats(workload: FTCircuit) -> dict[str, Any]:
    """Compute representation-independent and PBC-specific artifact metrics."""

    operations = [operation for layer in workload.layers for operation in layer.operations]
    op_counts = Counter(operation.name for operation in operations)
    pauli_operations = [operation for operation in operations if operation.pauli is not None]
    pauli_rotations = [
        operation for operation in pauli_operations if operation.kind == "pauli_rotation"
    ]
    pauli_measurements = [
        operation for operation in pauli_operations if operation.kind == "pauli_measurement"
    ]
    pbc_weights = [operation.weight or 0 for operation in pauli_operations]

    if workload.representation == "clifford_t":
        t_count = op_counts["t"] + op_counts["tdg"]
    else:
        t_count = op_counts["t_pauli"]

    stats: dict[str, Any] = {
        "num_qubits": workload.num_qubits,
        "num_clbits": workload.num_clbits,
        "depth": len(workload.layers),
        "operation_count": workload.operation_count,
        "operation_counts": dict(sorted(op_counts.items())),
        "t_count": t_count,
    }
    if workload.representation == "pbc":
        stats.update(
            {
                "pbc_operation_count": len(pauli_operations),
                "pbc_rotation_count": len(pauli_rotations),
                "pbc_measurement_count": len(pauli_measurements),
                "pbc_depth": len(workload.layers),
                "pbc_mean_weight": (
                    sum(pbc_weights) / len(pbc_weights) if pbc_weights else 0.0
                ),
                "pbc_max_weight": max(pbc_weights, default=0),
                "pbc_weight_histogram": dict(
                    sorted(Counter(pbc_weights).items(), key=lambda pair: pair[0])
                ),
            }
        )
    return normalize_json_value(stats)
