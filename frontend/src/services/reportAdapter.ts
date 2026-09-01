import {
  ARCHITECTURE_PROFILE_V3,
  ArchitectureHierarchyViewModel,
  ArchitectureInterconnectDocument,
  ArchitectureModuleDocument,
  ArchitectureNodeDocument,
  ArchitectureProfileModuleDocument,
  ArchitectureProfileNodeDocument,
  ArchitectureProfileSubmoduleDocument,
  ArchitectureProfileV3Document,
  ArchitectureSlotDocument,
  ArchitectureSubmoduleDocument,
  CircuitStatisticsViewModel,
  CompletedEvaluationEventModel,
  DynamicProgramWorkViewModel,
  EVALUATION_REPORT_V1,
  EVALUATION_REPORT_V2,
  EXECUTION_TRACE_V3,
  EvaluationEventDocument,
  EvaluationReportDocument,
  EvaluationReportModel,
  EvaluationReportV1,
  EvaluationReportV2,
  EvaluationViewModels,
  ExecutionPlanV6Document,
  ExecutionTraceV3Document,
  ExecutionTransitionV3Document,
  JsonRecord,
  JsonValue,
  OutputProgramModel,
  ProgramContinuationDocument,
  ProgramExecutionViewModel,
  ProgramInstructionDocument,
  ProgramWorkLineageDocument,
  QecBindingViewModel,
  RuntimeContinuationViewModel,
  RuntimeEventSemanticsModel,
  RuntimeMeasurementViewModel,
  RuntimeProgramLineageViewModel,
  SpaceBreakdownViewModel,
  TimelineEventViewModel,
  TimelineRowViewModel,
  TimelineViewModel,
  TimeBreakdownViewModel,
} from "@/types/evaluationReport";

export interface ReportViewOptions {
  /** Number of source Program layers included in the initial timeline window. */
  maxProgramLayers?: number;
}

export const DEFAULT_TIMELINE_LAYER_LIMIT = 12;

/**
 * Presentation safety rail. The canonical report remains complete; only the
 * browser view is capped so a large workload cannot create tens of thousands
 * of timeline DOM nodes in one click.
 */
export const MAX_TIMELINE_LAYER_LIMIT = 240;

/**
 * Independent DOM-node safety rail inside the selected causal layer window.
 * Program work is retained before Resource work so the executed program and
 * any runtime-expanded gadget path remain visible under heavy resource churn.
 */
export const MAX_TIMELINE_EVENT_LIMIT = 2_000;

function contractError(path: string, expectation: string): never {
  throw new Error(`Invalid evaluation report at ${path}: expected ${expectation}`);
}

function record(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return contractError(path, "an object");
  }
  return value as Record<string, unknown>;
}

function array(value: unknown, path: string): unknown[] {
  if (!Array.isArray(value)) return contractError(path, "an array");
  return value;
}

function string(value: unknown, path: string): string {
  if (typeof value !== "string") return contractError(path, "a string");
  return value;
}

function nonEmptyString(value: unknown, path: string): string {
  const result = string(value, path);
  if (result.length === 0 || result !== result.trim()) {
    return contractError(path, "a non-empty plain string");
  }
  return result;
}

function finiteNumber(value: unknown, path: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return contractError(path, "a finite number");
  }
  return value;
}

function nonNegativeNumber(value: unknown, path: string): number {
  const result = finiteNumber(value, path);
  if (result < 0) return contractError(path, "a non-negative finite number");
  return result;
}

function integer(value: unknown, path: string): number {
  const result = finiteNumber(value, path);
  if (!Number.isInteger(result)) return contractError(path, "an integer");
  return result;
}

function nonNegativeInteger(value: unknown, path: string): number {
  const result = integer(value, path);
  if (result < 0) return contractError(path, "a non-negative integer");
  return result;
}

function boolean(value: unknown, path: string): boolean {
  if (typeof value !== "boolean") return contractError(path, "a boolean");
  return value;
}

function exactFields(
  value: Record<string, unknown>,
  path: string,
  expected: readonly string[],
): void {
  const actual = Object.keys(value).sort();
  const canonical = [...expected].sort();
  if (
    actual.length !== canonical.length ||
    actual.some((field, index) => field !== canonical[index])
  ) {
    const missing = canonical.filter((field) => !actual.includes(field));
    const unknown = actual.filter((field) => !canonical.includes(field));
    contractError(
      path,
      `exact fields ${canonical.join(", ")}; missing [${missing.join(", ")}], unknown [${unknown.join(", ")}]`,
    );
  }
}

function nullableString(value: unknown, path: string): string | null {
  if (value === null) return null;
  return string(value, path);
}

function nullableNumber(value: unknown, path: string): number | null {
  if (value === null) return null;
  return finiteNumber(value, path);
}

function nullableBoolean(value: unknown, path: string): boolean | null {
  if (value === null) return null;
  if (typeof value !== "boolean") return contractError(path, "a boolean or null");
  return value;
}

function stringArray(value: unknown, path: string): string[] {
  return array(value, path).map((item, index) => string(item, `${path}[${index}]`));
}

function numberArray(value: unknown, path: string): number[] {
  return array(value, path).map((item, index) => finiteNumber(item, `${path}[${index}]`));
}

function coordinatePair(value: unknown, path: string): [number, number] {
  const result = numberArray(value, path);
  if (result.length !== 2) return contractError(path, "exactly two finite coordinates");
  return [result[0], result[1]];
}

function numberRecord(value: unknown, path: string): Record<string, number> {
  const source = record(value, path);
  for (const key in source) {
    if (!Object.prototype.hasOwnProperty.call(source, key)) continue;
    nonNegativeNumber(source[key], `${path}.${key}`);
  }
  return source as Record<string, number>;
}

function booleanRecord(value: unknown, path: string): Record<string, boolean> {
  const source = record(value, path);
  for (const key in source) {
    if (!Object.prototype.hasOwnProperty.call(source, key)) continue;
    if (typeof source[key] !== "boolean") contractError(`${path}.${key}`, "a boolean");
  }
  return source as Record<string, boolean>;
}

function jsonValue(value: unknown, path: string): JsonValue {
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean"
  ) {
    return value as JsonValue;
  }
  if (typeof value === "number") return finiteNumber(value, path);
  if (Array.isArray(value)) {
    for (let index = 0; index < value.length; index += 1) {
      jsonValue(value[index], `${path}[${index}]`);
    }
    return value as JsonValue;
  }
  const source = record(value, path);
  for (const key in source) {
    if (!Object.prototype.hasOwnProperty.call(source, key)) continue;
    jsonValue(source[key], `${path}.${key}`);
  }
  return source as JsonRecord;
}

function jsonRecord(value: unknown, path: string): JsonRecord {
  return jsonValue(record(value, path), path) as JsonRecord;
}

function optionalString(value: unknown, path: string): string | undefined {
  return value === undefined ? undefined : string(value, path);
}

function optionalNumber(value: unknown, path: string): number | undefined {
  return value === undefined ? undefined : finiteNumber(value, path);
}

function validateSlot(value: unknown, path: string): void {
  const slot = record(value, path);
  string(slot.id, `${path}.id`);
  optionalString(slot.ref, `${path}.ref`);
  optionalString(slot.kind, `${path}.kind`);
  optionalString(slot.zone, `${path}.zone`);
  optionalString(slot.adjacent_to, `${path}.adjacent_to`);
  if (slot.coordinate !== undefined) coordinatePair(slot.coordinate, `${path}.coordinate`);
  if (slot.interfaces !== undefined) stringArray(slot.interfaces, `${path}.interfaces`);
}

function validateSubmodule(value: unknown, path: string): void {
  const submodule = record(value, path);
  string(submodule.id, `${path}.id`);
  string(submodule.ref, `${path}.ref`);
  string(submodule.type, `${path}.type`);
  optionalString(submodule.payload, `${path}.payload`);
  optionalNumber(submodule.copy_count, `${path}.copy_count`);
  optionalString(submodule.coordinate_semantics, `${path}.coordinate_semantics`);
  if (submodule.logical_origin !== undefined) {
    coordinatePair(submodule.logical_origin, `${path}.logical_origin`);
  }
  if (submodule.capacity !== undefined) numberRecord(submodule.capacity, `${path}.capacity`);
  if (submodule.qec !== undefined) {
    const qec = record(submodule.qec, `${path}.qec`);
    string(qec.code, `${path}.qec.code`);
    jsonRecord(qec.parameters, `${path}.qec.parameters`);
  }
  if (submodule.resource_protocol !== undefined) {
    const protocol = record(submodule.resource_protocol, `${path}.resource_protocol`);
    string(protocol.id, `${path}.resource_protocol.id`);
  }
  if (submodule.slots !== undefined) {
    array(submodule.slots, `${path}.slots`).forEach((slot, index) =>
      validateSlot(slot, `${path}.slots[${index}]`),
    );
  }
}

function validateModule(value: unknown, path: string): void {
  const module = record(value, path);
  string(module.id, `${path}.id`);
  string(module.ref, `${path}.ref`);
  string(module.type, `${path}.type`);
  array(module.submodules, `${path}.submodules`).forEach((submodule, index) =>
    validateSubmodule(submodule, `${path}.submodules[${index}]`),
  );
}

function validateNode(value: unknown, path: string): void {
  const node = record(value, path);
  string(node.id, `${path}.id`);
  string(node.modality, `${path}.modality`);
  array(node.modules, `${path}.modules`).forEach((module, index) =>
    validateModule(module, `${path}.modules[${index}]`),
  );
  array(node.connections, `${path}.connections`).forEach((connectionValue, index) => {
    const connectionPath = `${path}.connections[${index}]`;
    const connection = record(connectionValue, connectionPath);
    string(connection.id, `${connectionPath}.id`);
    string(connection.direction, `${connectionPath}.direction`);
    optionalString(connection.payload, `${connectionPath}.payload`);
    optionalString(connection.from, `${connectionPath}.from`);
    optionalString(connection.to, `${connectionPath}.to`);
    if (connection.endpoints !== undefined) {
      stringArray(connection.endpoints, `${connectionPath}.endpoints`);
    }
  });
  if (node.coordinate_frame !== undefined) {
    const frame = record(node.coordinate_frame, `${path}.coordinate_frame`);
    string(frame.id, `${path}.coordinate_frame.id`);
    const dimensions = integer(frame.dimensions, `${path}.coordinate_frame.dimensions`);
    if (dimensions !== 2) {
      contractError(`${path}.coordinate_frame.dimensions`, "the 2D logical frame value 2");
    }
    string(frame.unit, `${path}.coordinate_frame.unit`);
  }
}

function validateInterconnect(value: unknown, path: string): void {
  const interconnect = record(value, path);
  string(interconnect.id, `${path}.id`);
  stringArray(interconnect.endpoints, `${path}.endpoints`);
  array(interconnect.access, `${path}.access`).forEach((accessValue, index) => {
    const accessPath = `${path}.access[${index}]`;
    const access = record(accessValue, accessPath);
    string(access.node, `${accessPath}.node`);
    string(access.submodule, `${accessPath}.submodule`);
    stringArray(access.local_submodules, `${accessPath}.local_submodules`);
  });
  array(interconnect.submodules, `${path}.submodules`).forEach((submodule, index) =>
    validateSubmodule(submodule, `${path}.submodules[${index}]`),
  );
}

function validateCircuit(value: unknown, path: string): void {
  const circuit = record(value, path);
  string(circuit.schema_version, `${path}.schema_version`);
  string(circuit.representation, `${path}.representation`);
  nonNegativeNumber(circuit.num_qubits, `${path}.num_qubits`);
  nonNegativeNumber(circuit.num_clbits, `${path}.num_clbits`);
  string(circuit.semantic_hash, `${path}.semantic_hash`);
  jsonRecord(circuit.provenance, `${path}.provenance`);
  array(circuit.layers, `${path}.layers`).forEach((layerValue, layerIndex) => {
    const layerPath = `${path}.layers[${layerIndex}]`;
    const layer = record(layerValue, layerPath);
    integer(layer.index, `${layerPath}.index`);
    numberArray(layer.active_qubits, `${layerPath}.active_qubits`);
    array(layer.operations, `${layerPath}.operations`).forEach((operationValue, operationIndex) => {
      const operationPath = `${layerPath}.operations[${operationIndex}]`;
      const operation = record(operationValue, operationPath);
      string(operation.kind, `${operationPath}.kind`);
      string(operation.name, `${operationPath}.name`);
      numberArray(operation.qubits, `${operationPath}.qubits`);
      numberArray(operation.classical_bits, `${operationPath}.classical_bits`);
      array(operation.parameters, `${operationPath}.parameters`).forEach((parameter, index) =>
        jsonValue(parameter, `${operationPath}.parameters[${index}]`),
      );
      optionalString(operation.pauli, `${operationPath}.pauli`);
      optionalNumber(operation.weight, `${operationPath}.weight`);
    });
  });
}

