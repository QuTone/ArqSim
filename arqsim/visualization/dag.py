"""Program- and resource-dependence graph views."""

from __future__ import annotations

import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.figure import Figure

from arqsim.evaluation import ProgramDAG, ResourceDAG


def plot_program_dag(program: ProgramDAG, *, max_nodes: int | None = 80) -> Figure:
    """Draw explicit program-dependence edges, optionally truncating the view."""

    instructions = program.instructions[:max_nodes] if max_nodes else program.instructions
    visible = {item.id for item in instructions}
    graph = nx.DiGraph()
    labels: dict[int, str] = {}
    for item in instructions:
        graph.add_node(item.id)
        layer = "" if item.layer_index is None else f"\nL{item.layer_index}"
        labels[item.id] = f"{item.id}: {item.opcode.value}{layer}"
        for predecessor in item.predecessor_ids:
            if predecessor in visible:
                graph.add_edge(predecessor, item.id)
    figure, axis = plt.subplots(figsize=(max(8.0, len(instructions) * 0.22), 6.0))
    positions = nx.spring_layout(graph, seed=7)
    nx.draw_networkx(
        graph,
        pos=positions,
        labels=labels,
        node_color="#dce9f7",
        edgecolors="#243447",
        linewidths=0.7,
        node_size=1050,
        font_size=6,
        arrowsize=10,
        ax=axis,
    )
    axis.set_title("Program DAG")
    axis.axis("off")
    figure.tight_layout()
    return figure


def plot_resource_dag(resources: ResourceDAG) -> Figure:
    """Draw streaming resource processes and their buffer-mediated edges."""

    graph = nx.DiGraph()
    labels: dict[str, str] = {}
    producers: dict[str, list[str]] = {}
    consumers: dict[str, list[str]] = {}
    for process in resources.processes:
        graph.add_node(process.id)
        labels[process.id] = f"{process.id}\n{process.opcode.value}"
        for buffer in process.produces:
            producers.setdefault(buffer, []).append(process.id)
        for buffer in process.consumes:
            consumers.setdefault(buffer, []).append(process.id)
    for buffer, sources in producers.items():
        for source in sources:
            for destination in consumers.get(buffer, ()):
                graph.add_edge(source, destination, buffer=buffer)
    figure, axis = plt.subplots(figsize=(max(7.0, len(graph) * 1.8), 4.5))
    positions = nx.spring_layout(graph, seed=11)
    nx.draw_networkx(
        graph,
        positions,
        labels=labels,
        node_color="#e7f3df",
        edgecolors="#243447",
        linewidths=0.7,
        node_size=1700,
        font_size=7,
        arrowsize=12,
        ax=axis,
    )
    nx.draw_networkx_edge_labels(
        graph,
        positions,
        edge_labels=nx.get_edge_attributes(graph, "buffer"),
        font_size=6,
        ax=axis,
    )
    axis.set_title("Streaming Resource DAG")
    axis.axis("off")
    figure.tight_layout()
    return figure
