from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError

import pytest

from arqsim.architecture.gallery.na_c_plus_sc_f import (
    PROFILE as REMOTE_MAGIC_PROFILE,
)
from arqsim.architecture.gallery.na_cf import (
    PROFILE,
    make_sizing_policy,
)
from arqsim.architecture.gallery.na_cf.sizing import (
    NeutralAtomComputeFactorySizingPolicy,
)
from arqsim.architecture.gallery.quantile import QuantileSizingConfig
from arqsim.architecture.gallery.sc_cf import (
    PROFILE as SC_PROFILE,
    make_sizing_policy as make_sc_sizing_policy,
)
from arqsim.architecture.gallery.sc_cf.sizing import (
    SuperconductingComputeFactorySizingPolicy,
)
from arqsim.architecture.identifiers import SubmoduleKey
from arqsim.architecture.sizing import (
    SizingPolicy,
    SizingResult,
)
from arqsim.program.statistics import CircuitStatistics
from arqsim.qec import (
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
)


COMPUTE = SubmoduleKey("na_node", "na_compute", "compute_region")
MAGIC_INPUT = SubmoduleKey(
    "na_node", "na_compute", "magic_state_input_buffer"
)
FACTORY = SubmoduleKey("na_node", "na_msf", "factory_engine")
MAGIC_OUTPUT = SubmoduleKey(
    "na_node", "na_msf", "magic_state_output_buffer"
)
SC_COMPUTE = SubmoduleKey("sc_node", "sc_compute", "compute_region")
SC_MAGIC_INPUT = SubmoduleKey(
    "sc_node", "sc_compute", "magic_state_input_buffer"
)
SC_FACTORY = SubmoduleKey("sc_node", "sc_msf", "factory_engine")
SC_MAGIC_OUTPUT = SubmoduleKey(
    "sc_node", "sc_msf", "magic_state_output_buffer"
)


def _statistics(
    magic_states_per_layer: tuple[int, ...] = (0, 0, 2, 4),
    *,
    logical_qubits: int = 4,
) -> CircuitStatistics:
    return CircuitStatistics(
        representation="clifford_t",
        logical_qubits=logical_qubits,
        magic_states_per_layer=magic_states_per_layer,
        operation_qubits_by_layer=tuple(
            tuple((index % logical_qubits,) for index in range(count))
            for count in magic_states_per_layer
        ),
    )


def _quantile_policy(
    probability: float = 0.60,
) -> NeutralAtomComputeFactorySizingPolicy:
    return make_sizing_policy(
        QuantileSizingConfig(
            compute_quantile=0.50,
            magic_state_quantile=probability,
            default_store_load_quantile=0.95,
        )
    )


def _selected_qec_protocols():
    return {
        FACTORY: get_magic_state_factory_profile(
            "cultivation-d5-d15-p1e3"
        )
    }


def test_submodule_key_is_an_opaque_validated_absolute_key() -> None:
    target = SubmoduleKey("node", "module", "submodule")

    assert str(target) == "node/module/submodule"
    with pytest.raises(FrozenInstanceError):
        target.owner_id = "other"  # type: ignore[misc]
    with pytest.raises(ValueError, match="must not contain"):
        SubmoduleKey("node/encoded", "module", "submodule")


def test_sizing_result_is_immutable_and_accepts_nonnegative_plain_ints() -> None:
    source = {COMPUTE: 4}
    dependencies = {COMPUTE: "0" * 64}
    result = SizingResult(source, qec_protocol_dependencies=dependencies)
    source[COMPUTE] = 9
    dependencies[COMPUTE] = "1" * 64

    assert result[COMPUTE] == 4
    assert result.qec_protocol_dependencies[COMPUTE] == "0" * 64
    with pytest.raises(TypeError):
        result.capacities[COMPUTE] = 8  # type: ignore[index]
    with pytest.raises(ValueError, match="non-negative plain integer"):
        SizingResult({COMPUTE: True})
    assert SizingResult({COMPUTE: 0})[COMPUTE] == 0
    with pytest.raises(ValueError, match="non-negative plain integer"):
        SizingResult({COMPUTE: -1})
    with pytest.raises(ValueError, match="SHA-256"):
        SizingResult(
            {COMPUTE: 1},
            qec_protocol_dependencies={COMPUTE: "not-a-hash"},
        )