function validateCircuitV2(value: unknown, path: string): void {
  validateCircuit(value, path);
  const circuit = record(value, path);
  exactFields(circuit, path, [
    "schema_version",
    "representation",
    "num_qubits",
    "num_clbits",
    "layers",
    "semantic_hash",
    "provenance",
  ]);
  nonNegativeInteger(circuit.num_qubits, `${path}.num_qubits`);
  nonNegativeInteger(circuit.num_clbits, `${path}.num_clbits`);
  array(circuit.layers, `${path}.layers`).forEach((layerValue, layerIndex) => {
    const layerPath = `${path}.layers[${layerIndex}]`;
    const layer = record(layerValue, layerPath);
    exactFields(layer, layerPath, ["index", "active_qubits", "operations"]);
    nonNegativeInteger(layer.index, `${layerPath}.index`);
    array(layer.active_qubits, `${layerPath}.active_qubits`).forEach((qubit, index) =>
      nonNegativeInteger(qubit, `${layerPath}.active_qubits[${index}]`),
    );
    array(layer.operations, `${layerPath}.operations`).forEach((operationValue, operationIndex) => {
      const operationPath = `${layerPath}.operations[${operationIndex}]`;
      const operation = record(operationValue, operationPath);
      const required = ["kind", "name", "qubits", "classical_bits", "parameters"];
      const hasPauli = operation.pauli !== undefined || operation.weight !== undefined;
      exactFields(operation, operationPath, hasPauli ? [...required, "pauli", "weight"] : required);
      array(operation.qubits, `${operationPath}.qubits`).forEach((qubit, index) =>
        nonNegativeInteger(qubit, `${operationPath}.qubits[${index}]`),
      );
      array(operation.classical_bits, `${operationPath}.classical_bits`).forEach((bit, index) =>
        nonNegativeInteger(bit, `${operationPath}.classical_bits[${index}]`),
      );
      if (hasPauli) {
        string(operation.pauli, `${operationPath}.pauli`);
        nonNegativeInteger(operation.weight, `${operationPath}.weight`);
      }
    });
  });
}

function validateEvent(value: unknown, path: string): void {
  const event = record(value, path);
  integer(event.event_id, `${path}.event_id`);
  const plane = string(event.plane, `${path}.plane`);
  if (plane !== "program" && plane !== "resource") {
    contractError(`${path}.plane`, '"program" or "resource"');
  }
  if (event.instruction_id !== null) integer(event.instruction_id, `${path}.instruction_id`);
  if (event.process_id !== null) string(event.process_id, `${path}.process_id`);
  string(event.opcode, `${path}.opcode`);
  nonNegativeNumber(event.start_s, `${path}.start_s`);
  nonNegativeNumber(event.end_s, `${path}.end_s`);
  nonNegativeNumber(event.duration_s, `${path}.duration_s`);
  record(event.metadata, `${path}.metadata`);
  stringArray(event.wait_reasons, `${path}.wait_reasons`);
}

/**
 * Validate the frontend integration boundary without rejecting additive v1
 * diagnostics. Hash/causal replay validation remains the backend's authority.
 */
export function parseEvaluationReport(value: unknown): EvaluationReportV1 {
  const root = record(value, "report");
  const schemaVersion = string(root.schema_version, "report.schema_version");
  if (schemaVersion !== EVALUATION_REPORT_V1) {
    throw new Error(
      `Unsupported evaluation report schema ${JSON.stringify(schemaVersion)}; ` +
        `expected ${EVALUATION_REPORT_V1}`,
    );
  }
  string(root.report_hash, "report.report_hash");

  const config = record(root.config, "report.config");
  string(config.profile_id, "report.config.profile_id");
  nullableString(config.workflow_id, "report.config.workflow_id");

  const effective = record(root.effective_configuration, "report.effective_configuration");
  string(effective.workflow_id, "report.effective_configuration.workflow_id");

  const workload = record(root.workload, "report.workload");
  validateCircuit(workload.evaluated, "report.workload.evaluated");
  if (workload.magic_sizing_reference !== null) {
    validateCircuit(workload.magic_sizing_reference, "report.workload.magic_sizing_reference");
  }

  const specification = record(root.specification, "report.specification");
  const profile = record(specification.profile, "report.specification.profile");
  string(profile.id, "report.specification.profile.id");
  string(profile.name, "report.specification.profile.name");
  optionalString(profile.description, "report.specification.profile.description");
  const workflow = record(specification.workflow, "report.specification.workflow");
  string(workflow.id, "report.specification.workflow.id");
  nonNegativeNumber(workflow.logical_qubits, "report.specification.workflow.logical_qubits");
  const architecture = record(
    specification.logical_architecture,
    "report.specification.logical_architecture",
  );
  string(architecture.schema_version, "report.specification.logical_architecture.schema_version");
  string(
    architecture.logical_architecture_hash,
    "report.specification.logical_architecture.logical_architecture_hash",
  );
  array(architecture.nodes, "report.specification.logical_architecture.nodes").forEach(
    (node, index) => validateNode(node, `report.specification.logical_architecture.nodes[${index}]`),
  );
  array(
    architecture.interconnects,
    "report.specification.logical_architecture.interconnects",
  ).forEach((interconnect, index) =>
    validateInterconnect(
      interconnect,
      `report.specification.logical_architecture.interconnects[${index}]`,
    ),
  );

  const evaluation = record(root.evaluation, "report.evaluation");
  nonNegativeNumber(evaluation.total_latency_s, "report.evaluation.total_latency_s");
  array(evaluation.events, "report.evaluation.events").forEach((event, index) =>
    validateEvent(event, `report.evaluation.events[${index}]`),
  );

  const summary = record(root.summary, "report.summary");
  nonNegativeNumber(summary.total_latency_s, "report.summary.total_latency_s");
  nonNegativeNumber(summary.total_physical_qubits, "report.summary.total_physical_qubits");
  nullableNumber(summary.success_probability, "report.summary.success_probability");
  nullableBoolean(
    summary.fidelity_complete_coverage,
    "report.summary.fidelity_complete_coverage",
  );
  nonNegativeNumber(
    summary.completed_program_instructions,
    "report.summary.completed_program_instructions",
  );
  nonNegativeNumber(summary.event_count, "report.summary.event_count");
  booleanRecord(summary.invariant_checks, "report.summary.invariant_checks");

  const analysis = record(root.analysis, "report.analysis");
  if (analysis.exclusive_time_s !== null) {
    numberRecord(analysis.exclusive_time_s, "report.analysis.exclusive_time_s");
  }
  numberRecord(analysis.physical_space_qubits, "report.analysis.physical_space_qubits");
  numberRecord(analysis.engine_utilization, "report.analysis.engine_utilization");
  if (analysis.buffer_occupancy !== null) {
    const occupancy = record(analysis.buffer_occupancy, "report.analysis.buffer_occupancy");
    Object.entries(occupancy).forEach(([bufferId, metrics]) =>
      numberRecord(metrics, `report.analysis.buffer_occupancy.${bufferId}`),
    );
  }
  const unavailable = record(analysis.unavailable, "report.analysis.unavailable");
  Object.entries(unavailable).forEach(([key, item]) =>
    string(item, `report.analysis.unavailable.${key}`),
  );

  return value as EvaluationReportV1;
}

const REPORT_V2_FIELDS = [
  "schema_version",
  "workflow_id",
  "request",
  "resolved_inputs",
  "artifacts",
  "results",
  "report_hash",
] as const;

const TRANSITION_V3_FIELDS = [
  "transition_id",
  "kind",
  "time_s",
  "event_id",
  "reservation_id",
  "state_version_before",
  "state_version_after",
  "plane",
  "opcode",
  "instruction_id",
  "process_id",
  "instance",
  "candidate_id",
  "start_s",
  "end_s",
  "wait_reasons",
  "consumes",
  "consumed_tokens",
  "consumed_slots",
  "produces",
  "produced_slots",
  "produced_tokens",
  "forwards",
  "engines",
  "required_locations",
  "completion_locations",
  "token_sequence_after",
  "buffer_occupancy_after",
  "pending_incoming_after",
  "metadata",
  "backend_artifact",
  "outcome",
  "program_lineage",
  "measurements",
  "continuation",
] as const;

const EXECUTION_PLAN_V6_FIELDS = [
  "schema_version",
  "source",
  "architecture_hash",
  "latency_profile_hash",
  "policy",
  "program_dag",
  "resource_dag",
  "architectural_state",
  "provenance",
  "runtime_components",
  "plan_hash",
] as const;

const EVALUATION_POLICY_V1_FIELDS = [
  "magic_state_consumption",
  "store_load_policy",
  "resource_fill_policy",
  "trace_level",
  "runtime_injection_mode",
  "selected_layers",
  "seed",
  "max_events",
] as const;

const SUPPORTED_INJECTION_CONVENTIONS = [
  "cx_data_magic_measure_magic_z_v1",
] as const;

function schemaRecord(
  value: unknown,
  path: string,
  schemaVersion: string,
): Record<string, unknown> {
  const result = record(value, path);
  if (string(result.schema_version, `${path}.schema_version`) !== schemaVersion) {
    contractError(`${path}.schema_version`, JSON.stringify(schemaVersion));
  }
  jsonRecord(result, path);
  return result;
}

function validateV2Config(value: unknown, path: string): Record<string, unknown> {
  const config = record(value, path);
  const required = [
    "schema_version",
    "profile_id",
    "workflow_id",
    "layout_policy_overrides",
    "latency_profile",
    "evaluation_policy",
    "compiler_spec",
    "fidelity_profile",
    "footprint_model",
    "runtime_components",
  ];
  const allowed = [...required, "logical_layout"];
  const actual = Object.keys(config);
  const missing = required.filter((field) => !actual.includes(field));
  const unknown = actual.filter((field) => !allowed.includes(field));
  if (missing.length || unknown.length) {
    contractError(
      path,
      `EvaluationConfig v1 fields; missing [${missing.join(", ")}], unknown [${unknown.join(", ")}]`,
    );
  }
  if (string(config.schema_version, `${path}.schema_version`) !== "arqsim.evaluation-config.v1") {
    contractError(`${path}.schema_version`, '"arqsim.evaluation-config.v1"');
  }
  string(config.profile_id, `${path}.profile_id`);
  nullableString(config.workflow_id, `${path}.workflow_id`);
  jsonRecord(config.layout_policy_overrides, `${path}.layout_policy_overrides`);
  jsonRecord(config.latency_profile, `${path}.latency_profile`);
  jsonRecord(config.evaluation_policy, `${path}.evaluation_policy`);
  jsonRecord(config.compiler_spec, `${path}.compiler_spec`);
  if (config.fidelity_profile !== null) {
    jsonRecord(config.fidelity_profile, `${path}.fidelity_profile`);
  }
  jsonRecord(config.footprint_model, `${path}.footprint_model`);
  jsonRecord(config.runtime_components, `${path}.runtime_components`);
  if (config.logical_layout !== undefined) {
    jsonRecord(config.logical_layout, `${path}.logical_layout`);
  }
  return config;
}

function validateCanonicalSubmodule(value: unknown, path: string): void {
  const submodule = record(value, path);
  const required = ["id", "type", "payload", "capacity"];
  const optional = ["slots", "qec", "logical_origin", "grid_shape", "resource_protocol"];
  const actual = Object.keys(submodule);
  const missing = required.filter((field) => !actual.includes(field));
  const unknown = actual.filter((field) => !required.includes(field) && !optional.includes(field));
  if (missing.length || unknown.length) {
    contractError(
      path,
      `canonical Submodule fields; missing [${missing.join(", ")}], unknown [${unknown.join(", ")}]`,
    );
  }
  string(submodule.id, `${path}.id`);
  string(submodule.type, `${path}.type`);
  string(submodule.payload, `${path}.payload`);
  nonNegativeInteger(submodule.capacity, `${path}.capacity`);
  if (submodule.slots !== undefined) {
    array(submodule.slots, `${path}.slots`).forEach((slotValue, index) => {
      const slotPath = `${path}.slots[${index}]`;
      const slot = record(slotValue, slotPath);
      const fields = slot.coordinate === undefined ? ["id"] : ["id", "coordinate"];
      exactFields(slot, slotPath, fields);
      string(slot.id, `${slotPath}.id`);
      if (slot.coordinate !== undefined) coordinatePair(slot.coordinate, `${slotPath}.coordinate`);
    });
  }
  if (submodule.qec !== undefined) {
    const qec = record(submodule.qec, `${path}.qec`);
    exactFields(qec, `${path}.qec`, ["code", "parameters"]);
    string(qec.code, `${path}.qec.code`);
    jsonRecord(qec.parameters, `${path}.qec.parameters`);
  }
  if (submodule.logical_origin !== undefined) {
    coordinatePair(submodule.logical_origin, `${path}.logical_origin`);
  }
  if (submodule.grid_shape !== undefined) {
    const shape = record(submodule.grid_shape, `${path}.grid_shape`);
    exactFields(shape, `${path}.grid_shape`, ["rows", "columns"]);
    nonNegativeInteger(shape.rows, `${path}.grid_shape.rows`);
    nonNegativeInteger(shape.columns, `${path}.grid_shape.columns`);
  }
  if (submodule.resource_protocol !== undefined) {
    const protocol = record(submodule.resource_protocol, `${path}.resource_protocol`);
    exactFields(protocol, `${path}.resource_protocol`, ["id", "profile_hash"]);
    string(protocol.id, `${path}.resource_protocol.id`);
    string(protocol.profile_hash, `${path}.resource_protocol.profile_hash`);
  }
}

function validateCanonicalModule(value: unknown, path: string): void {
  const module = record(value, path);
  exactFields(module, path, ["id", "type", "submodules"]);
  string(module.id, `${path}.id`);
  string(module.type, `${path}.type`);
  array(module.submodules, `${path}.submodules`).forEach((item, index) =>
    validateCanonicalSubmodule(item, `${path}.submodules[${index}]`),
  );
}

function validateCanonicalConnection(value: unknown, path: string): void {
  const connection = record(value, path);
  exactFields(connection, path, ["id", "direction", "endpoints"]);
  string(connection.id, `${path}.id`);
  const direction = string(connection.direction, `${path}.direction`);
  if (direction !== "directed" && direction !== "bidirectional") {
    contractError(`${path}.direction`, '"directed" or "bidirectional"');
  }
  const endpoints = stringArray(connection.endpoints, `${path}.endpoints`);
  if (endpoints.length !== 2) contractError(`${path}.endpoints`, "exactly two endpoints");
}

