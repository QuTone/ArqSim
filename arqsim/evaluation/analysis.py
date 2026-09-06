"""Post-evaluation attribution over immutable ArqSim results."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Mapping

from arqsim.architecture.specification import ArchitectureSpecification
from arqsim.schema import canonical_float_sum, deep_freeze_json, normalize_json

from .footprint import PhysicalFootprintEstimate


@dataclass(frozen=True)
class QubitExposure:
    """Trace-derived logical-qubit active/idle exposure.

    The Event Engine records dispatch/completion facts and concrete location
    transitions.  This record is deliberately derived after execution: it is
    never read from the Engine's legacy ``result.metrics`` compatibility map.
    """

    logical_qubit_count: int
    idle_by_location: Mapping[str, float]
    idle_by_qubit: Mapping[str, float]
    idle_by_qubit_and_location: Mapping[str, Mapping[str, float]]
    active_by_opcode: Mapping[str, float]
    active_by_qubit: Mapping[str, float]
    idle_total: float
    active_total: float
    classified_total: float
    conflict_free: bool
    time_conserved: bool

    def __post_init__(self) -> None:
        for name in (
            "idle_by_location",
            "idle_by_qubit",
            "idle_by_qubit_and_location",
            "active_by_opcode",
            "active_by_qubit",
        ):
            object.__setattr__(self, name, deep_freeze_json(getattr(self, name)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "logical_qubit_count": self.logical_qubit_count,
            "idle_by_location": normalize_json(self.idle_by_location),
            "idle_by_qubit": normalize_json(self.idle_by_qubit),
            "idle_by_qubit_and_location": normalize_json(
                self.idle_by_qubit_and_location
            ),
            "active_by_opcode": normalize_json(self.active_by_opcode),
            "active_by_qubit": normalize_json(self.active_by_qubit),
            "idle_total": self.idle_total,
            "active_total": self.active_total,
            "classified_total": self.classified_total,
            "conflict_free": self.conflict_free,
            "time_conserved": self.time_conserved,
        }


@dataclass(frozen=True)
class EvaluationAnalysis:
    """Immutable derived metrics for one realized execution trace."""

    exclusive_time_s: Mapping[str, float] | None
    engine_utilization: Mapping[str, float]
    buffer_occupancy: Mapping[str, Mapping[str, float]] | None
    physical_space_qubits: Mapping[str, float]
    fidelity_negative_log_success: Mapping[str, float] | None
    unavailable: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "exclusive_time_s",
            "engine_utilization",
            "buffer_occupancy",
            "physical_space_qubits",
            "fidelity_negative_log_success",
            "unavailable",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, deep_freeze_json(value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "exclusive_time_s": (
                normalize_json(self.exclusive_time_s)
                if self.exclusive_time_s is not None
                else None
            ),
            "engine_utilization": normalize_json(self.engine_utilization),
            "buffer_occupancy": (
                normalize_json(self.buffer_occupancy)
                if self.buffer_occupancy is not None
                else None
            ),
            "physical_space_qubits": normalize_json(
                self.physical_space_qubits
            ),
            "fidelity_negative_log_success": (
                normalize_json(self.fidelity_negative_log_success)
                if self.fidelity_negative_log_success is not None
                else None
            ),
            "unavailable": normalize_json(self.unavailable),
        }


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _trace_transitions(result: Any) -> tuple[Any, ...]:
    trace = getattr(result, "trace", None)
    transitions = getattr(trace, "transitions", None)
    if transitions is None:
        raise ValueError(
            "Qubit exposure requires an execution trace with causal transitions"
        )
    return tuple(transitions)


def qubit_exposure(result: Any) -> QubitExposure:
    """Derive every logical-qubit second from causal trace facts.

    Completion transitions are applied before the following open time
    interval, matching the Event Engine's complete-before-dispatch timestamp
    phase.  Resource-plane work never marks Program qubits active.
    """

    trace = getattr(result, "trace", None)
    initial_state = getattr(trace, "initial_state", None)
    if initial_state is None:
        raise ValueError(
            "Qubit exposure requires an execution trace with initial_state"
        )
    horizon = float(trace.total_latency_s)
    if not math.isfinite(horizon) or horizon < 0:
        raise ValueError("Trace latency must be finite and non-negative")

    locations = {
        str(item): str(location)
        for item, location in initial_state.locations.items()
    }
    logical_qubits = tuple(
        sorted(item for item in locations if item.startswith("q:"))
    )
    transitions = _trace_transitions(result)
    by_time: defaultdict[float, list[Any]] = defaultdict(list)
    time_points = {0.0, horizon}
    for transition in transitions:
        timestamp = float(transition.time_s)
        if not math.isfinite(timestamp) or timestamp < 0 or timestamp > horizon:
            raise ValueError("Trace transition lies outside the Program horizon")
        by_time[timestamp].append(transition)
        time_points.add(timestamp)
    ordered_times = sorted(time_points)

    active: dict[int, Any] = {}
    idle_by_location: defaultdict[str, float] = defaultdict(float)
    idle_by_qubit: defaultdict[str, float] = defaultdict(float)
    idle_by_qubit_location: defaultdict[
        str, defaultdict[str, float]
    ] = defaultdict(lambda: defaultdict(float))
    active_by_opcode: defaultdict[str, float] = defaultdict(float)
    active_by_qubit: defaultdict[str, float] = defaultdict(float)
    conflict = False

    for index, timestamp in enumerate(ordered_times):
        for transition in sorted(
            by_time[timestamp], key=lambda item: int(item.transition_id)
        ):
            kind = _enum_value(transition.kind)
            plane = _enum_value(transition.plane)
            event_id = int(transition.event_id)
            if kind == "completion":
                active.pop(event_id, None)
                for item, location in transition.completion_locations.items():
                    locations[str(item)] = str(location)
            elif kind == "dispatch":
                if plane == "program":
                    active[event_id] = transition
            else:
                raise ValueError(f"Unsupported execution transition kind: {kind}")

        if index + 1 == len(ordered_times):
            continue
        delta = ordered_times[index + 1] - timestamp
        if delta <= 0:
            continue

        active_opcode_by_qubit: dict[str, str] = {}
        for transition in active.values():
            opcode = _enum_value(transition.opcode)
            for raw_qubit in transition.metadata.get("qubits", ()):
                text = str(raw_qubit)
                qubit = text if text.startswith("q:") else f"q:{int(raw_qubit)}"
                if qubit not in locations:
                    continue
                if qubit in active_opcode_by_qubit:
                    conflict = True
                active_opcode_by_qubit[qubit] = opcode

        for qubit in logical_qubits:
            opcode = active_opcode_by_qubit.get(qubit)
            if opcode is None:
                location = locations[qubit]
                idle_by_location[location] += delta
                idle_by_qubit[qubit] += delta
                idle_by_qubit_location[qubit][location] += delta
            else:
                active_by_opcode[opcode] += delta
                active_by_qubit[qubit] += delta

    idle_total = canonical_float_sum(
        idle_by_qubit[qubit] for qubit in sorted(idle_by_qubit)
    )
    active_total = canonical_float_sum(
        active_by_qubit[qubit] for qubit in sorted(active_by_qubit)
    )
    classified_total = canonical_float_sum((idle_total, active_total))
    expected_total = len(logical_qubits) * horizon
    conserved = math.isclose(
        classified_total, expected_total, rel_tol=1e-10, abs_tol=1e-12
    )
    if not conserved:
        raise AssertionError(
            "Qubit exposure does not conserve logical-qubit time: "
            f"{classified_total} != {expected_total}"
        )
    return QubitExposure(
        logical_qubit_count=len(logical_qubits),
        idle_by_location=dict(sorted(idle_by_location.items())),
        idle_by_qubit=dict(sorted(idle_by_qubit.items())),
        idle_by_qubit_and_location={
            qubit: dict(sorted(by_location.items()))
            for qubit, by_location in sorted(idle_by_qubit_location.items())
        },
        active_by_opcode=dict(sorted(active_by_opcode.items())),
        active_by_qubit=dict(sorted(active_by_qubit.items())),
        idle_total=idle_total,
        active_total=active_total,
        classified_total=classified_total,
        conflict_free=not conflict,
        time_conserved=conserved,
    )


def _program_group(opcode: str) -> str:
    return {
        "EXECUTE_COMPUTE": "compute",
        "MOVE_QUBITS": "program_move",
        "STORE_QUBITS": "store_load",
        "LOAD_QUBITS": "store_load",
        "TELEPORT_QUBITS": "program_teleport",
        "CLASSICAL_REACTION": "classical_reaction",
        "FENCE": "control",
    }.get(opcode, "other_program")


def _buffer_ready(
    architecture_state: Mapping[str, Any], name: str
) -> int:
    buffers = architecture_state.get("buffers", {})
    state = buffers.get(name, {}) if isinstance(buffers, Mapping) else {}
    return int(state.get("ready", 0)) if isinstance(state, Mapping) else 0


def _next_resource_completion(
    running: list[Mapping[str, Any]], *, opcode: str
) -> float:
    completions = [
        float(item["completes_s"])
        for item in running
        if item.get("plane") == "resource" and item.get("opcode") == opcode
    ]
    return min(completions, default=math.inf)


def _remote_magic_stall_group(
    architecture_state: Mapping[str, Any],
    running: list[Mapping[str, Any]],
) -> str:
    """Trace an empty compute-side MS buffer to its active upstream cause.

    ``magic_compute`` is the Program-visible resource, but a remote-MS
    architecture fills it through a three-stage pipeline: magic-state
    preparation, logical-Bell preparation, and teleportation/delivery.  Merely
    labeling an empty compute-side buffer as an MSF stall conflates all three.
    The realized frontier has enough information to identify which prerequisite
    currently lies on the critical path without adding resource work to the
    Program DAG.
    """

    resource_running = [
        item for item in running if item.get("plane") == "resource"
    ]
    if any(
        item.get("opcode") == "TELEPORT_QUBITS"
        and item.get("process_id") == "deliver_magic_remote"
        for item in resource_running
    ):
        return "resource_delivery_stall"

    magic_ready = _buffer_ready(architecture_state, "msf_output")
    buffers = architecture_state.get("buffers", {})
    bell_ready = sum(
        int(state.get("ready", 0))
        for name, state in buffers.items()
        if str(name).startswith("bell:") and isinstance(state, Mapping)
    ) if isinstance(buffers, Mapping) else 0

    if magic_ready > 0 and bell_ready <= 0:
        return "bell_pair_supply_stall"
    if bell_ready > 0 and magic_ready <= 0:
        return "magic_state_supply_stall"
    if magic_ready > 0 and bell_ready > 0:
        # Both inputs exist, so a shared delivery engine or its in-flight
        # reservation is the only remaining architectural prerequisite.
        return "resource_delivery_stall"

    # Both inputs are absent.  Their producers run in parallel, so the later
    # realized prerequisite is the one that gates the next delivery.
    next_magic = _next_resource_completion(
        resource_running, opcode="PREPARE_MAGIC_STATE"
    )
    next_bell = _next_resource_completion(
        resource_running, opcode="PREPARE_LOGICAL_BELL"
    )
    if math.isfinite(next_magic) and math.isfinite(next_bell):
        return (
            "bell_pair_supply_stall"
            if next_bell >= next_magic
            else "magic_state_supply_stall"
        )
    if math.isfinite(next_bell):
        return "bell_pair_supply_stall"
    if math.isfinite(next_magic):
        return "magic_state_supply_stall"
    return "architecture_resource_stall"


def _stall_group(
    waiting: list[Mapping[str, Any]],
    architecture_state: Mapping[str, Any],
    running: list[Mapping[str, Any]],
    next_program_instruction: int | None,
) -> str:
    if not waiting:
        return "dependency_or_control_idle"

    # Attribute an idle interval to the Program-ready instruction that actually
    # dispatches next in the realized execution.  This avoids a fixed priority
    # over unrelated blockers elsewhere in a wide ready frontier.
    focused = next(
        (
            item
            for item in waiting
            if item.get("instruction_id") == next_program_instruction
        ),
        waiting[0],
    )
    blockers = [
        str(blocker)
        for blocker in focused.get("resource_blockers", ())
    ]
    if any("magic_compute" in blocker for blocker in blockers):
        buffers = architecture_state.get("buffers", {})
        if isinstance(buffers, Mapping) and "msf_output" in buffers:
            return _remote_magic_stall_group(architecture_state, running)
        return "magic_state_supply_stall"
    if any("msf_output" in blocker for blocker in blockers):
        return "magic_state_supply_stall"
    if any("magic" in blocker.lower() for blocker in blockers):
        return "magic_state_supply_stall"
    if any("bell:" in blocker for blocker in blockers):
        return "bell_pair_supply_stall"
    if any("location" in blocker for blocker in blockers):
        return "data_location_stall"
    if blockers:
        return "architecture_resource_stall"
    return "dependency_or_control_idle"


def exclusive_time_breakdown(result: Any) -> Mapping[str, float]:
    """Partition wall time into mutually exclusive service or stall states."""

    totals: defaultdict[str, float] = defaultdict(float)
    log = result.discrete_time_log
    next_program_dispatch: list[int | None] = [None] * len(log)
    next_instruction: int | None = None
    for index in range(len(log) - 1, -1, -1):
        next_program_dispatch[index] = next_instruction
        dispatched = [
            item
            for item in log[index].get("dispatched", ())
            if item.get("plane") == "program"
            and item.get("instruction_id") is not None
        ]
        if dispatched:
            # ``dispatched`` is already in committed dispatch order.  An ID
            # minimum would silently undo a custom Scheduler's decision.
            next_instruction = int(dispatched[0]["instruction_id"])

    for index, (current, following) in enumerate(zip(log, log[1:])):
        delta = float(following["time_s"]) - float(current["time_s"])
        if delta <= 0:
            continue
        running_program = [
            item
            for item in current["frontier_after"]["running"]
            if item["plane"] == "program"
        ]
        groups = {_program_group(item["opcode"]) for item in running_program}
        groups.discard("control")
        if len(groups) == 1:
            category = next(iter(groups))
        elif len(groups) > 1:
            category = "overlapped_program_service"
        else:
            category = _stall_group(
                current["frontier_after"]["waiting"],
                current.get("architecture_state_after", {}),
                current["frontier_after"]["running"],
                next_program_dispatch[index],
            )
        totals[category] += delta

    classified = sum(totals.values())
    if not math.isclose(
        classified, result.total_latency_s, rel_tol=1e-10, abs_tol=1e-12
    ):
        raise AssertionError(
            "Time attribution does not conserve total latency: "
            f"{classified} != {result.total_latency_s}"
        )
    return deep_freeze_json(dict(sorted(totals.items())))


def buffer_occupancy_statistics(
    result: Any, plan: Any
) -> Mapping[str, Mapping[str, float]]:
    """Return time-weighted ready/committed occupancy and boundary fractions."""

    capacities = {item.id: int(item.capacity) for item in plan.buffers}
    accumulators: dict[str, defaultdict[str, float]] = {
        name: defaultdict(float) for name in capacities
    }
    for current, following in zip(result.discrete_time_log, result.discrete_time_log[1:]):
        delta = float(following["time_s"]) - float(current["time_s"])
        if delta <= 0:
            continue
        buffers = current.get("architecture_state_after", {}).get("buffers", {})
        for name, capacity in capacities.items():
            state = buffers.get(name, {})
            ready = int(state.get("ready", 0))
            pending = int(state.get("pending_incoming", 0))
            committed = ready + pending
            accumulator = accumulators[name]
            accumulator["ready_slot_seconds"] += ready * delta
            accumulator["committed_slot_seconds"] += committed * delta
            if ready == 0:
                accumulator["ready_empty_s"] += delta
            if committed >= capacity:
                accumulator["committed_full_s"] += delta

    latency = float(result.total_latency_s)
    statistics: dict[str, dict[str, float]] = {}
    for name, capacity in capacities.items():
        values = accumulators[name]
        denominator = capacity * latency
        statistics[name] = {
            "capacity": float(capacity),
            "mean_ready": values["ready_slot_seconds"] / latency if latency else 0.0,
            "mean_committed": (
                values["committed_slot_seconds"] / latency if latency else 0.0
            ),
            "ready_utilization": (
                values["ready_slot_seconds"] / denominator if denominator else 0.0
            ),
            "committed_utilization": (
                values["committed_slot_seconds"] / denominator
                if denominator
                else 0.0
            ),
            "ready_empty_fraction": (
                values["ready_empty_s"] / latency if latency else 0.0
            ),
            "committed_full_fraction": (
                values["committed_full_s"] / latency if latency else 0.0
            ),
        }
    return deep_freeze_json(dict(sorted(statistics.items())))


def engine_utilization(result: Any, plan: Any) -> Mapping[str, float]:
    """Compute Program-horizon utilization from committed dispatch spans.

    A Resource event may still be running when the final Program instruction
    completes.  Its dispatch reservation consumed engine capacity during the
    observed prefix even though it has no completion event, so completed-event
    accounting is insufficient.  Causal dispatch transitions make summary and
    full traces agree without extending the Program horizon.
    """

    capacities = {item.id: int(item.capacity) for item in plan.engines}
    occupied_seconds: defaultdict[str, float] = defaultdict(float)
    latency = float(result.total_latency_s)
    for transition in result.trace.transitions:
        if _enum_value(transition.kind) != "dispatch":
            continue
        start = float(transition.start_s)
        end = min(float(transition.end_s), latency)
        if not math.isfinite(start) or not math.isfinite(end) or start < 0:
            raise ValueError("Dispatch span must be finite and non-negative")
        duration = max(0.0, end - start)
        for engine, amount in transition.engines.items():
            name = str(engine)
            if name not in capacities:
                raise ValueError(f"Trace names unknown engine {name!r}")
            occupied_seconds[name] += int(amount) * duration

    utilization = {
        name: (
            occupied_seconds[name] / (capacity * latency)
            if capacity > 0 and latency > 0
            else 0.0
        )
        for name, capacity in sorted(capacities.items())
    }
    if any(value < -1e-12 or value > 1.0 + 1e-10 for value in utilization.values()):
        raise AssertionError(
            "Trace-derived engine utilization violates declared capacity"
        )
    return deep_freeze_json(utilization)


def space_breakdown(footprint: Any) -> Mapping[str, float]:
    """Group physical space from canonical structural component types."""

    if not isinstance(footprint, PhysicalFootprintEstimate):
        raise TypeError("footprint must be a PhysicalFootprintEstimate")

    def category(item: Any) -> str:
        if item.module_type == "compute":
            if item.submodule_type == "region":
                return "compute"
            if item.submodule_type == "buffer" and item.payload == "logical_qubit":
                return "store_load_buffer"
            if item.submodule_type == "buffer" and item.payload == "magic_state":
                return "magic_input_buffer"
        if item.module_type == "memory":
            if item.submodule_type == "region":
                return "memory"
            if item.submodule_type == "buffer" and item.payload == "logical_qubit":
                return "store_load_buffer"
        if item.module_type == "resource_factory":
            if item.submodule_type == "engine":
                return "magic_state_factory"
            if item.submodule_type == "buffer":
                return "magic_output_buffer"
        if item.module_type == "bell_engine":
            return "bell_pair_engine"
        if item.module_type == "bell_storage":
            return "bell_pair_buffer"
        return "other"

    totals: defaultdict[str, float] = defaultdict(float)
    for item in footprint.components:
        totals[category(item)] += float(item.physical_qubits)
    if not math.isclose(
        sum(totals.values()),
        footprint.total_physical_qubits,
        rel_tol=1e-10,
        abs_tol=1e-9,
    ):
        raise AssertionError("Space breakdown does not conserve physical footprint")
    return deep_freeze_json(dict(sorted(totals.items())))


def fidelity_breakdown(
    estimate: Any,
    specification: ArchitectureSpecification,
) -> Mapping[str, float]:
    """Group additive negative-log survival by operation and idle exposure."""

    totals: defaultdict[str, float] = defaultdict(float)
    non_clifford = {"t", "tdg", "t_pauli"}
    two_qubit = {"cx", "cnot"}
    measurements = {"m_pauli", "measure"}
    for operation, log_success in estimate.log_success_by_logical_operation.items():
        if operation in non_clifford:
            category = "non_clifford_operations"
        elif operation in two_qubit:
            category = "two_qubit_clifford"
        elif operation in measurements:
            category = "logical_measurement"
        else:
            category = "single_qubit_clifford"
        totals[category] += -float(log_success)

    if not isinstance(specification, ArchitectureSpecification):
        raise TypeError("specification must be an ArchitectureSpecification")
    module_types = {
        f"{node.id}/{module.id}": module.type
        for node in specification.nodes
        for module in node.modules
    }
    logical_buffers = {
        f"{node.id}/{module.id}/{submodule.id}"
        for node in specification.nodes
        for module in node.modules
        for submodule in module.submodules
        if submodule.type == "buffer" and submodule.payload == "logical_qubit"
    }
    for location, log_success in estimate.log_success_by_idle_location.items():
        if location in logical_buffers:
            category = "idle_store_load_buffer"
        elif module_types.get(location) == "memory":
            category = "idle_memory"
        elif module_types.get(location) == "compute":
            category = "idle_compute"
        else:
            category = "idle_other"
        totals[category] += -float(log_success)
    for opcode, log_success in estimate.log_success_by_operation.items():
        totals[f"operation_{opcode.lower()}"] += -float(log_success)
    for resource_kind, log_success in (
        estimate.log_success_by_resource_output.items()
    ):
        totals[f"resource_{resource_kind}_output"] += -float(log_success)
    for location, log_success in (
        estimate.log_success_by_resource_idle_location.items()
    ):
        totals[f"resource_idle_{location}"] += -float(log_success)

    expected = -math.log(max(estimate.success_probability, 1e-300))
    if not math.isclose(
        sum(totals.values()), expected, rel_tol=1e-10, abs_tol=1e-12
    ):
        raise AssertionError("Fidelity breakdown does not conserve log survival")
    return deep_freeze_json(dict(sorted(totals.items())))


def analyze_evaluation(
    result: Any,
    plan: Any,
    specification: ArchitectureSpecification,
    footprint: PhysicalFootprintEstimate,
    fidelity_profile: Any | None = None,
) -> tuple[EvaluationAnalysis, Any | None]:
    """Purely derive report metrics from one immutable execution record.

    This service never invokes the Event Engine, ArchitectureState, runtime
    components, or a scheduler.  ``run_evaluation`` remains the orchestration
    facade; analysis can be safely repeated over the same trace.
    """

    from .fidelity_estimator import estimate_fidelity

    if not isinstance(specification, ArchitectureSpecification):
        raise TypeError("specification must be an ArchitectureSpecification")
    if not isinstance(footprint, PhysicalFootprintEstimate):
        raise TypeError("footprint must be a PhysicalFootprintEstimate")

    fidelity = (
        estimate_fidelity(result, fidelity_profile, plan=plan)
        if fidelity_profile is not None
        else None
    )
    unavailable: dict[str, str] = {}
    if result.discrete_time_log:
        time_attribution = exclusive_time_breakdown(result)
        occupancy = buffer_occupancy_statistics(result, plan)
    else:
        time_attribution = None
        occupancy = None
        reason = (
            "The selected trace level does not retain the discrete-time log "
            "required for causal attribution."
        )
        unavailable["exclusive_time_s"] = reason
        unavailable["buffer_occupancy"] = reason

    if fidelity is None:
        fidelity_attribution = None
        unavailable["fidelity"] = "No fidelity profile was selected."
    else:
        fidelity_attribution = fidelity_breakdown(fidelity, specification)

    return (
        EvaluationAnalysis(
            exclusive_time_s=time_attribution,
            engine_utilization=engine_utilization(result, plan),
            buffer_occupancy=occupancy,
            physical_space_qubits=space_breakdown(footprint),
            fidelity_negative_log_success=fidelity_attribution,
            unavailable=unavailable,
        ),
        fidelity,
    )