def test_quantile_policy_implements_general_sizing_interface() -> None:
    policy = _quantile_policy()

    assert isinstance(policy, SizingPolicy)
    assert isinstance(policy, NeutralAtomComputeFactorySizingPolicy)
    assert SizingPolicy in NeutralAtomComputeFactorySizingPolicy.__mro__
    with pytest.raises(TypeError, match="abstract"):
        SizingPolicy()
    with pytest.raises(TypeError, match="statistics must be CircuitStatistics"):
        policy.size(  # type: ignore[arg-type]
            PROFILE,
            object(),
            selected_qec_protocols=_selected_qec_protocols(),
        )


def test_each_compute_factory_bundle_owns_a_concrete_sizing_policy() -> None:
    config = QuantileSizingConfig(
        compute_quantile=0.50,
        magic_state_quantile=0.60,
        default_store_load_quantile=0.95,
    )
    sc_policy = make_sc_sizing_policy(config)
    protocol = get_magic_state_factory_profile(
        "cultivation-d5-d15-p1e3"
    )

    assert isinstance(sc_policy, SuperconductingComputeFactorySizingPolicy)
    assert type(sc_policy) is not type(_quantile_policy())
    result = sc_policy.size(
        SC_PROFILE,
        _statistics(),
        selected_qec_protocols={SC_FACTORY: protocol},
    )
    assert dict(result.capacities) == {
        SC_COMPUTE: 4,
        SC_MAGIC_INPUT: 2,
        SC_FACTORY: 2,
        SC_MAGIC_OUTPUT: 2,
    }
    with pytest.raises(TypeError, match="QuantileSizingConfig"):
        make_sizing_policy(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="QuantileSizingConfig"):
        make_sc_sizing_policy(None)  # type: ignore[arg-type]


def test_na_cf_policy_rejects_a_remote_multi_node_profile() -> None:
    with pytest.raises(ValueError, match="one local Node and no Interconnect"):
        _quantile_policy().size(
            REMOTE_MAGIC_PROFILE,
            _statistics(),
        )


def test_quantile_policy_sizes_profile_11_without_hidden_configuration() -> None:
    profile = PROFILE
    result = _quantile_policy().size(
        profile,
        _statistics(),
        selected_qec_protocols=_selected_qec_protocols(),
    )

    # Linear q=.60 of [0, 0, 2, 4] is 1.6 and the explicit rounding mode is ceil.
    assert dict(result.capacities) == {
        COMPUTE: 4,
        MAGIC_INPUT: 2,
        FACTORY: 2,
        MAGIC_OUTPUT: 2,
    }


def test_quantile_probability_is_a_typed_policy_field() -> None:
    profile = PROFILE
    statistics = _statistics()

    median = _quantile_policy(0.50).size(
        profile,
        statistics,
        selected_qec_protocols=_selected_qec_protocols(),
    )
    maximum = _quantile_policy(1.0).size(
        profile,
        statistics,
        selected_qec_protocols=_selected_qec_protocols(),
    )

    assert median[MAGIC_INPUT] == 1
    assert maximum[MAGIC_INPUT] == 4
    with pytest.raises(ValueError, match=r"in \[0, 1\]"):
        _quantile_policy(1.1)


def test_reference_statistics_affect_magic_demand_but_not_data_capacity() -> None:
    result = _quantile_policy(1.0).size(
        PROFILE,
        _statistics((0,), logical_qubits=4),
        reference_statistics=_statistics((3,), logical_qubits=4),
        selected_qec_protocols=_selected_qec_protocols(),
    )

    assert result[COMPUTE] == 4
    assert result[MAGIC_INPUT] == 3
    assert result[MAGIC_OUTPUT] == 3

    with pytest.raises(ValueError, match="main circuit logical-qubit count"):
        _quantile_policy(1.0).size(
            PROFILE,
            _statistics((0,), logical_qubits=4),
            reference_statistics=_statistics((3,), logical_qubits=2),
            selected_qec_protocols=_selected_qec_protocols(),
        )


def test_exact_overrides_win_per_target_and_drive_factory_provisioning() -> None:
    result = _quantile_policy().size(
        PROFILE,
        _statistics(),
        selected_qec_protocols=_selected_qec_protocols(),
        overrides={MAGIC_INPUT: 3, MAGIC_OUTPUT: 5, FACTORY: 7},
    )

    assert result[MAGIC_INPUT] == 3
    assert result[MAGIC_OUTPUT] == 5
    assert result[FACTORY] == 7
    with pytest.raises(ValueError, match="unknown Submodule"):
        _quantile_policy().size(
            PROFILE,
            _statistics(),
            selected_qec_protocols=_selected_qec_protocols(),
            overrides={SubmoduleKey("na_node", "na_compute", "missing"): 1},
        )


