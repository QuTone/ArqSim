"""Static QEC protocol semantics, separate from architecture geometry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from functools import lru_cache
import math
from pathlib import Path
from typing import Any, Mapping

import yaml

from arqsim.schema import normalize_json, semantic_hash


MAGIC_STATE_FACTORY_SCHEMA_VERSION = "arqsim.magic-state-factory-catalog.v1"
ENTANGLEMENT_DISTILLATION_SCHEMA_VERSION = (
    "arqsim.entanglement-distillation-catalog.v3"
)
PROTOCOL_PROFILE_DATA_DIR = Path(__file__).with_name("protocol_profiles")


class QECResourceProtocolProfile(ABC):
    """Common catalog contract for fault-tolerant resource protocols."""

    id: str
    outputs_per_batch: int

    @property
    @abstractmethod
    def resource_payload(self) -> str:
        """Return the logical resource produced by this protocol."""

        raise NotImplementedError

    @property
    @abstractmethod
    def profile_hash(self) -> str:
        """Return the content identity of the complete catalog profile."""

        raise NotImplementedError


@dataclass(frozen=True)
class MagicStateFactoryProfile(QECResourceProtocolProfile):
    """One selectable end-to-end magic-state preparation protocol.

    ``cycles_per_batch`` is the mean wall-clock cost in QEC cycles after any
    protocol-level retries already included by the cited source.  It is kept
    separate from ``outputs_per_batch`` because a multi-output factory emits
    one synchronized batch, rather than several unrelated factories.
    """

    id: str
    label: str
    physical_error_probability: float
    output_error_probability: float
    physical_qubits_per_copy: int
    cycles_per_attempt: float
    expected_attempts_per_batch: float
    cycles_per_batch: float
    outputs_per_batch: int
    arrival_model: str = "exponential"
    source: Mapping[str, Any] = field(default_factory=dict)
    assumptions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.label.strip():
            raise ValueError("Magic-state factory id and label must be non-empty")
        probabilities = (
            float(self.physical_error_probability),
            float(self.output_error_probability),
        )
        if any(not math.isfinite(value) or not 0 <= value < 1 for value in probabilities):
            raise ValueError("Magic-state factory probabilities must be in [0, 1)")
        positive = (
            float(self.cycles_per_attempt),
            float(self.expected_attempts_per_batch),
            float(self.cycles_per_batch),
        )
        if any(not math.isfinite(value) or value <= 0 for value in positive):
            raise ValueError("Magic-state factory cycle/attempt costs must be positive")
        if self.physical_qubits_per_copy <= 0 or self.outputs_per_batch <= 0:
            raise ValueError("Magic-state factory space and output multiplicity must be positive")
        if self.arrival_model not in {"deterministic", "exponential", "geometric"}:
            raise ValueError(f"Unsupported magic-state arrival model: {self.arrival_model}")
        object.__setattr__(self, "source", normalize_json(self.source))
        object.__setattr__(self, "assumptions", normalize_json(self.assumptions))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MagicStateFactoryProfile":
        timing = data.get("timing", {})
        output = data.get("output", {})
        implementation = data.get("implementation", {})
        if not all(isinstance(value, Mapping) for value in (timing, output, implementation)):
            raise ValueError("Magic-state timing, output, and implementation must be mappings")
        return cls(
            id=str(data["id"]),
            label=str(data.get("label", data["id"])),
            physical_error_probability=float(data["physical_error_probability"]),
            output_error_probability=float(output["error_probability"]),
            physical_qubits_per_copy=int(implementation["physical_qubits_per_copy"]),
            cycles_per_attempt=float(timing["cycles_per_attempt"]),
            expected_attempts_per_batch=float(timing["expected_attempts_per_batch"]),
            cycles_per_batch=float(timing["cycles_per_batch"]),
            outputs_per_batch=int(output["states_per_batch"]),
            arrival_model=str(timing.get("arrival_model", "exponential")),
            source=data.get("source", {}),
            assumptions=data.get("assumptions", {}),
        )

    def mean_batch_interval_s(self, qec_cycle_time_s: float) -> float:
        cycle = float(qec_cycle_time_s)
        if not math.isfinite(cycle) or cycle <= 0:
            raise ValueError("QEC cycle time must be positive")
        return self.cycles_per_batch * cycle

    @property
    def resource_payload(self) -> str:
        """Architecture payload produced by this protocol family."""

        return "magic_state"

    def to_dict(self) -> dict[str, Any]:
        """Return the complete, hashable protocol-catalog record."""

        return {
            "id": self.id,
            "label": self.label,
            "physical_error_probability": self.physical_error_probability,
            "output": {
                "states_per_batch": self.outputs_per_batch,
                "error_probability": self.output_error_probability,
            },
            "implementation": {
                "physical_qubits_per_copy": self.physical_qubits_per_copy,
            },
            "timing": {
                "cycles_per_attempt": self.cycles_per_attempt,
                "expected_attempts_per_batch": self.expected_attempts_per_batch,
                "cycles_per_batch": self.cycles_per_batch,
                "arrival_model": self.arrival_model,
            },
            "source": normalize_json(self.source),
            "assumptions": normalize_json(self.assumptions),
        }

    @property
    def profile_hash(self) -> str:
        """Content identity of every protocol semantic consumed downstream."""

        return semantic_hash(self.to_dict())

    def layout_policy_overrides(self) -> dict[str, Any]:
        """Fields consumed while sizing the factory and its output buffer."""

        return {
            "protocols.magic_state.id": self.id,
            "protocols.magic_state.outputs_per_copy_per_batch": self.outputs_per_batch,
            "protocols.magic_state.physical_qubits_per_copy": self.physical_qubits_per_copy,
            "protocols.magic_state.qec_cycles_per_batch": self.cycles_per_batch,
        }


@dataclass(frozen=True)
class EntanglementDistillationProfile(QECResourceProtocolProfile):
    """One selectable protocol for preparing logical Bell pairs.

    ``outputs_per_batch`` is an output *multiplicity*, not a rate.  Together
    with the local critical-path cost it defines the local service ceiling.
    Raw physical-Bell supply is modeled independently, so one batch takes

    ``max(C_batch * t_cycle, O_Bell * K_batch / R_physical)``.

    This is deliberately one uniform contract for constant-rate distillation
    and entanglement boosting.  It also keeps the actual physical-link rate as
    an architecture parameter, rather than incorrectly treating it as an
    intrinsic property of the distillation protocol.
    """

    id: str
    label: str
    family: str
    physical_bell_error_probability: float
    physical_bell_fidelity: float
    output_error_probability: float
    output_fidelity: float
    outputs_per_batch: int
    raw_bell_pairs_per_output: float
    qec_cycles_per_batch: float
    qec_cycles_per_output: float
    qec_cycle_time_s: float
    qec_code: str | None
    code_distance: int | None
    logical_qubits_per_copy_per_endpoint: int
    physical_qubits_per_copy_per_endpoint: int | None
    physical_qubits_per_copy_total: int | None
    reference_physical_bell_pair_rate_per_s: float
    ideal_logical_bell_pair_rate_per_s: float
    saturation_physical_bell_pair_rate_per_s: float
    reference_expected_logical_bell_pair_rate_per_s: float
    reference_mean_logical_bell_pair_interval_s: float
    reference_mean_batch_interval_s: float
    arrival_model: str = "exponential"
    source: Mapping[str, Any] = field(default_factory=dict)
    assumptions: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.label.strip() or not self.family.strip():
            raise ValueError("Entanglement-distillation identifiers must be non-empty")
        probabilities = (
            float(self.physical_bell_error_probability),
            float(self.physical_bell_fidelity),
            float(self.output_error_probability),
            float(self.output_fidelity),
        )
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in probabilities):
            raise ValueError("Entanglement-distillation probabilities must be in [0, 1]")
        if not math.isclose(
            self.physical_bell_fidelity,
            1.0 - self.physical_bell_error_probability,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError("Physical Bell fidelity must equal 1 - error probability")
        if not math.isclose(
            self.output_fidelity,
            1.0 - self.output_error_probability,
            rel_tol=0.0,
            abs_tol=1e-15,
        ):
            raise ValueError("Logical Bell fidelity must equal 1 - error probability")
        positive = (
            float(self.raw_bell_pairs_per_output),
            float(self.qec_cycles_per_batch),
            float(self.qec_cycles_per_output),
            float(self.qec_cycle_time_s),
            float(self.reference_physical_bell_pair_rate_per_s),
            float(self.ideal_logical_bell_pair_rate_per_s),
            float(self.saturation_physical_bell_pair_rate_per_s),
            float(self.reference_expected_logical_bell_pair_rate_per_s),
            float(self.reference_mean_logical_bell_pair_interval_s),
            float(self.reference_mean_batch_interval_s),
        )
        if any(not math.isfinite(value) or value <= 0 for value in positive):
            raise ValueError("Bell-protocol time, rate, and consumption must be positive")
        if self.outputs_per_batch <= 0 or self.logical_qubits_per_copy_per_endpoint <= 0:
            raise ValueError("Bell protocol output and endpoint space must be positive")
        if self.code_distance is not None and self.code_distance <= 0:
            raise ValueError("Bell protocol code distance must be positive when fixed")
        if self.qec_code is not None and not self.qec_code.strip():
            raise ValueError("Entanglement-distillation QEC code must be non-empty")
        physical_endpoint = self.physical_qubits_per_copy_per_endpoint
        physical_total = self.physical_qubits_per_copy_total
        if (physical_endpoint is None) != (physical_total is None):
            raise ValueError(
                "Bell-protocol physical endpoint and total footprints must both "
                "be fixed or both be architecture-derived"
            )
        if physical_endpoint is not None:
            if physical_endpoint <= 0:
                raise ValueError("Endpoint physical space must be positive")
            if physical_total != 2 * physical_endpoint:
                raise ValueError(
                    "Total Bell-protocol footprint must equal the two endpoint footprints"
                )
        if self.arrival_model not in {"deterministic", "exponential", "geometric"}:
            raise ValueError(
                f"Unsupported logical-Bell arrival model: {self.arrival_model}"
            )
        expected_cycles_per_output = (
            self.qec_cycles_per_batch / self.outputs_per_batch
        )
        if not math.isclose(
            self.qec_cycles_per_output,
            expected_cycles_per_output,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "qec_cycles_per_output must equal batch cycles divided by "
                "batch output count"
            )
        expected_ideal_rate = self.outputs_per_batch / (
            self.qec_cycles_per_batch * self.qec_cycle_time_s
        )
        expected_saturation_rate = (
            self.raw_bell_pairs_per_output * expected_ideal_rate
        )
        expected_output_rate = min(
            expected_ideal_rate,
            self.reference_physical_bell_pair_rate_per_s
            / self.raw_bell_pairs_per_output,
        )
        expected_pair_interval = 1.0 / expected_output_rate
        expected_batch_interval = self.outputs_per_batch / expected_output_rate
        derived_checks = (
            (
                self.ideal_logical_bell_pair_rate_per_s,
                expected_ideal_rate,
                "ideal_logical_bell_pair_rate_per_s",
            ),
            (
                self.saturation_physical_bell_pair_rate_per_s,
                expected_saturation_rate,
                "saturation_physical_bell_pair_rate_per_s",
            ),
            (
                self.reference_expected_logical_bell_pair_rate_per_s,
                expected_output_rate,
                "reference_expected_logical_bell_pair_rate_per_s",
            ),
            (
                self.reference_mean_logical_bell_pair_interval_s,
                expected_pair_interval,
                "reference_mean_logical_bell_pair_interval_s",
            ),
            (
                self.reference_mean_batch_interval_s,
                expected_batch_interval,
                "reference_mean_batch_interval_s",
            ),
        )
        for actual, expected, label in derived_checks:
            if not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12):
                raise ValueError(
                    f"Canonical {label} is inconsistent with the timing model: "
                    f"{actual} != {expected}"
                )
        object.__setattr__(self, "source", normalize_json(self.source))
        object.__setattr__(self, "assumptions", normalize_json(self.assumptions))

    @property
    def resource_payload(self) -> str:
        """Architecture payload produced by this protocol family."""

        return "bell_pair"

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EntanglementDistillationProfile":
        timing = data.get("timing", {})
        output = data.get("output", {})
        input_spec = data.get("input", {})
        implementation = data.get("implementation", {})
        if not all(
            isinstance(value, Mapping)
            for value in (timing, output, input_spec, implementation)
        ):
            raise ValueError(
                "Entanglement-distillation input, output, timing, and "
                "implementation must be mappings"
            )
        physical_bell = input_spec.get("physical_bell_pair", {})
        if not isinstance(physical_bell, Mapping):
            raise ValueError("input.physical_bell_pair must be a mapping")
        return cls(
            id=str(data["id"]),
            label=str(data.get("label", data["id"])),
            family=str(data["family"]),
            physical_bell_error_probability=float(
                physical_bell["error_probability"]
            ),
            physical_bell_fidelity=float(physical_bell["fidelity"]),
            output_error_probability=float(output["error_probability"]),
            output_fidelity=float(output["fidelity"]),
            outputs_per_batch=int(output["logical_bell_pairs_per_batch"]),
            raw_bell_pairs_per_output=float(
                physical_bell["pairs_per_logical_bell_pair"]
            ),
            qec_cycles_per_batch=float(timing["qec_cycles_per_batch"]),
            qec_cycles_per_output=float(timing["qec_cycles_per_output"]),
            qec_cycle_time_s=float(timing["qec_cycle_time_s"]),
            qec_code=(
                str(implementation["qec_code"])
                if implementation.get("qec_code") is not None
                else None
            ),
            code_distance=(
                int(implementation["code_distance"])
                if implementation.get("code_distance") is not None
                else None
            ),
            logical_qubits_per_copy_per_endpoint=int(
                implementation["logical_qubits_per_copy_per_endpoint"]
            ),
            physical_qubits_per_copy_per_endpoint=(
                int(implementation["physical_qubits_per_copy_per_endpoint"])
                if implementation.get("physical_qubits_per_copy_per_endpoint")
                is not None
                else None
            ),
            physical_qubits_per_copy_total=(
                int(implementation["physical_qubits_per_copy_total"])
                if implementation.get("physical_qubits_per_copy_total") is not None
                else None
            ),
            reference_physical_bell_pair_rate_per_s=float(
                physical_bell["reference_generation_rate_per_s"]
            ),
            ideal_logical_bell_pair_rate_per_s=float(
                timing["ideal_logical_bell_pair_rate_per_s"]
            ),
            saturation_physical_bell_pair_rate_per_s=float(
                timing["saturation_physical_bell_pair_rate_per_s"]
            ),
            reference_expected_logical_bell_pair_rate_per_s=float(
                timing["reference_expected_logical_bell_pair_rate_per_s"]
            ),
            reference_mean_logical_bell_pair_interval_s=float(
                timing["reference_mean_logical_bell_pair_interval_s"]
            ),
            reference_mean_batch_interval_s=float(
                timing["reference_mean_batch_interval_s"]
            ),
            arrival_model=str(timing.get("arrival_model", "exponential")),
            source=data.get("source", {}),
            assumptions=data.get("assumptions", {}),
        )

    def mean_batch_interval_s(
        self,
        physical_bell_pair_rate_per_s: float,
        qec_cycle_time_s: float,
    ) -> float:
        """Mean interval between complete protocol runs.

        The local distillation engine and the raw-link supply run in parallel;
        the larger of their two service intervals is the bottleneck.
        """

        rate = float(physical_bell_pair_rate_per_s)
        cycle = float(qec_cycle_time_s)
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("Physical Bell-pair rate must be positive")
        if not math.isfinite(cycle) or cycle <= 0:
            raise ValueError("QEC cycle time must be positive")
        protocol_interval_s = self.qec_cycles_per_batch * cycle
        physical_supply_interval_s = self.raw_bell_pairs_per_batch / rate
        return max(protocol_interval_s, physical_supply_interval_s)

    @property
    def raw_bell_pairs_per_batch(self) -> float:
        return self.raw_bell_pairs_per_output * self.outputs_per_batch

    def expected_logical_bell_pair_rate_per_s(
        self,
        physical_bell_pair_rate_per_s: float,
        qec_cycle_time_s: float,
    ) -> float:
        return self.outputs_per_batch / (
            self.mean_batch_interval_s(
                physical_bell_pair_rate_per_s,
                qec_cycle_time_s,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the complete, hashable protocol-catalog record."""

        return {
            "id": self.id,
            "label": self.label,
            "family": self.family,
            "input": {
                "physical_bell_pair": {
                    "error_probability": self.physical_bell_error_probability,
                    "fidelity": self.physical_bell_fidelity,
                    "pairs_per_logical_bell_pair": self.raw_bell_pairs_per_output,
                    "reference_generation_rate_per_s": (
                        self.reference_physical_bell_pair_rate_per_s
                    ),
                }
            },
            "output": {
                "error_probability": self.output_error_probability,
                "fidelity": self.output_fidelity,
                "logical_bell_pairs_per_batch": self.outputs_per_batch,
            },
            "implementation": {
                "qec_code": self.qec_code,
                "code_distance": self.code_distance,
                "logical_qubits_per_copy_per_endpoint": (
                    self.logical_qubits_per_copy_per_endpoint
                ),
                "physical_qubits_per_copy_per_endpoint": (
                    self.physical_qubits_per_copy_per_endpoint
                ),
                "physical_qubits_per_copy_total": self.physical_qubits_per_copy_total,
            },
            "timing": {
                "qec_cycles_per_batch": self.qec_cycles_per_batch,
                "qec_cycles_per_output": self.qec_cycles_per_output,
                "qec_cycle_time_s": self.qec_cycle_time_s,
                "ideal_logical_bell_pair_rate_per_s": (
                    self.ideal_logical_bell_pair_rate_per_s
                ),
                "saturation_physical_bell_pair_rate_per_s": (
                    self.saturation_physical_bell_pair_rate_per_s
                ),
                "reference_expected_logical_bell_pair_rate_per_s": (
                    self.reference_expected_logical_bell_pair_rate_per_s
                ),
                "reference_mean_logical_bell_pair_interval_s": (
                    self.reference_mean_logical_bell_pair_interval_s
                ),
                "reference_mean_batch_interval_s": (
                    self.reference_mean_batch_interval_s
                ),
                "arrival_model": self.arrival_model,
            },
            "source": normalize_json(self.source),
            "assumptions": normalize_json(self.assumptions),
        }

    @property
    def profile_hash(self) -> str:
        """Content identity of every protocol semantic consumed downstream."""

        return semantic_hash(self.to_dict())

    def with_distillation_rate_scale(
        self, scale: float
    ) -> "EntanglementDistillationProfile":
        """Return a runtime sensitivity variant with a faster local service.

        The variant preserves batch size, raw-pair consumption, fidelity, and
        physical footprint.  Only the distillation critical path changes, so
        architecture sizing must continue to use the unscaled base profile.
        """

        factor = float(scale)
        if not math.isfinite(factor) or factor <= 0:
            raise ValueError("Entanglement-distillation rate scale must be positive")
        if math.isclose(factor, 1.0):
            return self
        cycles_per_batch = self.qec_cycles_per_batch / factor
        cycles_per_output = cycles_per_batch / self.outputs_per_batch
        ideal_rate = self.outputs_per_batch / (
            cycles_per_batch * self.qec_cycle_time_s
        )
        saturation_rate = self.raw_bell_pairs_per_output * ideal_rate
        reference_rate = min(
            ideal_rate,
            self.reference_physical_bell_pair_rate_per_s
            / self.raw_bell_pairs_per_output,
        )
        return replace(
            self,
            qec_cycles_per_batch=cycles_per_batch,
            qec_cycles_per_output=cycles_per_output,
            ideal_logical_bell_pair_rate_per_s=ideal_rate,
            saturation_physical_bell_pair_rate_per_s=saturation_rate,
            reference_expected_logical_bell_pair_rate_per_s=reference_rate,
            reference_mean_logical_bell_pair_interval_s=1.0 / reference_rate,
            reference_mean_batch_interval_s=(
                self.outputs_per_batch / reference_rate
            ),
            source={
                **dict(self.source),
                "sensitivity_distillation_rate_scale": factor,
            },
        )

    def layout_policy_overrides(self) -> dict[str, Any]:
        """Fields consumed while sizing a selectable logical-Bell engine."""

        overrides: dict[str, Any] = {
            "protocols.entanglement_distillation.id": self.id,
            "protocols.entanglement_distillation.outputs_per_copy_per_batch": (
                self.outputs_per_batch
            ),
            "protocols.entanglement_distillation.logical_qubits_per_copy_per_endpoint": (
                self.logical_qubits_per_copy_per_endpoint
            ),
            "protocols.entanglement_distillation.qec_cycles_per_batch": (
                self.qec_cycles_per_batch
            ),
            "protocols.entanglement_distillation.qec_cycle_time_s": (
                self.qec_cycle_time_s
            ),
            "protocols.entanglement_distillation.raw_bell_pairs_per_output": (
                self.raw_bell_pairs_per_output
            ),
            "protocols.entanglement_distillation.reference_physical_bell_pair_rate_per_s": (
                self.reference_physical_bell_pair_rate_per_s
            ),
        }
        if self.physical_qubits_per_copy_per_endpoint is not None:
            overrides[
                "protocols.entanglement_distillation.physical_qubits_per_copy_per_endpoint"
            ] = self.physical_qubits_per_copy_per_endpoint
        return overrides


