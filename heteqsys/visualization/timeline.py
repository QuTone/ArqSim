"""Realized-execution timeline views."""

from __future__ import annotations

from collections import defaultdict

import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from heteqsys.evaluation import EvaluationResult


def plot_execution_timeline(
    result: EvaluationResult,
    *,
    max_events: int | None = 400,
) -> Figure:
    """Group realized events by occupied module/engine instead of event id."""

    events = result.events[:max_events] if max_events else result.events
    rows: dict[str, list] = defaultdict(list)
    for event in events:
        targets = event.metadata.get("timeline_rows") or event.metadata.get("target_modules")
        if not targets and event.metadata.get("target_links"):
            targets = tuple(f"link:{item}" for item in event.metadata["target_links"])
        if not targets and event.metadata.get("engines"):
            targets = tuple(f"engine:{item}" for item in event.metadata["engines"])
        if not targets:
            targets = (event.process_id or event.plane.value,)
        if isinstance(targets, str):
            targets = (targets,)
        for target in targets:
            rows[str(target)].append(event)
    names = sorted(rows)
    figure, axis = plt.subplots(figsize=(13.0, max(3.2, 0.55 * len(names) + 1.4)))
    colors = {"program": "#4c78a8", "resource": "#72b05f"}
    for y, name in enumerate(names):
        for event in rows[name]:
            duration = max(event.end_s - event.start_s, result.total_latency_s * 1e-6)
            axis.barh(
                y,
                duration,
                left=event.start_s,
                height=0.65,
                color=colors[event.plane.value],
                edgecolor="#243447",
                linewidth=0.55,
            )
    axis.set_yticks(range(len(names)), names)
    axis.set_xlabel("Time (s)")
    axis.set_title("State-coupled execution timeline")
    axis.grid(axis="x", alpha=0.2)
    figure.tight_layout()
    return figure
