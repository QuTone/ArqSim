"""Evaluation-breakdown views."""

from __future__ import annotations

from collections import defaultdict

import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from arqsim.evaluation.footprint import PhysicalFootprintEstimate


def plot_space_breakdown(estimate: PhysicalFootprintEstimate) -> Figure:
    by_module: dict[str, float] = defaultdict(float)
    for component in estimate.components:
        by_module[component.module_id] += component.physical_qubits
    names = sorted(by_module)
    figure, axis = plt.subplots(figsize=(max(6.5, len(names) * 1.2), 4.2))
    axis.bar(
        names,
        [by_module[name] for name in names],
        color="#7aa6c2",
        edgecolor="#243447",
        linewidth=0.65,
    )
    axis.set_ylabel("Physical qubits")
    axis.set_title("Physical-space breakdown by module")
    axis.tick_params(axis="x", rotation=25)
    figure.tight_layout()
    return figure
