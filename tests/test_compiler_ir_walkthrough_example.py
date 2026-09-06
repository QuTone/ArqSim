from __future__ import annotations

import json

from examples.compiler_ir_walkthrough import main


def test_compiler_ir_walkthrough_prints_canonical_offline_artifacts(
    capsys,
) -> None:
    main()
    document = json.loads(capsys.readouterr().out)

    circuit = document["ft_circuit"]
    compilation = document["logical_compilation_result"]
    plan = document["execution_plan"]

    assert circuit["schema_version"] == "arqsim.ft-workload.v2"
    assert [
        [operation["name"] for operation in layer["operations"]]
        for layer in circuit["layers"]
    ] == [["h", "x"], ["cx"], ["t"]]
    assert (
        compilation["schema_version"]
        == "arqsim.logical-compilation-result.v1"
    )
    assert len(compilation["compute_units"]) == 3
    assert compilation["compute_units"][-1]["route"]["dispatch_deferred"]
    assert plan["schema_version"] == "arqsim.execution-plan.v9"
    assert len(plan["program_dag"]["instructions"]) == 6
    assert len(plan["resource_dag"]["processes"]) == 2
