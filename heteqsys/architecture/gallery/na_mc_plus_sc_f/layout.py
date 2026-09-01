"""Logical placement for local memory/compute with remote magic supply."""

from __future__ import annotations

from dataclasses import dataclass

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
    _apply_if_nonempty,
    _coordinate,
    _east_columns_layout,
    _invalid,
    _materialize_slot_identities,
    _request_for,
    _square_layout,
    _west_columns_layout,
)
from ...profile import ArchitectureProfile
from ...sizing import SizingResult


_ProfileSignature = tuple[str, str, str]
_MEMORY_REGION = ("memory", "region", "logical_qubit")
_COMPUTE_REGION = ("compute", "region", "logical_qubit")
_STORE_LOAD_BUFFER = ("compute", "buffer", "logical_qubit")
_MAGIC_INPUT = ("compute", "buffer", "magic_state")
_FACTORY_ENGINE = ("resource_factory", "engine", "magic_state")
_MAGIC_OUTPUT = ("resource_factory", "buffer", "magic_state")
_BELL_ENGINE = ("bell_engine", "engine", "bell_pair")
_BELL_BUFFER = ("bell_storage", "buffer", "bell_pair")


def _matching(
    records: list[_ProfileRecord],
    signature: _ProfileSignature,
) -> list[_ProfileRecord]:
    return [
        record
        for record in records
        if (record[1].type, record[2].type, record[2].payload) == signature
    ]


def _single(
    records: list[_ProfileRecord],
    signature: _ProfileSignature,
    *,
    label: str,
) -> _ProfileRecord:
    matches = _matching(records, signature)
    if len(matches) != 1:
        _invalid(
            "RemoteMagicMemoryComputeLayoutPolicy needs exactly one " + label,
            submodules=[str(record[-1]) for record in matches],
        )
    return matches[0]


def _identity_only(
    base: SubmoduleLayoutResult,
    request: SubmoduleLayoutRequest | None,
    *,
    target: SubmoduleKey,
    label: str,
) -> SubmoduleLayoutResult:
    if request is not None and (
        request.grid is not None or request.slots is not None
    ):
        _invalid(
            f"{label} slots have identity but no operational coordinates",
            submodule=str(target),
        )
    return _apply_if_nonempty(base, request, target=target)


