"""The reference NA-CF architecture implementation."""

from pathlib import Path

from ...profile import load_architecture_profile
from .layout import (
    NeutralAtomComputeFactoryLayoutPolicy,
    make_layout_policy,
)
from .sizing import (
    NeutralAtomComputeFactorySizingPolicy,
    make_sizing_policy,
)


PROFILE = load_architecture_profile(Path(__file__).with_name("profile.yaml"))


__all__ = [
    "PROFILE",
    "NeutralAtomComputeFactoryLayoutPolicy",
    "NeutralAtomComputeFactorySizingPolicy",
    "make_layout_policy",
    "make_sizing_policy",
]