def load_magic_state_factory_catalog(path: Path | str) -> dict[str, MagicStateFactoryProfile]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Magic-state factory catalog must be a mapping")
    if payload.get("schema_version") != MAGIC_STATE_FACTORY_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported magic-state factory catalog schema: {payload.get('schema_version')}"
        )
    profiles: dict[str, MagicStateFactoryProfile] = {}
    for raw in payload.get("profiles", ()):
        if not isinstance(raw, Mapping):
            raise ValueError("Each magic-state factory profile must be a mapping")
        profile = MagicStateFactoryProfile.from_dict(raw)
        if profile.id in profiles:
            raise ValueError(f"Duplicate magic-state factory profile: {profile.id}")
        profiles[profile.id] = profile
    return profiles


@lru_cache(maxsize=1)
def magic_state_factory_profiles() -> Mapping[str, MagicStateFactoryProfile]:
    return load_magic_state_factory_catalog(
        PROTOCOL_PROFILE_DATA_DIR / "magic_state_factories.yaml"
    )


def get_magic_state_factory_profile(profile_id: str) -> MagicStateFactoryProfile:
    try:
        return magic_state_factory_profiles()[profile_id]
    except KeyError as exc:
        raise ValueError(f"Unknown magic-state factory profile: {profile_id}") from exc


