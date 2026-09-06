"""Relational sizing for one-pool memory/compute architectures."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping

from arqsim.program.layout import store_load_transition_demands
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
from ..quantile import QuantileSizingConfig


@dataclass(frozen=True, slots=True)
class _MemoryComputeTargets:
    memory: SubmoduleKey
    compute: SubmoduleKey
    store_load: SubmoduleKey
    magic_input: SubmoduleKey
    factory: SubmoduleKey
    magic_output: SubmoduleKey

    @property
    def all(self) -> frozenset[SubmoduleKey]:
        return frozenset(
            {
                self.memory,
                self.compute,
                self.store_load,
                self.magic_input,
                self.factory,
                self.magic_output,
            }
        )


def _probability(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name} must be finite and in [0, 1]")
    return result


def _rounding(value: object, *, name: str) -> RoundingMode:
    if value not in {"ceil", "floor"}:
        raise ValueError(f"{name} must be 'ceil' or 'floor'")
    return value  # type: ignore[return-value]


def _single_target(
    targets: Mapping[
        SubmoduleKey,
        tuple[ProfileModule, ProfileSubmodule],
    ],
    *,
    label: str,
    module_type: str,
    submodule_type: str,
    payload: str,
) -> SubmoduleKey:
    matches = [
        target
        for target, (module, submodule) in targets.items()
        if module.type == module_type
        and submodule.type == submodule_type
        and submodule.payload == payload
    ]
    if len(matches) != 1:
        raise ValueError(
            "MemoryComputeSizingPolicy needs exactly one "
            f"{label}; found {len(matches)}"
        )
    return matches[0]


def _memory_compute_targets(
    targets: Mapping[
        SubmoduleKey,
        tuple[ProfileModule, ProfileSubmodule],
    ],
) -> _MemoryComputeTargets:
    return _MemoryComputeTargets(
        memory=_single_target(
            targets,
            label="logical-qubit memory region",
            module_type="memory",
            submodule_type="region",
            payload="logical_qubit",
        ),
        compute=_single_target(
            targets,
            label="logical-qubit compute region",
            module_type="compute",
            submodule_type="region",
            payload="logical_qubit",
        ),
        store_load=_single_target(
            targets,
            label="logical-qubit compute buffer",
            module_type="compute",
            submodule_type="buffer",
            payload="logical_qubit",
        ),
        magic_input=_single_target(
            targets,
            label="magic-state compute buffer",
            module_type="compute",
            submodule_type="buffer",
            payload="magic_state",
        ),
        factory=_single_target(
            targets,
            label="magic-state factory engine",
            module_type="resource_factory",
            submodule_type="engine",
            payload="magic_state",
        ),
        magic_output=_single_target(
            targets,
            label="magic-state factory output buffer",
            module_type="resource_factory",
            submodule_type="buffer",
            payload="magic_state",
        ),
    )


def _validated_qec_protocols(
    selected: Mapping[SubmoduleKey, QECResourceProtocolProfile] | None,
    targets: Mapping[
        SubmoduleKey,
        tuple[ProfileModule, ProfileSubmodule],
    ],
) -> Mapping[SubmoduleKey, QECResourceProtocolProfile]:
    if selected is None:
        return {}
    if not isinstance(selected, Mapping):
        raise TypeError("selected_qec_protocols must be a mapping")
    for target, protocol in selected.items():
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
                f"Selected QEC protocol target {target} is not a resource engine"
            )
        if not isinstance(protocol, QECResourceProtocolProfile):
            raise TypeError(
                f"Selected QEC protocol for {target} must be a catalog profile"
            )
        if protocol.resource_payload != submodule.payload:
            raise ValueError(
                f"Selected QEC protocol for {target} produces "
                f"{protocol.resource_payload!r}, not {submodule.payload!r}"
            )
    return selected


@dataclass(frozen=True, slots=True)
class MemoryComputeSizingPolicy(SizingPolicy):
    """Size one partitioned memory/compute pool and its resource buffers.

    Compute and memory capacities are one atomic partition decision. The
    resulting compute capacity determines Store/Load transition demand. Magic
    demand is independent, while factory copies depend on the selected output
    buffer capacity and QEC resource-protocol batch multiplicity.

    The policy deliberately supports exactly one pool. A Profile with several
    compute or memory pools needs an explicit allocation strategy rather than
    an inferred target pairing or a generic rule DSL.
    """

    compute_quantile: float
    compute_rounding: RoundingMode
    compute_fraction_limit: float
    store_load_quantiles_by_representation: Mapping[str, float]
    default_store_load_quantile: float
    store_load_rounding: RoundingMode
    cold_start: bool
    magic_state_quantile: float
    magic_state_rounding: RoundingMode
    minimum_magic_state_capacity: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "compute_quantile",
            _probability(self.compute_quantile, name="compute_quantile"),
        )
        object.__setattr__(
            self,
            "compute_rounding",
            _rounding(self.compute_rounding, name="compute_rounding"),
        )
        object.__setattr__(
            self,
            "compute_fraction_limit",
            _probability(
                self.compute_fraction_limit,
                name="compute_fraction_limit",
            ),
        )
        if not isinstance(self.store_load_quantiles_by_representation, Mapping):
            raise TypeError(
                "store_load_quantiles_by_representation must be a mapping"
            )
        store_load_quantiles: dict[str, float] = {}
        for representation, probability in (
            self.store_load_quantiles_by_representation.items()
        ):
            if (
                not isinstance(representation, str)
                or not representation
                or representation != representation.strip()
            ):
                raise ValueError(
                    "Store/Load quantile representation keys must be non-empty"
                )
            store_load_quantiles[representation] = _probability(
                probability,
                name=f"Store/Load quantile for {representation}",
            )
        object.__setattr__(
            self,
            "store_load_quantiles_by_representation",
            MappingProxyType(dict(sorted(store_load_quantiles.items()))),
        )
        object.__setattr__(
            self,
            "default_store_load_quantile",
            _probability(
                self.default_store_load_quantile,
                name="default_store_load_quantile",
            ),
        )
        object.__setattr__(
            self,
            "store_load_rounding",
            _rounding(self.store_load_rounding, name="store_load_rounding"),
        )
        if type(self.cold_start) is not bool:
            raise TypeError("cold_start must be a boolean")
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
            _rounding(self.magic_state_rounding, name="magic_state_rounding"),
        )
        object.__setattr__(
            self,
            "minimum_magic_state_capacity",
            _positive_capacity(
                self.minimum_magic_state_capacity,
                where="minimum_magic_state_capacity",
            ),
        )

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
        """Return a complete relational sizing result for one local pool."""

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
            and reference_statistics.logical_qubits != statistics.logical_qubits
        ):
            raise ValueError(
                "reference_statistics must use the main circuit logical-qubit count"
            )

        targets = _profile_targets(profile)
        group = _memory_compute_targets(targets)
        exact = _validated_overrides(overrides, targets)
        qec_protocols = _validated_qec_protocols(
            selected_qec_protocols,
            targets,
        )

        logical_qubits = statistics.logical_qubits
        compute_is_pinned = group.compute in exact
        memory_is_pinned = group.memory in exact
        if compute_is_pinned and memory_is_pinned:
            compute_capacity = exact[group.compute]
            memory_capacity = exact[group.memory]
            if compute_capacity + memory_capacity != logical_qubits:
                raise ValueError(
                    "Pinned compute and memory capacities must sum to the "
                    "circuit logical-qubit count"
                )
        elif compute_is_pinned:
            compute_capacity = exact[group.compute]
            if compute_capacity > logical_qubits:
                raise ValueError(
                    "Pinned compute capacity cannot exceed the circuit "
                    "logical-qubit count"
                )
            memory_capacity = logical_qubits - compute_capacity
        elif memory_is_pinned:
            memory_capacity = exact[group.memory]
            if memory_capacity > logical_qubits:
                raise ValueError(
                    "Pinned memory capacity cannot exceed the circuit "
                    "logical-qubit count"
                )
            compute_capacity = logical_qubits - memory_capacity
        else:
            quantile_capacity = _round(
                _linear_quantile(
                    statistics.active_qubits_per_layer,
                    self.compute_quantile,
                ),
                self.compute_rounding,
            )
            fraction_capacity = math.floor(
                logical_qubits * self.compute_fraction_limit
            )
            compute_capacity = min(
                logical_qubits,
                max(
                    max(statistics.operation_widths, default=1),
                    min(
                        logical_qubits,
                        fraction_capacity,
                        max(1, quantile_capacity),
                    ),
                ),
            )
            memory_capacity = logical_qubits - compute_capacity

        if compute_capacity <= 0:
            raise ValueError(
                "MemoryComputeSizingPolicy needs a positive compute capacity"
            )
        maximum_operation_width = max(statistics.operation_widths, default=0)
        if maximum_operation_width > compute_capacity:
            raise ValueError(
                "Compute capacity cannot hold one logical operation: "
                f"width={maximum_operation_width}, capacity={compute_capacity}"
            )

        if group.store_load in exact:
            store_load_capacity = exact[group.store_load]
        else:
            transition_demands = store_load_transition_demands(
                statistics,
                compute_capacity,
                cold_start=self.cold_start,
            )
            probability = self.store_load_quantiles_by_representation.get(
                statistics.representation,
                self.default_store_load_quantile,
            )
            store_load_capacity = min(
                compute_capacity,
                max(
                    1,
                    _round(
                        _linear_quantile(
                            tuple(transition_demands["exchanges"]),
                            probability,
                        ),
                        self.store_load_rounding,
                    ),
                ),
            )
        if store_load_capacity > compute_capacity:
            raise ValueError(
                "Store/Load buffer capacity cannot exceed compute capacity"
            )
        if store_load_capacity == 0:
            raise ValueError(
                "A non-empty compute region needs a positive Store/Load buffer"
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
        magic_input_capacity = exact.get(group.magic_input, magic_capacity)
        magic_output_capacity = exact.get(group.magic_output, magic_capacity)
        if magic_input_capacity <= 0 or magic_output_capacity <= 0:
            raise ValueError(
                "Magic-state input and output buffers must be positive"
            )

        dependencies: dict[SubmoduleKey, str] = {}
        if group.factory in exact:
            factory_capacity = exact[group.factory]
        else:
            outputs_per_copy = _qec_protocol_output_multiplicity(
                group.factory,
                qec_protocols,
            )
            factory_capacity = max(
                1,
                math.ceil(magic_output_capacity / outputs_per_copy),
            )
            dependencies[group.factory] = qec_protocols[
                group.factory
            ].profile_hash

        capacities: dict[SubmoduleKey, int] = {
            group.memory: memory_capacity,
            group.compute: compute_capacity,
            group.store_load: store_load_capacity,
            group.magic_input: magic_input_capacity,
            group.factory: factory_capacity,
            group.magic_output: magic_output_capacity,
        }
        for target in targets:
            if target in group.all:
                continue
            try:
                capacities[target] = exact[target]
            except KeyError as exc:
                raise ValueError(
                    "MemoryComputeSizingPolicy has no sizing relation for "
                    f"{target}; provide an exact override or another SizingPolicy"
                ) from exc
        return SizingResult(
            capacities,
            qec_protocol_dependencies=dependencies,
        )


def make_sizing_policy(
    config: QuantileSizingConfig,
) -> MemoryComputeSizingPolicy:
    """Bind shared experiment quantiles to NA-MCF's sizing relation."""

    if not isinstance(config, QuantileSizingConfig):
        raise TypeError("config must be a QuantileSizingConfig")

    return MemoryComputeSizingPolicy(
        compute_quantile=config.compute_quantile,
        compute_rounding=config.compute_rounding,
        compute_fraction_limit=0.40,
        store_load_quantiles_by_representation=(
            config.store_load_quantiles_by_representation
        ),
        default_store_load_quantile=config.default_store_load_quantile,
        store_load_rounding=config.buffer_rounding,
        cold_start=True,
        magic_state_quantile=config.magic_state_quantile,
        magic_state_rounding=config.buffer_rounding,
        minimum_magic_state_capacity=1,
    )


__all__ = [
    "MemoryComputeSizingPolicy",
    "make_sizing_policy",
]
