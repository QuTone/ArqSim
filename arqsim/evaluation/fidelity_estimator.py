"""End-to-end fidelity aggregation over one realized execution trace."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from arqsim.architecture.isa import (
    ArchitectureInstruction,
    ArchitectureOpcode,
    ProgramWorkTemplate,
)
from arqsim.operation_profiles import (
    FidelityProfile,
    ResolvedResourceProtocolBindings,
)
from arqsim.schema import deep_freeze_json, normalize_json

from .components import json_type_strict_equal
from .plan import ExecutionPlan
from .resource_dag import ResourceProcess
from .result import EvaluationResult, ExecutionPlane, ExecutionTransition
from .resource_fidelity import resource_token_ledger


def _operation_multiplicity(
    transition: ExecutionTransition,
    *,
    model_kind: str,
    program_by_id: Mapping[int, ArchitectureInstruction],
    resource_by_id: Mapping[str, ResourceProcess],
) -> int:
    """Resolve item count from frozen work and realized token identities."""

    if transition.plane == ExecutionPlane.PROGRAM:
        source = program_by_id.get(transition.instruction_id)
        lineage = transition.program_lineage
        if source is None or lineage is None or (
            lineage.source_instruction_id != source.id
        ):
            raise ValueError("Program fidelity requires its typed Plan work context")
        operation: ArchitectureInstruction | ProgramWorkTemplate = source
        if lineage.parent_event_id is not None:
            templates = tuple(
                template
                for template in source.continuation_templates
                if template.key
                == (
                    tuple(member.key for member in lineage.recipe_members),
                    lineage.step,
                )
            )
            if len(templates) != 1:
                raise ValueError("Program fidelity requires one frozen work template")
            operation = templates[0]
        if operation.opcode != transition.opcode:
            raise ValueError(
                "Program fidelity opcode disagrees with its typed Plan work"
            )
        multiplicity = len(operation.qubits)
    elif transition.plane == ExecutionPlane.RESOURCE:
        process = resource_by_id.get(transition.process_id)
        if (
            process is None
            or model_kind != "independent_channels_per_teleported_item"
            or process.opcode != ArchitectureOpcode.TELEPORT_QUBITS
            or transition.opcode != process.opcode
            or not process.forwards
            or transition.forwards != process.forwards
        ):
            raise ValueError("Resource fidelity requires its typed Plan teleport flow")

        # Eager dispatch can transfer several instances' worth of payload.
        # Count only identity-preserving flows, excluding consumed ancillas
        # and any independently produced/discarded outputs of the same event.
        batch_sizes: set[int] = set()
        teleported_tokens: list[str] = []
        for source_buffer, destination_buffer in process.forwards.items():
            consumed = transition.consumed_tokens.get(source_buffer, ())
            produced = transition.produced_tokens.get(destination_buffer, ())
            batch_size, remainder = divmod(
                len(consumed), process.consumes[source_buffer]
            )
            if remainder or batch_size <= 0:
                raise ValueError("Teleport token quantity disagrees with its Plan claim")
            batch_sizes.add(batch_size)
            if len(consumed) != len(produced) or set(consumed) != set(produced):
                raise ValueError(
                    "Teleport completion must preserve forwarded token identity"
                )
            teleported_tokens.extend(consumed)
        if len(batch_sizes) != 1 or (
            process.dispatch_policy == "single" and batch_sizes != {1}
        ):
            raise ValueError(
                "Teleport token quantities disagree with the Plan batch policy"
            )
        if len(teleported_tokens) != len(set(teleported_tokens)):
            raise ValueError("Teleported item identities must be unique")
        multiplicity = len(teleported_tokens)
    else:
        raise ValueError(
            "Parameterized operation fidelity requires a typed execution plane"
        )

    if multiplicity <= 0:
        raise ValueError(
            "Parameterized operation fidelity requires a non-empty typed item payload"
        )
    return multiplicity


def _parameterized_operation_log_success(
    model: Mapping[str, Any],
    multiplicity: int,
) -> dict[str, float]:
    """Return one completed transition's named log-survival terms."""

    kind = str(model.get("kind", ""))
    if kind == "independent_per_logical_qubit":
        probability = float(model["failure_probability"])
        if not math.isfinite(probability) or not 0 <= probability < 1:
            raise ValueError(
                "Architecture-operation failure probability must be in [0, 1)"
            )
        return {"": multiplicity * math.log1p(-probability)}
    if kind == "independent_channels_per_teleported_item":
        channels = model.get("channels", {})
        if not isinstance(channels, Mapping) or not channels:
            raise ValueError("Teleport fidelity requires named failure channels")
        result = {}
        for raw_name, raw_probability in channels.items():
            probability = float(raw_probability)
            if not math.isfinite(probability) or not 0 <= probability < 1:
                raise ValueError(
                    "Teleport failure-channel probabilities must be in [0, 1)"
                )
            result[str(raw_name)] = multiplicity * math.log1p(-probability)
        return result
    raise ValueError(f"Unsupported architecture-operation fidelity model: {kind}")


