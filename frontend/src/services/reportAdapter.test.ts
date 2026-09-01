import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import {
  adaptEvaluationReport,
  architectureProfileToViewModel,
  MAX_TIMELINE_EVENT_LIMIT,
  parseArchitectureProfile,
  parseEvaluationReportV2,
  reportToViewModels,
} from "./reportAdapter";
import {
  FINITE_INJECTION_DEMO_PRESET_ID,
  finiteInjectionExploratoryWorkflowId,
  finiteInjectionDemoConfig,
} from "./evaluationPresets";
import { defaultExperimentSetupParams } from "../types/experiment";

type PlainRecord = Record<string, unknown>;

function coreReportV2Fixture(name: "static-summary" | "dynamic-t-full"): unknown {
  const path = resolve(
    process.cwd(),
    "../tests/fixtures/report_v2",
    `${name}.v2.json`,
  );
  return JSON.parse(readFileSync(path, "utf8"));
}

function finiteInjectionReportFixture(): unknown {
  const path = resolve(
    process.cwd(),
    "../system_cases/finite_runtime_injection_demo/reference/report.v2.json",
  );
  return JSON.parse(readFileSync(path, "utf8"));
}

test("Finite-injection request preset is explicit and does not alter the default path", () => {
  assert.equal(defaultExperimentSetupParams.evaluationPreset, "default");
  assert.equal(
    FINITE_INJECTION_DEMO_PRESET_ID,
    "finite_t_injection_demo_v1",
  );
  assert.deepEqual(
    finiteInjectionDemoConfig(
      "2.3",
      "arqsim_timeline_demo",
    ),
    {
      schema_version: "arqsim.evaluation-config.v1",
      profile_id: "2.3",
      workflow_id:
        "frontend:finite_t_injection_demo_v1:arqsim_timeline_demo__2.3",
      latency_profile: {
        reaction_latency_by_modality_s: {
          neutral_atom: 0.0005,
          superconducting: 0.00001,
        },
        provenance: {
          reaction_latency_profile: "reference_reaction_latency_profile_v1",
          reaction_latency_source:
            "reference classical-control assumptions",
        },
      },
      evaluation_policy: {
        trace_level: "full",
        runtime_injection_mode: "finite_state_injection_v1",
        seed: 5,
      },
      fidelity_profile: { preset: "canonical_reference_v1" },
    },
  );
  assert.equal(
    finiteInjectionExploratoryWorkflowId("arqsim_timeline_demo", "2.3"),
    "frontend:finite_t_injection_demo_v1:arqsim_timeline_demo__2.3",
  );
});

function profileV3Fixture(): PlainRecord {
  const node = (modality: string) => ({
    modality,
    modules: {
      compute: {
        type: "compute",
        submodules: {
          data: { type: "region", payload: "logical_qubit" },
          communication: { type: "buffer", payload: "bell_pair" },
        },
      },
    },
  });
  return {
    schema_version: "arqsim.architecture-profile.v3",
    id: "frontend-profile",
    name: "Frontend Profile",
    nodes: {
      na: node("neutral_atom"),
      sc: node("superconducting"),
    },
    interconnects: {
      network: {
        endpoints: ["na/compute/communication", "sc/compute/communication"],
        modules: {
          storage: {
            type: "bell_storage",
            submodules: {
              bell: { type: "buffer", payload: "bell_pair" },
            },
          },
        },
      },
    },
  };
}

test("Profile catalog requires canonical Profile v3 and derives preview access", () => {
  const profile = parseArchitectureProfile(profileV3Fixture());
  const view = architectureProfileToViewModel(profile);

  assert.equal(profile.schema_version, "arqsim.architecture-profile.v3");
  assert.deepEqual(view.interconnects[0].endpoints, ["na", "sc"]);
  assert.equal(view.interconnects[0].access.length, 2);

  const legacy = profileV3Fixture();
  legacy.schema_version = "arqsim.architecture-profile.v2";
  assert.throws(() => parseArchitectureProfile(legacy), /expected arqsim\.architecture-profile\.v3/);
});

function workloadFixture(): PlainRecord {
  return {
    schema_version: "heteqsys.ft-workload.v2",
    representation: "clifford_t",
    num_qubits: 1,
    num_clbits: 0,
    layers: [],
    semantic_hash: "workload-hash",
    provenance: {},
  };
}

