"""Execution policies that change DAG lowering or runtime behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class RuntimeInjectionMode(str, Enum):
    """How logical state injection is represented at runtime."""

    BLACK_BOX = "black_box"
    FINITE_STATE_INJECTION_V1 = "finite_state_injection_v1"


@dataclass(frozen=True)
class EvaluationPolicy:
    magic_state_consumption: str = "bulk_wave"
    store_load_policy: str = "dependency_aware_overlap"
    resource_fill_policy: str = "greedy_fill_to_capacity"
    trace_level: str = "summary"
    runtime_injection_mode: RuntimeInjectionMode | str = RuntimeInjectionMode.BLACK_BOX
    selected_layers: tuple[int, ...] = field(default_factory=tuple)
    seed: int = 0
    max_events: int = 10_000_000

    def __post_init__(self) -> None:
        for name in (
            "magic_state_consumption",
            "store_load_policy",
            "resource_fill_policy",
            "trace_level",
        ):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"{name} must be a string")
        try:
            injection_mode = RuntimeInjectionMode(self.runtime_injection_mode)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported runtime injection mode: {self.runtime_injection_mode!r}"
            ) from exc
        object.__setattr__(self, "runtime_injection_mode", injection_mode)
        if not isinstance(self.seed, int) or isinstance(self.seed, bool):
            raise TypeError("seed must be an integer")
        if not isinstance(self.max_events, int) or isinstance(
            self.max_events, bool
        ):
            raise TypeError("max_events must be an integer")
        if self.magic_state_consumption not in {"bulk_wave", "incremental"}:
            raise ValueError("Unsupported magic-state consumption policy")
        if self.store_load_policy != "dependency_aware_overlap":
            raise ValueError(
                "Only dependency_aware_overlap is currently supported for "
                "Store/Load lowering"
            )
        if self.resource_fill_policy != "greedy_fill_to_capacity":
            raise ValueError("Only greedy_fill_to_capacity is currently supported")
        if self.trace_level not in {"summary", "full"}:
            raise ValueError(
                "EvaluationPolicy v1 supports only summary or full trace levels"
            )
        if self.max_events <= 0:
            raise ValueError("max_events must be positive")
        selected_layers_input = tuple(self.selected_layers)
        if any(
            not isinstance(layer, int)
            or isinstance(layer, bool)
            or layer < 0
            for layer in selected_layers_input
        ):
            raise TypeError(
                "selected_layers must contain non-negative integers"
            )
        selected_layers = tuple(sorted(set(selected_layers_input)))
        if selected_layers:
            raise ValueError(
                "EvaluationPolicy v1 does not support selected_layers"
            )
        object.__setattr__(self, "selected_layers", selected_layers)

    def to_dict(self) -> dict[str, Any]:
        return {
            "magic_state_consumption": self.magic_state_consumption,
            "store_load_policy": self.store_load_policy,
            "resource_fill_policy": self.resource_fill_policy,
            "trace_level": self.trace_level,
            "runtime_injection_mode": self.runtime_injection_mode.value,
            "selected_layers": list(self.selected_layers),
            "seed": self.seed,
            "max_events": self.max_events,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EvaluationPolicy":
        allowed = {
            "magic_state_consumption",
            "store_load_policy",
            "resource_fill_policy",
            "trace_level",
            "runtime_injection_mode",
            "selected_layers",
            "seed",
            "max_events",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"Unknown evaluation-policy fields: {sorted(unknown)}")
        return cls(
            magic_state_consumption=data.get(
                "magic_state_consumption", "bulk_wave"
            ),
            store_load_policy=data.get(
                "store_load_policy", "dependency_aware_overlap"
            ),
            resource_fill_policy=data.get(
                "resource_fill_policy", "greedy_fill_to_capacity"
            ),
            trace_level=data.get("trace_level", "summary"),
            runtime_injection_mode=data.get(
                "runtime_injection_mode", RuntimeInjectionMode.BLACK_BOX.value
            ),
            selected_layers=tuple(data.get("selected_layers", ())),
            seed=data.get("seed", 0),
            max_events=data.get("max_events", 10_000_000),
        )
