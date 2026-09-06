"""Circuit partition helpers over canonical typed statistics."""

from __future__ import annotations

from .statistics import CircuitStatistics


def partition_active_sets(
    statistics: CircuitStatistics,
    compute_capacity: int,
) -> tuple[frozenset[int], ...]:
    """Greedily partition logical layers without splitting an operation.

    Operations retain source order.  A new partition starts only when adding
    the next operation would exceed the compute-region capacity.
    """

    if compute_capacity < 1:
        raise ValueError("compute_capacity must be positive")
    result: list[frozenset[int]] = []
    if not isinstance(statistics, CircuitStatistics):
        raise TypeError("statistics must be a CircuitStatistics")
    for operations in statistics.operation_qubits_by_layer:
        current: set[int] = set()
        for operation_qubits in operations:
            qubits = set(operation_qubits)
            if len(qubits) > compute_capacity:
                raise ValueError(
                    "Compute capacity cannot hold one logical operation: "
                    f"width={len(qubits)}, capacity={compute_capacity}"
                )
            if current and len(current | qubits) > compute_capacity:
                result.append(frozenset(current))
                current = set()
            current.update(qubits)
        if current:
            result.append(frozenset(current))
    return tuple(result)


def store_load_transition_demands(
    statistics: CircuitStatistics,
    compute_capacity: int,
    *,
    cold_start: bool = True,
) -> dict[str, list[int]]:
    """Measure Store/Load deltas between successive compute partitions."""

    partitions = partition_active_sets(statistics, compute_capacity)
    previous: frozenset[int] = frozenset() if cold_start else (
        partitions[0] if partitions else frozenset()
    )
    stores: list[int] = []
    loads: list[int] = []
    exchanges: list[int] = []
    for active in partitions:
        store = len(previous - active)
        load = len(active - previous)
        stores.append(store)
        loads.append(load)
        exchanges.append(max(store, load))
        previous = active
    return {
        "stores": stores,
        "loads": loads,
        "exchanges": exchanges,
        "partition_count": [len(partitions)],
    }