def _ppm_weight(operation: Any) -> int:
    """Recover Pauli-product weight from one realized gate record."""

    if isinstance(operation, Mapping):
        for key in ("pauli_indices", "qubits", "support"):
            if key in operation:
                return len(operation[key])
        if "weight" in operation:
            return int(operation["weight"])
    if isinstance(operation, (list, tuple)):
        return len(operation)
    if operation is None:
        raise ValueError("Parameterized PPM fidelity requires operation support")
    return 1


def _parameterized_logical_failure_probability(
    model: Mapping[str, Any],
    operation: Any,
) -> float:
    kind = str(model.get("kind", ""))
    if kind != "unrotated_surface_ppm_parity":
        raise ValueError(f"Unsupported logical-operation fidelity model: {kind}")

    weight = _ppm_weight(operation)
    if weight <= 0:
        raise ValueError("PPM support must be non-empty")
    distance = float(model["distance"])
    if weight == 1:
        fit = model["weight_one_fit"]
        log10_hazard = (
            float(fit["log10_distance_coefficient"]) * distance
            + float(fit["log10_intercept"])
        )
    else:
        fit = model["multi_patch_fit"]
        effective_weight = 2 * math.ceil(weight / 2)
        log10_hazard = (
            float(fit["log10_distance_coefficient"]) * distance
            + float(fit["log10_effective_weight_coefficient"])
            * math.log10(effective_weight)
            + float(fit["log10_intercept"])
        )
    return -math.expm1(-(10.0**log10_hazard))


def _trace_has_resource_lifecycle(result: Any) -> bool:
    """Return whether a trace contains any plan-typed resource-buffer fact."""

    trace = result.trace
    states = [trace.initial_state]
    terminal_state = getattr(trace, "terminal_state", None)
    if terminal_state is not None:
        states.append(terminal_state)
    for state in states:
        if (
            getattr(state, "buffers", {})
            or getattr(state, "pending_incoming", {})
            or getattr(state, "reserved_output_slots", {})
            or getattr(state, "token_slots", {})
            or getattr(state, "token_sequence", {})
        ):
            return True
    for transition in (
        *trace.transitions,
        *getattr(trace, "terminal_inflight", ()),
    ):
        if (
            getattr(transition, "consumed_tokens", {})
            or getattr(transition, "consumed_slots", {})
            or getattr(transition, "produced_slots", {})
            or getattr(transition, "produced_tokens", {})
            or getattr(transition, "buffer_occupancy_after", {})
            or getattr(transition, "pending_incoming_after", {})
        ):
            return True
    return False


