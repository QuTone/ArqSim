from __future__ import annotations

from copy import deepcopy

import pytest

from arqsim.architecture.identifiers import SubmoduleKey
from arqsim.architecture.gallery import (
    QuantileSizingConfig,
    get_architecture_profile,
)
from arqsim.architecture.gallery.na_m_plus_sc_cf import (
    HybridMemoryComputeSizingPolicy,
    make_sizing_policy,
)
from arqsim.architecture.profile import ArchitectureProfile
from arqsim.architecture.sizing import SizingPolicy
from arqsim.program.statistics import CircuitStatistics
from arqsim.qec import (
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
)


MEMORY = SubmoduleKey(
    "na_memory_node", "na_memory", "memory_region"
)
MEMORY_STORE_LOAD = SubmoduleKey(
    "na_memory_node", "na_memory", "store_load_buffer"
)
COMPUTE = SubmoduleKey(
    "sc_compute_node", "sc_compute", "compute_region"
)
COMPUTE_STORE_LOAD = SubmoduleKey(
    "sc_compute_node", "sc_compute", "store_load_buffer"
)
MAGIC_INPUT = SubmoduleKey(
    "sc_compute_node", "sc_compute", "magic_state_input_buffer"
)
FACTORY = SubmoduleKey(
    "sc_compute_node", "sc_msf", "factory_engine"
)
MAGIC_OUTPUT = SubmoduleKey(
    "sc_compute_node", "sc_msf", "magic_state_output_buffer"
)
BELL_ENGINE = SubmoduleKey(
    "memory_compute_link", "bell_engine", "pair_generator"
)
BELL_BUFFER = SubmoduleKey(
    "memory_compute_link", "bell_storage", "bell_buffer"
)
REFERENCE_QUANTILES = QuantileSizingConfig(
    compute_quantile=0.50,
    magic_state_quantile=0.60,
    default_store_load_quantile=0.95,
    store_load_quantiles_by_representation={
        "clifford_t": 0.95,
        "pbc": 0.80,
    },
)


def _protocols(
    *,
    factory: SubmoduleKey = FACTORY,
    bell: SubmoduleKey = BELL_ENGINE,
):
    return {
        factory: get_magic_state_factory_profile(
            "cultivation-d5-d15-p1e3"
        ),
        bell: get_entanglement_distillation_profile(
            "boosting-dbell9-ds19-pbell1e2"
        ),
    }


def _oracle_statistics() -> CircuitStatistics:
    return CircuitStatistics(
        representation="clifford_t",
        logical_qubits=4,
        magic_states_per_layer=(0, 1, 0, 0, 1, 0),
        operation_qubits_by_layer=(
            ((0,), (1,)),
            ((0,),),
            ((0, 1),),
            ((2,), (3,)),
            ((2,),),
            ((2, 3),),
        ),
    )


def _sizing_statistics() -> CircuitStatistics:
    widths = (2, 4, 6, 8, 10)
    return CircuitStatistics(
        representation="clifford_t",
        logical_qubits=10,
        magic_states_per_layer=(0, 1, 2, 3, 4),
        operation_qubits_by_layer=tuple(
            tuple((qubit,) for qubit in range(width)) for width in widths
        ),
    )


def test_profile_22_baseline_is_an_explicit_hybrid_policy() -> None:
    policy = make_sizing_policy(REFERENCE_QUANTILES)

    assert isinstance(policy, HybridMemoryComputeSizingPolicy)
    assert HybridMemoryComputeSizingPolicy.__bases__ == (SizingPolicy,)
    assert policy.compute_quantile == 0.50
    assert policy.compute_rounding == "floor"
    assert policy.compute_fraction_limit == 0.40
    assert dict(policy.store_load_quantiles_by_representation) == {
        "clifford_t": 0.95,
        "pbc": 0.80,
    }
    assert policy.magic_state_quantile == 0.60