function reportV1Fixture(): PlainRecord {
  return {
    schema_version: "arqsim.evaluation-report.v1",
    report_hash: "v1-report-hash",
    config: { profile_id: "1.1", workflow_id: "frontend-fixture" },
    effective_configuration: { workflow_id: "frontend-fixture" },
    workload: { evaluated: workloadFixture(), magic_sizing_reference: null },
    specification: {
      profile: { id: "1.1", name: "Profile 1.1" },
      workflow: { id: "frontend-fixture", logical_qubits: 1 },
      logical_architecture: {
        schema_version: "arqsim.logical-architecture.v1",
        logical_architecture_hash: "architecture-hash",
        nodes: [],
        interconnects: [],
      },
    },
    evaluation: { total_latency_s: 0, events: [] },
    summary: {
      total_latency_s: 0,
      total_physical_qubits: 1,
      success_probability: null,
      fidelity_complete_coverage: null,
      completed_program_instructions: 0,
      event_count: 0,
      invariant_checks: { all_program_instructions_completed: true },
    },
    analysis: {
      exclusive_time_s: {},
      physical_space_qubits: { compute: 1 },
      engine_utilization: {},
      buffer_occupancy: {},
      unavailable: { fidelity: "disabled" },
    },
  };
}

test("legacy Report v1 remains supported through the version-neutral model", () => {
  const model = adaptEvaluationReport(reportV1Fixture());

  assert.equal(model.source_schema_version, "arqsim.evaluation-report.v1");
  assert.equal(model.profile_id, "1.1");
  assert.equal(reportToViewModels(model).spaceBreakdown.totalPhysicalQubits, 1);
});

test("canonical static-summary Report-v2 fixture maps without v1 projection", () => {
  const raw = coreReportV2Fixture("static-summary") as PlainRecord;
  const nestedRequest = raw.request;
  const document = parseEvaluationReportV2(raw);
  const model = adaptEvaluationReport(document);
  const views = reportToViewModels(model);

  assert.equal(document, raw, "validation must not clone the report root");
  assert.equal(document.request, nestedRequest, "validation must not clone nested JSON");
  assert.equal(model.source_schema_version, "arqsim.evaluation-report.v2");
  assert.equal(model.completed_events.length, model.summary.event_count);
  assert.ok(document.artifacts.execution_trace.transitions.length > model.completed_events.length);
  assert.ok(model.architecture.nodes.length > 0);
  assert.equal(views.headline.fidelityCompleteCoverage, false);
  assert.equal(typeof views.headline.successProbability, "number");
});

test("canonical dynamic-T Report-v2 fixture retains dynamic timeline spans", () => {
  const model = adaptEvaluationReport(coreReportV2Fixture("dynamic-t-full"));
  const timeline = reportToViewModels(model).timeline;
  const timelineEvents = timeline.rows.flatMap((row) => row.events);

  assert.equal(model.source_schema_version, "arqsim.evaluation-report.v2");
  assert.equal(model.completed_events.length, model.summary.event_count);
  assert.ok(timelineEvents.some((event) => event.opcode === "CLASSICAL_REACTION"));
  assert.ok(timelineEvents.some((event) => event.operationSummary === "S q0"));
  const correction = timelineEvents.find((event) => event.conditionalCorrection);
  assert.equal(correction?.runtimeLineage?.step, "correction");
  assert.equal(correction?.sourceMeasurements[0]?.bit, 1);
  assert.equal(timeline.renderedEventCount, timeline.candidateEventCount);
  assert.equal(timeline.eventTruncated, false);
});

