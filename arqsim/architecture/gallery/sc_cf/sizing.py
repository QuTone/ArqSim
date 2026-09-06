"""Sizing policy for the reference SC-CF architecture."""

from __future__ import annotations

from dataclasses import dataclass

from ...profile import ArchitectureProfile
from .._shared.compute_factory_sizing import (
    _ComputeFactorySizingPolicy,
    _require_single_node_compute_factory,
)
from ..quantile import QuantileSizingConfig


@dataclass(frozen=True, slots=True)
class SuperconductingComputeFactorySizingPolicy(_ComputeFactorySizingPolicy):
    """Size one superconducting Node with colocated compute and MS factory."""

    def _validate_profile_family(self, profile: ArchitectureProfile) -> None:
        _require_single_node_compute_factory(
            profile,
            modality="superconducting",
            policy_name=type(self).__name__,
        )


def make_sizing_policy(
    config: QuantileSizingConfig,
) -> SuperconductingComputeFactorySizingPolicy:
    """Translate one experiment quantile point into the SC-CF policy."""

    if not isinstance(config, QuantileSizingConfig):
        raise TypeError("config must be a QuantileSizingConfig")
    return SuperconductingComputeFactorySizingPolicy(
        magic_state_quantile=config.magic_state_quantile,
        magic_state_rounding=config.buffer_rounding,
        minimum_magic_state_capacity=1,
    )


__all__ = [
    "SuperconductingComputeFactorySizingPolicy",
    "make_sizing_policy",
]
