"""Default logical-layout recipe for the bundled NA-CF architecture."""

from __future__ import annotations

from collections.abc import Callable
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
from ...profile import ArchitectureProfile, ProfileModule, ProfileSubmodule
from ...sizing import SizingResult


def _is_compute_region(
    module: ProfileModule,
    submodule: ProfileSubmodule,
) -> bool:
    return (
        module.type == "compute"
        and submodule.type == "region"
        and submodule.payload == "logical_qubit"
    )


def _is_magic_input(
    module: ProfileModule,
    submodule: ProfileSubmodule,
) -> bool:
    return (
        module.type == "compute"
        and submodule.type == "buffer"
        and submodule.payload == "magic_state"
    )


def _is_magic_output(
    module: ProfileModule,
    submodule: ProfileSubmodule,
) -> bool:
    return (
        module.type == "resource_factory"
        and submodule.type == "buffer"
        and submodule.payload == "magic_state"
    )


@dataclass(frozen=True, slots=True)
class NeutralAtomComputeFactoryLayoutPolicy(LogicalLayoutPolicy):
    """Place NA-CF compute, magic input, and independent factory output."""

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
        """Materialize the complete neutral-atom compute/factory recipe."""

        records, layouts = _materialize_slot_identities(
            profile,
            sizing,
            request,
        )
        for node in profile.nodes:
            owned = [record for record in records if record[0] == node.id]
            compute = self._matching(owned, _is_compute_region)
            magic_input = self._matching(owned, _is_magic_input)
            magic_output = self._matching(owned, _is_magic_output)
            if (compute or magic_input or magic_output) and (
                node.modality != "neutral_atom"
            ):
                _invalid(
                    "NeutralAtomComputeFactoryLayoutPolicy requires a "
                    "neutral-atom Node",
                    node=node.id,
                    modality=node.modality,
                )
            self._require_cardinality(
                node.id,
                compute=compute,
                magic_input=magic_input,
                magic_output=magic_output,
            )

            if compute:
                target = compute[0][-1]
                layouts[target] = _apply_request(
                    _square_layout(
                        layouts[target].slot_ids,
                        origin=self.compute_origin,
                        keep_grid=True,
                    ),
                    _request_for(request, target),
                    target=target,
                )

            if magic_input:
                if not compute:
                    target = magic_input[0][-1]
                    _invalid(
                        "NeutralAtomComputeFactoryLayoutPolicy magic input "
                        "placement needs a compute region",
                        node=node.id,
                        submodule=f"{target.module_id}/{target.submodule_id}",
                    )
                target = magic_input[0][-1]
                layouts[target] = _apply_request(
                    _east_columns_layout(
                        layouts[target].slot_ids,
                        layouts[compute[0][-1]],
                        offset=self.right_edge_offset,
                    ),
                    _request_for(request, target),
                    target=target,
                )

            if magic_output:
                target = magic_output[0][-1]
                layouts[target] = _apply_request(
                    _square_layout(
                        layouts[target].slot_ids,
                        origin=self.magic_output_origin,
                        keep_grid=False,
                    ),
                    _request_for(request, target),
                    target=target,
                )

            handled = {
                *(record[-1] for record in compute),
                *(record[-1] for record in magic_input),
                *(record[-1] for record in magic_output),
            }
            for _, _module, submodule, target in owned:
                if submodule.type == "engine" or target in handled:
                    continue
                layouts[target] = _apply_request(
                    layouts[target],
                    _request_for(request, target),
                    target=target,
                )
        return LogicalLayoutResult(layouts)

    @staticmethod
    def _matching(
        records: list[_ProfileRecord],
        predicate: Callable[[ProfileModule, ProfileSubmodule], bool],
    ) -> list[_ProfileRecord]:
        return [
            record for record in records if predicate(record[1], record[2])
        ]

    @staticmethod
    def _require_cardinality(
        node_id: str,
        *,
        compute: list[_ProfileRecord],
        magic_input: list[_ProfileRecord],
        magic_output: list[_ProfileRecord],
    ) -> None:
        for label, records in (
            ("compute region", compute),
            ("magic input", magic_input),
            ("magic output", magic_output),
        ):
            if len(records) > 1:
                _invalid(
                    f"NeutralAtomComputeFactoryLayoutPolicy needs at most "
                    f"one {label} per Node",
                    node=node_id,
                    submodules=[f"{r[1].id}/{r[2].id}" for r in records],
                )


def make_layout_policy() -> NeutralAtomComputeFactoryLayoutPolicy:
    """Return the reference-baseline NA-CF logical placement."""

    return NeutralAtomComputeFactoryLayoutPolicy(
        magic_output_origin=(100, 40)
    )


__all__ = ["NeutralAtomComputeFactoryLayoutPolicy", "make_layout_policy"]
