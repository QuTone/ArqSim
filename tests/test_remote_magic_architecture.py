from __future__ import annotations

from heteqsys.architecture.construction import construct_architecture
from heteqsys.architecture.gallery.na_c_plus_sc_f import (
    PROFILE,
    make_layout_policy,
    make_sizing_policy,
)
from heteqsys.architecture.gallery.quantile import QuantileSizingConfig
from heteqsys.architecture.identifiers import SubmoduleKey
from heteqsys.architecture.specification import (
    ArchitectureSpecification,
    QECBinding,
)
from heteqsys.program.statistics import CircuitStatistics
from heteqsys.qec import (
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


def _statistics() -> CircuitStatistics:
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


def test_profile_13_constructs_one_shared_interconnect_resource_domain() -> None:
    surface = QECBinding("surface", {"distance": 13})
    protocols = {
        FACTORY: get_magic_state_factory_profile(
            "cultivation-d5-d15-p1e3"
        ),
        BELL_ENGINE: get_entanglement_distillation_profile(
            "boosting-dbell9-ds19-pbell1e2"
        ),
    }
    specification = construct_architecture(
        PROFILE,
        _statistics(),
        make_sizing_policy(
            QuantileSizingConfig(
                compute_quantile=0.50,
                magic_state_quantile=0.60,
                default_store_load_quantile=0.95,
            )
        ),
        layout_policy=make_layout_policy(),
        qec_bindings={
            COMPUTE: surface,
            MAGIC_INPUT: surface,
            MAGIC_OUTPUT: surface,
        },
        selected_qec_protocols=protocols,
    )

    assert len(specification.nodes) == 2
    assert len(specification.interconnects) == 1
    interconnect = specification.interconnect("compute_msf_link")
    assert interconnect.endpoints == (
        "na_compute_node/na_compute/magic_state_input_buffer",
        "sc_msf_node/sc_msf/magic_state_output_buffer",
    )
    modules = {module.id: module for module in interconnect.modules}
    engine = modules["bell_engine"].submodules[0]
    buffer = modules["bell_storage"].submodules[0]
    assert (engine.id, engine.capacity, engine.slots) == (
        "pair_generator",
        1,
        (),
    )
    assert engine.resource_protocol is not None
    assert engine.resource_protocol.id == protocols[BELL_ENGINE].id
    assert (buffer.id, buffer.capacity) == ("bell_buffer", 1)
    assert tuple(slot.id for slot in buffer.slots) == ("slot_0",)
    assert buffer.slots[0].coordinate is None
    # The pair is one shared resource. Endpoint-local encoded halves are a
    # downstream footprint/runtime derivation, not one fake shared QEC code.
    assert buffer.qec is None
    assert [item.to_dict() for item in interconnect.connections] == [
        {
            "id": "engine_to_storage",
            "direction": "directed",
            "endpoints": [
                "bell_engine/pair_generator",
                "bell_storage/bell_buffer",
            ],
        }
    ]

    document = specification.to_dict()
    assert ArchitectureSpecification.from_dict(document) == specification
    serialized = str(document).lower()
    assert "routing" not in serialized
    assert "coupler" not in serialized
    assert "endpoint_half" not in serialized
