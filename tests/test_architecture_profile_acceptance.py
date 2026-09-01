"""Acceptance gates for the minimal canonical ArchitectureProfile v3.

These tests protect canonical authoring intent, independent of report-v1
projections.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from pathlib import Path

import pytest
import yaml

import heteqsys.architecture as architecture_package
import heteqsys.architecture.profile as profile_module
from heteqsys.architecture.gallery import (
    GalleryEntry,
    get_architecture_profile,
    get_gallery_entry,
    list_architecture_profiles,
    list_gallery_entries,
)
from heteqsys.architecture.profile import (
    ARCHITECTURE_PROFILE_SCHEMA_VERSION,
    ArchitectureProfile,
    ProfileInterconnect,
    ProfileLocalConnection,
    ProfileModule,
    ProfileNode,
    ProfileSubmodule,
    load_architecture_profile,
)
from heteqsys.schema import semantic_hash


PROJECT_ROOT = Path(__file__).parents[1]
PROFILE_SOURCE = PROJECT_ROOT / "heteqsys" / "architecture" / "profile.py"
GALLERY_DIR = PROJECT_ROOT / "heteqsys" / "architecture" / "gallery"

PROFILE_IDS = ("1.1", "1.2", "1.3", "2.1", "2.2", "2.3")
PROFILE_HASHES = {
    "1.1": "4e84c3c0561490a4098f7a44edd2ee76c845af14e12dbc28baee146e4b975b76",
    "1.2": "890df3c2f59503202512ca5baa542b1bccd953f2ce95669a997e6717355911fb",
    "1.3": "13405217a36af544e9d60754a382edb6f162cd9874c482a986ddad7938edc7f9",
    "2.1": "61de579e8c29173069faf42180d8d9349af28aeae6015aeca042b8faebbfc683",
    "2.2": "48134dada7d912d21b5dbc65e6355fe98e75ddd671a0def986cce3b027d002a6",
    "2.3": "a3699f2577fd29c2ae6f3f4a455505fcf3196cb4dd0f6501abc52a72653c6303",
}
LOCAL_PROFILE_IDS = ("1.1", "1.2", "2.1")
MULTI_NODE_PROFILE_IDS = ("1.3", "2.2", "2.3")


def _all_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value).union(
            *(_all_keys(child) for child in value.values())
        )
    if isinstance(value, list):
        return set().union(*(_all_keys(child) for child in value))
    return set()


def _reverse_mapping(value: dict[str, object]) -> dict[str, object]:
    return dict(reversed(tuple(value.items())))


def _first_node(document: dict[str, object]) -> dict[str, object]:
    nodes = document["nodes"]
    assert isinstance(nodes, dict)
    node = next(iter(nodes.values()))
    assert isinstance(node, dict)
    return node


def _first_submodule(document: dict[str, object]) -> dict[str, object]:
    node = _first_node(document)
    modules = node["modules"]
    assert isinstance(modules, dict)
    module = next(iter(modules.values()))
    assert isinstance(module, dict)
    submodules = module["submodules"]
    assert isinstance(submodules, dict)
    submodule = next(iter(submodules.values()))
    assert isinstance(submodule, dict)
    return submodule


def _first_connection(document: dict[str, object]) -> dict[str, object]:
    nodes = document["nodes"]
    assert isinstance(nodes, dict)
    for node in nodes.values():
        assert isinstance(node, dict)
        connections = node.get("connections", {})
        assert isinstance(connections, dict)
        if connections:
            connection = next(iter(connections.values()))
            assert isinstance(connection, dict)
            return connection
    raise AssertionError("fixture needs one Node-local connection")


def test_profile_v3_records_own_only_unsized_canonical_facts() -> None:
    assert ARCHITECTURE_PROFILE_SCHEMA_VERSION == (
        "arqsim.architecture-profile.v3"
    )
    assert tuple(field.name for field in fields(ProfileSubmodule)) == (
        "id",
        "type",
        "payload",
    )
    assert tuple(field.name for field in fields(ProfileModule)) == (
        "id",
        "type",
        "submodules",
    )
    assert tuple(field.name for field in fields(ProfileLocalConnection)) == (
        "id",
        "direction",
        "endpoints",
    )
    assert tuple(field.name for field in fields(ProfileNode)) == (
        "id",
        "modality",
        "modules",
        "connections",
    )
    assert tuple(field.name for field in fields(ProfileInterconnect)) == (
        "id",
        "endpoints",
        "modules",
        "connections",
    )
    assert tuple(field.name for field in fields(ArchitectureProfile)) == (
        "id",
        "name",
        "nodes",
        "description",
        "interconnects",
    )

    profile = get_architecture_profile("1.1")
    submodule = profile.nodes[0].modules[0].submodules[0]
    assert not hasattr(profile, "__dict__")
    assert not hasattr(submodule, "__dict__")
    with pytest.raises(FrozenInstanceError):
        submodule.payload = "changed"  # type: ignore[misc]


def test_canonical_profile_has_no_legacy_projection_surface() -> None:
    profile = get_architecture_profile("1.3")
    representatives = (
        profile,
        profile.nodes[0],
        profile.nodes[0].modules[0],
        profile.nodes[0].modules[0].submodules[0],
        profile.interconnects[0],
    )
    for value in representatives:
        assert not hasattr(value, "_compat_role")
        assert not hasattr(value, "role")
        assert not hasattr(value, "kind")
        assert not hasattr(value, "is_v2")
    assert not hasattr(profile, "connections")
    assert not hasattr(profile.nodes[0], "interconnect_access")

    source = PROFILE_SOURCE.read_text(encoding="utf-8")
    assert "_compat_role" not in source
    imports = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }.union(
        {
            node.module or ""
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom)
        }
    )
    assert not any(name.endswith("profile_compat") for name in imports)
    assert not hasattr(profile_module, "render_profile_v2")
    assert not hasattr(architecture_package, "render_profile_v2")


def test_all_six_authoring_documents_are_strict_v3_and_round_trip() -> None:
    paths = tuple(sorted(GALLERY_DIR.glob("*/profile.yaml")))
    assert len(paths) == len(PROFILE_IDS)
    for path in paths:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == ARCHITECTURE_PROFILE_SCHEMA_VERSION

    profiles = list_architecture_profiles()
    assert tuple(profile.id for profile in profiles) == PROFILE_IDS
    for profile in profiles:
        document = profile.to_dict()
        assert document["schema_version"] == ARCHITECTURE_PROFILE_SCHEMA_VERSION
        assert ArchitectureProfile.from_dict(document) == profile
        assert profile.profile_hash == semantic_hash(document)
        assert profile.profile_hash == PROFILE_HASHES[profile.id]


def test_gallery_is_an_explicit_runtime_index_not_an_architecture_level() -> None:
    entries = list_gallery_entries()
    profiles = list_architecture_profiles()

    assert tuple(entry.id for entry in entries) == PROFILE_IDS
    assert all(isinstance(entry, GalleryEntry) for entry in entries)
    assert tuple(entry.profile for entry in entries) == profiles
    assert get_gallery_entry("1.3").profile is get_architecture_profile("1.3")
    assert get_gallery_entry("heteqsys.1.3") is get_gallery_entry("1.3")
    assert get_gallery_entry("arqsim.1.3") is get_gallery_entry("1.3")
    assert not hasattr(profile_module, "get_architecture_profile")
    assert not hasattr(profile_module, "list_architecture_profiles")
    assert architecture_package.get_architecture_profile is get_architecture_profile
    assert architecture_package.list_architecture_profiles is list_architecture_profiles

    for entry in entries:
        assert not hasattr(entry, "to_dict")
        assert not hasattr(entry, "profile_hash")
        assert not hasattr(entry, "schema_version")
        assert not hasattr(entry, "register")

    with pytest.raises(TypeError, match="lookup ID"):
        get_gallery_entry(1.1)  # type: ignore[arg-type]
    with pytest.raises(KeyError, match="Unknown"):
        get_gallery_entry("unknown")


@pytest.mark.parametrize("profile_id", PROFILE_IDS)
def test_profile_v3_serialization_contains_no_resolved_or_legacy_fields(
    profile_id: str,
) -> None:
    document = get_architecture_profile(profile_id).to_dict()
    keys = _all_keys(document)

    assert not {
        "_compat_role",
        "role",
        "kind",
        "interconnect_access",
        "capacity",
        "qec",
        "slots",
        "logical_origin",
        "grid_shape",
        "resource_protocol",
        "technology",
    }.intersection(keys)
    assert "connections" not in set(document)  # no root aggregate

    nodes = document["nodes"]
    assert isinstance(nodes, dict)
    for node in nodes.values():
        assert isinstance(node, dict)
        connections = node.get("connections", {})
        assert isinstance(connections, dict)
        for connection in connections.values():
            assert isinstance(connection, dict)
            assert set(connection) == {"direction", "endpoints"}

    interconnects = document["interconnects"]
    assert isinstance(interconnects, dict)
    for interconnect in interconnects.values():
        assert isinstance(interconnect, dict)
        assert "submodules" not in interconnect
        connections = interconnect.get("connections", {})
        assert isinstance(connections, dict)
        for connection in connections.values():
            assert isinstance(connection, dict)
            assert set(connection) == {"direction", "endpoints"}


@pytest.mark.parametrize("profile_id", LOCAL_PROFILE_IDS)
def test_local_profiles_have_no_interconnect_owner(profile_id: str) -> None:
    profile = get_architecture_profile(profile_id)
    assert profile.interconnects == ()
    assert profile.to_dict()["interconnects"] == {}


@pytest.mark.parametrize("profile_id", MULTI_NODE_PROFILE_IDS)
def test_multi_node_profiles_author_shared_interconnect_owners(
    profile_id: str,
) -> None:
    profile = get_architecture_profile(profile_id)
    assert len(profile.interconnects) == 1
    interconnect = profile.interconnects[0]

    assert {module.type for module in interconnect.modules} == {
        "bell_engine",
        "bell_storage",
    }
    assert {
        (submodule.type, submodule.payload)
        for module in interconnect.modules
        for submodule in module.submodules
    } == {
        ("engine", "bell_pair"),
        ("buffer", "bell_pair"),
    }
    assert len(interconnect.connections) == 1

    endpoint_nodes = set()
    for endpoint in interconnect.endpoints:
        node_id, module_id, submodule_id = endpoint.split("/")
        endpoint_nodes.add(node_id)
        node = next(node for node in profile.nodes if node.id == node_id)
        module = next(module for module in node.modules if module.id == module_id)
        next(item for item in module.submodules if item.id == submodule_id)
    assert len(endpoint_nodes) == 2


def test_profile_hash_and_serialization_ignore_authoring_order() -> None:
    original = get_architecture_profile("2.3")
    document = deepcopy(original.to_dict())
    nodes = document["nodes"]
    interconnects = document["interconnects"]
    assert isinstance(nodes, dict)
    assert isinstance(interconnects, dict)
    document["nodes"] = _reverse_mapping(nodes)
    document["interconnects"] = _reverse_mapping(interconnects)

    for owner in [*document["nodes"].values(), *document["interconnects"].values()]:
        assert isinstance(owner, dict)
        modules = owner["modules"]
        assert isinstance(modules, dict)
        owner["modules"] = _reverse_mapping(modules)
        connections = owner.get("connections", {})
        assert isinstance(connections, dict)
        owner["connections"] = _reverse_mapping(connections)
        for module in owner["modules"].values():
            assert isinstance(module, dict)
            submodules = module["submodules"]
            assert isinstance(submodules, dict)
            module["submodules"] = _reverse_mapping(submodules)
        for connection in owner["connections"].values():
            assert isinstance(connection, dict)
            if connection["direction"] == "bidirectional":
                connection["endpoints"].reverse()

    interconnect = next(iter(document["interconnects"].values()))
    assert isinstance(interconnect, dict)
    interconnect["endpoints"].reverse()

    reordered = ArchitectureProfile.from_dict(document)
    assert reordered == original
    assert reordered.to_dict() == original.to_dict()
    assert reordered.profile_hash == original.profile_hash


def test_profile_hash_changes_when_authoring_semantics_change() -> None:
    original = get_architecture_profile("1.1")
    document = deepcopy(original.to_dict())
    document["description"] += " Changed semantics."
    changed = ArchitectureProfile.from_dict(document)
    assert changed.profile_hash != original.profile_hash


@pytest.mark.parametrize(
    "legacy_field",
    ("connections", "interconnect_access"),
)
def test_profile_v3_rejects_legacy_root_or_node_fields(
    legacy_field: str,
) -> None:
    document = deepcopy(get_architecture_profile("1.3").to_dict())
    if legacy_field == "connections":
        document[legacy_field] = {}
    else:
        _first_node(document)[legacy_field] = {}
    with pytest.raises(ValueError, match="Unknown fields"):
        ArchitectureProfile.from_dict(document)


def test_profile_v3_rejects_flat_interconnect_submodules() -> None:
    document = deepcopy(get_architecture_profile("1.3").to_dict())
    interconnects = document["interconnects"]
    assert isinstance(interconnects, dict)
    interconnect = next(iter(interconnects.values()))
    assert isinstance(interconnect, dict)
    interconnect["submodules"] = {}
    with pytest.raises(ValueError, match="Unknown fields"):
        ArchitectureProfile.from_dict(document)


@pytest.mark.parametrize(
    ("target", "field", "value"),
    (
        ("submodule", "role", "compute"),
        ("submodule", "kind", "region"),
        ("connection", "payload", "magic_state"),
        ("connection", "from", "factory/output"),
        ("connection", "to", "compute/input"),
    ),
)
def test_profile_v3_rejects_legacy_nested_fields(
    target: str,
    field: str,
    value: object,
) -> None:
    document = deepcopy(get_architecture_profile("1.1").to_dict())
    record = (
        _first_submodule(document)
        if target == "submodule"
        else _first_connection(document)
    )
    record[field] = value
    with pytest.raises(ValueError, match="Unknown fields"):
        ArchitectureProfile.from_dict(document)


def test_profile_loader_rejects_v2_before_parsing_its_shape(tmp_path: Path) -> None:
    path = tmp_path / "legacy-profile.yaml"
    path.write_text(
        "schema_version: arqsim.architecture-profile.v2\n"
        "id: legacy\n"
        "name: Legacy\n"
        "nodes: {}\n"
        "interconnects: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="schema"):
        load_architecture_profile(path)


def test_profile_yaml_loader_rejects_duplicate_mapping_keys(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text(
        "schema_version: arqsim.architecture-profile.v3\n"
        "id: demo\n"
        "name: Demo\n"
        "nodes:\n"
        "  node:\n"
        "    modality: neutral_atom\n"
        "    modality: superconducting\n"
        "    modules: {}\n"
        "interconnects: {}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate YAML mapping key"):
        load_architecture_profile(path)


def test_local_connection_validation_is_owner_local_and_payload_safe() -> None:
    document = deepcopy(get_architecture_profile("1.1").to_dict())
    connection = _first_connection(document)
    connection["endpoints"] = [
        "na_msf/factory_engine",
        "na_msf/magic_state_output_buffer",
    ]
    with pytest.raises(ValueError, match="two distinct Modules"):
        ArchitectureProfile.from_dict(document)

    document = deepcopy(get_architecture_profile("1.1").to_dict())
    connection = _first_connection(document)
    connection["endpoints"][1] = "na_compute/compute_region"
    with pytest.raises(ValueError, match="same payload"):
        ArchitectureProfile.from_dict(document)

    document = deepcopy(get_architecture_profile("1.1").to_dict())
    connection = _first_connection(document)
    connection["endpoints"][1] = "outside/missing"
    with pytest.raises(ValueError, match="unknown Submodules"):
        ArchitectureProfile.from_dict(document)


def test_interconnect_attachments_must_resolve_and_span_two_nodes() -> None:
    document = deepcopy(get_architecture_profile("1.3").to_dict())
    interconnects = document["interconnects"]
    assert isinstance(interconnects, dict)
    interconnect = next(iter(interconnects.values()))
    assert isinstance(interconnect, dict)
    interconnect["endpoints"][0] = "missing/module/submodule"
    with pytest.raises(ValueError, match="unknown Node"):
        ArchitectureProfile.from_dict(document)

    document = deepcopy(get_architecture_profile("1.3").to_dict())
    interconnects = document["interconnects"]
    assert isinstance(interconnects, dict)
    interconnect = next(iter(interconnects.values()))
    assert isinstance(interconnect, dict)
    interconnect["endpoints"] = [
        "na_compute_node/na_compute/compute_region",
        "na_compute_node/na_compute/magic_state_input_buffer",
    ]
    with pytest.raises(ValueError, match="two distinct Nodes"):
        ArchitectureProfile.from_dict(document)