function validateCanonicalArchitecture(value: unknown, path: string): Record<string, unknown> {
  const architecture = record(value, path);
  exactFields(architecture, path, ["schema_version", "nodes", "interconnects", "architecture_hash"]);
  if (
    string(architecture.schema_version, `${path}.schema_version`) !==
    "arqsim.architecture-specification.v3"
  ) {
    contractError(`${path}.schema_version`, '"arqsim.architecture-specification.v3"');
  }
  string(architecture.architecture_hash, `${path}.architecture_hash`);
  array(architecture.nodes, `${path}.nodes`).forEach((nodeValue, index) => {
    const nodePath = `${path}.nodes[${index}]`;
    const node = record(nodeValue, nodePath);
    exactFields(node, nodePath, ["id", "modality", "modules", "connections"]);
    string(node.id, `${nodePath}.id`);
    string(node.modality, `${nodePath}.modality`);
    array(node.modules, `${nodePath}.modules`).forEach((item, moduleIndex) =>
      validateCanonicalModule(item, `${nodePath}.modules[${moduleIndex}]`),
    );
    array(node.connections, `${nodePath}.connections`).forEach((item, connectionIndex) =>
      validateCanonicalConnection(item, `${nodePath}.connections[${connectionIndex}]`),
    );
  });
  array(architecture.interconnects, `${path}.interconnects`).forEach(
    (interconnectValue, index) => {
      const interconnectPath = `${path}.interconnects[${index}]`;
      const interconnect = record(interconnectValue, interconnectPath);
      exactFields(interconnect, interconnectPath, ["id", "endpoints", "modules", "connections"]);
      string(interconnect.id, `${interconnectPath}.id`);
      stringArray(interconnect.endpoints, `${interconnectPath}.endpoints`);
      array(interconnect.modules, `${interconnectPath}.modules`).forEach((item, moduleIndex) =>
        validateCanonicalModule(item, `${interconnectPath}.modules[${moduleIndex}]`),
      );
      array(interconnect.connections, `${interconnectPath}.connections`).forEach(
        (item, connectionIndex) =>
          validateCanonicalConnection(item, `${interconnectPath}.connections[${connectionIndex}]`),
      );
    },
  );
  return architecture;
}

function validateProgramLineage(
  value: unknown,
  path: string,
): ProgramWorkLineageDocument {
  const lineage = record(value, path);
  exactFields(lineage, path, [
    "work_id",
    "source_instruction_id",
    "parent_event_id",
    "recipe_invocation_id",
    "recipe_id",
    "stage_index",
    "step",
  ]);
  string(lineage.work_id, `${path}.work_id`);
  nonNegativeInteger(lineage.source_instruction_id, `${path}.source_instruction_id`);
  const parentEventId = lineage.parent_event_id;
  if (parentEventId !== null) nonNegativeInteger(parentEventId, `${path}.parent_event_id`);
  const recipeInvocationId = nullableString(
    lineage.recipe_invocation_id,
    `${path}.recipe_invocation_id`,
  );
  const recipeId = nullableString(lineage.recipe_id, `${path}.recipe_id`);
  const stageIndex = lineage.stage_index;
  if (stageIndex !== null) nonNegativeInteger(stageIndex, `${path}.stage_index`);
  const step = string(lineage.step, `${path}.step`);
  if (!["source", "injection", "reaction", "correction"].includes(step)) {
    contractError(`${path}.step`, '"source", "injection", "reaction", or "correction"');
  }
  if (step === "source") {
    if (
      parentEventId !== null ||
      recipeInvocationId !== null ||
      recipeId !== null ||
      stageIndex !== null
    ) {
      contractError(path, "source work without parent or recipe-stage identity");
    }
  } else if (
    parentEventId === null ||
    recipeInvocationId === null ||
    recipeId === null ||
    stageIndex === null
  ) {
    contractError(path, "continuation work with complete parent and recipe-stage identity");
  }
  return lineage as unknown as ProgramWorkLineageDocument;
}

function validateMeasurements(value: unknown, path: string): Record<string, number> {
  const measurements = record(value, path);
  for (const [registerId, bit] of Object.entries(measurements)) {
    if (!registerId) contractError(path, "non-empty measurement register IDs");
    const parsed = nonNegativeInteger(bit, `${path}.${registerId}`);
    if (parsed !== 0 && parsed !== 1) {
      contractError(`${path}.${registerId}`, "a measurement bit (0 or 1)");
    }
  }
  return measurements as Record<string, number>;
}

function validateContinuation(
  value: unknown,
  path: string,
): ProgramContinuationDocument {
  const continuation = record(value, path);
  exactFields(continuation, path, ["kind", "activated_work_ids"]);
  const kind = string(continuation.kind, `${path}.kind`);
  if (kind !== "activate" && kind !== "complete_source") {
    contractError(`${path}.kind`, '"activate" or "complete_source"');
  }
  const activatedWorkIds = stringArray(
    continuation.activated_work_ids,
    `${path}.activated_work_ids`,
  );
  if (activatedWorkIds.some((workId) => workId.length === 0 || workId !== workId.trim())) {
    contractError(`${path}.activated_work_ids`, "non-empty plain work IDs");
  }
  if (new Set(activatedWorkIds).size !== activatedWorkIds.length) {
    contractError(`${path}.activated_work_ids`, "unique work IDs");
  }
  if (kind === "complete_source" && activatedWorkIds.length > 0) {
    contractError(`${path}.activated_work_ids`, "an empty array for complete_source");
  }
  return continuation as unknown as ProgramContinuationDocument;
}

function validateInjectionRecipe(value: unknown, path: string): void {
  const recipe = record(value, path);
  exactFields(recipe, path, [
    "schema_version",
    "kind",
    "invocation_id",
    "recipe_id",
    "source_layer_index",
    "source_operation_index",
    "qubits",
    "stages",
    "data_mapping",
    "compute_location",
    "compute_engine",
    "reaction_duration_s",
    "correction_duration_s",
    "convention",
  ]);
  if (
    string(recipe.schema_version, `${path}.schema_version`) !==
    "arqsim.injection-recipe.v1"
  ) {
    contractError(`${path}.schema_version`, '"arqsim.injection-recipe.v1"');
  }
  if (string(recipe.kind, `${path}.kind`) !== "finite_state_injection") {
    contractError(`${path}.kind`, '"finite_state_injection"');
  }
  nonEmptyString(recipe.invocation_id, `${path}.invocation_id`);
  nonEmptyString(recipe.recipe_id, `${path}.recipe_id`);
  nonNegativeInteger(recipe.source_layer_index, `${path}.source_layer_index`);
  nonNegativeInteger(recipe.source_operation_index, `${path}.source_operation_index`);
  array(recipe.qubits, `${path}.qubits`).forEach((qubit, index) =>
    nonNegativeInteger(qubit, `${path}.qubits[${index}]`),
  );
  const stages = array(recipe.stages, `${path}.stages`);
  if (stages.length === 0) contractError(`${path}.stages`, "at least one injection stage");
  stages.forEach((stageValue, index) => {
    const stagePath = `${path}.stages[${index}]`;
    const stage = record(stageValue, stagePath);
    const hasNext = stage.failure_next_stage !== undefined;
    const hasCorrection = stage.failure_correction !== undefined;
    exactFields(stage, stagePath, [
      "index",
      "resource",
      "attempt_duration_s",
      ...(hasNext ? ["failure_next_stage"] : []),
      ...(hasCorrection ? ["failure_correction"] : []),
    ]);
    if (hasNext === hasCorrection) {
      contractError(stagePath, "exactly one unfavorable-outcome action");
    }
    if (nonNegativeInteger(stage.index, `${stagePath}.index`) !== index) {
      contractError(`${stagePath}.index`, `contiguous value ${index}`);
    }
    nonNegativeNumber(stage.attempt_duration_s, `${stagePath}.attempt_duration_s`);
    if (hasNext) {
      const nextStage = nonNegativeInteger(
        stage.failure_next_stage,
        `${stagePath}.failure_next_stage`,
      );
      if (nextStage !== index + 1 || nextStage >= stages.length) {
        contractError(
          `${stagePath}.failure_next_stage`,
          `the next valid stage index ${index + 1}`,
        );
      }
    } else {
      nonEmptyString(stage.failure_correction, `${stagePath}.failure_correction`);
    }
    const resourcePath = `${stagePath}.resource`;
    const resource = record(stage.resource, resourcePath);
    const resourceFields = ["ref_id", "state_kind", "buffer_id", "token_kind", "quantity"];
    exactFields(
      resource,
      resourcePath,
      resource.angle === undefined ? resourceFields : [...resourceFields, "angle"],
    );
    string(resource.ref_id, `${resourcePath}.ref_id`);
    const stateKind = string(resource.state_kind, `${resourcePath}.state_kind`);
    if (stateKind !== "t_magic" && stateKind !== "rz_angle") {
      contractError(`${resourcePath}.state_kind`, '"t_magic" or "rz_angle"');
    }
    string(resource.buffer_id, `${resourcePath}.buffer_id`);
    string(resource.token_kind, `${resourcePath}.token_kind`);
    const quantity = nonNegativeInteger(resource.quantity, `${resourcePath}.quantity`);
    if (quantity === 0) contractError(`${resourcePath}.quantity`, "a positive integer");
    if (resource.angle !== undefined) jsonRecord(resource.angle, `${resourcePath}.angle`);
  });
  stages.slice(0, -1).forEach((stageValue, index) => {
    if (!Object.prototype.hasOwnProperty.call(record(stageValue, `${path}.stages[${index}]`), "failure_next_stage")) {
      contractError(
        `${path}.stages[${index}]`,
        "a nonterminal stage that continues to the next stage",
      );
    }
  });
  const terminalStagePath = `${path}.stages[${stages.length - 1}]`;
  if (
    !Object.prototype.hasOwnProperty.call(
      record(stages[stages.length - 1], terminalStagePath),
      "failure_correction",
    )
  ) {
    contractError(terminalStagePath, "a terminal logical correction");
  }
  const mapping = record(recipe.data_mapping, `${path}.data_mapping`);
  Object.entries(mapping).forEach(([qubit, slot]) => {
    if (!/^\d+$/.test(qubit)) contractError(`${path}.data_mapping`, "canonical qubit keys");
    string(slot, `${path}.data_mapping.${qubit}`);
  });
  nonEmptyString(recipe.compute_location, `${path}.compute_location`);
  nonEmptyString(recipe.compute_engine, `${path}.compute_engine`);
  nonNegativeNumber(recipe.reaction_duration_s, `${path}.reaction_duration_s`);
  nonNegativeNumber(recipe.correction_duration_s, `${path}.correction_duration_s`);
  const convention = string(recipe.convention, `${path}.convention`);
  if (!(SUPPORTED_INJECTION_CONVENTIONS as readonly string[]).includes(convention)) {
    contractError(
      `${path}.convention`,
      `one of ${SUPPORTED_INJECTION_CONVENTIONS.map((item) => JSON.stringify(item)).join(", ")}`,
    );
  }
}

function validateProgramInstruction(value: unknown, path: string): void {
  const instruction = record(value, path);
  nonNegativeInteger(instruction.id, `${path}.id`);
  string(instruction.opcode, `${path}.opcode`);
  nonNegativeNumber(instruction.duration_s, `${path}.duration_s`);
  if (instruction.layer !== undefined) nonNegativeInteger(instruction.layer, `${path}.layer`);
  array(instruction.predecessors, `${path}.predecessors`).forEach((item, index) =>
    nonNegativeInteger(item, `${path}.predecessors[${index}]`),
  );
  array(instruction.qubits, `${path}.qubits`).forEach((item, index) =>
    nonNegativeInteger(item, `${path}.qubits[${index}]`),
  );
  stringArray(instruction.target_modules, `${path}.target_modules`);
  jsonRecord(instruction.metadata, `${path}.metadata`);
  if (instruction.implementation_recipes !== undefined) {
    array(instruction.implementation_recipes, `${path}.implementation_recipes`).forEach(
      (recipe, index) => validateInjectionRecipe(recipe, `${path}.implementation_recipes[${index}]`),
    );
  }
}

