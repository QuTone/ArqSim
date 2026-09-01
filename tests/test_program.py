from __future__ import annotations

from pathlib import Path

import pytest

from heteqsys.program import (
    CircuitStatistics,
    FTCircuit,
    LogicalLayer,
    LogicalOperation,
    WorkloadParseError,
    load_ft_workload,
)
from heteqsys.program.layout import partition_active_sets


FIXTURES = Path(__file__).parent / "fixtures"


def test_gate_program_preserves_measurement_targets() -> None:
    circuit = load_ft_workload(FIXTURES / "bell_state_teleportation.qasm", "clifford_t")
    measurements = [
        operation
        for layer in circuit.layers
        for operation in layer.operations
        if operation.kind == "measurement"
    ]
    assert circuit.num_qubits == 3
    assert sorted((item.qubits, item.classical_bits) for item in measurements) == [
        ((0,), (0,)), ((1,), (1,)), ((2,), (2,))
    ]


def test_pbc_identity_symbols_have_one_canonical_form(tmp_path: Path) -> None:
    def write(name: str, pauli: str) -> Path:
        path = tmp_path / name
        path.write_text(
            "OPENQASM 2.0;\ninclude \"qelib1.inc\";\nqreg q[3];\n"
            f"t_pauli {pauli};\nm_pauli +ZII;\n",
            encoding="utf-8",
        )
        return path

    legacy = load_ft_workload(write("legacy.qasm", "+X*Y"), "pbc")
    current = load_ft_workload(write("current.qasm", "+XIY"), "pbc")
    assert legacy.semantic_dict() == current.semantic_dict()
    assert legacy.layers[0].operations[0].pauli == "+XIY"


def test_invalid_input_raises_structured_error(tmp_path: Path) -> None:
    source = tmp_path / "invalid.qasm"
    source.write_text("not OpenQASM", encoding="utf-8")
    with pytest.raises(WorkloadParseError) as error:
        load_ft_workload(source, "clifford_t")
    assert error.value.code == "invalid_workload"


def test_workload_parameters_and_provenance_are_deeply_immutable() -> None:
    parameter = {"angle": [0.25]}
    provenance = {"source": {"tags": ["fixture"]}}
    operation = LogicalOperation(
        "gate",
        "rz",
        qubits=(0,),
        parameters=(parameter,),
    )
    circuit = FTCircuit(
        "clifford_t",
        1,
        0,
        (LogicalLayer(0, (operation,)),),
        provenance=provenance,
    )
    original_hash = circuit.semantic_hash
    original_document = circuit.to_dict()

    parameter["angle"].append(0.5)
    provenance["source"]["tags"].append("mutated")
    with pytest.raises(TypeError):
        operation.parameters[0]["angle"] += (0.5,)
    with pytest.raises(TypeError):
        circuit.provenance["source"]["tags"] += ("mutated",)

    assert circuit.semantic_hash == original_hash
    assert circuit.to_dict() == original_document
    assert FTCircuit.from_json(circuit.to_json()).to_dict() == original_document


def test_circuit_statistics_are_cached_program_facts_not_architecture_input() -> None:
    circuit = FTCircuit(
        "clifford_t",
        3,
        0,
        (
            LogicalLayer(
                0,
                (
                    LogicalOperation("gate", "cx", qubits=(0, 1)),
                    LogicalOperation("gate", "t", qubits=(2,)),
                ),
            ),
            LogicalLayer(1, (LogicalOperation("gate", "h", qubits=(1,)),)),
        ),
    )

    statistics = circuit.statistics
    assert isinstance(statistics, CircuitStatistics)
    assert circuit.statistics is statistics
    assert statistics.logical_qubits == 3
    assert statistics.logical_layers == 2
    assert statistics.active_qubits_per_layer == (3, 1)
    assert statistics.magic_states_per_layer == (1, 0)
    assert statistics.operation_widths == (2, 1, 1)
    assert statistics.operation_qubits_by_layer == (((0, 1), (2,)), ((1,),))

    assert partition_active_sets(statistics, compute_capacity=2) == (
        frozenset({0, 1}),
        frozenset({2}),
        frozenset({1}),
    )
