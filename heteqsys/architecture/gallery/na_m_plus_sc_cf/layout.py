"""Logical placement for neutral-atom memory and superconducting compute."""

from __future__ import annotations

from dataclasses import dataclass
import math

from ...identifiers import SubmoduleKey
from ...logical_layout import (
    LogicalCoordinate,
    LogicalLayoutRequest,
    LogicalLayoutResult,
    SubmoduleLayoutRequest,
    SubmoduleLayoutResult,
)
from ...logical_layout_policy import (
    LogicalLayoutPolicy,
    _ProfileRecord,
    _apply_request,
    _apply_if_nonempty,
    _coordinate,
    _east_strided_columns_layout,
    _invalid,
    _materialize_slot_identities,
    _request_for,
    _strided_square_layout,
)
from ...profile import ArchitectureProfile
from ...sizing import SizingResult


_ProfileSignature = tuple[str, str, str]
_PATCH_STRIDE = 2
_MEMORY_REGION = ("memory", "region", "logical_qubit")
_MEMORY_STORE_LOAD = ("memory", "buffer", "logical_qubit")
_COMPUTE_REGION = ("compute", "region", "logical_qubit")
_COMPUTE_STORE_LOAD = ("compute", "buffer", "logical_qubit")
_MAGIC_INPUT = ("compute", "buffer", "magic_state")
_FACTORY_ENGINE = ("resource_factory", "engine", "magic_state")
_MAGIC_OUTPUT = ("resource_factory", "buffer", "magic_state")
_BELL_ENGINE = ("bell_engine", "engine", "bell_pair")
_BELL_BUFFER = ("bell_storage", "buffer", "bell_pair")


def _matching(
    records: tuple[_ProfileRecord, ...],
    signature: _ProfileSignature,
) -> list[_ProfileRecord]:
    return [
        record
        for record in records
        if (record[1].type, record[2].type, record[2].payload) == signature
    ]


def _single(
    records: tuple[_ProfileRecord, ...],
    signature: _ProfileSignature,
    *,
    label: str,
) -> _ProfileRecord:
    matches = _matching(records, signature)
    if len(matches) != 1:
        _invalid(
            "HybridMemoryComputeLayoutPolicy needs exactly one " + label,
            submodules=[str(record[-1]) for record in matches],
        )
    return matches[0]


def _west_boundary_layout(
    slot_ids: tuple[str, ...],
    upstream: SubmoduleLayoutResult,
    *,
    offset: int,
) -> SubmoduleLayoutResult:
    """Place dense boundary-buffer columns west of a checkerboard canvas."""

    if not upstream.coordinates:
        raise ValueError("west-boundary placement needs spatial input")
    origin = upstream.logical_origin or (0, 0)
    if upstream.grid is not None:
        nearest_x = origin[0] - offset
        first_y = origin[1]
        boundary_rows = upstream.grid.rows + 1
    else:
        points = [
            upstream.coordinate_for(slot_id) for slot_id in upstream.slot_ids
        ]
        spatial = [point for point in points if point is not None]
        nearest_x = min(point[0] for point in spatial) - offset
        first_y = min(point[1] for point in spatial)
        boundary_rows = max(point[1] for point in spatial) - first_y + 2
    boundary_columns = max(1, math.ceil(len(slot_ids) / boundary_rows))
    first_x = nearest_x - boundary_columns + 1
    return SubmoduleLayoutResult(
        slot_ids=slot_ids,
        coordinates={
            slot_id: (
                boundary_columns - 1 - index // boundary_rows,
                index % boundary_rows,
            )
            for index, slot_id in enumerate(slot_ids)
        },
        logical_origin=(first_x, first_y),
    )


def _identity_only(
    base: SubmoduleLayoutResult,
    request: SubmoduleLayoutRequest | None,
    *,
    target: SubmoduleKey,
    label: str,
) -> SubmoduleLayoutResult:
    if not base.slot_ids:
        if request is not None:
            _invalid(
                "An empty Submodule cannot have logical geometry",
                submodule=str(target),
            )
        return base
    if request is not None and (
        request.grid is not None or request.slots is not None
    ):
        _invalid(
            f"{label} slots have identity but no operational coordinates",
            submodule=str(target),
        )
    return _apply_request(base, request, target=target)


