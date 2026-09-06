from __future__ import annotations

import json
from pathlib import Path

import pytest

from arqsim.api import (
    CANONICAL_FIDELITY_PRESET,
    EFFECTIVE_EVALUATION_CONFIG_SCHEMA_VERSION,
    EVALUATION_CONFIG_SCHEMA_VERSION,
    EVALUATION_REPORT_SCHEMA_VERSION,
    EffectiveEvaluationConfig,
    EvaluationConfig,
    EvaluationReport,
    load_evaluation_report_document,
    run_evaluation,
    validate_evaluation_report_document,
)
from arqsim.architecture import (
    LogicalLayoutGrid,
    LogicalLayoutRequest,
    SubmoduleKey,
    SubmoduleLayoutRequest,
)
from arqsim.cli import main as cli_main
from arqsim.evaluation import EvaluationPolicy
from arqsim.operation_profiles import ArrivalDistribution, OperationLatencyProfile
from arqsim.program import load_ft_workload
from arqsim.report_v1 import (
    EVALUATION_REPORT_SCHEMA_VERSION as EVALUATION_REPORT_V1_SCHEMA_VERSION,
    render_evaluation_report_v1,
)
from arqsim.schema import normalize_json, semantic_hash
from tests.generate_public_report_fixture import (
    FIXTURE as PUBLIC_REPORT_FIXTURE,
    V1_FIXTURE as PUBLIC_REPORT_V1_FIXTURE,
    render_current_report,
    render_v1_compatibility_report,
)


FIXTURE = Path(__file__).parent / "fixtures/small_original.qasm"


def _latency_profile() -> OperationLatencyProfile:
    return OperationLatencyProfile(
        magic_state_arrival=ArrivalDistribution.from_rate(
            1_000.0, kind="deterministic"
        ),
        bell_pair_arrival=ArrivalDistribution.from_rate(
            1_000.0, kind="deterministic"
        ),
    )


def _config(*, trace_level: str = "full") -> EvaluationConfig:
    return EvaluationConfig(
        profile_id="1.2",
        run_label="public-api-test",
        latency_profile=_latency_profile(),
        execution_policy=EvaluationPolicy(trace_level=trace_level, seed=0),
        fidelity_profile=CANONICAL_FIDELITY_PRESET,
    )


def _v1_payload(report) -> dict:
    return normalize_json(render_evaluation_report_v1(report))


def test_public_api_runs_fixture_deterministically() -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    config = EvaluationConfig.from_json(_config().to_json())

    first = run_evaluation(circuit, config)
    second = run_evaluation(circuit, config)
    payload = first.to_dict()
    runtime_diagnostics = first.evaluation.diagnostic_dict()

    assert payload == second.to_dict()
    assert payload["schema_version"] == EVALUATION_REPORT_SCHEMA_VERSION
    assert payload["request"]["config"] == config.to_dict()
    assert payload["request"]["workload"] == circuit.to_dict()
    assert payload["resolved_inputs"]["architecture"] == (
        first.specification.to_dict()
    )
    assert payload["resolved_inputs"]["latency_profile"] == (
        first.latency_profile.to_dict()
    )
    assert payload["artifacts"]["logical_compilation"] == (
        first.logical_compilation.to_dict()
    )
    assert payload["artifacts"]["execution_plan"] == (
        first.execution_plan.to_dict()
    )
    assert payload["artifacts"]["execution_trace"]["trace_hash"] == (
        first.evaluation.trace_hash
    )
    assert (
        payload["artifacts"]["execution_trace"]["schema_version"]
        == "arqsim.execution-trace.v4"
    )
    assert len(payload["artifacts"]["execution_trace"]["transitions"]) == len(
        first.evaluation.trace.transitions
    )
    assert "schema_version" not in runtime_diagnostics
    assert "events" not in runtime_diagnostics
    assert runtime_diagnostics["trace_hash"] == first.evaluation.trace_hash
    assert first.execution_plan.plan_hash == first.evaluation.plan_hash
    assert all(first.evaluation.invariant_checks.values())
    assert all(first.footprint.checks.values())

    summary = payload["results"]["summary"]
    assert summary["total_latency_s"] > 0
    assert summary["total_physical_qubits"] > 0
    assert 0 <= summary["success_probability"] <= 1
    assert summary["fidelity_complete_coverage"] is True
    assert first.fidelity is not None
    assert first.fidelity.unprofiled_logical_operation_counts == {}

    analysis = payload["results"]["analysis"]
    assert sum(analysis["exclusive_time_s"].values()) == pytest.approx(
        summary["total_latency_s"]
    )
    assert sum(analysis["physical_space_qubits"].values()) == pytest.approx(
        summary["total_physical_qubits"]
    )
    assert analysis["engine_utilization"]
    assert analysis["buffer_occupancy"]
    assert json.loads(first.to_json())["report_hash"] == first.report_hash
    # The redundant effective-config receipt exists only for explicit v1
    # compatibility; native v2 records independent resolved authorities.
    effective = first.effective_configuration.to_dict()
    assert (
        effective["schema_version"]
        == EFFECTIVE_EVALUATION_CONFIG_SCHEMA_VERSION
    )
    assert effective["workflow_id"] == config.run_label
    assert effective["compiler_spec"] == first.compiler_spec.to_dict()
    assert effective["footprint_model"] == (
        first.footprint_model.to_dict()
    )
    assert effective["runtime_components"] == normalize_json(
        first.execution_plan.runtime_components
    )
    assert (
        EffectiveEvaluationConfig.from_dict(effective).effective_config_hash
        == effective["effective_config_hash"]
    )
    assert normalize_report(load_evaluation_report_document(first.to_json())) == payload