function validateExecutionPlanV6(value: unknown, path: string): ExecutionPlanV6Document {
  const plan = schemaRecord(value, path, "arqsim.execution-plan.v6");
  exactFields(plan, path, EXECUTION_PLAN_V6_FIELDS);

  const source = record(plan.source, `${path}.source`);
  exactFields(source, `${path}.source`, ["circuit_hash"]);
  nonEmptyString(source.circuit_hash, `${path}.source.circuit_hash`);
  nonEmptyString(plan.architecture_hash, `${path}.architecture_hash`);
  nonEmptyString(plan.latency_profile_hash, `${path}.latency_profile_hash`);
  nonEmptyString(plan.plan_hash, `${path}.plan_hash`);

  const policy = record(plan.policy, `${path}.policy`);
  exactFields(policy, `${path}.policy`, EVALUATION_POLICY_V1_FIELDS);
  string(policy.magic_state_consumption, `${path}.policy.magic_state_consumption`);
  string(policy.store_load_policy, `${path}.policy.store_load_policy`);
  string(policy.resource_fill_policy, `${path}.policy.resource_fill_policy`);
  string(policy.trace_level, `${path}.policy.trace_level`);
  const runtimeInjectionMode = string(
    policy.runtime_injection_mode,
    `${path}.policy.runtime_injection_mode`,
  );
  if (runtimeInjectionMode !== "black_box" && runtimeInjectionMode !== "finite_state_injection_v1") {
    contractError(
      `${path}.policy.runtime_injection_mode`,
      '"black_box" or "finite_state_injection_v1"',
    );
  }
  array(policy.selected_layers, `${path}.policy.selected_layers`).forEach((layer, index) =>
    nonNegativeInteger(layer, `${path}.policy.selected_layers[${index}]`),
  );
  integer(policy.seed, `${path}.policy.seed`);
  const maxEvents = nonNegativeInteger(policy.max_events, `${path}.policy.max_events`);
  if (maxEvents === 0) contractError(`${path}.policy.max_events`, "a positive integer");

  const dag = record(plan.program_dag, `${path}.program_dag`);
  exactFields(dag, `${path}.program_dag`, ["instructions"]);
  const instructionIds = new Set<number>();
  const invocationIds = new Set<string>();
  let hasImplementationRecipes = false;
  array(dag.instructions, `${path}.program_dag.instructions`).forEach(
    (instructionValue, instructionIndex) => {
      const instructionPath = `${path}.program_dag.instructions[${instructionIndex}]`;
      validateProgramInstruction(instructionValue, instructionPath);
      const instruction = record(instructionValue, instructionPath);
      const instructionId = nonNegativeInteger(instruction.id, `${instructionPath}.id`);
      if (instructionIds.has(instructionId)) {
        contractError(`${instructionPath}.id`, "a unique Program instruction ID");
      }
      instructionIds.add(instructionId);
      if (instruction.implementation_recipes === undefined) return;
      const recipes = array(
        instruction.implementation_recipes,
        `${instructionPath}.implementation_recipes`,
      );
      hasImplementationRecipes ||= recipes.length > 0;
      recipes.forEach((recipeValue, recipeIndex) => {
        const recipePath = `${instructionPath}.implementation_recipes[${recipeIndex}]`;
        const invocationId = nonEmptyString(
          record(recipeValue, recipePath).invocation_id,
          `${recipePath}.invocation_id`,
        );
        if (invocationIds.has(invocationId)) {
          contractError(`${recipePath}.invocation_id`, "a globally unique Plan recipe invocation ID");
        }
        invocationIds.add(invocationId);
      });
    },
  );
  if (runtimeInjectionMode === "black_box" && hasImplementationRecipes) {
    contractError(
      `${path}.program_dag.instructions`,
      "no implementation recipes while runtime_injection_mode is black_box",
    );
  }

  jsonRecord(plan.resource_dag, `${path}.resource_dag`);
  jsonRecord(plan.architectural_state, `${path}.architectural_state`);
  jsonRecord(plan.provenance, `${path}.provenance`);
  jsonRecord(plan.runtime_components, `${path}.runtime_components`);
  return plan as unknown as ExecutionPlanV6Document;
}

function validateTransitionV3(value: unknown, path: string): ExecutionTransitionV3Document {
  const transition = record(value, path);
  exactFields(transition, path, TRANSITION_V3_FIELDS);
  nonNegativeInteger(transition.transition_id, `${path}.transition_id`);
  const kind = string(transition.kind, `${path}.kind`);
  if (kind !== "dispatch" && kind !== "completion") {
    contractError(`${path}.kind`, '"dispatch" or "completion"');
  }
  nonNegativeNumber(transition.time_s, `${path}.time_s`);
  nonNegativeInteger(transition.event_id, `${path}.event_id`);
  nonNegativeInteger(transition.reservation_id, `${path}.reservation_id`);
  const stateVersionBefore = nonNegativeInteger(
    transition.state_version_before,
    `${path}.state_version_before`,
  );
  const stateVersionAfter = nonNegativeInteger(
    transition.state_version_after,
    `${path}.state_version_after`,
  );
  if (stateVersionAfter !== stateVersionBefore + 1) {
    contractError(`${path}.state_version_after`, "state_version_before + 1");
  }
  const plane = string(transition.plane, `${path}.plane`);
  if (plane !== "program" && plane !== "resource") {
    contractError(`${path}.plane`, '"program" or "resource"');
  }
  string(transition.opcode, `${path}.opcode`);
  if (transition.instruction_id !== null) {
    nonNegativeInteger(transition.instruction_id, `${path}.instruction_id`);
  }
  if (transition.process_id !== null) string(transition.process_id, `${path}.process_id`);
  if (transition.instance !== null) nonNegativeInteger(transition.instance, `${path}.instance`);
  string(transition.candidate_id, `${path}.candidate_id`);
  const start = nonNegativeNumber(transition.start_s, `${path}.start_s`);
  const end = nonNegativeNumber(transition.end_s, `${path}.end_s`);
  if (end < start) contractError(`${path}.end_s`, "a value no earlier than start_s");
  if (transition.time_s !== (kind === "dispatch" ? start : end)) {
    contractError(`${path}.time_s`, `${kind} transition boundary time`);
  }
  stringArray(transition.wait_reasons, `${path}.wait_reasons`);
  [
    "consumes",
    "consumed_tokens",
    "consumed_slots",
    "produces",
    "produced_slots",
    "produced_tokens",
    "forwards",
    "engines",
    "required_locations",
    "completion_locations",
    "token_sequence_after",
    "buffer_occupancy_after",
    "pending_incoming_after",
    "metadata",
    "backend_artifact",
    "outcome",
    "measurements",
  ].forEach((field) => jsonRecord(transition[field], `${path}.${field}`));
  const lineage =
    transition.program_lineage === null
      ? null
      : validateProgramLineage(transition.program_lineage, `${path}.program_lineage`);
  const measurements = validateMeasurements(transition.measurements, `${path}.measurements`);
  const continuation =
    transition.continuation === null
      ? null
      : validateContinuation(transition.continuation, `${path}.continuation`);

  if (plane === "program") {
    if (transition.instruction_id === null || transition.process_id !== null || transition.instance !== null) {
      contractError(path, "a Program transition with instruction_id and no Resource identity");
    }
    if (lineage === null) {
      contractError(`${path}.program_lineage`, "typed lineage for every Program transition");
    }
    if (lineage.source_instruction_id !== transition.instruction_id) {
      contractError(
        `${path}.program_lineage.source_instruction_id`,
        "the Program transition instruction_id",
      );
    }
  } else {
    if (transition.instruction_id !== null || transition.process_id === null || transition.instance === null) {
      contractError(path, "a Resource transition with process/instance and no instruction_id");
    }
    if (lineage !== null) {
      contractError(`${path}.program_lineage`, "null for a Resource transition");
    }
  }

  if (kind === "dispatch") {
    if (Object.keys(measurements).length > 0) {
      contractError(`${path}.measurements`, "an empty object on dispatch");
    }
    if (continuation !== null) {
      contractError(`${path}.continuation`, "null on dispatch");
    }
  } else if (plane === "program") {
    if (continuation === null) {
      contractError(`${path}.continuation`, "a typed receipt on Program completion");
    }
  } else if (continuation !== null) {
    contractError(`${path}.continuation`, "null on Resource completion");
  }

  const outcome = record(transition.outcome, `${path}.outcome`);
  const rawOutcomeMeasurements = outcome.measurements;
  if (rawOutcomeMeasurements === undefined) {
    if (Object.keys(measurements).length > 0) {
      contractError(`${path}.outcome.measurements`, "the typed measurement projection");
    }
  } else {
    const projected: Record<string, number> = {};
    array(rawOutcomeMeasurements, `${path}.outcome.measurements`).forEach((item, index) => {
      const measurementPath = `${path}.outcome.measurements[${index}]`;
      const measurement = record(item, measurementPath);
      exactFields(measurement, measurementPath, ["register_id", "bit"]);
      const registerId = nonEmptyString(measurement.register_id, `${measurementPath}.register_id`);
      const bit = nonNegativeInteger(measurement.bit, `${measurementPath}.bit`);
      if (bit !== 0 && bit !== 1) contractError(`${measurementPath}.bit`, "a measurement bit (0 or 1)");
      if (Object.prototype.hasOwnProperty.call(projected, registerId)) {
        contractError(`${measurementPath}.register_id`, "a unique measurement register ID");
      }
      projected[registerId] = bit;
    });
    if (
      Object.keys(projected).length !== Object.keys(measurements).length ||
      Object.entries(projected).some(([registerId, bit]) => measurements[registerId] !== bit)
    ) {
      contractError(`${path}.outcome.measurements`, "the typed measurements field");
    }
  }
  return transition as unknown as ExecutionTransitionV3Document;
}

function validateTraceV3(value: unknown, path: string): ExecutionTraceV3Document {
  const trace = record(value, path);
  exactFields(trace, path, [
    "schema_version",
    "plan_hash",
    "seed",
    "total_latency_s",
    "transitions",
    "initial_state",
    "terminal_state",
    "terminal_inflight",
    "trace_hash",
  ]);
  if (string(trace.schema_version, `${path}.schema_version`) !== EXECUTION_TRACE_V3) {
    contractError(`${path}.schema_version`, JSON.stringify(EXECUTION_TRACE_V3));
  }
  string(trace.plan_hash, `${path}.plan_hash`);
  integer(trace.seed, `${path}.seed`);
  nonNegativeNumber(trace.total_latency_s, `${path}.total_latency_s`);
  const transitions = array(trace.transitions, `${path}.transitions`).map((item, index) =>
    validateTransitionV3(item, `${path}.transitions[${index}]`),
  );
  transitions.forEach((transition, index) => {
    if (transition.transition_id !== index) {
      contractError(`${path}.transitions[${index}].transition_id`, `contiguous value ${index}`);
    }
    if (
      index > 0 &&
      transition.state_version_before !== transitions[index - 1].state_version_after
    ) {
      contractError(
        `${path}.transitions[${index}].state_version_before`,
        "the preceding transition state_version_after",
      );
    }
  });
  jsonRecord(trace.initial_state, `${path}.initial_state`);
  jsonRecord(trace.terminal_state, `${path}.terminal_state`);
  array(trace.terminal_inflight, `${path}.terminal_inflight`).forEach((item, index) => {
    const transition = validateTransitionV3(item, `${path}.terminal_inflight[${index}]`);
    if (transition.kind !== "dispatch" || transition.plane !== "resource") {
      contractError(
        `${path}.terminal_inflight[${index}]`,
        "a Resource dispatch transition",
      );
    }
  });
  string(trace.trace_hash, `${path}.trace_hash`);
  return trace as unknown as ExecutionTraceV3Document;
}

function recipeMeasurementRegister(
  recipe: NonNullable<ProgramInstructionDocument["implementation_recipes"]>[number],
  stageIndex: number,
): string {
  return `${recipe.invocation_id}:stage:${stageIndex}:bit`;
}

function recipeWorkId(
  sourceInstructionId: number,
  recipe: NonNullable<ProgramInstructionDocument["implementation_recipes"]>[number],
  stageIndex: number,
  step: "injection" | "reaction" | "correction",
): string {
  return (
    `program:${sourceInstructionId}:recipe:${recipe.invocation_id}:` +
    `stage:${stageIndex}:${step}`
  );
}

function requireExactStringMembers(
  actual: readonly string[],
  expected: readonly string[],
  path: string,
): void {
  if (
    actual.length !== expected.length ||
    actual.some((item) => !expected.includes(item))
  ) {
    contractError(path, `exact members [${expected.join(", ")}]`);
  }
}

/**
 * Validate only the frozen Plan/Trace facts consumed by the browser's dynamic
 * Program view. Core remains authoritative for hashes, state replay, resource
 * reservations, and the complete continuation state machine.
 */