def _validate_resource_protocol_authority(
    plan: ExecutionPlan,
    profile: FidelityProfile,
) -> None:
    """Bind protocol-backed fidelity models to the Plan's signed receipt."""

    protocol_models = {
        kind: model
        for kind, model in profile.resource_state_models.items()
        if model.protocol_id is not None
    }
    if not protocol_models:
        return

    raw_receipt = plan.provenance.get("resource_protocol_bindings")
    recorded_hash = plan.provenance.get("resource_protocol_bindings_hash")
    if not isinstance(raw_receipt, Mapping) or type(recorded_hash) is not str:
        raise ValueError(
            "Protocol-bound resource fidelity requires the Plan's canonical "
            "resource_protocol_bindings receipt and separately recorded hash"
        )
    try:
        bindings = ResolvedResourceProtocolBindings.from_dict(raw_receipt)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "ExecutionPlan resource_protocol_bindings receipt is invalid"
        ) from exc
    if not json_type_strict_equal(raw_receipt, bindings.to_dict()):
        raise ValueError(
            "ExecutionPlan resource_protocol_bindings receipt is not the exact "
            "canonical typed record"
        )
    if recorded_hash != bindings.bindings_hash:
        raise ValueError(
            "ExecutionPlan resource_protocol_bindings_hash does not match its "
            "typed receipt"
        )

    for resource_kind, model in protocol_models.items():
        binding = bindings.get(resource_kind)
        if binding is None:
            raise ValueError(
                f"Protocol-bound fidelity model {resource_kind!r} has no selected "
                "Plan resource binding"
            )
        mismatches = {
            "resource_kind": (resource_kind, binding.resource_kind),
            "protocol_id": (model.protocol_id, binding.protocol_id),
            "protocol_profile_hash": (
                model.protocol_profile_hash,
                binding.protocol_profile_hash,
            ),
            "output_failure_probability": (
                model.output_failure_probability,
                binding.output_error_probability,
            ),
        }
        disagreements = {
            name: {"model": selected, "binding": authoritative}
            for name, (selected, authoritative) in mismatches.items()
            if selected != authoritative
        }
        if disagreements:
            raise ValueError(
                f"Resource fidelity model {resource_kind!r} disagrees with the "
                f"Plan protocol authority: {disagreements}"
            )


@dataclass(frozen=True)
class FidelityCoverageGaps:
    """Structured missing-model evidence for one fidelity estimate.

    The field names intentionally match :class:`FidelityEstimate` and the
    Report-v2 fidelity record.  This is a runtime validation view, not a new
    wire schema: partial estimates remain serializable and replayable.
    """

    unprofiled_operation_counts: Mapping[str, int]
    unprofiled_logical_operation_counts: Mapping[str, int]
    unprofiled_idle_exposure_s: Mapping[str, float]
    unprofiled_resource_output_counts: Mapping[str, int]
    unprofiled_resource_idle_exposure_s: Mapping[str, float]

    def __post_init__(self) -> None:
        count_fields = (
            "unprofiled_operation_counts",
            "unprofiled_logical_operation_counts",
            "unprofiled_resource_output_counts",
        )
        exposure_fields = (
            "unprofiled_idle_exposure_s",
            "unprofiled_resource_idle_exposure_s",
        )
        for name in (*count_fields, *exposure_fields):
            value = getattr(self, name)
            if not isinstance(value, Mapping):
                raise TypeError(f"{name} must be a mapping")
            if any(type(key) is not str or not key for key in value):
                raise TypeError(f"{name} keys must be non-empty plain strings")
        for name in count_fields:
            if any(
                type(value) is not int or value <= 0
                for value in getattr(self, name).values()
            ):
                raise ValueError(f"{name} values must be positive plain integers")
        for name in exposure_fields:
            if any(
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(float(value))
                or float(value) <= 0
                for value in getattr(self, name).values()
            ):
                raise ValueError(f"{name} values must be finite and positive")
        for name in (*count_fields, *exposure_fields):
            object.__setattr__(self, name, deep_freeze_json(getattr(self, name)))

    @property
    def is_empty(self) -> bool:
        """Return whether every modeled coverage dimension is complete."""

        return not any(
            (
                self.unprofiled_operation_counts,
                self.unprofiled_logical_operation_counts,
                self.unprofiled_idle_exposure_s,
                self.unprofiled_resource_output_counts,
                self.unprofiled_resource_idle_exposure_s,
            )
        )

    @property
    def nonempty_fields(self) -> tuple[str, ...]:
        """Return stable field names for the missing coverage dimensions."""

        return tuple(
            name
            for name in (
                "unprofiled_operation_counts",
                "unprofiled_logical_operation_counts",
                "unprofiled_idle_exposure_s",
                "unprofiled_resource_output_counts",
                "unprofiled_resource_idle_exposure_s",
            )
            if getattr(self, name)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "unprofiled_operation_counts": normalize_json(
                self.unprofiled_operation_counts
            ),
            "unprofiled_logical_operation_counts": normalize_json(
                self.unprofiled_logical_operation_counts
            ),
            "unprofiled_idle_exposure_s": normalize_json(
                self.unprofiled_idle_exposure_s
            ),
            "unprofiled_resource_output_counts": normalize_json(
                self.unprofiled_resource_output_counts
            ),
            "unprofiled_resource_idle_exposure_s": normalize_json(
                self.unprofiled_resource_idle_exposure_s
            ),
        }


