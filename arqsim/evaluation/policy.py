"""Execution policies that change DAG lowering or runtime behavior."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class RuntimeInjectionMode(str, Enum):
    """How logical state injection is represented at runtime."""

    BLACK_BOX = "black_box"
    FINITE_STATE_INJECTION_V1 = "finite_state_injection_v1"


_STORE_LOAD_POLICY = "dependency_aware_overlap"
_RESOURCE_FILL_POLICY = "greedy_fill_to_capacity"
_SELECTED_LAYERS: tuple[int, ...] = ()


@dataclass(frozen=True, init=False)
class ExecutionPolicy:
    """User-selectable execution behavior.

    The five dataclass fields are the policy controls that can actually vary.
    Older Python attribute names remain available as read-only properties, and
    :meth:`to_dict` deliberately retains the frozen eight-field v2 wire shape.
    """

    magic_state_batching: str = "bulk_wave"
    injection_lowering_mode: RuntimeInjectionMode = RuntimeInjectionMode.BLACK_BOX
    observation_level: str = "full"
    run_seed: int = 0
    max_transitions: int = 10_000_000

    def __init__(
        self,
        magic_state_batching: str = "bulk_wave",
        injection_lowering_mode: RuntimeInjectionMode | str = (
            RuntimeInjectionMode.BLACK_BOX
        ),
        observation_level: str = "full",
        run_seed: int = 0,
        max_transitions: int = 10_000_000,
        **compatibility: Any,
    ) -> None:
        """Build a policy, accepting active legacy names during migration.

        The ``compatibility`` catch-all is intentionally not part of the
        dataclass field surface.  It keeps existing callers in ``api.py`` and
        the CLI operational while their old active-control names migrate.  The
        three invariant v2 wire fields are accepted only at their canonical
        values; they are not selectable policy controls.
        """

        values: dict[str, Any] = {
            "magic_state_batching": magic_state_batching,
            "injection_lowering_mode": injection_lowering_mode,
            "observation_level": observation_level,
            "run_seed": run_seed,
            "max_transitions": max_transitions,
        }
        defaults: dict[str, Any] = {
            "magic_state_batching": "bulk_wave",
            "injection_lowering_mode": RuntimeInjectionMode.BLACK_BOX,
            "observation_level": "full",
            "run_seed": 0,
            "max_transitions": 10_000_000,
        }
        active_aliases = {
            "magic_state_consumption": "magic_state_batching",
            "runtime_injection_mode": "injection_lowering_mode",
            "trace_level": "observation_level",
            "seed": "run_seed",
            "max_events": "max_transitions",
        }
        for legacy_name, field_name in active_aliases.items():
            if legacy_name not in compatibility:
                continue
            legacy_value = compatibility.pop(legacy_name)
            if (
                values[field_name] != defaults[field_name]
                and values[field_name] != legacy_value
            ):
                raise TypeError(
                    f"Cannot specify both {field_name!r} and its legacy alias "
                    f"{legacy_name!r}"
                )
            values[field_name] = legacy_value

        self._validate_fixed_compatibility(compatibility)
        if compatibility:
            raise TypeError(
                "Unexpected ExecutionPolicy constructor fields: "
                f"{sorted(compatibility)}"
            )

        object.__setattr__(
            self, "magic_state_batching", values["magic_state_batching"]
        )
        object.__setattr__(
            self, "injection_lowering_mode", values["injection_lowering_mode"]
        )
        object.__setattr__(self, "observation_level", values["observation_level"])
        object.__setattr__(self, "run_seed", values["run_seed"])
        object.__setattr__(self, "max_transitions", values["max_transitions"])
        self.__post_init__()

    @staticmethod
    def _validate_fixed_compatibility(compatibility: dict[str, Any]) -> None:
        if "store_load_policy" in compatibility:
            value = compatibility.pop("store_load_policy")
            if not isinstance(value, str):
                raise TypeError("store_load_policy must be a string")
            if value != _STORE_LOAD_POLICY:
                raise ValueError(
                    "Only dependency_aware_overlap is currently supported for "
                    "Store/Load lowering"
                )
        if "resource_fill_policy" in compatibility:
            value = compatibility.pop("resource_fill_policy")
            if not isinstance(value, str):
                raise TypeError("resource_fill_policy must be a string")
            if value != _RESOURCE_FILL_POLICY:
                raise ValueError(
                    "Only greedy_fill_to_capacity is currently supported"
                )
        if "selected_layers" in compatibility:
            value = compatibility.pop("selected_layers")
            selected_layers = ExecutionPolicy._normalize_selected_layers(value)
            if selected_layers:
                raise ValueError(
                    "EvaluationPolicy v1 does not support selected_layers"
                )

    @staticmethod
    def _normalize_selected_layers(value: Any) -> tuple[int, ...]:
        try:
            selected_layers_input = tuple(value)
        except TypeError as exc:
            raise TypeError(
                "selected_layers must contain non-negative integers"
            ) from exc
        if any(
            not isinstance(layer, int)
            or isinstance(layer, bool)
            or layer < 0
            for layer in selected_layers_input
        ):
            raise TypeError("selected_layers must contain non-negative integers")
        return tuple(sorted(set(selected_layers_input)))

    def __post_init__(self) -> None:
        if not isinstance(self.magic_state_batching, str):
            raise TypeError("magic_state_batching must be a string")
        if not isinstance(self.observation_level, str):
            raise TypeError("observation_level must be a string")
        try:
            injection_mode = RuntimeInjectionMode(self.injection_lowering_mode)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Unsupported runtime injection mode (injection_lowering_mode): "
                f"{self.injection_lowering_mode!r}"
            ) from exc
        object.__setattr__(self, "injection_lowering_mode", injection_mode)
        if not isinstance(self.run_seed, int) or isinstance(self.run_seed, bool):
            raise TypeError("run_seed must be an integer")
        if not isinstance(self.max_transitions, int) or isinstance(
            self.max_transitions, bool
        ):
            raise TypeError("max_transitions must be an integer")
        if self.magic_state_batching not in {"bulk_wave", "incremental"}:
            raise ValueError("Unsupported magic-state batching policy")
        if self.observation_level not in {"summary", "full"}:
            raise ValueError(
                "ExecutionPolicy supports only summary or full observation levels"
            )
        if self.max_transitions <= 0:
            raise ValueError("max_transitions must be positive")

    @property
    def magic_state_consumption(self) -> str:
        """Legacy read-only alias for :attr:`magic_state_batching`."""

        return self.magic_state_batching

    @property
    def runtime_injection_mode(self) -> RuntimeInjectionMode:
        """Legacy read-only alias for :attr:`injection_lowering_mode`."""

        return self.injection_lowering_mode

    @property
    def trace_level(self) -> str:
        """Legacy read-only alias for :attr:`observation_level`."""

        return self.observation_level

    @property
    def seed(self) -> int:
        """Legacy read-only alias for :attr:`run_seed`."""

        return self.run_seed

    @property
    def max_events(self) -> int:
        """Legacy read-only alias for :attr:`max_transitions`."""

        return self.max_transitions

    @property
    def store_load_policy(self) -> str:
        """Frozen v2 wire value retained as a read-only compatibility view."""

        return _STORE_LOAD_POLICY

    @property
    def resource_fill_policy(self) -> str:
        """Frozen v2 wire value retained as a read-only compatibility view."""

        return _RESOURCE_FILL_POLICY

    @property
    def selected_layers(self) -> tuple[int, ...]:
        """Frozen v2 wire value retained as a read-only compatibility view."""

        return _SELECTED_LAYERS

    def to_dict(self) -> dict[str, Any]:
        """Return the frozen eight-field Report v2 wire representation."""

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
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutionPolicy":
        """Decode the frozen eight-field Report v2 wire representation."""

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

        fixed_fields = {
            "store_load_policy": data.get(
                "store_load_policy", _STORE_LOAD_POLICY
            ),
            "resource_fill_policy": data.get(
                "resource_fill_policy", _RESOURCE_FILL_POLICY
            ),
            "selected_layers": data.get("selected_layers", _SELECTED_LAYERS),
        }
        cls._validate_fixed_compatibility(fixed_fields)
        return cls(
            magic_state_batching=data.get(
                "magic_state_consumption", "bulk_wave"
            ),
            injection_lowering_mode=data.get(
                "runtime_injection_mode", RuntimeInjectionMode.BLACK_BOX.value
            ),
            observation_level=data.get("trace_level", "full"),
            run_seed=data.get("seed", 0),
            max_transitions=data.get("max_events", 10_000_000),
        )


# Historical import name.  This is an exact alias so isinstance checks and old
# imports continue to describe the same policy value object.
EvaluationPolicy = ExecutionPolicy


__all__ = ["EvaluationPolicy", "ExecutionPolicy", "RuntimeInjectionMode"]
