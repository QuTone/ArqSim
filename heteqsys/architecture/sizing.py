"""Circuit-aware logical capacity sizing.

Sizing has one narrow responsibility: assign an exact logical capacity to
every Submodule in an Architecture Profile. Slot-owning Submodules may be
empty; resource engines always require a positive copy count. Sizing does not
choose QEC, place logical slots, resolve QEC protocol identity, or retain
workflow provenance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import math
from types import MappingProxyType
from typing import Literal, Mapping

from heteqsys.program.statistics import CircuitStatistics
from heteqsys.qec.protocol import QECResourceProtocolProfile

from .identifiers import SubmoduleKey
from .profile import ArchitectureProfile, ProfileModule, ProfileSubmodule


RoundingMode = Literal["ceil", "floor"]


def _positive_capacity(value: object, *, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{where} must be a positive plain integer")
    return value


def _nonnegative_capacity(value: object, *, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{where} must be a non-negative plain integer")
    return value


@dataclass(frozen=True, slots=True)
class SizingResult:
    """Exact capacities plus hashes of QEC protocols used to derive them.

    A result may carry zero for an empty slot-owning Submodule. The concrete
    policy and final Specification enforce that engine copy counts stay
    positive.
    """

    capacities: Mapping[SubmoduleKey, int]
    qec_protocol_dependencies: Mapping[SubmoduleKey, str] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        if not isinstance(self.capacities, Mapping):
            raise TypeError("SizingResult capacities must be a mapping")
        normalized: dict[SubmoduleKey, int] = {}
        for target, capacity in self.capacities.items():
            if not isinstance(target, SubmoduleKey):
                raise TypeError("SizingResult keys must be SubmoduleKey values")
            normalized[target] = _nonnegative_capacity(
                capacity,
                where=f"Capacity for {target}",
            )
        object.__setattr__(
            self,
            "capacities",
            MappingProxyType(dict(sorted(normalized.items()))),
        )
        if not isinstance(self.qec_protocol_dependencies, Mapping):
            raise TypeError(
                "SizingResult qec_protocol_dependencies must be a mapping"
            )
        dependencies: dict[SubmoduleKey, str] = {}
        for target, profile_hash in self.qec_protocol_dependencies.items():
            if not isinstance(target, SubmoduleKey):
                raise TypeError(
                    "SizingResult QEC protocol dependency keys must be "
                    "SubmoduleKey values"
                )
            if target not in normalized:
                raise ValueError(
                    f"QEC protocol dependency targets unsized Submodule {target}"
                )
            if (
                not isinstance(profile_hash, str)
                or len(profile_hash) != 64
                or any(character not in "0123456789abcdef" for character in profile_hash)
            ):
                raise ValueError(
                    f"QEC protocol dependency for {target} must be a SHA-256 hash"
                )
            dependencies[target] = profile_hash
        object.__setattr__(
            self,
            "qec_protocol_dependencies",
            MappingProxyType(dict(sorted(dependencies.items()))),
        )

    def capacity_for(self, target: SubmoduleKey) -> int:
        """Return the capacity of one exact target."""

        if not isinstance(target, SubmoduleKey):
            raise TypeError("Sizing target must be a SubmoduleKey")
        try:
            return self.capacities[target]
        except KeyError as exc:
            raise KeyError(f"Sizing has no capacity for {target}") from exc

    def __getitem__(self, target: SubmoduleKey) -> int:
        return self.capacity_for(target)


class SizingPolicy(ABC):
    """Abstract strategy for lowering circuit facts into logical capacities."""

    @abstractmethod
    def size(
        self,
        profile: ArchitectureProfile,
        statistics: CircuitStatistics,
        *,
        selected_qec_protocols: Mapping[SubmoduleKey, QECResourceProtocolProfile]
        | None = None,
        reference_statistics: CircuitStatistics | None = None,
        overrides: Mapping[SubmoduleKey, int] | None = None,
    ) -> SizingResult:
        """Return one exact capacity for every Profile Submodule."""

        raise NotImplementedError


def _profile_targets(
    profile: ArchitectureProfile,
) -> dict[SubmoduleKey, tuple[ProfileModule, ProfileSubmodule]]:
    if not isinstance(profile, ArchitectureProfile):
        raise TypeError("profile must be an ArchitectureProfile")
    result: dict[SubmoduleKey, tuple[ProfileModule, ProfileSubmodule]] = {}
    for owner in (*profile.nodes, *profile.interconnects):
        for module in owner.modules:
            for submodule in module.submodules:
                target = SubmoduleKey(owner.id, module.id, submodule.id)
                if target in result:
                    raise ValueError(f"Duplicate Profile Submodule target: {target}")
                result[target] = (module, submodule)
    return result


def _validated_overrides(
    overrides: Mapping[SubmoduleKey, int] | None,
    targets: Mapping[
        SubmoduleKey,
        tuple[ProfileModule, ProfileSubmodule],
    ],
) -> dict[SubmoduleKey, int]:
    if overrides is None:
        return {}
    if not isinstance(overrides, Mapping):
        raise TypeError("Sizing overrides must be a mapping")
    result: dict[SubmoduleKey, int] = {}
    for target, capacity in overrides.items():
        if not isinstance(target, SubmoduleKey):
            raise TypeError("Sizing override keys must be SubmoduleKey values")
        if target not in targets:
            raise ValueError(f"Sizing override targets unknown Submodule {target}")
        _module, submodule = targets[target]
        validator = (
            _positive_capacity
            if submodule.type == "engine"
            else _nonnegative_capacity
        )
        result[target] = validator(capacity, where=f"Sizing override for {target}")
    return result


def _linear_quantile(values: tuple[int, ...], probability: float) -> float:
    """Return the linearly interpolated quantile without a NumPy dependency."""

    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def _round(value: float, mode: RoundingMode) -> int:
    return math.ceil(value) if mode == "ceil" else math.floor(value)


def _qec_protocol_output_multiplicity(
    target: SubmoduleKey,
    selected_qec_protocols: Mapping[SubmoduleKey, QECResourceProtocolProfile],
) -> int:
    try:
        protocol = selected_qec_protocols[target]
    except KeyError as exc:
        raise ValueError(
            f"Sizing resource engine {target} needs a selected QEC protocol profile"
        ) from exc
    if not isinstance(protocol, QECResourceProtocolProfile):
        raise TypeError(
            f"Selected QEC protocol for {target} must be a catalog profile"
        )
    if not protocol.id.strip():
        raise ValueError(f"Selected QEC protocol for {target} needs an ID")
    return _positive_capacity(
        protocol.outputs_per_batch,
        where=f"QEC protocol output multiplicity for {target}",
    )


__all__ = [
    "SizingPolicy",
    "SizingResult",
]
