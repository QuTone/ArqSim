from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

import pytest

from arqsim.program import FTCircuit, LogicalLayer, LogicalOperation
from arqsim.program.errors import UnsupportedSynthesisPassError
from arqsim.synthesizer import (
    NWQECSynthesizer,
    SynthesisBackend,
    SynthesisSpec,
    Synthesizer,
    load_artifact_workload,
)


class _FutureStarSynthesizer(SynthesisBackend):
    name = "future_star"

    def version(self) -> str:
        return "test"

    def effective_parameters(self, spec: SynthesisSpec) -> Mapping[str, Any]:
        return {}

    def synthesize(
        self,
        source: Path,
        destination: Path,
        spec: SynthesisSpec,
        effective_parameters: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        destination.write_bytes(source.read_bytes())
        return {}

    def load_workload(
        self,
        artifact: Path,
        spec: SynthesisSpec,
        *,
        provenance: Mapping[str, Any],
    ) -> FTCircuit:
        return FTCircuit(
            representation=spec.representation,
            num_qubits=1,
            num_clbits=0,
            layers=(
                LogicalLayer(
                    0,
                    (
                        LogicalOperation(
                            "gate",
                            "rz",
                            qubits=(0,),
                            parameters=(0.125,),
                        ),
                    ),
                ),
            ),
            provenance=provenance,
        )


def test_synthesis_request_allows_backend_defined_output_dialects() -> None:
    spec = SynthesisSpec(
        backend="future_star",
        representation="clifford-rz",
    )

    assert spec.representation == "clifford_rz"


def test_nwqec_rejects_unsupported_dialect_at_backend_boundary() -> None:
    spec = SynthesisSpec(
        backend="nwqec",
        representation="clifford_rz",
    )

    with pytest.raises(
        UnsupportedSynthesisPassError,
        match="cannot produce",
    ) as exc_info:
        NWQECSynthesizer().effective_parameters(spec)

    assert exc_info.value.details == {
        "representation": "clifford_rz",
        "supported": ["clifford_t", "pbc"],
    }


def test_custom_synthesizer_owns_normalization_for_a_new_dialect(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.qasm"
    source.write_text("opaque test source\n", encoding="utf-8")
    service = Synthesizer(
        tmp_path / "cache",
        backends={"future_star": _FutureStarSynthesizer()},
    )

    artifact = service.synthesize(
        source,
        SynthesisSpec("future_star", "clifford_rz"),
    )
    circuit = load_artifact_workload(artifact)

    assert circuit.representation == "clifford_rz"
    assert circuit.layers[0].operations[0].name == "rz"
