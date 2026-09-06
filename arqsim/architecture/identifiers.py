"""Opaque identifier validation shared by architecture contracts.

Identifiers are opaque labels only.  Their spelling does not select
architecture behavior or encode hierarchy; hierarchy is carried by the
surrounding data structure and by qualified keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def require_local_id(value: Any, *, where: str) -> str:
    """Return a trimmed, non-empty component key.

    ``/`` is reserved because local qualified keys are serialized as
    ``Module/Submodule``.  All other characters are intentionally opaque.
    """

    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{where} must be a non-empty trimmed string: {value!r}")
    if "/" in value:
        raise ValueError(f"{where} must not contain '/': {value!r}")
    return value


def require_profile_id(value: Any) -> str:
    """Return an opaque, trimmed, non-empty Architecture Profile key."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(
            "Architecture Profile ID must be a non-empty trimmed string: "
            f"{value!r}"
        )
    return value


@dataclass(frozen=True, slots=True, order=True)
class SubmoduleKey:
    """Opaque absolute ``owner/module/submodule`` construction key."""

    owner_id: str
    module_id: str
    submodule_id: str

    def __post_init__(self) -> None:
        require_local_id(self.owner_id, where="Submodule owner ID")
        require_local_id(self.module_id, where="Submodule Module ID")
        require_local_id(self.submodule_id, where="Submodule ID")

    def __str__(self) -> str:
        return f"{self.owner_id}/{self.module_id}/{self.submodule_id}"


__all__ = ["SubmoduleKey", "require_local_id", "require_profile_id"]