@dataclass(frozen=True, slots=True)
class RemoteMagicMemoryComputeLayoutPolicy(LogicalLayoutPolicy):
    """Place local neutral-atom memory/compute and a remote factory.

    Memory and the shared Bell buffer retain stable identities without default
    operational coordinates. An explicit request may place the shared Bell
    slots on the Interconnect's own logical canvas. Compute owns the
    neutral-atom reference canvas: its Store/Load buffer is derived to the west
    and magic input to the east. The superconducting factory output uses an
    independent owner-local origin. No routes, endpoint halves, couplers, or
    physical placement are emitted.
    """

    magic_output_origin: LogicalCoordinate
    compute_origin: LogicalCoordinate = (0, 0)
    west_edge_offset: int = 2
    right_edge_offset: int = 2

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "compute_origin",
            _coordinate(self.compute_origin, name="compute origin"),
        )
        object.__setattr__(
            self,
            "magic_output_origin",
            _coordinate(self.magic_output_origin, name="magic output origin"),
        )
        for field_name in ("west_edge_offset", "right_edge_offset"):
            value = getattr(self, field_name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{field_name} must be a positive integer")

    def place(
        self,
        profile: ArchitectureProfile,
        sizing: SizingResult,
        *,
        request: LogicalLayoutRequest | None = None,
    ) -> LogicalLayoutResult:
        """Materialize the complete NA-MC + SC-F logical-layout recipe."""

        records, layouts = _materialize_slot_identities(profile, sizing, request)
        node_ids = {node.id for node in profile.nodes}
        node_records = [record for record in records if record[0] in node_ids]
        interconnect_records = [
            record for record in records if record[0] not in node_ids
        ]

        memory = _single(
            node_records,
            _MEMORY_REGION,
            label="logical-qubit memory region",
        )
        compute = _single(
            node_records,
            _COMPUTE_REGION,
            label="logical-qubit compute region",
        )
        store_load = _single(
            node_records,
            _STORE_LOAD_BUFFER,
            label="Store/Load buffer",
        )
        magic_input = _single(
            node_records,
            _MAGIC_INPUT,
            label="magic input",
        )
        factory = _single(
            node_records,
            _FACTORY_ENGINE,
            label="remote magic factory engine",
        )
        magic_output = _single(
            node_records,
            _MAGIC_OUTPUT,
            label="remote magic output",
        )
        bell_engine = _single(
            interconnect_records,
            _BELL_ENGINE,
            label="shared Bell engine",
        )
        bell_buffer = _single(
            interconnect_records,
            _BELL_BUFFER,
            label="shared Bell buffer",
        )

        memory_target = memory[-1]
        compute_target = compute[-1]
        store_load_target = store_load[-1]
        input_target = magic_input[-1]
        factory_target = factory[-1]
        output_target = magic_output[-1]
        bell_engine_target = bell_engine[-1]
        bell_target = bell_buffer[-1]

        local_targets = (
            memory_target,
            compute_target,
            store_load_target,
            input_target,
        )
        if len({target.owner_id for target in local_targets}) != 1:
            _invalid(
                "RemoteMagicMemoryComputeLayoutPolicy needs memory, compute, "
                "Store/Load, and magic input on one Node",
                submodules=[str(target) for target in local_targets],
            )
        if store_load_target.module_id != compute_target.module_id:
            _invalid(
                "Store/Load must belong to the compute Module",
                compute=str(compute_target),
                store_load=str(store_load_target),
            )
        if input_target.module_id != compute_target.module_id:
            _invalid(
                "Magic input must belong to the compute Module",
                compute=str(compute_target),
                magic_input=str(input_target),
            )

        nodes = {node.id: node for node in profile.nodes}
        compute_node = nodes[compute_target.owner_id]
        output_node = nodes[output_target.owner_id]
        if compute_node.modality != "neutral_atom":
            _invalid(
                "RemoteMagicMemoryComputeLayoutPolicy needs a neutral-atom "
                "memory/compute Node",
                node=compute_node.id,
                modality=compute_node.modality,
            )
        expected_memory_connection = {
            f"{memory_target.module_id}/{memory_target.submodule_id}",
            f"{store_load_target.module_id}/{store_load_target.submodule_id}",
        }
        memory_connections = [
            connection
            for connection in compute_node.connections
            if connection.direction == "bidirectional"
            and set(connection.endpoints) == expected_memory_connection
        ]
        if len(memory_connections) != 1:
            _invalid(
                "The memory/compute Node needs exactly one bidirectional "
                "memory-region/Store-Load connection",
                node=compute_node.id,
            )
        if (
            factory_target.owner_id != output_target.owner_id
            or factory_target.module_id != output_target.module_id
        ):
            _invalid(
                "The factory engine and output buffer must belong to one Module",
                factory=str(factory_target),
                magic_output=str(output_target),
            )
        if output_node.modality != "superconducting":
            _invalid(
                "RemoteMagicMemoryComputeLayoutPolicy needs a superconducting "
                "factory Node",
                node=output_node.id,
                modality=output_node.modality,
            )
        if compute_target.owner_id == output_target.owner_id:
            _invalid(
                "RemoteMagicMemoryComputeLayoutPolicy needs a remote factory Node",
                node=compute_target.owner_id,
            )

        interconnect = next(
            (
                item
                for item in profile.interconnects
                if item.id == bell_target.owner_id
            ),
            None,
        )
        if interconnect is None or set(interconnect.endpoints) != {
            str(input_target),
            str(output_target),
        }:
            _invalid(
                "The shared Interconnect must attach the remote magic input "
                "and output buffers",
                interconnect=bell_target.owner_id,
            )
        if bell_engine_target.owner_id != bell_target.owner_id:
            _invalid(
                "The Bell engine and buffer must belong to one Interconnect",
                bell_engine=str(bell_engine_target),
                bell_buffer=str(bell_target),
            )
        expected_bell_connection = (
            f"{bell_engine_target.module_id}/{bell_engine_target.submodule_id}",
            f"{bell_target.module_id}/{bell_target.submodule_id}",
        )
        bell_connections = [
            connection
            for connection in interconnect.connections
            if connection.direction == "directed"
            and connection.endpoints == expected_bell_connection
        ]
        if len(bell_connections) != 1:
            _invalid(
                "The Interconnect needs exactly one directed "
                "Bell-engine-to-storage connection",
                interconnect=interconnect.id,
            )

        layouts[memory_target] = _identity_only(
            layouts[memory_target],
            _request_for(request, memory_target),
            target=memory_target,
            label="Memory",
        )

        if layouts[compute_target].slot_ids:
            compute_base = _square_layout(
                layouts[compute_target].slot_ids,
                origin=self.compute_origin,
                keep_grid=True,
            )
        else:
            compute_base = layouts[compute_target]
        layouts[compute_target] = _apply_if_nonempty(
            compute_base,
            _request_for(request, compute_target),
            target=compute_target,
        )

        if layouts[store_load_target].slot_ids:
            if not layouts[compute_target].coordinates:
                _invalid(
                    "Store/Load placement needs a spatial compute region",
                    submodule=str(store_load_target),
                )
            store_load_base = _west_columns_layout(
                layouts[store_load_target].slot_ids,
                layouts[compute_target],
                offset=self.west_edge_offset,
            )
        else:
            store_load_base = layouts[store_load_target]
        layouts[store_load_target] = _apply_if_nonempty(
            store_load_base,
            _request_for(request, store_load_target),
            target=store_load_target,
        )

        if layouts[input_target].slot_ids:
            if not layouts[compute_target].coordinates:
                _invalid(
                    "Magic input placement needs a spatial compute region",
                    submodule=str(input_target),
                )
            input_base = _east_columns_layout(
                layouts[input_target].slot_ids,
                layouts[compute_target],
                offset=self.right_edge_offset,
            )
        else:
            input_base = layouts[input_target]
        layouts[input_target] = _apply_if_nonempty(
            input_base,
            _request_for(request, input_target),
            target=input_target,
        )

        if layouts[output_target].slot_ids:
            output_base = _square_layout(
                layouts[output_target].slot_ids,
                origin=self.magic_output_origin,
                keep_grid=False,
            )
        else:
            output_base = layouts[output_target]
        layouts[output_target] = _apply_if_nonempty(
            output_base,
            _request_for(request, output_target),
            target=output_target,
        )

        layouts[bell_target] = _apply_if_nonempty(
            layouts[bell_target],
            _request_for(request, bell_target),
            target=bell_target,
        )

        handled = {
            memory_target,
            compute_target,
            store_load_target,
            input_target,
            output_target,
            bell_target,
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


def make_layout_policy() -> RemoteMagicMemoryComputeLayoutPolicy:
    """Return the reference-baseline NA-MC + SC-F logical placement."""

    return RemoteMagicMemoryComputeLayoutPolicy(
        magic_output_origin=(0, 40),
    )


__all__ = ["RemoteMagicMemoryComputeLayoutPolicy", "make_layout_policy"]