def test_runtime_caches_preserve_frozen_small_fixture_contract_hashes() -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    report = run_evaluation(circuit, _config())

    assert report.report_hash == (
        "92fd2bf749e267ff394904dd16445149f03d2292cfa339f18f4b0db790bbe676"
    )
    assert report.execution_plan.plan_hash == (
        "37be942c172183ee4762e695d1d54135c5c856665aae76915506509bb4faf732"
    )
    assert report.evaluation.trace_hash == (
        "e38fb6cd743fb0a3736dcf0d37a82594a1bb7fce8cc22b1be87e2ea46d342b81"
    )
    assert validate_evaluation_report_document(report.to_dict())


def test_evaluation_report_v2_class_codec_preserves_canonical_artifacts() -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    native = run_evaluation(circuit, _config())

    loaded = EvaluationReport.from_json(native.to_json())

    assert isinstance(loaded, EvaluationReport)
    assert loaded.to_dict() == native.to_dict()
    assert loaded.report_hash == native.report_hash
    assert loaded.logical_compilation == native.logical_compilation
    assert loaded.execution_plan == native.execution_plan
    assert loaded.execution_trace == native.evaluation.trace
    assert loaded.discrete_time_log == native.evaluation.discrete_time_log
    # V2 does not serialize operational diagnostic caches, so parsing must not
    # invent a partial EvaluationResult and present it as runtime authority.
    assert loaded.evaluation is None
    with pytest.raises(TypeError, match="cannot be converted"):
        loaded.effective_configuration
    with pytest.raises(TypeError, match="native evaluation artifacts"):
        render_evaluation_report_v1(loaded)


def test_non_full_trace_marks_causal_analysis_unavailable() -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    config = EvaluationConfig(
        profile_id="1.2",
        latency_profile=_latency_profile(),
        execution_policy=EvaluationPolicy(trace_level="summary", seed=0),
    )
    payload = run_evaluation(circuit, config).to_dict()

    assert payload["results"]["analysis"]["exclusive_time_s"] is None
    assert payload["results"]["analysis"]["buffer_occupancy"] is None
    assert set(payload["results"]["analysis"]["unavailable"]) == {
        "buffer_occupancy",
        "exclusive_time_s",
    }
    assert 0 <= payload["results"]["summary"]["success_probability"] <= 1
    assert payload["results"]["summary"]["fidelity_complete_coverage"] is True


def test_layout_policy_override_reaches_resolved_architecture() -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    config = EvaluationConfig(
        profile_id="1.2",
        architecture_overrides={
            "protocols.magic_state.buffer_capacity": 2,
        },
        latency_profile=_latency_profile(),
    )
    report = run_evaluation(circuit, config)

    magic_buffers = [
        submodule.capacity
        for node in report.specification.nodes
        for module in node.modules
        for submodule in module.submodules
        if submodule.type == "buffer" and submodule.payload == "magic_state"
    ]
    assert magic_buffers == [2, 2]
    assert report.to_dict()["request"]["config"]["layout_policy_overrides"] == {
        "protocols.magic_state.buffer_capacity": 2,
    }


