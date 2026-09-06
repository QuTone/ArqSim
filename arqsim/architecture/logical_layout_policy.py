"""Policies that materialize logical slot identities and placement.

Sizing decides *how many* logical resources each Submodule owns.  This module
decides which stable slot identities represent that capacity and, where the
architecture needs spatial routing, where those slots live on an owner-local
logical canvas. QEC bindings, QEC resource protocols, and physical-qubit
placement are deliberately outside this boundary.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import math
from typing import NoReturn

from .errors import ArchitectureValidationError
from .identifiers import SubmoduleKey
from .logical_layout import (
    LogicalCoordinate,
    LogicalLayoutGrid,
    LogicalLayoutRequest,
    LogicalLayoutResult,
    SubmoduleLayoutRequest,
    SubmoduleLayoutResult,
)
from .profile import ArchitectureProfile, ProfileModule, ProfileSubmodule
from .sizing import SizingResult


_ProfileRecord = tuple[str, ProfileModule, ProfileSubmodule, SubmoduleKey]


def _invalid(message: str, **details: object) -> NoReturn:
    raise ArchitectureValidationError(message, details=details)


def _coordinate(value: object, *, name: str) -> LogicalCoordinate:
    if isinstance(value, (str, bytes)) or not isinstance(value, (tuple, list)):
        raise TypeError(f"{name} must be a two-integer coordinate")
    if len(value) != 2 or any(type(component) is not int for component in value):
        raise ValueError(f"{name} must be a two-integer coordinate")
    return value[0], value[1]


def _square_layout(
    slot_ids: tuple[str, ...],
    *,
    origin: LogicalCoordinate,
    keep_grid: bool,
) -> SubmoduleLayoutResult:
    columns = max(1, math.ceil(math.sqrt(len(slot_ids))))
    rows = max(1, math.ceil(len(slot_ids) / columns))
    grid = LogicalLayoutGrid(rows=rows, columns=columns)
    return SubmoduleLayoutResult(
        slot_ids=slot_ids,
        coordinates={
            slot_id: (index % columns, index // columns)
            for index, slot_id in enumerate(slot_ids)
        },
        logical_origin=origin,
        grid=grid if keep_grid else None,
    )


def _east_columns_layout(
    slot_ids: tuple[str, ...],
    upstream: SubmoduleLayoutResult,
    *,
    offset: int,
) -> SubmoduleLayoutResult:
    """Place columns east of the upstream grid or occupied envelope."""

    if not upstream.coordinates:
        raise ValueError("east-edge placement needs a spatial upstream layout")
    upstream_origin = upstream.logical_origin or (0, 0)
    if upstream.grid is not None:
        first_x = upstream_origin[0] + upstream.grid.columns - 1 + offset
        first_y = upstream_origin[1]
        rows = upstream.grid.rows
    else:
        points = [
            upstream.coordinate_for(slot_id) for slot_id in upstream.slot_ids
        ]
        spatial = [point for point in points if point is not None]
        first_x = max(point[0] for point in spatial) + offset
        first_y = min(point[1] for point in spatial)
        rows = max(point[1] for point in spatial) - first_y + 1
    return SubmoduleLayoutResult(
        slot_ids=slot_ids,
        coordinates={
            slot_id: (index // rows, index % rows)
            for index, slot_id in enumerate(slot_ids)
        },
        logical_origin=(first_x, first_y),
    )


def _west_columns_layout(
    slot_ids: tuple[str, ...],
    upstream: SubmoduleLayoutResult,
    *,
    offset: int,
) -> SubmoduleLayoutResult:
    """Place a row-major buffer bank west of an upstream envelope."""

    if not upstream.coordinates:
        raise ValueError("west-edge placement needs a spatial upstream layout")
    upstream_origin = upstream.logical_origin or (0, 0)
    if upstream.grid is not None:
        x_min = upstream_origin[0]
        y_min = upstream_origin[1]
        rows = upstream.grid.rows
    else:
        points = [
            upstream.coordinate_for(slot_id) for slot_id in upstream.slot_ids
        ]
        spatial = [point for point in points if point is not None]
        x_min = min(point[0] for point in spatial)
        y_min = min(point[1] for point in spatial)
        rows = max(point[1] for point in spatial) - y_min + 1
    columns = max(1, math.ceil(len(slot_ids) / rows))
    leftmost_x = x_min - offset - columns + 1
    return SubmoduleLayoutResult(
        slot_ids=slot_ids,
        coordinates={
            slot_id: (index % columns, index // columns)
            for index, slot_id in enumerate(slot_ids)
        },
        logical_origin=(leftmost_x, y_min),
    )


def _strided_square_layout(
    slot_ids: tuple[str, ...],
    *,
    origin: LogicalCoordinate,
    stride: int,
) -> SubmoduleLayoutResult:
    """Place a square grid with a fixed gap between occupied cells."""

    patch_columns = max(1, math.ceil(math.sqrt(len(slot_ids))))
    patch_rows = max(1, math.ceil(len(slot_ids) / patch_columns))
    return SubmoduleLayoutResult(
        slot_ids=slot_ids,
        coordinates={
            slot_id: (
                (index % patch_columns) * stride,
                (index // patch_columns) * stride,
            )
            for index, slot_id in enumerate(slot_ids)
        },
        logical_origin=origin,
        grid=LogicalLayoutGrid(
            rows=(patch_rows - 1) * stride + 1,
            columns=(patch_columns - 1) * stride + 1,
        ),
    )


def _east_strided_columns_layout(
    slot_ids: tuple[str, ...],
    upstream: SubmoduleLayoutResult,
    *,
    stride: int,
) -> SubmoduleLayoutResult:
    """Place strided columns east of an upstream occupied envelope."""

    if not upstream.coordinates:
        raise ValueError("strided east-edge placement needs spatial input")
    origin = upstream.logical_origin or (0, 0)
    if upstream.grid is not None:
        first_x = origin[0] + math.ceil(upstream.grid.columns / stride) * stride
        first_y = origin[1]
        rows = math.ceil(upstream.grid.rows / stride)
    else:
        points = [
            upstream.coordinate_for(slot_id) for slot_id in upstream.slot_ids
        ]
        spatial = [point for point in points if point is not None]
        first_x = max(point[0] for point in spatial) + stride
        first_y = min(point[1] for point in spatial)
        height = max(point[1] for point in spatial) - first_y
        rows = height // stride + 1
    return SubmoduleLayoutResult(
        slot_ids=slot_ids,
        coordinates={
            slot_id: (
                (index // rows) * stride,
                (index % rows) * stride,
            )
            for index, slot_id in enumerate(slot_ids)
        },
        logical_origin=(first_x, first_y),
    )


def _request_for(
    request: LogicalLayoutRequest | None,
    target: SubmoduleKey,
) -> SubmoduleLayoutRequest | None:
    if request is None:
        return None
    return request.submodules.get(target)


def _validate_requests(
    profile: ArchitectureProfile,
    request: LogicalLayoutRequest | None,
) -> None:
    if request is None:
        return
    available = {
        SubmoduleKey(owner.id, module.id, submodule.id)
        for owner in (*profile.nodes, *profile.interconnects)
        for module in owner.modules
        for submodule in module.submodules
    }
    unknown = sorted(set(request.submodules) - available)
    if unknown:
        _invalid(
            "Logical layout references an unknown Submodule",
            submodule=str(unknown[0]),
        )


def _apply_request(
    base: SubmoduleLayoutResult,
    request: SubmoduleLayoutRequest | None,
    *,
    target: SubmoduleKey,
    grid_stride: int = 1,
) -> SubmoduleLayoutResult:
    if request is None:
        return base
    if request.logical_origin is not None:
        origin = request.logical_origin
    elif base.logical_origin is not None:
        origin = base.logical_origin
    elif request.grid is not None or request.slots is not None:
        origin = (0, 0)
    else:
        origin = None
    coordinates = base.coordinates
    grid = base.grid
    if request.grid is not None:
        requested_grid = request.grid
        if requested_grid.rows * requested_grid.columns < len(base.slot_ids):
            _invalid(
                "Logical grid does not have enough sites for Submodule capacity",
                node=target.owner_id,
                submodule=f"{target.module_id}/{target.submodule_id}",
                capacity=len(base.slot_ids),
                rows=requested_grid.rows,
                columns=requested_grid.columns,
            )
        coordinates = {
            slot_id: (
                (index % requested_grid.columns) * grid_stride,
                (index // requested_grid.columns) * grid_stride,
            )
            for index, slot_id in enumerate(base.slot_ids)
        }
        grid = LogicalLayoutGrid(
            rows=(requested_grid.rows - 1) * grid_stride + 1,
            columns=(requested_grid.columns - 1) * grid_stride + 1,
        )
    elif request.slots is not None:
        expected = set(base.slot_ids)
        actual = set(request.slots)
        if actual != expected:
            _invalid(
                "Logical slot IDs must exactly match the materialized "
                "Submodule slots",
                node=target.owner_id,
                submodule=f"{target.module_id}/{target.submodule_id}",
                missing=sorted(expected - actual),
                unknown=sorted(actual - expected),
            )
        coordinates = {
            slot_id: request.slots[slot_id] for slot_id in base.slot_ids
        }
        grid = None
    return SubmoduleLayoutResult(
        slot_ids=base.slot_ids,
        coordinates=coordinates,
        logical_origin=origin,
        grid=grid,
    )


def _apply_if_nonempty(
    base: SubmoduleLayoutResult,
    request: SubmoduleLayoutRequest | None,
    *,
    target: SubmoduleKey,
    grid_stride: int = 1,
) -> SubmoduleLayoutResult:
    """Apply requested geometry only when the Submodule owns slots."""

    if base.slot_ids:
        return _apply_request(
            base,
            request,
            target=target,
            grid_stride=grid_stride,
        )
    if request is not None:
        _invalid(
            "An empty Submodule cannot have logical geometry",
            submodule=str(target),
        )
    return base


def _materialize_slot_identities(
    profile: ArchitectureProfile,
    sizing: SizingResult,
    request: LogicalLayoutRequest | None,
) -> tuple[tuple[_ProfileRecord, ...], dict[SubmoduleKey, SubmoduleLayoutResult]]:
    """Validate shared inputs and materialize owner-local slot identities."""

    if not isinstance(profile, ArchitectureProfile):
        raise TypeError("profile must be an ArchitectureProfile")
    if not isinstance(sizing, SizingResult):
        raise TypeError("sizing must be a SizingResult")
    if request is not None and not isinstance(request, LogicalLayoutRequest):
        raise TypeError("request must be a LogicalLayoutRequest or None")

    records: tuple[_ProfileRecord, ...] = tuple(
        (
            owner.id,
            module,
            submodule,
            SubmoduleKey(owner.id, module.id, submodule.id),
        )
        for owner in (*profile.nodes, *profile.interconnects)
        for module in owner.modules
        for submodule in module.submodules
    )
    expected = {record[-1] for record in records}
    actual = set(sizing.capacities)
    if actual != expected:
        _invalid(
            "SizingResult does not cover the Profile exactly",
            missing=sorted(str(target) for target in expected - actual),
            unknown=sorted(str(target) for target in actual - expected),
        )
    _validate_requests(profile, request)

    layouts: dict[SubmoduleKey, SubmoduleLayoutResult] = {}
    for node_id, module, submodule, target in records:
        submodule_request = _request_for(request, target)
        if submodule.type == "engine":
            if submodule_request is not None:
                _invalid(
                    "Engine Submodules cannot have logical slot geometry",
                    node=node_id,
                    submodule=f"{module.id}/{submodule.id}",
                )
            continue
        capacity = sizing.capacity_for(target)
        layouts[target] = SubmoduleLayoutResult(
            slot_ids=tuple(f"slot_{index}" for index in range(capacity))
        )
    return records, layouts


class LogicalLayoutPolicy(ABC):
    """Abstract strategy from a sized Profile to a complete logical layout."""

    @abstractmethod
    def place(
        self,
        profile: ArchitectureProfile,
        sizing: SizingResult,
        *,
        request: LogicalLayoutRequest | None = None,
    ) -> LogicalLayoutResult:
        """Materialize slot identities and optional owner-local coordinates."""


__all__ = ["LogicalLayoutPolicy"]
