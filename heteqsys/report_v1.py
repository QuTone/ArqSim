"""One-way plain-mapping renderer for ``evaluation-report.v1``.

The evaluator hands this boundary canonical architecture plus independent
compiler, footprint, protocol, execution, and analysis artifacts. This module
projects them into the deliberately redundant v1 wire document still consumed
by the API, CLI, fixtures, and frontend. Historical aliases such as ``D0`` and
compatibility hashes are output receipts only.

No legacy architecture or QEC class is imported or instantiated here, and no
mapping produced here may be parsed back into compilation or evaluation. The
large projection is intentionally quarantined until Report/API v2 replaces
the frozen v1 consumer surface retained by the Report/API-v2 cutover.
"""

from __future__ import annotations

from typing import Any, Mapping

from heteqsys._run_artifacts import EvaluationRunArtifacts
from heteqsys.architecture.profile import ArchitectureProfile
from heteqsys.architecture.specification import (
    ArchitectureSpecification,
    Interconnect,
    Module,
    Node,
    Submodule,
)
from heteqsys.compiler.models import LayoutSlot, LogicalLayout
from heteqsys.evaluation.footprint import (
    FootprintComponent,
    PhysicalFootprintEstimate,
)
from heteqsys.schema import (
    hash_and_freeze_json_document,
    normalize_json,
    semantic_hash,
)


EVALUATION_REPORT_SCHEMA_VERSION = "arqsim.evaluation-report.v1"
_EXECUTION_TRACE_V1_SCHEMA_VERSION = "arqsim.execution-trace.v1"

_SPECIFICATION_BUILD_SCHEMA_VERSION = "arqsim.specification-build.v2"
_PROFILE_V2_SCHEMA_VERSION = "arqsim.architecture-profile.v2"
_LAYOUT_PLAN_SCHEMA_VERSION = "arqsim.architecture-layout-plan.v1"
_LOGICAL_ARCHITECTURE_SCHEMA_VERSION = "arqsim.logical-architecture.v1"
_RESOLVED_SYSTEM_SCHEMA_VERSION = "heteqsys.resolved-ft-system.v1"
_QEC_SCHEMA_VERSION = "heteqsys.qec-configuration.v2"

_POLICY_V1_BASE: Mapping[str, Any] = {
    "schema_version": "arqsim.quantile-layout-policy.v1",
    "id": "reference-quantile-square-v1",
    "quantiles": {
        "compute": 0.5,
        "compute_rounding": "floor",
        "store_load": {"clifford_t": 0.95, "pbc": 0.8, "default": 0.95},
        "magic_state": 0.6,
        "buffer_rounding": "ceil",
    },
    "limits": {"compute_fraction": 0.4},
    "protocols": {
        "store_load": {"buffer_capacity": None},
        "magic_state": {
            "id": "cultivation-d5-d15-p1e3",
            "buffer_capacity": None,
            "copies": None,
            "outputs_per_copy_per_batch": 1,
            "physical_qubits_per_copy": 463,
            "qec_cycles_per_batch": 2187.680929963632,
        },
        "entanglement_distillation": {
            "id": "boosting-dbell9-ds19-pbell1e2",
            "copy_policy": "rate_matched_capped",
            "buffer_capacity": None,
            "transfer_demand_source": {
                "remote_magic": "magic_state_buffer",
                "remote_logical_data": "store_load_buffer",
            },
            "buffer_capacity_policy": "one_transfer_wave",
            "rate_demand_source": {
                "remote_magic": "aggregate_magic_state_factory_rate",
                "remote_logical_data": "reference_link_ceiling",
            },
            "copy_cap_policy": "one_transfer_wave",
            "copies": None,
            "outputs_per_copy_per_batch": 1,
            "logical_qubits_per_copy_per_endpoint": 1,
            "physical_qubits_per_copy_per_endpoint": 721,
            "qec_cycles_per_batch": 20.14,
            "qec_cycle_time_s": 0.001,
            "raw_bell_pairs_per_output": 85.86,
            "reference_physical_bell_pair_rate_per_s": "1.0e4",
        },
    },
    "timing": {
        "qec_cycle_time_s_by_modality": {
            "neutral_atom": 0.001,
            "superconducting": 0.000001,
        }
    },
    "cold_start": True,
    "qec": {
        "default": {"code": "surface", "parameters": {"distance": 13}},
        "rules": [
            {
                "selector": {
                    "module_role": "memory",
                    "submodule_role": "memory",
                },
                "code": "bb",
                "parameters": {"n": 288, "k": 12, "distance": 18},
            }
        ],
    },
    "placement": {
        "neutral_atom_magic_state": "right_edge",
        "superconducting_magic_state": "east_edge",
        "memory": "dense_code_block_array",
        "store_load": "boundary_buffer",
        "magic_state_input": "boundary_buffer",
        "magic_state_output": "boundary_buffer",
        "magic_state_factory": "factory_region",
        "bell_pair_preparation": "pipelined_bell_preparation",
        "logical_bell_buffer": "boundary_buffer",
    },
}

Owner = Node | Interconnect


def render_profile_v2(profile: ArchitectureProfile) -> dict[str, Any]:
    """Render the report-v1 Profile receipt from a canonical Profile."""

    if not isinstance(profile, ArchitectureProfile):
        raise TypeError("profile must be an ArchitectureProfile")
    return _profile_v2_receipt(
        {"architecture_profile": {"definition": profile.to_dict()}}
    )


