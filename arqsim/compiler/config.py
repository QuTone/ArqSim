"""Canonical compiler configuration for each compute modality."""

from arqsim.architecture.specification import ArchitectureSpecification

from .errors import LogicalCompilerValidationError
from .layout import single_node_module
from .models import BackendSpec, LogicalCompilerSpec


def canonical_compiler_spec(
    specification: ArchitectureSpecification,
) -> LogicalCompilerSpec:
    owned_compute = single_node_module(specification, "compute")
    assert owned_compute is not None
    node, compute = owned_compute
    if node.modality == "neutral_atom":
        return LogicalCompilerSpec(
            mapping=BackendSpec("sabre_na", {"seed": 42}),
            routing=BackendSpec(
                "powermove_na",
                {"distance_metric": "euclidean"},
            ),
        )
    if node.modality == "superconducting":
        return LogicalCompilerSpec(
            mapping=BackendSpec("row_major_checkerboard_sc"),
            routing=BackendSpec("greedy_steiner_sc", {"guard_distance": 0}),
        )
    raise LogicalCompilerValidationError(
        "No compiler exists for the compute modality",
        details={"module": compute.id, "modality": node.modality},
    )