def test_cli_evaluate_emits_public_report_contract(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.json"
    status = cli_main(
        [
            "evaluate",
            str(FIXTURE),
            "--representation",
            "clifford_t",
            "--architecture",
            "1.2",
            "--arrival",
            "deterministic",
            "--observation-level",
            "summary",
            "--run-seed",
            "0",
            "--output",
            str(output),
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert status == 0
    assert payload["schema_version"] == EVALUATION_REPORT_SCHEMA_VERSION
    assert payload == json.loads(output.read_text(encoding="utf-8"))
    assert payload["artifacts"]["execution_plan"]["plan_hash"] == (
        payload["artifacts"]["execution_trace"]["plan_hash"]
    )


def test_evaluation_config_rejects_unknown_schema() -> None:
    with pytest.raises(ValueError, match="Unsupported evaluation-config schema"):
        EvaluationConfig.from_dict({"schema_version": "unknown"})


def normalize_report(value) -> dict:
    """Round-trip a frozen JSON-shaped validator result for assertions."""

    return normalize_json(value)


def _resign_report(payload: dict) -> None:
    unsigned = {key: value for key, value in payload.items() if key != "report_hash"}
    payload["report_hash"] = semantic_hash(unsigned)


def test_sparse_config_materializes_exact_facade_defaults() -> None:
    sparse = EvaluationConfig.from_dict(
        {"schema_version": EVALUATION_CONFIG_SCHEMA_VERSION}
    )
    direct = EvaluationConfig()

    assert sparse.to_dict() == direct.to_dict()
    assert sparse.config_hash == direct.config_hash
    assert sparse.evaluation_policy.trace_level == "full"
    assert sparse.latency_profile.magic_state_arrival is None
    assert sparse.latency_profile.bell_pair_arrival is None
    assert sparse.latency_profile.derived_arrival_kind is None
    assert sparse.to_dict()["latency_profile"]["magic_state_arrival"] is None
    assert sparse.to_dict()["latency_profile"]["bell_pair_arrival"] is None
    assert OperationLatencyProfile.from_dict({}).to_dict() == (
        OperationLatencyProfile().to_dict()
    )


def test_latency_profile_distinguishes_auto_and_explicit_arrival_requests() -> None:
    explicit_magic = ArrivalDistribution.from_rate(
        250.0, kind="exponential"
    ).to_dict()
    explicit_bell = ArrivalDistribution.from_rate(
        125.0, kind="geometric"
    ).to_dict()

    auto = OperationLatencyProfile.from_dict(
        {
            "magic_state_arrival": None,
            "bell_pair_arrival": None,
            "derived_arrival_kind": " Exponential ",
        }
    )
    assert auto.magic_state_arrival is None
    assert auto.bell_pair_arrival is None
    assert auto.derived_arrival_kind == "exponential"

    legacy_explicit = OperationLatencyProfile.from_dict(
        {
            "magic_state_arrival": explicit_magic,
            "bell_pair_arrival": explicit_bell,
        }
    )
    assert legacy_explicit.magic_state_arrival is not None
    assert legacy_explicit.magic_state_arrival.to_dict() == explicit_magic
    assert legacy_explicit.bell_pair_arrival is not None
    assert legacy_explicit.bell_pair_arrival.to_dict() == explicit_bell
    assert legacy_explicit.derived_arrival_kind is None
    assert "derived_arrival_kind" not in legacy_explicit.to_dict()

    with pytest.raises(ValueError, match="derived_arrival_kind"):
        OperationLatencyProfile(derived_arrival_kind="trace")


def test_cli_arrival_flags_preserve_auto_request_semantics(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured_configs: list[EvaluationConfig] = []

    class _StubReport:
        @staticmethod
        def to_json() -> str:
            return "{}\n"

    def _capture_config(_circuit, config: EvaluationConfig) -> _StubReport:
        captured_configs.append(config)
        return _StubReport()

    monkeypatch.setattr("arqsim.api.run_evaluation", _capture_config)

    cases = (
        ((), None, None, None),
        (("--arrival", "exponential"), None, None, "exponential"),
        (("--magic-rate", "250"), 0.004, None, None),
        (
            ("--bell-rate", "125", "--arrival", "geometric"),
            None,
            0.008,
            "geometric",
        ),
    )
    for flags, magic_interval, bell_interval, derived_kind in cases:
        status = cli_main(
            [
                "evaluate",
                str(FIXTURE),
                "--representation",
                "clifford_t",
                *flags,
            ]
        )
        capsys.readouterr()

        assert status == 0
        profile = captured_configs.pop().latency_profile
        assert profile.derived_arrival_kind == derived_kind
        if magic_interval is None:
            assert profile.magic_state_arrival is None
        else:
            assert profile.magic_state_arrival is not None
            assert profile.magic_state_arrival.mean_interval_s == pytest.approx(
                magic_interval
            )
            assert profile.magic_state_arrival.kind == "deterministic"
        if bell_interval is None:
            assert profile.bell_pair_arrival is None
        else:
            assert profile.bell_pair_arrival is not None
            assert profile.bell_pair_arrival.mean_interval_s == pytest.approx(
                bell_interval
            )
            assert profile.bell_pair_arrival.kind == derived_kind


def test_config_json_round_trip_is_exact_and_hash_stable() -> None:
    original = _config()
    restored = EvaluationConfig.from_json(original.to_json())

    assert restored.to_dict() == original.to_dict()
    assert restored.config_hash == original.config_hash


def test_logical_layout_is_optional_and_custom_requests_round_trip_exactly() -> None:
    assert "logical_layout" not in EvaluationConfig().to_dict()

    layout = LogicalLayoutRequest(
        submodules={
            SubmoduleKey("na_node", "na_msf", "magic_state_output_buffer"): (
                SubmoduleLayoutRequest(logical_origin=(12, -2))
            ),
            SubmoduleKey("na_node", "na_compute", "compute_region"): (
                SubmoduleLayoutRequest(
                    logical_origin=(2, 3),
                    slots={"slot_1": (4, 0), "slot_0": (0, 0)},
                )
            ),
        }
    )
    config = EvaluationConfig(profile_id="1.1", logical_layout=layout)
    payload = config.to_dict()

    assert payload["logical_layout"] == {
        "submodules": {
            "na_node/na_compute/compute_region": {
                "logical_origin": [2, 3],
                "slots": {"slot_0": [0, 0], "slot_1": [4, 0]},
            },
            "na_node/na_msf/magic_state_output_buffer": {
                "logical_origin": [12, -2],
            },
        }
    }
    restored = EvaluationConfig.from_json(config.to_json())
    assert restored.to_dict() == payload
    assert restored.config_hash == config.config_hash
    assert restored.logical_layout is not None
    assert restored.logical_layout.logical_layout_hash == layout.logical_layout_hash

    with pytest.raises(TypeError):
        layout.submodules[SubmoduleKey("other", "compute", "region")] = next(
            iter(layout.submodules.values())
        )  # type: ignore[index]
    compute_key = SubmoduleKey("na_node", "na_compute", "compute_region")
    with pytest.raises(TypeError):
        layout.submodules[compute_key] = SubmoduleLayoutRequest(
            logical_origin=(0, 0)
        )  # type: ignore[index]
    compute = layout.submodules[compute_key]
    assert compute.slots is not None
    with pytest.raises(TypeError):
        compute.slots["slot_0"] = (99, 99)  # type: ignore[index]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"submodules": {}}, "cannot be empty"),
        (
            {
                "submodules": {
                    "bad/node/compute/region": {"logical_origin": [0, 0]}
                }
            },
            "canonical owner/module/submodule",
        ),
        (
            {
                "submodules": {
                    "unqualified": {"logical_origin": [0, 0]}
                }
            },
            "canonical owner/module/submodule",
        ),
        (
            {
                "submodules": {"node/compute/region": {}}
            },
            "must specify",
        ),
        (
            {
                "submodules": {
                    "node/compute/region": {"logical_origin": [0]}
                }
            },
            "exactly two",
        ),
        (
            {
                "submodules": {
                    "node/compute/region": {"logical_origin": [True, 0]}
                }
            },
            "must be integers",
        ),
        (
            {
                "submodules": {
                    "node/compute/region": {
                        "slots": {"slot_0": [0.0, 0]}
                    }
                }
            },
            "must be integers",
        ),
        (
            {
                "submodules": {
                    "node/compute/region": {
                        "grid": {"rows": True, "columns": 1}
                    }
                }
            },
            "positive integer",
        ),
        (
            {
                "submodules": {
                    "node/compute/region": {
                        "grid": {"rows": 0, "columns": 1}
                    }
                }
            },
            "positive integer",
        ),
        (
            {
                "submodules": {
                    "node/compute/region": {
                        "grid": {"rows": 1, "columns": 1},
                        "slots": {"slot_0": [0, 0]},
                    }
                }
            },
            "mutually exclusive",
        ),
        (
            {
                "submodules": {
                    "node/compute/region": {"slots": {}}
                }
            },
            "cannot be empty",
        ),
        (
            {
                "submodules": {
                    "node/compute/region": {
                        "logical_origin": [0, 0],
                        "physical_offset": [0, 0],
                    }
                }
            },
            "Unknown Submodule layout request fields",
        ),
        (
            {
                "submodules": {
                    "node/compute/region": {
                        "logical_origin": [1_000_000_001, 0]
                    }
                }
            },
            "coordinates must be within",
        ),
    ],
)
def test_logical_layout_request_rejects_ambiguous_or_noninteger_geometry(
    payload: dict,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        LogicalLayoutRequest.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "selection"),
    [
        (
            "compiler_spec",
            {"preset": "canonical", "explicit": {"ignored": True}},
        ),
        (
            "fidelity_profile",
            {"preset": CANONICAL_FIDELITY_PRESET, "extra": True},
        ),
        (
            "footprint_model",
            {"preset": "protocol_aware_reference_v1", "extra": True},
        ),
    ],
)
def test_config_rejects_ambiguous_or_extra_selection_fields(
    field: str, selection: dict
) -> None:
    payload = EvaluationConfig().to_dict()
    payload[field] = selection
    with pytest.raises(ValueError):
        EvaluationConfig.from_dict(payload)


def test_config_rejects_unknown_fields_wrong_types_and_nonfinite_json() -> None:
    with pytest.raises(ValueError, match="Unknown evaluation-config fields"):
        EvaluationConfig.from_dict(
            {
                "schema_version": EVALUATION_CONFIG_SCHEMA_VERSION,
                "architectuer": "1.2",
            }
        )
    with pytest.raises(TypeError, match="latency_profile"):
        EvaluationConfig(latency_profile={})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="finite JSON"):
        EvaluationConfig(architecture_overrides={"bad": float("nan")})
    with pytest.raises(ValueError, match="finite JSON"):
        OperationLatencyProfile(provenance={"bad": float("nan")})