function validateProgramRuntimeProjection(
  plan: ExecutionPlanV6Document,
  trace: ExecutionTraceV3Document,
  path: string,
): void {
  if (trace.plan_hash !== plan.plan_hash) {
    contractError(`${path}.plan_hash`, "the frozen ExecutionPlan plan_hash");
  }
  if (trace.seed !== plan.policy.seed) {
    contractError(`${path}.seed`, "the frozen ExecutionPlan policy seed");
  }
  const instructionsById = new Map(
    plan.program_dag.instructions.map((instruction) => [instruction.id, instruction]),
  );
  const recipeOwners = new Map<
    string,
    {
      instruction: ProgramInstructionDocument;
      recipe: NonNullable<ProgramInstructionDocument["implementation_recipes"]>[number];
    }
  >();
  plan.program_dag.instructions.forEach((instruction) => {
    (instruction.implementation_recipes ?? []).forEach((recipe) => {
      recipeOwners.set(recipe.invocation_id, { instruction, recipe });
    });
  });

  const completedByEventId = new Map<number, ExecutionTransitionV3Document>();
  trace.transitions.forEach((transition, transitionIndex) => {
    if (transition.plane !== "program") {
      if (transition.kind === "completion") completedByEventId.set(transition.event_id, transition);
      return;
    }

    const transitionPath = `${path}.transitions[${transitionIndex}]`;
    const lineage = transition.program_lineage;
    if (lineage === null) {
      // validateTransitionV3 already reports this path; retain a local guard for
      // type narrowing if this validator is reused.
      contractError(`${transitionPath}.program_lineage`, "typed Program lineage");
    }
    const instruction = instructionsById.get(lineage.source_instruction_id);
    if (instruction === undefined) {
      contractError(
        `${transitionPath}.program_lineage.source_instruction_id`,
        "an instruction in the frozen Program DAG",
      );
    }

    let recipe:
      | NonNullable<ProgramInstructionDocument["implementation_recipes"]>[number]
      | null = null;
    if (lineage.step === "source") {
      if (lineage.work_id !== `program:${lineage.source_instruction_id}`) {
        contractError(`${transitionPath}.program_lineage.work_id`, "the canonical source work ID");
      }
    } else {
      const owner = recipeOwners.get(lineage.recipe_invocation_id ?? "");
      if (owner === undefined || owner.instruction.id !== instruction.id) {
        contractError(
          `${transitionPath}.program_lineage.recipe_invocation_id`,
          "a recipe owned by the source Program instruction",
        );
      }
      recipe = owner.recipe;
      if (lineage.recipe_id !== recipe.recipe_id) {
        contractError(`${transitionPath}.program_lineage.recipe_id`, "the frozen Plan recipe_id");
      }
      const stageIndex = lineage.stage_index;
      if (stageIndex === null || stageIndex >= recipe.stages.length) {
        contractError(
          `${transitionPath}.program_lineage.stage_index`,
          "a stage in the frozen Plan recipe",
        );
      }
      if (lineage.work_id !== recipeWorkId(instruction.id, recipe, stageIndex, lineage.step)) {
        contractError(
          `${transitionPath}.program_lineage.work_id`,
          "the canonical recipe work ID",
        );
      }
    }

    if (transition.kind !== "completion") return;
    const continuation = transition.continuation;
    if (continuation === null) {
      contractError(`${transitionPath}.continuation`, "a typed Program completion receipt");
    }

    if (lineage.step === "source") {
      const recipes = instruction.implementation_recipes ?? [];
      const expectedRegisters = recipes.map((item) => recipeMeasurementRegister(item, 0));
      requireExactStringMembers(
        Object.keys(transition.measurements),
        expectedRegisters,
        `${transitionPath}.measurements`,
      );
      if (recipes.length === 0) {
        if (continuation.kind !== "complete_source") {
          contractError(`${transitionPath}.continuation.kind`, '"complete_source" without recipes');
        }
      } else {
        if (continuation.kind !== "activate") {
          contractError(`${transitionPath}.continuation.kind`, '"activate" for recipe work');
        }
        requireExactStringMembers(
          continuation.activated_work_ids,
          recipes.map((item) => recipeWorkId(instruction.id, item, 0, "reaction")),
          `${transitionPath}.continuation.activated_work_ids`,
        );
      }
      completedByEventId.set(transition.event_id, transition);
      return;
    }

    // The non-source branch above always resolves the frozen recipe and stage.
    if (recipe === null || lineage.stage_index === null) {
      contractError(`${transitionPath}.program_lineage`, "a frozen recipe-stage identity");
    }
    const stageIndex = lineage.stage_index;
    const registerId = recipeMeasurementRegister(recipe, stageIndex);

    if (lineage.step === "injection") {
      const parent =
        lineage.parent_event_id === null
          ? undefined
          : completedByEventId.get(lineage.parent_event_id);
      const parentLineage = parent?.program_lineage;
      if (
        stageIndex === 0 ||
        parent?.plane !== "program" ||
        parentLineage?.step !== "reaction" ||
        parentLineage.source_instruction_id !== instruction.id ||
        parentLineage.recipe_invocation_id !== recipe.invocation_id ||
        parentLineage.stage_index !== stageIndex - 1
      ) {
        contractError(
          `${transitionPath}.program_lineage.parent_event_id`,
          "the preceding recipe-stage reaction completion",
        );
      }
      requireExactStringMembers(
        Object.keys(transition.measurements),
        [registerId],
        `${transitionPath}.measurements`,
      );
      if (continuation.kind !== "activate") {
        contractError(`${transitionPath}.continuation.kind`, '"activate" after injection');
      }
      requireExactStringMembers(
        continuation.activated_work_ids,
        [recipeWorkId(instruction.id, recipe, stageIndex, "reaction")],
        `${transitionPath}.continuation.activated_work_ids`,
      );
    } else if (lineage.step === "reaction") {
      requireExactStringMembers(
        Object.keys(transition.measurements),
        [],
        `${transitionPath}.measurements`,
      );
      const parent =
        lineage.parent_event_id === null
          ? undefined
          : completedByEventId.get(lineage.parent_event_id);
      const parentLineage = parent?.program_lineage;
      const validParentLineage =
        stageIndex === 0
          ? parent?.plane === "program" &&
            parentLineage?.step === "source" &&
            parentLineage.source_instruction_id === instruction.id
          : parent?.plane === "program" &&
            parentLineage?.step === "injection" &&
            parentLineage.source_instruction_id === instruction.id &&
            parentLineage.recipe_invocation_id === recipe.invocation_id &&
            parentLineage.stage_index === stageIndex;
      if (!validParentLineage || parent?.measurements[registerId] === undefined) {
        contractError(
          `${transitionPath}.program_lineage.parent_event_id`,
          `a completed parent carrying measurement ${JSON.stringify(registerId)}`,
        );
      }
      const bit = parent.measurements[registerId];
      const stage = recipe.stages[stageIndex];
      if (bit === 1) {
        if (continuation.kind !== "activate") {
          contractError(`${transitionPath}.continuation.kind`, '"activate" after outcome 1');
        }
        const expectedWorkId =
          stage.failure_next_stage === undefined
            ? recipeWorkId(instruction.id, recipe, stageIndex, "correction")
            : recipeWorkId(
                instruction.id,
                recipe,
                stage.failure_next_stage,
                "injection",
              );
        requireExactStringMembers(
          continuation.activated_work_ids,
          [expectedWorkId],
          `${transitionPath}.continuation.activated_work_ids`,
        );
      } else {
        if (continuation.kind !== "complete_source") {
          contractError(
            `${transitionPath}.continuation.kind`,
            '"complete_source" after outcome 0',
          );
        }
        requireExactStringMembers(
          continuation.activated_work_ids,
          [],
          `${transitionPath}.continuation.activated_work_ids`,
        );
      }
    } else {
      const parent =
        lineage.parent_event_id === null
          ? undefined
          : completedByEventId.get(lineage.parent_event_id);
      const parentLineage = parent?.program_lineage;
      if (
        parent?.plane !== "program" ||
        parentLineage?.step !== "reaction" ||
        parentLineage.source_instruction_id !== instruction.id ||
        parentLineage.recipe_invocation_id !== recipe.invocation_id ||
        parentLineage.stage_index !== stageIndex
      ) {
        contractError(
          `${transitionPath}.program_lineage.parent_event_id`,
          "the matching recipe-stage reaction completion",
        );
      }
      requireExactStringMembers(
        Object.keys(transition.measurements),
        [],
        `${transitionPath}.measurements`,
      );
      requireExactStringMembers(
        continuation.activated_work_ids,
        [],
        `${transitionPath}.continuation.activated_work_ids`,
      );
      if (continuation.kind !== "complete_source") {
        contractError(
          `${transitionPath}.continuation.kind`,
          '"complete_source" after logical correction',
        );
      }
      const correction = recipe.stages[stageIndex].failure_correction;
      if (correction === undefined) {
        contractError(
          `${transitionPath}.program_lineage.stage_index`,
          "a terminal recipe stage with a logical correction",
        );
      }
      const gates = record(transition.metadata.gates, `${transitionPath}.metadata.gates`);
      exactFields(gates, `${transitionPath}.metadata.gates`, [correction]);
      const correctionQubits = array(
        gates[correction],
        `${transitionPath}.metadata.gates.${correction}`,
      ).map((qubit, qubitIndex) =>
        nonNegativeInteger(
          qubit,
          `${transitionPath}.metadata.gates.${correction}[${qubitIndex}]`,
        ),
      );
      if (
        correctionQubits.length !== recipe.qubits.length ||
        correctionQubits.some((qubit, qubitIndex) => qubit !== recipe.qubits[qubitIndex])
      ) {
        contractError(
          `${transitionPath}.metadata.gates.${correction}`,
          `the frozen recipe qubits [${recipe.qubits.join(", ")}]`,
        );
      }
    }

    completedByEventId.set(transition.event_id, transition);
  });
}

function validateSummaryV2(value: unknown, path: string): EvaluationReportV2["results"]["summary"] {
  const summary = record(value, path);
  exactFields(summary, path, [
    "total_latency_s",
    "total_physical_qubits",
    "success_probability",
    "fidelity_complete_coverage",
    "completed_program_instructions",
    "event_count",
    "invariant_checks",
  ]);
  nonNegativeNumber(summary.total_latency_s, `${path}.total_latency_s`);
  nonNegativeNumber(summary.total_physical_qubits, `${path}.total_physical_qubits`);
  nullableNumber(summary.success_probability, `${path}.success_probability`);
  nullableBoolean(summary.fidelity_complete_coverage, `${path}.fidelity_complete_coverage`);
  nonNegativeInteger(summary.completed_program_instructions, `${path}.completed_program_instructions`);
  nonNegativeInteger(summary.event_count, `${path}.event_count`);
  booleanRecord(summary.invariant_checks, `${path}.invariant_checks`);
  return summary as unknown as EvaluationReportV2["results"]["summary"];
}

function validateAnalysisV2(value: unknown, path: string): EvaluationReportV2["results"]["analysis"] {
  const analysis = record(value, path);
  exactFields(analysis, path, [
    "exclusive_time_s",
    "engine_utilization",
    "buffer_occupancy",
    "physical_space_qubits",
    "fidelity_negative_log_success",
    "unavailable",
  ]);
  if (analysis.exclusive_time_s !== null) numberRecord(analysis.exclusive_time_s, `${path}.exclusive_time_s`);
  numberRecord(analysis.engine_utilization, `${path}.engine_utilization`);
  if (analysis.buffer_occupancy !== null) {
    const occupancy = record(analysis.buffer_occupancy, `${path}.buffer_occupancy`);
    Object.entries(occupancy).forEach(([key, metrics]) =>
      numberRecord(metrics, `${path}.buffer_occupancy.${key}`),
    );
  }
  numberRecord(analysis.physical_space_qubits, `${path}.physical_space_qubits`);
  if (analysis.fidelity_negative_log_success !== null) {
    numberRecord(analysis.fidelity_negative_log_success, `${path}.fidelity_negative_log_success`);
  }
  const unavailable = record(analysis.unavailable, `${path}.unavailable`);
  Object.entries(unavailable).forEach(([key, reason]) => string(reason, `${path}.unavailable.${key}`));
  return analysis as unknown as EvaluationReportV2["results"]["analysis"];
}

/** Strictly validate the frozen Report-v2 frontend integration boundary. */
export function parseEvaluationReportV2(value: unknown): EvaluationReportV2 {
  const root = record(value, "report");
  exactFields(root, "report", REPORT_V2_FIELDS);
  // Enforce the v2 codec's exact JSON domain (including finite numbers) before
  // inspecting the presentation subset below.
  jsonRecord(root, "report");
  if (string(root.schema_version, "report.schema_version") !== EVALUATION_REPORT_V2) {
    throw new Error(
      `Unsupported evaluation report schema ${JSON.stringify(root.schema_version)}; ` +
        `expected ${EVALUATION_REPORT_V2}`,
    );
  }
  string(root.workflow_id, "report.workflow_id");
  string(root.report_hash, "report.report_hash");

  const request = record(root.request, "report.request");
  exactFields(request, "report.request", ["config", "workload", "magic_sizing_workload"]);
  validateV2Config(request.config, "report.request.config");
  validateCircuitV2(request.workload, "report.request.workload");
  if (request.magic_sizing_workload !== null) {
    validateCircuitV2(request.magic_sizing_workload, "report.request.magic_sizing_workload");
  }

  const resolved = record(root.resolved_inputs, "report.resolved_inputs");
  exactFields(resolved, "report.resolved_inputs", [
    "architecture",
    "latency_profile",
    "footprint_model",
    "fidelity_profile",
  ]);
  validateCanonicalArchitecture(resolved.architecture, "report.resolved_inputs.architecture");
  jsonRecord(resolved.latency_profile, "report.resolved_inputs.latency_profile");
  jsonRecord(resolved.footprint_model, "report.resolved_inputs.footprint_model");
  if (resolved.fidelity_profile !== null) {
    jsonRecord(resolved.fidelity_profile, "report.resolved_inputs.fidelity_profile");
  }

  const artifacts = record(root.artifacts, "report.artifacts");
  exactFields(artifacts, "report.artifacts", [
    "logical_compilation",
    "execution_plan",
    "execution_trace",
  ]);
  schemaRecord(
    artifacts.logical_compilation,
    "report.artifacts.logical_compilation",
    "heteqsys.logical-compilation-result.v1",
  );
  const plan = validateExecutionPlanV6(
    artifacts.execution_plan,
    "report.artifacts.execution_plan",
  );
  const trace = validateTraceV3(artifacts.execution_trace, "report.artifacts.execution_trace");
  validateProgramRuntimeProjection(plan, trace, "report.artifacts.execution_trace");

  const results = record(root.results, "report.results");
  exactFields(results, "report.results", [
    "observations",
    "footprint",
    "fidelity",
    "analysis",
    "summary",
  ]);
  const observations = record(results.observations, "report.results.observations");
  exactFields(observations, "report.results.observations", ["discrete_time_log"]);
  array(observations.discrete_time_log, "report.results.observations.discrete_time_log").forEach(
    (item, index) => jsonRecord(item, `report.results.observations.discrete_time_log[${index}]`),
  );
  schemaRecord(
    results.footprint,
    "report.results.footprint",
    "arqsim.physical-footprint-estimate.v2",
  );
  if (results.fidelity !== null) jsonRecord(results.fidelity, "report.results.fidelity");
  validateAnalysisV2(results.analysis, "report.results.analysis");
  const summary = validateSummaryV2(results.summary, "report.results.summary");

  const completedTransitions = trace.transitions.filter((item) => item.kind === "completion");
  if (summary.event_count !== completedTransitions.length) {
    contractError("report.results.summary.event_count", "the number of Trace-v3 completion transitions");
  }
  if (summary.total_latency_s !== trace.total_latency_s) {
    contractError("report.results.summary.total_latency_s", "Trace-v3 total_latency_s");
  }
  return value as EvaluationReportV2;
}

/** Dispatch to the frozen v1 or v2 parser without coercing either document. */
export function parseEvaluationReportDocument(value: unknown): EvaluationReportDocument {
  const root = record(value, "report");
  const schema = string(root.schema_version, "report.schema_version");
  if (schema === EVALUATION_REPORT_V1) return parseEvaluationReport(value);
  if (schema === EVALUATION_REPORT_V2) return parseEvaluationReportV2(value);
  throw new Error(`Unsupported evaluation report schema ${JSON.stringify(schema)}`);
}

function profileContractError(path: string, expectation: string): never {
  throw new Error(`Invalid ${ARCHITECTURE_PROFILE_V3} at ${path}: expected ${expectation}`);
}

function profileRecord(value: unknown, path: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return profileContractError(path, "an object");
  }
  return value as Record<string, unknown>;
}

