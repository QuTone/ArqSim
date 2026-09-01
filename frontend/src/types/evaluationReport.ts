/** ArqSim wire contracts and version-neutral frontend presentation models. */

export const EVALUATION_REPORT_V1 = "arqsim.evaluation-report.v1" as const;
export const EVALUATION_REPORT_V2 = "arqsim.evaluation-report.v2" as const;
export const EXECUTION_TRACE_V3 = "arqsim.execution-trace.v3" as const;
export const ARCHITECTURE_PROFILE_V3 = "arqsim.architecture-profile.v3" as const;

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type JsonRecord = { [key: string]: JsonValue };

export interface FTLogicalOperationDocument {
  kind: string;
  name: string;
  qubits: number[];
  classical_bits: number[];
  parameters: JsonValue[];
  pauli?: string;
  weight?: number;
  [key: string]: unknown;
}

export interface FTLogicalLayerDocument {
  index: number;
  active_qubits: number[];
  operations: FTLogicalOperationDocument[];
  [key: string]: unknown;
}

export interface FTCircuitDocument {
  schema_version: string;
  representation: string;
  num_qubits: number;
  num_clbits: number;
  layers: FTLogicalLayerDocument[];
  semantic_hash: string;
  provenance: JsonRecord;
  [key: string]: unknown;
}

/** Capacity- and QEC-free canonical ArchitectureProfile v3 document. */
export interface ArchitectureProfileSubmoduleDocument {
  type: string;
  payload: string;
  [key: string]: unknown;
}

export interface ArchitectureProfileModuleDocument {
  type: string;
  submodules: Record<string, ArchitectureProfileSubmoduleDocument>;
  [key: string]: unknown;
}

export interface ArchitectureProfileConnectionDocument {
  direction: "directed" | "bidirectional" | string;
  endpoints: string[];
  [key: string]: unknown;
}

export interface ArchitectureProfileNodeDocument {
  modality: string;
  modules: Record<string, ArchitectureProfileModuleDocument>;
  connections?: Record<string, ArchitectureProfileConnectionDocument>;
  [key: string]: unknown;
}

export interface ArchitectureProfileInterconnectDocument {
  endpoints: string[];
  modules: Record<string, ArchitectureProfileModuleDocument>;
  connections?: Record<string, ArchitectureProfileConnectionDocument>;
  [key: string]: unknown;
}

export interface ArchitectureProfileV3Document {
  schema_version: typeof ARCHITECTURE_PROFILE_V3;
  id: string;
  name: string;
  description?: string;
  nodes: Record<string, ArchitectureProfileNodeDocument>;
  interconnects: Record<string, ArchitectureProfileInterconnectDocument>;
  [key: string]: unknown;
}

export interface ArchitectureQecDocument {
  code: string;
  parameters: JsonRecord;
  [key: string]: unknown;
}

export interface ArchitectureSlotDocument {
  id: string;
  ref?: string;
  kind?: string;
  zone?: string;
  coordinate?: number[];
  interfaces?: string[];
  adjacent_to?: string;
  [key: string]: unknown;
}

export interface ArchitectureSubmoduleDocument {
  id: string;
  ref: string;
  type: string;
  payload?: string;
  capacity?: Record<string, number>;
  copy_count?: number;
  qec?: ArchitectureQecDocument;
  resource_protocol?: { id: string; [key: string]: unknown };
  logical_origin?: number[];
  coordinate_semantics?: string;
  slots?: ArchitectureSlotDocument[];
  [key: string]: unknown;
}

export interface ArchitectureModuleDocument {
  id: string;
  ref: string;
  type: string;
  submodules: ArchitectureSubmoduleDocument[];
  [key: string]: unknown;
}

export interface ArchitectureConnectionDocument {
  id: string;
  direction: "directed" | "bidirectional" | string;
  payload?: string;
  from?: string;
  to?: string;
  endpoints?: string[];
  [key: string]: unknown;
}