def test_hybrid_sizing_materializes_one_shared_bell_domain() -> None:
    result = make_sizing_policy(REFERENCE_QUANTILES).size(
        get_architecture_profile("2.2"),
        _oracle_statistics(),
        selected_qec_protocols=_protocols(),
    )

    assert dict(result.capacities) == {
        MEMORY: 2,
        MEMORY_STORE_LOAD: 2,
        COMPUTE: 2,
        COMPUTE_STORE_LOAD: 2,
        MAGIC_INPUT: 1,
        FACTORY: 1,
        MAGIC_OUTPUT: 1,
        BELL_ENGINE: 2,
        BELL_BUFFER: 2,
    }
    assert result[BELL_ENGINE] == 2  # canonical engine capacity is copies
    assert result.qec_protocol_dependencies == {
        FACTORY: _protocols()[FACTORY].profile_hash,
        BELL_ENGINE: _protocols()[BELL_ENGINE].profile_hash,
    }


def test_partition_and_endpoint_overrides_are_relational_inputs() -> None:
    policy = make_sizing_policy(REFERENCE_QUANTILES)
    profile = get_architecture_profile("2.2")
    statistics = _sizing_statistics()
    protocols = _protocols()

    compute_pinned = policy.size(
        profile,
        statistics,
        selected_qec_protocols=protocols,
        overrides={COMPUTE: 2, MEMORY_STORE_LOAD: 1},
    )
    memory_pinned = policy.size(
        profile,
        statistics,
        selected_qec_protocols=protocols,
        overrides={MEMORY: 8, COMPUTE_STORE_LOAD: 1},
    )
    for result in (compute_pinned, memory_pinned):
        assert result[COMPUTE] == 2
        assert result[MEMORY] == 8
        assert result[MEMORY_STORE_LOAD] == 1
        assert result[COMPUTE_STORE_LOAD] == 1
        assert result[BELL_BUFFER] == 1

    with pytest.raises(ValueError, match="must be equal"):
        policy.size(
            profile,
            statistics,
            selected_qec_protocols=protocols,
            overrides={MEMORY_STORE_LOAD: 1, COMPUTE_STORE_LOAD: 2},
        )
    with pytest.raises(ValueError, match="must sum"):
        policy.size(
            profile,
            statistics,
            selected_qec_protocols=protocols,
            overrides={MEMORY: 8, COMPUTE: 3},
        )


def test_bell_buffer_and_engine_overrides_remain_separate_decisions() -> None:
    policy = make_sizing_policy(REFERENCE_QUANTILES)
    profile = get_architecture_profile("2.2")
    statistics = _oracle_statistics()

    enlarged_wave = policy.size(
        profile,
        statistics,
        selected_qec_protocols=_protocols(),
        overrides={BELL_BUFFER: 4},
    )
    assert enlarged_wave[BELL_BUFFER] == 4
    # Three protocol copies saturate the selected reference link; a fourth
    # same-wave copy cannot increase useful throughput.
    assert enlarged_wave[BELL_ENGINE] == 3

    pinned = policy.size(
        profile,
        statistics,
        overrides={
            FACTORY: 2,
            BELL_BUFFER: 7,
            BELL_ENGINE: 5,
        },
    )
    assert pinned[FACTORY] == 2
    assert pinned[BELL_BUFFER] == 7
    assert pinned[BELL_ENGINE] == 5
    assert pinned.qec_protocol_dependencies == {}


