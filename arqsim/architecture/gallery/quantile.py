"""Shared quantile inputs for experiments over the architecture gallery.

This runtime-only value is not an architecture level and is never serialized.
Each gallery entry translates it into that architecture's own ``SizingPolicy``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Mapping

from ..sizing import RoundingMode


def _probability(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number")
    probability = float(value)
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        raise ValueError(f"{name} must be finite and in [0, 1]")
    return probability


def _rounding(value: object, *, name: str) -> RoundingMode:
    if value not in {"ceil", "floor"}:
        raise ValueError(f"{name} must be 'ceil' or 'floor'")
    return value  # type: ignore[return-value]


@dataclass(frozen=True, slots=True, kw_only=True)
class QuantileSizingConfig:
    """One common quantile point interpreted by each architecture policy.

    ArqSim's reference baseline intentionally uses different quantiles for
    compute activity, magic-state demand, and Store/Load traffic. ``uniform``
    is the explicit convenience for experiments that sweep one probability
    across all applicable demand classes.
    """

    compute_quantile: float
    magic_state_quantile: float
    default_store_load_quantile: float
    store_load_quantiles_by_representation: Mapping[str, float] = field(
        default_factory=dict
    )
    compute_rounding: RoundingMode = "floor"
    buffer_rounding: RoundingMode = "ceil"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "compute_quantile",
            _probability(self.compute_quantile, name="compute_quantile"),
        )
        object.__setattr__(
            self,
            "magic_state_quantile",
            _probability(
                self.magic_state_quantile,
                name="magic_state_quantile",
            ),
        )
        object.__setattr__(
            self,
            "default_store_load_quantile",
            _probability(
                self.default_store_load_quantile,
                name="default_store_load_quantile",
            ),
        )
        if not isinstance(self.store_load_quantiles_by_representation, Mapping):
            raise TypeError(
                "store_load_quantiles_by_representation must be a mapping"
            )
        store_load: dict[str, float] = {}
        for representation, probability in (
            self.store_load_quantiles_by_representation.items()
        ):
            if (
                not isinstance(representation, str)
                or not representation
                or representation != representation.strip()
            ):
                raise ValueError(
                    "Store/Load representation keys must be non-empty strings"
                )
            store_load[representation] = _probability(
                probability,
                name=f"Store/Load quantile for {representation}",
            )
        object.__setattr__(
            self,
            "store_load_quantiles_by_representation",
            MappingProxyType(dict(sorted(store_load.items()))),
        )
        object.__setattr__(
            self,
            "compute_rounding",
            _rounding(self.compute_rounding, name="compute_rounding"),
        )
        object.__setattr__(
            self,
            "buffer_rounding",
            _rounding(self.buffer_rounding, name="buffer_rounding"),
        )

    @classmethod
    def uniform(
        cls,
        probability: float,
        *,
        compute_rounding: RoundingMode = "floor",
        buffer_rounding: RoundingMode = "ceil",
    ) -> QuantileSizingConfig:
        """Use one probability for compute, magic, and Store/Load demand."""

        value = _probability(probability, name="uniform quantile")
        return cls(
            compute_quantile=value,
            magic_state_quantile=value,
            default_store_load_quantile=value,
            compute_rounding=compute_rounding,
            buffer_rounding=buffer_rounding,
        )

    @classmethod
    def reference_baseline(cls) -> QuantileSizingConfig:
        """Return the quantile point used by the ArqSim reference evaluation.

        This is the single owner of the reference quantiles after removal of the
        old combined sizing/QEC/layout policy. Architecture-local relations
        remain in each gallery bundle's concrete sizing policy.
        """

        return cls(
            compute_quantile=0.50,
            magic_state_quantile=0.60,
            default_store_load_quantile=0.95,
            store_load_quantiles_by_representation={
                "clifford_t": 0.95,
                "pbc": 0.80,
            },
            compute_rounding="floor",
            buffer_rounding="ceil",
        )


__all__ = ["QuantileSizingConfig"]
