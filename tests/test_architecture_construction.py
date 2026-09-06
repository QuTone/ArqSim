"""Focused ownership tests for canonical architecture construction."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest

import arqsim.architecture.construction as construction
import arqsim.specification as public_construction
from arqsim.architecture.gallery import (
    QuantileSizingConfig,
    get_gallery_entry,
)
from arqsim.architecture.identifiers import SubmoduleKey
from arqsim.architecture.logical_layout_policy import LogicalLayoutPolicy
from arqsim.architecture.profile import ArchitectureProfile
from arqsim.architecture.sizing import SizingPolicy
from arqsim.architecture.specification import ArchitectureSpecification, QECBinding
from arqsim.program.statistics import CircuitStatistics
from arqsim.qec import get_magic_state_factory_profile
from tests.architecture_semantic_oracle import oracle_circuit


PROJECT_ROOT = Path(__file__).parents[1]
CONSTRUCTION_PATH = (
    PROJECT_ROOT / "arqsim" / "architecture" / "construction.py"
)


def _targets(profile: ArchitectureProfile) -> tuple[SubmoduleKey, ...]:
    return tuple(
        SubmoduleKey(node.id, module.id, submodule.id)
        for node in profile.nodes
        for module in node.modules
        for submodule in module.submodules
    )


def _submodule_type(profile: ArchitectureProfile, target: SubmoduleKey) -> str:
    return next(
        submodule.type
        for node in profile.nodes
        if node.id == target.owner_id
        for module in node.modules
        if module.id == target.module_id
        for submodule in module.submodules
        if submodule.id == target.submodule_id
    )


def _construction_inputs() -> tuple[
    ArchitectureProfile,
    CircuitStatistics,
    SizingPolicy,
    LogicalLayoutPolicy,
    dict[SubmoduleKey, QECBinding],
    dict[SubmoduleKey, Any],
]:
    entry = get_gallery_entry("1.1")
    profile = entry.profile
    statistics = CircuitStatistics(
        representation="construction-test",
        logical_qubits=4,
        magic_states_per_layer=(0, 1),
        operation_qubits_by_layer=(((0, 1),), ((2,),)),
    )
    sizing_policy = entry.make_sizing_policy(QuantileSizingConfig(
        compute_quantile=0.50,
        magic_state_quantile=0.60,
        default_store_load_quantile=0.95,
        store_load_quantiles_by_representation={
            "clifford_t": 0.95,
            "pbc": 0.80,
        },
    ))
    qec_bindings = {
        target: QECBinding(code="surface", parameters={"distance": 13})
        for target in _targets(profile)
        if _submodule_type(profile, target) != "engine"
    }
    protocol = get_magic_state_factory_profile("cultivation-d5-d15-p1e3")
    selected_protocols = {
        target: protocol
        for target in _targets(profile)
        if _submodule_type(profile, target) == "engine"
    }
    return (
        profile,
        statistics,
        sizing_policy,
        entry.make_layout_policy(),
        qec_bindings,
        selected_protocols,
    )


def test_construct_architecture_runs_only_with_explicit_policies() -> None:
    (
        profile,
        statistics,
        sizing_policy,
        layout_policy,
        qec_bindings,
        selected_protocols,
    ) = _construction_inputs()
    result = construction.construct_architecture(
        profile,
        statistics,
        sizing_policy,
        layout_policy=layout_policy,
        reference_statistics=statistics,
        qec_bindings=qec_bindings,
        selected_qec_protocols=selected_protocols,
    )

    assert isinstance(result, ArchitectureSpecification)
    assert result.submodule(
        "na_node", "na_compute", "compute_region"
    ).capacity == 4
    assert result.submodule(
        "na_node", "na_msf", "factory_engine"
    ).resource_protocol is not None


def test_construct_architecture_accepts_explicit_policy_and_sizing_overrides(
) -> None:
    (
        profile,
        statistics,
        sizing_policy,
        selected_layout,
        qec_bindings,
        selected_protocols,
    ) = _construction_inputs()
    overrides = {
        target: 3
        for target in _targets(profile)
        if _submodule_type(profile, target) == "buffer"
    }
    result = construction.construct_architecture(
        profile,
        statistics,
        sizing_policy,
        layout_policy=selected_layout,
        sizing_overrides=overrides,
        qec_bindings=qec_bindings,
        selected_qec_protocols=selected_protocols,
    )

    assert {
        result.submodule(target.owner_id, target.module_id, target.submodule_id).capacity
        for target in overrides
    } == {3}


def test_construction_boundary_has_no_legacy_or_execution_dependencies() -> None:
    source = CONSTRUCTION_PATH.read_text(encoding="utf-8")
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

    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported
        for prefix in (
            "arqsim.compiler",
            "arqsim.evaluation",
            "arqsim.specification",
            "arqsim.architecture.gallery",
        )
    )
    for forbidden_name in (
        "FTCircuit",
        "QuantileLayoutPolicy",
        "WorkflowLayoutContext",
        "PhysicalFootprintModel",
    ):
        assert forbidden_name not in source

    assert tuple(inspect.signature(construction.construct_architecture).parameters) == (
        "profile",
        "statistics",
        "sizing_policy",
        "layout_policy",
        "layout_request",
        "reference_statistics",
        "sizing_overrides",
        "qec_bindings",
        "selected_qec_protocols",
    )


def test_public_builder_returns_the_canonical_specification_directly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    canonical = construction.construct_architecture

    def construct_spy(*args: Any, **kwargs: Any) -> ArchitectureSpecification:
        nonlocal calls
        calls += 1
        return canonical(*args, **kwargs)

    monkeypatch.setattr(
        public_construction,
        "construct_architecture",
        construct_spy,
    )
    specification = public_construction.build_architecture_specification(
        oracle_circuit(),
        "1.1",
    )

    assert calls == 1
    assert isinstance(specification, ArchitectureSpecification)
