#!/usr/bin/env python3
"""Run and verify the finite runtime-injection demonstration System Case.

The checked-in reference is deliberately small and exact.  Updating it is an
explicit review action; ordinary execution strictly reloads, replays, and
recomputes the reference before comparing a fresh deterministic run.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode


CASE_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = CASE_ROOT.parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from arqsim import EvaluationConfig, EvaluationReport, run_evaluation
from arqsim.api import CANONICAL_FIDELITY_PRESET
from arqsim.evaluation import (
    ExecutionPolicy,
    ExecutionTrace,
    RuntimeInjectionMode,
)
from arqsim.operation_profiles import (
    OperationLatencyProfile,
    reference_reaction_latency_profile_v1,
)
from arqsim.program import FTCircuit, load_ft_workload, workload_stats
from arqsim.schema import semantic_hash


MANIFEST_PATH = CASE_ROOT / "manifest.yaml"
SYSTEM_CASE_SCHEMA = "arqsim.system-case.v1"
SUCCESSOR_SYSTEM_CASE_SCHEMA = "arqsim.system-case.v2"
REQUEST_SCHEMA = "arqsim.system-case-request.v1"
REPORT_SCHEMA = "arqsim.evaluation-report.v2"
RECEIPT_SCHEMA = "arqsim.system-case-receipt.v1"
CASE_ID = "clifford_t_toy__2.3__seed_5"
LIVE_ACCEPTANCE_CASE_ID = "finite_runtime_injection_demo_measurement_v3"

_SUCCESSOR_LINEAGES = {
    "finite_runtime_injection_demo_locus_v2": {
        "predecessor_id": "finite_runtime_injection_demo",
        "change": "canonical_execution_locus_metadata_v2",
    },
    LIVE_ACCEPTANCE_CASE_ID: {
        "predecessor_id": "finite_runtime_injection_demo_locus_v2",
        "change": "logical_measurement_provider_plan_v9_seeded_branch_coverage",
    },
}
_BRANCH_EXPECTATIONS = {
    "finite_runtime_injection_demo": ([1, 0], 1),
    "finite_runtime_injection_demo_locus_v2": ([1, 0], 1),
    LIVE_ACCEPTANCE_CASE_ID: ([1, 0], 1),
}
_RUN_SEEDS = {
    "finite_runtime_injection_demo": 5,
    "finite_runtime_injection_demo_locus_v2": 5,
    LIVE_ACCEPTANCE_CASE_ID: 0,
}

_TOP_LEVEL_FIELDS = {
    "schema_version",
    "id",
    "title",
    "purpose",
    "ownership",
    "workload",
    "architecture",
    "evaluation",
    "outputs",
    "reference",
    "acceptance",
}
_SUCCESSOR_TOP_LEVEL_FIELDS = _TOP_LEVEL_FIELDS | {"lineage"}
_LINEAGE_FIELDS = {"predecessor_id", "change"}
_OWNERSHIP_FIELDS = {"source", "license", "contributors"}
_WORKLOAD_FIELDS = {"path", "representation", "sha256", "expected"}
_WORKLOAD_EXPECTED_FIELDS = {
    "num_qubits",
    "depth",
    "operation_count",
    "t_count",
}
_ARCHITECTURE_FIELDS = {"profile_id", "architecture_name"}
_EVALUATION_FIELDS = {
    "workflow_id",
    "seed",
    "trace_level",
    "runtime_injection_mode",
    "reaction_profile",
    "fidelity_preset",
}
_OUTPUT_FIELDS = {"request_schema", "report_schema", "receipt_schema"}
_REFERENCE_FIELDS = {"mutation_policy", "files"}
_REFERENCE_FILE_FIELDS = {"path", "sha256"}
_REFERENCE_FILE_IDS = {"request", "report", "receipt"}
_ACCEPTANCE_FIELDS = {
    "expected_recipe_count",
    "expected_outcomes",
    "expected_reaction_count",
    "expected_logical_s_correction_count",
    "require_all_invariants",
    "require_fidelity_complete_coverage",
    "require_logical_idle_fidelity",
    "require_resource_idle_fidelity",
}


class SystemCaseError(RuntimeError):
    """Raised when the frozen demonstration contract does not verify."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            repeated = key in result
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if repeated:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True)
class LoadedSystemCase:
    document: Mapping[str, Any]
    semantic_manifest_hash: str
    case_root: Path
    workload_path: Path
    workload_sha256: str

    @property
    def id(self) -> str:
        return str(self.document["id"])