def test_hybrid_policy_selects_semantics_without_ids_or_order() -> None:
    document = deepcopy(get_architecture_profile("2.2").to_dict())
    document["id"] = "renamed_hybrid"

    memory_node = document["nodes"].pop("na_memory_node")
    compute_node = document["nodes"].pop("sc_compute_node")
    document["nodes"] = {"processor_owner": compute_node, "vault_owner": memory_node}

    memory = memory_node["modules"].pop("na_memory")
    memory_node["modules"]["vault"] = memory
    memory["submodules"] = {
        "transfer": memory["submodules"]["store_load_buffer"],
        "cells": memory["submodules"]["memory_region"],
    }

    compute = compute_node["modules"].pop("sc_compute")
    factory = compute_node["modules"].pop("sc_msf")
    compute_node["modules"] = {"source": factory, "processor": compute}
    compute["submodules"] = {
        "magic_in": compute["submodules"]["magic_state_input_buffer"],
        "transfer": compute["submodules"]["store_load_buffer"],
        "work": compute["submodules"]["compute_region"],
    }
    factory["submodules"] = {
        "magic_out": factory["submodules"]["magic_state_output_buffer"],
        "producer": factory["submodules"]["factory_engine"],
    }
    compute_node["connections"] = {
        "delivery": {
            "direction": "directed",
            "endpoints": ["source/magic_out", "processor/magic_in"],
        }
    }

    interconnect = document["interconnects"].pop("memory_compute_link")
    document["interconnects"]["fabric"] = interconnect
    interconnect["endpoints"] = [
        "processor_owner/processor/transfer",
        "vault_owner/vault/transfer",
    ]
    engine = interconnect["modules"].pop("bell_engine")
    storage = interconnect["modules"].pop("bell_storage")
    interconnect["modules"] = {"queue": storage, "pair_source": engine}
    engine["submodules"] = {
        "producer": engine["submodules"]["pair_generator"]
    }
    storage["submodules"] = {"pairs": storage["submodules"]["bell_buffer"]}
    interconnect["connections"] = {
        "delivery": {
            "direction": "directed",
            "endpoints": ["pair_source/producer", "queue/pairs"],
        }
    }
    profile = ArchitectureProfile.from_dict(document)
    factory_target = SubmoduleKey("processor_owner", "source", "producer")
    bell_target = SubmoduleKey("fabric", "pair_source", "producer")

    result = make_sizing_policy(REFERENCE_QUANTILES).size(
        profile,
        _oracle_statistics(),
        selected_qec_protocols=_protocols(
            factory=factory_target,
            bell=bell_target,
        ),
    )

    assert result[SubmoduleKey("vault_owner", "vault", "cells")] == 2
    assert result[SubmoduleKey("processor_owner", "processor", "work")] == 2
    assert result[SubmoduleKey("vault_owner", "vault", "transfer")] == 2
    assert result[SubmoduleKey("processor_owner", "processor", "transfer")] == 2
    assert result[bell_target] == 2


@pytest.mark.parametrize(
    "malformation",
    ("split_memory", "reverse_bell", "duplicate_bell", "duplicate_magic"),
)
def test_hybrid_policy_fails_closed_on_semantic_topology(
    malformation: str,
) -> None:
    document = deepcopy(get_architecture_profile("2.2").to_dict())
    if malformation == "split_memory":
        memory = document["nodes"]["na_memory_node"]["modules"]["na_memory"]
        endpoint = memory["submodules"].pop("store_load_buffer")
        document["nodes"]["na_memory_node"]["modules"]["endpoint"] = {
            "type": "memory",
            "submodules": {"store_load_buffer": endpoint},
        }
        document["interconnects"]["memory_compute_link"]["endpoints"] = [
            "na_memory_node/endpoint/store_load_buffer",
            "sc_compute_node/sc_compute/store_load_buffer",
        ]
    elif malformation == "reverse_bell":
        document["interconnects"]["memory_compute_link"]["connections"][
            "engine_to_storage"
        ]["endpoints"].reverse()
    elif malformation == "duplicate_bell":
        connection = document["interconnects"]["memory_compute_link"][
            "connections"
        ]["engine_to_storage"]
        document["interconnects"]["memory_compute_link"]["connections"][
            "duplicate_delivery"
        ] = deepcopy(connection)
    else:
        connection = document["nodes"]["sc_compute_node"]["connections"][
            "sc_magic_bus"
        ]
        document["nodes"]["sc_compute_node"]["connections"][
            "duplicate_magic_bus"
        ] = deepcopy(connection)
    profile = ArchitectureProfile.from_dict(document)

    with pytest.raises(
        ValueError,
        match=(
            "one Module"
            if malformation == "split_memory"
            else "magic-state connection"
            if malformation == "duplicate_magic"
            else "engine-to-buffer"
        ),
    ):
        make_sizing_policy(REFERENCE_QUANTILES).size(
            profile,
            _oracle_statistics(),
            selected_qec_protocols=_protocols(),
        )
