from __future__ import annotations

from copy import deepcopy

import pytest

from heteqsys.architecture.identifiers import SubmoduleKey
from heteqsys.architecture.gallery import (
    QuantileSizingConfig,
    get_architecture_profile,
)
from heteqsys.architecture.gallery.na_mc_plus_sc_f import (
    RemoteMagicMemoryComputeSizingPolicy,
    make_sizing_policy,
)
from heteqsys.architecture.profile import ArchitectureProfile
from heteqsys.program.statistics import CircuitStatistics
from heteqsys.qec import (
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
)


MEMORY = SubmoduleKey(
    "na_compute_node", "na_memory", "memory_region"
)
COMPUTE = SubmoduleKey(
    "na_compute_node", "na_compute", "compute_region"
)
STORE_LOAD = SubmoduleKey(
    "na_compute_node", "na_compute", "store_load_buffer"
)
MAGIC_INPUT = SubmoduleKey(
    "na_compute_node", "na_compute", "magic_state_input_buffer"
)
FACTORY = SubmoduleKey("sc_msf_node", "sc_msf", "factory_engine")
MAGIC_OUTPUT = SubmoduleKey(
    "sc_msf_node", "sc_msf", "magic_state_output_buffer"
)
BELL_ENGINE = SubmoduleKey(
    "compute_msf_link", "bell_engine", "pair_generator"
)
BELL_BUFFER = SubmoduleKey(
    "compute_msf_link", "bell_storage", "bell_buffer"
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


def _statistics() -> CircuitStatistics:
    return CircuitStatistics(
        representation="clifford_t",
        logical_qubits=10,
        magic_states_per_layer=(0, 1, 2, 3, 4),
        operation_qubits_by_layer=tuple(
            tuple((qubit,) for qubit in range(width))
            for width in (2, 4, 6, 8, 10)
        ),
    )


def _protocols(
    *,
    factory: SubmoduleKey = FACTORY,
    bell_engine: SubmoduleKey = BELL_ENGINE,
):
    return {
        factory: get_magic_state_factory_profile(
            "cultivation-d5-d15-p1e3"
        ),
        bell_engine: get_entanglement_distillation_profile(
            "boosting-dbell9-ds19-pbell1e2"
        ),
    }


def test_profile_23_has_an_explicit_combined_sizing_policy() -> None:
    policy = make_sizing_policy(REFERENCE_QUANTILES)

    assert isinstance(policy, RemoteMagicMemoryComputeSizingPolicy)
    assert policy.compute_quantile == 0.50
    assert policy.compute_rounding == "floor"
    assert policy.compute_fraction_limit == 0.40
    assert dict(policy.store_load_quantiles_by_representation) == {
        "clifford_t": 0.95,
        "pbc": 0.80,
    }
    assert policy.magic_state_quantile == 0.60
    assert policy.factory_qec_cycle_time_s == 1.0e-6


def test_profile_23_sizes_partition_remote_magic_and_shared_bell_domain() -> None:
    protocols = _protocols()
    result = make_sizing_policy(REFERENCE_QUANTILES).size(
        get_architecture_profile("2.3"),
        _statistics(),
        selected_qec_protocols=protocols,
    )

    assert dict(result.capacities) == {
        MEMORY: 6,
        COMPUTE: 4,
        STORE_LOAD: 4,
        MAGIC_INPUT: 3,
        FACTORY: 3,
        MAGIC_OUTPUT: 3,
        BELL_ENGINE: 3,
        BELL_BUFFER: 3,
    }
    # Engine capacity is a copy count, not a protocol physical/logical footprint.
    assert result[BELL_ENGINE] == 3
    assert result.qec_protocol_dependencies == {
        FACTORY: protocols[FACTORY].profile_hash,
        BELL_ENGINE: protocols[BELL_ENGINE].profile_hash,
    }


def test_bell_transfer_wave_follows_magic_input_instead_of_store_load() -> None:
    result = make_sizing_policy(REFERENCE_QUANTILES).size(
        get_architecture_profile("2.3"),
        _statistics(),
        selected_qec_protocols=_protocols(),
        overrides={
            STORE_LOAD: 1,
            MAGIC_INPUT: 5,
            MAGIC_OUTPUT: 2,
        },
    )

    assert result[STORE_LOAD] == 1
    assert result[MAGIC_INPUT] == 5
    assert result[MAGIC_OUTPUT] == 2
    assert result[BELL_BUFFER] == 5
    assert result[FACTORY] == 2
    assert result[BELL_ENGINE] == 3


def test_partition_and_engine_overrides_remain_exact_decisions() -> None:
    result = make_sizing_policy(REFERENCE_QUANTILES).size(
        get_architecture_profile("2.3"),
        _statistics(),
        overrides={
            MEMORY: 8,
            STORE_LOAD: 2,
            FACTORY: 7,
            BELL_ENGINE: 4,
        },
    )

    assert result[MEMORY] == 8
    assert result[COMPUTE] == 2
    assert result[STORE_LOAD] == 2
    assert result[FACTORY] == 7
    assert result[BELL_ENGINE] == 4
    assert result.qec_protocol_dependencies == {}

    with pytest.raises(ValueError, match="must sum"):
        make_sizing_policy(REFERENCE_QUANTILES).size(
            get_architecture_profile("2.3"),
            _statistics(),
            overrides={
                MEMORY: 8,
                COMPUTE: 3,
                FACTORY: 1,
                BELL_ENGINE: 1,
            },
        )


def test_single_qubit_profile_has_an_empty_memory_partition() -> None:
    statistics = CircuitStatistics(
        representation="clifford_t",
        logical_qubits=1,
        magic_states_per_layer=(1,),
        operation_qubits_by_layer=(((0,),),),
    )
    result = make_sizing_policy(REFERENCE_QUANTILES).size(
        get_architecture_profile("2.3"),
        statistics,
        selected_qec_protocols=_protocols(),
    )

    assert result[MEMORY] == 0
    assert result[COMPUTE] == 1
    assert result[STORE_LOAD] == 1
    assert result[MAGIC_INPUT] == 1
    assert result[BELL_BUFFER] == 1


def test_policy_matches_semantics_and_topology_instead_of_ids_or_order() -> None:
    document = deepcopy(get_architecture_profile("2.3").to_dict())
    document["id"] = "renamed-profile"

    local = document["nodes"].pop("na_compute_node")
    remote = document["nodes"].pop("sc_msf_node")
    document["nodes"] = {"remote-owner": remote, "local-owner": local}

    memory = local["modules"].pop("na_memory")
    compute = local["modules"].pop("na_compute")
    local["modules"] = {"processor": compute, "vault": memory}
    memory["submodules"] = {
        "stored-data": memory["submodules"].pop("memory_region")
    }
    compute["submodules"] = {
        "magic-in": compute["submodules"].pop("magic_state_input_buffer"),
        "exchange": compute["submodules"].pop("store_load_buffer"),
        "work": compute["submodules"].pop("compute_region"),
    }
    local["connections"]["na_memory_compute_bus"]["endpoints"] = [
        "vault/stored-data",
        "processor/exchange",
    ]

    source = remote["modules"].pop("sc_msf")
    remote["modules"] = {"source": source}
    source["submodules"] = {
        "magic-out": source["submodules"].pop("magic_state_output_buffer"),
        "producer": source["submodules"].pop("factory_engine"),
    }

    link = document["interconnects"].pop("compute_msf_link")
    document["interconnects"] = {"shared-domain": link}
    link["endpoints"] = [
        "local-owner/processor/magic-in",
        "remote-owner/source/magic-out",
    ]
    generator = link["modules"].pop("bell_engine")
    storage = link["modules"].pop("bell_storage")
    link["modules"] = {"pair-store": storage, "pair-source": generator}
    generator["submodules"] = {
        "produce": generator["submodules"].pop("pair_generator")
    }
    storage["submodules"] = {
        "hold": storage["submodules"].pop("bell_buffer")
    }
    link["connections"]["engine_to_storage"]["endpoints"] = [
        "pair-source/produce",
        "pair-store/hold",
    ]

    profile = ArchitectureProfile.from_dict(document)
    factory = SubmoduleKey("remote-owner", "source", "producer")
    bell_engine = SubmoduleKey("shared-domain", "pair-source", "produce")
    result = make_sizing_policy(REFERENCE_QUANTILES).size(
        profile,
        _statistics(),
        selected_qec_protocols=_protocols(
            factory=factory,
            bell_engine=bell_engine,
        ),
    )

    assert result[SubmoduleKey("local-owner", "vault", "stored-data")] == 6
    assert result[SubmoduleKey("local-owner", "processor", "work")] == 4
    assert result[SubmoduleKey("shared-domain", "pair-store", "hold")] == 3


@pytest.mark.parametrize("broken_topology", ("memory", "bell"))
def test_policy_fails_closed_on_required_local_topology(
    broken_topology: str,
) -> None:
    document = deepcopy(get_architecture_profile("2.3").to_dict())
    if broken_topology == "memory":
        document["nodes"]["na_compute_node"]["connections"][
            "na_memory_compute_bus"
        ]["endpoints"] = [
            "na_memory/memory_region",
            "na_compute/compute_region",
        ]
        message = "memory-region/Store-Load"
    else:
        document["interconnects"]["compute_msf_link"]["connections"][
            "engine_to_storage"
        ]["direction"] = "bidirectional"
        message = "Bell-engine-to-storage"
    profile = ArchitectureProfile.from_dict(document)

    with pytest.raises(ValueError, match=message):
        make_sizing_policy(REFERENCE_QUANTILES).size(
            profile,
            _statistics(),
            selected_qec_protocols=_protocols(),
        )
