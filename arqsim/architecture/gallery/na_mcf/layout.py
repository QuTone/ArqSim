"""Default logical-layout recipe for neutral-atom memory/compute separation."""

from __future__ import annotations

from dataclasses import dataclass

from ...logical_layout import (
    LogicalCoordinate,
    LogicalLayoutRequest,
    LogicalLayoutResult,
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
_MAGIC_OUTPUT = ("resource_factory", "buffer", "magic_state")


def _matching(
    records: list[_ProfileRecord],
    signature: _ProfileSignature,
) -> list[_ProfileRecord]:
    return [
        record
        for record in records
        if (record[1].type, record[2].type, record[2].payload) == signature
    ]


@dataclass(frozen=True, slots=True)
class NeutralAtomMemoryComputeLayoutPolicy(LogicalLayoutPolicy):
    """Place one neutral-atom memory/compute/factory architecture family.

    Memory slots retain identity without operational coordinates. Compute owns
    the spatial reference envelope: its Store/Load buffer is derived to the
    west and its magic-state input buffer to the east. The factory output is
    independently anchored by explicit policy state. No routing fabric or
    physical placement is emitted.
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
        """Materialize the complete memory/compute/factory layout recipe."""

        records, layouts = _materialize_slot_identities(profile, sizing, request)
        matched_family = False
        for node in profile.nodes:
            owned = [record for record in records if record[0] == node.id]
            memory = _matching(owned, _MEMORY_REGION)
            compute = _matching(owned, _COMPUTE_REGION)
            store_load = _matching(owned, _STORE_LOAD_BUFFER)
            magic_input = _matching(owned, _MAGIC_INPUT)
            magic_output = _matching(owned, _MAGIC_OUTPUT)
            family = memory + compute + store_load + magic_input + magic_output
            matched_family = matched_family or bool(family)
            if family and node.modality != "neutral_atom":
                _invalid(
                    "NeutralAtomMemoryComputeLayoutPolicy requires a "
                    "neutral-atom Node",
                    node=node.id,
                    modality=node.modality,
                )
            if family:
                self._require_complete_family(
                    node.id,
                    memory=memory,
                    compute=compute,
                    store_load=store_load,
                    magic_input=magic_input,
                    magic_output=magic_output,
                )

            if memory:
                target = memory[0][-1]
                memory_request = _request_for(request, target)
                if memory_request is not None and (
                    memory_request.grid is not None
                    or memory_request.slots is not None
                ):
                    _invalid(
                        "Memory slots have identity but no operational coordinates",
                        node=node.id,
                        submodule=f"{target.module_id}/{target.submodule_id}",
                    )
                layouts[target] = _apply_if_nonempty(
                    layouts[target],
                    memory_request,
                    target=target,
                )

            if compute:
                target = compute[0][-1]
                if layouts[target].slot_ids:
                    base = _square_layout(
                        layouts[target].slot_ids,
                        origin=self.compute_origin,
                        keep_grid=True,
                    )
                else:
                    base = layouts[target]
                layouts[target] = _apply_if_nonempty(
                    base,
                    _request_for(request, target),
                    target=target,
                )

            if store_load:
                target = store_load[0][-1]
                if layouts[target].slot_ids:
                    if not compute or not layouts[compute[0][-1]].coordinates:
                        _invalid(
                            "Store/Load placement needs a spatial compute region",
                            node=node.id,
                            submodule=f"{target.module_id}/{target.submodule_id}",
                        )
                    base = _west_columns_layout(
                        layouts[target].slot_ids,
                        layouts[compute[0][-1]],
                        offset=self.west_edge_offset,
                    )
                else:
                    base = layouts[target]
                layouts[target] = _apply_if_nonempty(
                    base,
                    _request_for(request, target),
                    target=target,
                )

            if magic_input:
                target = magic_input[0][-1]
                if layouts[target].slot_ids:
                    if not compute or not layouts[compute[0][-1]].coordinates:
                        _invalid(
                            "Magic input placement needs a spatial compute region",
                            node=node.id,
                            submodule=f"{target.module_id}/{target.submodule_id}",
                        )
                    base = _east_columns_layout(
                        layouts[target].slot_ids,
                        layouts[compute[0][-1]],
                        offset=self.right_edge_offset,
                    )
                else:
                    base = layouts[target]
                layouts[target] = _apply_if_nonempty(
                    base,
                    _request_for(request, target),
                    target=target,
                )

            if magic_output:
                target = magic_output[0][-1]
                if layouts[target].slot_ids:
                    base = _square_layout(
                        layouts[target].slot_ids,
                        origin=self.magic_output_origin,
                        keep_grid=False,
                    )
                else:
                    base = layouts[target]
                layouts[target] = _apply_if_nonempty(
                    base,
                    _request_for(request, target),
                    target=target,
                )

            handled = {
                *(record[-1] for record in family),
            }
            for _, _module, submodule, target in owned:
                if submodule.type == "engine" or target in handled:
                    continue
                layouts[target] = _apply_if_nonempty(
                    layouts[target],
                    _request_for(request, target),
                    target=target,
                )
        if not matched_family:
            _invalid(
                "NeutralAtomMemoryComputeLayoutPolicy needs a memory/compute "
                "architecture family"
            )
        return LogicalLayoutResult(layouts)

    @staticmethod
    def _require_complete_family(
        node_id: str,
        *,
        memory: list[_ProfileRecord],
        compute: list[_ProfileRecord],
        store_load: list[_ProfileRecord],
        magic_input: list[_ProfileRecord],
        magic_output: list[_ProfileRecord],
    ) -> None:
        for label, records in (
            ("memory region", memory),
            ("compute region", compute),
            ("Store/Load buffer", store_load),
            ("magic input", magic_input),
            ("magic output", magic_output),
        ):
            if len(records) != 1:
                _invalid(
                    "NeutralAtomMemoryComputeLayoutPolicy needs exactly one "
                    f"{label} per Node",
                    node=node_id,
                    submodules=[f"{r[1].id}/{r[2].id}" for r in records],
                )


def make_layout_policy() -> NeutralAtomMemoryComputeLayoutPolicy:
    """Return the reference-baseline NA-MCF logical placement."""

    return NeutralAtomMemoryComputeLayoutPolicy(
        magic_output_origin=(200, 40),
    )


__all__ = ["NeutralAtomMemoryComputeLayoutPolicy", "make_layout_policy"]
