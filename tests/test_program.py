from __future__ import annotations

import copy
from pathlib import Path

import pytest

from arqsim.program import (
    CircuitStatistics,
    FTCircuit,
    LogicalLayer,
    LogicalOperation,
    WorkloadParseError,
    load_ft_workload,
    workload_stats,
)
from arqsim.program.layout import partition_active_sets


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


def test_gate_qasm_loader_derives_dependency_layers_without_gate_set_claim(
    tmp_path: Path,
) -> None:
    source = tmp_path / "clifford_rz.qasm"
    source.write_text(
        "OPENQASM 2.0;\n"
        'include "qelib1.inc";\n'
        "qreg q[2];\n"
        "h q[0];\n"
        "rz(pi/8) q[1];\n"
        "cx q[0],q[1];\n",
        encoding="utf-8",
    )

    circuit = load_ft_workload(source, "gate")

    assert circuit.representation == "gate"
    assert [
        [operation.name for operation in layer.operations]
        for layer in circuit.layers
    ] == [["h", "rz"], ["cx"]]


def test_ft_circuit_accepts_extensible_ir_dialect_and_round_trips() -> None:
    circuit = FTCircuit(
        representation="clifford_rz",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (
                    LogicalOperation(
                        "gate",
                        "rz",
                        qubits=(0,),
                        parameters=(0.125,),
                    ),
                ),
            ),
        ),
    )

    restored = FTCircuit.from_json(circuit.to_json())

    assert restored.to_dict() == circuit.to_dict()
    assert restored.semantic_hash == circuit.semantic_hash


def test_workload_statistics_infer_magic_operations_from_ir_not_dialect() -> None:
    circuit = FTCircuit(
        representation="gate",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "t", qubits=(0,)),),
            ),
        ),
    )

    assert workload_stats(circuit)["t_count"] == 1
    assert circuit.statistics.magic_states_per_layer == (1,)


@pytest.mark.parametrize("representation", ["", " gate", "gate "])
def test_ft_circuit_requires_canonical_nonempty_dialect(
    representation: str,
) -> None:
    with pytest.raises(WorkloadParseError, match="trimmed string"):
        FTCircuit(representation, 0, 0, ())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("num_qubits", True),
        ("num_qubits", 1.0),
        ("num_clbits", False),
        ("num_clbits", 0.0),
    ],
)
def test_ft_circuit_does_not_coerce_counts_to_integers(
    field: str,
    value: object,
) -> None:
    arguments = {
        "representation": "gate",
        "num_qubits": 1,
        "num_clbits": 0,
        "layers": (),
    }
    arguments[field] = value

    with pytest.raises(WorkloadParseError, match=field):
        FTCircuit(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize("index", [True, 0.0, -1])
def test_logical_layer_does_not_coerce_index(index: object) -> None:
    with pytest.raises(WorkloadParseError, match="LogicalLayer.index"):
        LogicalLayer(index, ())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "indices"),
    [
        ("qubits", (True,)),
        ("qubits", (0.0,)),
        ("classical_bits", (False,)),
        ("classical_bits", (1.0,)),
    ],
)
def test_logical_operation_does_not_coerce_indices(
    field: str,
    indices: tuple[object, ...],
) -> None:
    arguments = {"kind": "gate", "name": "h"}
    arguments[field] = indices

    with pytest.raises(WorkloadParseError, match=field):
        LogicalOperation(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", ""),
        ("kind", "Gate"),
        ("kind", True),
        ("name", ""),
        ("name", "H"),
        ("name", " h"),
        ("name", False),
    ],
)
def test_logical_operation_requires_canonical_string_identity(
    field: str,
    value: object,
) -> None:
    arguments = {"kind": "gate", "name": "h"}
    arguments[field] = value

    with pytest.raises(WorkloadParseError, match=field):
        LogicalOperation(**arguments)  # type: ignore[arg-type]


def test_program_records_reject_members_of_the_wrong_type() -> None:
    with pytest.raises(WorkloadParseError, match="LogicalOperation records"):
        LogicalLayer(0, (object(),))  # type: ignore[arg-type]
    with pytest.raises(WorkloadParseError, match="LogicalLayer records"):
        FTCircuit("gate", 0, 0, (object(),))  # type: ignore[arg-type]


def test_logical_layer_rejects_shared_quantum_dependency() -> None:
    with pytest.raises(WorkloadParseError, match="DAG layer") as exc_info:
        LogicalLayer(
            0,
            (
                LogicalOperation("gate", "h", qubits=(0,)),
                LogicalOperation("gate", "t", qubits=(0,)),
            ),
        )

    assert exc_info.value.details["shared_qubits"] == [0]


