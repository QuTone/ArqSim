"""Typed construction pipeline for canonical Architecture Specifications.

The caller supplies circuit facts and already-selected construction policies.
This module runs those policies in order and hands their complete results to
the mechanical architecture resolver.  It does not accept circuits, legacy
combined-policy records, compiler state, workflow context, or report objects.

Both policies are explicit inputs. Bundled policy selection belongs to the
architecture gallery, not to this generic construction boundary.
"""

from __future__ import annotations

from typing import Mapping

from heteqsys.program.statistics import CircuitStatistics
from heteqsys.qec.protocol import QECResourceProtocolProfile

from .identifiers import SubmoduleKey
from .logical_layout import LogicalLayoutRequest
from .logical_layout_policy import LogicalLayoutPolicy
from .profile import ArchitectureProfile
from .resolver import resolve_architecture
from .sizing import SizingPolicy
from .specification import ArchitectureSpecification, QECBinding


def construct_architecture(
    profile: ArchitectureProfile,
    statistics: CircuitStatistics,
    sizing_policy: SizingPolicy,
    *,
    layout_policy: LogicalLayoutPolicy,
    layout_request: LogicalLayoutRequest | None = None,
    reference_statistics: CircuitStatistics | None = None,
    sizing_overrides: Mapping[SubmoduleKey, int] | None = None,
    qec_bindings: Mapping[SubmoduleKey, QECBinding] | None = None,
    selected_qec_protocols: Mapping[
        SubmoduleKey, QECResourceProtocolProfile
    ]
    | None = None,
) -> ArchitectureSpecification:
    """Construct one static architecture from typed policy inputs.

    ``statistics`` and ``reference_statistics`` are architecture-independent
    circuit facts.  ``sizing_policy`` converts them into exact capacities;
    ``layout_policy`` converts those capacities into complete slot identities
    and logical placement.  The generic resolver then joins both results with
    the Profile hierarchy and the explicitly selected QEC annotations.

    No bundled Profile lookup occurs here. Gallery entries and third-party
    callers supply both policies explicitly, so core construction never
    branches on a Profile ID.
    """

    if not isinstance(profile, ArchitectureProfile):
        raise TypeError("profile must be an ArchitectureProfile")
    if not isinstance(statistics, CircuitStatistics):
        raise TypeError("statistics must be CircuitStatistics")
    if reference_statistics is not None and not isinstance(
        reference_statistics, CircuitStatistics
    ):
        raise TypeError(
            "reference_statistics must be CircuitStatistics or None"
        )
    if not isinstance(sizing_policy, SizingPolicy):
        raise TypeError("sizing_policy must be a SizingPolicy")
    if not isinstance(layout_policy, LogicalLayoutPolicy):
        raise TypeError("layout_policy must be a LogicalLayoutPolicy")
    if layout_request is not None and not isinstance(
        layout_request, LogicalLayoutRequest
    ):
        raise TypeError("layout_request must be a LogicalLayoutRequest or None")

    sizing = sizing_policy.size(
        profile,
        statistics,
        selected_qec_protocols=selected_qec_protocols,
        reference_statistics=reference_statistics,
        overrides=sizing_overrides,
    )
    logical_layout = layout_policy.place(
        profile,
        sizing,
        request=layout_request,
    )
    return resolve_architecture(
        profile,
        sizing,
        logical_layout,
        qec_bindings=qec_bindings,
        selected_qec_protocols=selected_qec_protocols,
    )


__all__ = ["construct_architecture"]
