"""Internal artifacts produced by one evaluation run.

This service-owned record is deliberately not part of the public facade.  It
gives orchestration one typed hand-off between execution/analysis and legacy
report rendering without making the report-v1 document shape the core model.
"""

from __future__ import annotations

from dataclasses import dataclass

from arqsim.architecture.specification import ArchitectureSpecification
from arqsim.compiler import (
    LogicalCompilationResult,
    LogicalCompilerSpec,
    LogicalLayout,
)
from arqsim.evaluation.analysis import EvaluationAnalysis
from arqsim.evaluation.fidelity_estimator import FidelityEstimate
from arqsim.evaluation.footprint import (
    PhysicalFootprintEstimate,
    PhysicalFootprintModel,
)
from arqsim.evaluation.plan import ExecutionPlan
from arqsim.evaluation.result import EvaluationResult
from arqsim.operation_profiles import (
    FidelityProfile,
    OperationLatencyProfile,
    ResolvedResourceProtocolBindings,
)
from arqsim.program import FTCircuit


@dataclass(frozen=True)
class EvaluationRunArtifacts:
    """Typed, immutable outputs of orchestration before report rendering.

    The canonical architecture is the only static-architecture authority.
    Effective latency, logical compilation, compiler layout, footprint
    accounting, and resource-protocol bindings are independent typed artifacts
    rather than fields on an architecture build wrapper.  Keeping them
    separate here gives Report v2 all of its validation authorities while the
    report-v1 adapter can still render a redundant compatibility document
    without feeding that document back into compilation or evaluation.
    """

    circuit: FTCircuit
    magic_sizing_circuit: FTCircuit | None
    specification: ArchitectureSpecification
    compiler_layout: LogicalLayout
    footprint_model: PhysicalFootprintModel
    footprint: PhysicalFootprintEstimate
    resource_protocol_bindings: ResolvedResourceProtocolBindings
    latency_profile: OperationLatencyProfile
    compiler_spec: LogicalCompilerSpec
    logical_compilation: LogicalCompilationResult
    execution_plan: ExecutionPlan
    evaluation: EvaluationResult
    fidelity_profile: FidelityProfile | None
    fidelity: FidelityEstimate | None
    analysis: EvaluationAnalysis


__all__ = ["EvaluationRunArtifacts"]