test("timeline caps a large causal window and retains Program work before Resource work", () => {
  const model = adaptEvaluationReport(coreReportV2Fixture("dynamic-t-full"));
  const firstLayer = model.circuit.layers[0]?.index ?? 0;
  const programTemplate = model.completed_events.find((event) => event.plane === "program");
  const resourceTemplate = model.completed_events.find((event) => event.plane === "resource");
  assert.ok(programTemplate);
  assert.ok(resourceTemplate);

  const programCount = MAX_TIMELINE_EVENT_LIMIT - 100;
  const resourceCount = 300;
  const programEvents = Array.from({ length: programCount }, (_, index) => ({
    ...programTemplate,
    event_id: index,
    start_s: 0,
    end_s: 1,
    duration_s: 1,
    metadata: { ...programTemplate.metadata, source_layer: firstLayer },
  }));
  const resourceEvents = Array.from({ length: resourceCount }, (_, index) => ({
    ...resourceTemplate,
    event_id: programCount + index,
    start_s: 0,
    end_s: 1,
    duration_s: 1,
  }));
  const timeline = reportToViewModels({
    ...model,
    completed_events: [...resourceEvents, ...programEvents],
    summary: {
      ...model.summary,
      total_latency_s: 1,
      event_count: programCount + resourceCount,
    },
  }).timeline;
  const rendered = timeline.rows.flatMap((row) => row.events);

  assert.equal(timeline.candidateEventCount, programCount + resourceCount);
  assert.equal(timeline.renderedEventCount, MAX_TIMELINE_EVENT_LIMIT);
  assert.equal(timeline.eventTruncated, true);
  assert.equal(rendered.filter((event) => event.plane === "program").length, programCount);
  assert.equal(rendered.filter((event) => event.plane === "resource").length, 100);
});

test("Finite-injection reference exposes both branches, typed Program work, and idle fidelity", () => {
  const model = adaptEvaluationReport(finiteInjectionReportFixture());
  const views = reportToViewModels(model);
  const recipes = views.programExecution.outputProgram.instructions.flatMap(
    (instruction) => instruction.recipes,
  );

  assert.equal(views.programExecution.outputProgram.runtimeInjectionMode, "finite_state_injection_v1");
  assert.equal(recipes.length, 2);
  assert.equal(recipes[0].stages[0].unfavorableAction.kind, "logical_correction");
  assert.equal(views.programExecution.dynamicWork.length, 5);
  assert.deepEqual(
    views.programExecution.dynamicWork
      .flatMap((item) => item.measurements)
      .map((measurement) => measurement.bit),
    [1, 0],
  );
  const corrections = views.programExecution.dynamicWork.filter(
    (item) => item.conditionalCorrection,
  );
  assert.equal(corrections.length, 1);
  assert.equal(corrections[0].operationSummary, "S q0");
  assert.deepEqual(corrections[0].sourceMeasurements.map((item) => item.bit), [1]);
  const zeroReaction = views.programExecution.dynamicWork.find(
    (item) =>
      item.lineage.step === "reaction" &&
      item.sourceMeasurements.some((measurement) => measurement.bit === 0),
  );
  assert.ok(zeroReaction);
  assert.equal(views.fidelity.completeCoverage, true);
  assert.ok(Object.keys(views.fidelity.logicalIdleCyclesByLocation).length > 0);
  assert.ok(Object.keys(views.fidelity.resourceIdleCyclesByLocation).length > 0);
  assert.ok(
    Object.keys(views.fidelity.negativeLogSuccessByCause).some((key) =>
      key.startsWith("resource_idle_"),
    ),
  );
});

test("continuation measurement association is scoped to recipe invocation", () => {
  const raw = coreReportV2Fixture("dynamic-t-full") as PlainRecord;
  const plan = (raw.artifacts as PlainRecord).execution_plan as PlainRecord;
  const instructions = (plan.program_dag as PlainRecord).instructions as PlainRecord[];
  const instruction = instructions.find(
    (item) => (item.implementation_recipes as PlainRecord[] | undefined)?.length,
  );
  assert.ok(instruction);
  const recipes = instruction.implementation_recipes as PlainRecord[];
  const firstRecipe = recipes[0];
  const secondInvocationId = `${String(firstRecipe.invocation_id)}:parallel`;
  const secondRecipe = JSON.parse(JSON.stringify(firstRecipe)) as PlainRecord;
  secondRecipe.invocation_id = secondInvocationId;
  secondRecipe.source_operation_index = Number(firstRecipe.source_operation_index) + 1;
  recipes.push(secondRecipe);

  const transitions = (((raw.artifacts as PlainRecord).execution_trace as PlainRecord)
    .transitions as PlainRecord[]);
  const source = transitions.find(
    (transition) =>
      transition.kind === "completion" &&
      (transition.program_lineage as PlainRecord | null)?.step === "source" &&
      Object.keys(transition.measurements as PlainRecord).length > 0,
  );
  assert.ok(source);
  const secondRegisterId = `${secondInvocationId}:stage:0:bit`;
  (source.measurements as PlainRecord)[secondRegisterId] = 0;
  ((source.outcome as PlainRecord).measurements as PlainRecord[]).push({
    bit: 0,
    register_id: secondRegisterId,
  });
  ((source.continuation as PlainRecord).activated_work_ids as string[]).push(
    `program:${String(instruction.id)}:recipe:${secondInvocationId}:stage:0:reaction`,
  );

  const views = reportToViewModels(adaptEvaluationReport(raw));
  const correction = views.programExecution.dynamicWork.find(
    (item) => item.conditionalCorrection,
  );
  assert.ok(correction);
  assert.deepEqual(correction.sourceMeasurements.map((item) => item.bit), [1]);
});

