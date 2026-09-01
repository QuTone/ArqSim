from __future__ import annotations

from dataclasses import replace

import pytest

from heteqsys.compiler import (
    BackendSpec,
    DefaultCompilerPipeline,
    LogicalCompilationResult,
)
from heteqsys.evaluation import (
    EvaluationPolicy,
    compile_and_lower,
    lower_compilation_result,
)
from heteqsys.operation_profiles import (
    OperationLatencyProfile,
    resolve_resource_protocol_bindings,
    with_effective_arrivals,
)
from heteqsys.program import FTCircuit, LogicalLayer, LogicalOperation
from heteqsys.specification import build_architecture_specification


def _lowering_fixture():
    circuit = FTCircuit(
        representation="clifford_t",
        num_qubits=1,
        num_clbits=0,
        layers=(
            LogicalLayer(
                0,
                (LogicalOperation("gate", "t", qubits=(0,)),),
            ),
        ),
    )
    specification = build_architecture_specification(circuit, "1.1")
    requested_latency = OperationLatencyProfile()
    bindings = resolve_resource_protocol_bindings(
        specification,
        requested_latency,
    )
    latency = with_effective_arrivals(requested_latency, bindings)
    policy = EvaluationPolicy()
    compilation = DefaultCompilerPipeline(
        magic_state_consumption=policy.magic_state_consumption,
    ).compile(circuit, specification, latency)
    return circuit, specification, latency, policy, bindings, compilation


def test_serialized_compilation_result_lowers_to_the_same_execution_plan() -> None:
    circuit, specification, latency, policy, bindings, compilation = (
        _lowering_fixture()
    )
    restored = LogicalCompilationResult.from_json(compilation.to_json())

    direct = lower_compilation_result(
        circuit,
        specification,
        latency,
        policy,
        restored,
        resource_protocol_bindings=bindings,
    )
    _, composed = compile_and_lower(
        circuit,
        specification,
        latency,
        policy,
        compiler_spec=compilation.compiler_spec,
        resource_protocol_bindings=bindings,
    )

    assert direct.to_dict() == composed.to_dict()
    assert direct.plan_hash == composed.plan_hash
    assert direct.provenance["compilation_hash"] == restored.compilation_hash


def test_compile_and_lower_returns_both_canonical_artifacts() -> None:
    circuit, specification, latency, policy, bindings, compilation = (
        _lowering_fixture()
    )

    class StaticPipeline:
        def compile(self, _circuit, _specification, _latency):
            return compilation

    returned_compilation, plan = compile_and_lower(
        circuit,
        specification,
        latency,
        policy,
        compiler_pipeline=StaticPipeline(),
        resource_protocol_bindings=bindings,
    )
    relowered = lower_compilation_result(
        circuit,
        specification,
        latency,
        policy,
        compilation,
        resource_protocol_bindings=bindings,
    )

    assert returned_compilation is compilation
    assert plan.to_dict() == relowered.to_dict()
    assert plan.provenance["compilation_hash"] == compilation.compilation_hash


def test_plan_lowering_uses_the_result_compiler_spec_as_authority() -> None:
    circuit, specification, latency, policy, bindings, compilation = (
        _lowering_fixture()
    )
    effective_compiler = replace(
        compilation.compiler_spec,
        mapping=BackendSpec(
            compilation.compiler_spec.mapping.backend,
            options={"receipt": "custom-pipeline"},
        ),
    )
    custom_result = replace(compilation, compiler_spec=effective_compiler)

    class StaticPipeline:
        def compile(self, _circuit, _specification, _latency):
            return custom_result

    _, plan = compile_and_lower(
        circuit,
        specification,
        latency,
        policy,
        compiler_spec=compilation.compiler_spec,
        compiler_pipeline=StaticPipeline(),
        resource_protocol_bindings=bindings,
    )

    assert (
        plan.provenance["compiler_spec_hash"]
        == effective_compiler.compiler_hash
    )
    assert plan.provenance["compilation_hash"] == custom_result.compilation_hash


def test_direct_lowering_rejects_a_stale_compilation_result() -> None:
    circuit, specification, latency, policy, bindings, compilation = (
        _lowering_fixture()
    )
    stale = replace(compilation, circuit_hash="stale-circuit")

    with pytest.raises(ValueError, match="source hashes do not match"):
        lower_compilation_result(
            circuit,
            specification,
            latency,
            policy,
            stale,
            resource_protocol_bindings=bindings,
        )
