"""Resolved timing, quality, and footprint semantics for resource protocols.

The architecture layout chooses protocol copies and capacities, while the QEC
catalog owns a protocol's timing law and output quality.  This module joins
those inputs once so that runtime latency, footprint, and fidelity cannot each
silently select an unrelated source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from types import MappingProxyType
from typing import Any, Mapping, TYPE_CHECKING

from heteqsys.architecture.specification import (
    ArchitectureSpecification,
    Submodule,
)
from heteqsys.qec.protocol import (
    ENTANGLEMENT_DISTILLATION_SCHEMA_VERSION,
    MAGIC_STATE_FACTORY_SCHEMA_VERSION,
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
)
from heteqsys.schema import deep_freeze_json, normalize_json, semantic_hash, strict_json

from .arrival_distribution import ARRIVAL_DISTRIBUTIONS, ArrivalDistribution

if TYPE_CHECKING:
    from heteqsys.operation_profiles.latency_profile import OperationLatencyProfile


RESOURCE_PROTOCOL_BINDING_SCHEMA_VERSION = (
    "arqsim.resolved-resource-protocol-binding.v1"
)
RESOURCE_PROTOCOL_BINDINGS_SCHEMA_VERSION = (
    "arqsim.resolved-resource-protocol-bindings.v1"
)

MAGIC_STATE_RESOURCE = "magic_state"
LOGICAL_BELL_PAIR_RESOURCE = "logical_bell_pair"
RESOURCE_KINDS = frozenset({MAGIC_STATE_RESOURCE, LOGICAL_BELL_PAIR_RESOURCE})
ARRIVAL_SOURCES = frozenset(
    {"protocol_profile", "derived_kind_override", "explicit_arrival_override"}
)

def _positive_integer(value: Any, *, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_float(value: Any, *, name: str) -> float:
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        raise TypeError(f"{name} must be a real number")
    try:
        result = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a real number") from exc
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


def _mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value


def _reject_unknown(data: Mapping[str, Any], allowed: set[str], *, name: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unknown {name} fields: {sorted(unknown)}")


def _arrival_with_kind(
    arrival: ArrivalDistribution,
    kind: str,
) -> ArrivalDistribution:
    normalized = str(kind).strip().lower()
    if normalized not in ARRIVAL_DISTRIBUTIONS - {"trace"}:
        raise ValueError(
            "derived_arrival_kind must be deterministic, exponential, or geometric"
        )
    return ArrivalDistribution(
        kind=normalized,
        mean_interval_s=arrival.mean_interval_s,
        success_probability=arrival.success_probability,
        initial_delay_s=arrival.initial_delay_s,
        initial_delay_samples=arrival.initial_delay_samples,
    )


@dataclass(frozen=True)
class ResolvedResourceProtocolBinding:
    """One immutable, effective resource-producer contract.

    An arrival interval describes one protocol copy completing one synchronized
    batch.  ``copies`` and ``outputs_per_copy_per_batch`` therefore remain
    separate rather than being folded into an aggregate rate.
    """

    resource_kind: str
    requested_protocol_id: str
    protocol_id: str
    protocol_family: str | None
    protocol_profile_hash: str
    copies: int
    outputs_per_copy_per_batch: int
    base_arrival_distribution: ArrivalDistribution
    effective_arrival_distribution: ArrivalDistribution
    arrival_source: str
    output_error_probability: float
    output_fidelity: float
    physical_footprint: Mapping[str, Any] = field(default_factory=dict)
    operating_point: Mapping[str, Any] = field(default_factory=dict)
    source: Mapping[str, Any] = field(default_factory=dict)
    assumptions: Mapping[str, Any] = field(default_factory=dict)
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        kind = str(self.resource_kind).strip().lower()
        if kind not in RESOURCE_KINDS:
            raise ValueError(f"Unsupported resource protocol kind: {self.resource_kind}")
        requested = str(self.requested_protocol_id).strip()
        protocol = str(self.protocol_id).strip()
        family = (
            str(self.protocol_family).strip()
            if self.protocol_family is not None
            else None
        )
        if not requested or not protocol:
            raise ValueError("Resource protocol IDs must be non-empty")
        if family == "":
            raise ValueError("Resource protocol family must be non-empty or None")
        profile_hash = str(self.protocol_profile_hash).strip()
        if len(profile_hash) != 64 or any(
            character not in "0123456789abcdef" for character in profile_hash
        ):
            raise ValueError("protocol_profile_hash must be a SHA-256 hex digest")
        copies = _positive_integer(self.copies, name="copies")
        outputs = _positive_integer(
            self.outputs_per_copy_per_batch,
            name="outputs_per_copy_per_batch",
        )
        if not isinstance(self.base_arrival_distribution, ArrivalDistribution):
            raise TypeError(
                "base_arrival_distribution must be an ArrivalDistribution"
            )
        if not isinstance(self.effective_arrival_distribution, ArrivalDistribution):
            raise TypeError(
                "effective_arrival_distribution must be an ArrivalDistribution"
            )
        arrival_source = str(self.arrival_source).strip().lower()
        if arrival_source not in ARRIVAL_SOURCES:
            raise ValueError(f"Unsupported arrival source: {self.arrival_source}")
        if (
            arrival_source == "protocol_profile"
            and self.effective_arrival_distribution
            != self.base_arrival_distribution
        ):
            raise ValueError(
                "A protocol-profile arrival must equal its base distribution"
            )
        error = float(self.output_error_probability)
        fidelity = float(self.output_fidelity)
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in (error, fidelity)):
            raise ValueError("Resource output probability and fidelity must be in [0, 1]")
        if not math.isclose(fidelity, 1.0 - error, rel_tol=0.0, abs_tol=1e-15):
            raise ValueError("Resource output fidelity must equal 1 - error probability")

        object.__setattr__(self, "resource_kind", kind)
        object.__setattr__(self, "requested_protocol_id", requested)
        object.__setattr__(self, "protocol_id", protocol)
        object.__setattr__(self, "protocol_family", family)
        object.__setattr__(self, "protocol_profile_hash", profile_hash)
        object.__setattr__(self, "copies", copies)
        object.__setattr__(self, "outputs_per_copy_per_batch", outputs)
        object.__setattr__(self, "arrival_source", arrival_source)
        object.__setattr__(self, "output_error_probability", error)
        object.__setattr__(self, "output_fidelity", fidelity)
        for name in (
            "physical_footprint",
            "operating_point",
            "source",
            "assumptions",
            "provenance",
        ):
            value = strict_json(getattr(self, name), label=name.replace("_", " "))
            if not isinstance(value, Mapping):
                raise TypeError(f"{name} must be a mapping")
            object.__setattr__(self, name, deep_freeze_json(value))

    def to_dict(self, *, include_binding_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": RESOURCE_PROTOCOL_BINDING_SCHEMA_VERSION,
            "resource_kind": self.resource_kind,
            "requested_protocol_id": self.requested_protocol_id,
            "protocol_id": self.protocol_id,
            "protocol_family": self.protocol_family,
            "protocol_profile_hash": self.protocol_profile_hash,
            "copies": self.copies,
            "outputs_per_copy_per_batch": self.outputs_per_copy_per_batch,
            "base_arrival_distribution": self.base_arrival_distribution.to_dict(),
            "effective_arrival_distribution": (
                self.effective_arrival_distribution.to_dict()
            ),
            "arrival_source": self.arrival_source,
            "output_error_probability": self.output_error_probability,
            "output_fidelity": self.output_fidelity,
            "physical_footprint": normalize_json(self.physical_footprint),
            "operating_point": normalize_json(self.operating_point),
            "source": normalize_json(self.source),
            "assumptions": normalize_json(self.assumptions),
            "provenance": normalize_json(self.provenance),
        }
        if include_binding_hash:
            result["binding_hash"] = self.binding_hash
        return result

    @property
    def binding_hash(self) -> str:
        return semantic_hash(self.to_dict(include_binding_hash=False))

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "ResolvedResourceProtocolBinding":
        if not isinstance(data, Mapping):
            raise TypeError("Resource protocol binding must be a mapping")
        allowed = {
            "schema_version",
            "resource_kind",
            "requested_protocol_id",
            "protocol_id",
            "protocol_family",
            "protocol_profile_hash",
            "copies",
            "outputs_per_copy_per_batch",
            "base_arrival_distribution",
            "effective_arrival_distribution",
            "arrival_source",
            "output_error_probability",
            "output_fidelity",
            "physical_footprint",
            "operating_point",
            "source",
            "assumptions",
            "provenance",
            "binding_hash",
        }
        _reject_unknown(data, allowed, name="resource-protocol-binding")
        if data.get("schema_version") != RESOURCE_PROTOCOL_BINDING_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported resource-protocol-binding schema: "
                f"{data.get('schema_version')}"
            )
        required = allowed - {"binding_hash"}
        missing = sorted(required - set(data))
        if missing:
            raise ValueError(f"Missing resource-protocol-binding fields: {missing}")
        result = cls(
            resource_kind=data["resource_kind"],
            requested_protocol_id=data["requested_protocol_id"],
            protocol_id=data["protocol_id"],
            protocol_family=data["protocol_family"],
            protocol_profile_hash=data["protocol_profile_hash"],
            copies=data["copies"],
            outputs_per_copy_per_batch=data["outputs_per_copy_per_batch"],
            base_arrival_distribution=ArrivalDistribution.from_dict(
                _mapping(
                    data["base_arrival_distribution"],
                    name="base_arrival_distribution",
                )
            ),
            effective_arrival_distribution=ArrivalDistribution.from_dict(
                _mapping(
                    data["effective_arrival_distribution"],
                    name="effective_arrival_distribution",
                )
            ),
            arrival_source=data["arrival_source"],
            output_error_probability=data["output_error_probability"],
            output_fidelity=data["output_fidelity"],
            physical_footprint=_mapping(
                data["physical_footprint"], name="physical_footprint"
            ),
            operating_point=_mapping(
                data["operating_point"], name="operating_point"
            ),
            source=_mapping(data["source"], name="source"),
            assumptions=_mapping(data["assumptions"], name="assumptions"),
            provenance=_mapping(data["provenance"], name="provenance"),
        )
        expected_hash = data.get("binding_hash")
        if expected_hash is not None and expected_hash != result.binding_hash:
            raise ValueError("Resource protocol binding hash does not match its content")
        return result


@dataclass(frozen=True)
class ResolvedResourceProtocolBindings:
    """Hash-stable collection keyed by resource kind."""

    bindings: Mapping[str, ResolvedResourceProtocolBinding]

    def __post_init__(self) -> None:
        if not isinstance(self.bindings, Mapping):
            raise TypeError("Resource protocol bindings must be a mapping")
        normalized: dict[str, ResolvedResourceProtocolBinding] = {}
        for raw_kind, binding in self.bindings.items():
            kind = str(raw_kind).strip().lower()
            if kind not in RESOURCE_KINDS:
                raise ValueError(f"Unsupported resource protocol kind: {raw_kind}")
            if not isinstance(binding, ResolvedResourceProtocolBinding):
                raise TypeError(
                    "Each resource protocol binding must be a "
                    "ResolvedResourceProtocolBinding"
                )
            if kind != binding.resource_kind:
                raise ValueError(
                    f"Binding key {kind} does not match resource kind "
                    f"{binding.resource_kind}"
                )
            normalized[kind] = binding
        object.__setattr__(
            self,
            "bindings",
            MappingProxyType(dict(sorted(normalized.items()))),
        )

    def __getitem__(self, resource_kind: str) -> ResolvedResourceProtocolBinding:
        return self.bindings[resource_kind]

    def get(
        self,
        resource_kind: str,
        default: ResolvedResourceProtocolBinding | None = None,
    ) -> ResolvedResourceProtocolBinding | None:
        return self.bindings.get(resource_kind, default)

    @property
    def magic_state(self) -> ResolvedResourceProtocolBinding | None:
        return self.bindings.get(MAGIC_STATE_RESOURCE)

    @property
    def logical_bell_pair(self) -> ResolvedResourceProtocolBinding | None:
        return self.bindings.get(LOGICAL_BELL_PAIR_RESOURCE)

    def to_dict(self, *, include_bindings_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": RESOURCE_PROTOCOL_BINDINGS_SCHEMA_VERSION,
            "bindings": {
                kind: binding.to_dict()
                for kind, binding in self.bindings.items()
            },
        }
        if include_bindings_hash:
            result["bindings_hash"] = self.bindings_hash
        return result

    @property
    def bindings_hash(self) -> str:
        return semantic_hash(self.to_dict(include_bindings_hash=False))

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "ResolvedResourceProtocolBindings":
        if not isinstance(data, Mapping):
            raise TypeError("Resolved resource protocol bindings must be a mapping")
        _reject_unknown(
            data,
            {"schema_version", "bindings", "bindings_hash"},
            name="resolved-resource-protocol-bindings",
        )
        if data.get("schema_version") != RESOURCE_PROTOCOL_BINDINGS_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported resolved-resource-protocol-bindings schema: "
                f"{data.get('schema_version')}"
            )
        if "bindings" not in data:
            raise ValueError("Resolved resource protocol bindings need bindings")
        raw_bindings = _mapping(data["bindings"], name="bindings")
        result = cls(
            {
                str(kind): ResolvedResourceProtocolBinding.from_dict(
                    _mapping(value, name=f"bindings.{kind}")
                )
                for kind, value in raw_bindings.items()
            }
        )
        expected_hash = data.get("bindings_hash")
        if expected_hash is not None and expected_hash != result.bindings_hash:
            raise ValueError(
                "Resolved resource protocol bindings hash does not match its content"
            )
        return result


@dataclass(frozen=True, slots=True)
class _ResourceEngine:
    """Private qualified view of one canonical resource engine."""

    owner_id: str
    owner_kind: str
    module_id: str
    submodule: Submodule
    modality: str | None

    @property
    def qualified_ref(self) -> str:
        return f"{self.owner_id}/{self.module_id}/{self.submodule.id}"


def _resource_engines(
    specification: ArchitectureSpecification,
) -> tuple[_ResourceEngine, ...]:
    engines: list[_ResourceEngine] = []
    for node in specification.nodes:
        for module in node.modules:
            for submodule in module.submodules:
                if submodule.type == "engine":
                    engines.append(
                        _ResourceEngine(
                            owner_id=node.id,
                            owner_kind="node",
                            module_id=module.id,
                            submodule=submodule,
                            modality=node.modality,
                        )
                    )
    for interconnect in specification.interconnects:
        for module in interconnect.modules:
            for submodule in module.submodules:
                if submodule.type == "engine":
                    engines.append(
                        _ResourceEngine(
                            owner_id=interconnect.id,
                            owner_kind="interconnect",
                            module_id=module.id,
                            submodule=submodule,
                            modality=None,
                        )
                    )
    return tuple(engines)


def _single_engine(
    engines: tuple[_ResourceEngine, ...],
    *,
    payload: str,
) -> _ResourceEngine | None:
    matches = tuple(
        engine for engine in engines if engine.submodule.payload == payload
    )
    if len(matches) > 1:
        raise ValueError(
            f"Runtime resource binding currently requires at most one {payload} "
            "engine"
        )
    return matches[0] if matches else None


def _require_protocol_ref(engine: _ResourceEngine):
    protocol_ref = engine.submodule.resource_protocol
    if protocol_ref is None:
        raise ValueError(
            f"Resource engine {engine.qualified_ref} has no QEC protocol reference"
        )
    return protocol_ref


def _magic_state_binding(
    engine: _ResourceEngine | None,
    latency_profile: "OperationLatencyProfile",
    *,
    architecture_hash: str,
) -> ResolvedResourceProtocolBinding | None:
    if engine is None:
        return None
    if engine.owner_kind != "node" or engine.modality is None:
        raise ValueError("A magic-state engine must be owned by one hardware Node")
    protocol_ref = _require_protocol_ref(engine)
    profile = get_magic_state_factory_profile(protocol_ref.id)
    if profile.resource_payload != engine.submodule.payload:
        raise ValueError(
            f"Protocol {profile.id} produces {profile.resource_payload}, not "
            f"{engine.submodule.payload}"
        )
    if protocol_ref.profile_hash != profile.profile_hash:
        raise ValueError(
            f"Protocol profile hash mismatch for {engine.qualified_ref}"
        )
    copies = _positive_integer(
        engine.submodule.capacity,
        name=f"{engine.qualified_ref} copies",
    )
    try:
        raw_cycle_time = latency_profile.gate_duration_s[engine.modality]
    except KeyError as exc:
        raise ValueError(
            f"No QEC cycle time is available for modality {engine.modality}"
        ) from exc
    cycle_time = _positive_float(
        raw_cycle_time,
        name=f"{engine.modality} QEC cycle time",
    )
    mean_batch_interval = profile.cycles_per_batch * cycle_time
    base_arrival = ArrivalDistribution(
        kind=profile.arrival_model,
        mean_interval_s=mean_batch_interval,
    )
    return ResolvedResourceProtocolBinding(
        resource_kind=MAGIC_STATE_RESOURCE,
        requested_protocol_id=profile.id,
        protocol_id=profile.id,
        protocol_family="magic_state_factory",
        protocol_profile_hash=profile.profile_hash,
        copies=copies,
        outputs_per_copy_per_batch=profile.outputs_per_batch,
        base_arrival_distribution=base_arrival,
        effective_arrival_distribution=base_arrival,
        arrival_source="protocol_profile",
        output_error_probability=profile.output_error_probability,
        output_fidelity=1.0 - profile.output_error_probability,
        physical_footprint={
            "physical_qubits_per_copy": profile.physical_qubits_per_copy,
            "physical_qubits_total": profile.physical_qubits_per_copy * copies,
        },
        operating_point={
            "factory_modality": engine.modality,
            "qec_cycle_time_s": cycle_time,
            "qec_cycles_per_batch": profile.cycles_per_batch,
            "mean_batch_interval_s_per_copy": mean_batch_interval,
            "aggregate_mean_output_rate_per_s": (
                copies * profile.outputs_per_batch / mean_batch_interval
            ),
        },
        source=profile.source,
        assumptions=profile.assumptions,
        provenance={
            "architecture_hash": architecture_hash,
            "catalog_schema_version": MAGIC_STATE_FACTORY_SCHEMA_VERSION,
            "engine": engine.qualified_ref,
        },
    )


def _logical_bell_binding(
    engine: _ResourceEngine | None,
    *,
    architecture_hash: str,
) -> ResolvedResourceProtocolBinding | None:
    if engine is None:
        return None
    if engine.owner_kind != "interconnect":
        raise ValueError("A logical-Bell engine must be owned by an Interconnect")
    protocol_ref = _require_protocol_ref(engine)
    profile = get_entanglement_distillation_profile(protocol_ref.id)
    if profile.resource_payload != engine.submodule.payload:
        raise ValueError(
            f"Protocol {profile.id} produces {profile.resource_payload}, not "
            f"{engine.submodule.payload}"
        )
    if protocol_ref.profile_hash != profile.profile_hash:
        raise ValueError(
            f"Protocol profile hash mismatch for {engine.qualified_ref}"
        )
    copies = _positive_integer(
        engine.submodule.capacity,
        name=f"{engine.qualified_ref} copies",
    )
    per_lane_physical_rate = (
        profile.reference_physical_bell_pair_rate_per_s / copies
    )
    local_interval = profile.qec_cycles_per_batch * profile.qec_cycle_time_s
    supply_interval = (
        profile.raw_bell_pairs_per_output
        * profile.outputs_per_batch
        / per_lane_physical_rate
    )
    mean_batch_interval = max(local_interval, supply_interval)
    # The steady-state interval overlaps raw-pair accumulation with the local
    # distillation circuit.  A cold protocol copy has no preceding batch to
    # provide that overlap, so its first completion also pays the smaller of
    # the two service components.  The first ``copies`` producer samples are
    # the first batch from each synchronized protocol copy.
    cold_start_extra = min(local_interval, supply_interval)
    base_arrival = ArrivalDistribution(
        kind=profile.arrival_model,
        mean_interval_s=mean_batch_interval,
        initial_delay_s=cold_start_extra,
        initial_delay_samples=copies,
    )
    physical_qubits = profile.physical_qubits_per_copy_per_endpoint
    return ResolvedResourceProtocolBinding(
        resource_kind=LOGICAL_BELL_PAIR_RESOURCE,
        requested_protocol_id=profile.id,
        protocol_id=profile.id,
        protocol_family=profile.family,
        protocol_profile_hash=profile.profile_hash,
        copies=copies,
        outputs_per_copy_per_batch=profile.outputs_per_batch,
        base_arrival_distribution=base_arrival,
        effective_arrival_distribution=base_arrival,
        arrival_source="protocol_profile",
        output_error_probability=profile.output_error_probability,
        output_fidelity=profile.output_fidelity,
        physical_footprint={
            "logical_qubits_per_copy_per_endpoint": (
                profile.logical_qubits_per_copy_per_endpoint
            ),
            "logical_qubits_total_per_endpoint": (
                profile.logical_qubits_per_copy_per_endpoint * copies
            ),
            "physical_qubits_per_copy_per_endpoint": physical_qubits,
            "physical_qubits_total_per_endpoint": (
                physical_qubits * copies if physical_qubits is not None else None
            ),
        },
        operating_point={
            "qec_cycle_time_s": profile.qec_cycle_time_s,
            "qec_cycles_per_batch": profile.qec_cycles_per_batch,
            "raw_bell_pairs_per_output": profile.raw_bell_pairs_per_output,
            "shared_physical_bell_pair_rate_per_s": (
                profile.reference_physical_bell_pair_rate_per_s
            ),
            "per_lane_physical_bell_pair_rate_per_s": per_lane_physical_rate,
            "local_batch_interval_s": local_interval,
            "physical_supply_batch_interval_s": supply_interval,
            "mean_batch_interval_s_per_copy": mean_batch_interval,
            "cold_start_extra_per_copy_s": cold_start_extra,
            "cold_start_delayed_samples": copies,
            "expected_first_batch_latency_s": (
                mean_batch_interval + cold_start_extra
            ),
            "aggregate_mean_output_rate_per_s": (
                copies * profile.outputs_per_batch / mean_batch_interval
            ),
        },
        source=profile.source,
        assumptions=profile.assumptions,
        provenance={
            "architecture_hash": architecture_hash,
            "catalog_schema_version": ENTANGLEMENT_DISTILLATION_SCHEMA_VERSION,
            "engine": engine.qualified_ref,
        },
    )


def resolve_resource_protocol_bindings(
    specification: ArchitectureSpecification,
    latency_profile: "OperationLatencyProfile",
) -> ResolvedResourceProtocolBindings:
    """Hydrate canonical engine references into effective resource contracts."""

    from .latency_profile import OperationLatencyProfile

    if not isinstance(specification, ArchitectureSpecification):
        raise TypeError("specification must be an ArchitectureSpecification")
    if not isinstance(latency_profile, OperationLatencyProfile):
        raise TypeError("latency_profile must be an OperationLatencyProfile")
    engines = _resource_engines(specification)
    unsupported = sorted(
        engine.qualified_ref
        for engine in engines
        if engine.submodule.payload not in {"magic_state", "bell_pair"}
    )
    if unsupported:
        raise ValueError(f"Unsupported resource-engine payloads: {unsupported}")
    bindings: dict[str, ResolvedResourceProtocolBinding] = {}
    magic = _magic_state_binding(
        _single_engine(engines, payload="magic_state"),
        latency_profile,
        architecture_hash=specification.architecture_hash,
    )
    if magic is not None:
        bindings[magic.resource_kind] = magic
    bell = _logical_bell_binding(
        _single_engine(engines, payload="bell_pair"),
        architecture_hash=specification.architecture_hash,
    )
    if bell is not None:
        bindings[bell.resource_kind] = bell
    return ResolvedResourceProtocolBindings(bindings)


def apply_arrival_overrides(
    bindings: ResolvedResourceProtocolBindings,
    *,
    magic_state_arrival: ArrivalDistribution | None = None,
    bell_pair_arrival: ArrivalDistribution | None = None,
    derived_arrival_kind: str | None = None,
) -> ResolvedResourceProtocolBindings:
    """Apply explicit timing sensitivity requests without losing base values."""

    if not isinstance(bindings, ResolvedResourceProtocolBindings):
        raise TypeError("bindings must be ResolvedResourceProtocolBindings")
    for name, arrival in (
        ("magic_state_arrival", magic_state_arrival),
        ("bell_pair_arrival", bell_pair_arrival),
    ):
        if arrival is not None and not isinstance(arrival, ArrivalDistribution):
            raise TypeError(f"{name} must be an ArrivalDistribution or None")
    if derived_arrival_kind is not None:
        normalized_kind = str(derived_arrival_kind).strip().lower()
        if normalized_kind not in ARRIVAL_DISTRIBUTIONS - {"trace"}:
            raise ValueError(
                "derived_arrival_kind must be deterministic, exponential, or geometric"
            )
    else:
        normalized_kind = None

    overrides = {
        MAGIC_STATE_RESOURCE: magic_state_arrival,
        LOGICAL_BELL_PAIR_RESOURCE: bell_pair_arrival,
    }
    resolved = {}
    for kind, binding in bindings.bindings.items():
        explicit = overrides[kind]
        if explicit is not None:
            effective = explicit
            arrival_source = "explicit_arrival_override"
        elif normalized_kind is not None:
            effective = _arrival_with_kind(
                binding.base_arrival_distribution,
                normalized_kind,
            )
            arrival_source = "derived_kind_override"
        else:
            effective = binding.base_arrival_distribution
            arrival_source = "protocol_profile"
        resolved[kind] = replace(
            binding,
            effective_arrival_distribution=effective,
            arrival_source=arrival_source,
            provenance={
                **normalize_json(binding.provenance),
                "arrival_resolution": {
                    "source": arrival_source,
                    "base_arrival_hash": semantic_hash(
                        binding.base_arrival_distribution.to_dict()
                    ),
                    "effective_arrival_hash": semantic_hash(effective.to_dict()),
                },
            },
        )
    return ResolvedResourceProtocolBindings(resolved)


def effective_resource_protocol_bindings(
    latency_profile: "OperationLatencyProfile",
    bindings: ResolvedResourceProtocolBindings,
) -> ResolvedResourceProtocolBindings:
    """Resolve nullable latency requests against protocol-derived arrivals."""

    # Local import avoids making the operation-profile package initializer
    # depend on module import order.
    from .latency_profile import OperationLatencyProfile

    if not isinstance(latency_profile, OperationLatencyProfile):
        raise TypeError("latency_profile must be an OperationLatencyProfile")
    return apply_arrival_overrides(
        bindings,
        magic_state_arrival=latency_profile.magic_state_arrival,
        bell_pair_arrival=latency_profile.bell_pair_arrival,
        derived_arrival_kind=latency_profile.derived_arrival_kind,
    )


def with_effective_arrivals(
    latency_profile: "OperationLatencyProfile",
    bindings: ResolvedResourceProtocolBindings,
) -> "OperationLatencyProfile":
    """Materialize runtime arrivals while retaining their binding receipt.

    Explicit distributions in ``latency_profile`` win.  Otherwise each
    resource uses its protocol-derived interval, optionally with the requested
    ``derived_arrival_kind``.  The returned profile contains concrete arrivals
    and clears that request-only hint.
    """

    from .latency_profile import OperationLatencyProfile

    if not isinstance(latency_profile, OperationLatencyProfile):
        raise TypeError("latency_profile must be an OperationLatencyProfile")
    effective = effective_resource_protocol_bindings(latency_profile, bindings)
    magic = effective.magic_state
    bell = effective.logical_bell_pair
    return replace(
        latency_profile,
        magic_state_arrival=(
            magic.effective_arrival_distribution
            if magic is not None
            else latency_profile.magic_state_arrival
        ),
        bell_pair_arrival=(
            bell.effective_arrival_distribution
            if bell is not None
            else latency_profile.bell_pair_arrival
        ),
        derived_arrival_kind=None,
        provenance={
            **normalize_json(latency_profile.provenance),
            "resource_protocol_bindings_hash": effective.bindings_hash,
            "resource_arrival_sources": {
                kind: binding.arrival_source
                for kind, binding in effective.bindings.items()
            },
        },
    )


__all__ = [
    "ARRIVAL_SOURCES",
    "LOGICAL_BELL_PAIR_RESOURCE",
    "MAGIC_STATE_RESOURCE",
    "RESOURCE_PROTOCOL_BINDING_SCHEMA_VERSION",
    "RESOURCE_PROTOCOL_BINDINGS_SCHEMA_VERSION",
    "ResolvedResourceProtocolBinding",
    "ResolvedResourceProtocolBindings",
    "apply_arrival_overrides",
    "effective_resource_protocol_bindings",
    "resolve_resource_protocol_bindings",
    "with_effective_arrivals",
]
