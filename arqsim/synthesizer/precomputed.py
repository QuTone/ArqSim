"""Adapter for a circuit that has already been synthesized."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Mapping

from arqsim.program import load_ft_workload
from arqsim.program.errors import UnsupportedSynthesisPassError

from .interface import SynthesisBackend, SynthesisSpec


class PrecomputedCircuitLoader(SynthesisBackend):
    name = "precomputed"

    def version(self) -> str:
        return "1"

    def effective_parameters(self, spec: SynthesisSpec) -> Mapping[str, Any]:
        active = {
            key: value
            for key, value in spec.requested_parameters().items()
            if value not in (None, False)
        }
        if active:
            raise UnsupportedSynthesisPassError(
                "Synthesis options cannot be applied to a precomputed circuit",
                details={"options": active},
            )
        return {"mode": "precomputed", "representation": spec.representation}

    def synthesize(
        self,
        source: Path,
        destination: Path,
        spec: SynthesisSpec,
        effective_parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        load_ft_workload(source, spec.representation)
        shutil.copyfile(source, destination)
        return {"adapter": "precomputed"}
