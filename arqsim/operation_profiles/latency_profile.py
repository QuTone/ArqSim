"""Simulation- or literature-derived operation timing inputs."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping

from arqsim.schema import deep_freeze_json, normalize_json, semantic_hash, strict_json

from .arrival_distribution import ArrivalDistribution
from .movement_profile import NeutralAtomMovementProfile


GBC_TRANSVERSAL_PROTOCOL = "gbc_transversal"
PBC_LATTICE_SURGERY_PROTOCOL = "pbc_lattice_surgery"
BB_SURFACE_TRANSFER_PROTOCOL = "bb_surface_transfer"
REFERENCE_REACTION_LATENCY_PROFILE_ID = "reference_reaction_latency_profile_v1"
REFERENCE_REACTION_LATENCY_BY_MODALITY_S = MappingProxyType(
    {"neutral_atom": 5e-4, "superconducting": 1e-5}
)


@dataclass(frozen=True)
class SyndromeTimingProfile:
    round_mode: str
    cycle_time_s: float
    rounds: int | None = None
    distance_module_role: str | None = None
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.round_mode, str):
            raise TypeError("round_mode must be a string")
        if not isinstance(self.cycle_time_s, (int, float)) or isinstance(
            self.cycle_time_s, bool
        ):
            raise TypeError("cycle_time_s must be a real number")
        if self.rounds is not None and (
            not isinstance(self.rounds, int) or isinstance(self.rounds, bool)
        ):
            raise TypeError("rounds must be an integer or None")
        if self.distance_module_role is not None and not isinstance(
            self.distance_module_role, str
        ):
            raise TypeError("distance_module_role must be a string or None")
        mode = self.round_mode.strip().lower()
        cycle = float(self.cycle_time_s)
        rounds = int(self.rounds) if self.rounds is not None else None
        role = self.distance_module_role.strip().lower() if self.distance_module_role else None
        if mode not in {"fixed", "qec_distance"}:
            raise ValueError(f"Unsupported syndrome timing mode: {mode}")
        if not math.isfinite(cycle) or cycle < 0:
            raise ValueError("Syndrome cycle time must be finite and non-negative")
        if mode == "fixed" and (rounds is None or rounds <= 0 or role is not None):
            raise ValueError("Fixed timing requires positive rounds and no distance role")
        if mode == "qec_distance" and (rounds is not None or not role):
            raise ValueError("Distance timing requires one module role and no fixed rounds")
        object.__setattr__(self, "round_mode", mode)
        object.__setattr__(self, "cycle_time_s", cycle)
        object.__setattr__(self, "rounds", rounds)
        object.__setattr__(self, "distance_module_role", role)
        object.__setattr__(
            self,
            "provenance",
            deep_freeze_json(
                strict_json(self.provenance, label="syndrome timing provenance")
            ),
        )

    @classmethod
    def fixed(cls, rounds: int, cycle_time_s: float, *, provenance=None):
        return cls("fixed", cycle_time_s, rounds=rounds, provenance=provenance or {})

    @classmethod
    def qec_distance(cls, module_role: str, cycle_time_s: float, *, provenance=None):
        return cls(
            "qec_distance",
            cycle_time_s,
            distance_module_role=module_role,
            provenance=provenance or {},
        )

    def to_dict(self) -> dict[str, Any]:
        result = {"round_mode": self.round_mode, "cycle_time_s": self.cycle_time_s}
        if self.rounds is not None:
            result["rounds"] = self.rounds
        if self.distance_module_role is not None:
            result["distance_module_role"] = self.distance_module_role
        if self.provenance:
            result["provenance"] = normalize_json(self.provenance)
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SyndromeTimingProfile":
        allowed = {
            "round_mode",
            "cycle_time_s",
            "rounds",
            "distance_module_role",
            "provenance",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(
                f"Unknown syndrome-timing fields: {sorted(unknown)}"
            )
        return cls(
            round_mode=data["round_mode"],
            cycle_time_s=data["cycle_time_s"],
            rounds=data["rounds"] if data.get("rounds") is not None else None,
            distance_module_role=(
                data["distance_module_role"]
                if data.get("distance_module_role") is not None
                else None
            ),
            provenance=data.get("provenance", {}),
        )


def canonical_syndrome_profiles(
    *,
    neutral_atom_cycle_s: float = 1e-3,
    superconducting_cycle_s: float = 1e-6,
    bb_surface_adapter_cycle_s: float = 1e-3,
) -> dict[str, SyndromeTimingProfile]:
    return {
        GBC_TRANSVERSAL_PROTOCOL: SyndromeTimingProfile.fixed(
            1,
            neutral_atom_cycle_s,
            provenance={"assumption": "one_round_correlated_decoding"},
        ),
        PBC_LATTICE_SURGERY_PROTOCOL: SyndromeTimingProfile.qec_distance(
            "compute",
            superconducting_cycle_s,
            provenance={"assumption": "surface_distance_merged_measurement"},
        ),
        BB_SURFACE_TRANSFER_PROTOCOL: SyndromeTimingProfile.qec_distance(
            "memory",
            bb_surface_adapter_cycle_s,
            provenance={"assumption": "qldpc_distance_adapter_measurement"},
        ),
    }


@dataclass(frozen=True)
class OperationLatencyProfile:
    magic_state_arrival: ArrivalDistribution | None = None
    bell_pair_arrival: ArrivalDistribution | None = None
    derived_arrival_kind: str | None = None
    gate_duration_s: Mapping[str, float] = field(
        default_factory=lambda: {"neutral_atom": 1e-3, "superconducting": 1e-6}
    )
    syndrome_profiles: Mapping[str, SyndromeTimingProfile] = field(
        default_factory=canonical_syndrome_profiles
    )
    compute_protocol_by_modality: Mapping[str, str] = field(
        default_factory=lambda: {
            "neutral_atom": GBC_TRANSVERSAL_PROTOCOL,
            "superconducting": PBC_LATTICE_SURGERY_PROTOCOL,
        }
    )
    store_load_protocol: str = BB_SURFACE_TRANSFER_PROTOCOL
    reaction_latency_by_modality_s: Mapping[str, float] = field(default_factory=dict)
    neutral_atom_movement: NeutralAtomMovementProfile = field(
        default_factory=NeutralAtomMovementProfile
    )
    local_magic_delivery_s: float = 0.0
    link_item_s: float = 1e-3
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.magic_state_arrival is not None and not isinstance(
            self.magic_state_arrival, ArrivalDistribution
        ):
            raise TypeError(
                "magic_state_arrival must be an ArrivalDistribution or None"
            )
        if self.bell_pair_arrival is not None and not isinstance(
            self.bell_pair_arrival, ArrivalDistribution
        ):
            raise TypeError(
                "bell_pair_arrival must be an ArrivalDistribution or None"
            )
        if self.derived_arrival_kind is not None and not isinstance(
            self.derived_arrival_kind, str
        ):
            raise TypeError("derived_arrival_kind must be a string or None")
        if not isinstance(self.neutral_atom_movement, NeutralAtomMovementProfile):
            raise TypeError(
                "neutral_atom_movement must be a NeutralAtomMovementProfile"
            )
        for name in (
            "gate_duration_s",
            "syndrome_profiles",
            "compute_protocol_by_modality",
            "reaction_latency_by_modality_s",
            "provenance",
        ):
            if not isinstance(getattr(self, name), Mapping):
                raise TypeError(f"{name} must be a mapping")
        if not isinstance(self.store_load_protocol, str):
            raise TypeError("store_load_protocol must be a string")
        for name in ("local_magic_delivery_s", "link_item_s"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError(f"{name} must be a real number")
        if any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in self.gate_duration_s.values()
        ):
            raise TypeError("gate_duration_s values must be real numbers")
        if any(
            not isinstance(value, str)
            for value in self.compute_protocol_by_modality.values()
        ):
            raise TypeError(
                "compute_protocol_by_modality values must be strings"
            )
        if any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in self.reaction_latency_by_modality_s.values()
        ):
            raise TypeError(
                "reaction_latency_by_modality_s values must be real numbers"
            )
        gate = {str(key): float(value) for key, value in self.gate_duration_s.items()}
        profiles = {
            str(key): (
                value
                if isinstance(value, SyndromeTimingProfile)
                else SyndromeTimingProfile.from_dict(value)
            )
            for key, value in self.syndrome_profiles.items()
        }
        compute = {str(key): str(value) for key, value in self.compute_protocol_by_modality.items()}
        reaction = {
            str(key): float(value)
            for key, value in self.reaction_latency_by_modality_s.items()
        }
        derived_arrival_kind = (
            self.derived_arrival_kind.strip().lower()
            if self.derived_arrival_kind is not None
            else None
        )
        if derived_arrival_kind not in {
            None,
            "deterministic",
            "exponential",
            "geometric",
        }:
            raise ValueError(
                "derived_arrival_kind must be deterministic, exponential, "
                "geometric, or None"
            )
        referenced = set(compute.values()) | {str(self.store_load_protocol)}
        missing = sorted(referenced - set(profiles))
        if missing:
            raise ValueError(f"Latency profile references unknown protocols: {missing}")
        values = [
            *gate.values(),
            *(item.cycle_time_s for item in profiles.values()),
            *reaction.values(),
            float(self.local_magic_delivery_s),
            float(self.link_item_s),
        ]
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("Operation latency values must be finite and non-negative")
        object.__setattr__(self, "gate_duration_s", deep_freeze_json(gate))
        # ``deep_freeze_json`` cannot preserve dataclass values.  Keep the
        # profile objects behind a read-only mapping instead.
        object.__setattr__(
            self,
            "syndrome_profiles",
            MappingProxyType(dict(sorted(profiles.items()))),
        )
        object.__setattr__(
            self,
            "compute_protocol_by_modality",
            deep_freeze_json(compute),
        )
        object.__setattr__(
            self,
            "reaction_latency_by_modality_s",
            deep_freeze_json(reaction),
        )
        object.__setattr__(self, "derived_arrival_kind", derived_arrival_kind)
        object.__setattr__(self, "local_magic_delivery_s", float(self.local_magic_delivery_s))
        object.__setattr__(self, "link_item_s", float(self.link_item_s))
        object.__setattr__(
            self,
            "provenance",
            deep_freeze_json(
                strict_json(self.provenance, label="latency-profile provenance")
            ),
        )

    def syndrome_profile(self, protocol_id: str) -> SyndromeTimingProfile:
        try:
            return self.syndrome_profiles[protocol_id]
        except KeyError as exc:
            raise ValueError(f"Unknown syndrome protocol: {protocol_id}") from exc

    def compute_protocol(self, modality: str) -> str:
        try:
            return str(self.compute_protocol_by_modality[modality])
        except KeyError as exc:
            raise ValueError(f"No compute protocol is bound for modality {modality}") from exc

    def gate_duration(self, modality: str) -> float:
        """Return the explicitly configured gate duration for one modality.

        A missing modality is not an ideal zero-duration gate.  Keep that
        distinction at this profile boundary so every compiler path fails in
        the same way instead of silently dropping primitive service time.
        Explicit ``0.0`` remains a valid idealized/test calibration.
        """

        try:
            return float(self.gate_duration_s[modality])
        except KeyError as exc:
            raise ValueError(
                f"No gate duration is configured for modality {modality}"
            ) from exc

    def reaction_latency(self, modality: str) -> float:
        """Return reaction delay, using zero only for an unused legacy receipt.

        Code that materializes a reaction must call
        :meth:`require_reaction_latency` so absence is never confused with an
        explicitly calibrated ``0.0``.
        """

        return float(self.reaction_latency_by_modality_s.get(modality, 0.0))

    def require_reaction_latency(self, modality: str) -> float:
        """Return an explicitly configured reaction latency.

        Black-box execution may continue to use :meth:`reaction_latency`'s
        compatibility default because it does not materialize a reaction.
        A finite runtime gadget must use this accessor: absence and an
        explicitly configured ``0.0`` have different contract meanings.
        """

        try:
            return float(self.reaction_latency_by_modality_s[modality])
        except KeyError as exc:
            raise ValueError(
                "No reaction latency is configured for modality "
                f"{modality}"
            ) from exc

    def to_dict(self) -> dict[str, Any]:
        result = {
            "magic_state_arrival": (
                self.magic_state_arrival.to_dict()
                if self.magic_state_arrival is not None
                else None
            ),
            "bell_pair_arrival": (
                self.bell_pair_arrival.to_dict()
                if self.bell_pair_arrival is not None
                else None
            ),
            "gate_duration_s": normalize_json(self.gate_duration_s),
            "syndrome_profiles": {
                key: value.to_dict() for key, value in self.syndrome_profiles.items()
            },
            "compute_protocol_by_modality": normalize_json(self.compute_protocol_by_modality),
            "store_load_protocol": self.store_load_protocol,
            "reaction_latency_by_modality_s": normalize_json(
                self.reaction_latency_by_modality_s
            ),
            "neutral_atom_movement": self.neutral_atom_movement.to_dict(),
            "local_magic_delivery_s": self.local_magic_delivery_s,
            "link_item_s": self.link_item_s,
            "provenance": normalize_json(self.provenance),
        }
        # Keep previously serialized explicit profiles canonical.  Absence of
        # this optional hint and ``None`` have the same request semantics.
        if self.derived_arrival_kind is not None:
            result["derived_arrival_kind"] = self.derived_arrival_kind
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "OperationLatencyProfile":
        allowed = {
            "magic_state_arrival",
            "bell_pair_arrival",
            "derived_arrival_kind",
            "gate_duration_s",
            "syndrome_profiles",
            "compute_protocol_by_modality",
            "store_load_protocol",
            "reaction_latency_by_modality_s",
            "neutral_atom_movement",
            "local_magic_delivery_s",
            "link_item_s",
            "provenance",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(
                f"Unknown operation-latency-profile fields: {sorted(unknown)}"
            )
        magic_state_arrival = data.get("magic_state_arrival")
        bell_pair_arrival = data.get("bell_pair_arrival")
        neutral_atom_movement = data.get("neutral_atom_movement")
        return cls(
            magic_state_arrival=(
                ArrivalDistribution.from_dict(magic_state_arrival)
                if magic_state_arrival is not None
                else None
            ),
            bell_pair_arrival=(
                ArrivalDistribution.from_dict(bell_pair_arrival)
                if bell_pair_arrival is not None
                else None
            ),
            derived_arrival_kind=data.get("derived_arrival_kind"),
            gate_duration_s=data.get(
                "gate_duration_s",
                {"neutral_atom": 1e-3, "superconducting": 1e-6},
            ),
            syndrome_profiles={
                str(key): SyndromeTimingProfile.from_dict(value)
                for key, value in data.get("syndrome_profiles", {}).items()
            }
            or canonical_syndrome_profiles(),
            compute_protocol_by_modality=data.get(
                "compute_protocol_by_modality",
                {
                    "neutral_atom": GBC_TRANSVERSAL_PROTOCOL,
                    "superconducting": PBC_LATTICE_SURGERY_PROTOCOL,
                },
            ),
            store_load_protocol=data.get(
                "store_load_protocol", BB_SURFACE_TRANSFER_PROTOCOL
            ),
            reaction_latency_by_modality_s=data.get(
                "reaction_latency_by_modality_s", {}
            ),
            neutral_atom_movement=(
                NeutralAtomMovementProfile.from_dict(neutral_atom_movement)
                if neutral_atom_movement is not None
                else NeutralAtomMovementProfile()
            ),
            local_magic_delivery_s=data.get("local_magic_delivery_s", 0.0),
            link_item_s=data.get("link_item_s", 1e-3),
            provenance=data.get("provenance", {}),
        )

    @property
    def profile_hash(self) -> str:
        return semantic_hash(self.to_dict())

    @property
    def binding_hash(self) -> str:
        """Hash of timing inputs used while compiling/evaluating a plan."""

        return self.profile_hash


def reference_reaction_latency_profile_v1(
    base: OperationLatencyProfile | None = None,
) -> OperationLatencyProfile:
    """Return the named reference reaction-timing profile.

    Timing data does not select injection semantics.  Callers opt into finite
    recipes through ``EvaluationPolicy.runtime_injection_mode``; that mode then
    requires a key for the active compute modality.  An explicit zero remains
    valid for deterministic tests.
    """

    source = base or OperationLatencyProfile()
    return replace(
        source,
        reaction_latency_by_modality_s=dict(
            REFERENCE_REACTION_LATENCY_BY_MODALITY_S
        ),
        provenance={
            **dict(source.provenance),
            "reaction_latency_profile": REFERENCE_REACTION_LATENCY_PROFILE_ID,
            "reaction_latency_source": "reference classical-control assumptions",
        },
    )
