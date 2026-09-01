"""Build canonical Architecture Specifications from bundled gallery designs.

This module is the small public convenience boundary for ArqSim's bundled
architectures. It selects one gallery entry, translates the experiment's
quantile point and explicit sensitivity overrides into that entry's typed
policies, and delegates to the generic architecture constructor.

The returned object is the canonical immutable static architecture. Compiler
layouts, physical-footprint estimates, runtime protocol bindings, execution
plans, and report-v1 projections are independent downstream artifacts and are
never attached here.
"""

from __future__ import annotations

from dataclasses import replace
import itertools
from typing import Any, Iterable, Mapping

from heteqsys.architecture.construction import construct_architecture
from heteqsys.architecture.errors import UnsupportedArchitectureError
from heteqsys.architecture.gallery import (
    GalleryEntry,
    QuantileSizingConfig,
    get_gallery_entry,
)
from heteqsys.architecture.identifiers import SubmoduleKey
from heteqsys.architecture.logical_layout import LogicalLayoutRequest
from heteqsys.architecture.profile import ArchitectureProfile
from heteqsys.architecture.sizing import SizingPolicy
from heteqsys.architecture.specification import (
    ArchitectureSpecification,
    QECBinding,
)
from heteqsys.program import FTCircuit
from heteqsys.qec.protocol import (
    EntanglementDistillationProfile,
    MagicStateFactoryProfile,
    QECResourceProtocolProfile,
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
)


DEFAULT_MAGIC_STATE_PROTOCOL_ID = "cultivation-d5-d15-p1e3"
DEFAULT_BELL_PROTOCOL_ID = "boosting-dbell9-ds19-pbell1e2"

_MAGIC_STATE_PROTOCOL_ALIASES: Mapping[str, str] = {
    "cultivation": DEFAULT_MAGIC_STATE_PROTOCOL_ID,
}

_QUANTILE_OVERRIDE_KEYS = frozenset(
    {
        "quantiles.compute",
        "quantiles.compute_rounding",
        "quantiles.store_load.clifford_t",
        "quantiles.store_load.pbc",
        "quantiles.store_load.default",
        "quantiles.magic_state",
        "quantiles.buffer_rounding",
    }
)
_RECIPE_OVERRIDE_KEYS = frozenset({"limits.compute_fraction", "cold_start"})
_CAPACITY_OVERRIDE_KEYS = frozenset(
    {
        "protocols.store_load.buffer_capacity",
        "protocols.magic_state.buffer_capacity",
        "protocols.magic_state.copies",
        "protocols.entanglement_distillation.buffer_capacity",
        "protocols.entanglement_distillation.copies",
    }
)
_MAGIC_INTRINSIC_FIELDS = {
    "outputs_per_copy_per_batch": "outputs_per_batch",
    "physical_qubits_per_copy": "physical_qubits_per_copy",
    "qec_cycles_per_batch": "cycles_per_batch",
}
_BELL_INTRINSIC_FIELDS = {
    "outputs_per_copy_per_batch": "outputs_per_batch",
    "logical_qubits_per_copy_per_endpoint": (
        "logical_qubits_per_copy_per_endpoint"
    ),
    "physical_qubits_per_copy_per_endpoint": (
        "physical_qubits_per_copy_per_endpoint"
    ),
    "qec_cycles_per_batch": "qec_cycles_per_batch",
    "qec_cycle_time_s": "qec_cycle_time_s",
    "raw_bell_pairs_per_output": "raw_bell_pairs_per_output",
    "reference_physical_bell_pair_rate_per_s": (
        "reference_physical_bell_pair_rate_per_s"
    ),
}
_PROTOCOL_ID_KEYS = frozenset(
    {
        "protocols.magic_state.id",
        "protocols.entanglement_distillation.id",
    }
)
_INTRINSIC_OVERRIDE_KEYS = frozenset(
    {
        *(f"protocols.magic_state.{name}" for name in _MAGIC_INTRINSIC_FIELDS),
        *(
            f"protocols.entanglement_distillation.{name}"
            for name in _BELL_INTRINSIC_FIELDS
        ),
    }
)
_KNOWN_OVERRIDE_KEYS = (
    _QUANTILE_OVERRIDE_KEYS
    | _RECIPE_OVERRIDE_KEYS
    | _CAPACITY_OVERRIDE_KEYS
    | _PROTOCOL_ID_KEYS
    | _INTRINSIC_OVERRIDE_KEYS
)