@dataclass(frozen=True, slots=True)
class HybridMemoryComputeLayoutPolicy(LogicalLayoutPolicy):
    """Place NA-M + SC-CF without inventing routing or physical geometry.

    The superconducting compute, Store/Load endpoint, and magic buffers use
    the same checkerboard canvas. Neutral-atom memory remains identity-only;
    its transfer endpoint may be spatialized only by an explicit request.
    The shared Interconnect Bell buffer is identity-only by default, while an
    explicit request may place its shared slots on the Interconnect's own
    logical canvas. No endpoint-local or physical Bell layout is inferred.
    """

    compute_origin: LogicalCoordinate = (2, 0)
    west_edge_offset: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "compute_origin",
            _coordinate(self.compute_origin, name="compute origin"),
        )
        if (
            type(self.west_edge_offset) is not int
            or self.west_edge_offset <= 0
        ):
            raise ValueError("west_edge_offset must be a positive integer")

    def place(
        self,
        profile: ArchitectureProfile,
        sizing: SizingResult,
        *,
        request: LogicalLayoutRequest | None = None,
    ) -> LogicalLayoutResult:
        """Materialize the two Node canvases and shared Interconnect slots."""

        records, layouts = _materialize_slot_identities(profile, sizing, request)
        memory_record = _single(
            records,
            _MEMORY_REGION,
            label="logical-qubit memory region",
        )
        memory_store_load_record = _single(
            records,
            _MEMORY_STORE_LOAD,
            label="memory-side Store/Load buffer",
        )
        compute_record = _single(
            records,
            _COMPUTE_REGION,
            label="logical-qubit compute region",
        )
        compute_store_load_record = _single(
            records,
            _COMPUTE_STORE_LOAD,
            label="compute-side Store/Load buffer",
        )
        magic_input_record = _single(
            records,
            _MAGIC_INPUT,
            label="compute-side magic-state buffer",
        )
        factory_record = _single(
            records,
            _FACTORY_ENGINE,
            label="local magic-state factory engine",
        )
        magic_output_record = _single(
            records,
            _MAGIC_OUTPUT,
            label="local magic-state output buffer",
        )
        bell_engine_record = _single(
            records,
            _BELL_ENGINE,
            label="shared Bell-pair engine",
        )
        bell_buffer_record = _single(
            records,
            _BELL_BUFFER,
            label="shared logical Bell-pair buffer",
        )

        memory_target = memory_record[-1]
        memory_store_load_target = memory_store_load_record[-1]
        compute_target = compute_record[-1]
        compute_store_load_target = compute_store_load_record[-1]
        magic_input_target = magic_input_record[-1]
        factory_target = factory_record[-1]
        magic_output_target = magic_output_record[-1]
        bell_engine_target = bell_engine_record[-1]
        bell_buffer_target = bell_buffer_record[-1]

        nodes = {node.id: node for node in profile.nodes}
        memory_node = nodes.get(memory_target.owner_id)
        compute_node = nodes.get(compute_target.owner_id)
        if memory_node is None or memory_node.modality != "neutral_atom":
            _invalid(
                "HybridMemoryComputeLayoutPolicy needs a neutral-atom memory Node",
                owner=memory_target.owner_id,
            )
        if compute_node is None or compute_node.modality != "superconducting":
            _invalid(
                "HybridMemoryComputeLayoutPolicy needs a superconducting compute Node",
                owner=compute_target.owner_id,
            )
        if memory_node.id == compute_node.id:
            _invalid(
                "HybridMemoryComputeLayoutPolicy needs remote memory and compute Nodes",
                owner=memory_node.id,
            )
        if memory_store_load_target.owner_id != memory_node.id:
            _invalid(
                "Memory-side Store/Load buffer must belong to the memory Node",
                submodule=str(memory_store_load_target),
            )
        if memory_store_load_target.module_id != memory_target.module_id:
            _invalid(
                "Memory region and Store/Load buffer must share one Module",
                memory=str(memory_target),
                store_load=str(memory_store_load_target),
            )
        if (
            compute_store_load_target.owner_id != compute_node.id
            or magic_input_target.owner_id != compute_node.id
            or compute_store_load_target.module_id != compute_target.module_id
            or magic_input_target.module_id != compute_target.module_id
        ):
            _invalid(
                "Compute region, Store/Load endpoint, and magic input must "
                "share one Module on the superconducting Node",
                compute=compute_node.id,
            )
        if (
            factory_target.owner_id != compute_node.id
            or magic_output_target.owner_id != compute_node.id
            or magic_output_target.module_id != factory_target.module_id
        ):
            _invalid(
                "Factory engine and output buffer must share one Module on "
                "the superconducting compute Node",
                compute=compute_node.id,
            )
        expected_magic_connection = (
            f"{magic_output_target.module_id}/{magic_output_target.submodule_id}",
            f"{magic_input_target.module_id}/{magic_input_target.submodule_id}",
        )
        magic_connections = [
            connection
            for connection in compute_node.connections
            if connection.direction == "directed"
            and connection.endpoints == expected_magic_connection
        ]
        if len(magic_connections) != 1:
            _invalid(
                "HybridMemoryComputeLayoutPolicy needs exactly one local "
                "magic-state connection from the factory output to the "
                "compute input",
                node=compute_node.id,
            )
        if bell_engine_target.owner_id != bell_buffer_target.owner_id:
            _invalid(
                "Bell engine and buffer must share one Interconnect owner",
                engine=str(bell_engine_target),
                buffer=str(bell_buffer_target),
            )
        interconnect = next(
            (
                item
                for item in profile.interconnects
                if item.id == bell_engine_target.owner_id
            ),
            None,
        )
        if interconnect is None or set(interconnect.endpoints) != {
            str(memory_store_load_target),
            str(compute_store_load_target),
        }:
            _invalid(
                "HybridMemoryComputeLayoutPolicy needs the Interconnect to "
                "attach the two Store/Load buffers",
                interconnect=bell_engine_target.owner_id,
            )
        expected_bell_connection = (
            f"{bell_engine_target.module_id}/{bell_engine_target.submodule_id}",
            f"{bell_buffer_target.module_id}/{bell_buffer_target.submodule_id}",
        )
        bell_connections = [
            connection
            for connection in interconnect.connections
            if connection.direction == "directed"
            and connection.endpoints == expected_bell_connection
        ]
        if len(bell_connections) != 1:
            _invalid(
                "HybridMemoryComputeLayoutPolicy needs exactly one directed "
                "Bell engine-to-buffer connection inside the Interconnect",
                interconnect=interconnect.id,
            )

        layouts[memory_target] = _identity_only(
            layouts[memory_target],
            _request_for(request, memory_target),
            target=memory_target,
            label="Memory",
        )
        layouts[memory_store_load_target] = _apply_if_nonempty(
            layouts[memory_store_load_target],
            _request_for(request, memory_store_load_target),
            target=memory_store_load_target,
        )

        layouts[compute_target] = _apply_if_nonempty(
            _strided_square_layout(
                layouts[compute_target].slot_ids,
                origin=self.compute_origin,
                stride=_PATCH_STRIDE,
            ),
            _request_for(request, compute_target),
            target=compute_target,
            grid_stride=_PATCH_STRIDE,
        )
        layouts[compute_store_load_target] = _apply_if_nonempty(
            _west_boundary_layout(
                layouts[compute_store_load_target].slot_ids,
                layouts[compute_target],
                offset=self.west_edge_offset,
            ),
            _request_for(request, compute_store_load_target),
            target=compute_store_load_target,
        )
        layouts[magic_input_target] = _apply_if_nonempty(
            _east_strided_columns_layout(
                layouts[magic_input_target].slot_ids,
                layouts[compute_target],
                stride=_PATCH_STRIDE,
            ),
            _request_for(request, magic_input_target),
            target=magic_input_target,
            grid_stride=_PATCH_STRIDE,
        )
        layouts[magic_output_target] = _apply_if_nonempty(
            _east_strided_columns_layout(
                layouts[magic_output_target].slot_ids,
                layouts[magic_input_target],
                stride=_PATCH_STRIDE,
            ),
            _request_for(request, magic_output_target),
            target=magic_output_target,
            grid_stride=_PATCH_STRIDE,
        )
        layouts[bell_buffer_target] = _apply_if_nonempty(
            layouts[bell_buffer_target],
            _request_for(request, bell_buffer_target),
            target=bell_buffer_target,
        )

        handled = {
            memory_target,
            memory_store_load_target,
            compute_target,
            compute_store_load_target,
            magic_input_target,
            factory_target,
            magic_output_target,
            bell_engine_target,
            bell_buffer_target,
        }
        for _, _module, submodule, target in records:
            if submodule.type == "engine" or target in handled:
                continue
            layouts[target] = _apply_if_nonempty(
                layouts[target],
                _request_for(request, target),
                target=target,
            )
        return LogicalLayoutResult(layouts)


def make_layout_policy() -> HybridMemoryComputeLayoutPolicy:
    """Return the reference-baseline NA-M + SC-CF logical placement."""

    return HybridMemoryComputeLayoutPolicy()


__all__ = ["HybridMemoryComputeLayoutPolicy", "make_layout_policy"]
