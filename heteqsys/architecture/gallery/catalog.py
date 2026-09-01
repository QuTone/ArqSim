"""Explicit index of the architecture designs bundled with ArqSim.

The gallery is a convenience for selecting shipped Profile and policy
implementations.  It is not an architecture level, a serialization format, or
a plugin registry.  Custom Profiles and policies continue to enter through
the generic construction API without registration here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from ..logical_layout_policy import LogicalLayoutPolicy
from ..profile import ArchitectureProfile
from ..sizing import SizingPolicy
from .na_c_plus_sc_f import (
    PROFILE as _NA_C_PLUS_SC_F_PROFILE,
    make_layout_policy as _make_na_c_plus_sc_f_layout_policy,
    make_sizing_policy as _make_na_c_plus_sc_f_sizing_policy,
)
from .na_cf import (
    PROFILE as _NA_CF_PROFILE,
    make_layout_policy as _make_na_cf_layout_policy,
    make_sizing_policy as _make_na_cf_sizing_policy,
)
from .na_m_plus_sc_cf import (
    PROFILE as _NA_M_PLUS_SC_CF_PROFILE,
    make_layout_policy as _make_na_m_plus_sc_cf_layout_policy,
    make_sizing_policy as _make_na_m_plus_sc_cf_sizing_policy,
)
from .na_mc_plus_sc_f import (
    PROFILE as _NA_MC_PLUS_SC_F_PROFILE,
    make_layout_policy as _make_na_mc_plus_sc_f_layout_policy,
    make_sizing_policy as _make_na_mc_plus_sc_f_sizing_policy,
)
from .na_mcf import (
    PROFILE as _NA_MCF_PROFILE,
    make_layout_policy as _make_na_mcf_layout_policy,
    make_sizing_policy as _make_na_mcf_sizing_policy,
)
from .quantile import QuantileSizingConfig
from .sc_cf import (
    PROFILE as _SC_CF_PROFILE,
    make_layout_policy as _make_sc_cf_layout_policy,
    make_sizing_policy as _make_sc_cf_sizing_policy,
)


SizingPolicyFactory = Callable[[QuantileSizingConfig], SizingPolicy]
LayoutPolicyFactory = Callable[[], LogicalLayoutPolicy]


@dataclass(frozen=True, slots=True)
class GalleryEntry:
    """Runtime association of one bundled Profile with its policy factories.

    The Profile remains the sole topology fact.  This descriptor only keeps
    the three shipped pieces together for selection and deliberately has no
    codec, schema version, semantic hash, or registration behavior.
    """

    profile: ArchitectureProfile
    sizing_policy_factory: SizingPolicyFactory
    layout_policy_factory: LayoutPolicyFactory

    def __post_init__(self) -> None:
        if not isinstance(self.profile, ArchitectureProfile):
            raise TypeError("GalleryEntry profile must be an ArchitectureProfile")
        if not callable(self.sizing_policy_factory):
            raise TypeError("GalleryEntry sizing_policy_factory must be callable")
        if not callable(self.layout_policy_factory):
            raise TypeError("GalleryEntry layout_policy_factory must be callable")

    @property
    def id(self) -> str:
        """Return the canonical ID owned by the bundled Profile."""

        return self.profile.id

    def make_sizing_policy(
        self,
        config: QuantileSizingConfig,
    ) -> SizingPolicy:
        """Instantiate this architecture's sizing policy for one experiment."""

        if not isinstance(config, QuantileSizingConfig):
            raise TypeError("config must be a QuantileSizingConfig")
        policy = self.sizing_policy_factory(config)
        if not isinstance(policy, SizingPolicy):
            raise TypeError(
                "Gallery sizing policy factory must return a SizingPolicy"
            )
        return policy

    def make_layout_policy(self) -> LogicalLayoutPolicy:
        """Instantiate this architecture's bundled logical-layout recipe."""

        policy = self.layout_policy_factory()
        if not isinstance(policy, LogicalLayoutPolicy):
            raise TypeError(
                "Gallery layout policy factory must return a LogicalLayoutPolicy"
            )
        return policy


_ENTRIES_BY_ID: Mapping[str, GalleryEntry] = MappingProxyType(
    {
        "1.1": GalleryEntry(
            profile=_NA_CF_PROFILE,
            sizing_policy_factory=_make_na_cf_sizing_policy,
            layout_policy_factory=_make_na_cf_layout_policy,
        ),
        "1.2": GalleryEntry(
            profile=_SC_CF_PROFILE,
            sizing_policy_factory=_make_sc_cf_sizing_policy,
            layout_policy_factory=_make_sc_cf_layout_policy,
        ),
        "1.3": GalleryEntry(
            profile=_NA_C_PLUS_SC_F_PROFILE,
            sizing_policy_factory=_make_na_c_plus_sc_f_sizing_policy,
            layout_policy_factory=_make_na_c_plus_sc_f_layout_policy,
        ),
        "2.1": GalleryEntry(
            profile=_NA_MCF_PROFILE,
            sizing_policy_factory=_make_na_mcf_sizing_policy,
            layout_policy_factory=_make_na_mcf_layout_policy,
        ),
        "2.2": GalleryEntry(
            profile=_NA_M_PLUS_SC_CF_PROFILE,
            sizing_policy_factory=_make_na_m_plus_sc_cf_sizing_policy,
            layout_policy_factory=_make_na_m_plus_sc_cf_layout_policy,
        ),
        "2.3": GalleryEntry(
            profile=_NA_MC_PLUS_SC_F_PROFILE,
            sizing_policy_factory=_make_na_mc_plus_sc_f_sizing_policy,
            layout_policy_factory=_make_na_mc_plus_sc_f_layout_policy,
        ),
    }
)

for _profile_id, _entry in _ENTRIES_BY_ID.items():
    if _entry.id != _profile_id:
        raise RuntimeError(
            "Architecture gallery key does not match bundled Profile ID: "
            f"key={_profile_id!r}, profile={_entry.id!r}"
        )


def list_gallery_entries() -> tuple[GalleryEntry, ...]:
    """Return all bundled architecture associations in canonical ID order."""

    return tuple(_ENTRIES_BY_ID.values())


def get_gallery_entry(profile_id: str) -> GalleryEntry:
    """Return one bundled architecture association by Profile ID.

    The two historic namespace prefixes remain accepted only at this gallery
    lookup boundary.  The Profile itself continues to own its unprefixed ID.
    """

    if not isinstance(profile_id, str):
        raise TypeError("Architecture Profile lookup ID must be a string")
    normalized = profile_id.removeprefix("heteqsys.").removeprefix("arqsim.")
    try:
        return _ENTRIES_BY_ID[normalized]
    except KeyError:
        raise KeyError(f"Unknown architecture Profile: {profile_id}") from None


def list_architecture_profiles() -> tuple[ArchitectureProfile, ...]:
    """Return the canonical Profiles represented by the bundled gallery."""

    return tuple(entry.profile for entry in list_gallery_entries())


def get_architecture_profile(profile_id: str) -> ArchitectureProfile:
    """Return one canonical bundled Profile by ID."""

    return get_gallery_entry(profile_id).profile


__all__ = [
    "GalleryEntry",
    "get_architecture_profile",
    "get_gallery_entry",
    "list_architecture_profiles",
    "list_gallery_entries",
]
