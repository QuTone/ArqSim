"""Stochastic timing distributions used by resource producers."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Mapping


ARRIVAL_DISTRIBUTIONS = frozenset({"deterministic", "exponential", "geometric", "trace"})


@dataclass(frozen=True)
class ArrivalDistribution:
    kind: str = "deterministic"
    mean_interval_s: float = 1.0
    success_probability: float = 1.0
    trace_intervals_s: tuple[float, ...] = field(default_factory=tuple)
    repeat_trace: bool = True
    initial_delay_s: float = 0.0
    initial_delay_samples: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str):
            raise TypeError("Arrival-distribution kind must be a string")
        for name, value in (
            ("mean_interval_s", self.mean_interval_s),
            ("success_probability", self.success_probability),
            ("initial_delay_s", self.initial_delay_s),
        ):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TypeError(f"{name} must be a real number")
        if not isinstance(self.repeat_trace, bool):
            raise TypeError("repeat_trace must be a boolean")
        if not isinstance(self.initial_delay_samples, int) or isinstance(
            self.initial_delay_samples, bool
        ):
            raise TypeError("initial_delay_samples must be an integer")
        raw_trace = tuple(self.trace_intervals_s)
        if any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in raw_trace
        ):
            raise TypeError("trace_intervals_s must contain real numbers")
        kind = self.kind.strip().lower()
        interval = float(self.mean_interval_s)
        probability = float(self.success_probability)
        trace = tuple(float(value) for value in raw_trace)
        initial_delay = float(self.initial_delay_s)
        initial_samples = int(self.initial_delay_samples)
        if kind not in ARRIVAL_DISTRIBUTIONS:
            raise ValueError(f"Unsupported arrival distribution: {self.kind}")
        if kind in {"deterministic", "exponential", "geometric"} and (
            not math.isfinite(interval) or interval <= 0
        ):
            raise ValueError("Mean arrival interval must be finite and positive")
        if not math.isfinite(probability) or not 0 < probability <= 1:
            raise ValueError("Success probability must be finite and in (0, 1]")
        if kind == "trace" and (
            not trace or any(not math.isfinite(value) or value <= 0 for value in trace)
        ):
            raise ValueError("Trace arrivals require positive finite intervals")
        if not math.isfinite(initial_delay) or initial_delay < 0:
            raise ValueError("Initial arrival delay must be finite and non-negative")
        if initial_samples < 0:
            raise ValueError("Initial delayed-sample count must be non-negative")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "mean_interval_s", interval)
        object.__setattr__(self, "success_probability", probability)
        object.__setattr__(self, "trace_intervals_s", trace)
        object.__setattr__(self, "initial_delay_s", initial_delay)
        object.__setattr__(self, "initial_delay_samples", initial_samples)

    @property
    def period_s(self) -> float:
        """Compatibility-free readable alias used in equations and reports."""

        return self.mean_interval_s

    @classmethod
    def from_rate(
        cls, rate_per_s: float, *, kind: str = "deterministic"
    ) -> "ArrivalDistribution":
        if not isinstance(rate_per_s, (int, float)) or isinstance(
            rate_per_s, bool
        ):
            raise TypeError("Arrival rate must be a real number")
        rate = float(rate_per_s)
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError("Arrival rate must be finite and positive")
        return cls(kind=kind, mean_interval_s=1.0 / rate)

    def sample_interval(self, rng: random.Random, trace_index: int) -> tuple[float, int]:
        startup = (
            self.initial_delay_s
            if trace_index < self.initial_delay_samples
            else 0.0
        )
        if self.kind == "deterministic":
            return self.mean_interval_s + startup, trace_index
        if self.kind == "exponential":
            return (
                rng.expovariate(1.0 / self.mean_interval_s) + startup,
                trace_index,
            )
        if self.kind == "geometric":
            attempts = 1
            while rng.random() > self.success_probability:
                attempts += 1
            return attempts * self.mean_interval_s + startup, trace_index
        if trace_index >= len(self.trace_intervals_s):
            if not self.repeat_trace:
                return float("inf"), trace_index
            trace_index = 0
        return self.trace_intervals_s[trace_index] + startup, trace_index + 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "mean_interval_s": self.mean_interval_s,
            "success_probability": self.success_probability,
            "trace_intervals_s": list(self.trace_intervals_s),
            "repeat_trace": self.repeat_trace,
            "initial_delay_s": self.initial_delay_s,
            "initial_delay_samples": self.initial_delay_samples,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ArrivalDistribution":
        allowed = {
            "kind",
            "mean_interval_s",
            "period_s",
            "success_probability",
            "trace_intervals_s",
            "repeat_trace",
            "initial_delay_s",
            "initial_delay_samples",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(
                f"Unknown arrival-distribution fields: {sorted(unknown)}"
            )
        if "mean_interval_s" in data and "period_s" in data:
            raise ValueError(
                "Arrival distribution cannot specify both mean_interval_s and period_s"
            )
        return cls(
            kind=data.get("kind", "deterministic"),
            mean_interval_s=data.get(
                "mean_interval_s", data.get("period_s", 1.0)
            ),
            success_probability=data.get("success_probability", 1.0),
            trace_intervals_s=tuple(data.get("trace_intervals_s", ())),
            repeat_trace=data.get("repeat_trace", True),
            initial_delay_s=data.get("initial_delay_s", 0.0),
            initial_delay_samples=data.get("initial_delay_samples", 0),
        )
