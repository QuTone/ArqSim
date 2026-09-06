"""Logical placement for a compute Node with a remote magic-state factory."""

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
    _apply_request,
    _coordinate,
    _east_columns_layout,
    _invalid,
    _materialize_slot_identities,
    _request_for,
    _square_layout,
)
from ...profile import ArchitectureProfile
from ...sizing import SizingResult


_ProfileSignature = tuple[str, str, str]
_COMPUTE_REGION = ("compute", "region", "logical_qubit")
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


@dataclass(frozen=True, slots=True)
class HybridRemoteMagicLayoutPolicy(LogicalLayoutPolicy):
    """Place one neutral-atom compute and remote superconducting factory.

    Compute and its magic input share the neutral-atom Node canvas. The
    factory output has an independent origin on the superconducting Node
    canvas. The Interconnect Bell buffer owns stable pair identities but no
    default geometry. Explicit requests may spatialize it on the Interconnect
    canvas. No endpoint halves, routing fabric, couplers, or physical layout
    are emitted here.
    """

    magic_output_origin: LogicalCoordinate
    compute_origin: LogicalCoordinate = (0, 0)
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
        if type(self.right_edge_offset) is not int or self.right_edge_offset <= 0:
            raise ValueError("right_edge_offset must be a positive integer")

    def place(
        self,
        profile: ArchitectureProfile,
        sizing: SizingResult,
        *,
        request: LogicalLayoutRequest | None = None,
    ) -> LogicalLayoutResult:
        """Materialize one complete hybrid remote-magic layout."""

        records, layouts = _materialize_slot_identities(profile, sizing, request)
        node_ids = {node.id for node in profile.nodes}
        node_records = [record for record in records if record[0] in node_ids]
        interconnect_records = [
            record for record in records if record[0] not in node_ids
        ]

        compute = _matching(node_records, _COMPUTE_REGION)
        magic_input = _matching(node_records, _MAGIC_INPUT)
        factory = _matching(node_records, _FACTORY_ENGINE)
        magic_output = _matching(node_records, _MAGIC_OUTPUT)
        bell_engine = _matching(interconnect_records, _BELL_ENGINE)
        bell_buffer = _matching(interconnect_records, _BELL_BUFFER)
        for label, matches in (
            ("compute region", compute),
            ("magic input", magic_input),
            ("remote magic factory engine", factory),
            ("magic output", magic_output),
            ("shared Bell engine", bell_engine),
            ("shared Bell buffer", bell_buffer),
        ):
            if len(matches) != 1:
                _invalid(
                    "HybridRemoteMagicLayoutPolicy needs exactly one " + label,
                    submodules=[str(record[-1]) for record in matches],
                )

        compute_target = compute[0][-1]
        input_target = magic_input[0][-1]
        factory_target = factory[0][-1]
        output_target = magic_output[0][-1]
        bell_engine_target = bell_engine[0][-1]
        bell_target = bell_buffer[0][-1]
        if (
            compute_target.owner_id != input_target.owner_id
            or compute_target.module_id != input_target.module_id
        ):
            _invalid(
                "HybridRemoteMagicLayoutPolicy needs compute and magic input "
                "in one Module on one Node",
                compute=str(compute_target),
                magic_input=str(input_target),
            )
        if (
            factory_target.owner_id != output_target.owner_id
            or factory_target.module_id != output_target.module_id
        ):
            _invalid(
                "HybridRemoteMagicLayoutPolicy needs the factory engine and "
                "magic output in one Module on one Node",
                factory=str(factory_target),
                magic_output=str(output_target),
            )
        nodes = {node.id: node for node in profile.nodes}
        compute_node = nodes[compute_target.owner_id]
        output_node = nodes[output_target.owner_id]
        if compute_node.modality != "neutral_atom":
            _invalid(
                "HybridRemoteMagicLayoutPolicy needs a neutral-atom compute Node",
                node=compute_node.id,
                modality=compute_node.modality,
            )
        if output_node.modality != "superconducting":
            _invalid(
                "HybridRemoteMagicLayoutPolicy needs a superconducting factory Node",
                node=output_node.id,
                modality=output_node.modality,
            )
        if compute_target.owner_id == output_target.owner_id:
            _invalid(
                "HybridRemoteMagicLayoutPolicy needs a remote factory Node",
                node=compute_target.owner_id,
            )
        if bell_engine_target.owner_id != bell_target.owner_id:
            _invalid(
                "HybridRemoteMagicLayoutPolicy needs the Bell engine and "
                "buffer on one Interconnect",
                bell_engine=str(bell_engine_target),
                bell_buffer=str(bell_target),
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
            str(input_target),
            str(output_target),
        }:
            _invalid(
                "HybridRemoteMagicLayoutPolicy needs the Interconnect to "
                "attach the remote magic input and output buffers",
                interconnect=bell_engine_target.owner_id,
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
                "HybridRemoteMagicLayoutPolicy needs exactly one directed "
                "Bell engine-to-buffer connection inside the Interconnect",
                interconnect=interconnect.id,
            )

        compute_base = _square_layout(
            layouts[compute_target].slot_ids,
            origin=self.compute_origin,
            keep_grid=True,
        )
        layouts[compute_target] = _apply_request(
            compute_base,
            _request_for(request, compute_target),
            target=compute_target,
        )
        layouts[input_target] = _apply_request(
            _east_columns_layout(
                layouts[input_target].slot_ids,
                layouts[compute_target],
                offset=self.right_edge_offset,
            ),
            _request_for(request, input_target),
            target=input_target,
        )
        layouts[output_target] = _apply_request(
            _square_layout(
                layouts[output_target].slot_ids,
                origin=self.magic_output_origin,
                keep_grid=False,
            ),
            _request_for(request, output_target),
            target=output_target,
        )
        layouts[bell_target] = _apply_request(
            layouts[bell_target],
            _request_for(request, bell_target),
            target=bell_target,
        )

        handled = {
            compute_target,
            input_target,
            factory_target,
            output_target,
            bell_engine_target,
            bell_target,
        }
        for _, _module, submodule, target in records:
            if submodule.type == "engine" or target in handled:
                continue
            layouts[target] = _apply_request(
                layouts[target],
                _request_for(request, target),
                target=target,
            )
        return LogicalLayoutResult(layouts)


def make_layout_policy() -> HybridRemoteMagicLayoutPolicy:
    """Return the reference-baseline NA-C + SC-F logical placement."""

    return HybridRemoteMagicLayoutPolicy(magic_output_origin=(0, 40))


__all__ = ["HybridRemoteMagicLayoutPolicy", "make_layout_policy"]
