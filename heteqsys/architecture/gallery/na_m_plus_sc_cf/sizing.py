"""Sizing for neutral-atom memory and superconducting compute."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping

from heteqsys.program.layout import store_load_transition_demands
from heteqsys.program.statistics import CircuitStatistics
from heteqsys.qec.protocol import (
    EntanglementDistillationProfile,
    MagicStateFactoryProfile,
    QECResourceProtocolProfile,
)

from ...identifiers import SubmoduleKey
from ...profile import ArchitectureProfile, ProfileModule, ProfileSubmodule
from ...sizing import (
    RoundingMode,
    SizingPolicy,
    SizingResult,
    _linear_quantile,
    _positive_capacity,
    _profile_targets,
    _round,
    _validated_overrides,
)
from ..quantile import QuantileSizingConfig


@dataclass(frozen=True, slots=True)
class _HybridMemoryComputeTargets:
    memory: SubmoduleKey
    memory_store_load: SubmoduleKey
    compute: SubmoduleKey
    compute_store_load: SubmoduleKey
    magic_input: SubmoduleKey
    factory: SubmoduleKey
    magic_output: SubmoduleKey
    bell_engine: SubmoduleKey
    bell_buffer: SubmoduleKey

    @property
    def all(self) -> frozenset[SubmoduleKey]:
        return frozenset(
            {
                self.memory,
                self.memory_store_load,
                self.compute,
                self.compute_store_load,
                self.magic_input,
                self.factory,
                self.magic_output,
                self.bell_engine,
                self.bell_buffer,
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
    owners: frozenset[str],
    label: str,
    module_type: str,
    submodule_type: str,
    payload: str,
) -> SubmoduleKey:
    matches = [
        target
        for target, (module, submodule) in targets.items()
        if target.owner_id in owners
        and module.type == module_type
        and submodule.type == submodule_type
        and submodule.payload == payload
    ]
    if len(matches) != 1:
        raise ValueError(
            "HybridMemoryComputeSizingPolicy needs exactly one "
            f"{label}; found {len(matches)}"
        )
    return matches[0]


def _hybrid_targets(
    profile: ArchitectureProfile,
    targets: Mapping[
        SubmoduleKey,
        tuple[ProfileModule, ProfileSubmodule],
    ],
) -> _HybridMemoryComputeTargets:
    node_ids = frozenset(node.id for node in profile.nodes)
    interconnect_ids = frozenset(item.id for item in profile.interconnects)
    group = _HybridMemoryComputeTargets(
        memory=_single_target(
            targets,
            owners=node_ids,
            label="logical-qubit memory region",
            module_type="memory",
            submodule_type="region",
            payload="logical_qubit",
        ),
        memory_store_load=_single_target(
            targets,
            owners=node_ids,
            label="memory-side Store/Load buffer",
            module_type="memory",
            submodule_type="buffer",
            payload="logical_qubit",
        ),
        compute=_single_target(
            targets,
            owners=node_ids,
            label="logical-qubit compute region",
            module_type="compute",
            submodule_type="region",
            payload="logical_qubit",
        ),
        compute_store_load=_single_target(
            targets,
            owners=node_ids,
            label="compute-side Store/Load buffer",
            module_type="compute",
            submodule_type="buffer",
            payload="logical_qubit",
        ),
        magic_input=_single_target(
            targets,
            owners=node_ids,
            label="compute-side magic-state buffer",
            module_type="compute",
            submodule_type="buffer",
            payload="magic_state",
        ),
        factory=_single_target(
            targets,
            owners=node_ids,
            label="local magic-state factory engine",
            module_type="resource_factory",
            submodule_type="engine",
            payload="magic_state",
        ),
        magic_output=_single_target(
            targets,
            owners=node_ids,
            label="local magic-state output buffer",
            module_type="resource_factory",
            submodule_type="buffer",
            payload="magic_state",
        ),
        bell_engine=_single_target(
            targets,
            owners=interconnect_ids,
            label="shared Bell-pair engine",
            module_type="bell_engine",
            submodule_type="engine",
            payload="bell_pair",
        ),
        bell_buffer=_single_target(
            targets,
            owners=interconnect_ids,
            label="shared logical Bell-pair buffer",
            module_type="bell_storage",
            submodule_type="buffer",
            payload="bell_pair",
        ),
    )

    nodes = {node.id: node for node in profile.nodes}
    memory_node = nodes[group.memory.owner_id]
    compute_node = nodes[group.compute.owner_id]
    if memory_node.modality != "neutral_atom":
        raise ValueError(
            "HybridMemoryComputeSizingPolicy needs a neutral-atom memory Node"
        )
    if compute_node.modality != "superconducting":
        raise ValueError(
            "HybridMemoryComputeSizingPolicy needs a superconducting compute Node"
        )
    if memory_node.id == compute_node.id:
        raise ValueError(
            "HybridMemoryComputeSizingPolicy needs remote memory and compute Nodes"
        )
    if group.memory_store_load.owner_id != memory_node.id:
        raise ValueError(
            "HybridMemoryComputeSizingPolicy needs the memory endpoint on the "
            "memory Node"
        )
    if group.memory_store_load.module_id != group.memory.module_id:
        raise ValueError(
            "HybridMemoryComputeSizingPolicy needs the memory region and its "
            "Store/Load endpoint in one Module"
        )
    if group.compute_store_load.owner_id != compute_node.id:
        raise ValueError(
            "HybridMemoryComputeSizingPolicy needs the compute endpoint on the "
            "compute Node"
        )
    if (
        group.compute_store_load.module_id != group.compute.module_id
        or group.magic_input.owner_id != compute_node.id
        or group.magic_input.module_id != group.compute.module_id
    ):
        raise ValueError(
            "HybridMemoryComputeSizingPolicy needs the compute region, its "
            "Store/Load endpoint, and magic input in one Module"
        )
    if (
        group.factory.owner_id != compute_node.id
        or group.magic_output.owner_id != compute_node.id
        or group.magic_output.module_id != group.factory.module_id
    ):
        raise ValueError(
            "HybridMemoryComputeSizingPolicy needs a local factory engine and "
            "output buffer in one Module on the superconducting compute Node"
        )
    if group.bell_engine.owner_id != group.bell_buffer.owner_id:
        raise ValueError(
            "HybridMemoryComputeSizingPolicy needs one shared Bell resource owner"
        )

    interconnect = next(
        item
        for item in profile.interconnects
        if item.id == group.bell_engine.owner_id
    )
    expected_endpoints = {
        str(group.memory_store_load),
        str(group.compute_store_load),
    }
    if set(interconnect.endpoints) != expected_endpoints:
        raise ValueError(
            "HybridMemoryComputeSizingPolicy needs the shared Interconnect to "
            "attach the two Store/Load buffers"
        )
    expected_bell_connection = (
        f"{group.bell_engine.module_id}/{group.bell_engine.submodule_id}",
        f"{group.bell_buffer.module_id}/{group.bell_buffer.submodule_id}",
    )
    bell_connections = [
        connection
        for connection in interconnect.connections
        if connection.direction == "directed"
        and connection.endpoints == expected_bell_connection
    ]
    if len(bell_connections) != 1:
        raise ValueError(
            "HybridMemoryComputeSizingPolicy needs exactly one directed Bell "
            "engine-to-buffer connection inside the Interconnect"
        )

    expected_magic_connection = (
        f"{group.magic_output.module_id}/{group.magic_output.submodule_id}",
        f"{group.magic_input.module_id}/{group.magic_input.submodule_id}",
    )
    magic_connections = [
        connection
        for connection in compute_node.connections
        if connection.direction == "directed"
        and connection.endpoints == expected_magic_connection
    ]
    if len(magic_connections) != 1:
        raise ValueError(
            "HybridMemoryComputeSizingPolicy needs exactly one local "
            "magic-state connection from the factory output to the compute input"
        )
    return group


def _validated_protocols(
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


def _required_protocol(
    selected: Mapping[SubmoduleKey, QECResourceProtocolProfile],
    target: SubmoduleKey,
    expected_type: type[MagicStateFactoryProfile]
    | type[EntanglementDistillationProfile],
) -> MagicStateFactoryProfile | EntanglementDistillationProfile:
    try:
        protocol = selected[target]
    except KeyError as exc:
        raise ValueError(
            f"Sizing resource engine {target} needs a selected QEC protocol profile"
        ) from exc
    if not isinstance(protocol, expected_type):
        raise TypeError(
            f"Selected QEC protocol for {target} must be a "
            f"{expected_type.__name__}"
        )
    return protocol


@dataclass(frozen=True, slots=True)
class HybridMemoryComputeSizingPolicy(SizingPolicy):
    """Size one neutral-atom memory and superconducting compute partition.

    The two Node endpoint buffers are one Store/Load wave and therefore always
    receive the same capacity. The Interconnect owns one shared Bell buffer,
    which follows that wave by default, and one Bell engine whose capacity is
    a copy count. Bell copies are just sufficient to saturate the selected
    protocol's reference physical-link ceiling, capped by the buffer wave.

    Magic-state production is local to the superconducting compute Node and
    is independent of the Bell path. This policy supports exactly one memory,
    one compute, and one connecting Interconnect; larger fabrics need an
    explicit allocation policy rather than ID- or order-based pairing.
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
        """Return capacities for both Nodes and their shared Interconnect."""

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
        group = _hybrid_targets(profile, targets)
        exact = _validated_overrides(overrides, targets)
        protocols = _validated_protocols(selected_qec_protocols, targets)

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
                "HybridMemoryComputeSizingPolicy needs a positive compute capacity"
            )
        maximum_operation_width = max(statistics.operation_widths, default=0)
        if maximum_operation_width > compute_capacity:
            raise ValueError(
                "Compute capacity cannot hold one logical operation: "
                f"width={maximum_operation_width}, capacity={compute_capacity}"
            )

        endpoint_overrides = {
            exact[target]
            for target in (
                group.memory_store_load,
                group.compute_store_load,
            )
            if target in exact
        }
        if len(endpoint_overrides) > 1:
            raise ValueError(
                "Pinned Store/Load endpoint capacities must be equal"
            )
        if endpoint_overrides:
            store_load_capacity = endpoint_overrides.pop()
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
                "Store/Load endpoint capacity cannot exceed compute capacity"
            )
        if store_load_capacity == 0:
            raise ValueError(
                "A non-empty compute region needs positive Store/Load endpoints"
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
            factory_protocol = _required_protocol(
                protocols,
                group.factory,
                MagicStateFactoryProfile,
            )
            factory_capacity = max(
                1,
                math.ceil(
                    magic_output_capacity / factory_protocol.outputs_per_batch
                ),
            )
            dependencies[group.factory] = factory_protocol.profile_hash

        bell_buffer_capacity = exact.get(
            group.bell_buffer,
            store_load_capacity,
        )
        if bell_buffer_capacity <= 0:
            raise ValueError("The shared logical Bell buffer must be positive")

        if group.bell_engine in exact:
            bell_engine_capacity = exact[group.bell_engine]
        else:
            bell_protocol = _required_protocol(
                protocols,
                group.bell_engine,
                EntanglementDistillationProfile,
            )
            local_rate_per_copy_per_s = (
                bell_protocol.outputs_per_batch
                / (
                    bell_protocol.qec_cycles_per_batch
                    * bell_protocol.qec_cycle_time_s
                )
            )
            shared_link_ceiling_per_s = (
                bell_protocol.reference_physical_bell_pair_rate_per_s
                / bell_protocol.raw_bell_pairs_per_output
            )
            rate_matched_copies = max(
                1,
                math.ceil(
                    shared_link_ceiling_per_s
                    / local_rate_per_copy_per_s
                ),
            )
            transfer_wave_cap = max(
                1,
                math.ceil(
                    bell_buffer_capacity / bell_protocol.outputs_per_batch
                ),
            )
            bell_engine_capacity = min(
                rate_matched_copies,
                transfer_wave_cap,
            )
            dependencies[group.bell_engine] = bell_protocol.profile_hash

        capacities: dict[SubmoduleKey, int] = {
            group.memory: memory_capacity,
            group.memory_store_load: store_load_capacity,
            group.compute: compute_capacity,
            group.compute_store_load: store_load_capacity,
            group.magic_input: magic_input_capacity,
            group.factory: factory_capacity,
            group.magic_output: magic_output_capacity,
            group.bell_engine: bell_engine_capacity,
            group.bell_buffer: bell_buffer_capacity,
        }
        for target in targets:
            if target in group.all:
                continue
            try:
                capacities[target] = exact[target]
            except KeyError as exc:
                raise ValueError(
                    "HybridMemoryComputeSizingPolicy has no sizing relation for "
                    f"{target}; provide an exact override or another SizingPolicy"
                ) from exc
        return SizingResult(
            capacities,
            qec_protocol_dependencies=dependencies,
        )


def make_sizing_policy(
    config: QuantileSizingConfig,
) -> HybridMemoryComputeSizingPolicy:
    """Bind shared experiment quantiles to NA-M + SC-CF's sizing relation."""

    if not isinstance(config, QuantileSizingConfig):
        raise TypeError("config must be a QuantileSizingConfig")

    return HybridMemoryComputeSizingPolicy(
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
    "HybridMemoryComputeSizingPolicy",
    "make_sizing_policy",
]