def test_policy_integer_fields_reject_bool_and_float_aliases() -> None:
    for kwargs in (
        {"seed": True},
        {"seed": 1.5},
        {"max_events": True},
        {"max_events": 1.5},
        {"selected_layers": (True,)},
        {"selected_layers": (1.5,)},
    ):
        with pytest.raises(TypeError):
            EvaluationPolicy(**kwargs)


@pytest.mark.parametrize(
    "unsupported",
    ("optimistic_legacy_overlap", "conservative_non_overlap"),
)
def test_policy_rejects_unimplemented_store_load_controls(
    unsupported: str,
) -> None:
    with pytest.raises(ValueError, match="Only dependency_aware_overlap"):
        EvaluationPolicy(store_load_policy=unsupported)


def test_arrival_distribution_rejects_json_type_aliases_and_nonfinite_values() -> None:
    with pytest.raises(TypeError, match="repeat_trace"):
        ArrivalDistribution(repeat_trace=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="initial_delay_samples"):
        ArrivalDistribution(initial_delay_samples=1.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Success probability"):
        ArrivalDistribution(success_probability=float("nan"))
    with pytest.raises(TypeError, match="Arrival rate"):
        ArrivalDistribution.from_rate(True)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="magic_state_arrival"):
        OperationLatencyProfile(magic_state_arrival={})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "mutation",
    [
        {"evaluation_policy": {"sead": 999}},
        {"latency_profile": {"magic_state_arival": {}}},
    ],
)
def test_config_rejects_unknown_nested_public_fields(mutation: dict) -> None:
    payload = EvaluationConfig().to_dict()
    for field, value in mutation.items():
        payload[field].update(value)
    with pytest.raises(ValueError, match="Unknown"):
        EvaluationConfig.from_dict(payload)


