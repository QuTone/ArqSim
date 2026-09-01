from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from heteqsys.visualization import (
    plot_architecture_hierarchy,
    plot_architecture_profile,
    plot_architecture_specification,
    plot_execution_timeline,
    plot_module_cube_layout,
    plot_program_dag,
    plot_resource_dag,
)
from heteqsys.architecture import get_architecture_profile
from heteqsys.evaluation import (
    PhysicalFootprintModel,
    estimate_physical_footprint,
)
from heteqsys.program import FTCircuit, LogicalLayer, LogicalOperation
from heteqsys.specification import build_architecture_specification

from .test_evaluation import _cold_start_plan
from heteqsys.evaluation import evaluate


def test_profile_visualization_reads_canonical_v3_for_local_and_multi_node(
    tmp_path,
) -> None:
    for profile_id in ("1.1", "1.3"):
        profile = get_architecture_profile(profile_id)
        before = profile.to_dict()
        figure = plot_architecture_profile(profile)
        output = tmp_path / f"profile-{profile_id}.png"
        figure.savefig(output)

        labels = {text.get_text() for text in figure.axes[0].texts}
        assert any(label.startswith(f"Profile {profile_id}:") for label in labels)
        assert output.stat().st_size > 0
        assert profile.to_dict() == before
        if profile_id == "1.3":
            assert any(label.startswith("INTERCONNECT:") for label in labels)
        plt.close(figure)


def test_canonical_visualizations_are_pure_views(tmp_path) -> None:
    plan = _cold_start_plan()
    result = evaluate(plan)
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "h", qubits=(0,)),),
            ),
        ),
    )
    architecture = build_architecture_specification(
        circuit,
        "2.3",
    )
    figures = (
        plot_architecture_hierarchy(architecture),
        plot_program_dag(plan.program_dag),
        plot_resource_dag(plan.resource_dag),
        plot_execution_timeline(result),
    )
    for index, figure in enumerate(figures):
        output = tmp_path / f"view-{index}.png"
        figure.savefig(output)
        assert output.stat().st_size > 0


def test_bb_memory_visualization_derives_blocks_without_slot_geometry() -> None:
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=36,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "h", qubits=(0,)),),
            ),
        ),
        provenance={"benchmark": "bb-visualization-derivation"},
    )
    specification = build_architecture_specification(circuit, "2.1")
    footprint = estimate_physical_footprint(
        specification,
        PhysicalFootprintModel.reference_v1(),
    )
    before = specification.to_dict()
    memory = next(
        submodule
        for node in specification.nodes
        for module in node.modules
        if module.type == "memory"
        for submodule in module.submodules
        if submodule.type == "region"
        and submodule.payload == "logical_qubit"
    )
    assert len(memory.slots) == 35
    assert all(slot.coordinate is None for slot in memory.slots)
    component = next(
        item
        for item in footprint.components
        if item.submodule_id == memory.id
    )
    assert component.details["blocks"] == 3

    blocks_figure = plot_module_cube_layout(
        specification,
        footprint,
    )
    memory_axis = next(
        axis for axis in blocks_figure.axes if axis.get_title().startswith("na_memory")
    )
    cell_labels = {
        text.get_text()
        for text in memory_axis.texts
        if text.get_text().startswith("B")
    }
    assert cell_labels == {"B0", "B1", "B2"}
    assert all("na_memory.D" not in text.get_text() for text in memory_axis.texts)
    assert any(
        "visualization-derived physical-footprint blocks" in text.get_text()
        for text in blocks_figure.texts
    )

    detail_figure = plot_architecture_specification(
        specification,
        footprint,
    )
    memory_detail = next(
        text.get_text()
        for text in detail_figure.axes[0].texts
        if "memory_region" in text.get_text()
    )
    assert "logical origin: —" in memory_detail
    assert "logical origin: [0.0, 20.0]" not in memory_detail
    assert specification.to_dict() == before
    plt.close(blocks_figure)
    plt.close(detail_figure)
