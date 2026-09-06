"""Shared mechanics for colocated compute-and-factory gallery policies.

This module is private to the gallery.  It factors the capacity calculation
used by the NA-CF and SC-CF implementations without pretending that one
concrete policy is the default for every Architecture Profile.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
import math
from typing import Mapping

from arqsim.program.statistics import CircuitStatistics
from arqsim.qec.protocol import QECResourceProtocolProfile

from ...identifiers import SubmoduleKey
from ...profile import ArchitectureProfile, ProfileModule, ProfileSubmodule
from ...sizing import (
    RoundingMode,
    SizingPolicy,
    SizingResult,
    _linear_quantile,
    _positive_capacity,
    _profile_targets,
    _qec_protocol_output_multiplicity,
    _round,
    _validated_overrides,
)


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


def _require_single_node_compute_factory(
    profile: ArchitectureProfile,
    *,
    modality: str,
    policy_name: str,
) -> None:
    """Validate the owner boundary shared by the two local implementations."""

    if len(profile.nodes) != 1 or profile.interconnects:
        raise ValueError(
            f"{policy_name} needs one local Node and no Interconnect"
        )
    node = profile.nodes[0]
    if node.modality != modality:
        raise ValueError(
            f"{policy_name} requires a {modality.replace('_', '-')} Node"
        )


@dataclass(frozen=True, slots=True)
class _ComputeFactorySizingPolicy(SizingPolicy):
    """Mechanical sizing shared by the concrete NA-CF and SC-CF policies."""

    magic_state_quantile: float
    magic_state_rounding: RoundingMode
    minimum_magic_state_capacity: int

    def __post_init__(self) -> None:
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
            "magic_state_rounding",
            _rounding(
                self.magic_state_rounding,
                name="magic_state_rounding",
            ),
        )
        object.__setattr__(
            self,
            "minimum_magic_state_capacity",
            _positive_capacity(
                self.minimum_magic_state_capacity,
                where="minimum_magic_state_capacity",
            ),
        )

    @abstractmethod
    def _validate_profile_family(self, profile: ArchitectureProfile) -> None:
        """Reject a Profile outside the concrete gallery family."""

        raise NotImplementedError

    def size(
        self,
        profile: ArchitectureProfile,
        statistics: CircuitStatistics,
        *,
        selected_qec_protocols: Mapping[
            SubmoduleKey,
            QECResourceProtocolProfile,
        ]
        | None = None,
        reference_statistics: CircuitStatistics | None = None,
        overrides: Mapping[SubmoduleKey, int] | None = None,
    ) -> SizingResult:
        """Return exact capacities for one colocated compute/factory Profile."""

        if not isinstance(profile, ArchitectureProfile):
            raise TypeError("profile must be an ArchitectureProfile")
        self._validate_profile_family(profile)
        if not isinstance(statistics, CircuitStatistics):
            raise TypeError("statistics must be CircuitStatistics")
        if reference_statistics is not None and not isinstance(
            reference_statistics,
            CircuitStatistics,
        ):
            raise TypeError(
                "reference_statistics must be CircuitStatistics or None"
            )
        if (
            reference_statistics is not None
            and reference_statistics.logical_qubits
            != statistics.logical_qubits
        ):
            raise ValueError(
                "reference_statistics must use the main circuit "
                "logical-qubit count"
            )
        if selected_qec_protocols is None:
            qec_protocols: Mapping[
                SubmoduleKey,
                QECResourceProtocolProfile,
            ] = {}
        elif isinstance(selected_qec_protocols, Mapping):
            qec_protocols = selected_qec_protocols
        else:
            raise TypeError("selected_qec_protocols must be a mapping")

        targets = _profile_targets(profile)
        exact = _validated_overrides(overrides, targets)
        for target, protocol in qec_protocols.items():
            if not isinstance(target, SubmoduleKey):
                raise TypeError(
                    "Selected QEC protocol keys must be SubmoduleKey values"
                )
            if target not in targets:
                raise ValueError(
                    f"Selected QEC protocol targets unknown Submodule {target}"
                )
            _module, submodule = targets[target]
            if submodule.type != "engine":
                raise ValueError(
                    f"Selected QEC protocol target {target} is not a "
                    "resource engine"
                )
            if not isinstance(protocol, QECResourceProtocolProfile):
                raise TypeError(
                    f"Selected QEC protocol for {target} must be a catalog "
                    "profile"
                )
            if protocol.resource_payload != submodule.payload:
                raise ValueError(
                    f"Selected QEC protocol for {target} produces "
                    f"{protocol.resource_payload!r}, not "
                    f"{submodule.payload!r}"
                )

        demand = reference_statistics or statistics
        magic_capacity = max(
            self.minimum_magic_state_capacity,
            _round(
                _linear_quantile(
                    demand.magic_states_per_layer,
                    self.magic_state_quantile,
                ),
                self.magic_state_rounding,
            ),
        )

        policy_name = type(self).__name__
        capacities: dict[SubmoduleKey, int] = {}
        engines: list[
            tuple[SubmoduleKey, ProfileModule, ProfileSubmodule]
        ] = []
        for target, (module, submodule) in targets.items():
            if target in exact:
                if (
                    submodule.type == "region"
                    and submodule.payload == "logical_qubit"
                ) or (
                    submodule.type == "buffer"
                    and submodule.payload == "magic_state"
                ):
                    capacities[target] = _positive_capacity(
                        exact[target],
                        where=f"{policy_name} capacity for {target}",
                    )
                else:
                    capacities[target] = exact[target]
            elif (
                submodule.type == "region"
                and submodule.payload == "logical_qubit"
            ):
                capacities[target] = _positive_capacity(
                    statistics.logical_qubits,
                    where=f"Logical-qubit capacity for {target}",
                )
            elif (
                submodule.type == "buffer"
                and submodule.payload == "magic_state"
            ):
                capacities[target] = magic_capacity
            elif (
                submodule.type == "engine"
                and submodule.payload == "magic_state"
            ):
                engines.append((target, module, submodule))
            else:
                raise ValueError(
                    f"{policy_name} has no sizing rule for {target}; "
                    "provide an exact override or another SizingPolicy"
                )

        for target, module, _submodule in engines:
            if target in exact:
                continue
            sibling_engines = [
                other_target
                for other_target, (other_module, other_submodule) in (
                    targets.items()
                )
                if other_target.owner_id == target.owner_id
                and other_module.id == module.id
                and other_submodule.type == "engine"
                and other_submodule.payload == "magic_state"
            ]
            if len(sibling_engines) != 1:
                raise ValueError(
                    f"Magic-state engine {target} needs to be the only sibling "
                    "magic-state engine when copies are inferred"
                )
            output_capacities = [
                capacities[other_target]
                for other_target, (other_module, other_submodule) in (
                    targets.items()
                )
                if other_target.owner_id == target.owner_id
                and other_module.id == module.id
                and other_submodule.type == "buffer"
                and other_submodule.payload == "magic_state"
            ]
            if len(output_capacities) != 1:
                raise ValueError(
                    f"Magic-state engine {target} needs exactly one sibling "
                    "magic-state output buffer"
                )
            outputs_per_copy = _qec_protocol_output_multiplicity(
                target,
                qec_protocols,
            )
            capacities[target] = max(
                1,
                math.ceil(output_capacities[0] / outputs_per_copy),
            )

        return SizingResult(
            capacities,
            qec_protocol_dependencies={
                target: qec_protocols[target].profile_hash
                for target, _module, _submodule in engines
            },
        )


__all__: list[str] = []