def test_public_config_is_deeply_immutable_and_hash_stable() -> None:
    source = {"neutral_atom": 1e-3, "superconducting": 1e-6}
    profile = OperationLatencyProfile(gate_duration_s=source)
    config = EvaluationConfig(latency_profile=profile)
    original_hash = config.config_hash
    source["neutral_atom"] = 7.0

    assert config.latency_profile.gate_duration_s["neutral_atom"] == 1e-3
    with pytest.raises(TypeError):
        config.latency_profile.gate_duration_s["neutral_atom"] = 7.0  # type: ignore[index]
    assert config.config_hash == original_hash


def test_report_artifacts_are_deeply_immutable_and_hash_stable() -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    report = run_evaluation(circuit, _config())
    before = report.to_dict()
    before_hash = report.report_hash

    qec_parameters = next(
        submodule.qec.parameters
        for node in report.specification.nodes
        for module in node.modules
        for submodule in module.submodules
        if submodule.qec is not None
    )
    with pytest.raises(TypeError):
        qec_parameters["distance"] = 99  # type: ignore[index]

    assert report.to_dict() == before
    assert report.report_hash == before_hash
    assert validate_evaluation_report_document(before)


def test_report_validator_rejects_tampering_even_with_rehashed_envelope() -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    payload = run_evaluation(circuit, _config()).to_dict()
    payload["artifacts"]["execution_trace"]["seed"] = 9
    _resign_report(payload)

    with pytest.raises(ValueError):
        validate_evaluation_report_document(payload)


