"""Strict, self-contained Report-v2 rendering and validation.

Report v2 embeds the canonical stage artifacts needed to validate a stored
evaluation without rerunning the logical compiler, scheduler, outcome model,
or Event Engine.  The only executable reconstruction performed here is the
deterministic ``LogicalCompilationResult -> ExecutionPlan`` lowering; all
other checked records are derived from the embedded Plan and Trace.

This module deliberately does not import :mod:`heteqsys.api` at import time.
``EvaluationConfig`` remains owned by that facade, so the codec resolves it
locally while parsing.  This keeps the Report-v2 codec usable by the facade
without creating an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import TYPE_CHECKING, Any, Mapping

from heteqsys._run_artifacts import EvaluationRunArtifacts
from heteqsys.architecture.specification import ArchitectureSpecification
from heteqsys.compiler.config import canonical_compiler_spec
from heteqsys.compiler.output import LogicalCompilationResult
from heteqsys.evaluation.analysis import (
    EvaluationAnalysis,
    analyze_evaluation,
    qubit_exposure,
)
from heteqsys.evaluation.footprint import (
    PhysicalFootprintEstimate,
    PhysicalFootprintModel,
    estimate_physical_footprint,
)
from heteqsys.evaluation.fidelity_estimator import FidelityEstimate
from heteqsys.evaluation.lowering import lower_compilation_result
from heteqsys.evaluation.plan import ExecutionPlan
from heteqsys.evaluation.result import (
    EvaluationResult,
    ExecutionPlane,
    ExecutionTrace,
    ExecutionTransitionKind,
    validate_discrete_time_log_document,
    replay_execution_trace,
)
from heteqsys.operation_profiles import (
    FidelityProfile,
    OperationLatencyProfile,
    ResolvedResourceProtocolBindings,
    effective_resource_protocol_bindings,
    resolve_resource_protocol_bindings,
    with_effective_arrivals,
)
from heteqsys.operation_profiles.canonical_fidelity import (
    canonical_fidelity_profile,
)
from heteqsys.program import FTCircuit
from heteqsys.schema import (
    deep_freeze_json,
    hash_and_freeze_json_document,
    normalize_json,
    semantic_hash,
)
from heteqsys.specification import build_architecture_specification

if TYPE_CHECKING:
    from heteqsys.api import EvaluationConfig


EVALUATION_REPORT_V2_SCHEMA_VERSION = "arqsim.evaluation-report.v2"

_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "workflow_id",
        "request",
        "resolved_inputs",
        "artifacts",
        "results",
        "report_hash",
    }
)
_REQUEST_FIELDS = frozenset(
    {"config", "workload", "magic_sizing_workload"}
)
_RESOLVED_INPUT_FIELDS = frozenset(
    {
        "architecture",
        "latency_profile",
        "footprint_model",
        "fidelity_profile",
    }
)
_ARTIFACT_FIELDS = frozenset(
    {"logical_compilation", "execution_plan", "execution_trace"}
)
_RESULT_FIELDS = frozenset(
    {"observations", "footprint", "fidelity", "analysis", "summary"}
)
_OBSERVATION_FIELDS = frozenset({"discrete_time_log"})
_SUMMARY_FIELDS = frozenset(
    {
        "total_latency_s",
        "total_physical_qubits",
        "success_probability",
        "fidelity_complete_coverage",
        "completed_program_instructions",
        "event_count",
        "invariant_checks",
    }
)
_INVARIANT_FIELDS = frozenset(
    {
        "active_reservations_account_for_engine_usage",
        "active_reservations_account_for_output_slots",
        "active_reservations_match_running_events",
        "all_program_instructions_completed",
        "buffer_capacities_respected",
        "buffer_slots_consistent",
        "engine_capacities_respected",
        "nonnegative_latency",
        "pending_output_slots_consistent",
        "pending_outputs_nonnegative",
        "qubit_exposure_conflict_free",
        "qubit_exposure_time_conserved",
        "token_locations_consistent",
        "transaction_versions_consistent",
    }
)


class ReportV2ValidationError(ValueError):
    """Raised when a Report-v2 document fails closed validation."""


def _require_exact_json_wire(value: Any, *, path: str) -> None:
    """Reject Python aliases and non-finite numbers at the codec boundary."""

    value_type = type(value)
    if value_type is dict:
        for key, child in value.items():
            if type(key) is not str:
                raise ReportV2ValidationError(
                    f"{path} JSON object keys must be strings"
                )
            _require_exact_json_wire(child, path=f"{path}.{key}")
        return
    if value_type is list:
        for index, child in enumerate(value):
            _require_exact_json_wire(child, path=f"{path}[{index}]")
        return
    if value is None or value_type in {str, bool, int}:
        return
    if value_type is float:
        if not math.isfinite(value):
            raise ReportV2ValidationError(
                f"{path} must contain only finite JSON numbers"
            )
        return
    if isinstance(value, Mapping):
        raise ReportV2ValidationError(
            f"{path} JSON objects must be plain dictionaries"
        )
    if isinstance(value, (tuple, list)):
        raise ReportV2ValidationError(
            f"{path} JSON arrays must be plain lists"
        )
    raise ReportV2ValidationError(
        f"{path} contains a non-JSON value of type {value_type.__name__}"
    )


def _require_fields(
    value: Any,
    *,
    fields: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if type(value) is not dict:
        raise ReportV2ValidationError(f"{label} must be a JSON object")
    missing = fields - set(value)
    unknown = set(value) - fields
    if missing:
        raise ReportV2ValidationError(
            f"{label} is missing fields: {sorted(missing)}"
        )
    if unknown:
        raise ReportV2ValidationError(
            f"{label} has unknown fields: {sorted(unknown)}"
        )
    return value


def _exact_json_equal(left: Any, right: Any) -> bool:
    """Compare canonical JSON without Python's bool/int aliasing."""

    if type(left) is dict and type(right) is dict:
        return set(left) == set(right) and all(
            _exact_json_equal(left[key], right[key]) for key in left
        )
    if type(left) is list and type(right) is list:
        return len(left) == len(right) and all(
            _exact_json_equal(a, b) for a, b in zip(left, right)
        )
    return type(left) is type(right) and left == right


