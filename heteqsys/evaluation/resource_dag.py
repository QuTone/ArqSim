"""Streaming resource-production graph.

Nodes are recurrent ISA templates.  Edges are implicit in named buffers: one
process produces a token that another process consumes.  Instances are created
lazily, so a long-running factory does not materialize an unbounded DAG.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from heteqsys.architecture.isa import (
    RESOURCE_OPCODES,
    ArchitectureOpcode,
    DeferredDispatchRecipe,
    MagicRouteDispatchRecipe,
    OperationClaims,
    ResourceMoveDispatchRecipe,
    deferred_dispatch_recipe_from_dict,
)
from heteqsys.operation_profiles import ArrivalDistribution
from heteqsys.schema import deep_freeze_json, normalize_json


@dataclass(frozen=True)
class ResourceProcess(OperationClaims):
    id: str
    opcode: ArchitectureOpcode | str
    duration_s: float = 0.0
    arrival_distribution: ArrivalDistribution | None = None
    parallelism: int = 1
    dispatch_policy: str = "single"
    output_overflow_policy: str = "block"
    protocol: str = "default"
    metadata: Mapping[str, Any] = field(default_factory=dict)
    deferred_dispatch: DeferredDispatchRecipe | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise TypeError("Resource process id must be a non-empty string")
        if type(self.duration_s) not in {int, float} or not math.isfinite(
            self.duration_s
        ):
            raise TypeError("Resource process duration_s must be a finite number")
        if type(self.parallelism) is not int:
            raise TypeError("Resource process parallelism must be an integer")
        for name in (
            "dispatch_policy",
            "output_overflow_policy",
            "protocol",
        ):
            if type(getattr(self, name)) is not str:
                raise TypeError(f"Resource process {name} must be a string")
        OperationClaims.__post_init__(self)
        if not isinstance(self.metadata, Mapping):
            raise TypeError("Resource process metadata must be a mapping")
        if self.arrival_distribution is not None and not isinstance(
            self.arrival_distribution,
            ArrivalDistribution,
        ):
            raise TypeError(
                "Resource process arrival_distribution must be typed or None"
            )
        try:
            opcode = ArchitectureOpcode(self.opcode)
        except ValueError as exc:
            raise ValueError(f"Unsupported resource opcode: {self.opcode}") from exc
        duration = float(self.duration_s)
        if opcode not in RESOURCE_OPCODES:
            raise ValueError(f"Program-only opcode in Resource DAG: {opcode.value}")
        if not self.id.strip() or not math.isfinite(duration) or duration < 0:
            raise ValueError("Resource process id and duration must be valid")
        if self.parallelism <= 0:
            raise ValueError("Resource process parallelism must be positive")
        if self.dispatch_policy not in {"single", "eager_available"}:
            raise ValueError(
                "Resource dispatch_policy must be 'single' or 'eager_available'"
            )
        if self.dispatch_policy == "eager_available" and self.parallelism != 1:
            raise ValueError("An eager resource process owns one batched dispatcher")
        if self.output_overflow_policy not in {"block", "discard_excess"}:
            raise ValueError(
                "Resource output_overflow_policy must be 'block' or "
                "'discard_excess'"
            )
        consumes = self.consumes
        produces = self.produces
        forwards = self.forwards
        object.__setattr__(self, "opcode", opcode)
        object.__setattr__(self, "duration_s", duration)
        object.__setattr__(self, "metadata", deep_freeze_json(self.metadata))
        legacy_control_metadata = {
            "deferred_until_dispatch",
            "dispatch_deferred",
            "runtime_move_compilation",
        } & set(self.metadata)
        if legacy_control_metadata:
            raise ValueError(
                "Legacy dispatch control cannot be stored in resource-process "
                f"metadata: {sorted(legacy_control_metadata)}"
            )
        route_metrics = self.metadata.get("route_metrics")
        if isinstance(route_metrics, Mapping):
            nested_legacy_control = {
                "deferred_until_dispatch",
                "dispatch_deferred",
            } & set(route_metrics)
            if nested_legacy_control:
                raise ValueError(
                    "Legacy dispatch control cannot be stored in "
                    "resource-process metadata.route_metrics: "
                    f"{sorted(nested_legacy_control)}"
                )
        if isinstance(self.deferred_dispatch, MagicRouteDispatchRecipe):
            raise ValueError("Magic-route recipes belong to the Program DAG")
        if self.deferred_dispatch is not None and not isinstance(
            self.deferred_dispatch,
            ResourceMoveDispatchRecipe,
        ):
            raise ValueError("deferred_dispatch must be a typed Resource recipe")
        if (
            isinstance(self.deferred_dispatch, ResourceMoveDispatchRecipe)
            and opcode != ArchitectureOpcode.MOVE_QUBITS
        ):
            raise ValueError(
                "A resource-move recipe is valid only for MOVE_QUBITS"
            )
        if isinstance(self.deferred_dispatch, ResourceMoveDispatchRecipe):
            if len(forwards) != 1:
                raise ValueError(
                    "A resource-move recipe requires exactly one forwarded flow"
                )
            source, destination = next(iter(forwards.items()))
            if source not in consumes or destination not in produces:
                raise ValueError(
                    "A resource-move forwarded flow must name consumed and "
                    "produced buffers"
                )
            if consumes[source] != produces[destination]:
                raise ValueError(
                    "A resource-move forwarded flow must preserve token count"
                )
        if isinstance(self.deferred_dispatch, ResourceMoveDispatchRecipe):
            duplicate_recipe_facts = {
                "deferred_until_dispatch",
                "dispatch_deferred",
                "entity_kind",
                "runtime_move_compilation",
            } & set(self.metadata)
            if duplicate_recipe_facts:
                raise ValueError(
                    "Typed resource-move recipe cannot be duplicated in "
                    f"metadata: {sorted(duplicate_recipe_facts)}"
                )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "id": self.id,
            "opcode": self.opcode.value,
            "consumes": normalize_json(self.consumes),
            "produces": normalize_json(self.produces),
            "forwards": normalize_json(self.forwards),
            "engines": normalize_json(self.engines),
            "duration_s": self.duration_s,
            "arrival_distribution": (
                self.arrival_distribution.to_dict() if self.arrival_distribution else None
            ),
            "parallelism": self.parallelism,
            "dispatch_policy": self.dispatch_policy,
            "output_overflow_policy": self.output_overflow_policy,
            "protocol": self.protocol,
            "target_modules": list(self.target_modules),
            "target_links": list(self.target_links),
            "metadata": normalize_json(self.metadata),
        }
        if self.deferred_dispatch is not None:
            result["deferred_dispatch"] = self.deferred_dispatch.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResourceProcess":
        if not isinstance(data, Mapping):
            raise ValueError("Resource process must be a mapping")
        allowed = {
            "id",
            "opcode",
            "consumes",
            "produces",
            "forwards",
            "engines",
            "duration_s",
            "arrival_distribution",
            "parallelism",
            "dispatch_policy",
            "output_overflow_policy",
            "protocol",
            "target_modules",
            "target_links",
            "metadata",
            "deferred_dispatch",
        }
        required = allowed - {"deferred_dispatch"}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"Unknown resource-process fields: {sorted(unknown)}")
        missing = required - set(data)
        if missing:
            raise ValueError(f"Resource process is missing: {sorted(missing)}")
        for name in (
            "id",
            "opcode",
            "dispatch_policy",
            "output_overflow_policy",
            "protocol",
        ):
            if type(data[name]) is not str:
                raise ValueError(f"Resource process {name} must be a string")
        duration = data["duration_s"]
        if type(duration) not in {int, float} or not math.isfinite(duration):
            raise ValueError("Resource process duration_s must be a finite number")
        if type(data["parallelism"]) is not int:
            raise ValueError("Resource process parallelism must be an integer")
        for name in ("consumes", "produces", "engines"):
            value = data[name]
            if not isinstance(value, Mapping) or any(
                type(key) is not str or type(amount) is not int
                for key, amount in value.items()
            ):
                raise ValueError(
                    f"Resource process {name} must map strings to integers"
                )
        forwards = data["forwards"]
        if not isinstance(forwards, Mapping) or any(
            type(key) is not str or type(value) is not str
            for key, value in forwards.items()
        ):
            raise ValueError(
                "Resource process forwards must map strings to strings"
            )
        for name in ("target_modules", "target_links"):
            value = data[name]
            if type(value) is not list or any(type(item) is not str for item in value):
                raise ValueError(f"Resource process {name} must be a string array")
        if not isinstance(data["metadata"], Mapping):
            raise ValueError("Resource process metadata must be a mapping")
        arrival = data["arrival_distribution"]
        if arrival is not None and not isinstance(arrival, Mapping):
            raise ValueError(
                "Resource process arrival_distribution must be a mapping or null"
            )
        if isinstance(arrival, Mapping):
            arrival_fields = {
                "kind",
                "mean_interval_s",
                "success_probability",
                "trace_intervals_s",
                "repeat_trace",
                "initial_delay_s",
                "initial_delay_samples",
            }
            if set(arrival) != arrival_fields:
                raise ValueError(
                    "Resource process arrival_distribution must use the exact "
                    "canonical fields"
                )
            if type(arrival["kind"]) is not str:
                raise ValueError("Arrival kind must be a string")
            for name in (
                "mean_interval_s",
                "success_probability",
                "initial_delay_s",
            ):
                value = arrival[name]
                if type(value) not in {int, float} or not math.isfinite(value):
                    raise ValueError(f"Arrival {name} must be a finite number")
            trace = arrival["trace_intervals_s"]
            if type(trace) is not list or any(
                type(value) not in {int, float} or not math.isfinite(value)
                for value in trace
            ):
                raise ValueError(
                    "Arrival trace_intervals_s must be a finite-number array"
                )
            if type(arrival["repeat_trace"]) is not bool:
                raise ValueError("Arrival repeat_trace must be a boolean")
            if type(arrival["initial_delay_samples"]) is not int:
                raise ValueError("Arrival initial_delay_samples must be an integer")
        if "deferred_dispatch" in data and not isinstance(
            data["deferred_dispatch"], Mapping
        ):
            raise ValueError(
                "Resource process deferred_dispatch must be a mapping"
            )
        return cls(
            id=data["id"],
            opcode=data["opcode"],
            consumes=data["consumes"],
            produces=data["produces"],
            forwards=data["forwards"],
            engines=data["engines"],
            duration_s=duration,
            arrival_distribution=(
                ArrivalDistribution.from_dict(arrival)
                if isinstance(arrival, Mapping)
                else None
            ),
            parallelism=data["parallelism"],
            dispatch_policy=data["dispatch_policy"],
            output_overflow_policy=data["output_overflow_policy"],
            protocol=data["protocol"],
            target_modules=tuple(data["target_modules"]),
            target_links=tuple(data["target_links"]),
            metadata=data["metadata"],
            deferred_dispatch=(
                deferred_dispatch_recipe_from_dict(data["deferred_dispatch"])
                if data.get("deferred_dispatch") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class ResourceDAG:
    processes: tuple[ResourceProcess, ...]
    fill_policy: str = "greedy_fill_to_capacity"

    def __post_init__(self) -> None:
        if self.fill_policy != "greedy_fill_to_capacity":
            raise ValueError("Only greedy_fill_to_capacity is currently supported")
        processes = tuple(self.processes)
        if any(not isinstance(process, ResourceProcess) for process in processes):
            raise TypeError("Resource DAG processes must be ResourceProcess values")
        ids = [process.id for process in processes]
        if len(ids) != len(set(ids)):
            raise ValueError("Resource process ids must be unique")
        object.__setattr__(self, "processes", processes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fill_policy": self.fill_policy,
            "processes": [process.to_dict() for process in self.processes],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ResourceDAG":
        if not isinstance(data, Mapping):
            raise ValueError("Resource DAG must be a mapping")
        if set(data) != {"fill_policy", "processes"}:
            unknown = set(data) - {"fill_policy", "processes"}
            missing = {"fill_policy", "processes"} - set(data)
            raise ValueError(
                "Resource DAG fields are invalid: "
                f"unknown={sorted(unknown)}, missing={sorted(missing)}"
            )
        if type(data["fill_policy"]) is not str:
            raise ValueError("Resource DAG fill_policy must be a string")
        if type(data["processes"]) is not list:
            raise ValueError("Resource DAG processes must be an array")
        return cls(
            tuple(ResourceProcess.from_dict(item) for item in data["processes"]),
            fill_policy=data["fill_policy"],
        )