def test_report_v1_is_output_only_and_has_no_public_input_codec() -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    payload = _v1_payload(run_evaluation(circuit, _config()))

    with pytest.raises(ValueError, match="Report-v2 document"):
        validate_evaluation_report_document(payload)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("summary", "total_physical_qubits"),
        ("analysis", "engine_utilization"),
    ],
)
def test_report_validator_recomputes_public_metrics(
    section: str, field: str
) -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    payload = run_evaluation(circuit, _config()).to_dict()
    if section == "summary":
        payload["results"][section][field] += 1
    else:
        engine = next(iter(payload["results"][section][field]))
        payload["results"][section][field][engine] /= 2
    _resign_report(payload)

    with pytest.raises(ValueError, match=section):
        validate_evaluation_report_document(payload)


def test_report_validator_rejects_re_signed_incomplete_or_failed_run() -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    original = run_evaluation(circuit, _config()).to_dict()
    mutations = []

    incomplete = normalize_json(original)
    incomplete["results"]["summary"]["completed_program_instructions"] -= 1
    mutations.append(incomplete)

    failed = normalize_json(original)
    failed["results"]["summary"]["invariant_checks"][
        "all_program_instructions_completed"
    ] = False
    mutations.append(failed)

    for payload in mutations:
        unsigned = {
            key: value for key, value in payload.items() if key != "report_hash"
        }
        payload["report_hash"] = semantic_hash(unsigned)
        with pytest.raises(ValueError):
            validate_evaluation_report_document(payload)


def test_report_validator_rejects_full_trace_downgrade_and_log_extensions() -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    original = run_evaluation(circuit, _config()).to_dict()

    downgraded = normalize_json(original)
    downgraded["results"]["observations"]["discrete_time_log"] = []
    downgraded["results"]["analysis"]["exclusive_time_s"] = None
    downgraded["results"]["analysis"]["buffer_occupancy"] = None
    reason = (
        "The selected trace level does not retain the discrete-time log "
        "required for causal attribution."
    )
    downgraded["results"]["analysis"]["unavailable"].update(
        {"exclusive_time_s": reason, "buffer_occupancy": reason}
    )
    _resign_report(downgraded)
    with pytest.raises(ValueError):
        validate_evaluation_report_document(downgraded)

    extended = normalize_json(original)
    extended["results"]["observations"]["discrete_time_log"][0][
        "unknown"
    ] = True
    _resign_report(extended)
    with pytest.raises(ValueError):
        validate_evaluation_report_document(extended)

    forged_stall = normalize_json(original)
    waiting = next(
        entry["frontier_after"]["waiting"]
        for entry in forged_stall["results"]["observations"][
            "discrete_time_log"
        ]
        if entry["frontier_after"]["waiting"]
    )
    waiting[0]["resource_blockers"][0] = "forged:stall:attribution"
    _resign_report(forged_stall)
    with pytest.raises(ValueError):
        validate_evaluation_report_document(forged_stall)