def _require_canonical(
    actual: Any,
    expected: Any,
    *,
    label: str,
) -> None:
    normalized_expected = normalize_json(expected)
    if not _exact_json_equal(actual, normalized_expected):
        raise ReportV2ValidationError(
            f"{label} is not its exact canonical or recomputed record"
        )


def _resource_protocol_bindings(plan: ExecutionPlan) -> ResolvedResourceProtocolBindings:
    record = plan.provenance.get("resource_protocol_bindings")
    recorded_hash = plan.provenance.get("resource_protocol_bindings_hash")
    if not isinstance(record, Mapping) or type(recorded_hash) is not str:
        raise ReportV2ValidationError(
            "ExecutionPlan is missing its typed resource-protocol authority"
        )
    parsed = ResolvedResourceProtocolBindings.from_dict(normalize_json(record))
    _require_canonical(
        normalize_json(record),
        parsed.to_dict(),
        label="ExecutionPlan resource-protocol bindings",
    )
    if recorded_hash != parsed.bindings_hash:
        raise ReportV2ValidationError(
            "ExecutionPlan resource-protocol bindings hash mismatch"
        )
    return parsed


def _completed_program_instructions(trace: ExecutionTrace) -> int:
    return len(
        {
            int(transition.instruction_id)
            for transition in trace.transitions
            if transition.kind == ExecutionTransitionKind.COMPLETION
            and transition.plane == ExecutionPlane.PROGRAM
            and transition.continuation is not None
            and transition.continuation.kind == "complete_source"
        }
    )


