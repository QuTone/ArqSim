"""Content-addressed QEC resource-protocol profiles."""
from .protocol import (
    ENTANGLEMENT_DISTILLATION_SCHEMA_VERSION,
    MAGIC_STATE_FACTORY_SCHEMA_VERSION,
    EntanglementDistillationProfile,
    MagicStateFactoryProfile,
    QECResourceProtocolProfile,
    entanglement_distillation_profiles,
    get_entanglement_distillation_profile,
    get_magic_state_factory_profile,
    load_entanglement_distillation_catalog,
    load_magic_state_factory_catalog,
    magic_state_factory_profiles,
)

__all__ = [
    "QECResourceProtocolProfile",
    "ENTANGLEMENT_DISTILLATION_SCHEMA_VERSION",
    "MAGIC_STATE_FACTORY_SCHEMA_VERSION",
    "EntanglementDistillationProfile",
    "MagicStateFactoryProfile",
    "entanglement_distillation_profiles",
    "get_entanglement_distillation_profile",
    "get_magic_state_factory_profile",
    "load_entanglement_distillation_catalog",
    "load_magic_state_factory_catalog",
    "magic_state_factory_profiles",
]
