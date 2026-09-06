from __future__ import annotations

from copy import deepcopy

import pytest

from arqsim.architecture.gallery.na_c_plus_sc_f import (
    PROFILE,
    make_sizing_policy,
)
from arqsim.architecture.gallery.na_c_plus_sc_f.sizing import (
    RemoteMagicSizingPolicy,
)
from arqsim.architecture.gallery.quantile import QuantileSizingConfig
from arqsim.architecture.identifiers import SubmoduleKey
from arqsim.architecture.profile import ArchitectureProfile
from arqsim.program.statistics import CircuitStatistics
from arqsim.qec import (
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
)


COMPUTE = SubmoduleKey(
    "na_compute_node", "na_compute", "compute_region"
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


def _statistics(
    magic_states_per_layer: tuple[int, ...] = (0, 1, 2, 3, 4),
) -> CircuitStatistics:
    logical_qubits = 4
    return CircuitStatistics(
        representation="clifford_t",
        logical_qubits=logical_qubits,
        magic_states_per_layer=magic_states_per_layer,
        operation_qubits_by_layer=tuple(
            tuple((index % logical_qubits,) for index in range(count))
            for count in magic_states_per_layer
        ),
    )


def _protocols():
    return {
        FACTORY: get_magic_state_factory_profile(
            "cultivation-d5-d15-p1e3"
        ),
        BELL_ENGINE: get_entanglement_distillation_profile(
            "boosting-dbell9-ds19-pbell1e2"
        ),
    }


def _policy(probability: float = 0.60) -> RemoteMagicSizingPolicy:
    return make_sizing_policy(
        QuantileSizingConfig(
            compute_quantile=0.50,
            magic_state_quantile=probability,
            default_store_load_quantile=0.95,
        )
    )


def test_profile_13_baseline_is_an_explicit_remote_magic_policy() -> None:
    policy = _policy()

    assert isinstance(policy, RemoteMagicSizingPolicy)
    assert policy.magic_state_quantile == 0.60
    assert policy.magic_state_rounding == "ceil"
    assert policy.minimum_magic_state_capacity == 1
    assert policy.factory_qec_cycle_time_s == 1.0e-6
    with pytest.raises(TypeError, match="QuantileSizingConfig"):
        make_sizing_policy(None)  # type: ignore[arg-type]


def test_remote_magic_sizing_counts_shared_bell_resources_once() -> None:
    result = _policy().size(
        PROFILE,
        _statistics(),
        selected_qec_protocols=_protocols(),
    )

    # Linear q=.60 of [0, 1, 2, 3, 4] is 2.4 and rounds up to 3.
    assert dict(result.capacities) == {
        COMPUTE: 4,
        MAGIC_INPUT: 3,
        FACTORY: 3,
        MAGIC_OUTPUT: 3,
        BELL_ENGINE: 3,
        BELL_BUFFER: 3,
    }
    assert result.qec_protocol_dependencies == {
        FACTORY: _protocols()[FACTORY].profile_hash,
        BELL_ENGINE: _protocols()[BELL_ENGINE].profile_hash,
    }


def test_remote_magic_overrides_are_exact_target_decisions() -> None:
    result = _policy().size(
        PROFILE,
        _statistics(),
        overrides={
            COMPUTE: 4,
            MAGIC_INPUT: 5,
            MAGIC_OUTPUT: 6,
            FACTORY: 2,
            BELL_BUFFER: 7,
            BELL_ENGINE: 4,
        },
    )

    assert dict(result.capacities) == {
        COMPUTE: 4,
        MAGIC_INPUT: 5,
        FACTORY: 2,
        MAGIC_OUTPUT: 6,
        BELL_ENGINE: 4,
        BELL_BUFFER: 7,
    }
    assert result.qec_protocol_dependencies == {}

    with pytest.raises(ValueError, match="must equal.*logical-qubit count"):
        _policy().size(
            PROFILE,
            _statistics(),
            overrides={
                COMPUTE: 3,
                MAGIC_INPUT: 1,
                MAGIC_OUTPUT: 1,
                FACTORY: 1,
                BELL_BUFFER: 1,
                BELL_ENGINE: 1,
            },
        )


def test_bell_buffer_tracks_compute_side_transfer_wave() -> None:
    result = _policy().size(
        PROFILE,
        _statistics(),
        selected_qec_protocols=_protocols(),
        overrides={MAGIC_INPUT: 5, MAGIC_OUTPUT: 2},
    )

    assert result[MAGIC_INPUT] == 5
    assert result[MAGIC_OUTPUT] == 2
    assert result[BELL_BUFFER] == 5
    assert result[FACTORY] == 2


def test_remote_magic_policy_does_not_pair_targets_by_id_or_order() -> None:
    document = deepcopy(PROFILE.to_dict())
    interconnect = document["interconnects"]["compute_msf_link"]
    interconnect["endpoints"][0] = (
        "na_compute_node/na_compute/compute_region"
    )
    profile = ArchitectureProfile.from_dict(document)

    with pytest.raises(ValueError, match="attach.*magic input and output"):
        _policy().size(
            profile,
            _statistics(),
            selected_qec_protocols=_protocols(),
        )

    document = deepcopy(PROFILE.to_dict())
    connection = document["interconnects"]["compute_msf_link"][
        "connections"
    ]["engine_to_storage"]
    connection["endpoints"].reverse()
    profile = ArchitectureProfile.from_dict(document)

    with pytest.raises(ValueError, match="engine-to-storage"):
        _policy().size(
            profile,
            _statistics(),
            selected_qec_protocols=_protocols(),
        )

    document = deepcopy(PROFILE.to_dict())
    connection = document["interconnects"]["compute_msf_link"][
        "connections"
    ]["engine_to_storage"]
    document["interconnects"]["compute_msf_link"]["connections"][
        "duplicate_delivery"
    ] = deepcopy(connection)
    profile = ArchitectureProfile.from_dict(document)

    with pytest.raises(ValueError, match="exactly one.*engine-to-storage"):
        _policy().size(
            profile,
            _statistics(),
            selected_qec_protocols=_protocols(),
        )


def test_remote_magic_rate_inference_requires_both_typed_protocols() -> None:
    profile = PROFILE
    statistics = _statistics()

    with pytest.raises(ValueError, match="needs a selected QEC protocol"):
        _policy().size(profile, statistics)
    with pytest.raises(ValueError, match="produces 'bell_pair'.*'magic_state'"):
        _policy().size(
            profile,
            statistics,
            selected_qec_protocols={
                FACTORY: _protocols()[BELL_ENGINE],
                BELL_ENGINE: _protocols()[BELL_ENGINE],
            },
        )
