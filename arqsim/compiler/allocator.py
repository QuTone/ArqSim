"""Compute-residency allocation policies.

Allocation is distinct from routing: it chooses which logical data slot each
resident qubit occupies.  Routing consumes that allocation afterwards.

Only one residency policy is currently implemented, so this module exposes the
policy as a pure function rather than promising a replaceable interface.
"""

from __future__ import annotations

from typing import Iterable, Mapping


def allocate_residency_first_fit(
    resident_qubits: Iterable[int],
    ordered_data_slots: Iterable[str],
    current_mapping: Mapping[int, str],
) -> dict[int, str]:
    """Keep resident placements and put incoming qubits in the first free slots.

    Qubits and slots are both handled deterministically.  The caller supplies
    slots in architecture order (currently row-major); incoming qubits are
    assigned in increasing logical-qubit order.
    """

    resident = tuple(sorted(set(int(qubit) for qubit in resident_qubits)))
    slots = tuple(str(slot) for slot in ordered_data_slots)
    if len(set(slots)) != len(slots):
        raise ValueError("Compute allocator received duplicate data slots")
    if len(resident) > len(slots):
        raise ValueError(
            f"Compute allocation needs {len(resident)} slots, but only "
            f"{len(slots)} are available"
        )

    slot_set = set(slots)
    retained = {
        qubit: str(current_mapping[qubit])
        for qubit in resident
        if qubit in current_mapping and str(current_mapping[qubit]) in slot_set
    }
    if len(set(retained.values())) != len(retained):
        raise ValueError("Current compute mapping assigns multiple qubits to one slot")

    free_slots = iter(slot for slot in slots if slot not in set(retained.values()))
    result = dict(retained)
    for qubit in resident:
        if qubit not in result:
            result[qubit] = next(free_slots)
    return result