class IncompleteFidelityCoverageError(ValueError):
    """Raised when a caller explicitly requires complete fidelity coverage."""

    code = "fidelity_coverage_incomplete"

    def __init__(self, gaps: FidelityCoverageGaps) -> None:
        if not isinstance(gaps, FidelityCoverageGaps):
            raise TypeError("gaps must be FidelityCoverageGaps")
        if gaps.is_empty:
            raise ValueError("Incomplete fidelity coverage requires at least one gap")
        self.gaps = gaps
        self.details = deep_freeze_json({"coverage_gaps": gaps.to_dict()})
        super().__init__(
            "Fidelity coverage is incomplete: " + ", ".join(gaps.nonempty_fields)
        )


@dataclass(frozen=True)
class FidelityEstimate:
    success_probability: float
    failure_probability: float
    log_success_by_operation: Mapping[str, float]
    log_success_by_logical_operation: Mapping[str, float]
    log_success_by_idle_location: Mapping[str, float]
    log_success_by_resource_output: Mapping[str, float]
    log_success_by_resource_idle_location: Mapping[str, float]
    architecture_operation_counts: Mapping[str, int]
    logical_operation_counts: Mapping[str, int]
    idle_cycles_by_location: Mapping[str, float]
    consumed_resource_counts: Mapping[str, int]
    resource_idle_cycles_by_location: Mapping[str, float]
    unprofiled_operation_counts: Mapping[str, int]
    unprofiled_logical_operation_counts: Mapping[str, int]
    unprofiled_idle_exposure_s: Mapping[str, float]
    unprofiled_resource_output_counts: Mapping[str, int]
    unprofiled_resource_idle_exposure_s: Mapping[str, float]
    complete_coverage: bool
    profile_hash: str

    def __post_init__(self) -> None:
        for name in (
            "log_success_by_operation",
            "log_success_by_logical_operation",
            "log_success_by_idle_location",
            "log_success_by_resource_output",
            "log_success_by_resource_idle_location",
            "architecture_operation_counts",
            "logical_operation_counts",
            "idle_cycles_by_location",
            "consumed_resource_counts",
            "resource_idle_cycles_by_location",
            "unprofiled_operation_counts",
            "unprofiled_logical_operation_counts",
            "unprofiled_idle_exposure_s",
            "unprofiled_resource_output_counts",
            "unprofiled_resource_idle_exposure_s",
        ):
            object.__setattr__(self, name, deep_freeze_json(getattr(self, name)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "success_probability": self.success_probability,
            "failure_probability": self.failure_probability,
            "log_success_by_operation": normalize_json(
                self.log_success_by_operation
            ),
            "log_success_by_logical_operation": normalize_json(
                self.log_success_by_logical_operation
            ),
            "log_success_by_idle_location": normalize_json(
                self.log_success_by_idle_location
            ),
            "log_success_by_resource_output": normalize_json(
                self.log_success_by_resource_output
            ),
            "log_success_by_resource_idle_location": normalize_json(
                self.log_success_by_resource_idle_location
            ),
            "architecture_operation_counts": normalize_json(
                self.architecture_operation_counts
            ),
            "logical_operation_counts": normalize_json(
                self.logical_operation_counts
            ),
            "idle_cycles_by_location": normalize_json(
                self.idle_cycles_by_location
            ),
            "consumed_resource_counts": normalize_json(
                self.consumed_resource_counts
            ),
            "resource_idle_cycles_by_location": normalize_json(
                self.resource_idle_cycles_by_location
            ),
            "unprofiled_operation_counts": normalize_json(
                self.unprofiled_operation_counts
            ),
            "unprofiled_logical_operation_counts": normalize_json(
                self.unprofiled_logical_operation_counts
            ),
            "unprofiled_idle_exposure_s": normalize_json(
                self.unprofiled_idle_exposure_s
            ),
            "unprofiled_resource_output_counts": normalize_json(
                self.unprofiled_resource_output_counts
            ),
            "unprofiled_resource_idle_exposure_s": normalize_json(
                self.unprofiled_resource_idle_exposure_s
            ),
            "complete_coverage": self.complete_coverage,
            "profile_hash": self.profile_hash,
        }


def fidelity_coverage_gaps(estimate: FidelityEstimate) -> FidelityCoverageGaps:
    """Return all missing-model evidence without rejecting a partial estimate."""

    if not isinstance(estimate, FidelityEstimate):
        raise TypeError("estimate must be a FidelityEstimate")
    return FidelityCoverageGaps(
        unprofiled_operation_counts=estimate.unprofiled_operation_counts,
        unprofiled_logical_operation_counts=(
            estimate.unprofiled_logical_operation_counts
        ),
        unprofiled_idle_exposure_s=estimate.unprofiled_idle_exposure_s,
        unprofiled_resource_output_counts=(
            estimate.unprofiled_resource_output_counts
        ),
        unprofiled_resource_idle_exposure_s=(
            estimate.unprofiled_resource_idle_exposure_s
        ),
    )


def require_complete_fidelity(estimate: FidelityEstimate) -> FidelityEstimate:
    """Return ``estimate`` or raise with its structured coverage gaps.

    This opt-in gate belongs at experiment/publication acceptance boundaries.
    The estimator itself deliberately continues to return partial estimates so
    callers can inspect exactly which calibration dimensions are missing.
    """

    gaps = fidelity_coverage_gaps(estimate)
    if estimate.complete_coverage != gaps.is_empty:
        raise ValueError(
            "FidelityEstimate.complete_coverage disagrees with its coverage gaps"
        )
    if not gaps.is_empty:
        raise IncompleteFidelityCoverageError(gaps)
    return estimate


def estimate_fidelity(
    result: EvaluationResult,
    profile: FidelityProfile,
    *,
    plan: ExecutionPlan | None = None,
) -> FidelityEstimate:
    """Combine operation failures and location-specific idle exposure.

    Log-success arithmetic avoids underflow and makes the breakdown additive.
    Resource-token quality and buffered residence are reconstructed from the
    causal trace plus its plan.  A plan is required whenever the trace contains
    resource lifecycle/buffer facts, resource-state models are selected, or a
    Resource-plane operation carries a fidelity channel. Parameterized
    architecture-operation channels also require a plan for their typed item
    payload. Diagnostics and buffer names never supply an item quantity,
    resource identity, protocol authority, or application relevance.
    """

    from .analysis import qubit_exposure

    if plan is None and (
        profile.resource_state_models or _trace_has_resource_lifecycle(result)
    ):
        raise ValueError(
            "Resource-token fidelity or trace lifecycle facts require the matching "
            "ExecutionPlan"
        )
    ledger = resource_token_ledger(result, plan) if plan is not None else None
    if plan is not None:
        _validate_resource_protocol_authority(plan, profile)
    settled_resource_event_ids = (
        set(ledger.settled_resource_event_ids) if ledger is not None else set()
    )
    program_by_id = (
        {operation.id: operation for operation in plan.program_dag.instructions}
        if plan is not None
        else {}
    )
    resource_by_id = (
        {process.id: process for process in plan.resource_dag.processes}
        if plan is not None
        else {}
    )

    operation_terms: dict[str, float] = {}
    architecture_operation_counts: dict[str, int] = {}
    unprofiled_operations: dict[str, int] = {}
    logical_counts: dict[str, int] = {}
    logical_instances: dict[str, list[Any]] = {}
    for transition in result.trace.transitions:
        transition_kind = str(
            getattr(transition.kind, "value", transition.kind)
        )
        if transition_kind != "completion":
            continue
        opcode = str(getattr(transition.opcode, "value", transition.opcode))
        plane = str(getattr(transition.plane, "value", transition.plane))
        gates = transition.metadata.get("gates", {})
        if plane == "program" and gates:
            lineage = getattr(transition, "program_lineage", None)
            if lineage is not None and not (
                lineage.parent_event_id is None
                or lineage.step == "correction"
            ):
                raise ValueError(
                    "Implementation-only Program work cannot claim logical "
                    "gate fidelity authority"
                )
        operation_model = profile.operation_failure_models.get(opcode)
        operation_probability = profile.operation_failure_probability.get(opcode)
        if plane == "resource":
            has_logical_payload = opcode == "EXECUTE_COMPUTE" and bool(gates)
            if ledger is None:
                if (
                    operation_model is not None
                    or operation_probability is not None
                    or has_logical_payload
                ):
                    raise ValueError(
                        "Resource-plane operation fidelity requires the matching "
                        "ExecutionPlan for application-relevance settlement"
                    )
                continue
            if int(transition.event_id) not in settled_resource_event_ids:
                continue
        architecture_operation_counts[opcode] = (
            architecture_operation_counts.get(opcode, 0) + 1
        )
        if operation_model is not None:
            if plan is None:
                raise ValueError(
                    "Parameterized operation fidelity requires the matching ExecutionPlan"
                )
            multiplicity = _operation_multiplicity(
                transition,
                model_kind=str(operation_model.get("kind", "")),
                program_by_id=program_by_id,
                resource_by_id=resource_by_id,
            )
            for channel, term in _parameterized_operation_log_success(
                operation_model, multiplicity
            ).items():
                if term == 0.0:
                    continue
                key = opcode if not channel else f"{opcode}:{channel}"
                operation_terms[key] = operation_terms.get(key, 0.0) + term
        else:
            if operation_probability is not None:
                term = math.log1p(-operation_probability)
                if term != 0.0:
                    operation_terms[opcode] = (
                        operation_terms.get(opcode, 0.0) + term
                    )
            else:
                unprofiled_operations[opcode] = (
                    unprofiled_operations.get(opcode, 0) + 1
                )
        if opcode != "EXECUTE_COMPUTE":
            continue
        for raw_name, operations in transition.metadata.get("gates", {}).items():
            name = str(raw_name).lower()
            if isinstance(operations, (list, tuple)):
                instances = list(operations)
                count = len(instances)
            else:
                count = int(operations)
                instances = [None] * count
            logical_counts[name] = logical_counts.get(name, 0) + count
            logical_instances.setdefault(name, []).extend(instances)

    logical_terms: dict[str, float] = {}
    unprofiled_logical: dict[str, int] = {}
    for name, count in sorted(logical_counts.items()):
        model = profile.logical_operation_failure_models.get(name)
        if model is not None:
            try:
                logical_terms[name] = sum(
                    math.log1p(
                        -_parameterized_logical_failure_probability(model, operation)
                    )
                    for operation in logical_instances[name]
                )
            except (KeyError, TypeError, ValueError):
                unprofiled_logical[name] = count
            continue
        probability = profile.logical_operation_failure_probability.get(name)
        if probability is None:
            unprofiled_logical[name] = count
            continue
        logical_terms[name] = count * math.log1p(-probability)

    idle = qubit_exposure(result).idle_by_location
    idle_terms: dict[str, float] = {}
    idle_cycles: dict[str, float] = {}
    unprofiled_idle: dict[str, float] = {}
    for raw_location, raw_exposure in idle.items():
        location = str(raw_location)
        exposure = float(raw_exposure)
        if location in profile.idle_failure_probability_per_cycle:
            cycles = exposure / profile.idle_cycle_time_s[location]
            idle_cycles[location] = cycles
            idle_terms[location] = cycles * math.log1p(
                -profile.idle_failure_probability_per_cycle[location]
            )
        elif location in profile.idle_failure_rate_per_s:
            idle_terms[location] = (
                -profile.idle_failure_rate_per_s[location] * exposure
            )
        elif exposure > 0:
            unprofiled_idle[location] = exposure

    resource_output_terms: dict[str, float] = {}
    resource_idle_terms: dict[str, float] = {}
    consumed_resource_counts: dict[str, int] = {}
    resource_idle_cycles: dict[str, float] = {}
    unprofiled_resource_output: dict[str, int] = {}
    unprofiled_resource_idle: dict[str, float] = {}
    if ledger is not None:
        for token in ledger.settled_tokens:
            kind = token.resource_kind
            consumed_resource_counts[kind] = (
                consumed_resource_counts.get(kind, 0) + 1
            )
            model = profile.resource_state_models.get(kind)
            if model is None:
                unprofiled_resource_output[kind] = (
                    unprofiled_resource_output.get(kind, 0) + 1
                )
                for interval in token.residence_intervals:
                    if interval.duration_s > 0:
                        unprofiled_resource_idle[interval.location] = (
                            unprofiled_resource_idle.get(interval.location, 0.0)
                            + interval.duration_s
                        )
                continue
            if (
                token.producer_protocol_id is not None
                and model.protocol_id is not None
                and token.producer_protocol_id != model.protocol_id
            ):
                raise ValueError(
                    f"Resource token {token.token_id} was produced by protocol "
                    f"{token.producer_protocol_id!r}, not {model.protocol_id!r}"
                )
            resource_output_terms[kind] = resource_output_terms.get(
                kind, 0.0
            ) + math.log1p(-model.output_failure_probability)
            for interval in token.residence_intervals:
                exposure = interval.duration_s
                if exposure <= 0:
                    continue
                idle_model = model.buffer_idle_models.get(interval.location)
                if idle_model is None:
                    unprofiled_resource_idle[interval.location] = (
                        unprofiled_resource_idle.get(interval.location, 0.0)
                        + exposure
                    )
                    continue
                cycles = (
                    exposure
                    / idle_model.idle_cycle_time_s
                    * idle_model.logical_qubits_per_token
                )
                resource_idle_cycles[interval.location] = (
                    resource_idle_cycles.get(interval.location, 0.0) + cycles
                )
                resource_idle_terms[interval.location] = (
                    resource_idle_terms.get(interval.location, 0.0)
                    + cycles
                    * math.log1p(
                        -idle_model.idle_failure_probability_per_cycle
                    )
                )
    log_success = (
        sum(operation_terms.values())
        + sum(logical_terms.values())
        + sum(idle_terms.values())
        + sum(resource_output_terms.values())
        + sum(resource_idle_terms.values())
    )
    success = math.exp(log_success)
    return FidelityEstimate(
        success_probability=success,
        failure_probability=1.0 - success,
        log_success_by_operation=dict(sorted(operation_terms.items())),
        log_success_by_logical_operation=dict(sorted(logical_terms.items())),
        log_success_by_idle_location=dict(sorted(idle_terms.items())),
        log_success_by_resource_output=dict(sorted(resource_output_terms.items())),
        log_success_by_resource_idle_location=dict(
            sorted(resource_idle_terms.items())
        ),
        architecture_operation_counts=dict(
            sorted(architecture_operation_counts.items())
        ),
        logical_operation_counts=dict(sorted(logical_counts.items())),
        idle_cycles_by_location=dict(sorted(idle_cycles.items())),
        consumed_resource_counts=dict(sorted(consumed_resource_counts.items())),
        resource_idle_cycles_by_location=dict(
            sorted(resource_idle_cycles.items())
        ),
        unprofiled_operation_counts=dict(sorted(unprofiled_operations.items())),
        unprofiled_logical_operation_counts=dict(sorted(unprofiled_logical.items())),
        unprofiled_idle_exposure_s=dict(sorted(unprofiled_idle.items())),
        unprofiled_resource_output_counts=dict(
            sorted(unprofiled_resource_output.items())
        ),
        unprofiled_resource_idle_exposure_s=dict(
            sorted(unprofiled_resource_idle.items())
        ),
        complete_coverage=(
            not unprofiled_operations
            and not unprofiled_logical
            and not unprofiled_idle
            and not unprofiled_resource_output
            and not unprofiled_resource_idle
        ),
        profile_hash=profile.profile_hash,
    )
