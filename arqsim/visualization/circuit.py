"""Logical-circuit views."""

from __future__ import annotations

from collections.abc import Iterable

import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from arqsim.program import FTCircuit


def plot_circuit_layers(
    circuit: FTCircuit,
    *,
    layers: Iterable[int] | None = None,
) -> Figure:
    """Draw a compact circuit diagram for selected conservative FT layers."""

    selected = set(range(len(circuit.layers))) if layers is None else set(layers)
    logical_layers = [layer for layer in circuit.layers if layer.index in selected]
    width = max(8.0, 0.9 * len(logical_layers) + 2.0)
    height = max(3.0, 0.3 * circuit.num_qubits + 1.5)
    figure, axis = plt.subplots(figsize=(width, height))
    for qubit in range(circuit.num_qubits):
        axis.hlines(qubit, -0.5, len(logical_layers) - 0.5, color="#b8bec9", linewidth=0.7)
        axis.text(-0.65, qubit, f"q{qubit}", ha="right", va="center", fontsize=7)
    for column, layer in enumerate(logical_layers):
        for operation in layer.operations:
            if len(operation.qubits) == 2:
                low, high = sorted(operation.qubits)
                axis.vlines(column, low, high, color="#243447", linewidth=1.0)
            for qubit in operation.qubits:
                axis.text(
                    column,
                    qubit,
                    operation.name.upper(),
                    ha="center",
                    va="center",
                    fontsize=6,
                    bbox={"boxstyle": "round,pad=0.18", "facecolor": "white", "edgecolor": "#243447", "linewidth": 0.65},
                )
    axis.set_xticks(range(len(logical_layers)), [f"L{layer.index}" for layer in logical_layers])
    axis.set_yticks([])
    axis.set_xlim(-1.2, max(0.5, len(logical_layers) - 0.5))
    axis.set_ylim(circuit.num_qubits - 0.4, -0.6)
    axis.set_title("Fault-tolerant circuit layers")
    axis.set_frame_on(False)
    figure.tight_layout()
    return figure
