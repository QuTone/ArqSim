"""Bundled NA-MCF architecture implementation."""

from pathlib import Path

from ...profile import load_architecture_profile
from .layout import NeutralAtomMemoryComputeLayoutPolicy, make_layout_policy
from .sizing import MemoryComputeSizingPolicy, make_sizing_policy


PROFILE = load_architecture_profile(Path(__file__).with_name("profile.yaml"))


__all__ = [
    "MemoryComputeSizingPolicy",
    "NeutralAtomMemoryComputeLayoutPolicy",
    "PROFILE",
    "make_layout_policy",
    "make_sizing_policy",
]
