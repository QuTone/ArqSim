"""External FT-circuit synthesis contract."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from heteqsys.program.errors import InputValidationError


@dataclass(frozen=True)
class SynthesisSpec:
    backend: str
    representation: str
    rz_error_policy: str | None = None
    epsilon: float | None = None
    keep_ccx: bool = False
    keep_cx: bool = False
    optimize_t_count: bool = False

    def __post_init__(self) -> None:
        backend = self.backend.strip().lower()
        representation = self.representation.strip().lower().replace("-", "_")
        if representation == "clifford+t":
            representation = "clifford_t"
        policy = self.rz_error_policy.strip().lower() if self.rz_error_policy else None
        if not backend:
            raise InputValidationError("A synthesis backend must be specified")
        if representation not in {"clifford_t", "pbc"}:
            raise InputValidationError(f"Unsupported representation: {representation}")
        if policy is not None and policy not in {"per-gate", "total", "relative"}:
            raise InputValidationError(f"Unsupported RZ approximation policy: {policy}")
        if self.epsilon is not None and (
            not math.isfinite(float(self.epsilon)) or float(self.epsilon) <= 0
        ):
            raise InputValidationError("RZ approximation epsilon must be positive")
        object.__setattr__(self, "backend", backend)
        object.__setattr__(self, "representation", representation)
        object.__setattr__(self, "rz_error_policy", policy)
        if self.epsilon is not None:
            object.__setattr__(self, "epsilon", float(self.epsilon))

    def requested_parameters(self) -> dict[str, Any]:
        return {
            "rz_error_policy": self.rz_error_policy,
            "epsilon": self.epsilon,
            "keep_ccx": self.keep_ccx,
            "keep_cx": self.keep_cx,
            "optimize_t_count": self.optimize_t_count,
        }


class SynthesisBackend(ABC):
    name: str

    @abstractmethod
    def version(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def effective_parameters(self, spec: SynthesisSpec) -> Mapping[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def synthesize(
        self,
        source: Path,
        destination: Path,
        spec: SynthesisSpec,
        effective_parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        raise NotImplementedError
