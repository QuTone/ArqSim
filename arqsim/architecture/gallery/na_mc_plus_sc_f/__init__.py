"""Bundled NA-MC + SC-F architecture implementation."""

from pathlib import Path

from ...profile import load_architecture_profile
from .layout import RemoteMagicMemoryComputeLayoutPolicy, make_layout_policy
from .sizing import RemoteMagicMemoryComputeSizingPolicy, make_sizing_policy


PROFILE = load_architecture_profile(Path(__file__).with_name("profile.yaml"))


__all__ = [
    "PROFILE",
    "RemoteMagicMemoryComputeLayoutPolicy",
    "RemoteMagicMemoryComputeSizingPolicy",
    "make_layout_policy",
    "make_sizing_policy",
]
