"""Architecture-aware allocation, mapping, routing, and movement compilation."""

from .config import canonical_compiler_spec
from .interface import CompilerPipeline
from .models import (
    BackendSpec,
    LayoutSlot,
    LogicalCompilerSpec,
    LogicalLayout,
    LogicalPlacement,
    LogicalRoutePlan,
    Movement,
    PlacementEntry,
    RouteStep,
)
from .output import (
    COMPILATION_RESULT_SCHEMA_VERSION,
    LogicalCompilationResult,
    CompiledComputeUnit,
    CompiledRouteResult,
    CompiledRouteStep,
    ComputeBatch,
    ComputeDuration,
    ComputePartition,
    SyndromeProtocolTiming,
    validate_compilation_coverage,
)
from .pipeline import (
    DefaultCompilerPipeline,
    compile_ft_circuit,
)

__all__ = [
    "BackendSpec",
    "COMPILATION_RESULT_SCHEMA_VERSION",
    "LogicalCompilationResult",
    "CompiledComputeUnit",
    "CompiledRouteResult",
    "CompiledRouteStep",
    "CompilerPipeline",
    "ComputeBatch",
    "ComputeDuration",
    "ComputePartition",
    "DefaultCompilerPipeline",
    "LayoutSlot",
    "LogicalCompilerSpec",
    "LogicalLayout",
    "LogicalPlacement",
    "LogicalRoutePlan",
    "Movement",
    "PlacementEntry",
    "RouteStep",
    "SyndromeProtocolTiming",
    "validate_compilation_coverage",
    "canonical_compiler_spec",
    "compile_ft_circuit",
]