def test_logical_layer_rejects_shared_classical_dependency() -> None:
    with pytest.raises(WorkloadParseError, match="DAG layer") as exc_info:
        LogicalLayer(
            0,
            (
                LogicalOperation(
                    "measurement",
                    "measure",
                    qubits=(0,),
                    classical_bits=(0,),
                ),
                LogicalOperation(
                    "measurement",
                    "measure",
                    qubits=(1,),
                    classical_bits=(0,),
                ),
            ),
        )

    assert exc_info.value.details["shared_classical_bits"] == [0]


def _canonical_document() -> dict:
    return FTCircuit(
        "gate",
        2,
        1,
        (
            LogicalLayer(
                0,
                (
                    LogicalOperation("gate", "h", qubits=(0,)),
                    LogicalOperation(
                        "measurement",
                        "measure",
                        qubits=(1,),
                        classical_bits=(0,),
                    ),
                ),
            ),
        ),
    ).to_dict()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda data: data.pop("provenance"), "missing_fields"),
        (lambda data: data.update({"extra": 1}), "unknown_fields"),
        (
            lambda data: data["layers"][0].pop("active_qubits"),
            "missing_fields",
        ),
        (
            lambda data: data["layers"][0].update({"extra": 1}),
            "unknown_fields",
        ),
        (
            lambda data: data["layers"][0]["operations"][0].pop("parameters"),
            "missing_fields",
        ),
        (
            lambda data: data["layers"][0]["operations"][0].update(
                {"extra": 1}
            ),
            "unknown_fields",
        ),
    ],
)
def test_ft_circuit_from_dict_requires_exact_canonical_wire_fields(
    mutation,
    expected: str,
) -> None:
    document = copy.deepcopy(_canonical_document())
    mutation(document)

    with pytest.raises(WorkloadParseError) as exc_info:
        FTCircuit.from_dict(document)

    assert expected in exc_info.value.details


def test_ft_circuit_from_dict_validates_derived_wire_fields() -> None:
    active_qubits = copy.deepcopy(_canonical_document())
    active_qubits["layers"][0]["active_qubits"] = [0]
    with pytest.raises(WorkloadParseError, match="active_qubits"):
        FTCircuit.from_dict(active_qubits)

    pauli = FTCircuit(
        "pbc",
        1,
        0,
        (
            LogicalLayer(
                0,
                (
                    LogicalOperation(
                        "pauli_rotation",
                        "t_pauli",
                        qubits=(0,),
                        pauli="+X",
                    ),
                ),
            ),
        ),
    ).to_dict()
    pauli["layers"][0]["operations"][0]["weight"] = 2
    with pytest.raises(WorkloadParseError, match="weight"):
        FTCircuit.from_dict(pauli)


@pytest.mark.parametrize("value", [None, [], "workload"])
def test_ft_circuit_from_dict_rejects_non_objects(value: object) -> None:
    with pytest.raises(WorkloadParseError, match="must be an object"):
        FTCircuit.from_dict(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [None, 1, b"{}"])
def test_ft_circuit_from_json_rejects_non_string_input(value: object) -> None:
    with pytest.raises(WorkloadParseError, match="must be a string"):
        FTCircuit.from_json(value)  # type: ignore[arg-type]


@pytest.mark.parametrize("text", ["null", "[]", '"workload"'])
def test_ft_circuit_from_json_requires_an_object(text: str) -> None:
    with pytest.raises(WorkloadParseError, match="must be an object"):
        FTCircuit.from_json(text)


def test_ft_circuit_from_json_rejects_duplicates_and_nonfinite_values() -> None:
    text = FTCircuit.from_dict(_canonical_document()).to_json()
    duplicate = text.replace(
        '"num_qubits": 2,',
        '"num_qubits": 2, "num_qubits": 2,',
        1,
    )
    with pytest.raises(WorkloadParseError, match="Duplicate"):
        FTCircuit.from_json(duplicate)

    nonfinite = text.replace('"parameters": []', '"parameters": [NaN]', 1)
    with pytest.raises(WorkloadParseError, match="Non-finite"):
        FTCircuit.from_json(nonfinite)


def test_ft_circuit_from_dict_wraps_nonfinite_nested_values() -> None:
    parameter = copy.deepcopy(_canonical_document())
    parameter["layers"][0]["operations"][0]["parameters"] = [float("nan")]
    with pytest.raises(WorkloadParseError, match="finite JSON"):
        FTCircuit.from_dict(parameter)

    provenance = copy.deepcopy(_canonical_document())
    provenance["provenance"] = {"score": float("inf")}
    with pytest.raises(WorkloadParseError, match="finite JSON"):
        FTCircuit.from_dict(provenance)


def test_ft_circuit_from_json_wraps_malformed_json() -> None:
    with pytest.raises(WorkloadParseError, match="JSON is invalid") as exc_info:
        FTCircuit.from_json('{"schema_version":')

    assert exc_info.value.details == {"line": 1, "column": 19}


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