def _normalized_overrides(
    overrides: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if overrides is None:
        return {}
    if not isinstance(overrides, Mapping):
        raise TypeError("policy_overrides must be a mapping or None")
    result: dict[str, Any] = {}
    for key, value in overrides.items():
        if not isinstance(key, str) or not key or key != key.strip():
            raise ValueError("policy override keys must be non-empty strings")
        if key not in _KNOWN_OVERRIDE_KEYS:
            raise ValueError(f"Unknown sizing-policy override: {key}")
        result[key] = value
    return result


def _quantile_config(
    base: QuantileSizingConfig,
    overrides: Mapping[str, Any],
) -> QuantileSizingConfig:
    if not isinstance(base, QuantileSizingConfig):
        raise TypeError("quantile_config must be a QuantileSizingConfig")
    store_load = dict(base.store_load_quantiles_by_representation)
    if "quantiles.store_load.clifford_t" in overrides:
        store_load["clifford_t"] = overrides[
            "quantiles.store_load.clifford_t"
        ]
    if "quantiles.store_load.pbc" in overrides:
        store_load["pbc"] = overrides["quantiles.store_load.pbc"]
    return QuantileSizingConfig(
        compute_quantile=overrides.get(
            "quantiles.compute", base.compute_quantile
        ),
        magic_state_quantile=overrides.get(
            "quantiles.magic_state", base.magic_state_quantile
        ),
        default_store_load_quantile=overrides.get(
            "quantiles.store_load.default",
            base.default_store_load_quantile,
        ),
        store_load_quantiles_by_representation=store_load,
        compute_rounding=overrides.get(
            "quantiles.compute_rounding", base.compute_rounding
        ),
        buffer_rounding=overrides.get(
            "quantiles.buffer_rounding", base.buffer_rounding
        ),
    )


def _strictly_equal(actual: Any, expected: Any) -> bool:
    """Compare scalar protocol facts without accepting bool/int aliases."""

    return type(actual) is type(expected) and bool(actual == expected)


def _validate_intrinsic_overrides(
    *,
    prefix: str,
    profile: QECResourceProtocolProfile,
    fields: Mapping[str, str],
    overrides: Mapping[str, Any],
) -> None:
    for public_name, attribute in fields.items():
        key = f"{prefix}.{public_name}"
        if key not in overrides:
            continue
        expected = getattr(profile, attribute)
        actual = overrides[key]
        if not _strictly_equal(actual, expected):
            raise ValueError(
                f"{key} is intrinsic to protocol {profile.id}: "
                f"expected {expected!r}, received {actual!r}"
            )


def _selected_catalog_profiles(
    overrides: Mapping[str, Any],
) -> tuple[MagicStateFactoryProfile, EntanglementDistillationProfile]:
    raw_magic_id = overrides.get(
        "protocols.magic_state.id", DEFAULT_MAGIC_STATE_PROTOCOL_ID
    )
    raw_bell_id = overrides.get(
        "protocols.entanglement_distillation.id", DEFAULT_BELL_PROTOCOL_ID
    )
    if not isinstance(raw_magic_id, str):
        raise TypeError("protocols.magic_state.id must be a string")
    if not isinstance(raw_bell_id, str):
        raise TypeError(
            "protocols.entanglement_distillation.id must be a string"
        )
    magic_id = _MAGIC_STATE_PROTOCOL_ALIASES.get(raw_magic_id, raw_magic_id)
    magic = get_magic_state_factory_profile(magic_id)
    bell = get_entanglement_distillation_profile(raw_bell_id)
    _validate_intrinsic_overrides(
        prefix="protocols.magic_state",
        profile=magic,
        fields=_MAGIC_INTRINSIC_FIELDS,
        overrides=overrides,
    )
    _validate_intrinsic_overrides(
        prefix="protocols.entanglement_distillation",
        profile=bell,
        fields=_BELL_INTRINSIC_FIELDS,
        overrides=overrides,
    )
    return magic, bell


def _profile_submodules(
    profile: ArchitectureProfile,
) -> tuple[tuple[SubmoduleKey, str, str, str], ...]:
    records: list[tuple[SubmoduleKey, str, str, str]] = []
    for owner in (*profile.nodes, *profile.interconnects):
        for module in owner.modules:
            for submodule in module.submodules:
                records.append(
                    (
                        SubmoduleKey(owner.id, module.id, submodule.id),
                        module.type,
                        submodule.type,
                        submodule.payload,
                    )
                )
    return tuple(records)


def _unique_targets(
    records: tuple[tuple[SubmoduleKey, str, str, str], ...],
    *,
    submodule_type: str,
    payload: str,
) -> tuple[SubmoduleKey, ...]:
    return tuple(
        target
        for target, _module_type, candidate_type, candidate_payload in records
        if candidate_type == submodule_type and candidate_payload == payload
    )


def _selected_qec_protocols(
    profile: ArchitectureProfile,
    magic: MagicStateFactoryProfile,
    bell: EntanglementDistillationProfile,
) -> Mapping[SubmoduleKey, QECResourceProtocolProfile]:
    records = _profile_submodules(profile)
    selected: dict[SubmoduleKey, QECResourceProtocolProfile] = {}
    magic_engines = _unique_targets(
        records, submodule_type="engine", payload="magic_state"
    )
    if len(magic_engines) != 1:
        raise ValueError(
            "A bundled gallery Profile must contain exactly one magic-state engine"
        )
    selected[magic_engines[0]] = magic
    bell_engines = _unique_targets(
        records, submodule_type="engine", payload="bell_pair"
    )
    if profile.interconnects and len(bell_engines) != 1:
        raise ValueError(
            "A multi-node bundled Profile must contain exactly one Bell engine"
        )
    if not profile.interconnects and bell_engines:
        raise ValueError("A local bundled Profile cannot contain a Bell engine")
    if bell_engines:
        selected[bell_engines[0]] = bell
    return selected


def _qec_bindings(profile: ArchitectureProfile) -> Mapping[SubmoduleKey, QECBinding]:
    """Bind code facts to Node-owned, slot-owning resources only."""

    bindings: dict[SubmoduleKey, QECBinding] = {}
    for node in profile.nodes:
        for module in node.modules:
            for submodule in module.submodules:
                if submodule.type == "engine":
                    continue
                target = SubmoduleKey(node.id, module.id, submodule.id)
                if (
                    module.type == "memory"
                    and submodule.type == "region"
                    and submodule.payload == "logical_qubit"
                ):
                    bindings[target] = QECBinding(
                        "bb", {"n": 288, "k": 12, "distance": 18}
                    )
                else:
                    bindings[target] = QECBinding(
                        "surface", {"distance": 13}
                    )
    return bindings


def _recipe_policy(
    policy: SizingPolicy,
    overrides: Mapping[str, Any],
) -> SizingPolicy:
    updates: dict[str, Any] = {}
    if "limits.compute_fraction" in overrides:
        if not hasattr(policy, "compute_fraction_limit"):
            raise ValueError(
                "limits.compute_fraction does not apply to this architecture"
            )
        updates["compute_fraction_limit"] = overrides[
            "limits.compute_fraction"
        ]
    if "cold_start" in overrides:
        if not hasattr(policy, "cold_start"):
            raise ValueError("cold_start does not apply to this architecture")
        updates["cold_start"] = overrides["cold_start"]
    return replace(policy, **updates) if updates else policy


def _sizing_overrides(
    profile: ArchitectureProfile,
    overrides: Mapping[str, Any],
) -> Mapping[SubmoduleKey, int]:
    records = _profile_submodules(profile)
    result: dict[SubmoduleKey, int] = {}

    def assign(
        key: str,
        *,
        submodule_type: str,
        payload: str,
        module_types: frozenset[str] | None = None,
    ) -> None:
        if key not in overrides or overrides[key] is None:
            return
        matches = tuple(
            target
            for target, module_type, candidate_type, candidate_payload in records
            if candidate_type == submodule_type
            and candidate_payload == payload
            and (module_types is None or module_type in module_types)
        )
        if not matches:
            raise ValueError(f"{key} does not apply to this architecture")
        for target in matches:
            result[target] = overrides[key]

    assign(
        "protocols.store_load.buffer_capacity",
        submodule_type="buffer",
        payload="logical_qubit",
        module_types=frozenset({"compute", "memory"}),
    )
    assign(
        "protocols.magic_state.buffer_capacity",
        submodule_type="buffer",
        payload="magic_state",
    )
    assign(
        "protocols.magic_state.copies",
        submodule_type="engine",
        payload="magic_state",
    )
    assign(
        "protocols.entanglement_distillation.buffer_capacity",
        submodule_type="buffer",
        payload="bell_pair",
    )
    assign(
        "protocols.entanglement_distillation.copies",
        submodule_type="engine",
        payload="bell_pair",
    )
    return result


def build_architecture_specification(
    circuit: FTCircuit,
    profile_id: str,
    *,
    magic_sizing_circuit: FTCircuit | None = None,
    quantile_config: QuantileSizingConfig | None = None,
    policy_overrides: Mapping[str, Any] | None = None,
    logical_layout: LogicalLayoutRequest | None = None,
) -> ArchitectureSpecification:
    """Resolve one bundled architecture directly into the canonical model.

    ``quantile_config`` is the experiment-wide demand point. Each gallery
    bundle interprets it through its own sizing relation. The dotted
    ``policy_overrides`` argument is retained as a strict sensitivity-input
    edge for the current API/CLI; it can change quantiles, supported recipe
    knobs, exact resource capacities, or select a catalog protocol, but it
    cannot replace catalog-owned intrinsic protocol facts.
    """

    if not isinstance(circuit, FTCircuit):
        raise TypeError("circuit must be an FTCircuit")
    if magic_sizing_circuit is not None and not isinstance(
        magic_sizing_circuit, FTCircuit
    ):
        raise TypeError("magic_sizing_circuit must be an FTCircuit or None")
    reference = magic_sizing_circuit or circuit
    if reference.num_qubits != circuit.num_qubits:
        raise ValueError(
            "The magic-state sizing circuit must use the same logical-qubit "
            "count as the evaluated circuit"
        )
    if logical_layout is not None and not isinstance(
        logical_layout, LogicalLayoutRequest
    ):
        raise TypeError("logical_layout must be a LogicalLayoutRequest or None")
    try:
        entry: GalleryEntry = get_gallery_entry(profile_id)
    except (KeyError, TypeError) as exc:
        raise UnsupportedArchitectureError(
            "Unknown bundled Architecture Profile",
            details={"profile": profile_id},
        ) from exc

    overrides = _normalized_overrides(policy_overrides)
    config = _quantile_config(
        quantile_config or QuantileSizingConfig.reference_baseline(), overrides
    )
    magic_protocol, bell_protocol = _selected_catalog_profiles(overrides)
    selected_protocols = _selected_qec_protocols(
        entry.profile, magic_protocol, bell_protocol
    )
    sizing_policy = _recipe_policy(
        entry.make_sizing_policy(config), overrides
    )
    return construct_architecture(
        entry.profile,
        circuit.statistics,
        sizing_policy,
        layout_policy=entry.make_layout_policy(),
        layout_request=logical_layout,
        reference_statistics=reference.statistics,
        sizing_overrides=_sizing_overrides(entry.profile, overrides),
        qec_bindings=_qec_bindings(entry.profile),
        selected_qec_protocols=selected_protocols,
    )


def sweep_architecture_specifications(
    circuit: FTCircuit,
    profile_id: str,
    parameter_grid: Mapping[str, Iterable[Any]],
    **kwargs: Any,
) -> tuple[ArchitectureSpecification, ...]:
    """Build the Cartesian product of explicit dotted sensitivity inputs."""

    if not isinstance(parameter_grid, Mapping):
        raise TypeError("parameter_grid must be a mapping")
    names = tuple(parameter_grid)
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError("parameter-grid keys must be non-empty strings")
    values = [tuple(parameter_grid[name]) for name in names]
    if any(not items for items in values):
        raise ValueError("Every sweep parameter needs at least one value")
    if "policy_overrides" in kwargs:
        raise TypeError(
            "sweep_architecture_specifications owns policy_overrides"
        )
    return tuple(
        build_architecture_specification(
            circuit,
            profile_id,
            policy_overrides=dict(zip(names, combination)),
            **kwargs,
        )
        for combination in itertools.product(*values)
    )


__all__ = [
    "DEFAULT_BELL_PROTOCOL_ID",
    "DEFAULT_MAGIC_STATE_PROTOCOL_ID",
    "ArchitectureSpecification",
    "build_architecture_specification",
    "sweep_architecture_specifications",
]