test("Report-v2 parser freezes the Plan-v6 topology and runtime mode", () => {
  for (const target of ["plan", "policy", "program_dag"] as const) {
    const raw = finiteInjectionReportFixture() as PlainRecord;
    const plan = (raw.artifacts as PlainRecord).execution_plan as PlainRecord;
    const owner =
      target === "plan"
        ? plan
        : (plan[target] as PlainRecord);
    owner.unexpected = true;
    assert.throws(() => parseEvaluationReportV2(raw), /unknown \[unexpected\]/);
  }

  const unsupported = finiteInjectionReportFixture() as PlainRecord;
  const unsupportedPlan = (unsupported.artifacts as PlainRecord).execution_plan as PlainRecord;
  (unsupportedPlan.policy as PlainRecord).runtime_injection_mode = "future_mode";
  assert.throws(
    () => parseEvaluationReportV2(unsupported),
    /runtime_injection_mode.*black_box.*finite_state_injection_v1/,
  );

  const duplicateInstruction = finiteInjectionReportFixture() as PlainRecord;
  const duplicatePlan = (duplicateInstruction.artifacts as PlainRecord)
    .execution_plan as PlainRecord;
  const duplicateInstructions = (duplicatePlan.program_dag as PlainRecord)
    .instructions as PlainRecord[];
  duplicateInstructions[1].id = duplicateInstructions[0].id;
  assert.throws(
    () => parseEvaluationReportV2(duplicateInstruction),
    /unique Program instruction ID/,
  );
});

test("Report-v2 parser rejects recipes outside the frozen Plan policy", () => {
  const unsupportedConvention = finiteInjectionReportFixture() as PlainRecord;
  const unsupportedInstructions = ((((unsupportedConvention.artifacts as PlainRecord)
    .execution_plan as PlainRecord).program_dag as PlainRecord).instructions as PlainRecord[]);
  const unsupportedRecipe = (unsupportedInstructions.find(
    (instruction) => (instruction.implementation_recipes as PlainRecord[] | undefined)?.length,
  )?.implementation_recipes as PlainRecord[])[0];
  unsupportedRecipe.convention = "future_injection_convention";
  assert.throws(
    () => parseEvaluationReportV2(unsupportedConvention),
    /convention.*cx_data_magic_measure_magic_z_v1/,
  );

  const blackBox = finiteInjectionReportFixture() as PlainRecord;
  const blackBoxPlan = (blackBox.artifacts as PlainRecord).execution_plan as PlainRecord;
  (blackBoxPlan.policy as PlainRecord).runtime_injection_mode = "black_box";
  assert.throws(
    () => parseEvaluationReportV2(blackBox),
    /no implementation recipes.*black_box/,
  );

  const duplicate = finiteInjectionReportFixture() as PlainRecord;
  const duplicateInstructions = ((((duplicate.artifacts as PlainRecord)
    .execution_plan as PlainRecord).program_dag as PlainRecord).instructions as PlainRecord[]);
  const recipeInstructions = duplicateInstructions.filter(
    (instruction) => (instruction.implementation_recipes as PlainRecord[] | undefined)?.length,
  );
  const firstInvocationId = ((recipeInstructions[0].implementation_recipes as PlainRecord[])[0]
    .invocation_id);
  ((recipeInstructions[1].implementation_recipes as PlainRecord[])[0]).invocation_id =
    firstInvocationId;
  assert.throws(
    () => parseEvaluationReportV2(duplicate),
    /globally unique Plan recipe invocation ID/,
  );
});

