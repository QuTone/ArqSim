"""Default checkerboard logical-layout recipe for bundled SC-CF."""

from __future__ import annotations

from collections.abc import Callable
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
    _apply_request,
    _coordinate,
    _east_strided_columns_layout,
    _invalid,
    _materialize_slot_identities,
    _request_for,
    _strided_square_layout,
)
from ...profile import ArchitectureProfile, ProfileModule, ProfileSubmodule
from ...sizing import SizingResult


_PATCH_STRIDE = 2


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
class SuperconductingCheckerboardLayoutPolicy(LogicalLayoutPolicy):
    """Place SC-CF occupied patches directly on its logical lattice.

    Occupied patches are two canvas units apart, leaving the intervening
    checkerboard sites available for compiler-derived routing. The policy
    emits no routing nodes, routing edges, selected paths, or couplers.

    A requested grid counts patch rows and columns. Its materialized result
    grid describes the expanded owner-local canvas envelope, including the
    unoccupied sites between patches. Exact slot requests are never rescaled
    or snapped; their coordinates remain caller-authored.
    """

    compute_origin: LogicalCoordinate = (2, 0)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "compute_origin",
            _coordinate(self.compute_origin, name="compute origin"),
        )

    def place(
        self,
        profile: ArchitectureProfile,
        sizing: SizingResult,
        *,
        request: LogicalLayoutRequest | None = None,
    ) -> LogicalLayoutResult:
        """Materialize the complete superconducting checkerboard recipe."""

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
            self._require_cardinality(
                node.id,
                compute=compute,
                magic_input=magic_input,
                magic_output=magic_output,
            )
            if (compute or magic_input or magic_output) and (
                node.modality != "superconducting"
            ):
                _invalid(
                    "SuperconductingCheckerboardLayoutPolicy requires a "
                    "superconducting Node",
                    node=node.id,
                    modality=node.modality,
                )

            if compute:
                target = compute[0][-1]
                layouts[target] = self._apply_request(
                    _strided_square_layout(
                        layouts[target].slot_ids,
                        origin=self.compute_origin,
                        stride=_PATCH_STRIDE,
                    ),
                    _request_for(request, target),
                    target=target,
                )

            if magic_input:
                if not compute:
                    target = magic_input[0][-1]
                    _invalid(
                        "SuperconductingCheckerboardLayoutPolicy magic input "
                        "placement needs a compute region",
                        node=node.id,
                        submodule=f"{target.module_id}/{target.submodule_id}",
                    )
                target = magic_input[0][-1]
                layouts[target] = self._apply_request(
                    _east_strided_columns_layout(
                        layouts[target].slot_ids,
                        layouts[compute[0][-1]],
                        stride=_PATCH_STRIDE,
                    ),
                    _request_for(request, target),
                    target=target,
                )

            if magic_output:
                if not magic_input:
                    target = magic_output[0][-1]
                    _invalid(
                        "SuperconductingCheckerboardLayoutPolicy magic output "
                        "placement needs a magic input buffer",
                        node=node.id,
                        submodule=f"{target.module_id}/{target.submodule_id}",
                    )
                target = magic_output[0][-1]
                layouts[target] = self._apply_request(
                    _east_strided_columns_layout(
                        layouts[target].slot_ids,
                        layouts[magic_input[0][-1]],
                        stride=_PATCH_STRIDE,
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
                layouts[target] = self._apply_request(
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
                    f"SuperconductingCheckerboardLayoutPolicy needs at most "
                    f"one {label} per Node",
                    node=node_id,
                    submodules=[f"{r[1].id}/{r[2].id}" for r in records],
                )

    @staticmethod
    def _apply_request(
        base: SubmoduleLayoutResult,
        request: SubmoduleLayoutRequest | None,
        *,
        target: SubmoduleKey,
    ) -> SubmoduleLayoutResult:
        return _apply_request(
            base,
            request,
            target=target,
            grid_stride=_PATCH_STRIDE,
        )


def make_layout_policy() -> SuperconductingCheckerboardLayoutPolicy:
    """Return the reference-baseline SC-CF logical placement."""

    return SuperconductingCheckerboardLayoutPolicy()


__all__ = ["SuperconductingCheckerboardLayoutPolicy", "make_layout_policy"]