@dataclass(frozen=True)
class _TraceAnalysisView:
    """Small read-only adapter for trace-derived analysis services."""

    trace: ExecutionTrace
    discrete_time_log: tuple[Mapping[str, Any], ...]

    @property
    def total_latency_s(self) -> float:
        return self.trace.total_latency_s

    @property
    def plan_hash(self) -> str:
        return self.trace.plan_hash

    @property
    def trace_hash(self) -> str:
        return self.trace.trace_hash


def _replayed_evaluation_result(
    trace: ExecutionTrace,
    plan: ExecutionPlan,
    discrete_time_log: tuple[Mapping[str, Any], ...],
    invariant_checks: Mapping[str, bool],
) -> EvaluationResult:
    """Build the narrow typed result needed by existing pure analyzers.

    Report v2 intentionally omits the Engine's redundant terminal caches and
    operational counters.  ``EvaluationResult`` validates its canonical trace
    and terminal projections, while the fields that are not consumed by
    Report-v2 derivation are represented by empty diagnostic records.  No
    omitted value is treated as execution authority.
    """

    terminal = trace.terminal_state
    return EvaluationResult(
        trace=trace,
        completed_program_instructions=_completed_program_instructions(trace),
        final_buffers=terminal.buffers,
        final_pending_incoming=terminal.pending_incoming,
        final_locations=terminal.locations,
        program_state_blocked_s=0.0,
        producer_blocked_s={},
        buffer_peaks={},
        metrics={},
        invariant_checks=invariant_checks,
        discrete_time_log=discrete_time_log,
        runtime_components=plan.runtime_components,
    )


def _recompute_invariant_checks(
    trace: ExecutionTrace,
    plan: ExecutionPlan,
    view: _TraceAnalysisView,
) -> Mapping[str, bool]:
    """Recompute the Engine's terminal invariant view from Plan + Trace."""

    terminal = trace.terminal_state
    capacities = {item.id: item.capacity for item in plan.buffers}
    engine_capacities = {item.id: item.capacity for item in plan.engines}
    buffer_slots = {item.id: frozenset(item.slots) for item in plan.buffers}

    active_output_slots: dict[str, set[str]] = {
        name: set() for name in capacities
    }
    active_engine_claims = {name: 0 for name in engine_capacities}
    for dispatch in trace.terminal_inflight:
        for name, slots in dispatch.produced_slots.items():
            active_output_slots[name].update(slots)
        for name, amount in dispatch.engines.items():
            active_engine_claims[name] += int(amount)

    exposure = qubit_exposure(view)
    next_event_id = 1 + max(
        (
            transition.event_id
            for transition in trace.transitions
            if transition.kind == ExecutionTransitionKind.DISPATCH
        ),
        default=-1,
    )
    checks = {
        "all_program_instructions_completed": (
            _completed_program_instructions(trace)
            == len(plan.program_dag.instructions)
        ),
        "buffer_capacities_respected": all(
            len(terminal.buffers[name]) + terminal.pending_incoming[name]
            <= capacity
            for name, capacity in capacities.items()
        ),
        "pending_outputs_nonnegative": all(
            amount >= 0 for amount in terminal.pending_incoming.values()
        ),
        "pending_output_slots_consistent": all(
            len(terminal.reserved_output_slots[name])
            == terminal.pending_incoming[name]
            and not (
                set(terminal.reserved_output_slots[name])
                & {
                    terminal.token_slots[token]
                    for token in terminal.buffers[name]
                }
            )
            for name in capacities
        ),
        "engine_capacities_respected": all(
            0 <= terminal.engine_usage[name] <= capacity
            for name, capacity in engine_capacities.items()
        ),
        "active_reservations_match_running_events": (
            set(terminal.active_reservation_ids)
            == {item.reservation_id for item in trace.terminal_inflight}
        ),
        "active_reservations_account_for_output_slots": all(
            active_output_slots[name]
            == set(terminal.reserved_output_slots[name])
            for name in capacities
        ),
        "active_reservations_account_for_engine_usage": all(
            active_engine_claims[name] == terminal.engine_usage[name]
            for name in engine_capacities
        ),
        "transaction_versions_consistent": (
            terminal.next_reservation_id == next_event_id
            and terminal.state_version == len(trace.transitions)
        ),
        "token_locations_consistent": all(
            terminal.locations.get(token) == name
            for name, tokens in terminal.buffers.items()
            for token in tokens
        ),
        "buffer_slots_consistent": all(
            len(
                {
                    terminal.token_slots[token]
                    for token in terminal.buffers[name]
                }
            )
            == len(terminal.buffers[name])
            and all(
                terminal.token_slots[token] in buffer_slots[name]
                for token in terminal.buffers[name]
            )
            for name in capacities
        ),
        "nonnegative_latency": trace.total_latency_s >= 0,
        "qubit_exposure_conflict_free": exposure.conflict_free,
        "qubit_exposure_time_conserved": exposure.time_conserved,
    }
    assert set(checks) == _INVARIANT_FIELDS
    return deep_freeze_json(checks)


