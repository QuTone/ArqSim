"""Transient typed inputs shared by execution-plan lowering stages.

These records have no codec or hash.  The canonical architecture and latency
profile remain the authorities; the records only make one lowering run's
derived topology and costs explicit between pure stages.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

from heteqsys.schema import deep_freeze_json


@dataclass(frozen=True)
class RuntimeTopology:
    compute_modality: str
    compute_location: str
    memory_location: str | None
    compute_buffer_location: str | None
    memory_buffer_location: str | None
    magic_input_location: str
    msf_location: str | None
    magic_output_location: str | None
    factory_engine_location: str | None
    store_load_buffer_capacity: int
    store_load_buffer_slots: tuple[str, ...]
    magic_buffer_capacity: int
    magic_buffer_slots: tuple[str, ...]
    msf_output_buffer_capacity: int
    msf_output_buffer_slots: tuple[str, ...]
    remote_magic_link: str | None
    memory_link: str | None
    bell_buffer_capacities: Mapping[str, int]
    bell_engine_profiles: Mapping[str, Mapping[str, Any]]
    magic_outputs_per_copy_per_batch: int
    magic_protocol: str

    def __post_init__(self) -> None:
        # These are transient projections, but they still cross stage
        # boundaries.  Detach them from resolver-owned containers so a later
        # mutation cannot silently change what another lowering stage sees.
        if not isinstance(self.bell_buffer_capacities, Mapping):
            raise TypeError("bell_buffer_capacities must be a mapping")
        if not isinstance(self.bell_engine_profiles, Mapping):
            raise TypeError("bell_engine_profiles must be a mapping")
        object.__setattr__(
            self,
            "store_load_buffer_slots",
            tuple(self.store_load_buffer_slots),
        )
        object.__setattr__(
            self,
            "magic_buffer_slots",
            tuple(self.magic_buffer_slots),
        )
        object.__setattr__(
            self,
            "msf_output_buffer_slots",
            tuple(self.msf_output_buffer_slots),
        )
        object.__setattr__(
            self,
            "bell_buffer_capacities",
            deep_freeze_json(self.bell_buffer_capacities),
        )
        object.__setattr__(
            self,
            "bell_engine_profiles",
            deep_freeze_json(self.bell_engine_profiles),
        )


@dataclass(frozen=True)
class SyndromeCostInput:
    """Typed store/load timing resolved for one lowering run."""

    protocol: str
    rounds: int
    cycle_time_s: float
    provenance: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.protocol, str) or not self.protocol.strip():
            raise TypeError("Syndrome protocol must be a non-empty string")
        if type(self.rounds) is not int or self.rounds <= 0:
            raise TypeError("Syndrome rounds must be a positive integer")
        if type(self.cycle_time_s) not in {int, float} or not math.isfinite(
            self.cycle_time_s
        ) or self.cycle_time_s <= 0:
            raise TypeError("Syndrome cycle time must be finite and positive")
        if not isinstance(self.provenance, Mapping):
            raise TypeError("Syndrome provenance must be a mapping")
        object.__setattr__(self, "cycle_time_s", float(self.cycle_time_s))
        object.__setattr__(self, "provenance", deep_freeze_json(self.provenance))

    @property
    def duration_s(self) -> float:
        """Derived service duration; rounds and cycle time own the facts."""

        return self.rounds * self.cycle_time_s

    def receipt(self) -> dict[str, Any]:
        """Project the typed cost once at the serialized ISA boundary."""

        return {
            "duration_s": self.duration_s,
            "protocol": self.protocol,
            "rounds": self.rounds,
            "cycle_time_s": self.cycle_time_s,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class PlanCostInputs:
    """Explicit costs passed between stages without metadata round-trips."""

    store_load: SyndromeCostInput | None
    logical_link_item_s: float
    local_magic_delivery_s: float
    classical_reaction_s: float

    def __post_init__(self) -> None:
        if self.store_load is not None and not isinstance(
            self.store_load,
            SyndromeCostInput,
        ):
            raise TypeError("store_load must be a SyndromeCostInput or None")
        for name in (
            "logical_link_item_s",
            "local_magic_delivery_s",
            "classical_reaction_s",
        ):
            value = getattr(self, name)
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                raise TypeError(f"{name} must be finite and non-negative")
            object.__setattr__(self, name, float(value))

    @property
    def store_load_duration_s(self) -> float:
        return self.store_load.duration_s if self.store_load is not None else 0.0


__all__ = ["PlanCostInputs", "RuntimeTopology", "SyndromeCostInput"]
