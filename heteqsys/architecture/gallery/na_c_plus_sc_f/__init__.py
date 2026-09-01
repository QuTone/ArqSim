"""The reference NA-C + SC-F architecture implementation."""

from pathlib import Path

from ...profile import load_architecture_profile
from .layout import HybridRemoteMagicLayoutPolicy, make_layout_policy
from .sizing import RemoteMagicSizingPolicy, make_sizing_policy


PROFILE = load_architecture_profile(Path(__file__).with_name("profile.yaml"))


__all__ = [
    "PROFILE",
    "HybridRemoteMagicLayoutPolicy",
    "RemoteMagicSizingPolicy",
    "make_layout_policy",
    "make_sizing_policy",
]
