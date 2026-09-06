"""Optional NWQEC synthesis adapter."""

from __future__ import annotations

import importlib
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping

from arqsim.program.circuit import normalize_json_value
from arqsim.program.errors import (
    BackendUnavailableError,
    SynthesisExecutionError,
    UnsupportedSynthesisPassError,
)

from .interface import SynthesisBackend, SynthesisSpec


class NWQECSynthesizer(SynthesisBackend):
    name = "nwqec"
    supported_representations = frozenset({"clifford_t", "pbc"})

    def _validate_representation(self, spec: SynthesisSpec) -> None:
        if spec.representation not in self.supported_representations:
            raise UnsupportedSynthesisPassError(
                "NWQEC cannot produce the requested output representation",
                details={
                    "representation": spec.representation,
                    "supported": sorted(self.supported_representations),
                },
            )

    def _module(self) -> Any:
        try:
            return importlib.import_module("nwqec")
        except (ImportError, OSError) as exc:
            raise BackendUnavailableError(
                "The NWQEC backend is not installed; install the optional extra "
                "from the ArqSim source checkout",
                details={"install": "python -m pip install '.[nwqec]'", "reason": str(exc)},
            ) from exc

    def version(self) -> str:
        try:
            return metadata.version("nwqec")
        except metadata.PackageNotFoundError:
            return str(getattr(self._module(), "__version__", "unknown"))

    def effective_parameters(self, spec: SynthesisSpec) -> Mapping[str, Any]:
        self._validate_representation(spec)
        invalid = (
            {"keep_cx": spec.keep_cx, "optimize_t_count": spec.optimize_t_count}
            if spec.representation == "clifford_t"
            else {"keep_ccx": spec.keep_ccx}
        )
        active_invalid = {key: value for key, value in invalid.items() if value}
        if active_invalid:
            raise UnsupportedSynthesisPassError(
                "Requested NWQEC options do not apply to this representation",
                details={"representation": spec.representation, "options": active_invalid},
            )
        policy = spec.rz_error_policy or "per-gate"
        epsilon = spec.epsilon if spec.epsilon is not None else (1e-10 if policy == "per-gate" else 1e-2)
        result: dict[str, Any] = {"rz_error_policy": policy, "epsilon": epsilon}
        if spec.representation == "clifford_t":
            result["keep_ccx"] = spec.keep_ccx
        else:
            result.update(
                {"keep_cx": spec.keep_cx, "optimize_t_count": spec.optimize_t_count}
            )
        return result

    def synthesize(
        self,
        source: Path,
        destination: Path,
        spec: SynthesisSpec,
        effective_parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        self._validate_representation(spec)
        nwqec = self._module()
        try:
            source_circuit = nwqec.load_qasm(str(source))
            common = {
                "rz_err": effective_parameters["rz_error_policy"],
                "epsilon": effective_parameters["epsilon"],
            }
            if spec.representation == "clifford_t":
                circuit = nwqec.to_clifford_t(
                    source_circuit,
                    keep_ccx=effective_parameters["keep_ccx"],
                    **common,
                )
            else:
                circuit = nwqec.to_pbc(
                    source_circuit,
                    keep_cx=effective_parameters["keep_cx"],
                    optimize_t_count=effective_parameters["optimize_t_count"],
                    **common,
                )
            circuit.save_qasm(str(destination))
            stats: dict[str, Any] = {}
            for method_name in ("count_ops", "stats", "num_qubits", "depth"):
                method = getattr(circuit, method_name, None)
                if callable(method):
                    stats[method_name] = normalize_json_value(method())
            return stats
        except UnsupportedSynthesisPassError:
            raise
        except Exception as exc:
            raise SynthesisExecutionError(
                "NWQEC could not synthesize the input circuit",
                details={"representation": spec.representation, "reason": str(exc)},
            ) from exc