def load_entanglement_distillation_catalog(
    path: Path | str,
) -> dict[str, EntanglementDistillationProfile]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Entanglement-distillation catalog must be a mapping")
    if payload.get("schema_version") != ENTANGLEMENT_DISTILLATION_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported entanglement-distillation catalog schema: "
            f"{payload.get('schema_version')}"
        )
    profiles: dict[str, EntanglementDistillationProfile] = {}
    for raw in payload.get("profiles", ()):
        if not isinstance(raw, Mapping):
            raise ValueError("Each entanglement-distillation profile must be a mapping")
        profile = EntanglementDistillationProfile.from_dict(raw)
        if profile.id in profiles:
            raise ValueError(f"Duplicate entanglement-distillation profile: {profile.id}")
        profiles[profile.id] = profile
    return profiles


@lru_cache(maxsize=1)
def entanglement_distillation_profiles(
) -> Mapping[str, EntanglementDistillationProfile]:
    return load_entanglement_distillation_catalog(
        PROTOCOL_PROFILE_DATA_DIR / "entanglement_distillation.yaml"
    )


def get_entanglement_distillation_profile(
    profile_id: str,
) -> EntanglementDistillationProfile:
    try:
        return entanglement_distillation_profiles()[profile_id]
    except KeyError as exc:
        raise ValueError(
            f"Unknown entanglement-distillation profile: {profile_id}"
        ) from exc


__all__ = [
    "ENTANGLEMENT_DISTILLATION_SCHEMA_VERSION",
    "MAGIC_STATE_FACTORY_SCHEMA_VERSION",
    "EntanglementDistillationProfile",
    "MagicStateFactoryProfile",
    "QECResourceProtocolProfile",
    "entanglement_distillation_profiles",
    "get_entanglement_distillation_profile",
    "get_magic_state_factory_profile",
    "load_entanglement_distillation_catalog",
    "load_magic_state_factory_catalog",
    "magic_state_factory_profiles",
]
