from __future__ import annotations

from copy import deepcopy

import pytest

from arqsim.architecture.identifiers import SubmoduleKey
from arqsim.architecture.gallery import (
    QuantileSizingConfig,
    get_architecture_profile,
)
from arqsim.architecture.gallery.na_mcf import (
    MemoryComputeSizingPolicy,
    make_sizing_policy,
)
from arqsim.architecture.profile import ArchitectureProfile
from arqsim.program.statistics import CircuitStatistics
from arqsim.qec import get_magic_state_factory_profile


MEMORY = SubmoduleKey("na_node", "na_memory", "memory_region")
COMPUTE = SubmoduleKey("na_node", "na_compute", "compute_region")
STORE_LOAD = SubmoduleKey("na_node", "na_compute", "store_load_buffer")
MAGIC_INPUT = SubmoduleKey(
    "na_node",
    "na_compute",
    "magic_state_input_buffer",
)
FACTORY = SubmoduleKey("na_node", "na_msf", "factory_engine")
MAGIC_OUTPUT = SubmoduleKey(
    "na_node",
    "na_msf",
    "magic_state_output_buffer",
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


def _protocol(protocol_id: str = "cultivation-d5-d15-p1e3"):
    return {FACTORY: get_magic_state_factory_profile(protocol_id)}


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


def _sizing_statistics(
    *,
    representation: str = "clifford_t",
) -> CircuitStatistics:
    widths = (2, 4, 6, 8, 10)
    return CircuitStatistics(
        representation=representation,
        logical_qubits=10,
        magic_states_per_layer=(0, 1, 2, 3, 4),
        operation_qubits_by_layer=tuple(
            tuple((qubit,) for qubit in range(width)) for width in widths
        ),
    )


def _store_load_quantile_statistics(representation: str) -> CircuitStatistics:
    return CircuitStatistics(
        representation=representation,
        logical_qubits=5,
        magic_states_per_layer=(0, 0, 0, 0, 0, 0),
        operation_qubits_by_layer=(
            ((0,),),
            ((1,),),
            ((2,),),
            ((3,),),
            ((4,),),
            ((0, 1, 2, 3),),
        ),
    )


def test_profile_21_baseline_is_an_explicit_memory_compute_policy() -> None:
    policy = make_sizing_policy(REFERENCE_QUANTILES)

    assert isinstance(policy, MemoryComputeSizingPolicy)
    assert policy.compute_quantile == 0.50
    assert policy.compute_rounding == "floor"
    assert policy.compute_fraction_limit == 0.40
    assert dict(policy.store_load_quantiles_by_representation) == {
        "clifford_t": 0.95,
        "pbc": 0.80,
    }
    assert policy.magic_state_quantile == 0.60


def test_profile_21_baseline_reproduces_relational_capacity_decisions() -> None:
    policy = make_sizing_policy(REFERENCE_QUANTILES)
    profile = get_architecture_profile("2.1")

    oracle = policy.size(
        profile,
        _oracle_statistics(),
        selected_qec_protocols=_protocol(),
    )
    sizing_fixture = policy.size(
        profile,
        _sizing_statistics(),
        selected_qec_protocols=_protocol(),
    )

    assert dict(oracle.capacities) == {
        MEMORY: 2,
        COMPUTE: 2,
        STORE_LOAD: 2,
        MAGIC_INPUT: 1,
        FACTORY: 1,
        MAGIC_OUTPUT: 1,
    }
    assert dict(sizing_fixture.capacities) == {
        MEMORY: 6,
        COMPUTE: 4,
        STORE_LOAD: 4,
        MAGIC_INPUT: 3,
        FACTORY: 3,
        MAGIC_OUTPUT: 3,
    }


def test_store_load_quantile_is_selected_by_circuit_representation() -> None:
    policy = make_sizing_policy(REFERENCE_QUANTILES)
    profile = get_architecture_profile("2.1")
    overrides = {COMPUTE: 4}

    clifford_t = policy.size(
        profile,
        _store_load_quantile_statistics("clifford_t"),
        selected_qec_protocols=_protocol(),
        overrides=overrides,
    )
    pbc = policy.size(
        profile,
        _store_load_quantile_statistics("pbc"),
        selected_qec_protocols=_protocol(),
        overrides=overrides,
    )

    # Exchange widths are [1, 1, 1, 1, 1, 4]. Linear q=.80 is 1,
    # while q=.95 rounds up from 3.25 to 4.
    assert clifford_t[STORE_LOAD] == 4
    assert pbc[STORE_LOAD] == 1


def test_compute_and_memory_overrides_are_pinned_partition_inputs() -> None:
    policy = make_sizing_policy(REFERENCE_QUANTILES)
    profile = get_architecture_profile("2.1")
    statistics = _sizing_statistics()
    protocols = _protocol()

    compute_pinned = policy.size(
        profile,
        statistics,
        selected_qec_protocols=protocols,
        overrides={COMPUTE: 2},
    )
    memory_pinned = policy.size(
        profile,
        statistics,
        selected_qec_protocols=protocols,
        overrides={MEMORY: 8},
    )

    for result in (compute_pinned, memory_pinned):
        assert result[COMPUTE] == 2
        assert result[MEMORY] == 8
        assert result[STORE_LOAD] == 2

    with pytest.raises(ValueError, match="must sum"):
        policy.size(
            profile,
            statistics,
            selected_qec_protocols=protocols,
            overrides={COMPUTE: 3, MEMORY: 8},
        )
    with pytest.raises(ValueError, match="cannot exceed.*logical-qubit count"):
        policy.size(
            profile,
            statistics,
            selected_qec_protocols=protocols,
            overrides={COMPUTE: 11},
        )
    with pytest.raises(ValueError, match="cannot exceed compute capacity"):
        policy.size(
            profile,
            statistics,
            selected_qec_protocols=protocols,
            overrides={COMPUTE: 2, STORE_LOAD: 3},
        )
    with pytest.raises(ValueError, match="positive compute capacity"):
        policy.size(
            profile,
            statistics,
            selected_qec_protocols=protocols,
            overrides={MEMORY: statistics.logical_qubits},
        )


def test_empty_memory_is_an_exact_valid_partition_result() -> None:
    statistics = CircuitStatistics(
        representation="clifford_t",
        logical_qubits=1,
        magic_states_per_layer=(1,),
        operation_qubits_by_layer=(((0,),),),
    )
    result = make_sizing_policy(REFERENCE_QUANTILES).size(
        get_architecture_profile("2.1"),
        statistics,
        selected_qec_protocols=_protocol(),
    )

    assert result[COMPUTE] == 1
    assert result[MEMORY] == 0
    assert result[STORE_LOAD] == 1


def test_reference_statistics_change_only_magic_resource_sizing() -> None:
    reference = CircuitStatistics(
        representation="pbc",
        logical_qubits=10,
        magic_states_per_layer=(5, 5),
        operation_qubits_by_layer=(
            tuple((qubit,) for qubit in range(5)),
            tuple((qubit,) for qubit in range(5)),
        ),
    )
    result = make_sizing_policy(REFERENCE_QUANTILES).size(
        get_architecture_profile("2.1"),
        _sizing_statistics(),
        reference_statistics=reference,
        selected_qec_protocols=_protocol(),
    )

    assert result[COMPUTE] == 4
    assert result[MEMORY] == 6
    assert result[STORE_LOAD] == 4
    assert result[MAGIC_INPUT] == 5
    assert result[MAGIC_OUTPUT] == 5
    assert result[FACTORY] == 5


def test_factory_dependency_tracks_only_protocol_influenced_inference() -> None:
    protocol_id = "litinski-15to1x20to4-13-5-5-23-11-13-p1e3"
    protocols = _protocol(protocol_id)
    policy = make_sizing_policy(REFERENCE_QUANTILES)
    profile = get_architecture_profile("2.1")
    statistics = _sizing_statistics()

    inferred = policy.size(
        profile,
        statistics,
        selected_qec_protocols=protocols,
        overrides={MAGIC_OUTPUT: 5},
    )
    pinned = policy.size(
        profile,
        statistics,
        selected_qec_protocols=protocols,
        overrides={MAGIC_OUTPUT: 5, FACTORY: 7},
    )

    assert inferred[FACTORY] == 2
    assert inferred.qec_protocol_dependencies == {
        FACTORY: protocols[FACTORY].profile_hash
    }
    assert pinned[FACTORY] == 7
    assert pinned.qec_protocol_dependencies == {}


def test_policy_selects_semantics_without_inspecting_ids() -> None:
    document = deepcopy(get_architecture_profile("2.1").to_dict())
    document["id"] = "renamed_profile"
    node = document["nodes"].pop("na_node")
    document["nodes"]["owner"] = node
    modules = node["modules"]
    compute = modules.pop("na_compute")
    memory = modules.pop("na_memory")
    factory = modules.pop("na_msf")
    modules.update({"processor": compute, "vault": memory, "source": factory})
    compute["submodules"] = {
        "work": compute["submodules"]["compute_region"],
        "exchange": compute["submodules"]["store_load_buffer"],
        "resource_input": compute["submodules"]["magic_state_input_buffer"],
    }
    memory["submodules"] = {
        "storage": memory["submodules"]["memory_region"],
    }
    factory["submodules"] = {
        "producer": factory["submodules"]["factory_engine"],
        "resource_output": factory["submodules"]["magic_state_output_buffer"],
    }
    node["connections"] = {
        "memory_bus": {
            "direction": "bidirectional",
            "endpoints": ["processor/exchange", "vault/storage"],
        },
        "resource_bus": {
            "direction": "directed",
            "endpoints": ["source/resource_output", "processor/resource_input"],
        },
    }
    profile = ArchitectureProfile.from_dict(document)
    factory_key = SubmoduleKey("owner", "source", "producer")
    protocol = get_magic_state_factory_profile("cultivation-d5-d15-p1e3")

    result = make_sizing_policy(REFERENCE_QUANTILES).size(
        profile,
        _oracle_statistics(),
        selected_qec_protocols={factory_key: protocol},
    )

    assert result[SubmoduleKey("owner", "vault", "storage")] == 2
    assert result[SubmoduleKey("owner", "processor", "work")] == 2
    assert result[SubmoduleKey("owner", "processor", "exchange")] == 2


def test_policy_rejects_ambiguous_memory_pool_instead_of_pairing_by_order() -> None:
    document = deepcopy(get_architecture_profile("2.1").to_dict())
    document["nodes"]["na_node"]["modules"]["na_memory"]["submodules"][
        "second_region"
    ] = {"type": "region", "payload": "logical_qubit"}
    profile = ArchitectureProfile.from_dict(document)

    with pytest.raises(ValueError, match="exactly one logical-qubit memory region"):
        make_sizing_policy(REFERENCE_QUANTILES).size(
            profile,
            _oracle_statistics(),
            selected_qec_protocols=_protocol(),
        )