def _summary(
    *,
    trace: ExecutionTrace,
    plan: ExecutionPlan,
    footprint: PhysicalFootprintEstimate,
    fidelity: FidelityEstimate | None,
    view: _TraceAnalysisView,
) -> dict[str, Any]:
    return {
        "total_latency_s": trace.total_latency_s,
        "total_physical_qubits": footprint.total_physical_qubits,
        "success_probability": (
            fidelity.success_probability if fidelity is not None else None
        ),
        "fidelity_complete_coverage": (
            fidelity.complete_coverage if fidelity is not None else None
        ),
        "completed_program_instructions": _completed_program_instructions(trace),
        "event_count": len(trace.events),
        "invariant_checks": normalize_json(
            _recompute_invariant_checks(trace, plan, view)
        ),
    }


@dataclass(frozen=True)
class ReportV2Document:
    """Strictly parsed typed view over one immutable Report-v2 document."""

    workflow_id: str
    config: EvaluationConfig
    workload: FTCircuit
    magic_sizing_workload: FTCircuit | None
    architecture: ArchitectureSpecification
    latency_profile: OperationLatencyProfile
    footprint_model: PhysicalFootprintModel
    fidelity_profile: FidelityProfile | None
    logical_compilation: LogicalCompilationResult
    execution_plan: ExecutionPlan
    execution_trace: ExecutionTrace
    discrete_time_log: tuple[Mapping[str, Any], ...]
    footprint: PhysicalFootprintEstimate
    fidelity: FidelityEstimate | None
    analysis: EvaluationAnalysis
    summary: Mapping[str, Any]
    _document: Mapping[str, Any]

    @property
    def report_hash(self) -> str:
        return str(self._document["report_hash"])

    def to_dict(self) -> dict[str, Any]:
        return normalize_json(self._document)

    def to_json(self, *, indent: int | None = 2) -> str:
        return (
            json.dumps(
                self.to_dict(),
                indent=indent,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        )


class ReportV2Codec:
    """Strict parser and validator for the canonical Report-v2 wire shape."""

    @classmethod
    def from_dict(cls, document: Mapping[str, Any]) -> ReportV2Document:
        if type(document) is not dict:
            raise ReportV2ValidationError(
                "Report-v2 document must be a plain dictionary"
            )
        _require_exact_json_wire(document, path="report-v2")
        payload = _require_fields(
            document,
            fields=_TOP_LEVEL_FIELDS,
            label="Report-v2 document",
        )
        if payload["schema_version"] != EVALUATION_REPORT_V2_SCHEMA_VERSION:
            raise ReportV2ValidationError(
                "Unsupported Report-v2 schema: "
                f"{payload['schema_version']!r}"
            )
        workflow_id = payload["workflow_id"]
        if type(workflow_id) is not str or not workflow_id.strip():
            raise ReportV2ValidationError(
                "Report-v2 workflow_id must be a non-empty string"
            )

        request = _require_fields(
            payload["request"], fields=_REQUEST_FIELDS, label="Report-v2 request"
        )
        resolved = _require_fields(
            payload["resolved_inputs"],
            fields=_RESOLVED_INPUT_FIELDS,
            label="Report-v2 resolved_inputs",
        )
        artifacts = _require_fields(
            payload["artifacts"],
            fields=_ARTIFACT_FIELDS,
            label="Report-v2 artifacts",
        )
        results = _require_fields(
            payload["results"], fields=_RESULT_FIELDS, label="Report-v2 results"
        )
        observations = _require_fields(
            results["observations"],
            fields=_OBSERVATION_FIELDS,
            label="Report-v2 observations",
        )
        summary_record = _require_fields(
            results["summary"],
            fields=_SUMMARY_FIELDS,
            label="Report-v2 summary",
        )

        # EvaluationConfig owns the request contract.  Resolve it lazily to
        # avoid an import cycle when api.py imports this module.
        from heteqsys.api import EvaluationConfig

        config_record = request["config"]
        config = EvaluationConfig.from_dict(config_record)
        _require_canonical(
            config_record, config.to_dict(), label="Report-v2 requested config"
        )
        workload = FTCircuit.from_dict(request["workload"])
        _require_canonical(
            request["workload"], workload.to_dict(), label="Report-v2 workload"
        )
        magic_record = request["magic_sizing_workload"]
        magic_sizing_workload = (
            FTCircuit.from_dict(magic_record) if magic_record is not None else None
        )
        if magic_sizing_workload is not None:
            _require_canonical(
                magic_record,
                magic_sizing_workload.to_dict(),
                label="Report-v2 magic-sizing workload",
            )
        if config.workflow_id is not None and config.workflow_id != workflow_id:
            raise ReportV2ValidationError(
                "Requested and Report-v2 workflow IDs disagree"
            )

        architecture = ArchitectureSpecification.from_dict(
            resolved["architecture"]
        )
        _require_canonical(
            resolved["architecture"],
            architecture.to_dict(),
            label="Report-v2 architecture",
        )
        latency_profile = OperationLatencyProfile.from_dict(
            resolved["latency_profile"]
        )
        _require_canonical(
            resolved["latency_profile"],
            latency_profile.to_dict(),
            label="Report-v2 latency profile",
        )
        footprint_model = PhysicalFootprintModel.from_dict(
            resolved["footprint_model"]
        )
        _require_canonical(
            resolved["footprint_model"],
            footprint_model.to_dict(),
            label="Report-v2 footprint model",
        )
        fidelity_profile = (
            FidelityProfile.from_dict(resolved["fidelity_profile"])
            if resolved["fidelity_profile"] is not None
            else None
        )
        if fidelity_profile is not None:
            _require_canonical(
                resolved["fidelity_profile"],
                fidelity_profile.to_dict(),
                label="Report-v2 fidelity profile",
            )

        expected_architecture = build_architecture_specification(
            workload,
            config.profile_id,
            magic_sizing_circuit=magic_sizing_workload,
            policy_overrides=config.layout_policy_overrides,
            logical_layout=config.logical_layout,
        )
        _require_canonical(
            resolved["architecture"],
            expected_architecture.to_dict(),
            label="Report-v2 resolved architecture",
        )

        logical_compilation = LogicalCompilationResult.from_dict(
            artifacts["logical_compilation"]
        )
        execution_plan = ExecutionPlan.from_dict(artifacts["execution_plan"])
        execution_trace = ExecutionTrace.from_dict(artifacts["execution_trace"])

        # Request/resolved-input/artifact cross-authority checks.
        if logical_compilation.circuit_hash != workload.semantic_hash:
            raise ReportV2ValidationError(
                "Logical compilation and workload hashes disagree"
            )
        if execution_plan.circuit_hash != workload.semantic_hash:
            raise ReportV2ValidationError("ExecutionPlan and workload hashes disagree")
        if logical_compilation.architecture_hash != architecture.architecture_hash:
            raise ReportV2ValidationError(
                "Logical compilation and architecture hashes disagree"
            )
        if execution_plan.architecture_hash != architecture.architecture_hash:
            raise ReportV2ValidationError(
                "ExecutionPlan and architecture hashes disagree"
            )
        if logical_compilation.latency_profile_hash != latency_profile.binding_hash:
            raise ReportV2ValidationError(
                "Logical compilation and latency-profile hashes disagree"
            )
        if execution_plan.latency_profile_hash != latency_profile.profile_hash:
            raise ReportV2ValidationError(
                "ExecutionPlan and latency-profile hashes disagree"
            )
        if execution_trace.plan_hash != execution_plan.plan_hash:
            raise ReportV2ValidationError(
                "ExecutionTrace and ExecutionPlan hashes disagree"
            )
        if execution_trace.seed != execution_plan.policy.seed:
            raise ReportV2ValidationError(
                "ExecutionTrace seed and ExecutionPlan policy disagree"
            )
        if execution_plan.policy.to_dict() != config.evaluation_policy.to_dict():
            raise ReportV2ValidationError(
                "Requested evaluation policy and ExecutionPlan disagree"
            )
        requested_compiler = config.compiler_spec or canonical_compiler_spec(
            architecture
        )
        if (
            requested_compiler.compiler_hash
            != logical_compilation.compiler_spec.compiler_hash
        ):
            raise ReportV2ValidationError(
                "Requested compiler and logical compilation disagree"
            )
        if (
            config.runtime_components.manifest_hash
            != execution_plan.runtime_components.get("manifest_hash")
        ):
            raise ReportV2ValidationError(
                "Requested runtime components and ExecutionPlan disagree"
            )

        bindings = _resource_protocol_bindings(execution_plan)
        expected_bindings = effective_resource_protocol_bindings(
            config.latency_profile,
            resolve_resource_protocol_bindings(
                architecture, config.latency_profile
            ),
        )
        if expected_bindings.to_dict() != bindings.to_dict():
            raise ReportV2ValidationError(
                "Resolved resource-protocol bindings do not match the request"
            )
        expected_latency = with_effective_arrivals(
            config.latency_profile, expected_bindings
        )
        _require_canonical(
            latency_profile.to_dict(),
            expected_latency.to_dict(),
            label="Report-v2 effective latency profile",
        )

        expected_footprint_model = (
            config.footprint_model
            if config.footprint_model is not None
            else PhysicalFootprintModel.reference_v1()
        )
        if footprint_model.to_dict() != expected_footprint_model.to_dict():
            raise ReportV2ValidationError(
                "Resolved footprint model does not match the request"
            )

        selected_fidelity = config.fidelity_profile
        if selected_fidelity is None:
            expected_fidelity_profile = None
        elif isinstance(selected_fidelity, FidelityProfile):
            expected_fidelity_profile = selected_fidelity
        elif selected_fidelity == "canonical_reference_v1":
            expected_fidelity_profile = canonical_fidelity_profile(
                architecture, latency_profile, bindings
            )
        else:  # EvaluationConfig rejects this, retained as fail-closed defense.
            raise ReportV2ValidationError("Unknown fidelity-profile selection")
        if (
            fidelity_profile.to_dict() if fidelity_profile is not None else None
        ) != (
            expected_fidelity_profile.to_dict()
            if expected_fidelity_profile is not None
            else None
        ):
            raise ReportV2ValidationError(
                "Resolved fidelity profile does not match the request"
            )

        # Reproduce deterministic compilation lowering without rerunning the
        # compiler.  An explicit delivery-channel override is the only Plan
        # input that cannot be inferred from architecture inventory.
        lowering_kwargs: dict[str, Any] = {}
        if execution_plan.provenance.get(
            "resource_delivery_concurrency_source"
        ) == "explicit_runtime_override":
            lowering_kwargs["resource_delivery_channels"] = execution_plan.provenance.get(
                "resource_delivery_channels"
            )
        reproduced_plan = lower_compilation_result(
            workload,
            architecture,
            latency_profile,
            execution_plan.policy,
            logical_compilation,
            runtime_components=execution_plan.runtime_components,
            resource_protocol_bindings=bindings,
            **lowering_kwargs,
        )
        _require_canonical(
            artifacts["execution_plan"],
            reproduced_plan.to_dict(),
            label="Report-v2 deterministically lowered ExecutionPlan",
        )

        replay_execution_trace(execution_trace, execution_plan)
        discrete_time_log = validate_discrete_time_log_document(
            observations["discrete_time_log"],
            execution_trace,
            execution_plan,
            execution_plan.policy.trace_level,
        )
        view = _TraceAnalysisView(execution_trace, discrete_time_log)
        invariant_checks = _recompute_invariant_checks(
            execution_trace, execution_plan, view
        )
        evaluation = _replayed_evaluation_result(
            execution_trace,
            execution_plan,
            discrete_time_log,
            invariant_checks,
        )

        footprint = estimate_physical_footprint(architecture, footprint_model)
        _require_canonical(
            results["footprint"],
            footprint.to_dict(),
            label="Report-v2 footprint result",
        )
        analysis, fidelity = analyze_evaluation(
            evaluation,
            execution_plan,
            architecture,
            footprint,
            fidelity_profile,
        )
        expected_fidelity_record = (
            fidelity.to_dict() if fidelity is not None else None
        )
        _require_canonical(
            results["fidelity"],
            expected_fidelity_record,
            label="Report-v2 fidelity result",
        )
        _require_canonical(
            results["analysis"],
            analysis.to_dict(),
            label="Report-v2 analysis result",
        )
        expected_summary = _summary(
            trace=execution_trace,
            plan=execution_plan,
            footprint=footprint,
            fidelity=fidelity,
            view=view,
        )
        _require_canonical(
            summary_record,
            expected_summary,
            label="Report-v2 summary",
        )

        expected_report_hash = payload["report_hash"]
        if type(expected_report_hash) is not str:
            raise ReportV2ValidationError("Report-v2 report_hash must be a string")
        unsigned = {
            key: value for key, value in payload.items() if key != "report_hash"
        }
        if expected_report_hash != semantic_hash(unsigned):
            raise ReportV2ValidationError(
                "Report-v2 hash does not match its content"
            )

        frozen_document = deep_freeze_json(payload)
        return ReportV2Document(
            workflow_id=workflow_id,
            config=config,
            workload=workload,
            magic_sizing_workload=magic_sizing_workload,
            architecture=architecture,
            latency_profile=latency_profile,
            footprint_model=footprint_model,
            fidelity_profile=fidelity_profile,
            logical_compilation=logical_compilation,
            execution_plan=execution_plan,
            execution_trace=execution_trace,
            discrete_time_log=discrete_time_log,
            footprint=footprint,
            fidelity=fidelity,
            analysis=analysis,
            summary=deep_freeze_json(expected_summary),
            _document=frozen_document,
        )

    @classmethod
    def from_json(cls, text: str) -> ReportV2Document:
        if type(text) is not str:
            raise TypeError("Report-v2 JSON document must be a string")

        def reject_constant(value: str) -> None:
            raise ReportV2ValidationError(
                f"Non-finite JSON constant is not allowed in Report v2: {value}"
            )

        def reject_duplicate_keys(
            pairs: list[tuple[str, Any]],
        ) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ReportV2ValidationError(
                        f"Duplicate JSON object key in Report v2: {key!r}"
                    )
                result[key] = value
            return result

        try:
            decoded = json.loads(
                text,
                parse_constant=reject_constant,
                object_pairs_hook=reject_duplicate_keys,
            )
        except json.JSONDecodeError as exc:
            raise ReportV2ValidationError(
                f"Invalid Report-v2 JSON at line {exc.lineno}, column {exc.colno}"
            ) from exc
        if type(decoded) is not dict:
            raise ReportV2ValidationError(
                "Report-v2 JSON document must contain an object"
            )
        return cls.from_dict(decoded)


class ReportV2Renderer:
    """Render typed run artifacts into the strict seven-field v2 document."""

    def render(
        self,
        artifacts: EvaluationRunArtifacts,
        *,
        requested_config: Mapping[str, Any],
        workflow_id: str,
    ) -> Mapping[str, Any]:
        if not isinstance(artifacts, EvaluationRunArtifacts):
            raise TypeError("artifacts must be EvaluationRunArtifacts")
        if not isinstance(requested_config, Mapping):
            raise TypeError("requested_config must be a mapping")
        if type(workflow_id) is not str or not workflow_id.strip():
            raise ValueError("workflow_id must be a non-empty string")

        evaluation = artifacts.evaluation
        payload = {
            "schema_version": EVALUATION_REPORT_V2_SCHEMA_VERSION,
            "workflow_id": workflow_id,
            "request": {
                "config": normalize_json(requested_config),
                "workload": artifacts.circuit.to_dict(),
                "magic_sizing_workload": (
                    artifacts.magic_sizing_circuit.to_dict()
                    if artifacts.magic_sizing_circuit is not None
                    else None
                ),
            },
            "resolved_inputs": {
                "architecture": artifacts.specification.to_dict(),
                "latency_profile": artifacts.latency_profile.to_dict(),
                "footprint_model": artifacts.footprint_model.to_dict(),
                "fidelity_profile": (
                    artifacts.fidelity_profile.to_dict()
                    if artifacts.fidelity_profile is not None
                    else None
                ),
            },
            "artifacts": {
                "logical_compilation": artifacts.logical_compilation.to_dict(),
                "execution_plan": artifacts.execution_plan.to_dict(),
                "execution_trace": evaluation.trace.to_dict(),
            },
            "results": {
                "observations": {
                    "discrete_time_log": normalize_json(
                        evaluation.discrete_time_log
                    )
                },
                "footprint": artifacts.footprint.to_dict(),
                "fidelity": (
                    artifacts.fidelity.to_dict()
                    if artifacts.fidelity is not None
                    else None
                ),
                "analysis": artifacts.analysis.to_dict(),
                "summary": _summary(
                    trace=evaluation.trace,
                    plan=artifacts.execution_plan,
                    footprint=artifacts.footprint,
                    fidelity=artifacts.fidelity,
                    view=_TraceAnalysisView(
                        evaluation.trace, evaluation.discrete_time_log
                    ),
                ),
            },
        }
        frozen = hash_and_freeze_json_document(
            payload,
            hash_key="report_hash",
            label="Report-v2 document",
        )
        # Validate even trusted renderer input.  This catches orchestration
        # drift (for example a stale derived result) before it becomes public.
        parsed = ReportV2Codec.from_dict(normalize_json(frozen))
        return deep_freeze_json(parsed.to_dict())


def validate_report_v2_document(
    document: Mapping[str, Any],
) -> ReportV2Document:
    """Parse and fully validate one in-memory Report-v2 document."""

    return ReportV2Codec.from_dict(document)


def load_report_v2_document(text: str) -> ReportV2Document:
    """Parse and fully validate one strict Report-v2 JSON document."""

    return ReportV2Codec.from_json(text)


__all__ = [
    "EVALUATION_REPORT_V2_SCHEMA_VERSION",
    "ReportV2Codec",
    "ReportV2Document",
    "ReportV2Renderer",
    "ReportV2ValidationError",
    "load_report_v2_document",
    "validate_report_v2_document",
]
