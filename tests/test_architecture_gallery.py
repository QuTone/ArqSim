"""Ownership and dependency gates for the bundled architecture gallery."""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest

from arqsim.architecture.gallery import (
    GalleryEntry,
    QuantileSizingConfig,
    get_gallery_entry,
    list_gallery_entries,
)
from arqsim.architecture.logical_layout_policy import LogicalLayoutPolicy
from arqsim.architecture.sizing import SizingPolicy


PROJECT_ROOT = Path(__file__).parents[1]
ARCHITECTURE_ROOT = PROJECT_ROOT / "arqsim" / "architecture"
GALLERY_ROOT = ARCHITECTURE_ROOT / "gallery"

_EXPECTED = {
    "1.1": (
        "NA-CF",
        "na_cf",
        "4e84c3c0561490a4098f7a44edd2ee76c845af14e12dbc28baee146e4b975b76",
    ),
    "1.2": (
        "SC-CF",
        "sc_cf",
        "890df3c2f59503202512ca5baa542b1bccd953f2ce95669a997e6717355911fb",
    ),
    "1.3": (
        "NA-C + SC-F",
        "na_c_plus_sc_f",
        "13405217a36af544e9d60754a382edb6f162cd9874c482a986ddad7938edc7f9",
    ),
    "2.1": (
        "NA-MCF",
        "na_mcf",
        "61de579e8c29173069faf42180d8d9349af28aeae6015aeca042b8faebbfc683",
    ),
    "2.2": (
        "NA-M + SC-CF",
        "na_m_plus_sc_cf",
        "48134dada7d912d21b5dbc65e6355fe98e75ddd671a0def986cce3b027d002a6",
    ),
    "2.3": (
        "NA-MC + SC-F",
        "na_mc_plus_sc_f",
        "a3699f2577fd29c2ae6f3f4a455505fcf3196cb4dd0f6501abc52a72653c6303",
    ),
}


def test_gallery_uses_the_six_reference_names_and_preserves_profiles() -> None:
    entries = list_gallery_entries()

    assert tuple(entry.id for entry in entries) == tuple(_EXPECTED)
    assert tuple(field.name for field in fields(GalleryEntry)) == (
        "profile",
        "sizing_policy_factory",
        "layout_policy_factory",
    )
    for entry in entries:
        architecture_name, package_name, profile_hash = _EXPECTED[entry.id]
        assert entry.profile.name == architecture_name
        assert entry.profile.profile_hash == profile_hash
        assert (GALLERY_ROOT / package_name / "profile.yaml").is_file()
        assert not hasattr(entry, "to_dict")


def test_one_quantile_point_instantiates_every_architecture_policy() -> None:
    config = QuantileSizingConfig.uniform(0.37)

    assert config.compute_quantile == pytest.approx(0.37)
    assert config.magic_state_quantile == pytest.approx(0.37)
    assert config.default_store_load_quantile == pytest.approx(0.37)
    assert dict(config.store_load_quantiles_by_representation) == {}

    for entry in list_gallery_entries():
        sizing = entry.make_sizing_policy(config)
        layout = entry.make_layout_policy()
        assert isinstance(sizing, SizingPolicy)
        assert isinstance(layout, LogicalLayoutPolicy)
        assert sizing.magic_state_quantile == pytest.approx(0.37)
        if hasattr(sizing, "compute_quantile"):
            assert sizing.compute_quantile == pytest.approx(0.37)
        if hasattr(sizing, "default_store_load_quantile"):
            assert sizing.default_store_load_quantile == pytest.approx(0.37)


def test_reference_baseline_quantiles_have_one_typed_owner() -> None:
    config = QuantileSizingConfig.reference_baseline()

    assert config.compute_quantile == 0.50
    assert config.magic_state_quantile == 0.60
    assert config.default_store_load_quantile == 0.95
    assert dict(config.store_load_quantiles_by_representation) == {
        "clifford_t": 0.95,
        "pbc": 0.80,
    }
    assert config.compute_rounding == "floor"
    assert config.buffer_rounding == "ceil"


@pytest.mark.parametrize("prefix", ("arqsim.",))
def test_gallery_prefix_compatibility_is_only_a_lookup_convenience(
    prefix: str,
) -> None:
    assert get_gallery_entry(f"{prefix}2.3") is get_gallery_entry("2.3")
    assert get_gallery_entry(f"{prefix}2.3").profile.id == "2.3"


def test_generic_architecture_modules_do_not_import_the_gallery() -> None:
    for relative_path in (
        "profile.py",
        "sizing.py",
        "logical_layout.py",
        "logical_layout_policy.py",
        "construction.py",
        "resolver.py",
    ):
        source = (ARCHITECTURE_ROOT / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any("gallery" in module for module in imported)


def test_horizontal_policy_and_flat_profile_directories_are_gone() -> None:
    assert not (ARCHITECTURE_ROOT / "templates").exists()
    assert not (ARCHITECTURE_ROOT / "profile_sizing").exists()
    assert not (ARCHITECTURE_ROOT / "profile_layouts").exists()
