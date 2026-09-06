"""Independent acceptance gates for the canonical architecture model.

These tests protect the ground-up contract rather than report-v1 or removed
construction shapes.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest

from arqsim.architecture.gallery.na_cf import (
    make_layout_policy,
    make_sizing_policy,
)
from arqsim.architecture.gallery import get_architecture_profile
from arqsim.architecture.gallery.quantile import QuantileSizingConfig
from arqsim.architecture.identifiers import SubmoduleKey
from arqsim.architecture.profile import ArchitectureProfile
from arqsim.architecture.resolver import resolve_architecture
from arqsim.architecture.specification import (
    ArchitectureSpecification,
    Interconnect,
    LocalConnection,
    LogicalSlot,
    Module,
    Node,
    QECBinding,
    QECResourceProtocolRef,
    Submodule,
)
from arqsim.qec import get_magic_state_factory_profile
from arqsim.specification import build_architecture_specification
from tests.behavior_baseline_support import assert_semantic_subset
from tests.architecture_semantic_oracle import (
    oracle_circuit,
)


PROJECT_ROOT = Path(__file__).parents[1]
CORE_PATH = PROJECT_ROOT / "arqsim" / "architecture" / "specification.py"
RESOLVER_PATH = PROJECT_ROOT / "arqsim" / "architecture" / "resolver.py"
ORACLE_FIXTURE = (
    PROJECT_ROOT / "tests" / "fixtures" / "architecture_semantic_oracles" / "profile-1.1.json"
)
PROTOCOL_HASH = "4" * 64


def _generic_resolution(
    profile: ArchitectureProfile,
) -> ArchitectureSpecification:
    circuit = oracle_circuit()
    protocol = get_magic_state_factory_profile("cultivation-d5-d15-p1e3")
    selected_qec_protocols = {
        SubmoduleKey(node.id, module.id, submodule.id): protocol
        for node in profile.nodes
        for module in node.modules
        for submodule in module.submodules
        if (submodule.type, submodule.payload) == ("engine", "magic_state")
    }
    qec_bindings = {
        SubmoduleKey(node.id, module.id, submodule.id): QECBinding(
            code="surface",
            parameters={"distance": 13},
        )
        for node in profile.nodes
        for module in node.modules
        for submodule in module.submodules
        if submodule.type != "engine"
    }
    sizing = make_sizing_policy(
        QuantileSizingConfig(
            compute_quantile=0.50,
            magic_state_quantile=0.60,
            default_store_load_quantile=0.95,
        )
    ).size(
        profile,
        circuit.statistics,
        selected_qec_protocols=selected_qec_protocols,
    )
    layout = make_layout_policy().place(profile, sizing)
    return resolve_architecture(
        profile,
        sizing,
        layout,
        qec_bindings=qec_bindings,
        selected_qec_protocols=selected_qec_protocols,
    )


def _minimal_specification() -> ArchitectureSpecification:
    return ArchitectureSpecification(
        nodes=(
            Node(
                id="node",
                modality="neutral_atom",
                modules=(
                    Module(
                        id="compute",
                        type="compute",
                        submodules=(
                            Submodule(
                                id="region",
                                type="region",
                                payload="logical_qubit",
                                capacity=2,
                                qec=QECBinding(
                                    code="surface",
                                    parameters={"distance": 13},
                                ),
                                logical_origin=(10, 20),
                                grid_shape=(1, 2),
                                slots=(
                                    LogicalSlot("D0", (0, 0)),
                                    LogicalSlot("D1", (1, 0)),
                                ),
                            ),
                        ),
                    ),
                    Module(
                        id="factory",
                        type="resource_factory",
                        submodules=(
                            Submodule(
                                id="engine",
                                type="engine",
                                payload="magic_state",
                                capacity=1,
                                resource_protocol=QECResourceProtocolRef(
                                    id="test-protocol",
                                    profile_hash=PROTOCOL_HASH,
                                ),
                            ),
                            Submodule(
                                id="identity_buffer",
                                type="buffer",
                                payload="magic_state",
                                capacity=1,
                                qec=QECBinding(
                                    code="surface",
                                    parameters={"distance": 13},
                                ),
                                slots=(LogicalSlot("M0"),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def _all_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(
            *(_all_keys(child) for child in value.values())
        )
    if isinstance(value, list):
        return set().union(*(_all_keys(child) for child in value))
    return set()


def test_architecture_model_is_deeply_immutable_and_detached_from_inputs() -> None:
    qec_parameters = {"distance": 13, "decoder": {"name": "example"}}
    node = Node(
        id="node",
        modality="neutral_atom",
        modules=(
            Module(
                id="compute",
                type="compute",
                submodules=(
                    Submodule(
                        id="region",
                        type="region",
                        payload="logical_qubit",
                        capacity=1,
                        qec=QECBinding(
                            code="surface",
                            parameters=qec_parameters,
                        ),
                        slots=(LogicalSlot("D0", (0, 0)),),
                    ),
                ),
            ),
        ),
    )

    qec_parameters["distance"] = 99
    qec_parameters["decoder"]["name"] = "mutated"
    assert node.modules[0].submodules[0].qec is not None
    assert node.modules[0].submodules[0].qec.parameters["distance"] == 13
    assert node.modules[0].submodules[0].qec.parameters["decoder"]["name"] == "example"
    assert node.modules[0].submodules[0].capacity == 1

    with pytest.raises(TypeError):
        node.modules[0].submodules[0].qec.parameters["distance"] = 20
    with pytest.raises(FrozenInstanceError):
        node.id = "changed"


def test_architecture_model_round_trip_hash_and_queries_are_strict() -> None:
    specification = _minimal_specification()
    document = specification.to_dict()
    restored = ArchitectureSpecification.from_dict(document)

    assert restored == specification
    assert restored.to_dict() == document
    assert restored.architecture_hash == document["architecture_hash"]
    assert restored.node("node").id == "node"
    assert restored.module("node", "compute").id == "compute"
    assert restored.submodule("node", "compute", "region").id == "region"
    assert restored.submodule(
        "node", "compute", "region"
    ).grid_shape == (1, 2)
    assert restored.effective_slot_coordinate(
        "node", "compute", "region", "D1"
    ) == (11, 20)
    assert restored.effective_slot_coordinate(
        "node", "factory", "identity_buffer", "M0"
    ) is None
    assert not hasattr(restored, "submodules")
    assert not hasattr(restored, "modules_of_type")

    with pytest.raises(KeyError, match="missing"):
        restored.node("missing")
    with pytest.raises(KeyError, match="node/missing"):
        restored.module("node", "missing")
    with pytest.raises(KeyError, match="node/compute/missing"):
        restored.submodule("node", "compute", "missing")
    with pytest.raises(KeyError, match="node/compute/region/missing"):
        restored.effective_slot_coordinate(
            "node", "compute", "region", "missing"
        )


def test_architecture_model_rejects_unknown_missing_non_json_and_tampered_data() -> None:
    document = _minimal_specification().to_dict()

    unknown = deepcopy(document)
    unknown["legacy_projection"] = {}
    with pytest.raises(ValueError, match="Unknown fields"):
        ArchitectureSpecification.from_dict(unknown)

    nested_unknown = deepcopy(document)
    nested_unknown["nodes"][0]["modules"][0]["old_role"] = "compute"
    with pytest.raises(ValueError, match="Unknown fields"):
        ArchitectureSpecification.from_dict(nested_unknown)

    missing = deepcopy(document)
    missing["nodes"][0].pop("connections")
    with pytest.raises(ValueError, match="Missing fields"):
        ArchitectureSpecification.from_dict(missing)

    non_string_key = deepcopy(document)
    non_string_key["nodes"][0][1] = "not-json"
    with pytest.raises(ValueError, match="keys must be strings"):
        ArchitectureSpecification.from_dict(non_string_key)

    non_finite = deepcopy(document)
    non_finite["nodes"][0]["modules"][0]["submodules"][0]["qec"][
        "parameters"
    ]["bad"] = float("nan")
    with pytest.raises(ValueError, match="NaN or infinity"):
        ArchitectureSpecification.from_dict(non_finite)

    tampered = deepcopy(document)
    tampered["nodes"][0]["modality"] = "tampered_modality"
    with pytest.raises(ValueError, match="hash mismatch"):
        ArchitectureSpecification.from_dict(tampered)


def test_architecture_model_rejects_node_global_coordinate_aliasing() -> None:
    with pytest.raises(ValueError, match="Logical slot coordinates"):
        ArchitectureSpecification(
            nodes=(
                Node(
                    id="node",
                    modality="neutral_atom",
                    modules=(
                        Module(
                            id="first",
                            type="compute",
                            submodules=(
                                Submodule(
                                    id="a",
                                    type="region",
                                    payload="logical_qubit",
                                    capacity=1,
                                    logical_origin=(10, 10),
                                    slots=(LogicalSlot("D0", (0, 0)),),
                                ),
                            ),
                        ),
                        Module(
                            id="second",
                            type="memory",
                            submodules=(
                                Submodule(
                                    id="b",
                                    type="buffer",
                                    payload="logical_qubit",
                                    capacity=1,
                                    logical_origin=(9, 10),
                                    slots=(LogicalSlot("D1", (1, 0)),),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        )


def test_architecture_core_excludes_legacy_runtime_cost_and_physical_authorities() -> None:
    tree = ast.parse(CORE_PATH.read_text(encoding="utf-8"), filename=str(CORE_PATH))
    imported: set[str] = set()
    referenced_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Name):
            referenced_names.add(node.id)

    assert not any(
        name.startswith(
            (
                "arqsim.compiler",
                "arqsim.evaluation",
                "arqsim.operation_profiles",
                "arqsim.program",
                "arqsim.qec",
                "arqsim.architecture.spec",
                "arqsim.architecture.layout_policy",
                "arqsim.architecture.instantiate",
            )
        )
        for name in imported
    )
    assert not {
        "ArchitectureSpec",
        "ArchitectureLayoutPlan",
        "QECConfiguration",
        "ResolvedFTSystemSpec",
        "WorkflowLayoutContext",
        "PhysicalFootprintEstimate",
        "LogicalLayout",
        "CircuitStatistics",
    }.intersection(referenced_names)

    assert {
        "BellLink",
        "InterconnectAccess",
        "ResolutionReceipt",
        "ResolvedConnection",
        "ResolvedInterconnect",
        "ResolvedModule",
        "ResolvedNode",
        "ResolvedSubmodule",
        "WorkloadBinding",
    }.isdisjoint(referenced_names)

    forbidden_serialized_keys = {
        "architecture_slot_layout",
        "compiler_layout",
        "duration_s",
        "effective_latency",
        "fidelity",
        "layout_plan",
        "physical_footprint",
        "physical_footprint_model",
        "resolved_system",
        "routing",
        "profile_id",
        "policy_id",
        "resolution",
        "sizing_policy",
        "workload",
    }
    document = _minimal_specification().to_dict()
    assert document["schema_version"] == "arqsim.architecture-specification.v3"
    assert set(document) == {
        "schema_version",
        "nodes",
        "interconnects",
        "architecture_hash",
    }
    assert not forbidden_serialized_keys.intersection(_all_keys(document))


def test_architecture_hash_is_independent_of_python_hash_seed() -> None:
    command = [
        sys.executable,
        "-c",
        (
            "from tests.test_architecture_model import "
            "_minimal_specification; print(_minimal_specification().architecture_hash)"
        ),
    ]
    hashes = []
    for seed in ("1", "997"):
        environment = os.environ.copy()
        environment["PYTHONHASHSEED"] = seed
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        hashes.append(completed.stdout.strip())
    assert hashes[0] == hashes[1]


def test_11_profile_contains_logical_capabilities_not_physical_calibration() -> None:
    profile = get_architecture_profile("1.1")
    assert ArchitectureProfile.from_dict(profile.to_dict()) == profile
    assert "technology" not in profile.to_dict()["nodes"]["na_node"]

    unknown = deepcopy(profile.to_dict())
    unknown["nodes"]["na_node"]["technology"] = {
        "atom_spacing_um": {"x": 19.0, "y": 15.0}
    }
    with pytest.raises(ValueError, match="Unknown fields"):
        ArchitectureProfile.from_dict(unknown)


def test_architecture_core_accepts_open_graph_vocabulary() -> None:
    document = _minimal_specification().to_dict()
    document["nodes"][0]["modality"] = "quantum_dust"
    document["nodes"][0]["modules"][0]["type"] = "star_compute"
    document["nodes"][0]["modules"][0]["submodules"][0]["type"] = "orbital"
    document["nodes"][0]["modules"][0]["submodules"][0]["payload"] = "cat_state"
    document.pop("architecture_hash")
    # Capability support belongs to a Profile/resolver/backend, not this
    # generic static graph.  Rebuild the hash from the open-vocabulary graph.
    rebuilt = ArchitectureSpecification(
        nodes=tuple(Node.from_dict(item) for item in document["nodes"]),
        interconnects=(),
    )
    assert rebuilt.node("node").modality == "quantum_dust"
    assert rebuilt.module("node", "compute").type == "star_compute"
    assert rebuilt.submodule("node", "compute", "region").payload == "cat_state"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda document: document["nodes"][0]["modules"][0].update(
                {"id": "ambiguous/path"}
            ),
            "must not contain '/'",
        ),
        (
            lambda document: document["nodes"][0]["modules"][0][
                "submodules"
            ][0].update({"capacity": {"logical_patches": 1}}),
            "capacity must be a plain integer",
        ),
    ],
)
def test_architecture_core_still_rejects_malformed_graph_values(
    mutation: Any, message: str
) -> None:
    document = _minimal_specification().to_dict()
    mutation(document)
    with pytest.raises(ValueError, match=message):
        ArchitectureSpecification.from_dict(document)


def test_architecture_core_enforces_grid_module_and_capacity_semantics() -> None:
    with pytest.raises(ValueError, match="outside grid_shape"):
        Submodule(
            id="region",
            type="region",
            payload="logical_qubit",
            capacity=1,
            grid_shape=(1, 1),
            slots=(LogicalSlot("D0", (1, 0)),),
        )

    for invalid in (True, 1.0, "1", {"logical_patches": 1}):
        with pytest.raises(ValueError, match="capacity must be a plain integer"):
            Submodule(
                id="buffer",
                type="buffer",
                payload="logical_qubit",
                capacity=invalid,
            )
    with pytest.raises(ValueError, match="capacity must be non-negative"):
        Submodule(
            id="buffer",
            type="buffer",
            payload="logical_qubit",
            capacity=-1,
        )

    empty = Submodule(
        id="empty buffer",
        type="buffer",
        payload="logical_qubit",
        capacity=0,
    )
    assert empty.slots == ()
    assert empty.logical_origin is None
    assert empty.grid_shape is None
    for geometry in (
        {"logical_origin": (0, 0)},
        {"grid_shape": (1, 1)},
    ):
        with pytest.raises(
            ValueError,
            match="Zero-capacity Submodule .* must not have logical geometry",
        ):
            Submodule(
                id="empty buffer",
                type="buffer",
                payload="logical_qubit",
                capacity=0,
                **geometry,
            )

    with pytest.raises(ValueError, match="Engine Submodule .* must be positive"):
        Submodule(
            id="empty engine",
            type="engine",
            payload="magic_state",
            capacity=0,
        )


def test_architecture_core_has_no_arbitrary_coordinate_magnitude_limit() -> None:
    huge = 10**30
    specification = ArchitectureSpecification(
        nodes=(
            Node(
                id="node",
                modality="future_modality",
                modules=(
                    Module(
                        id="module",
                        type="future_module",
                        submodules=(
                            Submodule(
                                id="region",
                                type="future_region",
                                payload="future_payload",
                                capacity=1,
                                logical_origin=(-huge, huge),
                                slots=(LogicalSlot("slot", (huge, -huge)),),
                            ),
                        ),
                    ),
                ),
            ),
        )
    )
    assert specification.effective_slot_coordinate(
        "node", "module", "region", "slot"
    ) == (0, 0)


def test_non_engine_capacity_is_exact_materialized_slot_count() -> None:
    for slots in (
        (LogicalSlot("D0"),),
        (LogicalSlot("D0"), LogicalSlot("D1"), LogicalSlot("D2")),
    ):
        with pytest.raises(ValueError, match="exactly capacity=2 slots"):
            Submodule(
                id="data buffer",
                type="buffer",
                payload="logical_qubit",
                capacity=2,
                slots=slots,
            )

    nonspatial = Submodule(
        id="BB logical modes",
        type="region",
        payload="logical_qubit",
        capacity=2,
        slots=(LogicalSlot("mode 0"), LogicalSlot("mode 1")),
    )
    assert nonspatial.capacity == len(nonspatial.slots) == 2
    assert all(slot.coordinate is None for slot in nonspatial.slots)


def test_engine_capacity_counts_opaque_copies_without_slots() -> None:
    engine = Submodule(
        id="factory engine",
        type="engine",
        payload="magic_state",
        capacity=3,
        resource_protocol=QECResourceProtocolRef(
            id="test-protocol",
            profile_hash=PROTOCOL_HASH,
        ),
    )

    assert engine.capacity == 3
    assert engine.slots == ()
    assert "slots" not in engine.to_dict()

    with pytest.raises(ValueError, match="must not expose slots"):
        Submodule(
            id="invalid engine",
            type="engine",
            payload="magic_state",
            capacity=1,
            slots=(LogicalSlot("internal patch"),),
        )
    for geometry in (
        {"logical_origin": (0, 0)},
        {"grid_shape": (1, 1)},
    ):
        with pytest.raises(ValueError, match="must not have logical geometry"):
            Submodule(
                id="invalid engine geometry",
                type="engine",
                payload="magic_state",
                capacity=1,
                **geometry,
            )


def test_architecture_ids_are_opaque_stable_keys_not_hierarchy_or_counts() -> None:
    specification = ArchitectureSpecification(
        nodes=(
            Node(
                id="Neutral Atom node-v2",
                modality="neutral_atom",
                modules=(
                    Module(
                        id="Compute.Array-v2",
                        type="compute",
                        submodules=(
                            Submodule(
                                id="Surface region-v2",
                                type="region",
                                payload="logical_qubit",
                                capacity=2,
                                slots=(LogicalSlot("q 0"), LogicalSlot("q 1")),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    assert specification.node("Neutral Atom node-v2").id == "Neutral Atom node-v2"
    assert (
        specification.submodule(
            "Neutral Atom node-v2", "Compute.Array-v2", "Surface region-v2"
        ).capacity
        == 2
    )


@pytest.mark.parametrize("value", ["", " leading", "trailing ", 7, True])
def test_architecture_ids_must_be_nonempty_trimmed_strings(value: object) -> None:
    with pytest.raises(ValueError, match="non-empty trimmed string"):
        LogicalSlot(value)  # type: ignore[arg-type]


def test_component_ids_reserve_slash_only_for_qualified_paths() -> None:
    with pytest.raises(ValueError, match="must not contain '/'"):
        Submodule(
            id="module/submodule",
            type="buffer",
            payload="logical_qubit",
            capacity=1,
        )


def test_local_connection_is_topology_only_and_payload_is_derived() -> None:
    connection = LocalConnection(
        id="delivery",
        direction="directed",
        endpoints=("source/output", "target/input"),
    )
    assert set(connection.to_dict()) == {"id", "direction", "endpoints"}

    with pytest.raises(ValueError, match="two distinct Modules"):
        LocalConnection(
            id="internal_path",
            direction="directed",
            endpoints=("module/source", "module/destination"),
        )

    with pytest.raises(ValueError, match="Module/Submodule"):
        LocalConnection(
            id="cross_owner",
            direction="directed",
            endpoints=(
                "left_node/source/output",
                "right_node/target/input",
            ),
        )

    with pytest.raises(ValueError, match="same payload"):
        Node(
            id="node",
            modality="future_modality",
            modules=(
                Module(
                    id="source",
                    type="producer",
                    submodules=(
                        Submodule(
                            id="output",
                            type="buffer",
                            payload="alpha",
                            capacity=1,
                            slots=(LogicalSlot("A0"),),
                        ),
                    ),
                ),
                Module(
                    id="target",
                    type="consumer",
                    submodules=(
                        Submodule(
                            id="input",
                            type="buffer",
                            payload="beta",
                            capacity=1,
                            slots=(LogicalSlot("B0"),),
                        ),
                    ),
                ),
            ),
            connections=(connection,),
        )


def _bell_node(
    node_id: str,
    *,
    endpoint_payload: str = "bell_pair",
    endpoint_ids: tuple[str, ...] = ("endpoint",),
) -> Node:
    def ingress_id(endpoint_id: str) -> str:
        return "bell_ingress" if endpoint_id == "endpoint" else f"{endpoint_id}_ingress"

    return Node(
        id=node_id,
        modality="superconducting",
        modules=(
            Module(
                id="compute",
                type="compute",
                submodules=tuple(
                    Submodule(
                        id=ingress_id(endpoint_id),
                        type="buffer",
                        payload=endpoint_payload,
                        capacity=1,
                        slots=(LogicalSlot("local_pair"),),
                    )
                    for endpoint_id in endpoint_ids
                ),
            ),
            Module(
                id="network",
                type="network_interface",
                submodules=tuple(
                    Submodule(
                        id=endpoint_id,
                        type="buffer",
                        payload=endpoint_payload,
                        capacity=1,
                        slots=(LogicalSlot("local_pair"),),
                    )
                    for endpoint_id in endpoint_ids
                ),
            ),
        ),
        connections=tuple(
            LocalConnection(
                id=(
                    "bell_access"
                    if endpoint_id == "endpoint"
                    else f"{endpoint_id}_access"
                ),
                direction="bidirectional",
                endpoints=(
                    f"network/{endpoint_id}",
                    f"compute/{ingress_id(endpoint_id)}",
                ),
            )
            for endpoint_id in endpoint_ids
        ),
    )


def _direct_access_node(node_id: str, *, payload: str = "logical_qubit") -> Node:
    return Node(
        id=node_id,
        modality="future_modality",
        modules=(
            Module(
                id="compute",
                type="compute",
                submodules=(
                    Submodule(
                        id="remote_access",
                        type="region",
                        payload=payload,
                        capacity=1,
                        slots=(LogicalSlot("D0"),),
                    ),
                ),
            ),
        ),
    )


def _shared_interconnect(
    *,
    endpoints: tuple[str, ...] = (
        "left_node/network/endpoint",
        "right_node/network/endpoint",
    ),
) -> Interconnect:
    return Interconnect(
        id="backbone",
        endpoints=endpoints,
        modules=(
            Module(
                id="bell_generation",
                type="bell_generation",
                submodules=(
                    Submodule(
                        id="generator",
                        type="engine",
                        payload="bell_pair",
                        capacity=2,
                        resource_protocol=QECResourceProtocolRef(
                            id="bell-protocol",
                            profile_hash=PROTOCOL_HASH,
                        ),
                    ),
                ),
            ),
            Module(
                id="bell_storage",
                type="bell_storage",
                submodules=(
                    Submodule(
                        id="shared_buffer",
                        type="buffer",
                        payload="bell_pair",
                        capacity=2,
                        slots=(LogicalSlot("B0"), LogicalSlot("B1")),
                    ),
                ),
            ),
        ),
        connections=(
            LocalConnection(
                id="generation_to_storage",
                direction="directed",
                endpoints=(
                    "bell_generation/generator",
                    "bell_storage/shared_buffer",
                ),
            ),
        ),
    )


def _minimal_interconnect_specification() -> ArchitectureSpecification:
    return ArchitectureSpecification(
        nodes=(_bell_node("left_node"), _bell_node("right_node")),
        interconnects=(_shared_interconnect(),),
    )


def test_interconnect_is_a_node_peer_resource_owner_and_round_trips() -> None:
    specification = _minimal_interconnect_specification()
    restored = ArchitectureSpecification.from_dict(specification.to_dict())
    interconnect = restored.interconnect("backbone")

    assert restored.to_dict() == specification.to_dict()
    assert interconnect.endpoints == (
        "left_node/network/endpoint",
        "right_node/network/endpoint",
    )
    assert set(interconnect.to_dict()) == {
        "id",
        "endpoints",
        "modules",
        "connections",
    }
    assert not hasattr(interconnect, "node_ids")
    assert not hasattr(interconnect, "bell_links")
    assert isinstance(interconnect.connections[0], LocalConnection)
    assert isinstance(restored.node("left_node").connections[0], LocalConnection)

    generation = next(module for module in interconnect.modules if module.id == "bell_generation")
    storage = next(module for module in interconnect.modules if module.id == "bell_storage")
    assert generation.submodules[0].capacity == 2
    assert storage.submodules[0].capacity == 2


def test_interconnect_collection_order_does_not_change_the_receipt() -> None:
    baseline = _shared_interconnect()
    storage_return = LocalConnection(
        id="storage_return",
        direction="directed",
        endpoints=(
            "bell_storage/shared_buffer",
            "bell_generation/generator",
        ),
    )
    connections = (*baseline.connections, storage_return)
    forward = ArchitectureSpecification(
        nodes=(_bell_node("left_node"), _bell_node("right_node")),
        interconnects=(
            Interconnect(
                id=baseline.id,
                endpoints=baseline.endpoints,
                modules=baseline.modules,
                connections=connections,
            ),
        ),
    )
    reversed_inputs = ArchitectureSpecification(
        nodes=(_bell_node("right_node"), _bell_node("left_node")),
        interconnects=(
            Interconnect(
                id=baseline.id,
                endpoints=tuple(reversed(baseline.endpoints)),
                modules=tuple(reversed(baseline.modules)),
                connections=tuple(reversed(connections)),
            ),
        ),
    )

    assert forward.to_dict() == reversed_inputs.to_dict()
    assert forward.architecture_hash == reversed_inputs.architecture_hash


def test_wire_endpoints_reject_mapping_shaped_collections() -> None:
    with pytest.raises((TypeError, ValueError)):
        LocalConnection.from_dict(
            {
                "id": "delivery",
                "direction": "directed",
                "endpoints": {
                    "source/output": None,
                    "target/input": None,
                },
            }
        )

    baseline = _shared_interconnect()
    document = baseline.to_dict()
    document["endpoints"] = {
        "left_node/network/endpoint": None,
        "right_node/network/endpoint": None,
    }
    with pytest.raises((TypeError, ValueError)):
        Interconnect.from_dict(document)


def test_one_bell_storage_holds_each_shared_pair_once() -> None:
    interconnect = _minimal_interconnect_specification().interconnect("backbone")
    storage_modules = [
        module for module in interconnect.modules if module.type == "bell_storage"
    ]
    assert len(storage_modules) == 1
    shared_buffer = storage_modules[0].submodules[0]
    assert shared_buffer.id == "shared_buffer"
    assert shared_buffer.capacity == len(shared_buffer.slots) == 2
    assert tuple(slot.id for slot in shared_buffer.slots) == ("B0", "B1")


def test_interconnect_may_expose_multiple_local_endpoints_per_node() -> None:
    endpoints = (
        "left_node/network/north",
        "left_node/network/south",
        "right_node/network/north",
        "right_node/network/south",
    )
    specification = ArchitectureSpecification(
        nodes=(
            _bell_node("left_node", endpoint_ids=("north", "south")),
            _bell_node("right_node", endpoint_ids=("north", "south")),
        ),
        interconnects=(_shared_interconnect(endpoints=endpoints),),
    )
    interconnect = specification.interconnect("backbone")
    assert interconnect.endpoints == endpoints
    assert len(
        [module for module in interconnect.modules if module.id == "bell_storage"]
    ) == 1


def test_bell_generation_and_storage_are_independent_modules() -> None:
    baseline = _shared_interconnect()
    generation, storage = baseline.modules
    assert generation.id == "bell_generation"
    assert generation.submodules[0].type == "engine"
    assert generation.submodules[0].capacity == 2
    assert storage.id == "bell_storage"
    assert storage.submodules[0].type == "buffer"
    assert storage.submodules[0].capacity == 2


def test_node_and_interconnect_connections_are_local_to_their_owner() -> None:
    baseline = _shared_interconnect()
    assert baseline.connections[0].endpoints == (
        "bell_generation/generator",
        "bell_storage/shared_buffer",
    )
    assert _bell_node("left_node").connections[0].endpoints == (
        "compute/bell_ingress",
        "network/endpoint",
    )

    with pytest.raises(ValueError, match="unknown Submodules.*Interconnect backbone"):
        Interconnect(
            id=baseline.id,
            endpoints=baseline.endpoints,
            modules=baseline.modules,
            connections=(
                LocalConnection(
                    id="outside_owner",
                    direction="directed",
                    endpoints=(
                        "bell_generation/generator",
                        "bell_storage/missing",
                    ),
                ),
            ),
        )


def test_interconnect_endpoints_are_absolute_node_submodule_paths() -> None:
    baseline = _shared_interconnect()
    assert baseline.endpoints == tuple(sorted(baseline.endpoints))

    with pytest.raises(ValueError, match="Node/Module/Submodule"):
        Interconnect(
            id=baseline.id,
            endpoints=("network/endpoint", "right_node/network/endpoint"),
            modules=baseline.modules,
            connections=baseline.connections,
        )
    with pytest.raises(ValueError, match="distinct endpoints"):
        Interconnect(
            id=baseline.id,
            endpoints=(
                "left_node/network/endpoint",
                "left_node/network/endpoint",
            ),
            modules=baseline.modules,
            connections=baseline.connections,
        )


def test_interconnect_rejects_unknown_node_or_submodule_references() -> None:
    baseline = _shared_interconnect()
    with pytest.raises(ValueError, match="unknown Node"):
        ArchitectureSpecification(
            nodes=(_bell_node("left_node"), _bell_node("right_node")),
            interconnects=(
                Interconnect(
                    id=baseline.id,
                    endpoints=(
                        "left_node/network/endpoint",
                        "missing_node/network/endpoint",
                    ),
                    modules=baseline.modules,
                    connections=baseline.connections,
                ),
            ),
        )

    with pytest.raises(ValueError, match="Unknown.*Submodule"):
        ArchitectureSpecification(
            nodes=(_bell_node("left_node"), _bell_node("right_node")),
            interconnects=(
                Interconnect(
                    id=baseline.id,
                    endpoints=(
                        "left_node/network/missing",
                        "right_node/network/endpoint",
                    ),
                    modules=baseline.modules,
                    connections=baseline.connections,
                ),
            ),
        )


def test_interconnect_endpoints_must_span_exactly_two_nodes() -> None:
    baseline = _shared_interconnect()
    with pytest.raises(ValueError, match="exactly two distinct Nodes"):
        ArchitectureSpecification(
            nodes=(_bell_node("left_node", endpoint_ids=("north", "south")),),
            interconnects=(
                Interconnect(
                    id=baseline.id,
                    endpoints=(
                        "left_node/network/north",
                        "left_node/network/south",
                    ),
                    modules=baseline.modules,
                    connections=baseline.connections,
                ),
            ),
        )

    with pytest.raises(ValueError, match="exactly two distinct Nodes"):
        ArchitectureSpecification(
            nodes=(
                _bell_node("left_node"),
                _bell_node("middle_node"),
                _bell_node("right_node"),
            ),
            interconnects=(
                _shared_interconnect(
                    endpoints=(
                        "left_node/network/endpoint",
                        "middle_node/network/endpoint",
                        "right_node/network/endpoint",
                    )
                ),
            ),
        )


def test_interconnect_endpoint_payload_is_not_a_core_constraint() -> None:
    specification = ArchitectureSpecification(
        nodes=(
            _bell_node("left_node", endpoint_payload="future_state"),
            _bell_node("right_node", endpoint_payload="future_state"),
        ),
        interconnects=(_shared_interconnect(),),
    )
    assert specification.submodule(
        "left_node", "network", "endpoint"
    ).payload == "future_state"


def test_interconnect_may_attach_directly_without_a_local_connection() -> None:
    specification = ArchitectureSpecification(
        nodes=(_direct_access_node("left_node"), _direct_access_node("right_node")),
        interconnects=(
            _shared_interconnect(
                endpoints=(
                    "left_node/compute/remote_access",
                    "right_node/compute/remote_access",
                )
            ),
        ),
    )
    assert all(not node.connections for node in specification.nodes)


def test_node_and_interconnect_ids_share_one_owner_namespace() -> None:
    baseline = _shared_interconnect()
    with pytest.raises(ValueError, match="globally unique resource owners"):
        ArchitectureSpecification(
            nodes=(_bell_node("backbone"), _bell_node("right_node")),
            interconnects=(
                Interconnect(
                    id="backbone",
                    endpoints=(
                        "backbone/network/endpoint",
                        "right_node/network/endpoint",
                    ),
                    modules=baseline.modules,
                    connections=baseline.connections,
                ),
            ),
        )


def _core_architecture_projection(
    specification: ArchitectureSpecification,
    *,
    profile_id: str,
    logical_qubits: int,
) -> dict[str, Any]:
    nodes = []
    for node in specification.nodes:
        modules = []
        for module in node.modules:
            submodules = []
            for submodule in module.submodules:
                record: dict[str, Any] = {
                    "id": submodule.id,
                    "type": submodule.type,
                    "payload": submodule.payload,
                    "capacity": {
                        {
                            ("region", "logical_qubit"): "logical_patches",
                            ("buffer", "logical_qubit"): "logical_patches",
                            ("buffer", "magic_state"): "logical_magic_states",
                            ("engine", "magic_state"): "copies",
                            ("engine", "bell_pair"): "copies",
                            ("buffer", "bell_pair"): "logical_bell_pairs",
                        }[(submodule.type, submodule.payload)]: submodule.capacity
                    },
                }
                if submodule.qec is not None:
                    record["qec"] = submodule.qec.to_dict()
                if submodule.resource_protocol is not None:
                    record["resource_protocol"] = {
                        "id": submodule.resource_protocol.id
                    }
                if submodule.slots:
                    slots = []
                    for slot in submodule.slots:
                        slot_record: dict[str, Any] = {
                            "id": (
                                f"{node.id}/{module.id}/{submodule.id}/{slot.id}"
                            )
                        }
                        coordinate = specification.effective_slot_coordinate(
                            node.id, module.id, submodule.id, slot.id
                        )
                        if coordinate is not None:
                            slot_record["logical_coordinate"] = list(coordinate)
                        slots.append(slot_record)
                    record["slots"] = slots
                submodules.append(record)
            modules.append(
                {
                    "id": module.id,
                    "type": module.type,
                    "submodules": submodules,
                }
            )
        nodes.append(
            {
                "id": node.id,
                "modality": node.modality,
                "modules": modules,
                "connections": [
                    {
                        "id": connection.id,
                        "payload": specification.submodule(
                            node.id, *connection.endpoints[0].split("/")
                        ).payload,
                        "direction": connection.direction,
                        "endpoints": list(connection.endpoints),
                    }
                    for connection in node.connections
                ],
            }
        )

    protocol_records = []
    for node in specification.nodes:
        for module in node.modules:
            for submodule in module.submodules:
                if (submodule.type, submodule.payload) != (
                    "engine",
                    "magic_state",
                ):
                    continue
                assert submodule.resource_protocol is not None
                protocol = get_magic_state_factory_profile(
                    submodule.resource_protocol.id
                )
                protocol_records.append(
                    {
                        "resource_kind": "magic_state",
                        "protocol_id": protocol.id,
                        "copies": submodule.capacity,
                        "outputs_per_copy_per_batch": protocol.outputs_per_batch,
                    }
                )
    return {
        "profile_id": profile_id,
        "workload": {"logical_qubits": logical_qubits},
        "nodes": nodes,
        "interconnects": [],
        "resource_protocols": protocol_records,
    }


def test_generic_resolver_and_public_build_share_the_canonical_result() -> None:
    profile = get_architecture_profile("1.1")
    resolved = _generic_resolution(profile)

    assert resolved.architecture_hash
    assert not hasattr(resolved.node("na_node"), "technology")

    packaged = build_architecture_specification(
        oracle_circuit(),
        "1.1",
    )
    assert packaged == resolved


def test_generic_core_matches_the_architecture_semantic_oracle() -> None:
    specification = _generic_resolution(get_architecture_profile("1.1"))
    expected = json.loads(ORACLE_FIXTURE.read_text(encoding="utf-8"))[
        "architecture"
    ]
    actual = _core_architecture_projection(
        specification,
        profile_id="1.1",
        logical_qubits=oracle_circuit().num_qubits,
    )

    assert_semantic_subset(actual, expected)
    assert_semantic_subset(expected, actual)
    assert "slots" not in specification.submodule(
        "na_node", "na_msf", "factory_engine"
    ).to_dict()


def test_resolver_dependency_is_forward_only() -> None:
    resolver_tree = ast.parse(
        RESOLVER_PATH.read_text(encoding="utf-8"), filename=str(RESOLVER_PATH)
    )
    imports: set[str] = set()
    attributes: set[str] = set()
    referenced_names: set[str] = set()
    string_literals: set[str] = set()
    for node in ast.walk(resolver_tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
        elif isinstance(node, ast.Attribute):
            attributes.add(node.attr)
        elif isinstance(node, ast.Name):
            referenced_names.add(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            string_literals.add(node.value)

    assert not any(
        name.startswith(
            (
                "arqsim.compiler",
                "arqsim.evaluation",
                "arqsim.specification",
                "arqsim.architecture.instantiate",
                "arqsim.architecture.legacy_adapter",
                "arqsim.architecture.spec",
                "arqsim.architecture.quantile_layout",
            )
        )
        for name in imports
    )
    assert not {"_compat_role", "role"}.intersection(attributes)
    assert not {
        "CircuitStatistics",
        "FTCircuit",
        "LogicalLayoutPolicy",
        "QuantileLayoutPolicy",
        "SizingPolicy",
    }.intersection(referenced_names)
    assert not {
        "1.1",
        "arqsim.1.1",
        "na_node",
        "na_compute",
        "na_msf",
    }.intersection(string_literals)
