"""Freeze the audited owner-scoped wildcard surfaces."""

from __future__ import annotations

import importlib


EXPECTED_EXPORTS = {
    "architecture": {
        "ARCHITECTURE_PROFILE_SCHEMA_VERSION", "ARCHITECTURE_RESOLVER_ID",
        "ARCHITECTURE_SPECIFICATION_SCHEMA_VERSION", "ArchitectureError",
        "ArchitectureProfile", "ArchitectureSpecification",
        "ArchitectureValidationError", "ExactPiAngle", "INJECTION_RECIPE_SCHEMA_VERSION",
        "InjectionRecipe", "InjectionStage", "Interconnect", "LocalConnection",
        "LogicalCoordinate", "LogicalLayoutGrid", "LogicalLayoutPolicy",
        "LogicalLayoutRequest", "LogicalLayoutResult", "LogicalSlot", "Module", "Node",
        "ProfileInterconnect", "ProfileLocalConnection", "ProfileModule", "ProfileNode",
        "ProfileSubmodule", "ProgramWorkLineage", "QECBinding", "QECResourceProtocolRef",
        "ResourceRef", "ResourceStateKind", "SizingPolicy", "SizingResult", "Submodule",
        "SubmoduleKey", "SubmoduleLayoutRequest", "SubmoduleLayoutResult",
        "UnsupportedArchitectureError", "build_angle_doubling_recipe",
        "construct_architecture", "get_architecture_profile", "list_architecture_profiles",
        "load_architecture_profile", "resolve_architecture",
    },
    "compiler": {
        "BackendSpec", "COMPILATION_RESULT_SCHEMA_VERSION", "CompiledComputeUnit",
        "CompiledRouteResult", "CompiledRouteStep", "CompilerPipeline", "ComputeBatch",
        "ComputeDuration", "ComputePartition", "DefaultCompilerPipeline", "LayoutSlot",
        "LogicalCompilationResult", "LogicalCompilerSpec", "LogicalLayout",
        "LogicalPlacement", "LogicalRoutePlan", "Movement", "PlacementEntry", "RouteStep",
        "SyndromeProtocolTiming", "canonical_compiler_spec", "compile_ft_circuit",
        "validate_compilation_coverage",
    },
    "evaluation": {
        "AnalyticEstimate", "BackendRequest", "BufferSpec", "CandidateImplementation",
        "CompletionRequest", "DeferredDispatchRequest", "EngineSpec", "EvaluationAnalysis",
        "EvaluationError", "EvaluationPolicy", "EvaluationResult", "EventOutcome",
        "ExecutionEvent", "ExecutionPlan", "ExecutionPlane", "ExecutionTrace",
        "ExecutionTransition", "ExecutionTransitionKind", "FidelityEstimate", "LayerEstimate",
        "OutcomeModel", "PhysicalFootprintModel", "PreparedExecution",
        "ProfileExecutionBackend", "ProgramContinuationReceipt", "ProgramDAG",
        "ProgramSchedulingRequest", "QubitExposure", "RealizationRequest", "ResourceBufferRef",
        "ResourceConsumption", "ResourceDAG", "ResourceProcess", "ResourceResidenceInterval",
        "ResourceSchedulingRequest", "ResourceTokenLedger", "ResourceTokenRecord",
        "RuntimeComponentDescriptor", "RuntimeComponentError", "RuntimeComponentManifest",
        "RuntimeComponentSet", "RuntimeInjectionMode", "RuntimeOperationView", "RuntimeRealizer",
        "RuntimeScheduler", "TraceReplayError", "TraceStateProjection", "TraceValidationError",
        "analyze_evaluation", "buffer_occupancy_statistics", "compile_and_lower",
        "default_runtime_component_manifest", "engine_utilization", "estimate_compiler_circuit_lower_bound",
        "estimate_fidelity", "estimate_physical_footprint", "estimate_static_layerwise_aggregation",
        "evaluate", "exclusive_time_breakdown", "fidelity_breakdown", "lower_compilation_result",
        "qubit_exposure", "replay_execution_trace", "resource_token_ledger", "space_breakdown",
        "validate_discrete_time_log_document", "validate_execution_trace_document",
    },
    "operation_profiles": {
        "ArrivalDistribution", "BB_SURFACE_TRANSFER_PROTOCOL", "FidelityProfile",
        "GBC_TRANSVERSAL_PROTOCOL", "LOGICAL_BELL_PAIR_RESOURCE", "MAGIC_STATE_RESOURCE",
        "NEUTRAL_ATOM_MOVEMENT_PROFILE_SCHEMA_VERSION", "NeutralAtomMovementProfile",
        "OperationLatencyProfile", "REFERENCE_REACTION_LATENCY_BY_MODALITY_S",
        "REFERENCE_REACTION_LATENCY_PROFILE_ID", "PBC_LATTICE_SURGERY_PROTOCOL",
        "RESOURCE_PROTOCOL_BINDING_SCHEMA_VERSION", "RESOURCE_PROTOCOL_BINDINGS_SCHEMA_VERSION",
        "ResolvedResourceProtocolBinding", "ResolvedResourceProtocolBindings",
        "ResourceBufferFidelityModel", "ResourceStateFidelityModel", "SyndromeTimingProfile",
        "apply_arrival_overrides", "canonical_fidelity_profile", "canonical_syndrome_profiles",
        "effective_resource_protocol_bindings", "reference_reaction_latency_profile_v1",
        "resolve_resource_protocol_bindings", "with_effective_arrivals",
    },
    "program": {
        "CircuitStatistics", "FTCircuit", "LogicalLayer", "LogicalOperation",
        "WORKLOAD_SCHEMA_VERSION", "WorkloadParseError", "circuit_statistics",
        "load_ft_workload", "make_layers", "normalize_pauli_string", "workload_stats",
    },
    "qec": {
        "ENTANGLEMENT_DISTILLATION_SCHEMA_VERSION", "EntanglementDistillationProfile",
        "MAGIC_STATE_FACTORY_SCHEMA_VERSION", "MagicStateFactoryProfile",
        "QECResourceProtocolProfile", "entanglement_distillation_profiles",
        "get_entanglement_distillation_profile", "get_magic_state_factory_profile",
        "load_entanglement_distillation_catalog", "load_magic_state_factory_catalog",
        "magic_state_factory_profiles",
    },
    "synthesizer": {
        "NWQECSynthesizer", "PrecomputedCircuitLoader", "SynthesisArtifact",
        "SynthesisBackend", "SynthesisSpec", "Synthesizer", "load_artifact_workload",
        "synthesize",
    },
    "visualization": {
        "plot_architecture_hierarchy", "plot_architecture_profile",
        "plot_architecture_slot_layout", "plot_architecture_specification",
        "plot_circuit_layers", "plot_execution_timeline", "plot_module_cube_layout",
        "plot_program_dag", "plot_resource_dag", "plot_space_breakdown",
        "plot_specification_overall_layout",
    },
}


