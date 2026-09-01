"""Static hierarchical architecture contracts and mutable runtime state."""

from .errors import (
    ArchitectureError,
    ArchitectureValidationError,
    UnsupportedArchitectureError,
)
from .identifiers import SubmoduleKey
from .logical_layout import (
    LogicalCoordinate,
    LogicalLayoutGrid,
    LogicalLayoutRequest,
    LogicalLayoutResult,
    SubmoduleLayoutRequest,
    SubmoduleLayoutResult,
)
from .logical_layout_policy import LogicalLayoutPolicy
from .recipes import (
    ExactPiAngle,
    INJECTION_RECIPE_SCHEMA_VERSION,
    InjectionRecipe,
    InjectionStage,
    ProgramWorkLineage,
    ResourceRef,
    ResourceStateKind,
    build_angle_doubling_recipe,
)
from .gallery import (
    get_architecture_profile,
    list_architecture_profiles,
)
from .profile import (
    ARCHITECTURE_PROFILE_SCHEMA_VERSION,
    ArchitectureProfile,
    ProfileInterconnect,
    ProfileLocalConnection,
    ProfileModule,
    ProfileNode,
    ProfileSubmodule,
    load_architecture_profile,
)
from .resolver import ARCHITECTURE_RESOLVER_ID, resolve_architecture
from .construction import construct_architecture
from .sizing import (
    SizingPolicy,
    SizingResult,
)
from .specification import (
    ARCHITECTURE_SPECIFICATION_SCHEMA_VERSION,
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
__all__ = [
    "ARCHITECTURE_PROFILE_SCHEMA_VERSION",
    "ARCHITECTURE_RESOLVER_ID",
    "ARCHITECTURE_SPECIFICATION_SCHEMA_VERSION",
    "ArchitectureError",
    "ArchitectureProfile",
    "ArchitectureSpecification",
    "ArchitectureValidationError",
    "Interconnect",
    "ExactPiAngle",
    "INJECTION_RECIPE_SCHEMA_VERSION",
    "InjectionRecipe",
    "InjectionStage",
    "LocalConnection",
    "LogicalCoordinate",
    "LogicalLayoutGrid",
    "LogicalLayoutPolicy",
    "LogicalLayoutRequest",
    "LogicalLayoutResult",
    "LogicalSlot",
    "Module",
    "Node",
    "ProfileInterconnect",
    "ProfileLocalConnection",
    "ProfileModule",
    "ProfileNode",
    "ProfileSubmodule",
    "ProgramWorkLineage",
    "QECBinding",
    "QECResourceProtocolRef",
    "ResourceRef",
    "ResourceStateKind",
    "build_angle_doubling_recipe",
    "SizingPolicy",
    "SizingResult",
    "Submodule",
    "SubmoduleLayoutRequest",
    "SubmoduleLayoutResult",
    "SubmoduleKey",
    "UnsupportedArchitectureError",
    "construct_architecture",
    "get_architecture_profile",
    "list_architecture_profiles",
    "load_architecture_profile",
    "resolve_architecture",
]