def test_report_v2_omits_runtime_diagnostic_caches() -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    payload = run_evaluation(circuit, _config()).to_dict()

    assert "evaluation" not in payload
    assert set(payload["results"]) == {
        "observations",
        "footprint",
        "fidelity",
        "analysis",
        "summary",
    }
    assert validate_evaluation_report_document(payload)


def test_report_validator_recomputes_execution_identity() -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    payload = run_evaluation(circuit, _config()).to_dict()
    payload["artifacts"]["execution_trace"]["plan_hash"] = "forged"
    _resign_report(payload)

    with pytest.raises(ValueError):
        validate_evaluation_report_document(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [("profile_id", "2.3"), ("workflow_id", "forged")],
)
def test_report_validator_checks_requested_effective_selection(
    field: str, value: str
) -> None:
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    payload = run_evaluation(circuit, _config()).to_dict()
    payload["request"]["config"][field] = value
    _resign_report(payload)

    with pytest.raises(ValueError):
        validate_evaluation_report_document(payload)


def test_evaluation_policy_v1_rejects_partial_trace_modes() -> None:
    with pytest.raises(ValueError, match="summary or full"):
        EvaluationPolicy(trace_level="layer")
    with pytest.raises(ValueError, match="selected_layers"):
        EvaluationPolicy(trace_level="full", selected_layers=(1,))


def test_cli_config_file_matches_python_api_exactly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = _config(trace_level="summary")
    config_path = tmp_path / "config.json"
    config_path.write_text(config.to_json(), encoding="utf-8")
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    expected = run_evaluation(circuit, config).to_dict()

    status = cli_main(
        [
            "evaluate",
            str(FIXTURE),
            "--representation",
            "clifford_t",
            "--config",
            str(config_path),
        ]
    )
    captured = capsys.readouterr()

    assert status == 0
    assert json.loads(captured.out) == expected


def test_cli_custom_logical_layout_matches_python_api_exactly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    layout = LogicalLayoutRequest(
        submodules={
            SubmoduleKey("sc_node", "sc_compute", "compute_region"): (
                SubmoduleLayoutRequest(
                    logical_origin=(-4, 6),
                    grid=LogicalLayoutGrid(rows=2, columns=2),
                )
            )
        }
    )
    payload = _config(trace_level="summary").to_dict()
    payload["logical_layout"] = layout.to_dict()
    config = EvaluationConfig.from_dict(payload)
    config_path = tmp_path / "logical-layout-config.json"
    config_path.write_text(config.to_json(), encoding="utf-8")
    circuit = load_ft_workload(FIXTURE, "clifford_t")
    expected = run_evaluation(circuit, config).to_dict()

    status = cli_main(
        [
            "evaluate",
            str(FIXTURE),
            "--representation",
            "clifford_t",
            "--config",
            str(config_path),
        ]
    )
    captured = capsys.readouterr()

    assert status == 0
    assert json.loads(captured.out) == expected