def test_owner_scoped_wildcard_exports_are_exact() -> None:
    for subpackage, expected in EXPECTED_EXPORTS.items():
        module = importlib.import_module(f"heteqsys.{subpackage}")
        assert set(module.__all__) == expected
        assert len(module.__all__) == len(expected)
        assert all(hasattr(module, name) for name in expected)


def test_internal_and_deleted_names_do_not_leak_through_facades() -> None:
    forbidden = {
        "architecture": {
            "ArchitectureState", "ArchitectureInstruction", "OperationClaims",
            "TentativeBinding",
        },
        "compiler": {
            "absolute_slot_id", "map_logical_qubits", "route_logical_circuit",
        },
        "evaluation": {
            "build_execution_plan", "ExecutionScheduler", "ResourceDispatchPolicy",
        },
        "operation_profiles": {
            "OperationProfileCatalog", "load_fidelity_fit",
            "unrotated_surface_clifford_failure",
        },
        "program": {"Benchmark", "workload_layout_statistics"},
        "qec": {"QECCodeSpec", "QECProtocolSpec"},
    }
    for subpackage, names in forbidden.items():
        module = importlib.import_module(f"heteqsys.{subpackage}")
        assert names.isdisjoint(module.__all__)
        assert all(not hasattr(module, name) for name in names)
