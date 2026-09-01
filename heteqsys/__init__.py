"""Small stable facade for the most common ArqSim workflow.

Advanced authoring, compiler, profile, schema, and validation contracts live
in their owning subpackages. Names removed from the root facade during the
Report/API-v2 cutover remain available for one release through lazy
deprecation aliases.
"""

from __future__ import annotations

from importlib import import_module
import warnings

from .api import EvaluationConfig, EvaluationReport, run_evaluation
from .program import FTCircuit

__all__ = [
    "EvaluationConfig",
    "EvaluationReport",
    "FTCircuit",
    "run_evaluation",
]

__version__ = "0.2.0"


_DEPRECATED_ROOT_ALIASES = {
    "ArchitectureProfile": (".architecture", "ArchitectureProfile"),
    "ArchitectureSpecification": (".architecture", "ArchitectureSpecification"),
    "ArrivalDistribution": (".operation_profiles", "ArrivalDistribution"),
    "BackendSpec": (".compiler", "BackendSpec"),
    "CANONICAL_FIDELITY_PRESET": (".api", "CANONICAL_FIDELITY_PRESET"),
    "DEFAULT_FOOTPRINT_PRESET": (".api", "DEFAULT_FOOTPRINT_PRESET"),
    "EFFECTIVE_EVALUATION_CONFIG_SCHEMA_VERSION": (
        ".api",
        "EFFECTIVE_EVALUATION_CONFIG_SCHEMA_VERSION",
    ),
    "EVALUATION_CONFIG_SCHEMA_VERSION": (
        ".api",
        "EVALUATION_CONFIG_SCHEMA_VERSION",
    ),
    "EVALUATION_REPORT_SCHEMA_VERSION": (
        ".api",
        "EVALUATION_REPORT_SCHEMA_VERSION",
    ),
    "EffectiveEvaluationConfig": (".api", "EffectiveEvaluationConfig"),
    "EvaluationAnalysis": (".evaluation", "EvaluationAnalysis"),
    "EvaluationPolicy": (".evaluation", "EvaluationPolicy"),
    "FidelityProfile": (".operation_profiles", "FidelityProfile"),
    "LogicalCompilerSpec": (".compiler", "LogicalCompilerSpec"),
    "LogicalLayer": (".program", "LogicalLayer"),
    "LogicalLayoutRequest": (".architecture", "LogicalLayoutRequest"),
    "LogicalOperation": (".program", "LogicalOperation"),
    "OperationLatencyProfile": (".operation_profiles", "OperationLatencyProfile"),
    "PhysicalFootprintModel": (".evaluation", "PhysicalFootprintModel"),
    "WORKLOAD_SCHEMA_VERSION": (".program", "WORKLOAD_SCHEMA_VERSION"),
    "WorkloadParseError": (".program", "WorkloadParseError"),
    "build_architecture_specification": (
        ".specification",
        "build_architecture_specification",
    ),
    "get_architecture_profile": (".architecture", "get_architecture_profile"),
    "list_architecture_profiles": (".architecture", "list_architecture_profiles"),
    "load_evaluation_report_document": (
        ".api",
        "load_evaluation_report_document",
    ),
    "load_ft_workload": (".program", "load_ft_workload"),
    "make_layers": (".program", "make_layers"),
    "validate_evaluation_report_document": (
        ".api",
        "validate_evaluation_report_document",
    ),
    "workload_stats": (".program", "workload_stats"),
}


def __getattr__(name: str) -> object:
    """Resolve one-release compatibility aliases without widening ``__all__``."""

    target = _DEPRECATED_ROOT_ALIASES.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    warnings.warn(
        f"heteqsys.{name} is deprecated; import {attribute_name} from "
        f"heteqsys{module_name} instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return getattr(import_module(module_name, __name__), attribute_name)