test("Report-v2 parser rejects malformed finite recipe stage continuations", () => {
  const badNext = finiteInjectionReportFixture() as PlainRecord;
  const badNextInstructions = ((((badNext.artifacts as PlainRecord)
    .execution_plan as PlainRecord).program_dag as PlainRecord).instructions as PlainRecord[]);
  const badNextStage = (((badNextInstructions.find(
    (instruction) => (instruction.implementation_recipes as PlainRecord[] | undefined)?.length,
  )?.implementation_recipes as PlainRecord[])[0].stages as PlainRecord[])[0]);
  delete badNextStage.failure_correction;
  badNextStage.failure_next_stage = 1;
  assert.throws(
    () => parseEvaluationReportV2(badNext),
    /failure_next_stage.*next valid stage index 1/,
  );

  const emptyCorrection = finiteInjectionReportFixture() as PlainRecord;
  const emptyCorrectionInstructions = ((((emptyCorrection.artifacts as PlainRecord)
    .execution_plan as PlainRecord).program_dag as PlainRecord).instructions as PlainRecord[]);
  const terminal = ((((emptyCorrectionInstructions.find(
    (instruction) => (instruction.implementation_recipes as PlainRecord[] | undefined)?.length,
  )?.implementation_recipes as PlainRecord[])[0].stages as PlainRecord[])[0]));
  terminal.failure_correction = "";
  assert.throws(
    () => parseEvaluationReportV2(emptyCorrection),
    /failure_correction.*non-empty plain string/,
  );
});

test("Report-v2 parser rejects malformed Trace-v3 Program causality", () => {
  const badVersion = finiteInjectionReportFixture() as PlainRecord;
  const badVersionTransitions = (((badVersion.artifacts as PlainRecord)
    .execution_trace as PlainRecord).transitions as PlainRecord[]);
  badVersionTransitions[0].state_version_after = badVersionTransitions[0].state_version_before;
  assert.throws(
    () => parseEvaluationReportV2(badVersion),
    /state_version_after.*state_version_before \+ 1/,
  );

  const missingLineage = finiteInjectionReportFixture() as PlainRecord;
  const missingLineageTransitions = (((missingLineage.artifacts as PlainRecord)
    .execution_trace as PlainRecord).transitions as PlainRecord[]);
  const programDispatch = missingLineageTransitions.find(
    (transition) => transition.plane === "program" && transition.kind === "dispatch",
  );
  assert.ok(programDispatch);
  programDispatch.program_lineage = null;
  assert.throws(
    () => parseEvaluationReportV2(missingLineage),
    /program_lineage.*typed lineage/,
  );

  const missingReceipt = finiteInjectionReportFixture() as PlainRecord;
  const missingReceiptTransitions = (((missingReceipt.artifacts as PlainRecord)
    .execution_trace as PlainRecord).transitions as PlainRecord[]);
  const programCompletion = missingReceiptTransitions.find(
    (transition) => transition.plane === "program" && transition.kind === "completion",
  );
  assert.ok(programCompletion);
  programCompletion.continuation = null;
  assert.throws(
    () => parseEvaluationReportV2(missingReceipt),
    /continuation.*typed receipt/,
  );
});

test("Report-v2 parser binds recipe measurements and continuations to invocation/stage", () => {
  const wrongRegister = finiteInjectionReportFixture() as PlainRecord;
  const wrongRegisterTransitions = (((wrongRegister.artifacts as PlainRecord)
    .execution_trace as PlainRecord).transitions as PlainRecord[]);
  const measuredSource = wrongRegisterTransitions.find(
    (transition) =>
      transition.kind === "completion" &&
      transition.plane === "program" &&
      Object.keys(transition.measurements as PlainRecord).length > 0,
  );
  assert.ok(measuredSource);
  const measurementEntry = Object.entries(measuredSource.measurements as PlainRecord)[0];
  const wrongId = `${measurementEntry[0]}:wrong-stage`;
  measuredSource.measurements = { [wrongId]: measurementEntry[1] };
  const outcomeMeasurement = (((measuredSource.outcome as PlainRecord)
    .measurements as PlainRecord[])[0]);
  outcomeMeasurement.register_id = wrongId;
  assert.throws(
    () => parseEvaluationReportV2(wrongRegister),
    /measurements.*exact members/,
  );

  const wrongContinuation = finiteInjectionReportFixture() as PlainRecord;
  const wrongContinuationTransitions = (((wrongContinuation.artifacts as PlainRecord)
    .execution_trace as PlainRecord).transitions as PlainRecord[]);
  const measuredCompletion = wrongContinuationTransitions.find(
    (transition) =>
      transition.kind === "completion" &&
      transition.plane === "program" &&
      Object.keys(transition.measurements as PlainRecord).length > 0,
  );
  assert.ok(measuredCompletion);
  ((measuredCompletion.continuation as PlainRecord).activated_work_ids as string[])[0] =
    "program:999:recipe:wrong:stage:1:reaction";
  assert.throws(
    () => parseEvaluationReportV2(wrongContinuation),
    /continuation\.activated_work_ids.*exact members/,
  );
});

