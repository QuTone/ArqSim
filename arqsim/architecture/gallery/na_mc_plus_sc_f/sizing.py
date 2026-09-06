"""Sizing for local memory/compute served by a remote magic factory."""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping

from arqsim.program.layout import store_load_transition_demands
from arqsim.program.statistics import CircuitStatistics
from arqsim.qec.protocol import (
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
class _RemoteMagicMemoryComputeTargets:
    memory: SubmoduleKey
    compute: SubmoduleKey
    store_load: SubmoduleKey
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
                self.compute,
                self.store_load,
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


def _positive_real(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a real number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return result


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
            "RemoteMagicMemoryComputeSizingPolicy needs exactly one "
            f"{label}; found {len(matches)}"
        )
    return matches[0]


def _targets(
    profile: ArchitectureProfile,
    targets: Mapping[
        SubmoduleKey,
        tuple[ProfileModule, ProfileSubmodule],
    ],
) -> _RemoteMagicMemoryComputeTargets:
    node_ids = frozenset(node.id for node in profile.nodes)
    interconnect_ids = frozenset(item.id for item in profile.interconnects)
    group = _RemoteMagicMemoryComputeTargets(
        memory=_single_target(
            targets,
            owners=node_ids,
            label="logical-qubit memory region",
            module_type="memory",
            submodule_type="region",
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
        store_load=_single_target(
            targets,
            owners=node_ids,
            label="logical-qubit Store/Load buffer",
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
            label="remote magic-state factory engine",
            module_type="resource_factory",
            submodule_type="engine",
            payload="magic_state",
        ),
        magic_output=_single_target(
            targets,
            owners=node_ids,
            label="remote magic-state output buffer",
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
    local_targets = (
        group.memory,
        group.compute,
        group.store_load,
        group.magic_input,
    )
    if len({target.owner_id for target in local_targets}) != 1:
        raise ValueError(
            "RemoteMagicMemoryComputeSizingPolicy needs memory, compute, "
            "Store/Load, and magic input on one Node"
        )
    if group.store_load.module_id != group.compute.module_id:
        raise ValueError(
            "RemoteMagicMemoryComputeSizingPolicy needs the Store/Load buffer "
            "inside the compute Module"
        )
    if group.magic_input.module_id != group.compute.module_id:
        raise ValueError(
            "RemoteMagicMemoryComputeSizingPolicy needs the magic input "
            "inside the compute Module"
        )
    compute_node = nodes[group.compute.owner_id]
    if compute_node.modality != "neutral_atom":
        raise ValueError(
            "RemoteMagicMemoryComputeSizingPolicy needs a neutral-atom "
            "memory/compute Node"
        )
    expected_memory_connection = {
        f"{group.memory.module_id}/{group.memory.submodule_id}",
        f"{group.store_load.module_id}/{group.store_load.submodule_id}",
    }
    memory_connections = [
        connection
        for connection in compute_node.connections
        if connection.direction == "bidirectional"
        and set(connection.endpoints) == expected_memory_connection
    ]
    if len(memory_connections) != 1:
        raise ValueError(
            "RemoteMagicMemoryComputeSizingPolicy needs exactly one "
            "bidirectional memory-region/Store-Load connection"
        )

    if group.factory.owner_id != group.magic_output.owner_id:
        raise ValueError(
            "RemoteMagicMemoryComputeSizingPolicy needs the factory and "
            "magic output on one Node"
        )
    if group.factory.module_id != group.magic_output.module_id:
        raise ValueError(
            "RemoteMagicMemoryComputeSizingPolicy needs the factory engine "
            "and output buffer inside one Module"
        )
    factory_node = nodes[group.factory.owner_id]
    if factory_node.modality != "superconducting":
        raise ValueError(
            "RemoteMagicMemoryComputeSizingPolicy needs a superconducting "
            "factory Node"
        )
    if compute_node.id == factory_node.id:
        raise ValueError(
            "RemoteMagicMemoryComputeSizingPolicy needs a remote factory Node"
        )

    if group.bell_engine.owner_id != group.bell_buffer.owner_id:
        raise ValueError(
            "RemoteMagicMemoryComputeSizingPolicy needs one shared Bell "
            "resource owner"
        )
    interconnect = next(
        item
        for item in profile.interconnects
        if item.id == group.bell_engine.owner_id
    )
    if set(interconnect.endpoints) != {
        str(group.magic_input),
        str(group.magic_output),
    }:
        raise ValueError(
            "RemoteMagicMemoryComputeSizingPolicy needs the shared "
            "Interconnect to attach the remote magic input and output buffers"
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
            "RemoteMagicMemoryComputeSizingPolicy needs exactly one directed "
            "Bell-engine-to-storage connection"
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
class RemoteMagicMemoryComputeSizingPolicy(SizingPolicy):
    """Size one local memory/compute pool supplied by a remote factory.

    Compute and memory form one atomic partition. Store/Load demand is local
    to that partition. Magic demand sizes the compute-side input and the
    remote factory output independently of Store/Load traffic. The shared
    Bell buffer follows the magic input transfer wave, and Bell-engine copies
    are rate matched to the remote factory and physical-link ceiling, capped
    by that wave.

    The policy deliberately supports one memory/compute pool, one factory,
    and one shared Interconnect. More general allocation needs an explicit
    policy rather than target pairing based on identifiers or declaration
    order.
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
    factory_qec_cycle_time_s: float

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
        quantiles: dict[str, float] = {}
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
            quantiles[representation] = _probability(
                probability,
                name=f"Store/Load quantile for {representation}",
            )
        object.__setattr__(
            self,
            "store_load_quantiles_by_representation",
            MappingProxyType(dict(sorted(quantiles.items()))),
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
        object.__setattr__(
            self,
            "factory_qec_cycle_time_s",
            _positive_real(
                self.factory_qec_cycle_time_s,
                name="factory_qec_cycle_time_s",
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
        """Return exact capacities for the local pool and remote resources."""

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
        group = _targets(profile, targets)
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
                "RemoteMagicMemoryComputeSizingPolicy needs a positive "
                "compute capacity"
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
                "Remote magic-state input and output buffers must be positive"
            )

        dependencies: dict[SubmoduleKey, str] = {}
        factory_protocol: MagicStateFactoryProfile | None = None
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
            magic_input_capacity,
        )
        if bell_buffer_capacity <= 0:
            raise ValueError("The shared logical Bell buffer must be positive")

        if group.bell_engine in exact:
            bell_engine_capacity = exact[group.bell_engine]
        else:
            if factory_protocol is None:
                factory_protocol = _required_protocol(
                    protocols,
                    group.factory,
                    MagicStateFactoryProfile,
                )
            bell_protocol = _required_protocol(
                protocols,
                group.bell_engine,
                EntanglementDistillationProfile,
            )
            factory_rate_per_s = (
                factory_capacity
                * factory_protocol.outputs_per_batch
                / (
                    factory_protocol.cycles_per_batch
                    * self.factory_qec_cycle_time_s
                )
            )
            bell_local_rate_per_copy_per_s = (
                bell_protocol.outputs_per_batch
                / (
                    bell_protocol.qec_cycles_per_batch
                    * bell_protocol.qec_cycle_time_s
                )
            )
            bell_shared_link_rate_per_s = (
                bell_protocol.reference_physical_bell_pair_rate_per_s
                / bell_protocol.raw_bell_pairs_per_output
            )
            rate_target_per_s = min(
                factory_rate_per_s,
                bell_shared_link_rate_per_s,
            )
            transfer_wave_cap = max(
                1,
                math.ceil(
                    bell_buffer_capacity / bell_protocol.outputs_per_batch
                ),
            )
            rate_matched_copies = max(
                1,
                math.ceil(
                    rate_target_per_s / bell_local_rate_per_copy_per_s
                ),
            )
            bell_engine_capacity = min(
                transfer_wave_cap,
                rate_matched_copies,
            )
            dependencies[group.factory] = factory_protocol.profile_hash
            dependencies[group.bell_engine] = bell_protocol.profile_hash

        capacities: dict[SubmoduleKey, int] = {
            group.memory: memory_capacity,
            group.compute: compute_capacity,
            group.store_load: store_load_capacity,
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
                    "RemoteMagicMemoryComputeSizingPolicy has no sizing "
                    f"relation for {target}; provide an exact override or "
                    "another SizingPolicy"
                ) from exc
        return SizingResult(
            capacities,
            qec_protocol_dependencies=dependencies,
        )


def make_sizing_policy(
    config: QuantileSizingConfig,
) -> RemoteMagicMemoryComputeSizingPolicy:
    """Bind shared experiment quantiles to NA-MC + SC-F's sizing relation."""

    if not isinstance(config, QuantileSizingConfig):
        raise TypeError("config must be a QuantileSizingConfig")

    return RemoteMagicMemoryComputeSizingPolicy(
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
        factory_qec_cycle_time_s=1.0e-6,
    )


__all__ = [
    "RemoteMagicMemoryComputeSizingPolicy",
    "make_sizing_policy",
]
