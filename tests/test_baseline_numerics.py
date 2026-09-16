"""Numerical regression coverage using synthetic architecture instructions."""

import pytest

from arqsim.architecture.isa import ArchitectureInstruction, ArchitectureOpcode
from arqsim.evaluation import (
    BufferSpec,
    EvaluationPolicy,
    ExecutionPlan,
    ProgramDAG,
    ResourceDAG,
    ResourceProcess,
    estimate_static_layerwise_aggregation,
)


def test_deep_static_estimate_conserves_latency_without_roundoff_failure() -> None:
    # Decimal costs deliberately have inexact binary representations. At this
    # depth, naive component-first and layer-first sums disagree enough to
    # reject a valid plan, even though its expected costs are exactly 4 + 12 s.
    layer_count = 40_000
    plan = ExecutionPlan(
        "synthetic-circuit",
        "synthetic-architecture",
        "synthetic-latency",
        EvaluationPolicy(),
        ProgramDAG(
            tuple(
                ArchitectureInstruction(
                    layer,
                    ArchitectureOpcode.EXECUTE_COMPUTE,
                    predecessor_ids=(layer - 1,) if layer else (),
                    duration_s=0.0001,
                    layer_index=layer,
                    consumes={"magic": 1},
                )
                for layer in range(layer_count)
            )
        ),
        ResourceDAG(
            (
                ResourceProcess(
                    "factory",
                    ArchitectureOpcode.PREPARE_MAGIC_STATE,
                    produces={"magic": 1},
                    duration_s=0.0003,
                ),
            )
        ),
        (BufferSpec("magic", 1, "magic_state"),),
        (),
    )

    estimate = estimate_static_layerwise_aggregation(plan)

    assert estimate.total_latency_s == pytest.approx(16.0, rel=1e-15, abs=0.0)
    assert estimate.exclusive_breakdown_s == pytest.approx(
        {"compute": 4.0, "resource_acquisition_magic": 12.0},
        rel=1e-15,
        abs=0.0,
    )
    assert len(estimate.layers) == layer_count
    assert estimate.layers[-1].total_latency_s == pytest.approx(
        0.0004, rel=1e-15, abs=0.0
    )
    assert estimate.plan_hash == plan.plan_hash
    assert all(layer.resource_demand["magic_states"] == 1 for layer in estimate.layers)