def render_policy_v1(
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Render the frozen report-v1 policy receipt with dotted overrides."""

    result = normalize_json(_POLICY_V1_BASE)
    for raw_key, value in (overrides or {}).items():
        key = str(raw_key)
        cursor: dict[str, Any] = result
        parts = key.split(".")
        for part in parts[:-1]:
            child = cursor.get(part)
            if not isinstance(child, dict):
                raise ValueError(f"Unknown report-v1 policy override: {key}")
            cursor = child
        if not parts or parts[-1] not in cursor:
            raise ValueError(f"Unknown report-v1 policy override: {key}")
        cursor[parts[-1]] = value
    if result["protocols"]["magic_state"]["id"] == "cultivation":
        result["protocols"]["magic_state"]["id"] = (
            "cultivation-d5-d15-p1e3"
        )
    return normalize_json(result)


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _effective_receipt(
    effective_configuration: Mapping[str, Any],
    section: str,
    member: str,
) -> Mapping[str, Any]:
    return _as_mapping(_as_mapping(effective_configuration.get(section)).get(member))


def _profile_v2_receipt(
    effective_configuration: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a Profile-v3 effective receipt into the frozen v2 wire shape.

    This is report rendering only.  The returned mapping is never parsed back
    into the canonical architecture model.
    """

    definition = _effective_receipt(
        effective_configuration, "architecture_profile", "definition"
    )
    if definition.get("schema_version") == _PROFILE_V2_SCHEMA_VERSION:
        return normalize_json(definition)

    raw_nodes = _as_mapping(definition.get("nodes"))
    raw_interconnects = _as_mapping(definition.get("interconnects"))
    interconnect_access: dict[str, dict[str, list[str]]] = {
        str(node_id): {} for node_id in raw_nodes
    }
    interconnects: dict[str, Any] = {}
    for raw_id, raw_interconnect in raw_interconnects.items():
        interconnect_id = str(raw_id)
        interconnect = _as_mapping(raw_interconnect)
        endpoint_nodes: set[str] = set()
        for raw_endpoint in interconnect.get("endpoints", ()):
            endpoint = str(raw_endpoint)
            parts = endpoint.split("/", 2)
            if len(parts) != 3:
                continue
            node_id, module_id, submodule_id = parts
            endpoint_nodes.add(node_id)
            target = f"{interconnect_id}/bell_buffer"
            interconnect_access.setdefault(node_id, {}).setdefault(
                target, []
            ).append(f"{module_id}/{submodule_id}")

        flattened: dict[str, Any] = {}
        for raw_module in _as_mapping(interconnect.get("modules")).values():
            module = _as_mapping(raw_module)
            for raw_submodule in _as_mapping(module.get("submodules")).values():
                submodule = _as_mapping(raw_submodule)
                resource_id = (
                    "bell_engine"
                    if submodule.get("type") == "engine"
                    else "bell_buffer"
                )
                flattened[resource_id] = {
                    "type": submodule.get("type"),
                    "payload": submodule.get("payload"),
                }
        interconnects[interconnect_id] = {
            "endpoints": sorted(endpoint_nodes),
            "submodules": flattened,
        }

    nodes: dict[str, Any] = {}
    for raw_node_id, raw_node in raw_nodes.items():
        node_id = str(raw_node_id)
        node = _as_mapping(raw_node)
        raw_modules = _as_mapping(node.get("modules"))
        modules = {
            str(module_id): {
                "type": _as_mapping(raw_module).get("type"),
                "submodules": {
                    str(submodule_id): {
                        "type": _as_mapping(raw_submodule).get("type"),
                        "payload": _as_mapping(raw_submodule).get("payload"),
                    }
                    for submodule_id, raw_submodule in _as_mapping(
                        _as_mapping(raw_module).get("submodules")
                    ).items()
                },
            }
            for module_id, raw_module in raw_modules.items()
        }
        connections: dict[str, Any] = {}
        for raw_connection_id, raw_connection in _as_mapping(
            node.get("connections")
        ).items():
            connection = _as_mapping(raw_connection)
            endpoints = tuple(str(item) for item in connection.get("endpoints", ()))
            if len(endpoints) != 2:
                continue
            module_id, submodule_id = endpoints[0].split("/", 1)
            payload = _as_mapping(
                _as_mapping(raw_modules.get(module_id)).get("submodules")
            ).get(submodule_id, {})
            record: dict[str, Any] = {
                "payload": _as_mapping(payload).get("payload")
            }
            if connection.get("direction") == "directed":
                record["from"], record["to"] = endpoints
            else:
                record["direction"] = "bidirectional"
                record["endpoints"] = list(endpoints)
            connections[str(raw_connection_id)] = record
        record = {"modality": node.get("modality"), "modules": modules}
        if connections:
            record["connections"] = connections
        if interconnect_access.get(node_id):
            record["interconnect_access"] = {
                target: sorted(local_refs)
                for target, local_refs in sorted(
                    interconnect_access[node_id].items()
                )
            }
        nodes[node_id] = record

    result: dict[str, Any] = {
        "schema_version": _PROFILE_V2_SCHEMA_VERSION,
        "id": definition.get("id", "unknown"),
        "name": definition.get("name", "Unnamed architecture"),
        "nodes": nodes,
        "interconnects": interconnects,
    }
    if definition.get("description"):
        result["description"] = definition["description"]
    return normalize_json(result)


def _owners(specification: ArchitectureSpecification) -> tuple[Owner, ...]:
    return (*specification.nodes, *specification.interconnects)


def _module_role(module: Module) -> str:
    return {
        "compute": "compute",
        "memory": "memory",
        "resource_factory": "msf",
        "bell_engine": "communication",
        "bell_storage": "communication",
    }.get(module.type, module.type)


def _submodule_role(module: Module, submodule: Submodule) -> str:
    return {
        ("compute", "region", "logical_qubit"): "compute",
        ("compute", "buffer", "logical_qubit"): "store_load",
        ("compute", "buffer", "magic_state"): "magic_state_input",
        ("memory", "region", "logical_qubit"): "memory",
        ("memory", "buffer", "logical_qubit"): "store_load",
        ("resource_factory", "engine", "magic_state"): "magic_state_factory",
        ("resource_factory", "buffer", "magic_state"): "magic_state_output",
        ("bell_engine", "engine", "bell_pair"): "bell_pair_preparation",
        ("bell_storage", "buffer", "bell_pair"): "logical_bell_buffer",
    }.get(
        (module.type, submodule.type, submodule.payload),
        f"{submodule.payload}_{submodule.type}",
    )


def _capacity_key(submodule: Submodule) -> str:
    return {
        ("region", "logical_qubit"): "logical_patches",
        ("buffer", "logical_qubit"): "logical_patches",
        ("buffer", "magic_state"): "logical_magic_states",
        ("buffer", "bell_pair"): "logical_bell_pairs",
        ("engine", "magic_state"): "copies",
        ("engine", "bell_pair"): "copies",
    }.get((submodule.type, submodule.payload), "units")


def _legacy_capacity(submodule: Submodule) -> dict[str, int]:
    return {_capacity_key(submodule): submodule.capacity}


def _slot_prefix(module: Module, submodule: Submodule) -> str:
    role = _submodule_role(module, submodule)
    if role == "compute":
        return "D"
    if role == "memory":
        return f"{module.id}.D"
    if role == "store_load":
        return f"{module.id}.SL"
    if role == "magic_state_input":
        return "M"
    if role == "magic_state_output":
        return "MO"
    if role == "logical_bell_buffer":
        return "S"
    return "S"


def _slot_aliases(
    specification: ArchitectureSpecification,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for owner in _owners(specification):
        for module in owner.modules:
            for submodule in module.submodules:
                prefix = _slot_prefix(module, submodule)
                for index, slot in enumerate(submodule.slots):
                    absolute = (
                        f"{owner.id}/{module.id}/{submodule.id}/{slot.id}"
                    )
                    result[absolute] = f"{prefix}{index}"
    return result


def _compiler_slots(layout: LogicalLayout) -> dict[str, LayoutSlot]:
    return {slot.id: slot for slot in layout.slots}


def _absolute_slot_coordinate(
    submodule: Submodule,
    coordinate: tuple[int, int] | None,
) -> list[float] | None:
    if coordinate is None:
        return None
    origin = submodule.logical_origin or (0, 0)
    return [
        float(origin[0] + coordinate[0]),
        float(origin[1] + coordinate[1]),
    ]


def _logical_slot_receipts(
    *,
    owner: Owner,
    module: Module,
    submodule: Submodule,
    aliases: Mapping[str, str],
    compiler_slots: Mapping[str, LayoutSlot],
    include_ref: bool,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for slot in submodule.slots:
        absolute = f"{owner.id}/{module.id}/{submodule.id}/{slot.id}"
        alias = aliases[absolute]
        compiler_slot = compiler_slots.get(absolute)
        item: dict[str, Any] = {"id": alias}
        if include_ref:
            item["ref"] = f"{owner.id}/{module.id}/{submodule.id}/{alias}"
        coordinate = (
            list(compiler_slot.coordinate)
            if compiler_slot is not None
            else _absolute_slot_coordinate(submodule, slot.coordinate)
        )
        if coordinate is not None:
            item["coordinate"] = [float(value) for value in coordinate]
        if compiler_slot is not None:
            item["interfaces"] = list(compiler_slot.interfaces)
            item["kind"] = compiler_slot.kind
            if compiler_slot.zone is not None:
                item["zone"] = compiler_slot.zone
        result.append(item)
    return result


def _compiler_layout_receipt(
    specification: ArchitectureSpecification,
    layout: LogicalLayout,
) -> dict[str, Any]:
    aliases = _slot_aliases(specification)
    slots = []
    for slot in layout.slots:
        item = slot.to_dict()
        item["id"] = aliases.get(slot.id, slot.id)
        slots.append(item)
    metadata = normalize_json(layout.metadata)
    if layout.layout_type == "checkerboard":
        metadata.setdefault("source_layout_type", "checkerboard")
    semantic = {
        "module": layout.module_id,
        "node": layout.node_id,
        "modality": layout.modality,
        "type": (
            "explicit_graph"
            if layout.layout_type == "checkerboard"
            else layout.layout_type
        ),
        "slots": slots,
        "routing_nodes": list(layout.routing_nodes),
        "routing_edges": [list(edge) for edge in layout.routing_edges],
        "metadata": metadata,
    }
    return {**semantic, "layout_hash": semantic_hash(semantic)}


def _components_for(
    footprint: PhysicalFootprintEstimate,
    owner: Owner,
    module: Module,
    submodule: Submodule,
) -> tuple[FootprintComponent, ...]:
    return tuple(
        component
        for component in footprint.components
        if component.owner_id == owner.id
        and component.module_id == module.id
        and component.submodule_id == submodule.id
    )


def _submodule_layout_receipt(
    *,
    specification: ArchitectureSpecification,
    owner: Owner,
    module: Module,
    submodule: Submodule,
    compiler_layout: LogicalLayout,
    footprint: PhysicalFootprintEstimate,
) -> dict[str, Any]:
    aliases = _slot_aliases(specification)
    compiler_slots = _compiler_slots(compiler_layout)
    slots = _logical_slot_receipts(
        owner=owner,
        module=module,
        submodule=submodule,
        aliases=aliases,
        compiler_slots=compiler_slots,
        include_ref=False,
    )
    origin = submodule.logical_origin or (0, 0)
    origin_3d = [float(origin[0]), float(origin[1]), 0.0]
    role = _submodule_role(module, submodule)

    if role == "compute":
        same_module = (
            owner.id == compiler_layout.node_id
            and module.id == compiler_layout.module_id
        )
        routing_coordinates = _as_mapping(
            compiler_layout.metadata.get("routing_coordinates")
        )
        return {
            "type": "explicit_graph",
            "origin_3d": origin_3d,
            "data_slots": slots,
            "routing_nodes": (
                [
                    {"id": identifier, "coordinate": list(coordinate)}
                    for identifier, coordinate in routing_coordinates.items()
                ]
                if same_module
                else []
            ),
            "routing_edges": (
                [list(edge) for edge in compiler_layout.routing_edges]
                if same_module
                else []
            ),
            "blocked_nodes": [],
            "source_layout_type": (
                compiler_layout.layout_type if same_module else "canonical"
            ),
            "magic_state_placement": (
                compiler_layout.metadata.get(
                    "magic_state_placement", "architecture_resolved"
                )
                if same_module
                else "architecture_resolved"
            ),
        }
    if role == "memory":
        return {
            "type": "dense_code_block_array",
            "origin_3d": origin_3d,
            "logical_origin": [float(origin[0]), float(origin[1])],
            "coordinate_semantics": (
                "spatial"
                if any("coordinate" in slot for slot in slots)
                else "identity_only"
            ),
            "slots": slots,
            "placement_resolved": True,
        }
    if submodule.type == "engine":
        components = _components_for(footprint, owner, module, submodule)
        details = dict(components[0].details) if components else {}
        layout_type = (
            "factory_region"
            if submodule.payload == "magic_state"
            else "pipelined_bell_preparation"
        )
        result: dict[str, Any] = {
            "type": layout_type,
            "origin_3d": origin_3d,
            "copies": submodule.capacity,
            "copy_count": submodule.capacity,
            "placement_resolved": True,
        }
        if submodule.resource_protocol is not None:
            result["protocol"] = submodule.resource_protocol.id
        for key in (
            "outputs_per_copy_per_batch",
            "physical_qubits_per_copy",
            "logical_qubits_per_copy_per_endpoint",
            "physical_qubits_per_copy_per_endpoint",
        ):
            if key in details:
                result[key] = details[key]
        return result
    return {
        "type": "boundary_buffer",
        "origin_3d": origin_3d,
        "slots": slots,
        "placement_resolved": True,
    }


def _manifest_submodule(
    *,
    owner: Owner,
    module: Module,
    submodule: Submodule,
    aliases: Mapping[str, str],
    compiler_slots: Mapping[str, LayoutSlot],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": submodule.id,
        "type": submodule.type,
        "payload": submodule.payload,
        "capacity": _legacy_capacity(submodule),
        "ref": f"{owner.id}/{module.id}/{submodule.id}",
    }
    if submodule.type == "engine":
        result["copy_count"] = submodule.capacity
    else:
        result["slots"] = _logical_slot_receipts(
            owner=owner,
            module=module,
            submodule=submodule,
            aliases=aliases,
            compiler_slots=compiler_slots,
            include_ref=True,
        )
    if submodule.qec is not None:
        result["qec"] = submodule.qec.to_dict()
    if submodule.resource_protocol is not None:
        result["resource_protocol"] = {"id": submodule.resource_protocol.id}
    return result


def _manifest_module(
    owner: Owner,
    module: Module,
    *,
    aliases: Mapping[str, str],
    compiler_slots: Mapping[str, LayoutSlot],
) -> dict[str, Any]:
    return {
        "id": module.id,
        "type": module.type,
        "ref": f"{owner.id}/{module.id}",
        "submodules": [
            _manifest_submodule(
                owner=owner,
                module=module,
                submodule=submodule,
                aliases=aliases,
                compiler_slots=compiler_slots,
            )
            for submodule in module.submodules
        ],
    }


def _qec_receipt(
    specification: ArchitectureSpecification,
    *,
    profile_id: str,
) -> dict[str, Any]:
    bindings = [
        {
            "module": module.id,
            "submodule": submodule.id,
            "code": submodule.qec.code,
            "parameters": normalize_json(submodule.qec.parameters),
        }
        for owner in _owners(specification)
        for module in owner.modules
        for submodule in module.submodules
        if submodule.qec is not None
    ]
    semantic = {
        "schema_version": _QEC_SCHEMA_VERSION,
        "configuration": {
            "id": f"arqsim.qec.{profile_id}.canonical_projection",
            "version": 1,
            "target_profile": f"heteqsys.{profile_id}",
        },
        "bindings": bindings,
    }
    return {
        **semantic,
        "metadata": {
            "source": "canonical architecture specification",
            "architecture_hash": specification.architecture_hash,
        },
        "qec_hash": semantic_hash(semantic),
    }


def _logical_architecture_receipt(
    specification: ArchitectureSpecification,
    compiler_layout: LogicalLayout,
    *,
    profile: Mapping[str, Any],
    workflow: Mapping[str, Any],
    layout_plan_hash: str,
    qec_hash: str,
    system_hash: str,
) -> dict[str, Any]:
    aliases = _slot_aliases(specification)
    compiler_slots = _compiler_slots(compiler_layout)
    nodes = []
    for node in specification.nodes:
        connections = []
        for connection in node.connections:
            item: dict[str, Any] = {
                "id": connection.id,
                "direction": connection.direction,
                "from": connection.endpoints[0],
                "to": connection.endpoints[1],
            }
            module_id, submodule_id = connection.endpoints[0].split("/", 1)
            source_module = next(
                module for module in node.modules if module.id == module_id
            )
            source = next(
                submodule
                for submodule in source_module.submodules
                if submodule.id == submodule_id
            )
            item["payload"] = source.payload
            connections.append(item)
        nodes.append(
            {
                "id": node.id,
                "modality": node.modality,
                "coordinate_frame": {
                    "id": f"{node.id}/logical",
                    "dimensions": 2,
                    "unit": "logical_site",
                },
                "modules": [
                    _manifest_module(
                        node,
                        module,
                        aliases=aliases,
                        compiler_slots=compiler_slots,
                    )
                    for module in node.modules
                ],
                "connections": connections,
            }
        )

    interconnects = []
    for interconnect in specification.interconnects:
        submodules = [
            _manifest_submodule(
                owner=interconnect,
                module=module,
                submodule=submodule,
                aliases=aliases,
                compiler_slots=compiler_slots,
            )
            for module in interconnect.modules
            for submodule in module.submodules
        ]
        interconnects.append(
            {
                "id": interconnect.id,
                "endpoints": sorted(
                    {endpoint.split("/", 1)[0] for endpoint in interconnect.endpoints}
                ),
                "submodules": submodules,
                "access": [
                    {
                        "node": endpoint.split("/", 1)[0],
                        "submodule": (
                            next(
                                (
                                    submodule.id
                                    for module in interconnect.modules
                                    for submodule in module.submodules
                                    if submodule.type == "buffer"
                                ),
                                "buffer",
                            )
                        ),
                        "local_submodules": [endpoint.split("/", 1)[1]],
                    }
                    for endpoint in interconnect.endpoints
                ],
            }
        )

    profile_record = {
        "id": profile.get("id", "unknown"),
        "profile_hash": semantic_hash(profile),
        "schema_version": profile.get("schema_version", _PROFILE_V2_SCHEMA_VERSION),
    }
    semantic = {
        "schema_version": _LOGICAL_ARCHITECTURE_SCHEMA_VERSION,
        "profile": profile_record,
        "workflow": normalize_json(workflow),
        "nodes": nodes,
        "interconnects": interconnects,
        "compiler_layout": _compiler_layout_receipt(
            specification, compiler_layout
        ),
        "resolution": {
            "capacity_placement_qec_source": (
                "canonical_architecture_specification"
            ),
            "layout_policy": "report_v1_receipt_only",
            "precedence": [
                "explicit_override",
                "selected_gallery_policy",
                "canonical_default",
            ],
        },
        "resolved_hashes": {
            "architecture": specification.architecture_hash,
            "layout_plan": layout_plan_hash,
            "qec": qec_hash,
            "system": system_hash,
        },
    }
    return {**semantic, "logical_architecture_hash": semantic_hash(semantic)}


def _legacy_payload(payload: str) -> str:
    return {
        "logical_qubit": "logical_patch",
        "magic_state": "logical_magic_state",
        "bell_pair": "logical_bell_pair",
    }.get(payload, payload)


def _resolved_architecture_receipt(
    specification: ArchitectureSpecification,
    compiler_layout: LogicalLayout,
    footprint: PhysicalFootprintEstimate,
    *,
    profile: Mapping[str, Any],
    workflow_id: str,
) -> dict[str, Any]:
    modules = []
    for owner in _owners(specification):
        modality = owner.modality if isinstance(owner, Node) else "interconnect"
        for module in owner.modules:
            ports = [
                {
                    "id": submodule.id,
                    "direction": "bidirectional",
                    "payload": _legacy_payload(submodule.payload),
                    "binds_to": submodule.id,
                }
                for submodule in module.submodules
            ]
            submodules = [
                {
                    "id": submodule.id,
                    "kind": submodule.type,
                    "role": _submodule_role(module, submodule),
                    "capacity": _legacy_capacity(submodule),
                    "layout": _submodule_layout_receipt(
                        specification=specification,
                        owner=owner,
                        module=module,
                        submodule=submodule,
                        compiler_layout=compiler_layout,
                        footprint=footprint,
                    ),
                }
                for submodule in module.submodules
            ]
            modules.append(
                {
                    "id": module.id,
                    "node": owner.id,
                    "role": _module_role(module),
                    "modality": modality,
                    "submodules": submodules,
                    "ports": ports,
                }
            )

    nodes = [
        {
            "id": node.id,
            "modality": node.modality,
            "timing_domain": node.modality,
            "failure_domain": node.id,
            "modules": [module.id for module in node.modules],
            "interfaces": [],
        }
        for node in specification.nodes
    ]
    nodes.extend(
        {
            "id": interconnect.id,
            "modality": "interconnect",
            "timing_domain": "interconnect",
            "failure_domain": interconnect.id,
            "modules": [module.id for module in interconnect.modules],
            "interfaces": [],
        }
        for interconnect in specification.interconnects
    )
    buses = [
        {
            "id": connection.id,
            "node": owner.id,
            "kind": "canonical_local_state_transfer",
            "endpoints": [
                {
                    "module": endpoint.split("/", 1)[0],
                    "port": endpoint.split("/", 1)[1],
                }
                for endpoint in connection.endpoints
            ],
            "resources": {
                "contention_model": "queue",
                "direction": connection.direction,
            },
        }
        for owner in _owners(specification)
        for connection in owner.connections
    ]
    links = [
        {
            "id": interconnect.id,
            "kind": "canonical_interconnect",
            "topology": {"type": "direct"},
            "endpoints": [
                {
                    "node": endpoint.split("/", 1)[0],
                    "interface": endpoint,
                }
                for endpoint in interconnect.endpoints
            ],
            "resources": {"contention_model": "queue", "channels": 1},
        }
        for interconnect in specification.interconnects
    ]
    semantic = {
        "schema_version": "heteqsys.architecture-spec.v2",
        "profile": {
            "id": f"heteqsys.{profile.get('id', 'unknown')}",
            "version": 1,
            "contract_hash": semantic_hash(profile),
        },
        "parameters": {},
        "nodes": nodes,
        "modules": modules,
        "links": links,
        "buses": buses,
        "required_primitive_interfaces": [],
    }
    return {
        **semantic,
        "metadata": {
            "source": "canonical architecture specification",
            "canonical_architecture_hash": specification.architecture_hash,
            "workflow": workflow_id,
        },
        "architecture_hash": semantic_hash(semantic),
    }


def _resolved_system_receipt(
    specification: ArchitectureSpecification,
    compiler_layout: LogicalLayout,
    footprint: PhysicalFootprintEstimate,
    *,
    profile: Mapping[str, Any],
    workflow_id: str,
) -> dict[str, Any]:
    qec = _qec_receipt(
        specification, profile_id=str(profile.get("id", "unknown"))
    )
    architecture = _resolved_architecture_receipt(
        specification,
        compiler_layout,
        footprint,
        profile=profile,
        workflow_id=workflow_id,
    )
    system_hash = semantic_hash(
        {
            "schema_version": _RESOLVED_SYSTEM_SCHEMA_VERSION,
            "architecture_hash": architecture["architecture_hash"],
            "qec_hash": qec["qec_hash"],
        }
    )
    return {
        "schema_version": _RESOLVED_SYSTEM_SCHEMA_VERSION,
        "architecture": architecture,
        "qec_configuration": qec,
        "system_hash": system_hash,
    }


def _footprint_v1_receipt(
    specification: ArchitectureSpecification,
    footprint: PhysicalFootprintEstimate,
    *,
    qec_hash: str,
    system_hash: str,
) -> dict[str, Any]:
    targets = {
        (owner.id, module.id, submodule.id): (module, submodule)
        for owner in _owners(specification)
        for module in owner.modules
        for submodule in module.submodules
    }
    components = []
    for component in footprint.components:
        module, submodule = targets[
            (
                component.owner_id,
                component.module_id,
                component.submodule_id,
            )
        ]
        details = normalize_json(component.details)
        if component.endpoint_node_id is not None:
            details = {**details, "endpoint_node": component.endpoint_node_id}
        components.append(
            {
                "module": component.module_id,
                "submodule": component.submodule_id,
                "module_role": _module_role(module),
                "submodule_role": _submodule_role(module, submodule),
                "modality": component.modality,
                "qec_code": component.qec_code,
                "capacity_key": _capacity_key(submodule),
                "quantity": component.capacity,
                "physical_qubits": component.physical_qubits,
                "rule": component.rule,
                "details": details,
            }
        )
    result = {
        "schema_version": "heteqsys.physical-footprint-estimate.v1",
        "architecture_hash": specification.architecture_hash,
        "qec_hash": qec_hash,
        "system_hash": system_hash,
        "model_id": footprint.model_id,
        "model_hash": footprint.model_hash,
        "total_physical_qubits": footprint.total_physical_qubits,
        "components": components,
        "checks": normalize_json(footprint.checks),
    }
    # This remains the hash of the independent canonical estimate artifact,
    # not a second authority over this report-only compatibility projection.
    result["estimate_hash"] = footprint.estimate_hash
    return result


def _layout_plan_receipt(
    specification: ArchitectureSpecification,
    compiler_layout: LogicalLayout,
    footprint: PhysicalFootprintEstimate,
    *,
    profile_id: str,
    workflow_id: str,
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    submodules = [
        {
            "module": module.id,
            "submodule": submodule.id,
            "capacity": _legacy_capacity(submodule),
            "qec": (
                submodule.qec.to_dict() if submodule.qec is not None else None
            ),
            "placement": _submodule_layout_receipt(
                specification=specification,
                owner=owner,
                module=module,
                submodule=submodule,
                compiler_layout=compiler_layout,
                footprint=footprint,
            ),
        }
        for owner in _owners(specification)
        for module in owner.modules
        for submodule in module.submodules
    ]
    semantic = {
        "schema_version": _LAYOUT_PLAN_SCHEMA_VERSION,
        "profile": f"heteqsys.{profile_id}",
        "workflow": workflow_id,
        "submodules": submodules,
        "connection_layouts": {},
        "provenance": {
            "source": "canonical architecture specification projection",
            "policy": policy.get("id", "unknown"),
            "policy_schema": policy.get("schema_version"),
            "placement_resolved": True,
            "canonical_architecture_hash": specification.architecture_hash,
        },
    }
    return {**semantic, "plan_hash": semantic_hash(semantic)}


def _specification_v1_receipt(
    artifacts: EvaluationRunArtifacts,
    *,
    effective_configuration: Mapping[str, Any],
    requested_config: Mapping[str, Any],
) -> tuple[dict[str, Any], str, str]:
    specification = artifacts.specification
    profile = _profile_v2_receipt(effective_configuration)
    policy = normalize_json(
        _effective_receipt(
            effective_configuration, "layout_policy", "configuration"
        )
    )
    if not policy:
        policy = render_policy_v1(
            _as_mapping(requested_config.get("layout_policy_overrides"))
        )
    workflow_id = str(
        effective_configuration.get(
            "workflow_id", requested_config.get("workflow_id", "unknown")
        )
    )
    workflow = {"id": workflow_id, "logical_qubits": artifacts.circuit.num_qubits}
    resolved_system = _resolved_system_receipt(
        specification,
        artifacts.compiler_layout,
        artifacts.footprint,
        profile=profile,
        workflow_id=workflow_id,
    )
    qec_hash = str(resolved_system["qec_configuration"]["qec_hash"])
    system_hash = str(resolved_system["system_hash"])
    layout_plan = _layout_plan_receipt(
        specification,
        artifacts.compiler_layout,
        artifacts.footprint,
        profile_id=str(profile.get("id", "unknown")),
        workflow_id=workflow_id,
        policy=policy,
    )
    logical_architecture = _logical_architecture_receipt(
        specification,
        artifacts.compiler_layout,
        profile=profile,
        workflow=workflow,
        layout_plan_hash=str(layout_plan["plan_hash"]),
        qec_hash=qec_hash,
        system_hash=system_hash,
    )
    receipt = {
        "schema_version": _SPECIFICATION_BUILD_SCHEMA_VERSION,
        "workflow": workflow,
        "profile": profile,
        "policy_parameters": policy,
        "resource_protocol_bindings": (
            artifacts.resource_protocol_bindings.to_dict()
        ),
        "layout_plan": layout_plan,
        "resolved_system": resolved_system,
        "architecture_slot_layout": _compiler_layout_receipt(
            specification, artifacts.compiler_layout
        ),
        "physical_footprint_model": artifacts.footprint_model.to_dict(),
        "physical_footprint": _footprint_v1_receipt(
            specification,
            artifacts.footprint,
            qec_hash=qec_hash,
            system_hash=system_hash,
        ),
        "logical_architecture": logical_architecture,
    }
    return receipt, qec_hash, system_hash


def _report_v1_execution_receipts(
    artifacts: EvaluationRunArtifacts,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project typed execution inputs onto report-v1's metadata markers.

    The canonical plan keeps deferred recipes and eager MOVE operands in typed
    fields.  Report v1 predates those fields, so this one-way renderer restores
    its historical metadata shape without making metadata a compiler input.
    """

    plan = normalize_json(artifacts.execution_plan.to_dict())
    program_metadata: dict[int, dict[str, Any]] = {}
    resource_metadata: dict[str, dict[str, Any]] = {}

    for instruction in plan["program_dag"]["instructions"]:
        if instruction.get("implementation_recipes"):
            raise ValueError(
                "evaluation-report.v1 cannot represent dynamic Program "
                "continuations; inspect the canonical Plan v6 / Trace v3 "
                "artifacts instead"
            )
        metadata = dict(instruction["metadata"])
        move_operands = instruction.pop("move_operands", None)
        if move_operands is not None:
            metadata["source_slots"] = move_operands["source_slots"]
            metadata["destination_slots"] = move_operands["destination_slots"]
        recipe = instruction.pop("deferred_dispatch", None)
        if recipe is not None:
            if recipe.get("kind") != "joint_magic_route":
                raise ValueError("Report v1 found an unsupported Program recipe")
            route_metrics = dict(metadata.get("route_metrics", {}))
            route_metrics["deferred_until_dispatch"] = True
            metadata["route_metrics"] = route_metrics
            metadata["operation_indices"] = recipe["operation_indices"]
            metadata["mapping"] = recipe["data_mapping"]
            metadata["route_binding_status"] = "unresolved_until_dispatch"
            metadata["runtime_route_request"] = {
                "operation_indices": recipe["operation_indices"],
                "operated_qubits": instruction["qubits"],
                "resident_qubits": metadata["resident_qubits"],
                "compute_partition": metadata["compute_partition"],
                "compute_partition_count": metadata["compute_partition_count"],
                "magic_batch": metadata["magic_batch"],
                "magic_batch_count": metadata["magic_batch_count"],
                "partition_magic_demand": metadata["partition_magic_demand"],
                "mapping": recipe["data_mapping"],
            }
        instruction["metadata"] = metadata
        if move_operands is not None or recipe is not None:
            program_metadata[int(instruction["id"])] = metadata

    for process in plan["resource_dag"]["processes"]:
        metadata = dict(process["metadata"])
        recipe = process.pop("deferred_dispatch", None)
        if recipe is not None:
            if recipe.get("kind") != "state_bound_resource_move":
                raise ValueError("Report v1 found an unsupported Resource recipe")
            metadata["runtime_move_compilation"] = True
        process["metadata"] = metadata
        if recipe is not None:
            resource_metadata[str(process["id"])] = metadata

    # Report v1 deliberately preserves its historical Plan-v5 projection. The
    # canonical run artifact remains Plan v6 and is the only replay authority.
    plan["schema_version"] = "arqsim.execution-plan.v5"

    evaluation = normalize_json(artifacts.evaluation.to_dict())
    # ExecutionTrace v3 has one serialized/hash authority: the ordered causal
    # transition ledger. Report v1 alone retains completed event spans, so
    # project them here instead of storing a second canonical trace record.
    evaluation["events"] = [
        event.to_dict() for event in artifacts.evaluation.events
    ]
    evaluation["schema_version"] = _EXECUTION_TRACE_V1_SCHEMA_VERSION

    def decorate(value: Any) -> None:
        if isinstance(value, dict):
            metadata = value.get("metadata")
            plane = value.get("plane")
            legacy: Mapping[str, Any] | None = None
            if isinstance(metadata, dict) and plane == "program":
                instruction_id = value.get("instruction_id")
                if type(instruction_id) is int:
                    legacy = program_metadata.get(instruction_id)
            elif isinstance(metadata, dict) and plane == "resource":
                process_id = value.get("process_id")
                if type(process_id) is str:
                    legacy = resource_metadata.get(process_id)
            if legacy is not None:
                projected = {**legacy, **metadata}
                if plane == "program" and "runtime_route_request" in legacy:
                    projected["route_binding_status"] = "resolved_at_dispatch"
                elif plane == "resource" and legacy.get(
                    "runtime_move_compilation"
                ):
                    reservation = value.get("reservation", value)

                    def flatten(name: str) -> list[str]:
                        values = reservation.get(name, {})
                        if not isinstance(values, dict):
                            return []
                        return [
                            item
                            for buffer_id in sorted(values)
                            for item in values[buffer_id]
                        ]

                    projected.update(
                        {
                            "source_slots": flatten("consumed_slots"),
                            "destination_slots": flatten("produced_slots"),
                            "moved_tokens": flatten("consumed_tokens"),
                            "compiler_binding_time": "dispatch",
                        }
                    )
                value["metadata"] = projected
            for child in value.values():
                decorate(child)
        elif isinstance(value, list):
            for child in value:
                decorate(child)

    decorate(evaluation)
    return plan, evaluation


class ReportV1Renderer:
    """Render one immutable legacy report without changing run semantics."""

    def render(
        self,
        artifacts: EvaluationRunArtifacts,
        *,
        requested_config: Mapping[str, Any],
        requested_config_hash: str,
        effective_configuration: Mapping[str, Any],
        effective_config_hash: str,
        effective_latency_profile_hash: str,
    ) -> Mapping[str, Any]:
        footprint = artifacts.footprint
        specification_receipt, qec_hash, system_hash = (
            _specification_v1_receipt(
                artifacts,
                effective_configuration=effective_configuration,
                requested_config=requested_config,
            )
        )
        fidelity_profile_hash = (
            artifacts.fidelity_profile.profile_hash
            if artifacts.fidelity_profile is not None
            else None
        )
        analysis = artifacts.analysis.to_dict()
        analysis["fidelity_profile"] = (
            artifacts.fidelity_profile.to_dict()
            if artifacts.fidelity_profile is not None
            else None
        )
        analysis["fidelity"] = (
            artifacts.fidelity.to_dict()
            if artifacts.fidelity is not None
            else None
        )
        execution_plan, evaluation = _report_v1_execution_receipts(artifacts)
        payload = {
            "schema_version": EVALUATION_REPORT_SCHEMA_VERSION,
            "config": requested_config,
            "effective_configuration": effective_configuration,
            "workload": {
                "evaluated": artifacts.circuit.to_dict(),
                "magic_sizing_reference": (
                    artifacts.magic_sizing_circuit.to_dict()
                    if artifacts.magic_sizing_circuit is not None
                    else None
                ),
            },
            "hashes": {
                "config": requested_config_hash,
                "effective_config": effective_config_hash,
                "workload": artifacts.circuit.semantic_hash,
                "magic_sizing_reference": (
                    artifacts.magic_sizing_circuit.semantic_hash
                    if artifacts.magic_sizing_circuit is not None
                    else None
                ),
                "architecture": artifacts.specification.architecture_hash,
                "qec": qec_hash,
                "system": system_hash,
                "compiler": artifacts.compiler_spec.compiler_hash,
                "latency_profile": effective_latency_profile_hash,
                "execution_plan": artifacts.execution_plan.plan_hash,
                "runtime_components": artifacts.evaluation.runtime_components.get(
                    "manifest_hash"
                ),
                "execution": artifacts.evaluation.execution_hash,
                "trace": artifacts.evaluation.trace_hash,
                "footprint_estimate": footprint.estimate_hash,
                "fidelity_profile": fidelity_profile_hash,
            },
            "specification": specification_receipt,
            "compiler": artifacts.compiler_spec.to_dict(),
            "execution_plan": execution_plan,
            "evaluation": evaluation,
            "summary": {
                "total_latency_s": artifacts.evaluation.total_latency_s,
                "total_physical_qubits": footprint.total_physical_qubits,
                "success_probability": (
                    artifacts.fidelity.success_probability
                    if artifacts.fidelity is not None
                    else None
                ),
                "fidelity_complete_coverage": (
                    artifacts.fidelity.complete_coverage
                    if artifacts.fidelity is not None
                    else None
                ),
                "completed_program_instructions": (
                    artifacts.evaluation.completed_program_instructions
                ),
                "event_count": len(artifacts.evaluation.events),
                "invariant_checks": normalize_json(
                    artifacts.evaluation.invariant_checks
                ),
                "footprint_checks": normalize_json(footprint.checks),
            },
            "analysis": analysis,
        }
        return hash_and_freeze_json_document(
            payload,
            label="evaluation report",
            hash_key="report_hash",
        )


def render_evaluation_report_v1(report: Any) -> Mapping[str, Any]:
    """Explicitly adapt one native typed report to the frozen v1 document.

    This is intentionally a typed-artifacts-to-JSON boundary.  It never parses
    a Report-v2 document, and the underlying renderer continues to reject
    dynamic implementation recipes that v1 cannot represent.
    """

    artifacts = getattr(report, "_run_artifacts", None)
    if not isinstance(artifacts, EvaluationRunArtifacts):
        raise TypeError("Report-v1 rendering requires native evaluation artifacts")
    config = getattr(report, "config", None)
    effective = getattr(report, "effective_configuration", None)
    if config is None or not callable(getattr(config, "to_dict", None)):
        raise TypeError("Report-v1 rendering requires a typed evaluation config")
    if effective is None or not callable(getattr(effective, "to_dict", None)):
        raise TypeError(
            "Report-v1 rendering requires a typed effective configuration"
        )
    return ReportV1Renderer().render(
        artifacts,
        requested_config=config.to_dict(),
        requested_config_hash=config.config_hash,
        effective_configuration=effective.to_dict(),
        effective_config_hash=effective.effective_config_hash,
        effective_latency_profile_hash=effective.latency_profile.profile_hash,
    )


__all__ = [
    "EVALUATION_REPORT_SCHEMA_VERSION",
    "ReportV1Renderer",
    "render_evaluation_report_v1",
    "render_policy_v1",
    "render_profile_v2",
]
