"""Sizing for compute nodes served by a remote magic-state factory."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

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
class _RemoteMagicTargets:
    compute: SubmoduleKey
    magic_input: SubmoduleKey
    factory: SubmoduleKey
    magic_output: SubmoduleKey
    bell_engine: SubmoduleKey
    bell_buffer: SubmoduleKey

    @property
    def all(self) -> frozenset[SubmoduleKey]:
        return frozenset(
            {
                self.compute,
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
            "RemoteMagicSizingPolicy needs exactly one "
            f"{label}; found {len(matches)}"
        )
    return matches[0]


def _remote_magic_targets(
    profile: ArchitectureProfile,
    targets: Mapping[
        SubmoduleKey,
        tuple[ProfileModule, ProfileSubmodule],
    ],
) -> _RemoteMagicTargets:
    node_ids = frozenset(node.id for node in profile.nodes)
    interconnect_ids = frozenset(item.id for item in profile.interconnects)
    group = _RemoteMagicTargets(
        compute=_single_target(
            targets,
            owners=node_ids,
            label="logical-qubit compute region",
            module_type="compute",
            submodule_type="region",
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

    compute_node = next(
        node for node in profile.nodes if node.id == group.compute.owner_id
    )
    factory_node = next(
        node for node in profile.nodes if node.id == group.factory.owner_id
    )
    if compute_node.modality != "neutral_atom":
        raise ValueError(
            "RemoteMagicSizingPolicy needs a neutral-atom compute Node"
        )
    if factory_node.modality != "superconducting":
        raise ValueError(
            "RemoteMagicSizingPolicy needs a superconducting factory Node"
        )
    if group.magic_input.owner_id != group.compute.owner_id:
        raise ValueError(
            "RemoteMagicSizingPolicy needs compute and magic input on one Node"
        )
    if group.magic_input.module_id != group.compute.module_id:
        raise ValueError(
            "RemoteMagicSizingPolicy needs compute and magic input in one Module"
        )
    if group.magic_output.owner_id != group.factory.owner_id:
        raise ValueError(
            "RemoteMagicSizingPolicy needs factory and output on one Node"
        )
    if group.magic_output.module_id != group.factory.module_id:
        raise ValueError(
            "RemoteMagicSizingPolicy needs factory and output in one Module"
        )
    if group.bell_engine.owner_id != group.bell_buffer.owner_id:
        raise ValueError(
            "RemoteMagicSizingPolicy needs one shared Bell resource owner"
        )

    interconnect = next(
        item
        for item in profile.interconnects
        if item.id == group.bell_engine.owner_id
    )
    expected_endpoints = {
        str(group.magic_input),
        str(group.magic_output),
    }
    if set(interconnect.endpoints) != expected_endpoints:
        raise ValueError(
            "RemoteMagicSizingPolicy needs the shared Interconnect to attach "
            "the remote magic input and output buffers"
        )
    expected_connection = (
        f"{group.bell_engine.module_id}/{group.bell_engine.submodule_id}",
        f"{group.bell_buffer.module_id}/{group.bell_buffer.submodule_id}",
    )
    matching_connections = [
        connection
        for connection in interconnect.connections
        if connection.direction == "directed"
        and connection.endpoints == expected_connection
    ]
    if len(matching_connections) != 1:
        raise ValueError(
            "RemoteMagicSizingPolicy needs exactly one directed shared "
            "Bell engine-to-storage connection"
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
class RemoteMagicSizingPolicy(SizingPolicy):
    """Size one compute Node supplied by one remote magic-state factory.

    The Interconnect owns one shared Bell engine and one shared Bell buffer.
    Bell capacity is therefore counted once, regardless of the two endpoint
    halves needed by a concrete implementation. The default buffer absorbs
    one compute-side magic transfer wave. Bell-engine copies are rate matched
    to the remote factory and physical-link ceilings, then capped by that
    transfer wave.

    This policy intentionally models one remote-magic path. Profiles with
    several factories or Interconnects need an explicit allocation policy;
    target pairing is never inferred from IDs or declaration order.
    """

    magic_state_quantile: float
    magic_state_rounding: RoundingMode
    minimum_magic_state_capacity: int
    factory_qec_cycle_time_s: float

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
        """Return capacities for the Nodes and their shared Interconnect."""

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
        group = _remote_magic_targets(profile, targets)
        exact = _validated_overrides(overrides, targets)
        protocols = _validated_protocols(selected_qec_protocols, targets)

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
        compute_capacity = exact.get(
            group.compute,
            statistics.logical_qubits,
        )
        if compute_capacity != statistics.logical_qubits:
            raise ValueError(
                "RemoteMagicSizingPolicy compute capacity must equal the "
                "circuit logical-qubit count because this Profile has no "
                "logical-qubit memory owner"
            )
        if compute_capacity <= 0:
            raise ValueError(
                "RemoteMagicSizingPolicy needs a positive compute capacity"
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
            group.compute: compute_capacity,
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
                    "RemoteMagicSizingPolicy has no sizing relation for "
                    f"{target}; provide an exact override or another SizingPolicy"
                ) from exc
        return SizingResult(
            capacities,
            qec_protocol_dependencies=dependencies,
        )


def make_sizing_policy(
    config: QuantileSizingConfig,
) -> RemoteMagicSizingPolicy:
    """Translate one experiment quantile point into the NA-C + SC-F policy."""

    if not isinstance(config, QuantileSizingConfig):
        raise TypeError("config must be a QuantileSizingConfig")
    return RemoteMagicSizingPolicy(
        magic_state_quantile=config.magic_state_quantile,
        magic_state_rounding=config.buffer_rounding,
        minimum_magic_state_capacity=1,
        factory_qec_cycle_time_s=1.0e-6,
    )


__all__ = ["RemoteMagicSizingPolicy", "make_sizing_policy"]