def test_cli_config_file_rejects_configuration_flag_conflicts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(EvaluationConfig().to_json(), encoding="utf-8")

    status = cli_main(
        [
            "evaluate",
            str(FIXTURE),
            "--representation",
            "clifford_t",
            "--config",
            str(config_path),
            "--seed",
            "1",
        ]
    )
    captured = capsys.readouterr()

    assert status == 2
    assert "--config cannot be combined" in captured.err


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ([], "QASM input requires --representation"),
        (
            ["--representation", "clifford_t", "--set", "not-an-assignment"],
            "Overrides require DOTTED_KEY=VALUE",
        ),
        (
            [
                "--representation",
                "clifford_t",
                "--set",
                "quantiles.compute=0.5",
                "--set",
                "quantiles.compute=0.75",
            ],
            "Override was specified twice",
        ),
    ],
)
def test_cli_validation_errors_use_the_versioned_json_contract(
    arguments: list[str],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status = cli_main(["evaluate", str(FIXTURE), *arguments])
    captured = capsys.readouterr()
    error_document = json.loads(captured.err)

    assert status == 2
    assert captured.out == ""
    assert error_document == {
        "schema_version": "arqsim.error.v1",
        "error": {
            "code": "evaluation_failed",
            "message": error_document["error"]["message"],
            "details": {},
        },
    }
    assert message in error_document["error"]["message"]


def test_package_root_exports_only_stable_common_surface() -> None:
    import arqsim
    import arqsim.api as public_api
    import arqsim.program as program

    expected = [
        "EvaluationConfig",
        "EvaluationReport",
        "FTCircuit",
        "run_evaluation",
    ]
    forbidden = {
        "ArchitectureState",
        "ExecutionPlan",
        "ProgramDAG",
        "ResourceDAG",
        "build_execution_plan",
        "evaluate",
    }

    assert arqsim.__all__ == expected
    assert arqsim.__version__ == "0.2.0"
    assert "__version__" not in arqsim.__all__
    assert forbidden.isdisjoint(arqsim.__all__)
    assert all(not hasattr(arqsim, name) for name in forbidden)
    assert arqsim.EvaluationConfig is public_api.EvaluationConfig
    assert arqsim.EvaluationReport is public_api.EvaluationReport
    assert arqsim.FTCircuit is program.FTCircuit
    assert arqsim.run_evaluation is public_api.run_evaluation


@pytest.mark.parametrize(
    ("legacy_name", "module_name"),
    [
        ("ArchitectureProfile", "arqsim.architecture"),
        ("ArchitectureSpecification", "arqsim.architecture"),
        ("ArrivalDistribution", "arqsim.operation_profiles"),
        ("BackendSpec", "arqsim.compiler"),
        ("CANONICAL_FIDELITY_PRESET", "arqsim.api"),
        ("DEFAULT_FOOTPRINT_PRESET", "arqsim.api"),
        ("EFFECTIVE_EVALUATION_CONFIG_SCHEMA_VERSION", "arqsim.api"),
        ("EVALUATION_CONFIG_SCHEMA_VERSION", "arqsim.api"),
        ("EVALUATION_REPORT_SCHEMA_VERSION", "arqsim.api"),
        ("EffectiveEvaluationConfig", "arqsim.api"),
        ("EvaluationAnalysis", "arqsim.evaluation"),
        ("EvaluationPolicy", "arqsim.evaluation"),
        ("FidelityProfile", "arqsim.operation_profiles"),
        ("LogicalCompilerSpec", "arqsim.compiler"),
        ("LogicalLayer", "arqsim.program"),
        ("LogicalLayoutRequest", "arqsim.architecture"),
        ("LogicalOperation", "arqsim.program"),
        ("OperationLatencyProfile", "arqsim.operation_profiles"),
        ("PhysicalFootprintModel", "arqsim.evaluation"),
        ("WORKLOAD_SCHEMA_VERSION", "arqsim.program"),
        ("WorkloadParseError", "arqsim.program"),
        ("build_architecture_specification", "arqsim.specification"),
        ("get_architecture_profile", "arqsim.architecture"),
        ("list_architecture_profiles", "arqsim.architecture"),
        ("load_evaluation_report_document", "arqsim.api"),
        ("load_ft_workload", "arqsim.program"),
        ("make_layers", "arqsim.program"),
        ("validate_evaluation_report_document", "arqsim.api"),
        ("workload_stats", "arqsim.program"),
    ],
)
def test_removed_root_names_are_identity_preserving_deprecation_aliases(
    legacy_name: str,
    module_name: str,
) -> None:
    import importlib
    import arqsim

    owner = importlib.import_module(module_name)
    with pytest.warns(DeprecationWarning, match=rf"arqsim\.{legacy_name} is deprecated"):
        legacy = getattr(arqsim, legacy_name)

    assert legacy is getattr(owner, legacy_name)
    assert legacy_name not in arqsim.__all__


def test_public_report_v2_fixture_is_current_and_self_validating() -> None:
    stored = PUBLIC_REPORT_FIXTURE.read_text(encoding="utf-8")

    document = load_evaluation_report_document(stored)

    assert document["schema_version"] == EVALUATION_REPORT_SCHEMA_VERSION
    assert stored == render_current_report()


def test_public_report_v1_fixture_remains_frozen_and_self_validating() -> None:
    stored = PUBLIC_REPORT_V1_FIXTURE.read_text(encoding="utf-8")

    document = json.loads(stored)

    assert document["schema_version"] == EVALUATION_REPORT_V1_SCHEMA_VERSION
    assert document["report_hash"] == semantic_hash(
        {key: value for key, value in document.items() if key != "report_hash"}
    )
    assert stored == render_v1_compatibility_report()
