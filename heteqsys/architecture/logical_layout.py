"""Logical-layout request and result data contracts.

Layout requests are partial user input.  A :class:`LogicalLayoutPolicy`
combines one with a sized Architecture Profile and produces a complete layout
result.  Both forms address Submodules directly with :class:`SubmoduleKey`;
there is no Node-only wrapper or ``module/submodule`` side channel.

Coordinates live on an owner-local *logical* canvas.  They are not physical
qubit coordinates: code-aware physical placement is a later lowering stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from heteqsys.schema import semantic_hash

from .identifiers import SubmoduleKey, require_local_id


LogicalCoordinate = tuple[int, int]
MAX_ABS_LOGICAL_COORDINATE = 1_000_000_000


def _mapping(value: Any, *, label: str) -> Mapping[Any, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _identifier(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty string without outer whitespace")
    return value


def _coordinate(value: Any, *, label: str) -> LogicalCoordinate:
    coordinate = _coordinate_pair(value, label=label)
    if any(abs(component) > MAX_ABS_LOGICAL_COORDINATE for component in coordinate):
        raise ValueError(
            f"{label} coordinates must be within "
            f"[-{MAX_ABS_LOGICAL_COORDINATE}, {MAX_ABS_LOGICAL_COORDINATE}]"
        )
    return coordinate


def _coordinate_pair(value: Any, *, label: str) -> LogicalCoordinate:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} must be a two-integer coordinate")
    if len(value) != 2:
        raise ValueError(f"{label} must contain exactly two coordinates")
    if any(type(component) is not int for component in value):
        raise ValueError(f"{label} coordinates must be integers")
    return value[0], value[1]


def _submodule_key(value: Any, *, label: str) -> SubmoduleKey:
    reference = _identifier(value, label=label)
    parts = reference.split("/")
    if len(parts) != 3:
        raise ValueError(
            f"{label} must use the canonical owner/module/submodule form"
        )
    try:
        return SubmoduleKey(*parts)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid {label}: {reference!r}") from error


@dataclass(frozen=True, slots=True)
class LogicalLayoutGrid:
    """A rectangular logical-grid shape for one spatial Submodule.

    In a request, rows and columns count policy placement cells. A policy may
    expand those cells when materializing the result's owner-local canvas. For
    example, a requested two-by-two superconducting patch grid becomes a
    three-by-three result envelope with routing sites between its patches.
    """

    rows: int
    columns: int

    def __post_init__(self) -> None:
        if type(self.rows) is not int or self.rows <= 0:
            raise ValueError("logical layout grid rows must be a positive integer")
        if type(self.columns) is not int or self.columns <= 0:
            raise ValueError("logical layout grid columns must be a positive integer")

    def to_dict(self) -> dict[str, int]:
        return {"rows": self.rows, "columns": self.columns}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogicalLayoutGrid":
        record = _mapping(data, label="logical layout grid")
        unknown = set(record) - {"rows", "columns"}
        missing = {"rows", "columns"} - set(record)
        if unknown:
            raise ValueError(f"Unknown logical layout grid fields: {sorted(unknown)}")
        if missing:
            raise ValueError(f"Logical layout grid is missing: {sorted(missing)}")
        return cls(rows=record["rows"], columns=record["columns"])


@dataclass(frozen=True, slots=True)
class SubmoduleLayoutRequest:
    """Partial user-authored placement for one absolute Submodule target.

    ``logical_origin`` places the Submodule on its owner's canvas.  ``grid``
    supplies a policy-specific placement shape; ``slots`` supplies exact
    Submodule-local coordinates. The two forms are mutually exclusive.
    An identity-only owner may request just an origin; slot identities are
    materialized later by the layout policy.
    """

    logical_origin: LogicalCoordinate | None = None
    grid: LogicalLayoutGrid | None = None
    slots: Mapping[str, LogicalCoordinate] | None = None

    def __post_init__(self) -> None:
        origin = (
            None
            if self.logical_origin is None
            else _coordinate(self.logical_origin, label="logical_origin")
        )
        if self.grid is not None and not isinstance(self.grid, LogicalLayoutGrid):
            raise TypeError("grid must be a LogicalLayoutGrid or None")
        if self.grid is not None and self.slots is not None:
            raise ValueError("logical layout grid and slots are mutually exclusive")

        slots: Mapping[str, LogicalCoordinate] | None
        if self.slots is None:
            slots = None
        else:
            record = _mapping(self.slots, label="logical layout slots")
            if not record:
                raise ValueError("logical layout slots cannot be empty")
            normalized: dict[str, LogicalCoordinate] = {}
            for slot_id, coordinate in record.items():
                identifier = _identifier(slot_id, label="logical slot ID")
                normalized[identifier] = _coordinate(
                    coordinate,
                    label=f"logical slot {identifier!r}",
                )
            slots = MappingProxyType(dict(sorted(normalized.items())))

        if origin is None and self.grid is None and slots is None:
            raise ValueError(
                "Submodule layout request must specify logical_origin, grid, or slots"
            )
        object.__setattr__(self, "logical_origin", origin)
        object.__setattr__(self, "slots", slots)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.logical_origin is not None:
            result["logical_origin"] = list(self.logical_origin)
        if self.grid is not None:
            result["grid"] = self.grid.to_dict()
        if self.slots is not None:
            result["slots"] = {
                slot_id: list(coordinate)
                for slot_id, coordinate in self.slots.items()
            }
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SubmoduleLayoutRequest":
        record = _mapping(data, label="Submodule layout request")
        allowed = {"logical_origin", "grid", "slots"}
        unknown = set(record) - allowed
        if unknown:
            raise ValueError(
                f"Unknown Submodule layout request fields: {sorted(unknown)}"
            )
        return cls(
            logical_origin=(
                _coordinate(record["logical_origin"], label="logical_origin")
                if "logical_origin" in record
                else None
            ),
            grid=(
                LogicalLayoutGrid.from_dict(
                    _mapping(record["grid"], label="logical layout grid")
                )
                if "grid" in record
                else None
            ),
            slots=(
                _mapping(record["slots"], label="logical layout slots")
                if "slots" in record
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class LogicalLayoutRequest:
    """Partial layout requests keyed by absolute Submodule address.

    The canonical wire form is a flat mapping::

        {"submodules": {"owner/module/submodule": {<request fields>}}}

    Each path segment is parsed and validated through :class:`SubmoduleKey`.
    """

    submodules: Mapping[SubmoduleKey, SubmoduleLayoutRequest]

    def __post_init__(self) -> None:
        record = _mapping(self.submodules, label="logical layout request")
        if not record:
            raise ValueError("logical layout request cannot be empty")
        normalized: dict[SubmoduleKey, SubmoduleLayoutRequest] = {}
        for target, request in record.items():
            if not isinstance(target, SubmoduleKey):
                raise TypeError(
                    "logical layout request keys must be SubmoduleKey values"
                )
            if not isinstance(request, SubmoduleLayoutRequest):
                raise TypeError(
                    "logical layout request values must be "
                    "SubmoduleLayoutRequest values"
                )
            normalized[target] = request
        object.__setattr__(
            self,
            "submodules",
            MappingProxyType(dict(sorted(normalized.items()))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "submodules": {
                str(target): request.to_dict()
                for target, request in self.submodules.items()
            }
        }

    @property
    def logical_layout_hash(self) -> str:
        return semantic_hash(self.to_dict())

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogicalLayoutRequest":
        record = _mapping(data, label="logical layout request")
        unknown = set(record) - {"submodules"}
        missing = {"submodules"} - set(record)
        if unknown:
            raise ValueError(
                f"Unknown logical layout request fields: {sorted(unknown)}"
            )
        if missing:
            raise ValueError(f"Logical layout request is missing: {sorted(missing)}")
        submodules = _mapping(
            record["submodules"], label="logical layout request submodules"
        )
        return cls(
            submodules={
                _submodule_key(target, label="Submodule layout target"): (
                    SubmoduleLayoutRequest.from_dict(
                        _mapping(
                            request,
                            label=f"Submodule layout request {target!r}",
                        )
                    )
                )
                for target, request in submodules.items()
            }
        )


@dataclass(frozen=True, slots=True)
class SubmoduleLayoutResult:
    """Complete logical identity and optional geometry for one Submodule.

    ``slot_ids`` materialize identity independently of geometry. For nonempty
    ``slot_ids``, an empty ``coordinates`` mapping means identity-only slots.
    Empty ``slot_ids`` represent zero capacity and cannot carry geometry.
    Coordinates are Submodule-local; ``logical_origin`` translates them onto
    the owner's logical canvas.
    """

    slot_ids: tuple[str, ...] = ()
    coordinates: Mapping[str, LogicalCoordinate] = field(default_factory=dict)
    logical_origin: LogicalCoordinate | None = None
    grid: LogicalLayoutGrid | None = None

    def __post_init__(self) -> None:
        slot_ids = tuple(self.slot_ids)
        normalized_ids = tuple(
            require_local_id(slot_id, where="logical slot ID")
            for slot_id in slot_ids
        )
        if len(normalized_ids) != len(set(normalized_ids)):
            raise ValueError("logical layout result has duplicate slot IDs")

        if not isinstance(self.coordinates, Mapping):
            raise TypeError("logical layout result coordinates must be a mapping")
        unknown = set(self.coordinates) - set(normalized_ids)
        if unknown:
            raise ValueError(
                "logical layout result coordinates reference unknown slots: "
                f"{sorted(unknown)}"
            )
        if self.coordinates and set(self.coordinates) != set(normalized_ids):
            missing = sorted(set(normalized_ids) - set(self.coordinates))
            raise ValueError(
                "spatial Submodule layout needs a coordinate for every slot: "
                f"{missing}"
            )
        normalized_coordinates = {
            slot_id: _coordinate(
                self.coordinates[slot_id], label=f"logical slot {slot_id!r}"
            )
            for slot_id in normalized_ids
            if slot_id in self.coordinates
        }
        if len(set(normalized_coordinates.values())) != len(normalized_coordinates):
            raise ValueError("logical layout result has duplicate slot coordinates")

        origin = (
            None
            if self.logical_origin is None
            else _coordinate(self.logical_origin, label="logical origin")
        )
        if normalized_coordinates and origin is None:
            raise ValueError("spatial Submodule layout needs a logical origin")
        if origin is not None:
            for slot_id, local in normalized_coordinates.items():
                effective = (origin[0] + local[0], origin[1] + local[1])
                if any(
                    abs(component) > MAX_ABS_LOGICAL_COORDINATE
                    for component in effective
                ):
                    raise ValueError(
                        "Resolved logical slot coordinate exceeds the "
                        f"coordinate limit for {slot_id!r}: {effective}"
                    )
        if not normalized_ids and (
            normalized_coordinates or origin is not None or self.grid
        ):
            raise ValueError("a slotless Submodule cannot have logical geometry")
        if self.grid is not None:
            if not isinstance(self.grid, LogicalLayoutGrid):
                raise TypeError("grid must be a LogicalLayoutGrid or None")
            if not normalized_coordinates:
                raise ValueError("a logical grid needs spatial slots")
            outside = [
                slot_id
                for slot_id, (x, y) in normalized_coordinates.items()
                if not (0 <= x < self.grid.columns and 0 <= y < self.grid.rows)
            ]
            if outside:
                raise ValueError(
                    "logical slots lie outside the declared grid: "
                    f"{outside}"
                )

        object.__setattr__(self, "slot_ids", normalized_ids)
        object.__setattr__(
            self,
            "coordinates",
            MappingProxyType(normalized_coordinates),
        )
        object.__setattr__(self, "logical_origin", origin)

    def coordinate_for(self, slot_id: str) -> LogicalCoordinate | None:
        """Return one owner-local coordinate, or ``None`` for identity-only slots."""

        try:
            local = self.coordinates[slot_id]
        except KeyError:
            if slot_id not in self.slot_ids:
                raise KeyError(f"unknown logical slot: {slot_id}") from None
            return None
        origin = self.logical_origin or (0, 0)
        return origin[0] + local[0], origin[1] + local[1]


@dataclass(frozen=True, slots=True)
class LogicalLayoutResult:
    """Complete transient layouts keyed by absolute Submodule address."""

    submodules: Mapping[SubmoduleKey, SubmoduleLayoutResult]

    def __post_init__(self) -> None:
        if not isinstance(self.submodules, Mapping):
            raise TypeError("logical layout result must be a mapping")
        normalized: dict[SubmoduleKey, SubmoduleLayoutResult] = {}
        for target, layout in self.submodules.items():
            if not isinstance(target, SubmoduleKey):
                raise TypeError(
                    "logical layout result keys must be SubmoduleKey values"
                )
            if not isinstance(layout, SubmoduleLayoutResult):
                raise TypeError(
                    "logical layout result values must be SubmoduleLayoutResult values"
                )
            normalized[target] = layout
        object.__setattr__(
            self,
            "submodules",
            MappingProxyType(dict(sorted(normalized.items()))),
        )

    def layout_for(self, target: SubmoduleKey) -> SubmoduleLayoutResult:
        """Return the complete layout for one absolute Submodule target."""

        try:
            return self.submodules[target]
        except KeyError:
            raise KeyError(f"logical layout has no target {target}") from None


__all__ = [
    "LogicalCoordinate",
    "LogicalLayoutGrid",
    "LogicalLayoutRequest",
    "LogicalLayoutResult",
    "SubmoduleLayoutRequest",
    "SubmoduleLayoutResult",
]