function profileString(value: unknown, path: string): string {
  if (typeof value !== "string") return profileContractError(path, "a string");
  return value;
}

function profileStringArray(value: unknown, path: string): string[] {
  if (!Array.isArray(value)) return profileContractError(path, "an array");
  return value.map((item, index) => profileString(item, `${path}[${index}]`));
}

function profileExactFields(
  value: Record<string, unknown>,
  path: string,
  required: readonly string[],
  optional: readonly string[] = [],
): void {
  const actual = Object.keys(value);
  const missing = required.filter((field) => !actual.includes(field));
  const unknown = actual.filter(
    (field) => !required.includes(field) && !optional.includes(field),
  );
  if (missing.length || unknown.length) {
    profileContractError(
      path,
      `canonical fields; missing [${missing.join(", ")}], unknown [${unknown.join(", ")}]`,
    );
  }
}

function validateProfileSubmodules(value: unknown, path: string): void {
  const submodules = profileRecord(value, path);
  Object.entries(submodules).forEach(([submoduleId, submoduleValue]) => {
    const submodulePath = `${path}.${submoduleId}`;
    const submodule = profileRecord(submoduleValue, submodulePath);
    profileExactFields(submodule, submodulePath, ["type", "payload"]);
    profileString(submodule.type, `${submodulePath}.type`);
    profileString(submodule.payload, `${submodulePath}.payload`);
  });
}

function validateProfileModules(value: unknown, path: string): void {
  const modules = profileRecord(value, path);
  Object.entries(modules).forEach(([moduleId, moduleValue]) => {
    const modulePath = `${path}.${moduleId}`;
    const module = profileRecord(moduleValue, modulePath);
    profileExactFields(module, modulePath, ["type", "submodules"]);
    profileString(module.type, `${modulePath}.type`);
    validateProfileSubmodules(module.submodules, `${modulePath}.submodules`);
  });
}

function validateProfileConnections(value: unknown, path: string): void {
  const connections = profileRecord(value, path);
  Object.entries(connections).forEach(([connectionId, connectionValue]) => {
    const connectionPath = `${path}.${connectionId}`;
    const connection = profileRecord(connectionValue, connectionPath);
    profileExactFields(connection, connectionPath, ["direction", "endpoints"]);
    const direction = profileString(connection.direction, `${connectionPath}.direction`);
    if (direction !== "directed" && direction !== "bidirectional") {
      profileContractError(`${connectionPath}.direction`, '"directed" or "bidirectional"');
    }
    const endpoints = profileStringArray(connection.endpoints, `${connectionPath}.endpoints`);
    if (endpoints.length !== 2 || endpoints[0] === endpoints[1]) {
      profileContractError(`${connectionPath}.endpoints`, "two distinct references");
    }
  });
}

/** Validate the capacity-free public profile boundary used by preset previews. */
export function parseArchitectureProfile(value: unknown): ArchitectureProfileV3Document {
  const root = profileRecord(value, "architecture_profile");
  profileExactFields(
    root,
    "architecture_profile",
    ["schema_version", "id", "name", "nodes", "interconnects"],
    ["description"],
  );
  const schemaVersion = profileString(
    root.schema_version,
    "architecture_profile.schema_version",
  );
  if (schemaVersion !== ARCHITECTURE_PROFILE_V3) {
    throw new Error(
      `Unsupported architecture profile schema ${JSON.stringify(schemaVersion)}; ` +
        `expected ${ARCHITECTURE_PROFILE_V3}`,
    );
  }
  profileString(root.id, "architecture_profile.id");
  profileString(root.name, "architecture_profile.name");
  if (root.description !== undefined) {
    profileString(root.description, "architecture_profile.description");
  }

  const nodes = profileRecord(root.nodes, "architecture_profile.nodes");
  Object.entries(nodes).forEach(([nodeId, nodeValue]) => {
    const nodePath = `architecture_profile.nodes.${nodeId}`;
    const node = profileRecord(nodeValue, nodePath);
    profileExactFields(node, nodePath, ["modality", "modules"], ["connections"]);
    profileString(node.modality, `${nodePath}.modality`);
    validateProfileModules(node.modules, `${nodePath}.modules`);

    if (node.connections !== undefined) {
      validateProfileConnections(node.connections, `${nodePath}.connections`);
    }
  });

  const interconnects = profileRecord(
    root.interconnects,
    "architecture_profile.interconnects",
  );
  Object.entries(interconnects).forEach(([interconnectId, interconnectValue]) => {
    const interconnectPath = `architecture_profile.interconnects.${interconnectId}`;
    const interconnect = profileRecord(interconnectValue, interconnectPath);
    profileExactFields(
      interconnect,
      interconnectPath,
      ["endpoints", "modules"],
      ["connections"],
    );
    profileStringArray(interconnect.endpoints, `${interconnectPath}.endpoints`);
    validateProfileModules(interconnect.modules, `${interconnectPath}.modules`);
    if (interconnect.connections !== undefined) {
      validateProfileConnections(interconnect.connections, `${interconnectPath}.connections`);
    }
  });

  return value as ArchitectureProfileV3Document;
}

function words(value: string): string {
  return value
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_:/.-]+/g, " ")
    .trim()
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function profileSubmoduleView(
  ownerRef: string,
  submoduleId: string,
  submodule: ArchitectureProfileSubmoduleDocument,
) {
  return {
    id: submoduleId,
    ref: `${ownerRef}/${submoduleId}`,
    label: words(submoduleId),
    type: submodule.type,
    payload: submodule.payload,
    capacity: {},
    copyCount: null,
    logicalOrigin: null,
    coordinateSemantics: null,
    qec: null,
    resourceProtocolId: null,
    slots: [],
  };
}

function profileModuleView(
  nodeId: string,
  moduleId: string,
  module: ArchitectureProfileModuleDocument,
) {
  const ref = `${nodeId}/${moduleId}`;
  return {
    id: moduleId,
    ref,
    label: words(moduleId),
    type: module.type,
    submodules: Object.entries(module.submodules).map(([submoduleId, submodule]) =>
      profileSubmoduleView(ref, submoduleId, submodule),
    ),
  };
}

function profileNodeView(nodeId: string, node: ArchitectureProfileNodeDocument) {
  return {
    id: nodeId,
    label: words(nodeId),
    modality: node.modality,
    coordinateFrame: null,
    modules: Object.entries(node.modules).map(([moduleId, module]) =>
      profileModuleView(nodeId, moduleId, module),
    ),
    connections: Object.entries(node.connections ?? {}).map(([connectionId, connection]) => ({
      id: connectionId,
      direction: connection.direction,
      payload: null,
      endpoints: connection.endpoints,
    })),
  };
}

/**
 * Map a capacity-free profile into the same hierarchy model as a resolved
 * report. Empty QEC/capacity/slot fields are intentional: evaluation has not
 * resolved workload-dependent architecture facts yet.
 */
export function architectureProfileToViewModel(
  input: ArchitectureProfileV3Document | unknown,
): ArchitectureHierarchyViewModel {
  const profile = parseArchitectureProfile(input);
  const accessByInterconnect = new Map<
    string,
    { nodeId: string; interconnectSubmoduleId: string; localSubmoduleRefs: string[] }[]
  >();
  Object.entries(profile.interconnects).forEach(([interconnectId, interconnect]) => {
    const sharedBufferId = Object.values(interconnect.modules)
      .flatMap((module) => Object.entries(module.submodules))
      .find(([, submodule]) => submodule.type === "buffer")?.[0] ?? "shared";
    accessByInterconnect.set(
      interconnectId,
      interconnect.endpoints.map((endpoint) => {
        const [nodeId, ...localParts] = endpoint.split("/");
        return {
          nodeId,
          interconnectSubmoduleId: sharedBufferId,
          localSubmoduleRefs: [localParts.join("/")],
        };
      }),
    );
  });

  return {
    // This is a stable UI identity, not a resolved logical-architecture receipt hash.
    logicalArchitectureHash: `profile:${profile.id}`,
    profileId: profile.id,
    profileLabel: profile.name,
    profileDescription: profile.description ?? null,
    workflowId: "profile-preview",
    nodes: Object.entries(profile.nodes).map(([nodeId, node]) =>
      profileNodeView(nodeId, node),
    ),
    interconnects: Object.entries(profile.interconnects).map(
      ([interconnectId, interconnect]) => ({
        id: interconnectId,
        label: words(interconnectId),
        endpoints: [...new Set(interconnect.endpoints.map((endpoint) => endpoint.split("/", 1)[0]))],
        access: accessByInterconnect.get(interconnectId) ?? [],
        submodules: Object.entries(interconnect.modules).flatMap(([moduleId, module]) =>
          Object.entries(module.submodules).map(([submoduleId, submodule]) =>
            profileSubmoduleView(`${interconnectId}/${moduleId}`, submoduleId, submodule),
          ),
        ),
      }),
    ),
  };
}

function pair(value: number[] | undefined): readonly [number, number] | null {
  return value ? [value[0], value[1]] : null;
}

function qecView(qec: ArchitectureSubmoduleDocument["qec"]): QecBindingViewModel | null {
  if (!qec) return null;
  const parameters = qec.parameters;
  const compactParameters = Object.entries(parameters)
    .map(([key, value]) => `${key}=${typeof value === "object" ? JSON.stringify(value) : value}`)
    .join(", ");
  return {
    code: qec.code,
    parameters,
    label: compactParameters ? `${words(qec.code)} (${compactParameters})` : words(qec.code),
  };
}

function slotView(
  slot: ArchitectureSlotDocument,
  fallbackRef: string,
  fallbackPayload: string,
): EvaluationViewModels["architecture"]["nodes"][number]["modules"][number]["submodules"][number]["slots"][number] {
  return {
    id: slot.id,
    ref: slot.ref ?? `${fallbackRef}/${slot.id}`,
    kind: slot.kind ?? "slot",
    payload: fallbackPayload,
    zone: slot.zone ?? null,
    coordinate: pair(slot.coordinate),
    interfaces: slot.interfaces ?? [],
    adjacentTo: slot.adjacent_to ?? null,
  };
}

function submoduleView(submodule: ArchitectureSubmoduleDocument) {
  return {
    id: submodule.id,
    ref: submodule.ref,
    label: words(submodule.id),
    type: submodule.type,
    payload: submodule.payload ?? "unspecified",
    capacity: submodule.capacity ?? {},
    copyCount: submodule.copy_count ?? null,
    logicalOrigin: pair(submodule.logical_origin),
    coordinateSemantics: submodule.coordinate_semantics ?? null,
    qec: qecView(submodule.qec),
    resourceProtocolId: submodule.resource_protocol?.id ?? null,
    slots: (submodule.slots ?? []).map((slot) =>
      slotView(slot, submodule.ref, submodule.payload ?? "unspecified"),
    ),
  };
}

function moduleView(module: ArchitectureModuleDocument) {
  return {
    id: module.id,
    ref: module.ref,
    label: words(module.id),
    type: module.type,
    submodules: module.submodules.map(submoduleView),
  };
}

function nodeView(node: ArchitectureNodeDocument) {
  return {
    id: node.id,
    label: words(node.id),
    modality: node.modality,
    coordinateFrame: node.coordinate_frame
      ? {
          id: node.coordinate_frame.id,
          dimensions: node.coordinate_frame.dimensions,
          unit: node.coordinate_frame.unit,
        }
      : null,
    modules: node.modules.map(moduleView),
    connections: node.connections.map((connection) => ({
      id: connection.id,
      direction: connection.direction,
      payload: connection.payload ?? null,
      endpoints:
        connection.endpoints ??
        ([connection.from, connection.to].filter(Boolean) as string[]),
    })),
  };
}

function interconnectView(interconnect: ArchitectureInterconnectDocument) {
  return {
    id: interconnect.id,
    label: words(interconnect.id),
    endpoints: interconnect.endpoints,
    access: interconnect.access.map((access) => ({
      nodeId: access.node,
      interconnectSubmoduleId: access.submodule,
      localSubmoduleRefs: access.local_submodules,
    })),
    submodules: interconnect.submodules.map(submoduleView),
  };
}

function architectureView(report: EvaluationReportV1): ArchitectureHierarchyViewModel {
  const architecture = report.specification.logical_architecture;
  return {
    logicalArchitectureHash: architecture.logical_architecture_hash,
    profileId: report.specification.profile.id,
    profileLabel: report.specification.profile.name,
    profileDescription: report.specification.profile.description ?? null,
    workflowId: report.specification.workflow.id,
    nodes: architecture.nodes.map(nodeView),
    interconnects: architecture.interconnects.map(interconnectView),
  };
}

function canonicalSubmoduleView(
  ownerRef: string,
  value: unknown,
): ArchitectureHierarchyViewModel["nodes"][number]["modules"][number]["submodules"][number] {
  const submodule = value as {
    id: string;
    type: string;
    payload: string;
    capacity: number;
    slots?: { id: string; coordinate?: number[] }[];
    qec?: { code: string; parameters: JsonRecord };
    resource_protocol?: { id: string };
    logical_origin?: number[];
    grid_shape?: { rows: number; columns: number };
  };
  const ref = `${ownerRef}/${submodule.id}`;
  const capacityKey =
    ({
      "region/logical_qubit": "logical_patches",
      "buffer/logical_qubit": "logical_patches",
      "buffer/magic_state": "logical_magic_states",
      "buffer/bell_pair": "logical_bell_pairs",
      "engine/magic_state": "copies",
      "engine/bell_pair": "copies",
    } as Record<string, string>)[`${submodule.type}/${submodule.payload}`] ?? "units";
  return {
    id: submodule.id,
    ref,
    label: words(submodule.id),
    type: submodule.type,
    payload: submodule.payload,
    capacity: { [capacityKey]: submodule.capacity },
    copyCount: submodule.type === "engine" ? submodule.capacity : null,
    logicalOrigin: pair(submodule.logical_origin),
    coordinateSemantics: submodule.grid_shape ? "canonical_logical_grid" : null,
    qec: qecView(submodule.qec),
    resourceProtocolId: submodule.resource_protocol?.id ?? null,
    slots: (submodule.slots ?? []).map((slot) => ({
      id: slot.id,
      ref: `${ref}/${slot.id}`,
      kind: "logical_slot",
      payload: submodule.payload,
      zone: null,
      coordinate: pair(slot.coordinate),
      interfaces: [],
      adjacentTo: null,
    })),
  };
}

