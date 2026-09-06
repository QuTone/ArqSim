"""Typed one-shot service for ArqSim architecture evaluation.

The facade composes the public semantic stages without embedding reference sweep
logic.  Advanced callers can still use the lower-level architecture, compiler,
and evaluation contracts directly.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
from typing import Any, Iterator, Literal, Mapping

from arqsim.compiler import (
    LogicalCompilationResult,
    LogicalLayout,
    LogicalCompilerSpec,
    canonical_compiler_spec,
)
from arqsim.compiler.layout import materialize_compute_layout
from arqsim.evaluation import (
    EvaluationAnalysis,
    EvaluationResult,
    ExecutionPolicy,
    ExecutionPlan,
    ExecutionTrace,
    FidelityEstimate,
    IncompleteFidelityCoverageError,
    PhysicalFootprintModel,
    analyze_evaluation,
    compile_and_lower,
    estimate_physical_footprint,
    evaluate,
    require_complete_fidelity,
    replay_execution_trace,
)
from arqsim.evaluation.lowering import (
    build_runtime_instruction_compiler,
    build_runtime_resource_compiler,
)
from arqsim.evaluation.components import (
    RuntimeComponentManifest,
    build_runtime_component_set,
    default_runtime_component_manifest,
    json_type_strict_equal,
)
from arqsim.evaluation.footprint import PhysicalFootprintEstimate
from arqsim._run_artifacts import EvaluationRunArtifacts
from arqsim.operation_profiles import (
    FidelityProfile,
    OperationLatencyProfile,
    ResolvedResourceProtocolBindings,
    effective_resource_protocol_bindings,
    resolve_resource_protocol_bindings,
    with_effective_arrivals,
)
from arqsim.architecture.logical_layout import LogicalLayoutRequest
from arqsim.operation_profiles.canonical_fidelity import canonical_fidelity_profile
from arqsim.program import FTCircuit
from arqsim.schema import (
    deep_freeze_json,
    normalize_json,
    semantic_hash,
    strict_json,
)
from arqsim.report_v2 import (
    EVALUATION_REPORT_V2_SCHEMA_VERSION,
    ReportV2Document,
    ReportV2Renderer,
    load_report_v2_document,
    validate_report_v2_document,
)
from arqsim.specification import (
    ArchitectureSpecification,
    build_architecture_specification,
)


EVALUATION_CONFIG_SCHEMA_VERSION = "arqsim.evaluation-config.v1"
EFFECTIVE_EVALUATION_CONFIG_SCHEMA_VERSION = (
    "arqsim.effective-evaluation-config.v1"
)
# Descriptive Python symbols for the frozen v1 preset identifiers.  The wire
# values stay unchanged so existing Report-v2 documents and semantic hashes
# remain valid.
REFERENCE_END_TO_END_FIDELITY_P1E3_PRESET = "canonical_reference_v1"
REFERENCE_PHYSICAL_QUBIT_FOOTPRINT_PRESET = "protocol_aware_reference_v1"
CANONICAL_FIDELITY_PRESET = REFERENCE_END_TO_END_FIDELITY_P1E3_PRESET
DEFAULT_FOOTPRINT_PRESET = REFERENCE_PHYSICAL_QUBIT_FOOTPRINT_PRESET
EVALUATION_REPORT_SCHEMA_VERSION = EVALUATION_REPORT_V2_SCHEMA_VERSION

FidelitySelection = FidelityProfile | Literal["canonical_reference_v1"] | None
FootprintSelection = (
    PhysicalFootprintModel | Literal["protocol_aware_reference_v1"]
)


class EvaluationRunError(RuntimeError):
    """Stable failure boundary for a staged :func:`run_evaluation` call.

    Programmer errors in the facade arguments remain ``TypeError``.  Once a
    valid request enters the evaluation pipeline, failures are surfaced here
    with a stable stage and code while the original exception is retained as
    ``__cause__`` for diagnostics.
    """

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        code: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.code = code
        self.details = deep_freeze_json(
            strict_json(details or {}, label="evaluation-run error details")
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": str(self),
                "stage": self.stage,
                "details": normalize_json(self.details),
            }
        }


@contextmanager
def _evaluation_stage(
    stage: str,
    code: str,
    *,
    details: Mapping[str, Any],
) -> Iterator[None]:
    try:
        yield
    except EvaluationRunError:
        raise
    except Exception as exc:
        raise EvaluationRunError(
            f"Evaluation failed during {stage}: {exc}",
            stage=stage,
            code=code,
            details={**details, "cause_type": type(exc).__name__},
        ) from exc

_EVALUATION_CONFIG_FIELDS = frozenset(
    {
        "schema_version",
        "profile_id",
        "workflow_id",
        "layout_policy_overrides",
        "logical_layout",
        "latency_profile",
        "evaluation_policy",
        "compiler_spec",
        "fidelity_profile",
        "footprint_model",
        "runtime_components",
    }
)


def _strict_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    strict_json(value, label=label)
    return value


def _selection(
    value: Any,
    *,
    label: str,
) -> tuple[str, Any]:
    record = _strict_mapping(value, label=label)
    unknown = set(record) - {"preset", "explicit"}
    if unknown:
        raise ValueError(f"Unknown {label} fields: {sorted(unknown)}")
    selected = [name for name in ("preset", "explicit") if name in record]
    if len(selected) != 1:
        raise ValueError(
            f"{label} must select exactly one of preset or explicit"
        )
    return selected[0], record[selected[0]]


def _full_trace_policy() -> ExecutionPolicy:
    return ExecutionPolicy(observation_level="full", run_seed=0)


def _resolve_evaluation_resource_protocols(
    specification: ArchitectureSpecification,
    requested_latency: OperationLatencyProfile,
) -> tuple[ResolvedResourceProtocolBindings, OperationLatencyProfile]:
    """Bind one protocol selection to runtime timing and fidelity inputs."""

    base = resolve_resource_protocol_bindings(
        specification,
        requested_latency,
    )
    bindings = effective_resource_protocol_bindings(
        requested_latency,
        base,
    )
    return bindings, with_effective_arrivals(requested_latency, bindings)


@dataclass(frozen=True, kw_only=True)
class EvaluationConfig:
    """Versioned inputs for one Program × Architecture evaluation.

    The facade selects named reference fidelity and footprint models by
    default.  Both selections remain explicit in the serialized request and
    the resolved model IDs, hashes, and provenance are retained in the report.
    Set ``fidelity_profile=None`` when an experiment intentionally disables
    fidelity estimation.  ``footprint_model`` is not a boolean switch: select
    either the named preset or an explicit :class:`PhysicalFootprintModel`.
    ``logical_layout=None`` preserves the architecture's canonical default
    logical canvas and is omitted from the serialized request.

    ``run_label``, ``architecture_overrides``, and ``execution_policy`` are the
    human-facing Python names.  Report-v2 keeps the original ``workflow_id``,
    ``layout_policy_overrides``, and ``evaluation_policy`` JSON keys as a
    frozen wire-compatibility detail.
    ``runtime_components`` is a replay receipt for the built-in one-shot
    facade, not a component-injection point; custom components use the lower
    level evaluation API.
    """

    profile_id: str = "1.1"
    run_label: str | None = None
    architecture_overrides: Mapping[str, Any] = field(default_factory=dict)
    latency_profile: OperationLatencyProfile = field(
        default_factory=OperationLatencyProfile
    )
    execution_policy: ExecutionPolicy = field(default_factory=_full_trace_policy)
    compiler_spec: LogicalCompilerSpec | None = None
    fidelity_profile: FidelitySelection = CANONICAL_FIDELITY_PRESET
    footprint_model: FootprintSelection = DEFAULT_FOOTPRINT_PRESET
    runtime_components: RuntimeComponentManifest = field(
        default_factory=default_runtime_component_manifest,
        init=False,
        repr=False,
    )
    logical_layout: LogicalLayoutRequest | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str):
            raise TypeError("profile_id must be a string")
        if self.run_label is not None and not isinstance(self.run_label, str):
            raise TypeError("run_label must be a string or None")
        if not isinstance(self.latency_profile, OperationLatencyProfile):
            raise TypeError(
                "latency_profile must be an OperationLatencyProfile"
            )
        if not isinstance(self.execution_policy, ExecutionPolicy):
            raise TypeError("execution_policy must be an ExecutionPolicy")
        if self.compiler_spec is not None and not isinstance(
            self.compiler_spec, LogicalCompilerSpec
        ):
            raise TypeError(
                "compiler_spec must be a LogicalCompilerSpec or None"
            )
        if self.fidelity_profile is not None and not isinstance(
            self.fidelity_profile, (str, FidelityProfile)
        ):
            raise TypeError(
                "fidelity_profile must be a FidelityProfile, named preset, or None"
            )
        if not isinstance(self.footprint_model, (str, PhysicalFootprintModel)):
            raise TypeError(
                "footprint_model must be a PhysicalFootprintModel or named preset"
            )
        profile_id = self.profile_id.strip()
        run_label = (
            self.run_label.strip() if self.run_label is not None else None
        )
        if not profile_id:
            raise ValueError("profile_id cannot be empty")
        if run_label == "":
            raise ValueError("run_label cannot be empty")
        if not isinstance(self.architecture_overrides, Mapping):
            raise ValueError("architecture_overrides must be a mapping")
        if self.logical_layout is not None and not isinstance(
            self.logical_layout, LogicalLayoutRequest
        ):
            raise TypeError(
                "logical_layout must be a LogicalLayoutRequest or None"
            )
        overrides = deep_freeze_json(
            strict_json(
                self.architecture_overrides,
                label="architecture_overrides",
            )
        )
        if isinstance(self.fidelity_profile, str) and (
            self.fidelity_profile != CANONICAL_FIDELITY_PRESET
        ):
            raise ValueError(
                f"Unknown fidelity preset: {self.fidelity_profile!r}"
            )
        if isinstance(self.footprint_model, str) and (
            self.footprint_model != DEFAULT_FOOTPRINT_PRESET
        ):
            raise ValueError(
                f"Unknown footprint preset: {self.footprint_model!r}"
            )
        if not isinstance(self.runtime_components, RuntimeComponentManifest):
            raise TypeError(
                "runtime_components must be a RuntimeComponentManifest"
            )
        object.__setattr__(self, "profile_id", profile_id)
        object.__setattr__(self, "run_label", run_label)
        object.__setattr__(self, "architecture_overrides", overrides)

    @property
    def workflow_id(self) -> str | None:
        """Frozen Report-v2 name for :attr:`run_label`.

        New Python code should use ``run_label``.  The alias remains so code
        inspecting older reports does not need a flag day migration.
        """

        return self.run_label

    @property
    def layout_policy_overrides(self) -> Mapping[str, Any]:
        """Frozen Report-v2 name for :attr:`architecture_overrides`."""

        return self.architecture_overrides

    @property
    def evaluation_policy(self) -> ExecutionPolicy:
        """Compatibility name for :attr:`execution_policy`.

        The serialized v1 request keeps ``evaluation_policy`` as its frozen
        wire key.  New Python callers use the narrower name
        ``execution_policy`` because this object does not select an
        architecture, logical layout, compiler backend, or scheduler
        implementation.
        """

        return self.execution_policy

    def to_dict(self) -> dict[str, Any]:
        compiler: dict[str, Any]
        if self.compiler_spec is None:
            compiler = {"preset": "canonical"}
        else:
            compiler = {"explicit": self.compiler_spec.to_dict()}

        fidelity: dict[str, Any] | None
        if self.fidelity_profile is None:
            fidelity = None
        elif isinstance(self.fidelity_profile, str):
            fidelity = {"preset": self.fidelity_profile}
        else:
            fidelity = {"explicit": self.fidelity_profile.to_dict()}

        footprint = (
            {"preset": self.footprint_model}
            if isinstance(self.footprint_model, str)
            else {"explicit": self.footprint_model.to_dict()}
        )
        result = {
            "schema_version": EVALUATION_CONFIG_SCHEMA_VERSION,
            "profile_id": self.profile_id,
            "workflow_id": self.run_label,
            "layout_policy_overrides": normalize_json(
                self.architecture_overrides
            ),
            "latency_profile": self.latency_profile.to_dict(),
            "evaluation_policy": self.execution_policy.to_dict(),
            "compiler_spec": compiler,
            "fidelity_profile": fidelity,
            "footprint_model": footprint,
            "runtime_components": self.runtime_components.to_dict(),
        }
        # Keep the established default config byte-for-byte stable.  Absence
        # selects the canonical architecture layout; it is not serialized as
        # a null or empty request.
        if self.logical_layout is not None:
            result["logical_layout"] = self.logical_layout.to_dict()
        return result

    @property
    def config_hash(self) -> str:
        return semantic_hash(self.to_dict())

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvaluationConfig":
        if not isinstance(data, Mapping):
            raise ValueError("Evaluation configuration must be a mapping")
        unknown = set(data) - _EVALUATION_CONFIG_FIELDS
        if unknown:
            raise ValueError(
                f"Unknown evaluation-config fields: {sorted(unknown)}"
            )
        if data.get("schema_version") != EVALUATION_CONFIG_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported evaluation-config schema: "
                f"{data.get('schema_version')!r}"
            )

        compiler_kind, compiler_value = _selection(
            data.get("compiler_spec", {"preset": "canonical"}),
            label="compiler_spec",
        )
        if compiler_kind == "preset" and compiler_value == "canonical":
            compiler = None
        elif compiler_kind == "explicit":
            compiler = LogicalCompilerSpec.from_dict(
                _strict_mapping(compiler_value, label="explicit compiler_spec")
            )
        else:
            raise ValueError("Unknown compiler_spec selection")

        fidelity_record = data.get(
            "fidelity_profile", {"preset": CANONICAL_FIDELITY_PRESET}
        )
        fidelity: FidelitySelection
        if fidelity_record is None:
            fidelity = None
        else:
            fidelity_kind, fidelity_value = _selection(
                fidelity_record,
                label="fidelity_profile",
            )
            if fidelity_kind == "preset":
                if fidelity_value != CANONICAL_FIDELITY_PRESET:
                    raise ValueError("Unknown fidelity_profile selection")
                fidelity = CANONICAL_FIDELITY_PRESET
            else:
                fidelity = FidelityProfile.from_dict(
                    _strict_mapping(
                        fidelity_value,
                        label="explicit fidelity_profile",
                    )
                )

        footprint_record = data.get(
            "footprint_model", {"preset": DEFAULT_FOOTPRINT_PRESET}
        )
        footprint_kind, footprint_value = _selection(
            footprint_record,
            label="footprint_model",
        )
        if (
            footprint_kind == "preset"
            and footprint_value == DEFAULT_FOOTPRINT_PRESET
        ):
            footprint: FootprintSelection = DEFAULT_FOOTPRINT_PRESET
        elif footprint_kind == "explicit":
            footprint = PhysicalFootprintModel.from_dict(
                _strict_mapping(
                    footprint_value,
                    label="explicit footprint_model",
                )
            )
        else:
            raise ValueError("Unknown footprint_model selection")

        runtime_components = RuntimeComponentManifest.from_dict(
            _strict_mapping(
                data.get(
                    "runtime_components",
                    default_runtime_component_manifest().to_dict(),
                ),
                label="runtime_components",
            )
        )
        canonical_runtime_components = default_runtime_component_manifest()
        if not json_type_strict_equal(
            runtime_components.to_dict(), canonical_runtime_components.to_dict()
        ):
            raise ValueError(
                "EvaluationConfig runtime_components is a reserved replay "
                "receipt and must select the built-in one-shot runtime; use "
                "the lower-level evaluation API for custom components"
            )

        return cls(
            profile_id=data.get("profile_id", "1.1"),
            run_label=(
                data["workflow_id"]
                if data.get("workflow_id") is not None
                else None
            ),
            architecture_overrides=_strict_mapping(
                data.get("layout_policy_overrides", {}),
                label="layout_policy_overrides",
            ),
            logical_layout=(
                LogicalLayoutRequest.from_dict(
                    _strict_mapping(
                        data["logical_layout"], label="logical_layout"
                    )
                )
                if "logical_layout" in data
                else None
            ),
            latency_profile=OperationLatencyProfile.from_dict(
                _strict_mapping(
                    data.get(
                        "latency_profile",
                        OperationLatencyProfile().to_dict(),
                    ),
                    label="latency_profile",
                )
            ),
            execution_policy=ExecutionPolicy.from_dict(
                _strict_mapping(
                    data.get(
                        "evaluation_policy",
                        _full_trace_policy().to_dict(),
                    ),
                    label="evaluation_policy",
                )
            ),
            compiler_spec=compiler,
            fidelity_profile=fidelity,
            footprint_model=footprint,
        )

    @classmethod
    def from_json(cls, text: str) -> "EvaluationConfig":
        if type(text) is not str:
            raise TypeError("Evaluation configuration JSON must be a string")

        def reject_constant(value: str) -> None:
            raise ValueError(
                "Non-finite JSON constant is not allowed in an evaluation "
                f"configuration: {value}"
            )

        def reject_duplicate_keys(
            pairs: list[tuple[str, Any]],
        ) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(
                        "Duplicate JSON object key in evaluation "
                        f"configuration: {key!r}"
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
            raise ValueError(
                "Invalid evaluation configuration JSON at "
                f"line {exc.lineno}, column {exc.colno}"
            ) from exc
        if type(decoded) is not dict:
            raise ValueError(
                "Evaluation configuration JSON must contain an object"
            )
        return cls.from_dict(decoded)


@dataclass(frozen=True)
class EffectiveEvaluationConfig:
    """Fully resolved, replay-oriented configuration recorded in a report.

    ``EvaluationConfig`` preserves what the user requested, including named
    presets.  This record preserves what the facade actually selected after it
    saw the workload and resolved architecture.
    """

    workflow_id: str
    architecture_profile: Mapping[str, Any]
    layout_policy: Mapping[str, Any]
    latency_profile: OperationLatencyProfile
    evaluation_policy: ExecutionPolicy
    seed: int
    compiler_spec: LogicalCompilerSpec
    footprint_model: PhysicalFootprintModel
    fidelity_profile: FidelityProfile | None
    runtime_components: RuntimeComponentManifest

    def __post_init__(self) -> None:
        if not isinstance(self.workflow_id, str) or not self.workflow_id.strip():
            raise ValueError("effective workflow_id must be a non-empty string")
        if not isinstance(self.latency_profile, OperationLatencyProfile):
            raise TypeError("effective latency_profile has the wrong type")
        if not isinstance(self.evaluation_policy, ExecutionPolicy):
            raise TypeError("effective evaluation_policy has the wrong type")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise TypeError("effective seed must be an integer")
        if self.seed != self.evaluation_policy.seed:
            raise ValueError("effective seed must match evaluation_policy.seed")
        if not isinstance(self.compiler_spec, LogicalCompilerSpec):
            raise TypeError("effective compiler_spec has the wrong type")
        if not isinstance(self.footprint_model, PhysicalFootprintModel):
            raise TypeError("effective footprint_model has the wrong type")
        if self.fidelity_profile is not None and not isinstance(
            self.fidelity_profile, FidelityProfile
        ):
            raise TypeError("effective fidelity_profile has the wrong type")
        if not isinstance(self.runtime_components, RuntimeComponentManifest):
            raise TypeError("effective runtime_components has the wrong type")
        object.__setattr__(self, "workflow_id", self.workflow_id.strip())
        object.__setattr__(
            self,
            "architecture_profile",
            deep_freeze_json(
                strict_json(
                    _strict_mapping(
                        self.architecture_profile,
                        label="effective architecture_profile",
                    ),
                    label="effective architecture_profile",
                )
            ),
        )
        object.__setattr__(
            self,
            "layout_policy",
            deep_freeze_json(
                strict_json(
                    _strict_mapping(
                        self.layout_policy,
                        label="effective layout_policy",
                    ),
                    label="effective layout_policy",
                )
            ),
        )

    def semantic_dict(self) -> dict[str, Any]:
        architecture_profile = normalize_json(self.architecture_profile)
        layout_policy = normalize_json(self.layout_policy)
        fidelity = (
            self.fidelity_profile.to_dict()
            if self.fidelity_profile is not None
            else None
        )
        return {
            "schema_version": EFFECTIVE_EVALUATION_CONFIG_SCHEMA_VERSION,
            "workflow_id": self.workflow_id,
            "architecture_profile": {
                "definition": architecture_profile,
                "profile_hash": semantic_hash(architecture_profile),
            },
            "layout_policy": {
                "configuration": layout_policy,
                "policy_hash": semantic_hash(layout_policy),
            },
            "latency_profile": {
                "profile": self.latency_profile.to_dict(),
                "profile_hash": self.latency_profile.profile_hash,
            },
            "evaluation_policy": self.evaluation_policy.to_dict(),
            "seed": self.seed,
            "compiler_spec": self.compiler_spec.to_dict(),
            "footprint_model": self.footprint_model.to_dict(),
            "fidelity_profile": (
                {
                    "profile": fidelity,
                    "profile_hash": self.fidelity_profile.profile_hash,
                }
                if self.fidelity_profile is not None
                else None
            ),
            "runtime_components": self.runtime_components.to_dict(),
        }

    @property
    def effective_config_hash(self) -> str:
        return semantic_hash(self.semantic_dict())

    def to_dict(self) -> dict[str, Any]:
        result = self.semantic_dict()
        result["effective_config_hash"] = self.effective_config_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EffectiveEvaluationConfig":
        if not isinstance(data, Mapping):
            raise ValueError("Effective evaluation configuration must be a mapping")
        allowed = {
            "schema_version",
            "workflow_id",
            "architecture_profile",
            "layout_policy",
            "latency_profile",
            "evaluation_policy",
            "seed",
            "compiler_spec",
            "footprint_model",
            "fidelity_profile",
            "runtime_components",
            "effective_config_hash",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(
                f"Unknown effective-config fields: {sorted(unknown)}"
            )
        missing = allowed - {"effective_config_hash"} - set(data)
        if missing:
            raise ValueError(
                f"Effective evaluation configuration is missing: {sorted(missing)}"
            )
        if data.get("schema_version") != EFFECTIVE_EVALUATION_CONFIG_SCHEMA_VERSION:
            raise ValueError("Unsupported effective-evaluation-config schema")

        architecture = _strict_mapping(
            data["architecture_profile"], label="architecture_profile"
        )
        if set(architecture) != {"definition", "profile_hash"}:
            raise ValueError("Invalid effective architecture_profile record")
        architecture_definition = _strict_mapping(
            architecture["definition"], label="architecture profile definition"
        )
        if architecture["profile_hash"] != semantic_hash(architecture_definition):
            raise ValueError("Effective architecture profile hash mismatch")

        layout = _strict_mapping(data["layout_policy"], label="layout_policy")
        if set(layout) != {"configuration", "policy_hash"}:
            raise ValueError("Invalid effective layout_policy record")
        layout_configuration = _strict_mapping(
            layout["configuration"], label="layout policy configuration"
        )
        if layout["policy_hash"] != semantic_hash(layout_configuration):
            raise ValueError("Effective layout-policy hash mismatch")

        latency = _strict_mapping(
            data["latency_profile"], label="latency_profile"
        )
        if set(latency) != {"profile", "profile_hash"}:
            raise ValueError("Invalid effective latency_profile record")
        latency_profile = OperationLatencyProfile.from_dict(
            _strict_mapping(latency["profile"], label="latency profile")
        )
        if latency["profile_hash"] != latency_profile.profile_hash:
            raise ValueError("Effective latency-profile hash mismatch")

        fidelity_record = data["fidelity_profile"]
        fidelity_profile: FidelityProfile | None
        if fidelity_record is None:
            fidelity_profile = None
        else:
            fidelity = _strict_mapping(
                fidelity_record, label="effective fidelity_profile"
            )
            if set(fidelity) != {"profile", "profile_hash"}:
                raise ValueError("Invalid effective fidelity_profile record")
            fidelity_profile = FidelityProfile.from_dict(
                _strict_mapping(fidelity["profile"], label="fidelity profile")
            )
            if fidelity["profile_hash"] != fidelity_profile.profile_hash:
                raise ValueError("Effective fidelity-profile hash mismatch")

        result = cls(
            workflow_id=data["workflow_id"],
            architecture_profile=architecture_definition,
            layout_policy=layout_configuration,
            latency_profile=latency_profile,
            evaluation_policy=ExecutionPolicy.from_dict(
                _strict_mapping(
                    data["evaluation_policy"], label="evaluation_policy"
                )
            ),
            seed=data["seed"],
            compiler_spec=LogicalCompilerSpec.from_dict(
                _strict_mapping(data["compiler_spec"], label="compiler_spec")
            ),
            footprint_model=PhysicalFootprintModel.from_dict(
                _strict_mapping(data["footprint_model"], label="footprint_model")
            ),
            fidelity_profile=fidelity_profile,
            runtime_components=RuntimeComponentManifest.from_dict(
                _strict_mapping(
                    data["runtime_components"], label="runtime_components"
                )
            ),
        )
        expected = data.get("effective_config_hash")
        if expected is not None and expected != result.effective_config_hash:
            raise ValueError("Effective evaluation configuration hash mismatch")
        return result


@dataclass(frozen=True)
class EvaluationSummary:
    """Headline results shared by live and loaded evaluation reports.

    The fields are the typed Python view of the frozen
    ``results.summary`` Report-v2 record.  Detailed causal and scientific
    results remain available through :attr:`EvaluationReport.execution_trace`,
    :attr:`EvaluationReport.analysis`, and :attr:`EvaluationReport.fidelity`.
    """

    total_latency_s: float
    total_physical_qubits: float
    success_probability: float | None
    fidelity_complete_coverage: bool | None
    completed_program_instructions: int
    event_count: int
    invariant_checks: Mapping[str, bool] = field(hash=False)

    def __post_init__(self) -> None:
        for name in ("total_latency_s", "total_physical_qubits"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError(f"{name} must be a real number")
            strict_json(value, label=name)
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
            object.__setattr__(self, name, float(value))
        probability = self.success_probability
        if probability is not None:
            if not isinstance(probability, (int, float)) or isinstance(
                probability, bool
            ):
                raise TypeError("success_probability must be a real number or None")
            strict_json(probability, label="success_probability")
            probability = float(probability)
            if not 0 <= probability <= 1:
                raise ValueError("success_probability must be between zero and one")
            object.__setattr__(self, "success_probability", probability)
        coverage = self.fidelity_complete_coverage
        if coverage is not None and type(coverage) is not bool:
            raise TypeError(
                "fidelity_complete_coverage must be a boolean or None"
            )
        for name in ("completed_program_instructions", "event_count"):
            value = getattr(self, name)
            if type(value) is not int:
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} cannot be negative")
        if not isinstance(self.invariant_checks, Mapping):
            raise TypeError("invariant_checks must be a mapping")
        checks = dict(self.invariant_checks)
        if any(type(name) is not str for name in checks):
            raise TypeError("invariant_checks keys must be strings")
        if any(type(value) is not bool for value in checks.values()):
            raise TypeError("invariant_checks values must be booleans")
        object.__setattr__(self, "invariant_checks", deep_freeze_json(checks))

    @property
    def all_invariants_satisfied(self) -> bool:
        """Whether every terminal correctness invariant passed."""

        return all(self.invariant_checks.values())

    def to_dict(self) -> dict[str, Any]:
        """Return the exact JSON-shaped Report-v2 summary record."""

        return {
            "total_latency_s": self.total_latency_s,
            "total_physical_qubits": self.total_physical_qubits,
            "success_probability": self.success_probability,
            "fidelity_complete_coverage": self.fidelity_complete_coverage,
            "completed_program_instructions": (
                self.completed_program_instructions
            ),
            "event_count": self.event_count,
            "invariant_checks": normalize_json(self.invariant_checks),
        }


@dataclass(frozen=True, init=False, repr=False, eq=False)
class EvaluationReport:
    """Native typed result of one evaluation, rendered as Report v2.

    Obtain reports from :func:`run_evaluation`, :meth:`from_dict`, or
    :meth:`from_json`; direct construction is intentionally unsupported.
    Those three factories expose the same stable result surface, including
    :attr:`summary`, :attr:`execution_trace`, :attr:`footprint`,
    :attr:`fidelity`, and :attr:`analysis`.

    ``evaluation`` is a live-run compatibility cache and is ``None`` after a
    Report-v2 round trip.  New user code must not depend on it.  Report
    equality and hashing use :attr:`report_hash`, the identity of the complete
    canonical Report-v2 document.

    Report v1 is intentionally not part of construction.  Call
    :func:`arqsim.report_v1.render_evaluation_report_v1` when the frozen
    compatibility projection is required.
    """

    config: EvaluationConfig
    workflow_id: str
    circuit: FTCircuit
    magic_sizing_circuit: FTCircuit | None
    specification: ArchitectureSpecification
    compiler_layout: LogicalLayout
    footprint_model: PhysicalFootprintModel
    footprint: PhysicalFootprintEstimate
    resource_protocol_bindings: ResolvedResourceProtocolBindings
    latency_profile: OperationLatencyProfile
    compiler_spec: LogicalCompilerSpec
    logical_compilation: LogicalCompilationResult
    execution_plan: ExecutionPlan
    evaluation: EvaluationResult | None = field(repr=False, compare=False)
    fidelity_profile: FidelityProfile | None
    fidelity: FidelityEstimate | None
    analysis: EvaluationAnalysis
    _run_artifacts: EvaluationRunArtifacts | None = field(
        init=False,
        repr=False,
        compare=False,
    )
    _document: Mapping[str, Any] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _effective_configuration: EffectiveEvaluationConfig | None = field(
        init=False,
        repr=False,
        compare=False,
        default=None,
    )
    _stored_execution_trace: ExecutionTrace | None = field(
        init=False,
        repr=False,
        compare=False,
        default=None,
    )
    _stored_discrete_time_log: tuple[Mapping[str, Any], ...] | None = field(
        init=False,
        repr=False,
        compare=False,
        default=None,
    )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError(
            "EvaluationReport cannot be constructed directly; use "
            "run_evaluation(), EvaluationReport.from_dict(), or "
            "EvaluationReport.from_json()"
        )

    def __post_init__(self) -> None:
        if not isinstance(self.config, EvaluationConfig):
            raise TypeError("report config must be an EvaluationConfig")
        if not isinstance(self.workflow_id, str) or not self.workflow_id.strip():
            raise ValueError("report workflow_id must be a non-empty string")
        if not isinstance(self.circuit, FTCircuit):
            raise TypeError("report circuit must be an FTCircuit")
        if self.magic_sizing_circuit is not None and not isinstance(
            self.magic_sizing_circuit, FTCircuit
        ):
            raise TypeError("magic_sizing_circuit must be an FTCircuit or None")
        if not isinstance(self.specification, ArchitectureSpecification):
            raise TypeError("report specification has the wrong type")
        if not isinstance(self.compiler_layout, LogicalLayout):
            raise TypeError("report compiler_layout has the wrong type")
        if not isinstance(self.footprint_model, PhysicalFootprintModel):
            raise TypeError("report footprint_model has the wrong type")
        if not isinstance(self.footprint, PhysicalFootprintEstimate):
            raise TypeError("report footprint has the wrong type")
        if not isinstance(
            self.resource_protocol_bindings,
            ResolvedResourceProtocolBindings,
        ):
            raise TypeError(
                "report resource_protocol_bindings has the wrong type"
            )
        if not isinstance(self.latency_profile, OperationLatencyProfile):
            raise TypeError("report latency_profile has the wrong type")
        if not isinstance(self.compiler_spec, LogicalCompilerSpec):
            raise TypeError("report compiler_spec has the wrong type")
        if not isinstance(self.logical_compilation, LogicalCompilationResult):
            raise TypeError("report logical_compilation has the wrong type")
        if not isinstance(self.execution_plan, ExecutionPlan):
            raise TypeError("report execution_plan has the wrong type")
        if not isinstance(self.evaluation, EvaluationResult):
            raise TypeError("report evaluation has the wrong type")
        if not isinstance(self.analysis, EvaluationAnalysis):
            raise TypeError("report analysis has the wrong type")
        if self.fidelity_profile is not None and not isinstance(
            self.fidelity_profile, FidelityProfile
        ):
            raise TypeError("report fidelity_profile has the wrong type")
        if self.fidelity is not None and not isinstance(
            self.fidelity, FidelityEstimate
        ):
            raise TypeError("report fidelity estimate has the wrong type")
        if (self.fidelity_profile is None) != (self.fidelity is None):
            raise ValueError(
                "Report fidelity profile and estimate must be enabled together"
            )

        plan = self.execution_plan
        if plan.circuit_hash != self.circuit.semantic_hash:
            raise ValueError("Report workload and execution plan do not match")
        if plan.architecture_hash != self.specification.architecture_hash:
            raise ValueError("Report architecture and execution plan do not match")
        if plan.latency_profile_hash != self.latency_profile.profile_hash:
            raise ValueError("Report latency profile and plan do not match")
        compilation = self.logical_compilation
        if compilation.circuit_hash != self.circuit.semantic_hash:
            raise ValueError("Report workload and logical compilation do not match")
        if compilation.architecture_hash != self.specification.architecture_hash:
            raise ValueError(
                "Report architecture and logical compilation do not match"
            )
        if compilation.latency_profile_hash != self.latency_profile.binding_hash:
            raise ValueError(
                "Report latency profile and logical compilation do not match"
            )
        if (
            compilation.compiler_spec.compiler_hash
            != self.compiler_spec.compiler_hash
        ):
            raise ValueError("Report compiler and logical compilation do not match")
        bindings = self.resource_protocol_bindings
        if not json_type_strict_equal(
            plan.provenance.get("resource_protocol_bindings"),
            bindings.to_dict(),
        ) or plan.provenance.get("resource_protocol_bindings_hash") != (
            bindings.bindings_hash
        ):
            raise ValueError(
                "Report resource protocol bindings and plan do not match"
            )
        if self.latency_profile.provenance.get(
            "resource_protocol_bindings_hash"
        ) != bindings.bindings_hash:
            raise ValueError(
                "Report resource protocol bindings and latency do not match"
            )
        if plan.policy.to_dict() != self.config.evaluation_policy.to_dict():
            raise ValueError("Report evaluation policy and plan do not match")
        if self.evaluation.plan_hash != plan.plan_hash:
            raise ValueError("Report execution result and plan do not match")
        # EvaluationResult already owns a validated, recursively immutable
        # ExecutionTrace.  Serializing that trace, reconstructing the same
        # typed ledger, and hashing it again adds no validation coverage here;
        # replay the authoritative typed trace directly.  The public document
        # validator below still performs the full parse/hash/replay path for
        # every untrusted report document.
        replay_execution_trace(self.evaluation.trace, plan)
        if self.evaluation.seed != plan.policy.seed:
            raise ValueError("Report execution seed and plan policy do not match")
        if (
            self.config.compiler_spec is not None
            and self.compiler_spec.compiler_hash
            != self.config.compiler_spec.compiler_hash
        ):
            raise ValueError("Requested and resolved compilers do not match")
        recorded_compiler = plan.provenance.get("compiler_spec_hash")
        if recorded_compiler is not None and (
            recorded_compiler != self.compiler_spec.compiler_hash
        ):
            raise ValueError("Report compiler and execution plan do not match")
        if self.footprint.model_hash != self.footprint_model.model_hash:
            raise ValueError("Report footprint model and estimate do not match")
        if self.footprint.architecture_hash != self.specification.architecture_hash:
            raise ValueError("Report footprint and architecture do not match")
        runtime_hash = self.evaluation.runtime_components.get("manifest_hash")
        if runtime_hash != self.config.runtime_components.manifest_hash:
            raise ValueError("Requested and effective runtime manifests do not match")
        if plan.provenance.get("compilation_hash") != compilation.compilation_hash:
            raise ValueError("Report logical compilation and plan do not match")

        artifacts = EvaluationRunArtifacts(
            circuit=self.circuit,
            magic_sizing_circuit=self.magic_sizing_circuit,
            specification=self.specification,
            compiler_layout=self.compiler_layout,
            footprint_model=self.footprint_model,
            footprint=self.footprint,
            resource_protocol_bindings=self.resource_protocol_bindings,
            latency_profile=self.latency_profile,
            compiler_spec=self.compiler_spec,
            logical_compilation=self.logical_compilation,
            execution_plan=self.execution_plan,
            evaluation=self.evaluation,
            fidelity_profile=self.fidelity_profile,
            fidelity=self.fidelity,
            analysis=self.analysis,
        )
        payload = ReportV2Renderer().render(
            artifacts,
            requested_config=self.config.to_dict(),
            workflow_id=self.workflow_id,
        )
        object.__setattr__(self, "_run_artifacts", artifacts)
        object.__setattr__(self, "_document", payload)

    @classmethod
    def _from_run_artifacts(
        cls,
        *,
        config: EvaluationConfig,
        workflow_id: str,
        artifacts: EvaluationRunArtifacts,
    ) -> "EvaluationReport":
        """Adapt core run artifacts to the native public Report-v2 object."""

        report = cls.__new__(cls)
        values = {
            "config": config,
            "workflow_id": workflow_id,
            "circuit": artifacts.circuit,
            "magic_sizing_circuit": artifacts.magic_sizing_circuit,
            "specification": artifacts.specification,
            "compiler_layout": artifacts.compiler_layout,
            "footprint_model": artifacts.footprint_model,
            "footprint": artifacts.footprint,
            "resource_protocol_bindings": artifacts.resource_protocol_bindings,
            "latency_profile": artifacts.latency_profile,
            "compiler_spec": artifacts.compiler_spec,
            "logical_compilation": artifacts.logical_compilation,
            "execution_plan": artifacts.execution_plan,
            "evaluation": artifacts.evaluation,
            "fidelity_profile": artifacts.fidelity_profile,
            "fidelity": artifacts.fidelity,
            "analysis": artifacts.analysis,
            "_run_artifacts": None,
            "_document": {},
            "_effective_configuration": None,
            "_stored_execution_trace": None,
            "_stored_discrete_time_log": None,
        }
        for name, value in values.items():
            object.__setattr__(report, name, value)
        report.__post_init__()
        return report

    @property
    def effective_configuration(self) -> EffectiveEvaluationConfig:
        """Build the frozen v1 compatibility receipt only when requested."""

        if self._run_artifacts is None:
            raise TypeError(
                "Loaded Report v2 cannot be converted into Report-v1 receipts"
            )
        cached = self._effective_configuration
        if cached is not None:
            return cached
        from arqsim.architecture.gallery import get_architecture_profile
        from arqsim.report_v1 import render_policy_v1, render_profile_v2

        effective = EffectiveEvaluationConfig(
            workflow_id=self.workflow_id,
            architecture_profile=render_profile_v2(
                get_architecture_profile(self.config.profile_id)
            ),
            layout_policy=render_policy_v1(self.config.architecture_overrides),
            latency_profile=self.latency_profile,
            evaluation_policy=self.execution_plan.policy,
            seed=self.execution_trace.seed,
            compiler_spec=self.compiler_spec,
            footprint_model=self.footprint_model,
            fidelity_profile=self.fidelity_profile,
            runtime_components=RuntimeComponentManifest.from_dict(
                normalize_json(self.execution_plan.runtime_components)
            ),
        )
        object.__setattr__(self, "_effective_configuration", effective)
        return effective

    @property
    def execution_trace(self) -> ExecutionTrace:
        """Return the canonical execution authority for native or loaded v2."""

        if self.evaluation is not None:
            return self.evaluation.trace
        if self._stored_execution_trace is None:  # pragma: no cover - defense
            raise RuntimeError("Evaluation report has no execution trace")
        return self._stored_execution_trace

    @property
    def discrete_time_log(self) -> tuple[Mapping[str, Any], ...]:
        """Return the validated, non-authoritative observation cache."""

        if self.evaluation is not None:
            return self.evaluation.discrete_time_log
        if self._stored_discrete_time_log is None:  # pragma: no cover - defense
            raise RuntimeError("Evaluation report has no observation cache")
        return self._stored_discrete_time_log

    @property
    def summary(self) -> EvaluationSummary:
        """Return stable headline results for a live or loaded report."""

        results = self._document["results"]
        if not isinstance(results, Mapping):  # pragma: no cover - validated
            raise RuntimeError("Evaluation report has no results record")
        record = results["summary"]
        if not isinstance(record, Mapping):  # pragma: no cover - validated
            raise RuntimeError("Evaluation report has no summary record")
        return EvaluationSummary(
            total_latency_s=record["total_latency_s"],
            total_physical_qubits=record["total_physical_qubits"],
            success_probability=record["success_probability"],
            fidelity_complete_coverage=record[
                "fidelity_complete_coverage"
            ],
            completed_program_instructions=record[
                "completed_program_instructions"
            ],
            event_count=record["event_count"],
            invariant_checks=record["invariant_checks"],
        )

    @property
    def run_label(self) -> str:
        """Human-facing name for the frozen Report-v2 ``workflow_id``."""

        return self.workflow_id

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> "EvaluationReport":
        """Strictly parse Report v2 without rerunning compiler or runtime.

        Report v2 deliberately omits mutable-runtime diagnostic caches.  A
        loaded report therefore exposes ``execution_trace`` and
        ``discrete_time_log`` as its execution views and leaves ``evaluation``
        as ``None`` instead of fabricating an incomplete ``EvaluationResult``.
        """

        return cls._from_validated_document(
            validate_report_v2_document(document)
        )

    @classmethod
    def _from_validated_document(
        cls, parsed: ReportV2Document
    ) -> "EvaluationReport":
        """Adapt the codec's validated, immutable document without re-parsing."""

        plan = parsed.execution_plan
        bindings_record = plan.provenance.get("resource_protocol_bindings")
        if not isinstance(bindings_record, Mapping):  # codec already checks
            raise ValueError(
                "Report-v2 Plan is missing resource-protocol bindings"
            )
        report = cls.__new__(cls)
        values = {
            "config": parsed.config,
            "workflow_id": parsed.workflow_id,
            "circuit": parsed.workload,
            "magic_sizing_circuit": parsed.magic_sizing_workload,
            "specification": parsed.architecture,
            "compiler_layout": materialize_compute_layout(parsed.architecture),
            "footprint_model": parsed.footprint_model,
            "footprint": parsed.footprint,
            "resource_protocol_bindings": (
                ResolvedResourceProtocolBindings.from_dict(
                    normalize_json(bindings_record)
                )
            ),
            "latency_profile": parsed.latency_profile,
            "compiler_spec": parsed.logical_compilation.compiler_spec,
            "logical_compilation": parsed.logical_compilation,
            "execution_plan": plan,
            "evaluation": None,
            "fidelity_profile": parsed.fidelity_profile,
            "fidelity": parsed.fidelity,
            "analysis": parsed.analysis,
            "_run_artifacts": None,
            "_document": parsed._document,
            "_effective_configuration": None,
            "_stored_execution_trace": parsed.execution_trace,
            "_stored_discrete_time_log": parsed.discrete_time_log,
        }
        for name, value in values.items():
            object.__setattr__(report, name, value)
        return report

    @classmethod
    def from_json(cls, text: str) -> "EvaluationReport":
        """Strictly parse one serialized Report-v2 document."""

        return cls._from_validated_document(load_report_v2_document(text))

    @property
    def report_hash(self) -> str:
        """Identity hash of the complete canonical Report-v2 document."""

        return str(self._document["report_hash"])

    def __repr__(self) -> str:
        summary = self.summary
        return (
            f"{type(self).__name__}("
            f"run_label={self.run_label!r}, "
            f"profile_id={self.config.profile_id!r}, "
            f"total_latency_s={summary.total_latency_s!r}, "
            f"total_physical_qubits={summary.total_physical_qubits!r}, "
            f"success_probability={summary.success_probability!r}, "
            f"report_hash={self.report_hash!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, EvaluationReport):
            return NotImplemented
        return self.report_hash == other.report_hash

    def __hash__(self) -> int:
        return hash((EvaluationReport, self.report_hash))

    def to_dict(self) -> dict[str, Any]:
        return normalize_json(self._document)

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            indent=indent,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"


def validate_evaluation_report_document(
    document: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Strictly validate one native Report-v2 document.

    Report v1 is output-only and intentionally has no public input codec.
    """

    return validate_report_v2_document(document)._document


def load_evaluation_report_document(text: str) -> Mapping[str, Any]:
    """Load strict JSON and validate one native Report-v2 document."""

    return load_report_v2_document(text)._document


def run_evaluation(
    circuit: FTCircuit,
    config: EvaluationConfig | None = None,
    *,
    magic_sizing_circuit: FTCircuit | None = None,
) -> EvaluationReport:
    """Build, compile, co-execute, and report one evaluation request.

    ``circuit`` is a synthesis-level, architecture-independent logical IR.
    ``magic_sizing_circuit`` is an advanced experimental reference: it may
    replace magic-state demand for architecture sizing but is never executed.

    Raises:
        TypeError: A facade argument has the wrong Python type.
        EvaluationRunError: A valid request fails in a named pipeline stage.
            The original exception is retained as ``__cause__``.
    """

    if not isinstance(circuit, FTCircuit):
        raise TypeError("circuit must be an FTCircuit")
    if config is not None and not isinstance(config, EvaluationConfig):
        raise TypeError("config must be an EvaluationConfig or None")
    if magic_sizing_circuit is not None and not isinstance(
        magic_sizing_circuit, FTCircuit
    ):
        raise TypeError("magic_sizing_circuit must be an FTCircuit or None")
    effective = config or EvaluationConfig()
    request_details = {
        "circuit_hash": circuit.semantic_hash,
        "profile_id": effective.profile_id,
        "run_label": effective.run_label,
    }

    with _evaluation_stage(
        "architecture_resolution",
        "architecture_resolution_failed",
        details=request_details,
    ):
        specification = build_architecture_specification(
            circuit,
            effective.profile_id,
            magic_sizing_circuit=magic_sizing_circuit,
            policy_overrides=effective.architecture_overrides,
            logical_layout=effective.logical_layout,
        )

    with _evaluation_stage(
        "resource_protocol_resolution",
        "resource_protocol_resolution_failed",
        details=request_details,
    ):
        resource_protocol_bindings, resolved_latency = (
            _resolve_evaluation_resource_protocols(
                specification,
                effective.latency_profile,
            )
        )

    with _evaluation_stage(
        "compiler_setup",
        "compiler_setup_failed",
        details=request_details,
    ):
        compiler = effective.compiler_spec or canonical_compiler_spec(specification)
        runtime_compiler = build_runtime_instruction_compiler(
            circuit,
            specification,
            compiler,
            resolved_latency,
        )
        resource_compiler = build_runtime_resource_compiler(
            specification,
            compiler,
            resolved_latency,
        )
        runtime_components = build_runtime_component_set(
            effective.runtime_components,
            runtime_instruction_compiler=runtime_compiler,
            runtime_resource_compiler=resource_compiler,
        )

    with _evaluation_stage(
        "logical_compilation",
        "logical_compilation_failed",
        details=request_details,
    ):
        logical_compilation, plan = compile_and_lower(
            circuit,
            specification,
            resolved_latency,
            effective.execution_policy,
            compiler_spec=compiler,
            runtime_components=runtime_components.manifest.to_dict(),
            resource_protocol_bindings=resource_protocol_bindings,
        )

    with _evaluation_stage(
        "runtime_evaluation",
        "runtime_evaluation_failed",
        details={**request_details, "plan_hash": plan.plan_hash},
    ):
        result = evaluate(
            plan,
            runtime_components=runtime_components,
        )

    with _evaluation_stage(
        "result_analysis",
        "result_analysis_failed",
        details={**request_details, "plan_hash": plan.plan_hash},
    ):
        resolved_fidelity_profile: FidelityProfile | None
        if effective.fidelity_profile is None:
            resolved_fidelity_profile = None
        elif effective.fidelity_profile == CANONICAL_FIDELITY_PRESET:
            resolved_fidelity_profile = canonical_fidelity_profile(
                specification,
                resolved_latency,
                resource_protocol_bindings,
            )
        else:
            resolved_fidelity_profile = effective.fidelity_profile
        if effective.footprint_model == DEFAULT_FOOTPRINT_PRESET:
            footprint_model = PhysicalFootprintModel.reference_v1()
        else:
            assert isinstance(
                effective.footprint_model, PhysicalFootprintModel
            )
            footprint_model = effective.footprint_model
        footprint = estimate_physical_footprint(
            specification,
            footprint_model,
        )
        analysis, fidelity = analyze_evaluation(
            result,
            plan,
            specification,
            footprint,
            resolved_fidelity_profile,
        )
        if fidelity is not None:
            try:
                require_complete_fidelity(fidelity)
            except IncompleteFidelityCoverageError as exc:
                raise EvaluationRunError(
                    "The selected fidelity profile does not cover every "
                    "realized operation and idle exposure",
                    stage="result_analysis",
                    code=exc.code,
                    details={
                        **request_details,
                        "plan_hash": plan.plan_hash,
                        "fidelity_profile_hash": fidelity.profile_hash,
                        **exc.details,
                    },
                ) from exc

    with _evaluation_stage(
        "report_rendering",
        "report_rendering_failed",
        details={**request_details, "plan_hash": plan.plan_hash},
    ):
        workflow_id = effective.run_label or str(
            circuit.provenance.get("benchmark", circuit.semantic_hash[:12])
        )
        artifacts = EvaluationRunArtifacts(
            circuit=circuit,
            magic_sizing_circuit=magic_sizing_circuit,
            specification=specification,
            compiler_layout=materialize_compute_layout(specification),
            footprint_model=footprint_model,
            footprint=footprint,
            resource_protocol_bindings=resource_protocol_bindings,
            latency_profile=resolved_latency,
            compiler_spec=compiler,
            logical_compilation=logical_compilation,
            execution_plan=plan,
            evaluation=result,
            fidelity_profile=resolved_fidelity_profile,
            fidelity=fidelity,
            analysis=analysis,
        )
        return EvaluationReport._from_run_artifacts(
            config=effective,
            workflow_id=workflow_id,
            artifacts=artifacts,
        )


__all__ = [
    "CANONICAL_FIDELITY_PRESET",
    "DEFAULT_FOOTPRINT_PRESET",
    "EFFECTIVE_EVALUATION_CONFIG_SCHEMA_VERSION",
    "EVALUATION_CONFIG_SCHEMA_VERSION",
    "EVALUATION_REPORT_SCHEMA_VERSION",
    "EffectiveEvaluationConfig",
    "EvaluationAnalysis",
    "EvaluationConfig",
    "EvaluationReport",
    "EvaluationRunError",
    "EvaluationSummary",
    "LogicalLayoutRequest",
    "REFERENCE_END_TO_END_FIDELITY_P1E3_PRESET",
    "REFERENCE_PHYSICAL_QUBIT_FOOTPRINT_PRESET",
    "load_evaluation_report_document",
    "run_evaluation",
    "validate_evaluation_report_document",
]