def test_quantile_policy_rejects_zero_compute_override() -> None:
    with pytest.raises(
        ValueError,
        match=(
            r"NeutralAtomComputeFactorySizingPolicy capacity for "
            r".*compute_region.*positive"
        ),
    ):
        _quantile_policy().size(
            PROFILE,
            _statistics(),
            selected_qec_protocols=_selected_qec_protocols(),
            overrides={COMPUTE: 0},
        )


@pytest.mark.parametrize("target", [MAGIC_INPUT, MAGIC_OUTPUT])
def test_quantile_policy_rejects_zero_magic_buffer_override(
    target: SubmoduleKey,
) -> None:
    with pytest.raises(
        ValueError,
        match=(
            r"NeutralAtomComputeFactorySizingPolicy capacity for "
            r".*magic_state.*buffer.*positive"
        ),
    ):
        _quantile_policy().size(
            PROFILE,
            _statistics(),
            selected_qec_protocols=_selected_qec_protocols(),
            overrides={target: 0},
        )


def test_quantile_policy_rejects_zero_logical_width() -> None:
    with pytest.raises(
        ValueError,
        match=r"Logical-qubit capacity for .*compute_region.*positive",
    ):
        _quantile_policy().size(
            PROFILE,
            _statistics((0,), logical_qubits=0),
            selected_qec_protocols=_selected_qec_protocols(),
        )


def test_factory_copies_come_from_selected_catalog_profile() -> None:
    profile = PROFILE

    with pytest.raises(ValueError, match="needs a selected QEC protocol profile"):
        _quantile_policy().size(profile, _statistics())

    result = _quantile_policy().size(
        profile,
        _statistics(),
        selected_qec_protocols=_selected_qec_protocols(),
        overrides={MAGIC_OUTPUT: 5},
    )
    assert result[FACTORY] == 5

    with pytest.raises(ValueError, match="is not a resource engine"):
        _quantile_policy().size(
            profile,
            _statistics(),
            selected_qec_protocols={
                MAGIC_INPUT: _selected_qec_protocols()[FACTORY]
            },
            overrides={FACTORY: 1},
        )


def test_factory_copy_inference_does_not_guess_between_multiple_buffers() -> None:
    document = deepcopy(PROFILE.to_dict())
    outputs = document["nodes"]["na_node"]["modules"]["na_msf"]["submodules"]
    outputs["backup_output"] = {
        "type": "buffer",
        "payload": "magic_state",
    }
    profile = type(PROFILE).from_dict(document)

    with pytest.raises(ValueError, match="exactly one sibling"):
        _quantile_policy().size(
            profile,
            _statistics(),
            selected_qec_protocols=_selected_qec_protocols(),
        )


def test_factory_copy_inference_does_not_guess_between_multiple_engines() -> None:
    document = deepcopy(PROFILE.to_dict())
    resources = document["nodes"]["na_node"]["modules"]["na_msf"]["submodules"]
    resources["backup_engine"] = {
        "type": "engine",
        "payload": "magic_state",
    }
    profile = type(PROFILE).from_dict(document)
    backup = SubmoduleKey("na_node", "na_msf", "backup_engine")
    protocol = _selected_qec_protocols()[FACTORY]

    with pytest.raises(ValueError, match="only sibling"):
        _quantile_policy().size(
            profile,
            _statistics(),
            selected_qec_protocols={FACTORY: protocol, backup: protocol},
        )
    with pytest.raises(ValueError, match="produces 'bell_pair', not 'magic_state'"):
        _quantile_policy().size(
            profile,
            _statistics(),
            selected_qec_protocols={
                FACTORY: get_entanglement_distillation_profile(
                    "boosting-dbell9-ds19-pbell1e2"
                )
            },
        )


def test_quantile_policy_requires_override_for_unknown_resource_semantics() -> None:
    profile = PROFILE
    target = SubmoduleKey("na_node", "na_compute", "compute_region")
    # The exact target override deliberately takes precedence over the built-in
    # semantic rule.  This is the same escape hatch a new Submodule type uses
    # until it has a dedicated SizingPolicy.
    result = _quantile_policy().size(
        profile,
        _statistics(),
        selected_qec_protocols=_selected_qec_protocols(),
        overrides={target: 12},
    )
    assert result[target] == 12