function canonicalModuleView(ownerRef: string, value: unknown) {
  const module = value as { id: string; type: string; submodules: unknown[] };
  const ref = `${ownerRef}/${module.id}`;
  return {
    id: module.id,
    ref,
    label: words(module.id),
    type: module.type,
    submodules: module.submodules.map((submodule) =>
      canonicalSubmoduleView(ref, submodule),
    ),
  };
}

function canonicalArchitectureView(report: EvaluationReportV2): ArchitectureHierarchyViewModel {
  const config = report.request.config as {
    profile_id: string;
    workflow_id: string | null;
  };
  const architecture = report.resolved_inputs.architecture as unknown as {
    architecture_hash: string;
    nodes: {
      id: string;
      modality: string;
      modules: unknown[];
      connections: { id: string; direction: string; endpoints: string[] }[];
    }[];
    interconnects: {
      id: string;
      endpoints: string[];
      modules: { id: string; type: string; submodules: unknown[] }[];
      connections: unknown[];
    }[];
  };
  return {
    logicalArchitectureHash: architecture.architecture_hash,
    profileId: config.profile_id,
    profileLabel: `Profile ${config.profile_id}`,
    profileDescription: null,
    workflowId: config.workflow_id ?? report.workflow_id,
    nodes: architecture.nodes.map((node) => ({
      id: node.id,
      label: words(node.id),
      modality: node.modality,
      coordinateFrame: null,
      modules: node.modules.map((module) => canonicalModuleView(node.id, module)),
      connections: node.connections.map((connection) => ({
        id: connection.id,
        direction: connection.direction,
        payload: null,
        endpoints: connection.endpoints,
      })),
    })),
    interconnects: architecture.interconnects.map((interconnect) => {
      const sharedSubmodules = interconnect.modules.flatMap((module) =>
        canonicalModuleView(interconnect.id, module).submodules,
      );
      const sharedBufferId = sharedSubmodules.find((item) => item.type === "buffer")?.id ?? "shared";
      return {
        id: interconnect.id,
        label: words(interconnect.id),
        endpoints: [...new Set(interconnect.endpoints.map((endpoint) => endpoint.split("/", 1)[0]))],
        access: interconnect.endpoints.map((endpoint) => {
          const [nodeId, ...localParts] = endpoint.split("/");
          return {
            nodeId,
            interconnectSubmoduleId: sharedBufferId,
            localSubmoduleRefs: [localParts.join("/")],
          };
        }),
        submodules: sharedSubmodules,
      };
    }),
  };
}

function completionTransitionEvent(
  transition: ExecutionTransitionV3Document,
): CompletedEvaluationEventModel {
  return {
    event_id: transition.event_id,
    plane: transition.plane,
    instruction_id: transition.instruction_id,
    process_id: transition.process_id,
    opcode: transition.opcode,
    start_s: transition.start_s,
    end_s: transition.end_s,
    duration_s: transition.end_s - transition.start_s,
    metadata: transition.metadata,
    wait_reasons: transition.wait_reasons,
    runtime:
      transition.program_lineage !== null ||
      Object.keys(transition.measurements).length > 0 ||
      transition.continuation !== null
        ? {
            lineage:
              transition.program_lineage === null
                ? null
                : lineageView(transition.program_lineage),
            measurements: measurementViews(transition.measurements),
            continuation:
              transition.continuation === null
                ? null
                : continuationView(transition.continuation),
          }
        : null,
  };
}

function lineageView(
  lineage: ProgramWorkLineageDocument,
): RuntimeProgramLineageViewModel {
  return {
    workId: lineage.work_id,
    sourceInstructionId: lineage.source_instruction_id,
    parentEventId: lineage.parent_event_id,
    recipeInvocationId: lineage.recipe_invocation_id,
    recipeId: lineage.recipe_id,
    stageIndex: lineage.stage_index,
    step: lineage.step,
  };
}

function measurementViews(
  measurements: Record<string, number>,
): RuntimeMeasurementViewModel[] {
  return Object.entries(measurements)
    .map(([registerId, bit]) => ({ registerId, bit }))
    .sort((left, right) => left.registerId.localeCompare(right.registerId));
}

function continuationView(
  continuation: ProgramContinuationDocument,
): RuntimeContinuationViewModel {
  return {
    kind: continuation.kind,
    activatedWorkIds: continuation.activated_work_ids,
  };
}

function recipeView(
  recipe: NonNullable<ProgramInstructionDocument["implementation_recipes"]>[number],
) {
  return {
    invocationId: recipe.invocation_id,
    recipeId: recipe.recipe_id,
    convention: recipe.convention,
    sourceLayerIndex: recipe.source_layer_index,
    sourceOperationIndex: recipe.source_operation_index,
    qubits: recipe.qubits,
    reactionDurationSeconds: recipe.reaction_duration_s,
    correctionDurationSeconds: recipe.correction_duration_s,
    stages: recipe.stages.map((stage) => ({
      index: stage.index,
      stateKind: stage.resource.state_kind,
      resourceId: stage.resource.ref_id,
      bufferId: stage.resource.buffer_id,
      attemptDurationSeconds: stage.attempt_duration_s,
      unfavorableAction:
        stage.failure_next_stage !== undefined
          ? ({ kind: "next_stage", stageIndex: stage.failure_next_stage } as const)
          : ({
              kind: "logical_correction",
              operation: stage.failure_correction!,
            } as const),
    })),
  };
}

function outputProgramFromV2(report: EvaluationReportV2): OutputProgramModel {
  const plan = report.artifacts.execution_plan;
  return {
    schemaVersion: plan.schema_version,
    runtimeInjectionMode: plan.policy.runtime_injection_mode,
    unavailableReason: null,
    instructions: plan.program_dag.instructions.map((instruction) => ({
      id: instruction.id,
      opcode: instruction.opcode,
      layerIndex: instruction.layer ?? null,
      predecessors: instruction.predecessors,
      qubits: instruction.qubits,
      targetModules: instruction.target_modules,
      durationSeconds: instruction.duration_s,
      operationSummary: operationSummary(instruction.metadata),
      recipes: (instruction.implementation_recipes ?? []).map(recipeView),
    })),
  };
}

function emptyFidelityEvidence() {
  return {
    negative_log_success_by_cause: {},
    logical_idle_cycles_by_location: {},
    resource_idle_cycles_by_location: {},
    unprofiled_logical_idle_exposure_s: {},
    unprofiled_resource_idle_exposure_s: {},
  };
}

function optionalFidelityNumberRecord(
  fidelity: JsonRecord | null,
  key: string,
): Record<string, number> {
  if (fidelity === null || fidelity[key] === undefined) return {};
  return numberRecord(fidelity[key], `report.results.fidelity.${key}`);
}

function fidelityEvidenceFromV2(report: EvaluationReportV2) {
  return {
    negative_log_success_by_cause:
      report.results.analysis.fidelity_negative_log_success ?? {},
    logical_idle_cycles_by_location: optionalFidelityNumberRecord(
      report.results.fidelity,
      "idle_cycles_by_location",
    ),
    resource_idle_cycles_by_location: optionalFidelityNumberRecord(
      report.results.fidelity,
      "resource_idle_cycles_by_location",
    ),
    unprofiled_logical_idle_exposure_s: optionalFidelityNumberRecord(
      report.results.fidelity,
      "unprofiled_idle_exposure_s",
    ),
    unprofiled_resource_idle_exposure_s: optionalFidelityNumberRecord(
      report.results.fidelity,
      "unprofiled_resource_idle_exposure_s",
    ),
  };
}

function fidelityUnavailableReason(analysis: EvaluationReportModel["analysis"]): string | null {
  const entry = Object.entries(analysis.unavailable).find(([key]) =>
    key.toLowerCase().includes("fidelity"),
  );
  return entry?.[1] ?? null;
}

function modelFromV1(report: EvaluationReportV1): EvaluationReportModel {
  return {
    adapter_version: "arqsim.frontend-evaluation-model.v1",
    source_schema_version: EVALUATION_REPORT_V1,
    report_hash: report.report_hash,
    profile_id: report.specification.profile.id,
    profile_label: report.specification.profile.name,
    profile_description: report.specification.profile.description ?? null,
    workflow_id: report.specification.workflow.id,
    circuit: report.workload.evaluated,
    architecture: architectureView(report),
    completed_events: report.evaluation.events.map((event) => ({
      ...event,
      runtime: null,
    })),
    output_program: {
      schemaVersion: null,
      runtimeInjectionMode: null,
      unavailableReason:
        "Report v1 does not expose the canonical ExecutionPlan-v6 Program DAG.",
      instructions: [],
    },
    fidelity_evidence: emptyFidelityEvidence(),
    summary: report.summary,
    analysis: report.analysis,
  };
}

function modelFromV2(report: EvaluationReportV2): EvaluationReportModel {
  const config = report.request.config as {
    profile_id: string;
    workflow_id: string | null;
  };
  const trace = report.artifacts.execution_trace;
  return {
    adapter_version: "arqsim.frontend-evaluation-model.v1",
    source_schema_version: EVALUATION_REPORT_V2,
    report_hash: report.report_hash,
    profile_id: config.profile_id,
    profile_label: `Profile ${config.profile_id}`,
    profile_description: null,
    workflow_id: config.workflow_id ?? report.workflow_id,
    circuit: report.request.workload,
    architecture: canonicalArchitectureView(report),
    completed_events: trace.transitions
      .filter((transition) => transition.kind === "completion")
      .map(completionTransitionEvent),
    output_program: outputProgramFromV2(report),
    fidelity_evidence: fidelityEvidenceFromV2(report),
    summary: report.results.summary,
    analysis: {
      exclusive_time_s: report.results.analysis.exclusive_time_s,
      physical_space_qubits: report.results.analysis.physical_space_qubits,
      engine_utilization: report.results.analysis.engine_utilization,
      buffer_occupancy: report.results.analysis.buffer_occupancy,
      unavailable: report.results.analysis.unavailable,
    },
  };
}

function isEvaluationReportModel(value: unknown): value is EvaluationReportModel {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (value as { adapter_version?: unknown }).adapter_version ===
      "arqsim.frontend-evaluation-model.v1" &&
    "output_program" in value &&
    "fidelity_evidence" in value
  );
}

/** Reduce either wire version to the only report model retained by UI state. */
export function adaptEvaluationReport(value: unknown): EvaluationReportModel {
  if (isEvaluationReportModel(value)) return value;
  const report = parseEvaluationReportDocument(value);
  return report.schema_version === EVALUATION_REPORT_V1
    ? modelFromV1(report)
    : modelFromV2(report);
}

function metadataStringArray(metadata: Record<string, unknown>, key: string): string[] {
  const value = metadata[key];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}

function metadataNumberArray(metadata: Record<string, unknown>, key: string): number[] {
  const value = metadata[key];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is number => typeof item === "number" && Number.isInteger(item));
}

