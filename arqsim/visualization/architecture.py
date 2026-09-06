"""Pure views over canonical architecture and compiler artifacts.

The Architecture Specification owns the resolved hierarchy, capacities, QEC
bindings, and logical slots.  Compiler routing is accepted separately as a
``LogicalLayout``.  This module deliberately does not reconstruct any legacy
architecture, QEC, or build-wrapper object in order to draw a figure.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.figure import Figure
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

from arqsim.architecture.profile import ArchitectureProfile
from arqsim.architecture.specification import (
    ArchitectureSpecification,
    Interconnect,
    Module,
    Node,
    QECResourceProtocolRef,
    Submodule,
)
from arqsim.compiler.models import LogicalLayout
from arqsim.evaluation.footprint import (
    FootprintComponent,
    PhysicalFootprintEstimate,
)


Owner = Node | Interconnect

_MODULE_COLORS = {
    "compute": "#DCEAF7",
    "memory": "#E5E0F4",
    "resource_factory": "#F8E2C2",
    "bell_engine": "#DDF0E2",
    "bell_storage": "#CDE7DA",
}
_MODALITY_COLORS = {
    "neutral_atom": "#3478A6",
    "superconducting": "#A55A2A",
    "interconnect": "#A13D63",
}
_SUBMODULE_COLORS = {
    ("region", "logical_qubit"): "#8EC1E6",
    ("buffer", "logical_qubit"): "#6C8EBF",
    ("buffer", "magic_state"): "#F3B562",
    ("buffer", "bell_pair"): "#4E9D78",
    ("engine", "magic_state"): "#D97532",
    ("engine", "bell_pair"): "#76B995",
}


def _human(value: str) -> str:
    return value.replace("_", " ")


def _owners(specification: ArchitectureSpecification) -> tuple[Owner, ...]:
    return (*specification.nodes, *specification.interconnects)


def _owner_kind(owner: Owner) -> str:
    return "node" if isinstance(owner, Node) else "interconnect"


def _owner_modality(
    specification: ArchitectureSpecification,
    owner: Owner,
) -> str:
    if isinstance(owner, Node):
        return owner.modality
    modalities = {
        node.modality
        for node in specification.nodes
        if node.id in {endpoint.split("/", 1)[0] for endpoint in owner.endpoints}
    }
    return next(iter(modalities)) if len(modalities) == 1 else "interconnect"


def _absolute_coordinate(
    submodule: Submodule,
    coordinate: tuple[int, int] | None,
) -> tuple[float, float] | None:
    if coordinate is None:
        return None
    origin = submodule.logical_origin or (0, 0)
    return (float(origin[0] + coordinate[0]), float(origin[1] + coordinate[1]))


def _components_for(
    footprint: PhysicalFootprintEstimate,
    owner_id: str,
    module_id: str,
    submodule_id: str,
) -> tuple[FootprintComponent, ...]:
    """Return all endpoint-expanded costs for one canonical Submodule."""

    return tuple(
        component
        for component in footprint.components
        if component.owner_id == owner_id
        and component.module_id == module_id
        and component.submodule_id == submodule_id
    )


def _physical_qubits(components: Iterable[FootprintComponent]) -> float:
    return sum(component.physical_qubits for component in components)


def _qec_text(submodule: Submodule) -> str:
    if submodule.qec is None:
        return "—"
    parameters = ", ".join(
        f"{key}={value}" for key, value in submodule.qec.parameters.items()
    )
    return (
        f"{submodule.qec.code}({parameters})"
        if parameters
        else submodule.qec.code
    )


def _protocol_text(reference: QECResourceProtocolRef | None) -> str:
    if reference is None:
        return ""
    return f"{reference.id} [{reference.profile_hash[:8]}]"


def _capacity_label(submodule: Submodule) -> str:
    unit = "copies" if submodule.type == "engine" else "logical slots"
    return f"{submodule.capacity} {unit}"


def plot_architecture_profile(profile: ArchitectureProfile) -> Figure:
    """Draw the structural facts in one unsized Architecture Profile."""

    if not profile.nodes:
        raise ValueError(f"Architecture profile {profile.id} has no nodes")

    figure, axis = plt.subplots(figsize=(16.0, 8.2))
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.axis("off")
    axis.text(
        0.5,
        0.965,
        f"Profile {profile.id}: {profile.name}",
        ha="center",
        va="top",
        fontsize=21,
        fontweight="bold",
    )
    axis.text(
        0.5,
        0.915,
        profile.description,
        ha="center",
        va="top",
        fontsize=10.5,
        color="#4B5563",
    )
    axis.text(
        0.5,
        0.875,
        "ARCHITECTURE PROFILE — capacities, QEC, and logical placement are unresolved",
        ha="center",
        va="top",
        fontsize=9.5,
        fontweight="bold",
        color="#8A3D3D",
    )

    node_gap = 0.035
    node_width = (0.94 - node_gap * (len(profile.nodes) - 1)) / len(profile.nodes)
    node_y, node_height = 0.22, 0.49
    node_boxes: dict[str, tuple[float, float, float, float]] = {}
    module_boxes: dict[tuple[str, str], tuple[float, float, float, float]] = {}
    modules: dict[tuple[str, str], object] = {}
    for node_index, node in enumerate(profile.nodes):
        node_x = 0.03 + node_index * (node_width + node_gap)
        node_boxes[node.id] = (node_x, node_y, node_width, node_height)
        inner_gap = 0.012
        inner_width = node_width - 0.028
        module_width = (
            inner_width - inner_gap * max(0, len(node.modules) - 1)
        ) / max(1, len(node.modules))
        for module_index, module in enumerate(node.modules):
            key = (node.id, module.id)
            modules[key] = module
            module_boxes[key] = (
                node_x + 0.014 + module_index * (module_width + inner_gap),
                node_y + 0.075,
                module_width,
                node_height - 0.13,
            )

    for node in profile.nodes:
        for index, connection in enumerate(node.connections):
            source_key = (node.id, connection.endpoints[0].split("/", 1)[0])
            target_key = (node.id, connection.endpoints[1].split("/", 1)[0])
            source = module_boxes.get(source_key)
            target = module_boxes.get(target_key)
            if source is None or target is None:
                continue
            start = (source[0] + source[2] / 2, source[1] + source[3] / 2)
            end = (target[0] + target[2] / 2, target[1] + target[3] / 2)
            axis.add_patch(
                FancyArrowPatch(
                    start,
                    end,
                    arrowstyle=(
                        "->" if connection.direction == "directed" else "<->"
                    ),
                    mutation_scale=11,
                    linewidth=1.5,
                    color="#56616D",
                    connectionstyle=f"arc3,rad={0.13 if index % 2 == 0 else -0.13}",
                    zorder=1,
                )
            )

    for interconnect in profile.interconnects:
        endpoint_nodes = sorted(
            {endpoint.split("/", 1)[0] for endpoint in interconnect.endpoints}
        )
        if len(endpoint_nodes) != 2:
            continue
        source, target = (node_boxes[node_id] for node_id in endpoint_nodes)
        start = (source[0] + source[2], node_y + node_height + 0.015)
        end = (target[0], node_y + node_height + 0.015)
        axis.add_patch(
            FancyArrowPatch(
                start,
                end,
                arrowstyle="<->",
                mutation_scale=13,
                linewidth=2.2,
                linestyle="--",
                color="#A13D63",
                zorder=5,
            )
        )
        axis.text(
            (start[0] + end[0]) / 2,
            start[1] + 0.018,
            "INTERCONNECT: "
            f"{interconnect.id} | "
            + " • ".join(_human(module.type) for module in interconnect.modules),
            ha="center",
            va="bottom",
            fontsize=8.2,
            fontweight="bold",
            color="#8B3154",
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.0},
            zorder=6,
        )

    for node in profile.nodes:
        x, y, width, height = node_boxes[node.id]
        color = _MODALITY_COLORS.get(node.modality, "#4F677D")
        axis.add_patch(
            FancyBboxPatch(
                (x, y),
                width,
                height,
                boxstyle="round,pad=0.006,rounding_size=0.012",
                facecolor="#FAFBFC",
                edgecolor=color,
                linewidth=2.2,
                zorder=2,
            )
        )
        axis.text(
            x + 0.012,
            y + height - 0.025,
            f"NODE: {node.id} | {_human(node.modality).upper()}",
            ha="left",
            va="center",
            fontsize=10.2,
            fontweight="bold",
            color=color,
            zorder=4,
        )

    for key, (x, y, width, height) in module_boxes.items():
        module = modules[key]
        axis.add_patch(
            FancyBboxPatch(
                (x, y),
                width,
                height,
                boxstyle="round,pad=0.004,rounding_size=0.008",
                facecolor=_MODULE_COLORS.get(module.type, "#ECEFF2"),
                edgecolor="#344454",
                linewidth=1.2,
                zorder=3,
            )
        )
        axis.text(
            x + width / 2,
            y + height - 0.035,
            f"{_human(module.type).upper()}\n{module.id}",
            ha="center",
            va="top",
            fontsize=9.2,
            fontweight="bold",
            color="#243447",
            zorder=4,
        )
        item_height = min(0.050, (height - 0.13) / max(1, len(module.submodules)))
        for index, submodule in enumerate(reversed(module.submodules)):
            item_y = y + 0.025 + index * (item_height + 0.009)
            axis.add_patch(
                FancyBboxPatch(
                    (x + 0.012, item_y),
                    width - 0.024,
                    item_height,
                    boxstyle="round,pad=0.003,rounding_size=0.005",
                    facecolor="#F7FAFC",
                    edgecolor="#596674",
                    linewidth=0.8,
                    zorder=4,
                )
            )
            axis.text(
                x + 0.020,
                item_y + item_height / 2,
                f"{submodule.id} | {submodule.type} / {submodule.payload}",
                ha="left",
                va="center",
                fontsize=6.8,
                color="#2F3B46",
                zorder=5,
            )

    axis.text(
        0.5,
        0.115,
        "Only authoring-time structure is shown; no resolved capacity or layout is inferred.",
        ha="center",
        va="center",
        fontsize=9.2,
        color="#6B7280",
        style="italic",
    )
    figure.tight_layout(pad=0.5)
    return figure


def plot_architecture_hierarchy(
    specification: ArchitectureSpecification,
) -> Figure:
    """Draw the canonical owner → Module → Submodule hierarchy."""

    graph = nx.DiGraph()
    labels: dict[str, str] = {}
    positions: dict[str, tuple[float, float]] = {}
    owner_ids: list[str] = []
    module_ids: list[str] = []
    submodule_ids: list[str] = []
    cursor = 0.0
    for owner in _owners(specification):
        owner_key = f"owner:{owner.id}"
        owner_ids.append(owner_key)
        graph.add_node(owner_key)
        labels[owner_key] = f"{_owner_kind(owner).upper()}\n{owner.id}"
        module_positions: list[float] = []
        for module in owner.modules:
            module_key = f"module:{owner.id}/{module.id}"
            module_ids.append(module_key)
            graph.add_edge(owner_key, module_key)
            labels[module_key] = f"MODULE\n{module.id}\ntype: {module.type}"
            child_positions: list[float] = []
            for submodule in module.submodules:
                submodule_key = (
                    f"submodule:{owner.id}/{module.id}/{submodule.id}"
                )
                submodule_ids.append(submodule_key)
                graph.add_edge(module_key, submodule_key)
                labels[submodule_key] = (
                    f"{submodule.id}\n{submodule.type} / {submodule.payload}"
                )
                positions[submodule_key] = (4.8, -cursor)
                child_positions.append(-cursor)
                cursor += 1.0
            module_y = sum(child_positions) / len(child_positions)
            positions[module_key] = (2.45, module_y)
            module_positions.append(module_y)
            cursor += 0.45
        positions[owner_key] = (0.0, sum(module_positions) / len(module_positions))
        cursor += 0.9

    figure, axis = plt.subplots(figsize=(14.0, max(5.0, cursor * 0.62)))
    nx.draw_networkx_edges(
        graph,
        positions,
        edge_color="#59636F",
        width=0.9,
        arrows=True,
        arrowsize=11,
        ax=axis,
    )
    for identifiers, color, size in (
        (owner_ids, "#D8E8F5", 2700),
        (module_ids, "#E7F1DC", 2500),
        (submodule_ids, "#F2E7F5", 2200),
    ):
        nx.draw_networkx_nodes(
            graph,
            positions,
            nodelist=identifiers,
            node_color=color,
            edgecolors="#243447",
            linewidths=0.8,
            node_size=size,
            node_shape="s",
            ax=axis,
        )
    nx.draw_networkx_labels(graph, positions, labels=labels, font_size=7, ax=axis)
    axis.set_title(
        f"Architecture hierarchy: {specification.architecture_hash[:12]}"
    )
    axis.axis("off")
    figure.tight_layout()
    return figure


def plot_architecture_specification(
    specification: ArchitectureSpecification,
    footprint: PhysicalFootprintEstimate,
) -> Figure:
    """Draw resolved capacities, QEC bindings, geometry, and footprint."""

    rows = sum(
        len(module.submodules)
        for owner in _owners(specification)
        for module in owner.modules
    )
    figure, axis = plt.subplots(figsize=(15.0, max(6.0, rows * 0.7)))
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.axis("off")
    axis.text(
        0.5,
        0.975,
        f"Concrete architecture: {specification.architecture_hash[:12]}",
        ha="center",
        va="top",
        fontsize=19,
        fontweight="bold",
    )
    axis.text(
        0.5,
        0.935,
        f"Total footprint: {footprint.total_physical_qubits:,.0f} physical qubits",
        ha="center",
        va="top",
        fontsize=11,
        color="#4B5563",
    )

    y = 0.875
    row_height = 0.78 / max(1, rows)
    for owner in _owners(specification):
        modality = _owner_modality(specification, owner)
        color = _MODALITY_COLORS.get(modality, "#4F677D")
        for module in owner.modules:
            first_y = y
            axis.text(
                0.02,
                y,
                f"{_owner_kind(owner)}: {owner.id}\n{modality}",
                ha="left",
                va="center",
                fontsize=8.2,
                color=color,
                fontweight="bold",
            )
            axis.text(
                0.16,
                y,
                f"{module.id}\n{module.type}",
                ha="left",
                va="center",
                fontsize=8.8,
                fontweight="bold",
            )
            for index, submodule in enumerate(module.submodules):
                item_y = first_y - index * row_height
                components = _components_for(
                    footprint, owner.id, module.id, submodule.id
                )
                origin = (
                    [float(value) for value in submodule.logical_origin]
                    if submodule.logical_origin is not None
                    else "—"
                )
                protocol = _protocol_text(submodule.resource_protocol)
                label = (
                    f"{submodule.id} | {_capacity_label(submodule)} | "
                    f"QEC: {_qec_text(submodule)} | "
                    f"physical qubits: {_physical_qubits(components):,.0f} | "
                    f"logical origin: {origin}"
                )
                if protocol:
                    label += f" | protocol: {protocol}"
                axis.add_patch(
                    FancyBboxPatch(
                        (0.30, item_y - row_height * 0.38),
                        0.68,
                        row_height * 0.76,
                        boxstyle="round,pad=0.004,rounding_size=0.005",
                        facecolor=_SUBMODULE_COLORS.get(
                            (submodule.type, submodule.payload), "#F7FAFC"
                        ),
                        edgecolor="#344454",
                        linewidth=0.9,
                    )
                )
                axis.text(
                    0.315,
                    item_y,
                    label,
                    ha="left",
                    va="center",
                    fontsize=7.3,
                    color="#25313C",
                )
            y -= max(1, len(module.submodules)) * row_height + row_height * 0.18
    figure.tight_layout(pad=0.5)
    return figure


def plot_specification_overall_layout(
    specification: ArchitectureSpecification,
    footprint: PhysicalFootprintEstimate,
    *,
    workflow_name: str | None = None,
) -> Figure:
    """Draw resolved resource owners, local buses, and Interconnect domains."""

    owners = _owners(specification)
    figure, axis = plt.subplots(figsize=(17.0, 9.2))
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.axis("off")
    suffix = f" for {workflow_name}" if workflow_name else ""
    axis.text(
        0.5,
        0.975,
        f"Architecture Specification{suffix}",
        ha="center",
        va="top",
        fontsize=20,
        fontweight="bold",
    )
    axis.text(
        0.5,
        0.935,
        "Canonical capacities + QEC configuration | "
        f"Total footprint: {footprint.total_physical_qubits:,.0f} physical qubits",
        ha="center",
        va="top",
        fontsize=10.5,
        color="#4B5563",
    )

    gap = 0.025
    owner_width = (0.94 - gap * max(0, len(owners) - 1)) / max(1, len(owners))
    owner_y, owner_height = 0.16, 0.68
    owner_boxes: dict[str, tuple[float, float, float, float]] = {}
    module_boxes: dict[tuple[str, str], tuple[float, float, float, float]] = {}
    for owner_index, owner in enumerate(owners):
        owner_x = 0.03 + owner_index * (owner_width + gap)
        owner_boxes[owner.id] = (owner_x, owner_y, owner_width, owner_height)
        inner_width = owner_width - 0.024
        module_gap = 0.010
        module_width = (
            inner_width - module_gap * max(0, len(owner.modules) - 1)
        ) / max(1, len(owner.modules))
        for module_index, module in enumerate(owner.modules):
            module_boxes[(owner.id, module.id)] = (
                owner_x + 0.012 + module_index * (module_width + module_gap),
                owner_y + 0.065,
                module_width,
                owner_height - 0.12,
            )

    for owner in owners:
        for index, connection in enumerate(owner.connections):
            left, right = (
                module_boxes[(owner.id, endpoint.split("/", 1)[0])]
                for endpoint in connection.endpoints
            )
            start = (left[0] + left[2] / 2, left[1] + left[3] / 2)
            end = (right[0] + right[2] / 2, right[1] + right[3] / 2)
            axis.add_patch(
                FancyArrowPatch(
                    start,
                    end,
                    arrowstyle=(
                        "->" if connection.direction == "directed" else "<->"
                    ),
                    mutation_scale=10,
                    linewidth=1.4,
                    color="#59636F",
                    connectionstyle=f"arc3,rad={0.10 if index % 2 == 0 else -0.10}",
                    zorder=1,
                )
            )

    for owner in owners:
        x, y, width, height = owner_boxes[owner.id]
        modality = _owner_modality(specification, owner)
        color = _MODALITY_COLORS.get(modality, "#4F677D")
        axis.add_patch(
            FancyBboxPatch(
                (x, y),
                width,
                height,
                boxstyle="round,pad=0.006,rounding_size=0.012",
                facecolor="#FAFBFC",
                edgecolor=color,
                linewidth=2.2,
                zorder=2,
            )
        )
        heading = f"{_owner_kind(owner).upper()}: {owner.id}"
        if isinstance(owner, Node):
            heading += f" | {_human(owner.modality).upper()}"
        else:
            heading += f" | endpoints={len(owner.endpoints)}"
        axis.text(
            x + 0.010,
            y + height - 0.026,
            heading,
            ha="left",
            va="center",
            fontsize=8.7,
            fontweight="bold",
            color=color,
            zorder=4,
        )

        for module in owner.modules:
            mx, my, mw, mh = module_boxes[(owner.id, module.id)]
            axis.add_patch(
                FancyBboxPatch(
                    (mx, my),
                    mw,
                    mh,
                    boxstyle="round,pad=0.004,rounding_size=0.008",
                    facecolor=_MODULE_COLORS.get(module.type, "#ECEFF2"),
                    edgecolor="#344454",
                    linewidth=1.2,
                    zorder=3,
                )
            )
            axis.text(
                mx + mw / 2,
                my + mh - 0.025,
                f"{_human(module.type).upper()}\n{module.id}",
                ha="center",
                va="top",
                fontsize=7.8,
                fontweight="bold",
                color="#243447",
                zorder=4,
            )
            available = mh - 0.105
            item_gap = 0.008
            item_height = min(
                0.108,
                (
                    available
                    - item_gap * max(0, len(module.submodules) - 1)
                )
                / max(1, len(module.submodules)),
            )
            for index, submodule in enumerate(module.submodules):
                sy = my + 0.025 + index * (item_height + item_gap)
                components = _components_for(
                    footprint, owner.id, module.id, submodule.id
                )
                label = (
                    f"{submodule.id}\n{_capacity_label(submodule)}\n"
                    f"{_qec_text(submodule)} | "
                    f"{_physical_qubits(components):,.0f} phys."
                )
                axis.add_patch(
                    FancyBboxPatch(
                        (mx + 0.008, sy),
                        mw - 0.016,
                        item_height,
                        boxstyle="round,pad=0.002,rounding_size=0.004",
                        facecolor=_SUBMODULE_COLORS.get(
                            (submodule.type, submodule.payload), "#F7FAFC"
                        ),
                        edgecolor="#344454",
                        linewidth=0.8,
                        zorder=4,
                    )
                )
                axis.text(
                    mx + mw / 2,
                    sy + item_height / 2,
                    label,
                    ha="center",
                    va="center",
                    fontsize=5.9,
                    color="#24313D",
                    zorder=5,
                )
    figure.tight_layout(pad=0.5)
    return figure


def _display_cell_count(
    submodule: Submodule,
    components: tuple[FootprintComponent, ...],
) -> tuple[int, str, str]:
    rule = components[0].rule if components else ""
    details = dict(components[0].details) if components else {}
    if rule == "bb_code_block":
        return int(details.get("blocks", 0)), "code blocks", "B"
    if submodule.type == "engine":
        return submodule.capacity, "engine copies", "F"
    return submodule.capacity, "logical units", "U"


def _compiler_layout_matches(
    compiler_layout: LogicalLayout | None,
    owner: Owner,
    module: Module,
) -> bool:
    return (
        compiler_layout is not None
        and compiler_layout.node_id == owner.id
        and compiler_layout.module_id == module.id
    )


def _plot_compiler_layout_panel(axis, layout: LogicalLayout) -> bool:
    points = [slot.coordinate for slot in layout.slots]
    routing_coordinates = {
        str(identifier): tuple(float(value) for value in coordinate)
        for identifier, coordinate in layout.metadata.get(
            "routing_coordinates", {}
        ).items()
    }
    points.extend(routing_coordinates.values())
    if not points:
        return False
    for left, right in layout.routing_edges:
        if left not in routing_coordinates or right not in routing_coordinates:
            continue
        start, end = routing_coordinates[left], routing_coordinates[right]
        axis.plot(
            [start[0], end[0]],
            [start[1], end[1]],
            color="#C6CDD4",
            linewidth=0.45,
            zorder=1,
        )
    if routing_coordinates:
        routing = list(routing_coordinates.values())
        axis.scatter(
            [point[0] for point in routing],
            [point[1] for point in routing],
            s=5,
            c="#B7C0C8",
            linewidths=0,
            label="compiler routing site",
            zorder=2,
        )
    for kind, color, label in (
        ("data", "#3478A6", "compute slot"),
        ("magic_state", "#D8872B", "magic-state slot"),
    ):
        slots = layout.slots_of_kind(kind)
        if not slots:
            continue
        axis.scatter(
            [slot.coordinate[0] for slot in slots],
            [slot.coordinate[1] for slot in slots],
            marker="s",
            s=34,
            c=color,
            edgecolors="#263442",
            linewidths=0.6,
            label=label,
            zorder=4,
        )
    axis.text(
        0.5,
        0.04,
        "compiler-derived routing artifact",
        transform=axis.transAxes,
        ha="center",
        va="bottom",
        fontsize=7,
        color="#4B5563",
    )
    axis.legend(loc="upper right", fontsize=6)
    return True


def plot_module_cube_layout(
    specification: ArchitectureSpecification,
    footprint: PhysicalFootprintEstimate,
    *,
    workflow_name: str | None = None,
    compiler_layout: LogicalLayout | None = None,
) -> Figure:
    """Expand Modules into resolved logical units or footprint-derived blocks."""

    owned_modules = [
        (owner, module)
        for owner in _owners(specification)
        for module in owner.modules
    ]
    columns = min(3, len(owned_modules))
    rows = math.ceil(len(owned_modules) / columns)
    figure, axes = plt.subplots(
        rows,
        columns,
        figsize=(6.0 * columns, 5.0 * rows),
        squeeze=False,
    )
    suffix = f" — {workflow_name}" if workflow_name else ""
    figure.suptitle(
        f"Module-internal logical layout{suffix}",
        fontsize=19,
        fontweight="bold",
        y=0.975,
    )
    order = {
        ("buffer", "logical_qubit"): 0,
        ("region", "logical_qubit"): 1,
        ("buffer", "magic_state"): 2,
        ("engine", "magic_state"): 1,
        ("engine", "bell_pair"): 1,
        ("buffer", "bell_pair"): 2,
    }
    for axis, (owner, module) in zip(axes.flat, owned_modules):
        axis.set_xlim(0, 1)
        axis.set_ylim(0, 1)
        axis.axis("off")
        modality = _owner_modality(specification, owner)
        axis.set_title(
            f"{module.id} | {module.type} | {owner.id} ({_human(modality)})",
            fontsize=11.5,
            fontweight="bold",
            color="#243447",
            pad=8,
        )
        if _compiler_layout_matches(compiler_layout, owner, module):
            assert compiler_layout is not None
            if _plot_compiler_layout_panel(axis, compiler_layout):
                continue

        submodules = sorted(
            module.submodules,
            key=lambda item: (
                order.get((item.type, item.payload), 5),
                item.id,
            ),
        )
        gap = 0.025
        width = (
            0.94 - gap * max(0, len(submodules) - 1)
        ) / max(1, len(submodules))
        for index, submodule in enumerate(submodules):
            x0 = 0.03 + index * (width + gap)
            components = _components_for(
                footprint, owner.id, module.id, submodule.id
            )
            count, count_label, prefix = _display_cell_count(
                submodule, components
            )
            color = _SUBMODULE_COLORS.get(
                (submodule.type, submodule.payload), "#DDE4EA"
            )
            axis.add_patch(
                FancyBboxPatch(
                    (x0, 0.08),
                    width,
                    0.80,
                    boxstyle="round,pad=0.005,rounding_size=0.012",
                    facecolor="#F8FAFC",
                    edgecolor="#344454",
                    linewidth=1.2,
                )
            )
            axis.text(
                x0 + width / 2,
                0.84,
                f"{submodule.id}\n{submodule.type} / {submodule.payload}",
                ha="center",
                va="top",
                fontsize=8.0,
                fontweight="bold",
                wrap=True,
            )
            protocol = _protocol_text(submodule.resource_protocol)
            details = f"{_capacity_label(submodule)}\nQEC: {_qec_text(submodule)}"
            if protocol:
                details += f"\nprotocol: {protocol}"
            axis.text(
                x0 + width / 2,
                0.745,
                details,
                ha="center",
                va="top",
                fontsize=6.4,
                color="#4B5563",
            )

            grid_y0, grid_y1 = 0.20, 0.64
            if count:
                cell_columns = max(1, math.ceil(math.sqrt(count)))
                cell_rows = math.ceil(count / cell_columns)
                cell_gap = min(0.008, width * 0.035)
                cell_width = min(
                    (
                        width
                        - 0.035
                        - cell_gap * (cell_columns - 1)
                    )
                    / cell_columns,
                    0.11,
                )
                cell_height = min(
                    (
                        grid_y1
                        - grid_y0
                        - cell_gap * (cell_rows - 1)
                    )
                    / cell_rows,
                    0.105,
                )
                total_width = (
                    cell_columns * cell_width
                    + (cell_columns - 1) * cell_gap
                )
                total_height = (
                    cell_rows * cell_height
                    + (cell_rows - 1) * cell_gap
                )
                grid_x = x0 + (width - total_width) / 2
                grid_y = grid_y0 + (grid_y1 - grid_y0 - total_height) / 2
                for cell in range(count):
                    column = cell % cell_columns
                    row = cell // cell_columns
                    cell_x = grid_x + column * (cell_width + cell_gap)
                    cell_y = grid_y + row * (cell_height + cell_gap)
                    axis.add_patch(
                        Rectangle(
                            (cell_x, cell_y),
                            cell_width,
                            cell_height,
                            facecolor=color,
                            edgecolor="#263442",
                            linewidth=0.75,
                        )
                    )
                    axis.text(
                        cell_x + cell_width / 2,
                        cell_y + cell_height / 2,
                        f"{prefix}{cell}",
                        ha="center",
                        va="center",
                        fontsize=max(4.5, min(7.0, cell_width * 80)),
                        color="#1F2D3A",
                    )
            else:
                axis.text(
                    x0 + width / 2,
                    0.43,
                    "0 allocated",
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="#6B7280",
                )
            axis.text(
                x0 + width / 2,
                0.135,
                f"{count} {count_label}\n"
                f"{_physical_qubits(components):,.0f} physical qubits",
                ha="center",
                va="center",
                fontsize=6.8,
                color="#374151",
            )
    for axis in list(axes.flat)[len(owned_modules) :]:
        axis.axis("off")
    figure.text(
        0.5,
        0.012,
        "Each U* cell is a canonical logical slot, each F* cell is an engine "
        "copy, and BB B* cells are visualization-derived physical-footprint "
        "blocks rather than logical-slot identities.",
        ha="center",
        va="bottom",
        fontsize=8.5,
        color="#5B6470",
    )
    figure.tight_layout(rect=(0, 0.035, 1, 0.93))
    return figure


def plot_architecture_slot_layout(
    layout: LogicalLayout,
    architecture: ArchitectureSpecification | None = None,
) -> Figure:
    """Plot one compiler layout, optionally overlaying other canonical slots."""

    figure, axis = plt.subplots(figsize=(11.5, 8.0))
    routing_coordinates = {
        str(identifier): tuple(float(value) for value in point)
        for identifier, point in layout.metadata.get(
            "routing_coordinates", {}
        ).items()
    }
    for left, right in layout.routing_edges:
        if left in routing_coordinates and right in routing_coordinates:
            start, end = routing_coordinates[left], routing_coordinates[right]
            axis.plot(
                [start[0], end[0]],
                [start[1], end[1]],
                color="#CFD5DB",
                linewidth=0.6,
                zorder=1,
            )
    if routing_coordinates:
        points = list(routing_coordinates.values())
        axis.scatter(
            [point[0] for point in points],
            [point[1] for point in points],
            s=7,
            c="#C4CBD2",
            label="compiler routing site",
            zorder=2,
        )
    for kind, color, label in (
        ("data", "#3478A6", "compute slot"),
        ("magic_state", "#D8872B", "magic-state slot"),
    ):
        slots = layout.slots_of_kind(kind)
        if not slots:
            continue
        axis.scatter(
            [slot.coordinate[0] for slot in slots],
            [slot.coordinate[1] for slot in slots],
            s=58,
            c=color,
            edgecolors="#263442",
            linewidths=0.7,
            label=label,
            zorder=4,
        )
        for slot in slots:
            axis.text(
                slot.coordinate[0],
                slot.coordinate[1],
                slot.id,
                fontsize=5,
                ha="center",
                va="center",
                color="white",
                zorder=5,
            )

    if architecture is not None:
        for owner in _owners(architecture):
            if owner.id != layout.node_id:
                continue
            for module in owner.modules:
                if module.id != layout.module_id:
                    continue
                compiled_ids = {slot.id for slot in layout.slots}
                for submodule in module.submodules:
                    positions = [
                        (slot.id, _absolute_coordinate(submodule, slot.coordinate))
                        for slot in submodule.slots
                    ]
                    positions = [
                        (identifier, point)
                        for identifier, point in positions
                        if point is not None
                        and f"{owner.id}/{module.id}/{submodule.id}/{identifier}"
                        not in compiled_ids
                    ]
                    if not positions:
                        continue
                    axis.scatter(
                        [point[0] for _identifier, point in positions],
                        [point[1] for _identifier, point in positions],
                        marker="s",
                        s=62,
                        c="#6C5AA7",
                        edgecolors="#263442",
                        linewidths=0.7,
                        label=f"canonical {submodule.id}",
                        zorder=4,
                    )
    placement = layout.metadata.get("magic_state_placement")
    placement_suffix = f", magic={placement}" if placement else ""
    axis.set_title(
        f"Compiler slot layout: {layout.node_id}/{layout.module_id} "
        f"({layout.modality}, {layout.layout_type}{placement_suffix})"
    )
    axis.set_xlabel("logical-layout x")
    axis.set_ylabel("logical-layout y")
    axis.set_aspect("equal", adjustable="datalim")
    axis.grid(alpha=0.12)
    axis.legend(loc="best")
    figure.tight_layout()
    return figure


__all__ = [
    "plot_architecture_hierarchy",
    "plot_architecture_profile",
    "plot_architecture_specification",
    "plot_architecture_slot_layout",
    "plot_module_cube_layout",
    "plot_specification_overall_layout",
]