export interface ArchitectureNodeDocument {
  id: string;
  modality: string;
  modules: ArchitectureModuleDocument[];
  connections: ArchitectureConnectionDocument[];
  coordinate_frame?: {
    id: string;
    dimensions: number;
    unit: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface ArchitectureInterconnectAccessDocument {
  node: string;
  submodule: string;
  local_submodules: string[];
  [key: string]: unknown;
}

export interface ArchitectureInterconnectDocument {
  id: string;
  endpoints: string[];
  access: ArchitectureInterconnectAccessDocument[];
  submodules: ArchitectureSubmoduleDocument[];
  [key: string]: unknown;
}

export interface LogicalArchitectureDocument {
  schema_version: string;
  logical_architecture_hash: string;
  nodes: ArchitectureNodeDocument[];
  interconnects: ArchitectureInterconnectDocument[];
  [key: string]: unknown;
}

export interface EvaluationEventDocument {
  event_id: number;
  plane: "program" | "resource";
  instruction_id: number | null;
  process_id: string | null;
  opcode: string;
  start_s: number;
  end_s: number;
  duration_s: number;
  metadata: Record<string, unknown>;
  wait_reasons: string[];
  [key: string]: unknown;
}

/** Typed dynamic-Program facts carried by canonical Trace v3 transitions. */
export type ProgramWorkStep = "source" | "injection" | "reaction" | "correction";

export interface ProgramWorkLineageDocument {
  work_id: string;
  source_instruction_id: number;
  parent_event_id: number | null;
  recipe_invocation_id: string | null;
  recipe_id: string | null;
  stage_index: number | null;
  step: ProgramWorkStep;
}

export interface ProgramContinuationDocument {
  kind: "activate" | "complete_source";
  activated_work_ids: string[];
}

export interface InjectionResourceRefDocument {
  ref_id: string;
  state_kind: "t_magic" | "rz_angle";
  buffer_id: string;
  token_kind: string;
  quantity: number;
  angle?: JsonRecord;
}

export interface InjectionStageDocument {
  index: number;
  resource: InjectionResourceRefDocument;
  attempt_duration_s: number;
  failure_next_stage?: number;
  failure_correction?: string;
}

export interface InjectionRecipeDocument {
  schema_version: "arqsim.injection-recipe.v1";
  kind: "finite_state_injection";
  invocation_id: string;
  recipe_id: string;
  source_layer_index: number;
  source_operation_index: number;
  qubits: number[];
  stages: InjectionStageDocument[];
  data_mapping: Record<string, string>;
  compute_location: string;
  compute_engine: string;
  reaction_duration_s: number;
  correction_duration_s: number;
  convention: "cx_data_magic_measure_magic_z_v1";
}

export interface ProgramInstructionDocument {
  id: number;
  opcode: string;
  predecessors: number[];
  duration_s: number;
  layer?: number;
  qubits: number[];
  target_modules: string[];
  metadata: JsonRecord;
  implementation_recipes?: InjectionRecipeDocument[];
  [key: string]: unknown;
}

export interface ExecutionPlanV6Document {
  schema_version: "arqsim.execution-plan.v6";
  source: {
    circuit_hash: string;
  };
  architecture_hash: string;
  latency_profile_hash: string;
  policy: {
    magic_state_consumption: string;
    store_load_policy: string;
    resource_fill_policy: string;
    trace_level: string;
    runtime_injection_mode: "black_box" | "finite_state_injection_v1";
    selected_layers: number[];
    seed: number;
    max_events: number;
  };
  program_dag: {
    instructions: ProgramInstructionDocument[];
  };
  resource_dag: JsonRecord;
  architectural_state: JsonRecord;
  provenance: JsonRecord;
  runtime_components: JsonRecord;
  plan_hash: string;
}

export interface EvaluationReportV1 {
  schema_version: typeof EVALUATION_REPORT_V1;
  report_hash: string;
  config: {
    profile_id: string;
    workflow_id: string | null;
    [key: string]: unknown;
  };
  effective_configuration: {
    workflow_id: string;
    [key: string]: unknown;
  };
  workload: {
    evaluated: FTCircuitDocument;
    magic_sizing_reference: FTCircuitDocument | null;
    [key: string]: unknown;
  };
  specification: {
    logical_architecture: LogicalArchitectureDocument;
    profile: {
      id: string;
      name: string;
      description?: string;
      [key: string]: unknown;
    };
    workflow: {
      id: string;
      logical_qubits: number;
      [key: string]: unknown;
    };
    [key: string]: unknown;
  };
  evaluation: {
    total_latency_s: number;
    events: EvaluationEventDocument[];
    [key: string]: unknown;
  };
  summary: {
    total_latency_s: number;
    total_physical_qubits: number;
    success_probability: number | null;
    fidelity_complete_coverage: boolean | null;
    completed_program_instructions: number;
    event_count: number;
    invariant_checks: Record<string, boolean>;
    [key: string]: unknown;
  };
  analysis: {
    exclusive_time_s: Record<string, number> | null;
    physical_space_qubits: Record<string, number>;
    engine_utilization: Record<string, number>;
    buffer_occupancy: Record<string, JsonRecord> | null;
    unavailable: Record<string, string>;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

/**
 * A deliberately narrow frontend view of Report v2. The adapter validates the
 * exact public topology and keeps nested canonical artifacts as JSON records;
 * backend codecs remain authoritative for semantic hashes and causal replay.
 */
export interface ExecutionTransitionV3Document {
  transition_id: number;
  kind: "dispatch" | "completion";
  time_s: number;
  event_id: number;
  reservation_id: number;
  state_version_before: number;
  state_version_after: number;
  plane: "program" | "resource";
  opcode: string;
  instruction_id: number | null;
  process_id: string | null;
  instance: number | null;
  candidate_id: string;
  start_s: number;
  end_s: number;
  wait_reasons: string[];
  consumes: JsonRecord;
  consumed_tokens: JsonRecord;
  consumed_slots: JsonRecord;
  produces: JsonRecord;
  produced_slots: JsonRecord;
  produced_tokens: JsonRecord;
  forwards: JsonRecord;
  engines: JsonRecord;
  required_locations: JsonRecord;
  completion_locations: JsonRecord;
  token_sequence_after: JsonRecord;
  buffer_occupancy_after: JsonRecord;
  pending_incoming_after: JsonRecord;
  metadata: JsonRecord;
  backend_artifact: JsonRecord;
  outcome: JsonRecord;
  program_lineage: ProgramWorkLineageDocument | null;
  measurements: Record<string, number>;
  continuation: ProgramContinuationDocument | null;
}

export interface ExecutionTraceV3Document {
  schema_version: typeof EXECUTION_TRACE_V3;
  plan_hash: string;
  seed: number;
  total_latency_s: number;
  transitions: ExecutionTransitionV3Document[];
  initial_state: JsonRecord;
  terminal_state: JsonRecord;
  terminal_inflight: ExecutionTransitionV3Document[];
  trace_hash: string;
}

export interface EvaluationReportV2 {
  schema_version: typeof EVALUATION_REPORT_V2;
  workflow_id: string;
  request: {
    config: JsonRecord;
    workload: FTCircuitDocument;
    magic_sizing_workload: FTCircuitDocument | null;
  };
  resolved_inputs: {
    architecture: JsonRecord;
    latency_profile: JsonRecord;
    footprint_model: JsonRecord;
    fidelity_profile: JsonRecord | null;
  };
  artifacts: {
    logical_compilation: JsonRecord;
    execution_plan: ExecutionPlanV6Document;
    execution_trace: ExecutionTraceV3Document;
  };
  results: {
    observations: {
      discrete_time_log: JsonRecord[];
    };
    footprint: JsonRecord;
    fidelity: JsonRecord | null;
    analysis: {
      exclusive_time_s: Record<string, number> | null;
      engine_utilization: Record<string, number>;
      buffer_occupancy: Record<string, JsonRecord> | null;
      physical_space_qubits: Record<string, number>;
      fidelity_negative_log_success: Record<string, number> | null;
      unavailable: Record<string, string>;
    };
    summary: {
      total_latency_s: number;
      total_physical_qubits: number;
      success_probability: number | null;
      fidelity_complete_coverage: boolean | null;
      completed_program_instructions: number;
      event_count: number;
      invariant_checks: Record<string, boolean>;
    };
  };
  report_hash: string;
}

export type EvaluationReportDocument = EvaluationReportV1 | EvaluationReportV2;

export interface QecBindingViewModel {
  code: string;
  parameters: Readonly<Record<string, JsonValue>>;
  label: string;
}

export interface ArchitectureSlotViewModel {
  id: string;
  ref: string;
  kind: string;
  payload: string;
  zone: string | null;
  coordinate: readonly [number, number] | null;
  interfaces: readonly string[];
  adjacentTo: string | null;
}

export interface ArchitectureSubmoduleViewModel {
  id: string;
  ref: string;
  label: string;
  type: string;
  payload: string;
  capacity: Readonly<Record<string, number>>;
  copyCount: number | null;
  logicalOrigin: readonly [number, number] | null;
  coordinateSemantics: string | null;
  qec: QecBindingViewModel | null;
  resourceProtocolId: string | null;
  slots: readonly ArchitectureSlotViewModel[];
}

export interface ArchitectureModuleViewModel {
  id: string;
  ref: string;
  label: string;
  type: string;
  submodules: readonly ArchitectureSubmoduleViewModel[];
}

export interface ArchitectureConnectionViewModel {
  id: string;
  direction: string;
  payload: string | null;
  endpoints: readonly string[];
}

export interface ArchitectureNodeViewModel {
  id: string;
  label: string;
  modality: string;
  coordinateFrame: {
    id: string;
    dimensions: number;
    unit: string;
  } | null;
  modules: readonly ArchitectureModuleViewModel[];
  connections: readonly ArchitectureConnectionViewModel[];
}

export interface ArchitectureInterconnectViewModel {
  id: string;
  label: string;
  endpoints: readonly string[];
  access: readonly {
    nodeId: string;
    interconnectSubmoduleId: string;
    localSubmoduleRefs: readonly string[];
  }[];
  submodules: readonly ArchitectureSubmoduleViewModel[];
}

export interface ArchitectureHierarchyViewModel {
  logicalArchitectureHash: string;
  profileId: string;
  profileLabel: string;
  profileDescription: string | null;
  workflowId: string;
  nodes: readonly ArchitectureNodeViewModel[];
  interconnects: readonly ArchitectureInterconnectViewModel[];
}

export interface TimelineEventViewModel {
  id: string;
  label: string;
  opcode: string;
  plane: "program" | "resource";
  instructionId: number | null;
  processId: string | null;
  startSeconds: number;
  endSeconds: number;
  durationSeconds: number;
  layerIndex: number | null;
  operationSummary: string | null;
  qubits: readonly number[];
  direction: string | null;
  programReadySeconds: number | null;
  resourceWaitSeconds: number | null;
  targetModules: readonly string[];
  waitReasons: readonly string[];
  runtimeLineage: RuntimeProgramLineageViewModel | null;
  measurements: readonly RuntimeMeasurementViewModel[];
  sourceMeasurements: readonly RuntimeMeasurementViewModel[];
  continuation: RuntimeContinuationViewModel | null;
  conditionalCorrection: boolean;
}

export interface TimelineRowViewModel {
  id: string;
  label: string;
  plane: "program" | "resource";
  events: readonly TimelineEventViewModel[];
}

export interface TimelineViewModel {
  fullDurationSeconds: number;
  displayDurationSeconds: number;
  maxProgramLayers: number;
  visibleProgramLayerCount: number;
  totalProgramLayerCount: number;
  truncated: boolean;
  /** Completion events in the selected causal time window before the DOM cap. */
  candidateEventCount: number;
  /** Completion events retained for rendering after applying the DOM cap. */
  renderedEventCount: number;
  eventTruncated: boolean;
  rows: readonly TimelineRowViewModel[];
}

export interface TimeBreakdownSegmentViewModel {
  key: string;
  label: string;
  seconds: number;
  fraction: number;
}

export interface TimeBreakdownViewModel {
  totalSeconds: number;
  unavailableReason: string | null;
  segments: readonly TimeBreakdownSegmentViewModel[];
}

export interface CircuitLayerSeriesPointViewModel {
  layerIndex: number;
  activeQubits: number;
  operationCount: number;
  nonCliffordCount: number;
  pauliOperationCount: number;
  meanPauliWeight: number;
  maxPauliWeight: number;
}

export interface CircuitStatisticsViewModel {
  representation: string;
  numQubits: number;
  numClbits: number;
  layerCount: number;
  operationCount: number;
  operationCounts: Readonly<Record<string, number>>;
  series: readonly CircuitLayerSeriesPointViewModel[];
  gbc: {
    totalGates: number;
    depth: number;
    tCount: number;
    maxWidth: number;
  } | null;
  pbc: {
    totalStrings: number;
    maxWeight: number;
    avgWeight: number;
    rotationCount: number;
    measurementCount: number;
  } | null;
}

export interface HeadlineMetricsViewModel {
  profileId: string;
  workflowId: string;
  reportHash: string;
  totalLatencySeconds: number;
  totalPhysicalQubits: number;
  successProbability: number | null;
  fidelityCompleteCoverage: boolean | null;
  completedProgramInstructions: number;
  eventCount: number;
}

export interface SpaceBreakdownViewModel {
  totalPhysicalQubits: number;
  unavailableReason: string | null;
  segments: readonly {
    key: string;
    label: string;
    physicalQubits: number;
    fraction: number;
  }[];
}

export interface FidelitySummaryViewModel {
  successProbability: number | null;
  completeCoverage: boolean | null;
  unavailableReason: string | null;
  negativeLogSuccessByCause: Readonly<Record<string, number>>;
  logicalIdleCyclesByLocation: Readonly<Record<string, number>>;
  resourceIdleCyclesByLocation: Readonly<Record<string, number>>;
  unprofiledLogicalIdleSeconds: Readonly<Record<string, number>>;
  unprofiledResourceIdleSeconds: Readonly<Record<string, number>>;
}

export interface RuntimeProgramLineageViewModel {
  workId: string;
  sourceInstructionId: number;
  parentEventId: number | null;
  recipeInvocationId: string | null;
  recipeId: string | null;
  stageIndex: number | null;
  step: ProgramWorkStep;
}

export interface RuntimeMeasurementViewModel {
  registerId: string;
  bit: number;
}

export interface RuntimeContinuationViewModel {
  kind: ProgramContinuationDocument["kind"];
  activatedWorkIds: readonly string[];
}

export interface RuntimeEventSemanticsModel {
  lineage: RuntimeProgramLineageViewModel | null;
  measurements: readonly RuntimeMeasurementViewModel[];
  continuation: RuntimeContinuationViewModel | null;
}

export interface CompletedEvaluationEventModel extends EvaluationEventDocument {
  runtime: RuntimeEventSemanticsModel | null;
}

export interface InjectionStageViewModel {
  index: number;
  stateKind: string;
  resourceId: string;
  bufferId: string;
  attemptDurationSeconds: number;
  unfavorableAction:
    | { kind: "next_stage"; stageIndex: number }
    | { kind: "logical_correction"; operation: string };
}

export interface InjectionRecipeViewModel {
  invocationId: string;
  recipeId: string;
  convention: string;
  sourceLayerIndex: number;
  sourceOperationIndex: number;
  qubits: readonly number[];
  reactionDurationSeconds: number;
  correctionDurationSeconds: number;
  stages: readonly InjectionStageViewModel[];
}

export interface OutputProgramInstructionViewModel {
  id: number;
  opcode: string;
  layerIndex: number | null;
  predecessors: readonly number[];
  qubits: readonly number[];
  targetModules: readonly string[];
  durationSeconds: number;
  operationSummary: string | null;
  recipes: readonly InjectionRecipeViewModel[];
}

export interface OutputProgramModel {
  schemaVersion: string | null;
  runtimeInjectionMode: string | null;
  unavailableReason: string | null;
  instructions: readonly OutputProgramInstructionViewModel[];
}

export interface DynamicProgramWorkViewModel {
  eventId: number;
  instructionId: number;
  opcode: string;
  startSeconds: number;
  endSeconds: number;
  durationSeconds: number;
  operationSummary: string | null;
  qubits: readonly number[];
  targetModules: readonly string[];
  lineage: RuntimeProgramLineageViewModel;
  measurements: readonly RuntimeMeasurementViewModel[];
  sourceMeasurements: readonly RuntimeMeasurementViewModel[];
  continuation: RuntimeContinuationViewModel | null;
  conditionalCorrection: boolean;
}

export interface ProgramExecutionViewModel {
  outputProgram: OutputProgramModel;
  dynamicWork: readonly DynamicProgramWorkViewModel[];
}

export interface FidelityEvidenceModel {
  negative_log_success_by_cause: Record<string, number>;
  logical_idle_cycles_by_location: Record<string, number>;
  resource_idle_cycles_by_location: Record<string, number>;
  unprofiled_logical_idle_exposure_s: Record<string, number>;
  unprofiled_resource_idle_exposure_s: Record<string, number>;
}

/**
 * The only report-shaped value allowed in React state and caches. Both report
 * wire versions are reduced to this model at the service boundary.
 */
export interface EvaluationReportModel {
  adapter_version: "arqsim.frontend-evaluation-model.v1";
  source_schema_version:
    | typeof EVALUATION_REPORT_V1
    | typeof EVALUATION_REPORT_V2;
  report_hash: string;
  profile_id: string;
  profile_label: string;
  profile_description: string | null;
  workflow_id: string;
  circuit: FTCircuitDocument;
  architecture: ArchitectureHierarchyViewModel;
  completed_events: CompletedEvaluationEventModel[];
  output_program: OutputProgramModel;
  fidelity_evidence: FidelityEvidenceModel;
  summary: {
    total_latency_s: number;
    total_physical_qubits: number;
    success_probability: number | null;
    fidelity_complete_coverage: boolean | null;
    completed_program_instructions: number;
    event_count: number;
    invariant_checks: Record<string, boolean>;
  };
  analysis: {
    exclusive_time_s: Record<string, number> | null;
    physical_space_qubits: Record<string, number>;
    engine_utilization: Record<string, number>;
    buffer_occupancy: Record<string, JsonRecord> | null;
    unavailable: Record<string, string>;
  };
}

export interface EvaluationViewModels {
  headline: HeadlineMetricsViewModel;
  architecture: ArchitectureHierarchyViewModel;
  timeline: TimelineViewModel;
  timeBreakdown: TimeBreakdownViewModel;
  spaceBreakdown: SpaceBreakdownViewModel;
  fidelity: FidelitySummaryViewModel;
  circuitStatistics: CircuitStatisticsViewModel;
  programExecution: ProgramExecutionViewModel;
}
