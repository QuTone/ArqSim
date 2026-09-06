"""Generic materialization of one canonical architecture specification.

Sizing, QEC selection, QEC resource-protocol selection, and logical placement are
completed before this module runs.  The resolver only joins those completed
construction results to the unsized Profile hierarchy.  It therefore contains no
circuit model, policy algorithm, Profile-ID dispatch, or compiler adapter.
"""

from __future__ import annotations

from typing import Mapping

from arqsim.qec.protocol import QECResourceProtocolProfile

from .errors import ArchitectureValidationError
from .identifiers import SubmoduleKey
from .logical_layout import LogicalLayoutResult
from .profile import (
    ArchitectureProfile,
    ProfileLocalConnection,
    ProfileModule,
    ProfileSubmodule,
)
from .sizing import SizingResult
from .specification import (
    ArchitectureSpecification,
    Interconnect,
    LocalConnection,
    LogicalSlot,
    Module,
    Node,
    QECBinding,
    QECResourceProtocolRef,
    Submodule,
)


ARCHITECTURE_RESOLVER_ID = "arqsim.architecture-resolver.v1"


def _invalid(message: str, **details: object) -> None:
    raise ArchitectureValidationError(message, details=details)


def _profile_targets(
    profile: ArchitectureProfile,
) -> dict[SubmoduleKey, ProfileSubmodule]:
    return {
        SubmoduleKey(owner.id, module.id, submodule.id): submodule
        for owner in (*profile.nodes, *profile.interconnects)
        for module in owner.modules
        for submodule in module.submodules
    }


def _materialize_modules(
    *,
    owner_id: str,
    profile_modules: tuple[ProfileModule, ...],
    sizing: SizingResult,
    logical_layout: LogicalLayoutResult,
    qec: Mapping[SubmoduleKey, QECBinding],
    qec_protocols: Mapping[SubmoduleKey, QECResourceProtocolProfile],
) -> tuple[Module, ...]:
    """Join completed construction facts for one resource owner."""

    modules: list[Module] = []
    for profile_module in profile_modules:
        submodules: list[Submodule] = []
        for profile_submodule in profile_module.submodules:
            target = SubmoduleKey(
                owner_id,
                profile_module.id,
                profile_submodule.id,
            )
            placement = (
                None
                if profile_submodule.type == "engine"
                else logical_layout.layout_for(target)
            )
            protocol = qec_protocols.get(target)
            slots = (
                ()
                if placement is None
                else tuple(
                    LogicalSlot(slot_id, placement.coordinates.get(slot_id))
                    for slot_id in placement.slot_ids
                )
            )
            grid_shape = (
                (placement.grid.rows, placement.grid.columns)
                if placement is not None and placement.grid is not None
                else None
            )
            submodules.append(
                Submodule(
                    id=profile_submodule.id,
                    type=profile_submodule.type,
                    payload=profile_submodule.payload,
                    capacity=sizing.capacity_for(target),
                    qec=qec.get(target),
                    slots=slots,
                    logical_origin=(
                        placement.logical_origin
                        if placement is not None
                        else None
                    ),
                    grid_shape=grid_shape,
                    resource_protocol=(
                        QECResourceProtocolRef(
                            protocol.id,
                            protocol.profile_hash,
                        )
                        if protocol is not None
                        else None
                    ),
                )
            )
        modules.append(
            Module(
                id=profile_module.id,
                type=profile_module.type,
                submodules=tuple(submodules),
            )
        )
    return tuple(modules)


def _materialize_connections(
    values: tuple[ProfileLocalConnection, ...],
) -> tuple[LocalConnection, ...]:
    return tuple(
        LocalConnection(
            id=connection.id,
            direction=connection.direction,
            endpoints=connection.endpoints,
        )
        for connection in values
    )


def _require_exact_targets(
    *,
    label: str,
    expected: set[SubmoduleKey],
    actual: set[SubmoduleKey],
) -> None:
    missing = expected - actual
    unknown = actual - expected
    if missing or unknown:
        _invalid(
            f"{label} does not cover the Profile exactly",
            missing=sorted(str(target) for target in missing),
            unknown=sorted(str(target) for target in unknown),
        )


def _qec_bindings(
    values: Mapping[SubmoduleKey, QECBinding] | None,
    *,
    targets: Mapping[SubmoduleKey, ProfileSubmodule],
) -> Mapping[SubmoduleKey, QECBinding]:
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise TypeError("qec_bindings must be a mapping")
    for target, binding in values.items():
        if not isinstance(target, SubmoduleKey):
            raise TypeError("qec_bindings keys must be SubmoduleKey values")
        if target not in targets:
            _invalid("QEC binding targets an unknown Submodule", target=str(target))
        if not isinstance(binding, QECBinding):
            raise TypeError(f"QEC binding for {target} must be a QECBinding")
        if targets[target].type == "engine":
            _invalid("QEC binding cannot target a resource engine", target=str(target))
    return values