test("Report-v2 parser cross-checks Plan identity and terminal recipe branches", () => {
  const wrongPlanHash = finiteInjectionReportFixture() as PlainRecord;
  const wrongHashTrace = (wrongPlanHash.artifacts as PlainRecord)
    .execution_trace as PlainRecord;
  wrongHashTrace.plan_hash = "not-the-plan-hash";
  assert.throws(
    () => parseEvaluationReportV2(wrongPlanHash),
    /plan_hash.*frozen ExecutionPlan plan_hash/,
  );

  const wrongSeed = finiteInjectionReportFixture() as PlainRecord;
  const wrongSeedTrace = (wrongSeed.artifacts as PlainRecord)
    .execution_trace as PlainRecord;
  wrongSeedTrace.seed = Number(wrongSeedTrace.seed) + 1;
  assert.throws(
    () => parseEvaluationReportV2(wrongSeed),
    /seed.*frozen ExecutionPlan policy seed/,
  );

  const zeroBranch = finiteInjectionReportFixture() as PlainRecord;
  const zeroTransitions = (((zeroBranch.artifacts as PlainRecord)
    .execution_trace as PlainRecord).transitions as PlainRecord[]);
  const zeroReaction = zeroTransitions.find((transition) => {
    const lineage = transition.program_lineage as PlainRecord | null;
    if (transition.kind !== "completion" || lineage?.step !== "reaction") return false;
    const parent = zeroTransitions.find(
      (candidate) =>
        candidate.kind === "completion" && candidate.event_id === lineage.parent_event_id,
    );
    return Object.values((parent?.measurements as PlainRecord | undefined) ?? {}).includes(0);
  });
  assert.ok(zeroReaction);
  (zeroReaction.continuation as PlainRecord).kind = "activate";
  assert.throws(
    () => parseEvaluationReportV2(zeroBranch),
    /continuation\.kind.*complete_source.*outcome 0/,
  );

  const correctionReceipt = finiteInjectionReportFixture() as PlainRecord;
  const correctionReceiptTransitions = (((correctionReceipt.artifacts as PlainRecord)
    .execution_trace as PlainRecord).transitions as PlainRecord[]);
  const correctionWithWrongReceipt = correctionReceiptTransitions.find(
    (transition) =>
      transition.kind === "completion" &&
      (transition.program_lineage as PlainRecord | null)?.step === "correction",
  );
  assert.ok(correctionWithWrongReceipt);
  (correctionWithWrongReceipt.continuation as PlainRecord).kind = "activate";
  assert.throws(
    () => parseEvaluationReportV2(correctionReceipt),
    /continuation\.kind.*complete_source.*logical correction/,
  );

  const badCorrection = finiteInjectionReportFixture() as PlainRecord;
  const correctionTransitions = (((badCorrection.artifacts as PlainRecord)
    .execution_trace as PlainRecord).transitions as PlainRecord[]);
  const correction = correctionTransitions.find(
    (transition) =>
      transition.kind === "completion" &&
      (transition.program_lineage as PlainRecord | null)?.step === "correction",
  );
  assert.ok(correction);
  (correction.metadata as PlainRecord).gates = { s: [999] };
  assert.throws(
    () => parseEvaluationReportV2(badCorrection),
    /metadata\.gates\.s.*frozen recipe qubits/,
  );
});

test("Report-v2 parser rejects unknown fields and non-finite nested values", () => {
  const unknown = coreReportV2Fixture("static-summary") as PlainRecord;
  unknown.unexpected = true;
  assert.throws(() => parseEvaluationReportV2(unknown), /unknown \[unexpected\]/);

  const nonFinite = coreReportV2Fixture("static-summary") as PlainRecord;
  ((nonFinite.results as PlainRecord).summary as PlainRecord).total_latency_s = Number.NaN;
  assert.throws(() => parseEvaluationReportV2(nonFinite), /finite number/);
});
