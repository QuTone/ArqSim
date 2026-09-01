"""Replaceable whole-pipeline compiler contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from heteqsys.architecture.specification import ArchitectureSpecification
    from heteqsys.operation_profiles import OperationLatencyProfile
    from heteqsys.program import FTCircuit

    from .output import LogicalCompilationResult


class CompilerPipeline(ABC):
    """Lower an FT circuit to architecture-facing compute units."""

    @abstractmethod
    def compile(
        self,
        circuit: FTCircuit,
        specification: ArchitectureSpecification,
        latency_profile: OperationLatencyProfile,
    ) -> LogicalCompilationResult:
        raise NotImplementedError