def _selected_qec_protocols(
    values: Mapping[SubmoduleKey, QECResourceProtocolProfile] | None,
    *,
    targets: Mapping[SubmoduleKey, ProfileSubmodule],
) -> Mapping[SubmoduleKey, QECResourceProtocolProfile]:
    required = {
        target for target, submodule in targets.items() if submodule.type == "engine"
    }
    if values is None:
        values = {}
    if not isinstance(values, Mapping):
        raise TypeError("selected_qec_protocols must be a mapping")
    for target, protocol in values.items():
        if not isinstance(target, SubmoduleKey):
            raise TypeError(
                "selected_qec_protocols keys must be SubmoduleKey values"
            )
        if target not in targets:
            _invalid(
                "Selected QEC protocol targets an unknown Submodule",
                target=str(target),
            )
        submodule = targets[target]
        if submodule.type != "engine":
            _invalid(
                "Selected QEC protocol must target a resource engine",
                target=str(target),
            )
        if not isinstance(protocol, QECResourceProtocolProfile):
            raise TypeError(
                f"Selected QEC protocol for {target} must be a catalog profile"
            )
        if protocol.resource_payload != submodule.payload:
            _invalid(
                "Selected QEC protocol payload does not match its resource engine",
                target=str(target),
                protocol_payload=protocol.resource_payload,
                engine_payload=submodule.payload,
            )
    actual = set(values)
    if actual != required:
        _invalid(
            "Selected QEC protocols do not cover resource engines exactly",
            missing=sorted(str(target) for target in required - actual),
            unknown=sorted(str(target) for target in actual - required),
        )
    return values


def resolve_architecture(
    profile: ArchitectureProfile,
    sizing: SizingResult,
    logical_layout: LogicalLayoutResult,
    *,
    qec_bindings: Mapping[SubmoduleKey, QECBinding] | None = None,
    selected_qec_protocols: Mapping[SubmoduleKey, QECResourceProtocolProfile]
    | None = None,
) -> ArchitectureSpecification:
    """Materialize one Profile using already-resolved policy results.

    The function is deliberately mechanical: every Profile Submodule receives
    one capacity and one complete logical layout result.  QEC bindings and QEC
    resource-protocol selections are exact-target annotations.  Unknown or
    incomplete policy results fail before any architecture object is constructed.

    Nodes and Interconnects use the same owner-nested materialization path;
    only their final topology records differ.
    """

    if not isinstance(profile, ArchitectureProfile):
        raise TypeError("profile must be an ArchitectureProfile")
    if not isinstance(sizing, SizingResult):
        raise TypeError("sizing must be a SizingResult")
    if not isinstance(logical_layout, LogicalLayoutResult):
        raise TypeError("logical_layout must be a LogicalLayoutResult")
    targets = _profile_targets(profile)
    expected = set(targets)
    _require_exact_targets(
        label="SizingResult",
        expected=expected,
        actual=set(sizing.capacities),
    )
    _require_exact_targets(
        label="LogicalLayoutResult",
        expected={
            target
            for target, submodule in targets.items()
            if submodule.type != "engine"
        },
        actual=set(logical_layout.submodules),
    )
    qec = _qec_bindings(qec_bindings, targets=targets)
    qec_protocols = _selected_qec_protocols(
        selected_qec_protocols,
        targets=targets,
    )
    for target, expected_hash in sizing.qec_protocol_dependencies.items():
        protocol = qec_protocols.get(target)
        if protocol is None or protocol.profile_hash != expected_hash:
            _invalid(
                "Selected QEC protocol differs from the one used for sizing",
                target=str(target),
                sizing_profile_hash=expected_hash,
                selected_profile_hash=(
                    protocol.profile_hash if protocol is not None else None
                ),
            )

    nodes = tuple(
        Node(
            id=profile_node.id,
            modality=profile_node.modality,
            modules=_materialize_modules(
                owner_id=profile_node.id,
                profile_modules=profile_node.modules,
                sizing=sizing,
                logical_layout=logical_layout,
                qec=qec,
                qec_protocols=qec_protocols,
            ),
            connections=_materialize_connections(profile_node.connections),
        )
        for profile_node in profile.nodes
    )
    interconnects = tuple(
        Interconnect(
            id=profile_interconnect.id,
            endpoints=profile_interconnect.endpoints,
            modules=_materialize_modules(
                owner_id=profile_interconnect.id,
                profile_modules=profile_interconnect.modules,
                sizing=sizing,
                logical_layout=logical_layout,
                qec=qec,
                qec_protocols=qec_protocols,
            ),
            connections=_materialize_connections(profile_interconnect.connections),
        )
        for profile_interconnect in profile.interconnects
    )
    return ArchitectureSpecification(
        nodes=nodes,
        interconnects=interconnects,
    )


__all__ = ["ARCHITECTURE_RESOLVER_ID", "resolve_architecture"]
