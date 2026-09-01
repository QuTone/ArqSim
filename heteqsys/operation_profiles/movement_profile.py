"""Calibrated inputs for compiler-owned logical-movement cost models."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from heteqsys.schema import deep_freeze_json, normalize_json, semantic_hash, strict_json


NEUTRAL_ATOM_MOVEMENT_PROFILE_SCHEMA_VERSION = (
    "arqsim.neutral-atom-movement-profile.v1"
)


@dataclass(frozen=True)
class NeutralAtomMovementProfile:
    """Physical calibration consumed by the neutral-atom logical compiler.

    This record contains measured or literature-derived inputs only.  The
    distance and duration formulas remain owned and versioned by the compiler.
    Logical coordinates remain in ``ArchitectureSpecification``; this profile
    supplies the conversion from those coordinates to physical distance and
    then to estimated movement time.
    """

    x_spacing_um: float = 19.0
    y_spacing_um: float = 15.0
    aod_count: int = 4
    transfer_duration_us: float = 8.0
    reference_distance_um: float = 110.0
    reference_move_duration_us: float = 200.0
    provenance: Mapping[str, Any] = field(default_factory=dict)
    schema_version: str = NEUTRAL_ATOM_MOVEMENT_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != NEUTRAL_ATOM_MOVEMENT_PROFILE_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported neutral-atom movement-profile schema: "
                f"{self.schema_version!r}"
            )
        if (
            not isinstance(self.aod_count, int)
            or isinstance(self.aod_count, bool)
            or self.aod_count <= 0
        ):
            raise ValueError("aod_count must be a positive integer")
        positive = {
            "x_spacing_um": self.x_spacing_um,
            "y_spacing_um": self.y_spacing_um,
            "reference_distance_um": self.reference_distance_um,
            "reference_move_duration_us": self.reference_move_duration_us,
        }
        nonnegative = {"transfer_duration_us": self.transfer_duration_us}
        for name, value in {**positive, **nonnegative}.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a real number")
        if any(
            not math.isfinite(float(value)) or float(value) <= 0
            for value in positive.values()
        ):
            raise ValueError(
                "Neutral-atom movement spacing/reference inputs must be finite "
                "and positive"
            )
        if any(
            not math.isfinite(float(value)) or float(value) < 0
            for value in nonnegative.values()
        ):
            raise ValueError(
                "Neutral-atom transfer duration must be finite and non-negative"
            )
        for name in (*positive, *nonnegative):
            object.__setattr__(self, name, float(getattr(self, name)))
        object.__setattr__(
            self,
            "provenance",
            deep_freeze_json(
                strict_json(
                    self.provenance,
                    label="neutral-atom movement-profile provenance",
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "x_spacing_um": self.x_spacing_um,
            "y_spacing_um": self.y_spacing_um,
            "aod_count": self.aod_count,
            "transfer_duration_us": self.transfer_duration_us,
            "reference_distance_um": self.reference_distance_um,
            "reference_move_duration_us": self.reference_move_duration_us,
            "provenance": normalize_json(self.provenance),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "NeutralAtomMovementProfile":
        allowed = {
            "schema_version",
            "x_spacing_um",
            "y_spacing_um",
            "aod_count",
            "transfer_duration_us",
            "reference_distance_um",
            "reference_move_duration_us",
            "provenance",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(
                "Unknown neutral-atom movement-profile fields: "
                f"{sorted(unknown)}"
            )
        return cls(
            schema_version=str(
                data.get(
                    "schema_version",
                    NEUTRAL_ATOM_MOVEMENT_PROFILE_SCHEMA_VERSION,
                )
            ),
            x_spacing_um=data.get("x_spacing_um", 19.0),
            y_spacing_um=data.get("y_spacing_um", 15.0),
            aod_count=data.get("aod_count", 4),
            transfer_duration_us=data.get("transfer_duration_us", 8.0),
            reference_distance_um=data.get("reference_distance_um", 110.0),
            reference_move_duration_us=data.get(
                "reference_move_duration_us", 200.0
            ),
            provenance=data.get("provenance", {}),
        )

    @property
    def profile_hash(self) -> str:
        return semantic_hash(self.to_dict())


__all__ = [
    "NEUTRAL_ATOM_MOVEMENT_PROFILE_SCHEMA_VERSION",
    "NeutralAtomMovementProfile",
]
