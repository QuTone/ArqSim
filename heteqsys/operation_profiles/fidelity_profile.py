"""Operation- and location-specific failure inputs.

These records contain simulation/literature facts only.  They intentionally do
not inspect execution traces; end-to-end aggregation belongs to
``evaluation.fidelity_estimator``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from heteqsys.schema import deep_freeze_json, normalize_json, semantic_hash, strict_json


def _plain_nonempty_string(value: Any, *, label: str) -> str:
    """Return one normalized plain string without Python coercion."""

    if type(value) is not str:
        raise TypeError(f"{label} must be a plain string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must be non-empty")
    return normalized


def _plain_finite_number(value: Any, *, label: str) -> float:
    """Return one finite JSON-number primitive, explicitly excluding bool."""

    if type(value) not in {int, float}:
        raise TypeError(f"{label} must be a plain real number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


@dataclass(frozen=True)
class ResourceBufferFidelityModel:
    """One calibrated resource-buffer idle channel.

    ``location`` is the canonical qualified Submodule address used by the
    execution plan.  QEC identity lives here rather than on ``BufferSpec``:
    Node-owned buffers inherit it from their architecture Submodule, while an
    Interconnect-owned Bell buffer inherits the stored state's QEC from the
    selected resource protocol.
    """

    location: str
    owner_kind: str
    qec_code: str
    qec_parameters: Mapping[str, Any]
    logical_qubits_per_token: int
    idle_failure_probability_per_cycle: float
    idle_cycle_time_s: float

    def __post_init__(self) -> None:
        location = _plain_nonempty_string(
            self.location,
            label="Resource-buffer fidelity location",
        )
        owner_kind = _plain_nonempty_string(
            self.owner_kind,
            label="Resource-buffer owner_kind",
        ).lower()
        qec_code = _plain_nonempty_string(
            self.qec_code,
            label="Resource-buffer QEC code",
        ).lower()
        if owner_kind not in {"node", "interconnect"}:
            raise ValueError(
                "Resource-buffer owner_kind must be 'node' or 'interconnect'"
            )
        if type(self.logical_qubits_per_token) is not int:
            raise TypeError("logical_qubits_per_token must be a plain integer")
        if self.logical_qubits_per_token <= 0:
            raise ValueError(
                "logical_qubits_per_token must be a positive plain integer"
            )
        probability = _plain_finite_number(
            self.idle_failure_probability_per_cycle,
            label="Resource-buffer idle failure probability",
        )
        cycle_time = _plain_finite_number(
            self.idle_cycle_time_s,
            label="Resource-buffer idle cycle time",
        )
        if not 0 <= probability < 1:
            raise ValueError(
                "Resource-buffer idle failure probability must be in [0, 1)"
            )
        if cycle_time <= 0:
            raise ValueError("Resource-buffer idle cycle time must be positive")
        parameters = strict_json(
            self.qec_parameters,
            label="resource-buffer QEC parameters",
        )
        if not isinstance(parameters, Mapping):
            raise TypeError("Resource-buffer QEC parameters must be a mapping")
        object.__setattr__(self, "location", location)
        object.__setattr__(self, "owner_kind", owner_kind)
        object.__setattr__(self, "qec_code", qec_code)
        object.__setattr__(self, "qec_parameters", deep_freeze_json(parameters))
        object.__setattr__(self, "idle_failure_probability_per_cycle", probability)
        object.__setattr__(self, "idle_cycle_time_s", cycle_time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "location": self.location,
            "owner_kind": self.owner_kind,
            "qec_code": self.qec_code,
            "qec_parameters": normalize_json(self.qec_parameters),
            "logical_qubits_per_token": self.logical_qubits_per_token,
            "idle_failure_probability_per_cycle": (
                self.idle_failure_probability_per_cycle
            ),
            "idle_cycle_time_s": self.idle_cycle_time_s,
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "ResourceBufferFidelityModel":
        if not isinstance(data, Mapping):
            raise TypeError("Resource-buffer fidelity model must be a mapping")
        if any(type(key) is not str for key in data):
            raise TypeError("Resource-buffer fidelity fields must be plain strings")
        required = {
            "location",
            "owner_kind",
            "qec_code",
            "qec_parameters",
            "logical_qubits_per_token",
            "idle_failure_probability_per_cycle",
            "idle_cycle_time_s",
        }
        unknown = set(data) - required
        missing = required - set(data)
        if unknown or missing:
            raise ValueError(
                "Invalid resource-buffer fidelity fields: "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        return cls(**{name: data[name] for name in required})


@dataclass(frozen=True)
class ResourceStateFidelityModel:
    """Output quality and storage-idle models for one resource-token kind."""

    resource_kind: str
    output_failure_probability: float
    protocol_id: str | None = None
    protocol_profile_hash: str | None = None
    buffer_idle_models: Mapping[str, ResourceBufferFidelityModel] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        resource_kind = _plain_nonempty_string(
            self.resource_kind,
            label="Resource fidelity kind",
        ).lower()
        probability = _plain_finite_number(
            self.output_failure_probability,
            label="Resource output failure probability",
        )
        if not 0 <= probability < 1:
            raise ValueError("Resource output failure probability must be in [0, 1)")
        protocol_id = None
        if self.protocol_id is not None:
            protocol_id = _plain_nonempty_string(
                self.protocol_id,
                label="Resource protocol ID",
            )
        profile_hash = None
        if self.protocol_profile_hash is not None:
            profile_hash = _plain_nonempty_string(
                self.protocol_profile_hash,
                label="Resource protocol profile hash",
            )
        if profile_hash is not None and (
            len(profile_hash) != 64
            or any(character not in "0123456789abcdef" for character in profile_hash)
        ):
            raise ValueError(
                "Resource protocol profile hash must be a SHA-256 hex digest or None"
            )
        if (protocol_id is None) != (profile_hash is None):
            raise ValueError(
                "Resource protocol ID and profile hash must be present together"
            )
        if not isinstance(self.buffer_idle_models, Mapping):
            raise TypeError("buffer_idle_models must be a mapping")
        models: dict[str, ResourceBufferFidelityModel] = {}
        for raw_location, model in self.buffer_idle_models.items():
            if type(raw_location) is not str:
                raise TypeError(
                    "buffer_idle_models keys must be plain location strings"
                )
            location = raw_location
            if not isinstance(model, ResourceBufferFidelityModel):
                raise TypeError(
                    "buffer_idle_models values must be ResourceBufferFidelityModel"
                )
            if location != model.location:
                raise ValueError(
                    "Resource-buffer fidelity key must equal its canonical location"
                )
            models[location] = model
        object.__setattr__(self, "resource_kind", resource_kind)
        object.__setattr__(self, "output_failure_probability", probability)
        object.__setattr__(self, "protocol_id", protocol_id)
        object.__setattr__(self, "protocol_profile_hash", profile_hash)
        object.__setattr__(
            self,
            "buffer_idle_models",
            MappingProxyType(dict(sorted(models.items()))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_kind": self.resource_kind,
            "output_failure_probability": self.output_failure_probability,
            "protocol_id": self.protocol_id,
            "protocol_profile_hash": self.protocol_profile_hash,
            "buffer_idle_models": {
                location: model.to_dict()
                for location, model in self.buffer_idle_models.items()
            },
        }

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "ResourceStateFidelityModel":
        if not isinstance(data, Mapping):
            raise TypeError("Resource-state fidelity model must be a mapping")
        if any(type(key) is not str for key in data):
            raise TypeError("Resource-state fidelity fields must be plain strings")
        required = {
            "resource_kind",
            "output_failure_probability",
            "protocol_id",
            "protocol_profile_hash",
            "buffer_idle_models",
        }
        unknown = set(data) - required
        missing = required - set(data)
        if unknown or missing:
            raise ValueError(
                "Invalid resource-state fidelity fields: "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}"
            )
        raw_models = data["buffer_idle_models"]
        if not isinstance(raw_models, Mapping):
            raise TypeError("buffer_idle_models must be a mapping")
        if any(type(location) is not str for location in raw_models):
            raise TypeError("buffer_idle_models keys must be plain strings")
        return cls(
            resource_kind=data["resource_kind"],
            output_failure_probability=data["output_failure_probability"],
            protocol_id=data["protocol_id"],
            protocol_profile_hash=data["protocol_profile_hash"],
            buffer_idle_models={
                location: ResourceBufferFidelityModel.from_dict(model)
                for location, model in raw_models.items()
            },
        )


@dataclass(frozen=True)
class FidelityProfile:
    """Additive fidelity channels selected for one evaluation.

    Architecture-opcode maps contain *additional* opcode-level channels.  Every
    realized Program event and application-relevant Resource event must appear
    in exactly one of those maps for complete coverage; an explicit ``0.0``
    records an intentional zero channel, while omission is unprofiled.  Logical
    operations, Program-qubit idle exposure, and settled resource output/idle
    exposure are audited separately by ``FidelityEstimate.complete_coverage``.
    """

    operation_failure_probability: Mapping[str, float] = field(default_factory=dict)
    operation_failure_models: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )
    logical_operation_failure_probability: Mapping[str, float] = field(
        default_factory=dict
    )
    logical_operation_failure_models: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )
    idle_failure_rate_per_s: Mapping[str, float] = field(default_factory=dict)
    idle_failure_probability_per_cycle: Mapping[str, float] = field(
        default_factory=dict
    )
    idle_cycle_time_s: Mapping[str, float] = field(default_factory=dict)
    resource_state_models: Mapping[str, ResourceStateFidelityModel] = field(
        default_factory=dict
    )
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "operation_failure_probability",
            "operation_failure_models",
            "logical_operation_failure_probability",
            "logical_operation_failure_models",
            "idle_failure_rate_per_s",
            "idle_failure_probability_per_cycle",
            "idle_cycle_time_s",
            "resource_state_models",
            "provenance",
        ):
            if not isinstance(getattr(self, name), Mapping):
                raise TypeError(f"{name} must be a mapping")
        for name in (
            "operation_failure_probability",
            "logical_operation_failure_probability",
            "idle_failure_rate_per_s",
            "idle_failure_probability_per_cycle",
            "idle_cycle_time_s",
        ):
            if any(
                not isinstance(value, (int, float)) or isinstance(value, bool)
                for value in getattr(self, name).values()
            ):
                raise TypeError(f"{name} values must be real numbers")
        operations = {
            str(key): float(value) for key, value in self.operation_failure_probability.items()
        }
        operation_models = {
            str(key): normalize_json(value)
            for key, value in self.operation_failure_models.items()
        }
        logical_operations = {
            str(key).lower(): float(value)
            for key, value in self.logical_operation_failure_probability.items()
        }
        logical_models = {
            str(key).lower(): normalize_json(value)
            for key, value in self.logical_operation_failure_models.items()
        }
        idle = {str(key): float(value) for key, value in self.idle_failure_rate_per_s.items()}
        idle_per_cycle = {
            str(key): float(value)
            for key, value in self.idle_failure_probability_per_cycle.items()
        }
        cycle_time = {
            str(key): float(value) for key, value in self.idle_cycle_time_s.items()
        }
        resource_models: dict[str, ResourceStateFidelityModel] = {}
        for raw_kind, model in self.resource_state_models.items():
            kind = str(raw_kind).strip().lower()
            if not isinstance(model, ResourceStateFidelityModel):
                raise TypeError(
                    "resource_state_models values must be ResourceStateFidelityModel"
                )
            if kind != model.resource_kind:
                raise ValueError(
                    "Resource-state fidelity key must equal its resource_kind"
                )
            resource_models[kind] = model
        probabilities = [
            *operations.values(),
            *logical_operations.values(),
            *idle_per_cycle.values(),
            *(
                float(model["failure_probability"])
                for model in operation_models.values()
                if "failure_probability" in model
            ),
            *(
                float(probability)
                for model in operation_models.values()
                for probability in (
                    model.get("channels", {}).values()
                    if isinstance(model.get("channels", {}), Mapping)
                    else ()
                )
            ),
        ]
        if any(not math.isfinite(value) or not 0 <= value < 1 for value in probabilities):
            raise ValueError("Operation failure probabilities must be in [0, 1)")
        if any(not math.isfinite(value) or value < 0 for value in idle.values()):
            raise ValueError("Idle failure rates must be finite and non-negative")
        if any(not math.isfinite(value) or value <= 0 for value in cycle_time.values()):
            raise ValueError("Idle cycle times must be finite and positive")
        if set(idle_per_cycle) != set(cycle_time):
            raise ValueError(
                "Per-cycle idle failure and cycle-time maps must cover identical locations"
            )
        object.__setattr__(self, "operation_failure_probability", deep_freeze_json(operations))
        object.__setattr__(
            self,
            "operation_failure_models",
            deep_freeze_json(
                strict_json(operation_models, label="operation failure models")
            ),
        )
        object.__setattr__(
            self,
            "logical_operation_failure_probability",
            deep_freeze_json(logical_operations),
        )
        object.__setattr__(
            self,
            "logical_operation_failure_models",
            deep_freeze_json(
                strict_json(
                    logical_models,
                    label="logical-operation failure models",
                )
            ),
        )
        object.__setattr__(self, "idle_failure_rate_per_s", deep_freeze_json(idle))
        object.__setattr__(
            self,
            "idle_failure_probability_per_cycle",
            deep_freeze_json(idle_per_cycle),
        )
        object.__setattr__(self, "idle_cycle_time_s", deep_freeze_json(cycle_time))
        object.__setattr__(
            self,
            "resource_state_models",
            MappingProxyType(dict(sorted(resource_models.items()))),
        )
        object.__setattr__(
            self,
            "provenance",
            deep_freeze_json(
                strict_json(self.provenance, label="fidelity-profile provenance")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_failure_probability": normalize_json(
                self.operation_failure_probability
            ),
            "operation_failure_models": normalize_json(
                self.operation_failure_models
            ),
            "logical_operation_failure_probability": normalize_json(
                self.logical_operation_failure_probability
            ),
            "logical_operation_failure_models": normalize_json(
                self.logical_operation_failure_models
            ),
            "idle_failure_rate_per_s": normalize_json(self.idle_failure_rate_per_s),
            "idle_failure_probability_per_cycle": normalize_json(
                self.idle_failure_probability_per_cycle
            ),
            "idle_cycle_time_s": normalize_json(self.idle_cycle_time_s),
            "resource_state_models": {
                kind: model.to_dict()
                for kind, model in self.resource_state_models.items()
            },
            "provenance": normalize_json(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FidelityProfile":
        allowed = {
            "operation_failure_probability",
            "operation_failure_models",
            "logical_operation_failure_probability",
            "logical_operation_failure_models",
            "idle_failure_rate_per_s",
            "idle_failure_probability_per_cycle",
            "idle_cycle_time_s",
            "resource_state_models",
            "provenance",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(
                f"Unknown fidelity-profile fields: {sorted(unknown)}"
            )
        return cls(
            operation_failure_probability=data.get(
                "operation_failure_probability", {}
            ),
            operation_failure_models=data.get("operation_failure_models", {}),
            logical_operation_failure_probability=data.get(
                "logical_operation_failure_probability", {}
            ),
            logical_operation_failure_models=data.get(
                "logical_operation_failure_models", {}
            ),
            idle_failure_rate_per_s=data.get("idle_failure_rate_per_s", {}),
            idle_failure_probability_per_cycle=data.get(
                "idle_failure_probability_per_cycle", {}
            ),
            idle_cycle_time_s=data.get("idle_cycle_time_s", {}),
            resource_state_models={
                str(kind): ResourceStateFidelityModel.from_dict(model)
                for kind, model in data.get("resource_state_models", {}).items()
            },
            provenance=data.get("provenance", {}),
        )

    @property
    def profile_hash(self) -> str:
        return semantic_hash(self.to_dict())
