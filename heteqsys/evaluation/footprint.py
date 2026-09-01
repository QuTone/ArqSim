"""Physical-footprint accounting over canonical architecture facts."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any, Mapping

from heteqsys.architecture.specification import (
    ArchitectureSpecification,
    QECBinding,
    Submodule,
)
from heteqsys.qec.protocol import (
    EntanglementDistillationProfile,
    MagicStateFactoryProfile,
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
)

from heteqsys.schema import deep_freeze_json
from heteqsys.schema import normalize_json as normalize_json_value
from heteqsys.schema import semantic_hash, strict_json


FOOTPRINT_MODEL_SCHEMA_VERSION = "heteqsys.physical-footprint-model.v1"
FOOTPRINT_ESTIMATE_SCHEMA_VERSION = "arqsim.physical-footprint-estimate.v2"
RESOURCE_BUDGET_SCHEMA_VERSION = "heteqsys.resource-budget.v1"

_SURFACE_CAPACITY_KEYS = frozenset(
    {
        "logical_patches",
        "logical_magic_states",
        "logical_bell_pairs",
        "communication_qubits",
    }
)


def _positive_mapping(values: Mapping[str, float], *, label: str) -> dict[str, float]:
    if not isinstance(values, Mapping):
        raise TypeError(f"{label} must be a mapping")
    if any(
        not isinstance(value, (int, float)) or isinstance(value, bool)
        for value in values.values()
    ):
        raise TypeError(f"{label} values must be real numbers")
    result = {str(key): float(value) for key, value in values.items()}
    if not result or any(
        not math.isfinite(value) or value <= 0 for value in result.values()
    ):
        raise ValueError(f"{label} needs positive finite values")
    return dict(sorted(result.items()))


@dataclass(frozen=True)
class PhysicalFootprintModel:
    """Rules that translate structural capacities into physical qubits.

    The model is an evaluation input, not part of the architecture. This
    keeps code/layout structure separate from reference- or hardware-specific area
    assumptions. ``factory_qubits_per_copy`` remains only for report-v1 model
    codec compatibility; a pinned QEC resource-protocol profile owns the
    factory's intrinsic physical-qubit count.
    """

    model_id: str
    surface_patch_coefficient: float
    surface_modality_multiplier: Mapping[str, float]
    surface_capacity_multiplier: Mapping[str, float]
    bb_code_qubit_multiplier: Mapping[str, float]
    bb_controller_qubits_per_block: Mapping[str, float]
    factory_qubits_per_copy: Mapping[str, float]
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str):
            raise TypeError("Footprint model ID must be a string")
        if not isinstance(self.surface_patch_coefficient, (int, float)) or isinstance(
            self.surface_patch_coefficient, bool
        ):
            raise TypeError("surface_patch_coefficient must be a real number")
        if not isinstance(self.provenance, Mapping):
            raise TypeError("Footprint-model provenance must be a mapping")
        model_id = self.model_id.strip()
        coefficient = float(self.surface_patch_coefficient)
        if not model_id:
            raise ValueError("Footprint model ID cannot be empty")
        if not math.isfinite(coefficient) or coefficient <= 0:
            raise ValueError("surface_patch_coefficient must be positive and finite")
        surface_keys = _positive_mapping(
            self.surface_capacity_multiplier,
            label="surface_capacity_multiplier",
        )
        missing = sorted(_SURFACE_CAPACITY_KEYS - set(surface_keys))
        if missing:
            raise ValueError(f"Footprint model is missing capacity rules: {missing}")
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "surface_patch_coefficient", coefficient)
        object.__setattr__(
            self,
            "surface_modality_multiplier",
            deep_freeze_json(_positive_mapping(
                self.surface_modality_multiplier,
                label="surface_modality_multiplier",
            )),
        )
        object.__setattr__(
            self, "surface_capacity_multiplier", deep_freeze_json(surface_keys)
        )
        object.__setattr__(
            self,
            "bb_code_qubit_multiplier",
            deep_freeze_json(_positive_mapping(
                self.bb_code_qubit_multiplier,
                label="bb_code_qubit_multiplier",
            )),
        )
        object.__setattr__(
            self,
            "bb_controller_qubits_per_block",
            deep_freeze_json(_positive_mapping(
                self.bb_controller_qubits_per_block,
                label="bb_controller_qubits_per_block",
            )),
        )
        object.__setattr__(
            self,
            "factory_qubits_per_copy",
            deep_freeze_json(_positive_mapping(
                self.factory_qubits_per_copy,
                label="factory_qubits_per_copy",
            )),
        )
        object.__setattr__(
            self,
            "provenance",
            deep_freeze_json(
                strict_json(self.provenance, label="footprint-model provenance")
            ),
        )

    @classmethod
    def reference_v1(
        cls,
        *,
        factory_qubits_per_copy: float = 463.0,
    ) -> "PhysicalFootprintModel":
        """Return the named space assumptions used by the legacy evaluator.

        This is a provenance-carrying compatibility model, not a universal
        hardware constant. Callers must select it explicitly.
        """

        return cls(
            model_id="reference-space-v1",
            surface_patch_coefficient=2.0,
            surface_modality_multiplier={
                "neutral_atom": 1.0,
                "superconducting": 4.0,
            },
            surface_capacity_multiplier={
                key: 1.0 for key in sorted(_SURFACE_CAPACITY_KEYS)
            },
            bb_code_qubit_multiplier={
                "neutral_atom": 2.0,
                "superconducting": 2.0,
            },
            bb_controller_qubits_per_block={
                "neutral_atom": 158.0,
                "superconducting": 158.0,
            },
            factory_qubits_per_copy={
                "neutral_atom": factory_qubits_per_copy,
                "superconducting": factory_qubits_per_copy,
            },
            provenance={
                "kind": "legacy_compatibility",
                "source": "heteqsys.evaluation.footprint.estimate_physical_footprint",
                "surface_rule": "(2*d^2 - 1) with modality multiplier",
                "bb_rule": "ceil(logical_patches/k) * (2*n + 158)",
                "factory_rule": (
                    "pinned QEC resource protocol; legacy model field is "
                    "consistency-checked for report-v1 codec parity"
                ),
            },
        )

    @property
    def model_hash(self) -> str:
        return semantic_hash(self.to_dict(include_model_hash=False))

    def to_dict(self, *, include_model_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": FOOTPRINT_MODEL_SCHEMA_VERSION,
            "model_id": self.model_id,
            "surface_patch_coefficient": self.surface_patch_coefficient,
            "surface_modality_multiplier": dict(self.surface_modality_multiplier),
            "surface_capacity_multiplier": dict(self.surface_capacity_multiplier),
            "bb_code_qubit_multiplier": dict(self.bb_code_qubit_multiplier),
            "bb_controller_qubits_per_block": dict(
                self.bb_controller_qubits_per_block
            ),
            "factory_qubits_per_copy": dict(self.factory_qubits_per_copy),
            "provenance": normalize_json_value(self.provenance),
        }
        if include_model_hash:
            result["model_hash"] = self.model_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PhysicalFootprintModel":
        allowed = {
            "schema_version",
            "model_id",
            "surface_patch_coefficient",
            "surface_modality_multiplier",
            "surface_capacity_multiplier",
            "bb_code_qubit_multiplier",
            "bb_controller_qubits_per_block",
            "factory_qubits_per_copy",
            "provenance",
            "model_hash",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(
                f"Unknown physical-footprint model fields: {sorted(unknown)}"
            )
        schema = data.get("schema_version")
        if schema not in (None, FOOTPRINT_MODEL_SCHEMA_VERSION):
            raise ValueError(f"Unsupported physical-footprint model schema: {schema}")
        result = cls(
            model_id=data["model_id"],
            surface_patch_coefficient=data["surface_patch_coefficient"],
            surface_modality_multiplier=data["surface_modality_multiplier"],
            surface_capacity_multiplier=data["surface_capacity_multiplier"],
            bb_code_qubit_multiplier=data["bb_code_qubit_multiplier"],
            bb_controller_qubits_per_block=data[
                "bb_controller_qubits_per_block"
            ],
            factory_qubits_per_copy=data["factory_qubits_per_copy"],
            provenance=data.get("provenance", {}),
        )
        expected_hash = data.get("model_hash")
        if expected_hash is not None and expected_hash != result.model_hash:
            raise ValueError("Physical-footprint model hash does not match content")
        return result


@dataclass(frozen=True)
class FootprintComponent:
    owner_id: str
    module_id: str
    submodule_id: str
    module_type: str
    submodule_type: str
    payload: str
    modality: str | None
    qec_code: str | None
    capacity: float
    physical_qubits: float
    rule: str
    endpoint_node_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "owner_id",
            "module_id",
            "submodule_id",
            "module_type",
            "submodule_type",
            "payload",
            "rule",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Footprint component {name} must be non-empty")
        for name in ("modality", "qec_code", "endpoint_node_id"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
            ):
                raise ValueError(
                    f"Footprint component {name} must be non-empty or None"
                )
        capacity = float(self.capacity)
        physical_qubits = float(self.physical_qubits)
        if not math.isfinite(capacity) or capacity < 0:
            raise ValueError("Footprint component capacity must be finite and non-negative")
        if not math.isfinite(physical_qubits) or physical_qubits < 0:
            raise ValueError(
                "Footprint component physical qubits must be finite and non-negative"
            )
        details = strict_json(self.details, label="footprint component details")
        if not isinstance(details, Mapping):
            raise TypeError("Footprint component details must be a mapping")
        object.__setattr__(self, "capacity", capacity)
        object.__setattr__(self, "physical_qubits", physical_qubits)
        object.__setattr__(self, "details", deep_freeze_json(details))

    @property
    def qualified_submodule(self) -> str:
        return f"{self.owner_id}/{self.module_id}/{self.submodule_id}"

    def to_dict(self) -> dict[str, Any]:
        result = {
            "owner": self.owner_id,
            "module": self.module_id,
            "submodule": self.submodule_id,
            "module_type": self.module_type,
            "submodule_type": self.submodule_type,
            "payload": self.payload,
            "modality": self.modality,
            "qec_code": self.qec_code,
            "capacity": self.capacity,
            "physical_qubits": self.physical_qubits,
            "rule": self.rule,
            "details": normalize_json_value(self.details),
        }
        if self.endpoint_node_id is not None:
            result["endpoint_node"] = self.endpoint_node_id
        return result


@dataclass(frozen=True)
class PhysicalFootprintEstimate:
    architecture_hash: str
    model_id: str
    model_hash: str
    components: tuple[FootprintComponent, ...]
    checks: Mapping[str, bool]

    @property
    def total_physical_qubits(self) -> float:
        return sum(component.physical_qubits for component in self.components)

    @property
    def estimate_hash(self) -> str:
        return semantic_hash(self.to_dict(include_estimate_hash=False))

    def to_dict(self, *, include_estimate_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": FOOTPRINT_ESTIMATE_SCHEMA_VERSION,
            "architecture_hash": self.architecture_hash,
            "model_id": self.model_id,
            "model_hash": self.model_hash,
            "total_physical_qubits": self.total_physical_qubits,
            "components": [component.to_dict() for component in self.components],
            "checks": dict(self.checks),
        }
        if include_estimate_hash:
            result["estimate_hash"] = self.estimate_hash
        return result

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True) + "\n"


def _capacity_unit(submodule: Submodule) -> str:
    key = (submodule.type, submodule.payload)
    try:
        return {
            ("region", "logical_qubit"): "logical_patches",
            ("buffer", "logical_qubit"): "logical_patches",
            ("buffer", "magic_state"): "logical_magic_states",
            ("buffer", "bell_pair"): "logical_bell_pairs",
        }[key]
    except KeyError as exc:
        raise ValueError(
            f"No physical-capacity unit for {submodule.type}/{submodule.payload}"
        ) from exc


def _surface_cost(
    quantity: float,
    binding: QECBinding,
    modality: str,
    capacity_unit: str,
    model: PhysicalFootprintModel,
) -> tuple[float, dict[str, Any]]:
    if capacity_unit not in _SURFACE_CAPACITY_KEYS:
        raise ValueError(f"Unsupported surface-code capacity unit: {capacity_unit}")
    try:
        distance = float(binding.parameters["distance"])
        modality_multiplier = model.surface_modality_multiplier[modality]
        capacity_multiplier = model.surface_capacity_multiplier[capacity_unit]
    except KeyError as exc:
        raise ValueError(
            f"Incomplete surface footprint inputs for modality {modality}"
        ) from exc
    base_patch_qubits = model.surface_patch_coefficient * distance**2 - 1.0
    unit_qubits = base_patch_qubits * modality_multiplier * capacity_multiplier
    return quantity * unit_qubits, {
        "capacity_unit": capacity_unit,
        "distance": distance,
        "base_patch_physical_qubits": base_patch_qubits,
        "physical_qubits_per_unit": unit_qubits,
    }


def _bb_cost(
    quantity: float,
    binding: QECBinding,
    modality: str,
    capacity_unit: str,
    model: PhysicalFootprintModel,
) -> tuple[float, dict[str, Any]]:
    if capacity_unit != "logical_patches":
        raise ValueError(f"Unsupported BB-code capacity unit: {capacity_unit}")
    try:
        n = float(binding.parameters["n"])
        k = float(binding.parameters["k"])
        code_multiplier = model.bb_code_qubit_multiplier[modality]
        controller = model.bb_controller_qubits_per_block[modality]
    except KeyError as exc:
        raise ValueError(
            f"Incomplete BB footprint inputs for modality {modality}"
        ) from exc
    blocks = math.ceil(quantity / k) if quantity else 0
    block_qubits = n * code_multiplier + controller
    return blocks * block_qubits, {
        "capacity_unit": capacity_unit,
        "n": n,
        "k": k,
        "blocks": blocks,
        "physical_qubits_per_block": block_qubits,
    }


def _qec_cost(
    quantity: float,
    binding: QECBinding,
    modality: str,
    capacity_unit: str,
    model: PhysicalFootprintModel,
) -> tuple[float, str, dict[str, Any]]:
    if binding.code == "surface":
        cost, details = _surface_cost(
            quantity, binding, modality, capacity_unit, model
        )
        return cost, "surface_patch", details
    if binding.code == "bb":
        cost, details = _bb_cost(
            quantity, binding, modality, capacity_unit, model
        )
        return cost, "bb_code_block", details
    raise ValueError(f"No footprint rule for QEC code {binding.code}")


def _magic_profile(submodule: Submodule) -> MagicStateFactoryProfile:
    protocol_ref = submodule.resource_protocol
    if protocol_ref is None:
        raise ValueError(f"Magic-state engine {submodule.id} has no protocol reference")
    profile = get_magic_state_factory_profile(protocol_ref.id)
    if protocol_ref.profile_hash != profile.profile_hash:
        raise ValueError(f"Magic-state protocol hash mismatch for {submodule.id}")
    if profile.resource_payload != submodule.payload:
        raise ValueError(f"Magic-state protocol payload mismatch for {submodule.id}")
    return profile


def _bell_profile(submodule: Submodule) -> EntanglementDistillationProfile:
    protocol_ref = submodule.resource_protocol
    if protocol_ref is None:
        raise ValueError(f"Logical-Bell engine {submodule.id} has no protocol reference")
    profile = get_entanglement_distillation_profile(protocol_ref.id)
    if protocol_ref.profile_hash != profile.profile_hash:
        raise ValueError(f"Logical-Bell protocol hash mismatch for {submodule.id}")
    if profile.resource_payload != submodule.payload:
        raise ValueError(f"Logical-Bell protocol payload mismatch for {submodule.id}")
    return profile


def _endpoint_qec(
    specification: ArchitectureSpecification,
    endpoint: str,
) -> tuple[str, str, QECBinding]:
    node_id, module_id, submodule_id = endpoint.split("/")
    node = specification.node(node_id)
    submodule = specification.submodule(node_id, module_id, submodule_id)
    if submodule.qec is None:
        raise ValueError(f"Interconnect endpoint {endpoint} needs a QEC binding")
    return node.id, node.modality, submodule.qec


def _node_components(
    specification: ArchitectureSpecification,
    model: PhysicalFootprintModel,
) -> list[FootprintComponent]:
    components: list[FootprintComponent] = []
    for node in specification.nodes:
        for module in node.modules:
            for submodule in module.submodules:
                quantity = float(submodule.capacity)
                if submodule.type == "engine":
                    if submodule.payload != "magic_state":
                        raise ValueError(
                            "Node-owned resource engines currently support only "
                            "magic-state production"
                        )
                    profile = _magic_profile(submodule)
                    stale_value = model.factory_qubits_per_copy.get(node.modality)
                    if stale_value is not None and not math.isclose(
                        float(stale_value),
                        float(profile.physical_qubits_per_copy),
                        rel_tol=0.0,
                        abs_tol=0.0,
                    ):
                        raise ValueError(
                            "PhysicalFootprintModel.factory_qubits_per_copy is a "
                            "legacy codec field and disagrees with pinned protocol "
                            f"{profile.id}"
                        )
                    physical_qubits = quantity * profile.physical_qubits_per_copy
                    rule = "factory_copy"
                    details = {
                        "capacity_unit": "copies",
                        "protocol": profile.id,
                        "protocol_profile_hash": profile.profile_hash,
                        "physical_qubits_per_copy": profile.physical_qubits_per_copy,
                        "physical_qubits_source": "pinned_qec_resource_protocol",
                    }
                    qec_code = None
                else:
                    if submodule.qec is None:
                        raise ValueError(
                            f"Capacity-bearing Submodule {node.id}/{module.id}/"
                            f"{submodule.id} needs a QEC binding"
                        )
                    capacity_unit = _capacity_unit(submodule)
                    physical_qubits, rule, details = _qec_cost(
                        quantity,
                        submodule.qec,
                        node.modality,
                        capacity_unit,
                        model,
                    )
                    qec_code = submodule.qec.code
                components.append(
                    FootprintComponent(
                        owner_id=node.id,
                        module_id=module.id,
                        submodule_id=submodule.id,
                        module_type=module.type,
                        submodule_type=submodule.type,
                        payload=submodule.payload,
                        modality=node.modality,
                        qec_code=qec_code,
                        capacity=quantity,
                        physical_qubits=physical_qubits,
                        rule=rule,
                        details=details,
                    )
                )
    return components


def _interconnect_components(
    specification: ArchitectureSpecification,
    model: PhysicalFootprintModel,
) -> list[FootprintComponent]:
    components: list[FootprintComponent] = []
    for interconnect in specification.interconnects:
        if len(interconnect.endpoints) != 2:
            raise ValueError(
                "Physical Bell accounting currently requires two Interconnect endpoints"
            )
        all_submodules = tuple(
            (module, submodule)
            for module in interconnect.modules
            for submodule in module.submodules
        )
        engines = tuple(
            (module, submodule)
            for module, submodule in all_submodules
            if submodule.type == "engine" and submodule.payload == "bell_pair"
        )
        buffers = tuple(
            (module, submodule)
            for module, submodule in all_submodules
            if submodule.type == "buffer" and submodule.payload == "bell_pair"
        )
        if (
            len(engines) != 1
            or len(buffers) != 1
            or len(all_submodules) != 2
        ):
            raise ValueError(
                f"Interconnect {interconnect.id} needs one Bell engine and one buffer"
            )
        engine_module, engine = engines[0]
        buffer_module, buffer = buffers[0]
        profile = _bell_profile(engine)
        copies = float(engine.capacity)
        pair_capacity = float(buffer.capacity)
        for endpoint in interconnect.endpoints:
            endpoint_node, modality, endpoint_binding = _endpoint_qec(
                specification, endpoint
            )
            per_copy = profile.physical_qubits_per_copy_per_endpoint
            if per_copy is not None:
                engine_qubits = copies * per_copy
                engine_rule = "logical_bell_protocol_endpoint"
                engine_details = {
                    "capacity_unit": "copies",
                    "protocol": profile.id,
                    "protocol_profile_hash": profile.profile_hash,
                    "copies": int(copies),
                    "physical_qubits_per_copy_per_endpoint": per_copy,
                    "physical_qubits_source": "pinned_qec_resource_protocol",
                }
            else:
                logical_workspace = (
                    copies * profile.logical_qubits_per_copy_per_endpoint
                )
                engine_qubits, _, engine_details = _qec_cost(
                    logical_workspace,
                    endpoint_binding,
                    modality,
                    "logical_patches",
                    model,
                )
                engine_rule = "logical_bell_endpoint_qec_workspace"
                engine_details = {
                    **engine_details,
                    "protocol": profile.id,
                    "protocol_profile_hash": profile.profile_hash,
                    "copies": int(copies),
                    "logical_qubits_per_copy_per_endpoint": (
                        profile.logical_qubits_per_copy_per_endpoint
                    ),
                }
            components.append(
                FootprintComponent(
                    owner_id=interconnect.id,
                    module_id=engine_module.id,
                    submodule_id=engine.id,
                    module_type=engine_module.type,
                    submodule_type=engine.type,
                    payload=engine.payload,
                    modality=modality,
                    qec_code=(profile.qec_code or endpoint_binding.code),
                    capacity=copies,
                    physical_qubits=engine_qubits,
                    rule=engine_rule,
                    endpoint_node_id=endpoint_node,
                    details=engine_details,
                )
            )
            buffer_qubits, buffer_rule, buffer_details = _qec_cost(
                pair_capacity,
                endpoint_binding,
                modality,
                "logical_bell_pairs",
                model,
            )
            components.append(
                FootprintComponent(
                    owner_id=interconnect.id,
                    module_id=buffer_module.id,
                    submodule_id=buffer.id,
                    module_type=buffer_module.type,
                    submodule_type=buffer.type,
                    payload=buffer.payload,
                    modality=modality,
                    qec_code=endpoint_binding.code,
                    capacity=pair_capacity,
                    physical_qubits=buffer_qubits,
                    rule=buffer_rule,
                    endpoint_node_id=endpoint_node,
                    details={
                        **buffer_details,
                        "shared_pair_capacity": int(pair_capacity),
                    },
                )
            )
    return components


def estimate_physical_footprint(
    specification: ArchitectureSpecification,
    model: PhysicalFootprintModel,
) -> PhysicalFootprintEstimate:
    """Account for canonical capacities without materializing legacy Modules."""

    if not isinstance(specification, ArchitectureSpecification):
        raise TypeError("specification must be an ArchitectureSpecification")
    if not isinstance(model, PhysicalFootprintModel):
        raise TypeError("model must be a PhysicalFootprintModel")
    components = [
        *_node_components(specification, model),
        *_interconnect_components(specification, model),
    ]
    keys = [
        (
            component.owner_id,
            component.module_id,
            component.submodule_id,
            component.endpoint_node_id,
        )
        for component in components
    ]
    total = sum(component.physical_qubits for component in components)
    expected_components = sum(
        len(module.submodules)
        for node in specification.nodes
        for module in node.modules
    ) + sum(
        len(interconnect.endpoints)
        * sum(len(module.submodules) for module in interconnect.modules)
        for interconnect in specification.interconnects
    )
    checks = {
        "all_capacity_entries_accounted": len(components) == expected_components,
        "all_component_costs_non_negative": all(
            component.physical_qubits >= 0 for component in components
        ),
        "component_keys_unique": len(keys) == len(set(keys)),
        "finite_positive_total": math.isfinite(total) and total > 0,
    }
    result = PhysicalFootprintEstimate(
        architecture_hash=specification.architecture_hash,
        model_id=model.model_id,
        model_hash=model.model_hash,
        components=tuple(components),
        checks=checks,
    )
    if not all(checks.values()):
        raise ValueError(f"Physical-footprint checks failed: {checks}")
    return result


@dataclass(frozen=True)
class ResourceBudgetSpec:
    budget_id: str
    limit_physical_qubits: float
    tolerance_physical_qubits: float = 1e-9
    minimum_utilization: float = 0.0
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        limit = float(self.limit_physical_qubits)
        tolerance = float(self.tolerance_physical_qubits)
        minimum = float(self.minimum_utilization)
        if not self.budget_id.strip():
            raise ValueError("Budget ID cannot be empty")
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError("Physical-qubit budget must be positive and finite")
        if not math.isfinite(tolerance) or tolerance < 0:
            raise ValueError("Budget tolerance must be finite and non-negative")
        if not 0 <= minimum <= 1:
            raise ValueError("minimum_utilization must be in [0, 1]")
        object.__setattr__(self, "budget_id", self.budget_id.strip())
        object.__setattr__(self, "limit_physical_qubits", limit)
        object.__setattr__(self, "tolerance_physical_qubits", tolerance)
        object.__setattr__(self, "minimum_utilization", minimum)
        object.__setattr__(self, "provenance", normalize_json_value(self.provenance))

    @property
    def budget_hash(self) -> str:
        return semantic_hash(self.to_dict(include_budget_hash=False))

    def to_dict(self, *, include_budget_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": RESOURCE_BUDGET_SCHEMA_VERSION,
            "budget_id": self.budget_id,
            "metric": "physical_qubits",
            "limit_physical_qubits": self.limit_physical_qubits,
            "tolerance_physical_qubits": self.tolerance_physical_qubits,
            "minimum_utilization": self.minimum_utilization,
            "provenance": normalize_json_value(self.provenance),
        }
        if include_budget_hash:
            result["budget_hash"] = self.budget_hash
        return result


@dataclass(frozen=True)
class BudgetAssessment:
    budget: ResourceBudgetSpec
    footprint: PhysicalFootprintEstimate

    @property
    def used_physical_qubits(self) -> float:
        return self.footprint.total_physical_qubits

    @property
    def slack_physical_qubits(self) -> float:
        return self.budget.limit_physical_qubits - self.used_physical_qubits

    @property
    def utilization(self) -> float:
        return self.used_physical_qubits / self.budget.limit_physical_qubits

    @property
    def feasible(self) -> bool:
        return self.slack_physical_qubits >= -self.budget.tolerance_physical_qubits

    @property
    def utilization_satisfied(self) -> bool:
        return self.utilization + 1e-15 >= self.budget.minimum_utilization

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget_id": self.budget.budget_id,
            "budget_hash": self.budget.budget_hash,
            "footprint_hash": self.footprint.estimate_hash,
            "used_physical_qubits": self.used_physical_qubits,
            "limit_physical_qubits": self.budget.limit_physical_qubits,
            "slack_physical_qubits": self.slack_physical_qubits,
            "utilization": self.utilization,
            "feasible": self.feasible,
            "utilization_satisfied": self.utilization_satisfied,
        }


def assess_resource_budget(
    footprint: PhysicalFootprintEstimate,
    budget: ResourceBudgetSpec,
) -> BudgetAssessment:
    return BudgetAssessment(budget=budget, footprint=footprint)
