"""The reference SC-CF architecture implementation."""

from pathlib import Path

from ...profile import load_architecture_profile
from .layout import (
    SuperconductingCheckerboardLayoutPolicy,
    make_layout_policy,
)
from .sizing import (
    SuperconductingComputeFactorySizingPolicy,
    make_sizing_policy,
)


PROFILE = load_architecture_profile(Path(__file__).with_name("profile.yaml"))


__all__ = [
    "PROFILE",
    "SuperconductingCheckerboardLayoutPolicy",
    "SuperconductingComputeFactorySizingPolicy",
    "make_layout_policy",
    "make_sizing_policy",
]