@dataclass(frozen=True)
class SystemCaseRun:
    request: Mapping[str, Any]
    report: EvaluationReport
    receipt: Mapping[str, Any]


@dataclass(frozen=True)
class ArchivedSystemCaseRun:
    """Hash-checked historical evidence that is not rerun under newer lowering."""

    request: Mapping[str, Any]
    report: Mapping[str, Any]
    receipt: Mapping[str, Any]


def _mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SystemCaseError(f"{label} must be a mapping")
    return value


def _exact_fields(
    value: Any,
    fields: set[str],
    *,
    label: str,
) -> Mapping[str, Any]:
    record = _mapping(value, label=label)
    actual = set(record)
    if actual != fields:
        raise SystemCaseError(
            f"{label} fields differ: missing={sorted(fields - actual)}, "
            f"unknown={sorted(actual - fields)}"
        )
    return record


def _plain_string(value: Any, *, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise SystemCaseError(f"{label} must be a non-empty trimmed string")
    return value


def _plain_int(value: Any, *, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise SystemCaseError(f"{label} must be an integer >= {minimum}")
    return value


def _plain_bool(value: Any, *, label: str) -> bool:
    if type(value) is not bool:
        raise SystemCaseError(f"{label} must be a boolean")
    return value


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _safe_case_path(
    relative: Any,
    *,
    label: str,
    case_root: Path = CASE_ROOT,
) -> Path:
    raw = _plain_string(relative, label=label)
    candidate = (case_root / raw).resolve()
    try:
        candidate.relative_to(case_root.resolve())
    except ValueError as exc:
        raise SystemCaseError(f"{label} escapes the System Case directory") from exc
    return candidate


def _json_text(value: Any) -> str:
    return json.dumps(
        value,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _load_manifest_document(raw: bytes) -> Any:
    return yaml.load(raw, Loader=_UniqueKeySafeLoader)


def _semantic_manifest_hash(document: Mapping[str, Any]) -> str:
    """Hash authored case semantics without self-referential artifact digests."""

    semantic_document = json.loads(_canonical_json(document))
    reference_files = semantic_document["reference"]["files"]
    for record in reference_files.values():
        record.pop("sha256")
    return _sha256_bytes(_canonical_json(semantic_document).encode("utf-8"))


def _load_json(path: Path, *, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SystemCaseError(f"{label} repeats JSON key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(
                SystemCaseError(f"{label} contains non-finite value {value}")
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemCaseError(f"Cannot load {label}: {path}") from exc


def load_system_case(
    *,
    verify_reference_files: bool = True,
    manifest_path: Path | None = None,
    expected_id: str = "finite_runtime_injection_demo",
) -> LoadedSystemCase:
    """Strictly load the manifest, owned workload, and frozen references."""

    selected_manifest = MANIFEST_PATH if manifest_path is None else manifest_path
    case_root = selected_manifest.resolve().parent
    try:
        raw = selected_manifest.read_bytes()
        document = _load_manifest_document(raw)
    except (OSError, yaml.YAMLError) as exc:
        raise SystemCaseError(f"Cannot load System Case manifest: {exc}") from exc
    if isinstance(document, Mapping) and document.get("schema_version") == (
        SUCCESSOR_SYSTEM_CASE_SCHEMA
    ):
        root = _exact_fields(
            document,
            _SUCCESSOR_TOP_LEVEL_FIELDS,
            label="System Case",
        )
        lineage = _exact_fields(
            root["lineage"], _LINEAGE_FIELDS, label="lineage"
        )
        expected_lineage = _SUCCESSOR_LINEAGES.get(root.get("id"))
        if expected_lineage is None or dict(lineage) != expected_lineage:
            raise SystemCaseError("Unexpected successor lineage")
    else:
        root = _exact_fields(document, _TOP_LEVEL_FIELDS, label="System Case")
    if root["schema_version"] not in {
        SYSTEM_CASE_SCHEMA,
        SUCCESSOR_SYSTEM_CASE_SCHEMA,
    }:
        raise SystemCaseError("Unsupported System Case schema")
    if root["id"] != expected_id:
        raise SystemCaseError("Unexpected System Case id")
    _plain_string(root["title"], label="title")
    _plain_string(root["purpose"], label="purpose")

    ownership = _exact_fields(
        root["ownership"], _OWNERSHIP_FIELDS, label="ownership"
    )
    if ownership["source"] != "arqsim_owned_toy_circuit":
        raise SystemCaseError("The demonstration workload must remain ArqSim-owned")
    if ownership["license"] != "Apache-2.0":
        raise SystemCaseError("Unexpected demonstration workload license")
    contributors = ownership["contributors"]
    if type(contributors) is not list or not contributors:
        raise SystemCaseError("ownership.contributors must be a non-empty list")
    for index, contributor in enumerate(contributors):
        _plain_string(contributor, label=f"ownership.contributors[{index}]")

    workload = _exact_fields(
        root["workload"], _WORKLOAD_FIELDS, label="workload"
    )
    if workload["representation"] != "clifford_t":
        raise SystemCaseError("The demonstration workload must remain Clifford+T")
    workload_path = _safe_case_path(
        workload["path"], label="workload.path", case_root=case_root
    )
    if not workload_path.is_file():
        raise SystemCaseError(f"The owned workload is missing: {workload_path}")
    workload_sha256 = _sha256(workload_path)
    if workload["sha256"] != workload_sha256:
        raise SystemCaseError("The owned workload hash differs from the manifest")
    expected_stats = _exact_fields(
        workload["expected"],
        _WORKLOAD_EXPECTED_FIELDS,
        label="workload.expected",
    )
    for key, expected in expected_stats.items():
        _plain_int(expected, label=f"workload.expected.{key}")

    architecture = _exact_fields(
        root["architecture"], _ARCHITECTURE_FIELDS, label="architecture"
    )
    if architecture != {
        "profile_id": "2.3",
        "architecture_name": "NA-MC + SC-F",
    }:
        raise SystemCaseError("This System Case freezes the vertical slice to profile 2.3")

    evaluation = _exact_fields(
        root["evaluation"], _EVALUATION_FIELDS, label="evaluation"
    )
    expected_seed = _RUN_SEEDS.get(root["id"])
    if expected_seed is None:
        raise SystemCaseError("The System Case has no frozen run seed")
    expected_evaluation = {
        "workflow_id": f"{root['id']}:clifford_t_toy__2.3",
        "seed": expected_seed,
        "trace_level": "full",
        "runtime_injection_mode": "finite_state_injection_v1",
        "reaction_profile": "reference_reaction_latency_profile_v1",
        "fidelity_preset": "canonical_reference_v1",
    }
    if dict(evaluation) != expected_evaluation:
        raise SystemCaseError("The finite-injection evaluation policy changed")

    outputs = _exact_fields(root["outputs"], _OUTPUT_FIELDS, label="outputs")
    if dict(outputs) != {
        "request_schema": REQUEST_SCHEMA,
        "report_schema": REPORT_SCHEMA,
        "receipt_schema": RECEIPT_SCHEMA,
    }:
        raise SystemCaseError("Unexpected System Case output schemas")

    reference = _exact_fields(
        root["reference"], _REFERENCE_FIELDS, label="reference"
    )
    if reference["mutation_policy"] != (
        "immutable_create_successor_do_not_overwrite"
    ):
        raise SystemCaseError("Reference mutation policy changed")
    reference_files = _mapping(reference["files"], label="reference.files")
    if set(reference_files) != _REFERENCE_FILE_IDS:
        raise SystemCaseError("Reference files must be request/report/receipt")
    for name in sorted(_REFERENCE_FILE_IDS):
        record = _exact_fields(
            reference_files[name],
            _REFERENCE_FILE_FIELDS,
            label=f"reference.files.{name}",
        )
        path = _safe_case_path(
            record["path"],
            label=f"reference {name} path",
            case_root=case_root,
        )
        expected_hash = _plain_string(
            record["sha256"], label=f"reference {name} sha256"
        )
        if verify_reference_files:
            if not path.is_file():
                raise SystemCaseError(f"Reference {name} is missing: {path}")
            if _sha256(path) != expected_hash:
                raise SystemCaseError(f"Reference {name} hash differs")

    acceptance = _exact_fields(
        root["acceptance"], _ACCEPTANCE_FIELDS, label="acceptance"
    )
    expected_recipe_count = _plain_int(
        acceptance["expected_recipe_count"],
        label="acceptance.expected_recipe_count",
        minimum=1,
    )
    if expected_recipe_count != 2:
        raise SystemCaseError("The demonstration must contain two T recipes")
    outcomes = acceptance["expected_outcomes"]
    if type(outcomes) is not list:
        raise SystemCaseError("acceptance.expected_outcomes must be an array")
    checked_outcomes = [
        _plain_int(value, label=f"acceptance.expected_outcomes[{index}]")
        for index, value in enumerate(outcomes)
    ]
    if any(value not in {0, 1} for value in checked_outcomes):
        raise SystemCaseError("acceptance.expected_outcomes must contain only bits")
    branch_expectation = _BRANCH_EXPECTATIONS.get(root["id"])
    if branch_expectation is None:
        raise SystemCaseError("The System Case has no frozen branch expectation")
    expected_outcomes, expected_corrections = branch_expectation
    if checked_outcomes != expected_outcomes:
        raise SystemCaseError("The fixed-seed logical measurement outcomes changed")
    expected_reaction_count = _plain_int(
        acceptance["expected_reaction_count"],
        label="acceptance.expected_reaction_count",
        minimum=1,
    )
    if expected_reaction_count != 2:
        raise SystemCaseError("Every T attempt must materialize one reaction")
    expected_correction_count = _plain_int(
        acceptance["expected_logical_s_correction_count"],
        label="acceptance.expected_logical_s_correction_count",
        minimum=0,
    )
    if expected_correction_count != expected_corrections:
        raise SystemCaseError("The fixed-seed logical-S branch count changed")
    for field in (
        "require_all_invariants",
        "require_fidelity_complete_coverage",
        "require_logical_idle_fidelity",
        "require_resource_idle_fidelity",
    ):
        if not _plain_bool(acceptance[field], label=f"acceptance.{field}"):
            raise SystemCaseError(f"acceptance.{field} must remain enabled")

    system_case = LoadedSystemCase(
        document=root,
        semantic_manifest_hash=_semantic_manifest_hash(root),
        case_root=case_root,
        workload_path=workload_path,
        workload_sha256=workload_sha256,
    )
    circuit = load_workload(system_case)
    actual_stats = workload_stats(circuit)
    for key, expected in expected_stats.items():
        if actual_stats[key] != expected:
            raise SystemCaseError(
                f"Owned workload {key} changed: "
                f"expected={expected}, actual={actual_stats[key]}"
            )
    return system_case


def load_workload(system_case: LoadedSystemCase) -> FTCircuit:
    """Load the owned toy circuit with stable, reviewable provenance."""

    return load_ft_workload(
        system_case.workload_path,
        "clifford_t",
        provenance={
            "system_case": system_case.id,
            "input_role": "owned_demonstration_workload",
            "source": "arqsim_owned_toy_circuit",
            "license": "Apache-2.0",
            "source_path": system_case.workload_path.relative_to(
                system_case.case_root
            ).as_posix(),
            "source_sha256": system_case.workload_sha256,
        },
    )


def build_config(system_case: LoadedSystemCase) -> EvaluationConfig:
    """Build the fully explicit finite-injection evaluation request."""

    evaluation = system_case.document["evaluation"]
    latency = reference_reaction_latency_profile_v1(
        OperationLatencyProfile(
            provenance={"system_case": system_case.id, "role": "named_timing_input"}
        )
    )
    return EvaluationConfig(
        profile_id=system_case.document["architecture"]["profile_id"],
        run_label=evaluation["workflow_id"],
        latency_profile=latency,
        execution_policy=ExecutionPolicy(
            observation_level=evaluation["trace_level"],
            run_seed=evaluation["seed"],
            injection_lowering_mode=RuntimeInjectionMode(
                evaluation["runtime_injection_mode"]
            ),
        ),
        fidelity_profile=CANONICAL_FIDELITY_PRESET,
    )


def _case_id(system_case: LoadedSystemCase) -> str:
    seed = system_case.document["evaluation"]["seed"]
    return f"clifford_t_toy__2.3__seed_{seed}"


def build_request(system_case: LoadedSystemCase) -> Mapping[str, Any]:
    circuit = load_workload(system_case)
    config = build_config(system_case)
    return {
        "schema_version": REQUEST_SCHEMA,
        "system_case_id": system_case.id,
        "semantic_manifest_hash": system_case.semantic_manifest_hash,
        "case_id": _case_id(system_case),
        "workload": {
            "path": system_case.workload_path.relative_to(
                system_case.case_root
            ).as_posix(),
            "representation": circuit.representation,
            "sha256": system_case.workload_sha256,
            "semantic_hash": circuit.semantic_hash,
        },
        "config": config.to_dict(),
    }


def _runtime_injection_evidence(
    report: EvaluationReport,
    system_case: LoadedSystemCase,
) -> Mapping[str, Any]:
    plan = report.execution_plan
    if plan.policy.runtime_injection_mode != (
        RuntimeInjectionMode.FINITE_STATE_INJECTION_V1
    ):
        raise SystemCaseError("Report did not retain finite-state injection mode")
    recipes = tuple(
        recipe
        for instruction in plan.program_dag.instructions
        for recipe in instruction.implementation_recipes
    )
    acceptance = system_case.document["acceptance"]
    if len(recipes) != acceptance["expected_recipe_count"]:
        raise SystemCaseError("ExecutionPlan has the wrong T-recipe count")
    recipes = tuple(
        sorted(
            recipes,
            key=lambda item: (
                item.source_layer_index,
                item.source_operation_index,
                item.invocation_id,
            ),
        )
    )
    for recipe in recipes:
        if recipe.recipe_id != "surface_code.t_injection.v1":
            raise SystemCaseError("Unexpected finite-injection recipe id")
        if recipe.convention != "cx_data_magic_measure_magic_z_v1":
            raise SystemCaseError("T recipe lost its CX/measure convention")
        if len(recipe.stages) != 1:
            raise SystemCaseError("T recipe must contain exactly one stage")
        if recipe.stages[0].failure_correction != "s":
            raise SystemCaseError("T recipe must terminate in logical S")

    program_events = tuple(
        event
        for event in report.execution_trace.events
        if event.plane.value == "program"
    )
    evidence: list[dict[str, Any]] = []
    for recipe in recipes:
        register_id = recipe.measurement_register(0)
        source_matches = tuple(
            event
            for event in program_events
            if event.program_lineage is not None
            and event.program_lineage.step == "entangle"
            and any(
                member.recipe_invocation_id == recipe.invocation_id
                for member in event.program_lineage.recipe_members
            )
        )
        if len(source_matches) != 1:
            raise SystemCaseError(
                f"Recipe {recipe.invocation_id!r} has no unique entangle source"
            )
        source = source_matches[0]
        if source.metadata.get("gates", {}).get("t") is None:
            raise SystemCaseError("Injection source does not retain logical T")
        continuations = tuple(
            sorted(
                (
                    event
                    for event in program_events
                    if event.program_lineage is not None
                    and any(
                        member.recipe_invocation_id == recipe.invocation_id
                        for member in event.program_lineage.recipe_members
                    )
                ),
                key=lambda event: (event.start_s, event.event_id),
            )
        )
        measurement_matches = tuple(
            event
            for event in continuations
            if event.program_lineage.step == "measurement"
            and register_id in event.measurements
        )
        if len(measurement_matches) != 1:
            raise SystemCaseError(
                f"Recipe {recipe.invocation_id!r} has no unique measurement phase"
            )
        measurement = measurement_matches[0]
        steps = tuple(
            event.program_lineage.step for event in continuations
        )
        correction_events = tuple(
            event
            for event in continuations
            if event.program_lineage.step == "correction"
        )
        for correction in correction_events:
            if correction.metadata.get("gates") != {"s": recipe.qubits}:
                raise SystemCaseError("Conditional correction is not typed logical S")
        evidence.append(
            {
                "recipe_invocation_id": recipe.invocation_id,
                "source_instruction_id": source.program_lineage.source_instruction_id,
                "source_event_id": source.event_id,
                "measurement_event_id": measurement.event_id,
                "measurement_register": register_id,
                "outcome_bit": measurement.measurements[register_id],
                "steps": list(steps),
                "logical_s_materialized": bool(correction_events),
                "recipe_convention": recipe.convention,
            }
        )

    outcomes = [item["outcome_bit"] for item in evidence]
    if outcomes != acceptance["expected_outcomes"]:
        raise SystemCaseError(
            f"Frozen outcome branches changed: expected="
            f"{acceptance['expected_outcomes']}, actual={outcomes}"
        )
    reaction_count = sum(
        item["steps"].count("reaction") for item in evidence
    )
    correction_count = sum(
        bool(item["logical_s_materialized"]) for item in evidence
    )
    if reaction_count != acceptance["expected_reaction_count"]:
        raise SystemCaseError("Not every source T materialized one reaction")
    if correction_count != acceptance["expected_logical_s_correction_count"]:
        raise SystemCaseError("Conditional logical-S branch count changed")
    runtime_components = _mapping(
        report.execution_plan.runtime_components,
        label="runtime component manifest",
    )
    component_records = _mapping(
        runtime_components.get("components"),
        label="runtime component records",
    )
    measurement_provider = _mapping(
        component_records.get("measurement_provider"),
        label="logical measurement provider",
    )
    measurement_provider_id = measurement_provider.get("component_id")
    if measurement_provider_id != "measurement.seeded_bernoulli.v1":
        raise SystemCaseError("Unexpected logical measurement provider identity")
    return {
        "recipe_id": "surface_code.t_injection.v1",
        "recipe_convention": "cx_data_magic_measure_magic_z_v1",
        "measurement_provider": measurement_provider_id,
        "seed": report.execution_trace.seed,
        "invocations": evidence,
    }


def _build_receipt(
    system_case: LoadedSystemCase,
    request: Mapping[str, Any],
    report: EvaluationReport,
) -> Mapping[str, Any]:
    document = report.to_dict()
    if document["schema_version"] != REPORT_SCHEMA:
        raise SystemCaseError("System Case did not produce native Report v2")
    summary = document["results"]["summary"]
    invariants = summary["invariant_checks"]
    if system_case.document["acceptance"]["require_all_invariants"] and not all(
        invariants.values()
    ):
        raise SystemCaseError("A runtime invariant failed")
    fidelity = report.fidelity
    if fidelity is None:
        raise SystemCaseError("The demonstration requires canonical fidelity")
    acceptance = system_case.document["acceptance"]
    if (
        acceptance["require_fidelity_complete_coverage"]
        and not fidelity.complete_coverage
    ):
        raise SystemCaseError("Canonical fidelity coverage is incomplete")
    logical_idle_total = sum(fidelity.idle_cycles_by_location.values())
    resource_idle_total = sum(
        fidelity.resource_idle_cycles_by_location.values()
    )
    if acceptance["require_logical_idle_fidelity"] and logical_idle_total <= 0:
        raise SystemCaseError("The demo did not expose logical-qubit idling")
    if acceptance["require_resource_idle_fidelity"] and resource_idle_total <= 0:
        raise SystemCaseError("The demo did not expose resource-state idling")
    expected_logical_counts = {"cx": 2, "h": 4, "t": 2}
    expected_corrections = system_case.document["acceptance"][
        "expected_logical_s_correction_count"
    ]
    if expected_corrections:
        expected_logical_counts["s"] = expected_corrections
    if dict(fidelity.logical_operation_counts) != expected_logical_counts:
        raise SystemCaseError(
            "Fidelity did not account for the expected source gates and "
            "materialized logical S"
        )
    expected_resource_counts = {"logical_bell_pair": 2, "magic_state": 2}
    if dict(fidelity.consumed_resource_counts) != expected_resource_counts:
        raise SystemCaseError("Fidelity resource-consumption accounting changed")
    unprofiled = {
        "architecture_operations": dict(fidelity.unprofiled_operation_counts),
        "logical_operations": dict(
            fidelity.unprofiled_logical_operation_counts
        ),
        "logical_idle_exposure_s": dict(
            fidelity.unprofiled_idle_exposure_s
        ),
        "resource_outputs": dict(
            fidelity.unprofiled_resource_output_counts
        ),
        "resource_idle_exposure_s": dict(
            fidelity.unprofiled_resource_idle_exposure_s
        ),
    }
    if any(unprofiled.values()):
        raise SystemCaseError(f"Fidelity left unprofiled exposure: {unprofiled}")

    injection = _runtime_injection_evidence(report, system_case)
    report_text = report.to_json(indent=2)
    request_text = _json_text(request)
    return {
        "schema_version": RECEIPT_SCHEMA,
        "status": "complete",
        "system_case_id": system_case.id,
        "case_id": _case_id(system_case),
        "hashes": {
            "semantic_manifest": system_case.semantic_manifest_hash,
            "request_json": _sha256_bytes(request_text.encode("utf-8")),
            "report_json": _sha256_bytes(report_text.encode("utf-8")),
            "workload": report.circuit.semantic_hash,
            "config": report.config.config_hash,
            "architecture": report.specification.architecture_hash,
            "compiler": report.compiler_spec.compiler_hash,
            "logical_compilation": report.logical_compilation.compilation_hash,
            "execution_plan": report.execution_plan.plan_hash,
            "execution_trace": report.execution_trace.trace_hash,
            "report": report.report_hash,
        },
        "results": {
            "total_latency_s": summary["total_latency_s"],
            "total_physical_qubits": document["results"]["footprint"][
                "total_physical_qubits"
            ],
            "success_probability": fidelity.success_probability,
            "completed_program_instructions": summary[
                "completed_program_instructions"
            ],
            "event_count": summary["event_count"],
            "all_invariants_passed": all(invariants.values()),
            "invariant_checks": dict(invariants),
            "fidelity_complete_coverage": fidelity.complete_coverage,
            "logical_operation_counts": dict(
                fidelity.logical_operation_counts
            ),
            "consumed_resource_counts": dict(
                fidelity.consumed_resource_counts
            ),
            "unprofiled": unprofiled,
            "logical_idle_cycles_total": logical_idle_total,
            "logical_idle_cycles_by_location": dict(
                fidelity.idle_cycles_by_location
            ),
            "resource_idle_cycles_total": resource_idle_total,
            "resource_idle_cycles_by_location": dict(
                fidelity.resource_idle_cycles_by_location
            ),
        },
        "runtime_injection": injection,
    }


def run_case(system_case: LoadedSystemCase) -> SystemCaseRun:
    """Execute the live public API and verify all System Case acceptance facts."""

    circuit = load_workload(system_case)
    config = build_config(system_case)
    request = build_request(system_case)
    report = run_evaluation(circuit, config)
    receipt = _build_receipt(system_case, request, report)
    return SystemCaseRun(request=request, report=report, receipt=receipt)


def _reference_paths(system_case: LoadedSystemCase) -> Mapping[str, Path]:
    files = system_case.document["reference"]["files"]
    return {
        name: _safe_case_path(
            record["path"],
            label=f"reference {name} path",
            case_root=system_case.case_root,
        )
        for name, record in files.items()
    }


def _verify_archived_reference(
    system_case: LoadedSystemCase,
) -> ArchivedSystemCaseRun:
    """Verify immutable pre-successor Plan/Trace evidence without re-lowering."""

    paths = _reference_paths(system_case)
    request_text = paths["request"].read_text(encoding="utf-8")
    stored_request = _load_json(paths["request"], label="reference request")
    if _json_text(stored_request) != request_text:
        raise SystemCaseError("Frozen request is not canonical JSON")
    expected_request = build_request(system_case)
    archived_request_identity = {
        key: value for key, value in stored_request.items() if key != "config"
    }
    current_request_identity = {
        key: value for key, value in expected_request.items() if key != "config"
    }
    if archived_request_identity != current_request_identity:
        raise SystemCaseError("Frozen request identity differs from the manifest")
    archived_config = _mapping(
        stored_request.get("config"), label="archived request config"
    )

    report_text = paths["report"].read_text(encoding="utf-8")
    report_document = _load_json(paths["report"], label="reference report")
    if _json_text(report_document) != report_text:
        raise SystemCaseError("Frozen Report v2 is not canonical JSON")
    if report_document.get("schema_version") != REPORT_SCHEMA:
        raise SystemCaseError("Frozen report schema changed")
    if report_document.get("workflow_id") != system_case.document["evaluation"][
        "workflow_id"
    ]:
        raise SystemCaseError("Frozen report workflow changed")
    unsigned_report = {
        key: value
        for key, value in report_document.items()
        if key != "report_hash"
    }
    if report_document.get("report_hash") != semantic_hash(unsigned_report):
        raise SystemCaseError("Frozen Report v2 content hash differs")

    artifacts = _mapping(report_document.get("artifacts"), label="report artifacts")
    plan_document = _mapping(
        artifacts.get("execution_plan"), label="archived ExecutionPlan"
    )
    plan_hash = plan_document.get("plan_hash")
    unsigned_plan = {
        key: value for key, value in plan_document.items() if key != "plan_hash"
    }
    if plan_hash != semantic_hash(unsigned_plan):
        raise SystemCaseError("Archived ExecutionPlan content hash differs")
    trace = ExecutionTrace.from_dict(
        _mapping(artifacts.get("execution_trace"), label="archived ExecutionTrace")
    )
    if trace.plan_hash != plan_hash:
        raise SystemCaseError("Archived trace does not bind the archived plan")

    receipt_text = paths["receipt"].read_text(encoding="utf-8")
    stored_receipt = _load_json(paths["receipt"], label="reference receipt")
    if _json_text(stored_receipt) != receipt_text:
        raise SystemCaseError("Frozen receipt is not canonical JSON")
    if stored_receipt.get("schema_version") != RECEIPT_SCHEMA:
        raise SystemCaseError("Frozen receipt schema changed")
    if stored_receipt.get("system_case_id") != system_case.id:
        raise SystemCaseError("Frozen receipt System Case id changed")

    logical_compilation = _mapping(
        artifacts.get("logical_compilation"), label="archived logical compilation"
    )
    compiler = _mapping(
        logical_compilation.get("compiler"), label="archived compiler"
    )
    resolved_inputs = _mapping(
        report_document.get("resolved_inputs"), label="report resolved inputs"
    )
    architecture = _mapping(
        resolved_inputs.get("architecture"), label="archived architecture"
    )
    report_request = _mapping(
        report_document.get("request"), label="archived report request"
    )
    report_workload = _mapping(
        report_request.get("workload"), label="archived report workload"
    )
    expected_hashes = {
        "semantic_manifest": system_case.semantic_manifest_hash,
        "request_json": _sha256_bytes(request_text.encode("utf-8")),
        "report_json": _sha256_bytes(report_text.encode("utf-8")),
        "workload": report_workload.get("semantic_hash"),
        "config": semantic_hash(archived_config),
        "architecture": architecture.get("architecture_hash"),
        "compiler": compiler.get("compiler_hash"),
        "logical_compilation": logical_compilation.get("compilation_hash"),
        "execution_plan": plan_hash,
        "execution_trace": trace.trace_hash,
        "report": report_document.get("report_hash"),
    }
    if stored_receipt.get("hashes") != expected_hashes:
        raise SystemCaseError("Frozen receipt hashes do not bind the archived evidence")
    return ArchivedSystemCaseRun(
        request=stored_request,
        report=report_document,
        receipt=stored_receipt,
    )


def verify_reference(
    *,
    rerun: bool = False,
    system_case: LoadedSystemCase | None = None,
) -> SystemCaseRun | ArchivedSystemCaseRun:
    """Strictly reload/replay the reference and optionally compare a live run."""

    system_case = load_system_case() if system_case is None else system_case
    if system_case.id != LIVE_ACCEPTANCE_CASE_ID:
        if rerun:
            raise SystemCaseError(
                "The immutable predecessor is replay-only; run the measurement-v3 "
                "successor for live byte-identical acceptance"
            )
        return _verify_archived_reference(system_case)
    paths = _reference_paths(system_case)
    request_text = paths["request"].read_text(encoding="utf-8")
    stored_request = _load_json(paths["request"], label="reference request")
    if _json_text(stored_request) != request_text:
        raise SystemCaseError("Frozen request is not canonical JSON")
    expected_request = build_request(system_case)
    if stored_request != expected_request:
        raise SystemCaseError("Frozen request differs from the public request builder")
    report_text = paths["report"].read_text(encoding="utf-8")
    stored_report = EvaluationReport.from_json(report_text)
    if stored_report.to_json(indent=2) != report_text:
        raise SystemCaseError("Frozen Report v2 is not canonical")
    expected_receipt = _build_receipt(
        system_case, stored_request, stored_report
    )
    receipt_text = paths["receipt"].read_text(encoding="utf-8")
    stored_receipt = _load_json(paths["receipt"], label="reference receipt")
    if _json_text(stored_receipt) != receipt_text:
        raise SystemCaseError("Frozen receipt is not canonical JSON")
    if stored_receipt != expected_receipt:
        raise SystemCaseError("Frozen receipt differs from strict report replay")
    stored = SystemCaseRun(
        request=stored_request,
        report=stored_report,
        receipt=stored_receipt,
    )
    if rerun:
        live = run_case(system_case)
        if _json_text(live.request) != request_text:
            raise SystemCaseError("Live request is not byte-identical to reference")
        if live.report.to_json(indent=2) != report_text:
            raise SystemCaseError("Fixed-seed live Report v2 drifted")
        if _json_text(live.receipt) != receipt_text:
            raise SystemCaseError("Live receipt is not byte-identical to reference")
    return stored


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-rerun",
        action="store_true",
        help="strictly reload/replay references without a fresh public-API run",
    )
    parser.parse_args(argv)
    # This named predecessor is immutable historical evidence.  The option is
    # retained for command compatibility, but this runner never performs a
    # current-code rerun; its locus-v2 successor owns that acceptance gate.
    verified = verify_reference(rerun=False)
    report_hash = (
        verified.report.report_hash
        if isinstance(verified.report, EvaluationReport)
        else verified.report["report_hash"]
    )
    print(
        "PASS "
        f"{verified.receipt['case_id']} "
        f"report={report_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
