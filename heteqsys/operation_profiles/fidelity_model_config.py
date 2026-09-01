"""Load machine-readable operation-fidelity fit configurations."""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
from typing import Any

import yaml


@lru_cache(maxsize=None)
def load_fidelity_fit(profile_id: str) -> dict[str, Any]:
    """Return one validated fidelity-fit configuration by ID."""

    resource = files("heteqsys.operation_profiles").joinpath(
        "fidelity_profiles", f"{profile_id}.yaml"
    )
    if not resource.is_file():
        raise KeyError(f"Unknown fidelity-fit profile: {profile_id}")
    payload = yaml.safe_load(resource.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Fidelity-fit profile {profile_id} must be a mapping")
    if payload.get("schema_version") != "arqsim.fidelity-fit.v1":
        raise ValueError(
            f"Unsupported fidelity-fit schema in {profile_id}: "
            f"{payload.get('schema_version')!r}"
        )
    for key in ("experiment", "multi_patch_fit", "weight_one_control", "source"):
        if not isinstance(payload.get(key), dict):
            raise ValueError(f"Fidelity-fit profile {profile_id} is missing {key}")
    return payload


__all__ = ["load_fidelity_fit"]
