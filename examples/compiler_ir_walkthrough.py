"""Print the three canonical offline artifacts used by compiler integration."""

from __future__ import annotations

import json
from pathlib import Path

from arqsim.evaluation import ExecutionPolicy, compile_and_lower
from arqsim.operation_profiles import (
    OperationLatencyProfile,
    effective_resource_protocol_bindings,
    resolve_resource_protocol_bindings,
    with_effective_arrivals,
)
from arqsim.program import load_ft_workload
from arqsim.specification import build_architecture_specification


SOURCE = Path(__file__).with_name("workloads") / "compiler_ir_toy.qasm"


def main() -> None:
    circuit = load_ft_workload(
        SOURCE,
        representation="gate",
        provenance={"example": "compiler-ir-toy"},
    )
    specification = build_architecture_specification(circuit, "1.1")

    requested_latency = OperationLatencyProfile()
    base_bindings = resolve_resource_protocol_bindings(
        specification,
        requested_latency,
    )
    protocol_bindings = effective_resource_protocol_bindings(
        requested_latency,
        base_bindings,
    )
    resolved_latency = with_effective_arrivals(
        requested_latency,
        protocol_bindings,
    )

    compilation, plan = compile_and_lower(
        circuit,
        specification,
        resolved_latency,
        ExecutionPolicy(),
        resource_protocol_bindings=protocol_bindings,
    )

    # The outer object is only a display wrapper. Each nested value is the
    # exact canonical document returned by that artifact's public codec.
    print(
        json.dumps(
            {
                "ft_circuit": circuit.to_dict(),
                "logical_compilation_result": compilation.to_dict(),
                "execution_plan": plan.to_dict(),
            },
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    main()