function metadataOptionalNumber(metadata: Record<string, unknown>, key: string): number | null {
  const value = metadata[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function gateOperand(value: unknown): string | null {
  if (typeof value === "number" && Number.isInteger(value)) return `q${value}`;
  if (!Array.isArray(value)) return null;
  const qubits = value.filter(
    (item): item is number => typeof item === "number" && Number.isInteger(item),
  );
  return qubits.length === value.length ? qubits.map((qubit) => `q${qubit}`).join(",") : null;
}

function operationSummary(metadata: Record<string, unknown>): string | null {
  const value = metadata.gates;
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const summaries: string[] = [];
  Object.entries(value as Record<string, unknown>).forEach(([gate, rawInstances]) => {
    if (!Array.isArray(rawInstances)) return;
    const instances = rawInstances
      .map(gateOperand)
      .filter((item): item is string => item !== null);
    if (instances.length > 0) summaries.push(`${gate.toUpperCase()} ${instances.join(" · ")}`);
  });
  return summaries.length > 0 ? summaries.join(" + ") : null;
}

function eventLayer(event: EvaluationEventDocument): number | null {
  const sourceLayer = event.metadata.source_layer;
  if (typeof sourceLayer === "number" && Number.isInteger(sourceLayer)) return sourceLayer;
  const layer = event.metadata.layer;
  return typeof layer === "number" && Number.isInteger(layer) ? layer : null;
}

function sourceMeasurementsByInstruction(
  events: readonly CompletedEvaluationEventModel[],
): Map<number, readonly RuntimeMeasurementViewModel[]> {
  const result = new Map<number, RuntimeMeasurementViewModel[]>();
  for (const event of events) {
    const lineage = event.runtime?.lineage;
    const measurements = event.runtime?.measurements ?? [];
    if (lineage === null || lineage === undefined || measurements.length === 0) continue;
    const known = result.get(lineage.sourceInstructionId) ?? [];
    result.set(lineage.sourceInstructionId, [...known, ...measurements]);
  }
  return result;
}

function associatedSourceMeasurements(
  event: CompletedEvaluationEventModel,
  sourceMeasurements: ReadonlyMap<number, readonly RuntimeMeasurementViewModel[]>,
): readonly RuntimeMeasurementViewModel[] {
  const runtime = event.runtime;
  if (runtime?.lineage === null || runtime?.lineage === undefined) return [];
  if (runtime.measurements.length > 0) return [];
  const candidates = sourceMeasurements.get(runtime.lineage.sourceInstructionId) ?? [];
  if (runtime.lineage.recipeInvocationId === null || runtime.lineage.stageIndex === null) {
    return candidates;
  }
  const registerPrefix =
    `${runtime.lineage.recipeInvocationId}:stage:${runtime.lineage.stageIndex}:`;
  return candidates.filter((measurement) =>
    measurement.registerId.startsWith(registerPrefix),
  );
}

function timelineEventView(
  event: CompletedEvaluationEventModel,
  sourceMeasurements: ReadonlyMap<number, readonly RuntimeMeasurementViewModel[]>,
): TimelineEventViewModel {
  const runtime = event.runtime;
  return {
    id: `event:${event.event_id}`,
    label: words(event.opcode),
    opcode: event.opcode,
    plane: event.plane,
    instructionId: event.instruction_id,
    processId: event.process_id,
    startSeconds: event.start_s,
    endSeconds: event.end_s,
    durationSeconds: event.duration_s,
    layerIndex: eventLayer(event),
    operationSummary: operationSummary(event.metadata),
    qubits: metadataNumberArray(event.metadata, "qubits"),
    direction:
      typeof event.metadata.direction === "string" ? event.metadata.direction : null,
    programReadySeconds: metadataOptionalNumber(event.metadata, "program_ready_s"),
    resourceWaitSeconds: metadataOptionalNumber(event.metadata, "resource_wait_s"),
    targetModules: metadataStringArray(event.metadata, "target_modules"),
    waitReasons: event.wait_reasons,
    runtimeLineage: runtime?.lineage ?? null,
    measurements: runtime?.measurements ?? [],
    sourceMeasurements: associatedSourceMeasurements(event, sourceMeasurements),
    continuation: runtime?.continuation ?? null,
    conditionalCorrection: runtime?.lineage?.step === "correction",
  };
}

function timelineView(report: EvaluationReportModel, requestedLimit: number): TimelineViewModel {
  const allLayerIndices = report.circuit.layers.map((layer) => layer.index);
  const maxProgramLayers = Math.min(
    MAX_TIMELINE_LAYER_LIMIT,
    Math.max(1, Math.floor(requestedLimit)),
  );
  const visibleLayerIndices = new Set(allLayerIndices.slice(0, maxProgramLayers));
  const totalProgramLayerCount = allLayerIndices.length;
  const visibleProgramLayerCount = visibleLayerIndices.size;
  const events = report.completed_events;

  const eventsInVisibleLayers = events.filter(
    (event) => event.plane === "program" && visibleLayerIndices.has(eventLayer(event) ?? -1),
  );
  const displayCutoff =
    eventsInVisibleLayers.length > 0
      ? Math.max(...eventsInVisibleLayers.map((event) => event.end_s))
      : report.summary.total_latency_s;

  const candidateEvents = events.filter((event) => {
    const layer = eventLayer(event);
    if (event.plane === "program" && layer !== null) return visibleLayerIndices.has(layer);
    // Unlayered Program work and completed Resource work are shown only inside
    // the causal time window. Resource events are never assigned a fake layer.
    return event.end_s <= displayCutoff + Number.EPSILON;
  });
  const programEvents = candidateEvents.filter((event) => event.plane === "program");
  const resourceEvents = candidateEvents.filter((event) => event.plane === "resource");
  const visibleEvents = [
    ...programEvents.slice(0, MAX_TIMELINE_EVENT_LIMIT),
    ...resourceEvents.slice(
      0,
      Math.max(0, MAX_TIMELINE_EVENT_LIMIT - programEvents.length),
    ),
  ];
  const candidateEventCount = candidateEvents.length;
  const renderedEventCount = visibleEvents.length;
  const sourceMeasurements = sourceMeasurementsByInstruction(events);

  const rows = new Map<string, TimelineRowViewModel>();
  for (const event of visibleEvents) {
    const eventView = timelineEventView(event, sourceMeasurements);
    const targetModules = metadataStringArray(event.metadata, "target_modules");
    const resourceIdentity = event.process_id ?? targetModules[0] ?? "resource";
    const programIdentity = targetModules[0] ?? "program";
    const runtimeStep = eventView.runtimeLineage?.step;
    const identity =
      event.plane === "program"
        ? `${programIdentity}${runtimeStep && runtimeStep !== "source" ? `:${runtimeStep}` : ""}`
        : resourceIdentity;
    const rowId = `${event.plane}:${identity}`;
    const row = rows.get(rowId) ?? {
      id: rowId,
      label:
        event.plane === "program" && runtimeStep && runtimeStep !== "source"
          ? `Program · ${words(programIdentity)} · ${words(runtimeStep)}`
          : `${event.plane === "program" ? "Program" : "Resource"} · ${words(identity)}`,
      plane: event.plane,
      events: [],
    };
    (row.events as TimelineEventViewModel[]).push(eventView);
    rows.set(rowId, row);
  }

  const orderedRows = [...rows.values()]
    .map((row) => ({
      ...row,
      events: [...row.events].sort(
        (left, right) => left.startSeconds - right.startSeconds || left.endSeconds - right.endSeconds,
      ),
    }))
    .sort((left, right) => {
      if (left.plane !== right.plane) return left.plane === "program" ? -1 : 1;
      return left.label.localeCompare(right.label);
    });
  const displayDurationSeconds = Math.max(
    displayCutoff,
    ...visibleEvents.map((event) => event.end_s),
    0,
  );

  return {
    fullDurationSeconds: report.summary.total_latency_s,
    displayDurationSeconds,
    maxProgramLayers,
    visibleProgramLayerCount,
    totalProgramLayerCount,
    truncated: visibleProgramLayerCount < totalProgramLayerCount,
    candidateEventCount,
    renderedEventCount,
    eventTruncated: renderedEventCount < candidateEventCount,
    rows: orderedRows,
  };
}

function programExecutionView(report: EvaluationReportModel): ProgramExecutionViewModel {
  const sourceMeasurements = sourceMeasurementsByInstruction(report.completed_events);
  const dynamicWork: DynamicProgramWorkViewModel[] = report.completed_events
    .filter(
      (event): event is CompletedEvaluationEventModel & {
        instruction_id: number;
        runtime: RuntimeEventSemanticsModel & {
          lineage: RuntimeProgramLineageViewModel;
        };
      } =>
        event.plane === "program" &&
        event.instruction_id !== null &&
        event.runtime?.lineage !== null &&
        event.runtime?.lineage !== undefined &&
        (
          event.runtime.lineage.step !== "source" ||
          event.runtime.measurements.length > 0 ||
          event.runtime.continuation?.kind === "activate"
        ),
    )
    .map((event) => ({
      eventId: event.event_id,
      instructionId: event.instruction_id,
      opcode: event.opcode,
      startSeconds: event.start_s,
      endSeconds: event.end_s,
      durationSeconds: event.duration_s,
      operationSummary: operationSummary(event.metadata),
      qubits: metadataNumberArray(event.metadata, "qubits"),
      targetModules: metadataStringArray(event.metadata, "target_modules"),
      lineage: event.runtime.lineage,
      measurements: event.runtime.measurements,
      sourceMeasurements: associatedSourceMeasurements(event, sourceMeasurements),
      continuation: event.runtime.continuation,
      conditionalCorrection: event.runtime.lineage.step === "correction",
    }));
  return {
    outputProgram: report.output_program,
    dynamicWork,
  };
}

const BREAKDOWN_LABELS: Record<string, string> = {
  compute: "Compute",
  compiler_routing: "Compiler routing",
  magic_state_supply_stall: "Magic-state supply stall",
  bell_pair_supply_stall: "Bell-pair supply stall",
  memory_store_load: "Memory store / load",
  interconnect: "Interconnect",
  resource_move: "Resource movement",
};

function timeBreakdownView(report: EvaluationReportModel): TimeBreakdownViewModel {
  const totalSeconds = report.summary.total_latency_s;
  const unavailableReason = report.analysis.unavailable.exclusive_time_s ?? null;
  return {
    totalSeconds,
    unavailableReason,
    segments: Object.entries(report.analysis.exclusive_time_s ?? {})
      .map(([key, seconds]) => ({
        key,
        label: BREAKDOWN_LABELS[key] ?? words(key),
        seconds,
        fraction: totalSeconds > 0 ? seconds / totalSeconds : 0,
      }))
      .sort((left, right) => right.seconds - left.seconds || left.key.localeCompare(right.key)),
  };
}

function spaceBreakdownView(report: EvaluationReportModel): SpaceBreakdownViewModel {
  const totalPhysicalQubits = report.summary.total_physical_qubits;
  return {
    totalPhysicalQubits,
    unavailableReason: report.analysis.unavailable.physical_space_qubits ?? null,
    segments: Object.entries(report.analysis.physical_space_qubits)
      .map(([key, physicalQubits]) => ({
        key,
        label: words(key),
        physicalQubits,
        fraction: totalPhysicalQubits > 0 ? physicalQubits / totalPhysicalQubits : 0,
      }))
      .sort(
        (left, right) =>
          right.physicalQubits - left.physicalQubits || left.key.localeCompare(right.key),
      ),
  };
}

function circuitStatisticsView(report: EvaluationReportModel): CircuitStatisticsViewModel {
  const circuit = report.circuit;
  const operations = circuit.layers.flatMap((layer) => layer.operations);
  const operationCounts: Record<string, number> = {};
  for (const operation of operations) {
    operationCounts[operation.name] = (operationCounts[operation.name] ?? 0) + 1;
  }
  const sortedOperationCounts = Object.fromEntries(
    Object.entries(operationCounts).sort(([left], [right]) => left.localeCompare(right)),
  );
  const isNonClifford = (name: string) => ["t", "tdg", "t_pauli"].includes(name.toLowerCase());
  const pauliOperations = operations.filter((operation) => operation.pauli !== undefined);
  const pauliWeights = pauliOperations.map(
    (operation) => operation.weight ?? operation.qubits.length,
  );
  const rotations = pauliOperations.filter((operation) => operation.kind === "pauli_rotation");
  const measurements = pauliOperations.filter(
    (operation) => operation.kind === "pauli_measurement",
  );

  return {
    representation: circuit.representation,
    numQubits: circuit.num_qubits,
    numClbits: circuit.num_clbits,
    layerCount: circuit.layers.length,
    operationCount: operations.length,
    operationCounts: sortedOperationCounts,
    series: circuit.layers.map((layer) => {
      const pauliLayer = layer.operations.filter((operation) => operation.pauli !== undefined);
      const weights = pauliLayer.map((operation) => operation.weight ?? operation.qubits.length);
      return {
        layerIndex: layer.index,
        activeQubits: layer.active_qubits.length,
        operationCount: layer.operations.length,
        nonCliffordCount: layer.operations.filter((operation) => isNonClifford(operation.name))
          .length,
        pauliOperationCount: pauliLayer.length,
        meanPauliWeight:
          weights.length > 0 ? weights.reduce((sum, weight) => sum + weight, 0) / weights.length : 0,
        maxPauliWeight: weights.length > 0 ? Math.max(...weights) : 0,
      };
    }),
    gbc:
      circuit.representation === "clifford_t"
        ? {
            totalGates: operations.length,
            depth: circuit.layers.length,
            tCount: operations.filter((operation) => isNonClifford(operation.name)).length,
            maxWidth: Math.max(0, ...circuit.layers.map((layer) => layer.active_qubits.length)),
          }
        : null,
    pbc:
      circuit.representation === "pbc"
        ? {
            totalStrings: pauliOperations.length,
            maxWeight: Math.max(0, ...pauliWeights),
            avgWeight:
              pauliWeights.length > 0
                ? pauliWeights.reduce((sum, weight) => sum + weight, 0) / pauliWeights.length
                : 0,
            rotationCount: rotations.length,
            measurementCount: measurements.length,
          }
        : null,
  };
}

/** Pure version-neutral report-model -> presentation mapping. */
export function reportToViewModels(
  input: EvaluationReportModel | unknown,
  options: ReportViewOptions = {},
): EvaluationViewModels {
  const report = adaptEvaluationReport(input);
  const requestedLimit = options.maxProgramLayers ?? DEFAULT_TIMELINE_LAYER_LIMIT;
  if (!Number.isFinite(requestedLimit) || requestedLimit <= 0) {
    throw new Error("maxProgramLayers must be a positive finite number");
  }
  return {
    headline: {
      profileId: report.profile_id,
      workflowId: report.workflow_id,
      reportHash: report.report_hash,
      totalLatencySeconds: report.summary.total_latency_s,
      totalPhysicalQubits: report.summary.total_physical_qubits,
      successProbability: report.summary.success_probability,
      fidelityCompleteCoverage: report.summary.fidelity_complete_coverage,
      completedProgramInstructions: report.summary.completed_program_instructions,
      eventCount: report.summary.event_count,
    },
    architecture: report.architecture,
    timeline: timelineView(report, requestedLimit),
    timeBreakdown: timeBreakdownView(report),
    spaceBreakdown: spaceBreakdownView(report),
    fidelity: {
      successProbability: report.summary.success_probability,
      completeCoverage: report.summary.fidelity_complete_coverage,
      unavailableReason: fidelityUnavailableReason(report.analysis),
      negativeLogSuccessByCause:
        report.fidelity_evidence.negative_log_success_by_cause,
      logicalIdleCyclesByLocation:
        report.fidelity_evidence.logical_idle_cycles_by_location,
      resourceIdleCyclesByLocation:
        report.fidelity_evidence.resource_idle_cycles_by_location,
      unprofiledLogicalIdleSeconds:
        report.fidelity_evidence.unprofiled_logical_idle_exposure_s,
      unprofiledResourceIdleSeconds:
        report.fidelity_evidence.unprofiled_resource_idle_exposure_s,
    },
    circuitStatistics: circuitStatisticsView(report),
    programExecution: programExecutionView(report),
  };
}
