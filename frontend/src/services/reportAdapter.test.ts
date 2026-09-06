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
  FINITE_INJECTION_DEMO_PROFILE_ID,
  FINITE_INJECTION_DEMO_PROGRAM_ID,
  FINITE_INJECTION_DEMO_PRESET_ID,
  finiteInjectionDemoSelectionError,
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
    "../system_cases/finite_runtime_injection_demo_measurement_v3/reference/report.v2.json",
  );
  return JSON.parse(readFileSync(path, "utf8"));
}

test("Finite-injection request preset is the explicit browser acceptance default", () => {
  assert.equal(
    defaultExperimentSetupParams.evaluationPreset,
    "finite_t_injection_demo_v1",
  );
  assert.equal(
    FINITE_INJECTION_DEMO_PRESET_ID,
    "finite_t_injection_demo_v1",
  );
  assert.equal(FINITE_INJECTION_DEMO_PROGRAM_ID, "arqsim_timeline_demo");
  assert.equal(FINITE_INJECTION_DEMO_PROFILE_ID, "2.3");
  assert.deepEqual(
    finiteInjectionDemoConfig(),
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
        seed: 0,
      },
      fidelity_profile: { preset: "canonical_reference_v1" },
    },
  );
  assert.equal(
    finiteInjectionExploratoryWorkflowId(),
    "frontend:finite_t_injection_demo_v1:arqsim_timeline_demo__2.3",
  );
  assert.equal(
    finiteInjectionDemoSelectionError(["arqsim_timeline_demo"], ["2.3"]),
    null,
  );
  assert.match(
    finiteInjectionDemoSelectionError(["ising_n34"], ["2.3"]) ?? "",
    /requires exactly ArqSim Timeline Demo/,
  );
  assert.match(
    finiteInjectionDemoSelectionError(["arqsim_timeline_demo"], ["1.3"]) ?? "",
    /Architecture Profile 2\.3/,
  );
  assert.match(
    finiteInjectionDemoSelectionError(
      ["arqsim_timeline_demo", "ising_n34"],
      ["2.3"],
    ) ?? "",
    /Remove other selections/,
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
  assert.deepEqual(
    view.interconnects[0].modules.map((module) => [
      module.ref,
      module.submodules.map((submodule) => submodule.ref),
    ]),
    [["network/storage", ["network/storage/bell"]]],
  );

  const legacy = profileV3Fixture();
  legacy.schema_version = "arqsim.architecture-profile.v2";
  assert.throws(() => parseArchitectureProfile(legacy), /expected arqsim\.architecture-profile\.v3/);
});

function workloadFixture(): PlainRecord {
  return {
    schema_version: "arqsim.ft-workload.v2",
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
  const views = reportToViewModels(model);
  assert.equal(views.spaceBreakdown.totalPhysicalQubits, 1);
  assert.deepEqual(views.timeline.bufferTracks, []);
  assert.deepEqual(model.resourceProcesses, []);
  assert.ok(views.timeline.rows.every((row) => row.backpressureSpans.length === 0));
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
  assert.deepEqual(views.timeline.bufferTracks, []);
  assert.equal(views.headline.fidelityCompleteCoverage, true);
  assert.equal(typeof views.headline.successProbability, "number");
});

test("canonical dynamic-T Report-v2 fixture retains dynamic timeline spans", () => {
  const model = adaptEvaluationReport(coreReportV2Fixture("dynamic-t-full"));
  const views = reportToViewModels(model);
  const timeline = views.timeline;
  const timelineEvents = timeline.rows.flatMap((row) => row.events);

  assert.equal(model.source_schema_version, "arqsim.evaluation-report.v2");
  assert.equal(model.completed_events.length, model.summary.event_count);
  assert.ok(timelineEvents.some((event) => event.opcode === "CLASSICAL_REACTION"));
  assert.ok(timelineEvents.some((event) => event.operationSummary === "S q0"));
  const correction = timelineEvents.find((event) => event.conditionalCorrection);
  assert.equal(correction?.runtimeLineage?.step, "correction");
  assert.equal(correction?.sourceMeasurements[0]?.bit, 1);
  assert.equal(timeline.logicalGadgets.length, 1);
  assert.equal(views.programExecution.logicalGadgets.length, 1);
  assert.deepEqual(
    timeline.logicalGadgets[0].childEventIds,
    views.programExecution.logicalGadgets[0].childEventIds,
  );
  assert.equal(timeline.logicalGadgets[0].measurement?.bit, 1);
  assert.equal(timeline.logicalGadgets[0].correctionApplied, true);
  assert.equal(
    timeline.logicalGadgets[0].realizationElapsedSeconds,
    timeline.logicalGadgets[0].completionSeconds -
      timeline.logicalGadgets[0].dispatchSeconds,
  );
  assert.equal(
    timeline.logicalGadgets[0].queueWaitSeconds,
    timeline.logicalGadgets[0].dispatchSeconds -
      timeline.logicalGadgets[0].readySeconds,
  );
  assert.equal(timeline.gadgetHosts.length, 1);
  assert.match(
    timeline.gadgetHosts[0].ownerTrackId,
    /^submodule:.+\/na_compute\/compute_region$/,
  );
  assert.ok(timeline.rows.some((row) => row.trackKind === "classical"));
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
  assert.equal(views.programExecution.dynamicWork.length, 7);
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

  const timelineEvents = views.timeline.rows.flatMap((row) => row.events);
  const runtimeSteps = timelineEvents.flatMap((event) =>
    event.runtimeLineage === null ? [] : [event.runtimeLineage.step],
  );
  assert.equal(runtimeSteps.filter((step) => step === "entangle").length, 2);
  assert.equal(runtimeSteps.filter((step) => step === "measurement").length, 2);
  assert.equal(runtimeSteps.filter((step) => step === "reaction").length, 2);
  assert.equal(runtimeSteps.filter((step) => step === "correction").length, 1);
  const timelineCorrection = timelineEvents.find(
    (event) => event.conditionalCorrection,
  );
  assert.ok(timelineCorrection);
  assert.equal(timelineCorrection.runtimeLineage?.step, "correction");
  assert.equal(timelineCorrection.label, "Logical S correction (q0)");
  assert.equal(timelineCorrection.operationSummary, "S q0");
  assert.deepEqual(
    timelineCorrection.sourceMeasurements.map((measurement) => measurement.bit),
    [1],
  );

  const zeroReaction = views.programExecution.dynamicWork.find(
    (item) =>
      item.lineage.step === "reaction" &&
      item.sourceMeasurements.some((measurement) => measurement.bit === 0),
  );
  assert.ok(zeroReaction);
  assert.equal(views.programExecution.logicalGadgets.length, 2);
  assert.deepEqual(
    views.programExecution.logicalGadgets.map((gadget) => gadget.measurement?.bit),
    [1, 0],
  );
  assert.deepEqual(
    views.programExecution.logicalGadgets.map((gadget) => gadget.correctionApplied),
    [true, false],
  );
  assert.ok(views.timeline.rows.some((row) => row.label === "Classical Decoder"));
  const computeTracks = views.timeline.rows.filter(
    (row) =>
      row.id === "submodule:na_compute_node/na_compute/compute_region",
  );
  assert.equal(computeTracks.length, 1);
  assert.deepEqual(
    new Set(
      computeTracks[0].events.flatMap((event) =>
        event.runtimeLineage === null ? [] : [event.runtimeLineage.step],
      ),
    ),
    new Set(["source", "entangle", "measurement", "correction"]),
  );
  assert.equal(computeTracks[0].parentTrackId, "module:na_compute_node/na_compute");
  assert.equal(computeTracks[0].structural, false);
  assert.deepEqual(
    new Set(
      views.timeline.rows
        .filter(
          (row) =>
            row.id === "connection:na_compute_node/na_memory_compute_bus",
        )
        .flatMap((row) => row.events)
        .map((event) => event.opcode),
    ),
    new Set(["STORE_QUBITS", "LOAD_QUBITS"]),
  );
  assert.ok(
    views.timeline.rows.some(
      (row) =>
        row.id === "movement:na_compute_node/na_compute" &&
        row.parentTrackId === "module:na_compute_node/na_compute" &&
        row.events.every((event) => event.opcode === "MOVE_QUBITS"),
    ),
  );
  const factoryTrack = views.timeline.rows.find(
    (row) => row.id === "submodule:sc_msf_node/sc_msf/factory_engine",
  );
  assert.ok(factoryTrack?.events.some((event) => event.opcode === "PREPARE_MAGIC_STATE"));
  const pairGeneratorTrack = views.timeline.rows.find(
    (row) =>
      row.id === "submodule:compute_msf_link/bell_engine/pair_generator",
  );
  assert.ok(
    pairGeneratorTrack?.events.some(
      (event) => event.opcode === "PREPARE_LOGICAL_BELL",
    ),
  );
  const interconnectTransferTrack = views.timeline.rows.find(
    (row) => row.id === "interconnect-transfer:compute_msf_link",
  );
  assert.ok(
    interconnectTransferTrack?.events.some(
      (event) => event.opcode === "TELEPORT_QUBITS",
    ),
  );
  assert.equal(
    factoryTrack?.events.find((event) => event.opcode === "PREPARE_MAGIC_STATE")
      ?.locus.kind,
    "submodule",
  );
  assert.equal(
    interconnectTransferTrack?.events.find(
      (event) => event.opcode === "TELEPORT_QUBITS",
    )
      ?.locus.kind,
    "transfer",
  );
  const storeEvent = views.timeline.rows
    .flatMap((row) => row.events)
    .find((event) => event.opcode === "STORE_QUBITS");
  assert.deepEqual(storeEvent?.locus, {
    kind: "transfer",
    trackId: "connection:na_compute_node/na_memory_compute_bus",
    ownerRefs: [
      "na_compute_node/na_memory_compute_bus",
      "na_compute_node/na_compute/store_load_buffer",
      "na_compute_node/na_memory/memory_region",
    ],
  });
  assert.equal(
    views.timeline.rows.some(
      (row) => row.id.startsWith("module:") || row.id.startsWith("interconnect:"),
    ),
    false,
  );
  assert.ok(
    views.timeline.groups.some(
      (group) =>
        group.id === "interconnect:compute_msf_link" &&
        group.label === "NA Compute ↔ SC MSF",
    ),
  );
  timelineEvents.forEach((event) => {
    assert.equal(event.locus.trackId.length > 0, true);
  });
  assert.equal(
    new Set(views.timeline.rows.flatMap((row) => row.events.map((event) => event.id))).size,
    views.timeline.renderedEventCount,
  );
  assert.equal(views.fidelity.completeCoverage, true);
  assert.ok(Object.keys(views.fidelity.logicalIdleCyclesByLocation).length > 0);
  assert.ok(Object.keys(views.fidelity.resourceIdleCyclesByLocation).length > 0);
  assert.ok(
    Object.keys(views.fidelity.negativeLogSuccessByCause).some((key) =>
      key.startsWith("resource_idle_"),
    ),
  );
});

test("Finite-injection timeline projects wait, milestone, buffer-state, and inflight facts", () => {
  const model = adaptEvaluationReport(finiteInjectionReportFixture());
  const timeline = reportToViewModels(model).timeline;
  const events = timeline.rows.flatMap((row) => row.events);

  assert.deepEqual(
    model.resourceProcesses.map((process) => ({
      id: process.id,
      parallelism: process.parallelism,
      produces: process.produces,
      outputOverflowPolicy: process.outputOverflowPolicy,
    })),
    [
      {
        id: "deliver_magic_remote",
        parallelism: 1,
        produces: { magic_compute: 1 },
        outputOverflowPolicy: "block",
      },
      {
        id: "prepare_logical_bell:compute_msf_link",
        parallelism: 1,
        produces: { "bell:compute_msf_link": 1 },
        outputOverflowPolicy: "discard_excess",
      },
      {
        id: "prepare_magic",
        parallelism: 1,
        produces: { msf_output: 1 },
        outputOverflowPolicy: "discard_excess",
      },
    ],
  );

  const waiting = events.filter((event) => event.waitingSpan !== null);
  assert.equal(waiting.length, 1);
  waiting.forEach((event) => {
    assert.equal(event.waitingSpan?.plane, "program");
    assert.equal(event.waitingSpan?.startSeconds, event.programReadySeconds);
    assert.equal(event.waitingSpan?.endSeconds, event.startSeconds);
    assert.ok((event.waitingSpan?.durationSeconds ?? 0) > 0);
    assert.equal(event.waitingSpan?.reason, "Waiting for magic state");
    assert.equal(event.waitReasons.includes("buffer_empty:magic_compute:0/1"), false);
    assert.ok(
      Math.abs(
        (event.waitingSpan?.durationSeconds ?? 0) -
          (event.resourceWaitSeconds ?? 0),
      ) < 1e-12,
    );
  });
  const bellWaitModel = {
    ...model,
    completed_events: model.completed_events.map((event) =>
      event.event_id === Number(waiting[0].id.replace("event:", ""))
        ? {
            ...event,
            wait_reasons: ["buffer_empty:bell:compute_msf_link:0/1"],
          }
        : event,
    ),
  };
  const bellWait = reportToViewModels(bellWaitModel).timeline.rows
    .flatMap((row) => row.events)
    .find((event) => event.id === waiting[0].id);
  assert.equal(bellWait?.waitingSpan?.reason, "Waiting for logical bell pair");
  timeline.logicalGadgets.forEach((gadget) => {
    const children = events.filter((event) =>
      event.recipeInvocationIds.includes(gadget.invocationId),
    );
    assert.ok(children.length > 0);
    assert.equal(
      gadget.readySeconds,
      Math.min(
        ...children.map(
          (event) => event.programReadySeconds ?? event.startSeconds,
        ),
      ),
    );
    assert.equal(
      gadget.dispatchSeconds,
      Math.min(...children.map((event) => event.startSeconds)),
    );
    assert.equal(
      gadget.queueWaitSeconds,
      gadget.dispatchSeconds - gadget.readySeconds,
    );
  });

  const milestones = events.flatMap((event) => event.milestones);
  assert.equal(milestones.filter((item) => item.kind === "produced").length, 7);
  assert.equal(milestones.filter((item) => item.kind === "delivered").length, 3);
  assert.equal(milestones.filter((item) => item.kind === "consumed").length, 8);
  assert.deepEqual(
    milestones
      .filter((item) => item.kind === "measurement")
      .map((item) => item.outcome),
    [1, 0],
  );
  assert.ok(
    milestones.some(
      (item) =>
        item.kind === "produced" &&
        item.bufferId === "msf_output" &&
        item.ownerTrackId ===
          "submodule:sc_msf_node/sc_msf/magic_state_output_buffer",
    ),
  );
  assert.ok(
    milestones.some(
      (item) =>
        item.kind === "produced" &&
        item.bufferId === "bell:compute_msf_link" &&
        item.ownerTrackId ===
          "submodule:compute_msf_link/bell_storage/bell_buffer",
    ),
  );
  assert.ok(
    milestones.some(
      (item) =>
        item.kind === "delivered" &&
        item.bufferId === "magic_compute" &&
        item.ownerTrackId ===
          "submodule:na_compute_node/na_compute/magic_state_input_buffer",
    ),
  );
  events.forEach((event) => {
    event.milestones
      .filter((item) => item.kind === "consumed")
      .forEach((item) => assert.equal(item.timeSeconds, event.startSeconds));
    event.milestones
      .filter((item) => item.kind !== "consumed")
      .forEach((item) => assert.equal(item.timeSeconds, event.endSeconds));
  });

  assert.equal(timeline.bufferTracks.length, 3);
  assert.deepEqual(
    timeline.bufferTracks.map((track) => [
      track.id,
      track.ownerTrackId,
      track.capacity,
    ]),
    [
      [
        "buffer:magic_compute",
        "submodule:na_compute_node/na_compute/magic_state_input_buffer",
        1,
      ],
      [
        "buffer:msf_output",
        "submodule:sc_msf_node/sc_msf/magic_state_output_buffer",
        1,
      ],
      [
        "buffer:bell:compute_msf_link",
        "submodule:compute_msf_link/bell_storage/bell_buffer",
        1,
      ],
    ],
  );
  timeline.bufferTracks.forEach((track) => {
    assert.equal(track.segments[0]?.startSeconds, 0);
    assert.equal(track.segments.at(-1)?.endSeconds, timeline.displayDurationSeconds);
    track.segments.forEach((segment) => {
      assert.ok(segment.endSeconds > segment.startSeconds);
      assert.ok(segment.ready >= 0 && segment.ready <= segment.capacity);
      assert.ok(segment.pendingIncoming >= 0);
    });
  });
  assert.ok(
    timeline.bufferTracks
      .find((track) => track.id === "buffer:msf_output")
      ?.segments.some((segment) => segment.ready === 1),
  );
  assert.ok(
    timeline.bufferTracks
      .find((track) => track.id === "buffer:magic_compute")
      ?.segments.some((segment) => segment.pendingIncoming === 1),
  );
  const segmentSignature = (bufferId: string) =>
    timeline.bufferTracks
      .find((track) => track.id === `buffer:${bufferId}`)
      ?.segments.map((segment) => [
        segment.startSeconds,
        segment.endSeconds,
        segment.ready,
        segment.pendingIncoming,
      ]);
  assert.deepEqual(segmentSignature("magic_compute"), [
    [0, 0.03715719114085128, 0, 0],
    [0.03715719114085128, 0.03815719114085128, 0, 1],
    [0.03815719114085128, 0.0431919873088375, 0, 0],
    [0.0431919873088375, 0.0441919873088375, 0, 1],
    [0.0441919873088375, 0.08333534210511175, 1, 0],
    [0.08333534210511175, 0.08433534210511175, 0, 1],
    [0.08433534210511175, 0.08819355368181105, 1, 0],
  ]);
  assert.deepEqual(segmentSignature("msf_output"), [
    [0, 0.004070414695032114, 0, 1],
    [0.004070414695032114, 0.03715719114085128, 1, 0],
    [0.03715719114085128, 0.038351037382583095, 0, 1],
    [0.038351037382583095, 0.0431919873088375, 1, 0],
    [0.0431919873088375, 0.04475826786161998, 0, 1],
    [0.04475826786161998, 0.08333534210511175, 1, 0],
    [0.08333534210511175, 0.0866858735615787, 0, 1],
    [0.0866858735615787, 0.08819355368181105, 1, 0],
  ]);
  assert.deepEqual(segmentSignature("bell:compute_msf_link"), [
    [0, 0.053646322678944106, 0, 1],
    [0.053646322678944106, 0.08333534210511175, 1, 0],
    [0.08333534210511175, 0.08819355368181105, 0, 1],
  ]);

  const factoryBackpressure = timeline.rows.find(
    (row) => row.id === "submodule:sc_msf_node/sc_msf/factory_engine",
  )?.backpressureSpans.filter((span) => span.processId === "prepare_magic");
  assert.deepEqual(
    factoryBackpressure?.map((span) => [
      span.startSeconds,
      span.endSeconds,
      span.bufferIds,
      span.outputOverflowPolicy,
    ]),
    [
      [0.004070414695032114, 0.03715719114085128, ["msf_output"], "discard_excess"],
      [0.038351037382583095, 0.0431919873088375, ["msf_output"], "discard_excess"],
      [0.04475826786161998, 0.08333534210511175, ["msf_output"], "discard_excess"],
      [0.0866858735615787, 0.08819355368181105, ["msf_output"], "discard_excess"],
    ],
  );

  const inflight = events.filter((event) => event.continuesAfterWindow);
  assert.equal(inflight.length, 1);
  assert.equal(inflight[0].id, "event:26");
  assert.equal(events.filter((event) => event.id === "event:26").length, 1);
  assert.equal(inflight[0].endSeconds, timeline.displayDurationSeconds);
  assert.deepEqual(inflight[0].milestones, []);
  assert.equal(new Set(events.map((event) => event.id)).size, events.length);

  const firstLayerTimeline = reportToViewModels(model, {
    maxProgramLayers: 1,
  }).timeline;
  const crossingResources = firstLayerTimeline.rows
    .flatMap((row) => row.events)
    .filter(
      (event) => event.plane === "resource" && event.continuesAfterWindow,
    );
  assert.ok(crossingResources.length > 0);
  crossingResources.forEach((event) => {
    assert.equal(event.endSeconds, firstLayerTimeline.displayDurationSeconds);
    assert.ok(event.milestones.every((milestone) => milestone.kind === "consumed"));
  });
});

test("resource backpressure distinguishes block capacity from discard saturation", () => {
  const model = adaptEvaluationReport(finiteInjectionReportFixture());
  const ownerRowId = "submodule:sc_msf_node/sc_msf/factory_engine";
  const syntheticBuffers = [
    {
      id: "output_a",
      moduleRef: "sc_msf_node/sc_msf",
      submoduleRef: null,
      tokenKind: "synthetic_a",
      capacity: 2,
      initialReady: 0,
    },
    {
      id: "output_b",
      moduleRef: "sc_msf_node/sc_msf",
      submoduleRef: null,
      tokenKind: "synthetic_b",
      capacity: 2,
      initialReady: 0,
    },
  ];
  const process = {
    id: "prepare_magic",
    parallelism: 2,
    produces: { output_a: 2, output_b: 1 },
    outputOverflowPolicy: "block" as const,
  };
  const baseSynthetic = {
    ...model,
    architectureBuffers: syntheticBuffers,
    architectureBufferSnapshots: [
      {
        timeSeconds: 0,
        states: {
          output_a: { ready: 1, pendingIncoming: 0 },
          output_b: { ready: 0, pendingIncoming: 0 },
        },
      },
    ],
    resourceProcesses: [
      process,
      {
        id: "process_without_visible_owner",
        parallelism: 1,
        produces: { output_a: 1 },
        outputOverflowPolicy: "block" as const,
      },
    ],
  };
  const blocked = reportToViewModels(baseSynthetic).timeline;
  const blockedSpans = blocked.rows.find(
    (row) => row.id === ownerRowId,
  )?.backpressureSpans;
  assert.deepEqual(blockedSpans, [
    {
      id: "backpressure:prepare_magic:0",
      processId: "prepare_magic",
      startSeconds: 0,
      endSeconds: blocked.displayDurationSeconds,
      durationSeconds: blocked.displayDurationSeconds,
      reason: "Required output capacity unavailable",
      bufferIds: ["output_a"],
      outputOverflowPolicy: "block",
    },
  ]);
  assert.equal(
    blocked.rows.flatMap((row) => row.backpressureSpans)
      .some((span) => span.processId === "process_without_visible_owner"),
    false,
  );

  const partiallyFreeDiscard = reportToViewModels({
    ...baseSynthetic,
    resourceProcesses: [{ ...process, outputOverflowPolicy: "discard_excess" }],
  }).timeline;
  assert.deepEqual(
    partiallyFreeDiscard.rows.find((row) => row.id === ownerRowId)?.backpressureSpans,
    [],
  );

  const saturatedDiscard = reportToViewModels({
    ...baseSynthetic,
    architectureBufferSnapshots: [
      {
        timeSeconds: 0,
        states: {
          output_a: { ready: 2, pendingIncoming: 0 },
          output_b: { ready: 2, pendingIncoming: 0 },
        },
      },
    ],
    resourceProcesses: [{ ...process, outputOverflowPolicy: "discard_excess" }],
  }).timeline;
  const discardSpans = saturatedDiscard.rows.find(
    (row) => row.id === ownerRowId,
  )?.backpressureSpans;
  assert.equal(discardSpans?.length, 1);
  assert.deepEqual(discardSpans?.[0].bufferIds, ["output_a", "output_b"]);
  assert.equal(discardSpans?.[0].startSeconds, 0);
  assert.equal(discardSpans?.[0].endSeconds, saturatedDiscard.displayDurationSeconds);
  assert.equal(discardSpans?.[0].outputOverflowPolicy, "discard_excess");
});

test("buffer presentation preserves an explicitly unbound Plan buffer", () => {
  const raw = finiteInjectionReportFixture() as PlainRecord;
  const plan = (raw.artifacts as PlainRecord).execution_plan as PlainRecord;
  const state = plan.architectural_state as PlainRecord;
  const buffers = state.buffers as PlainRecord[];
  const output = buffers.find((buffer) => buffer.id === "msf_output");
  assert.ok(output);
  delete output.module;
  delete output.submodule;

  const timeline = reportToViewModels(adaptEvaluationReport(raw)).timeline;
  const track = timeline.bufferTracks.find((item) => item.id === "buffer:msf_output");
  assert.equal(track?.ownerTrackId, "resource:unbound");
  assert.equal(track?.ownerKind, "resource");
  assert.ok(
    timeline.rows
      .flatMap((row) => row.events)
      .flatMap((event) => event.milestones)
      .some(
        (milestone) =>
          milestone.bufferId === "msf_output" &&
          milestone.ownerTrackId === "resource:unbound",
      ),
  );
});

test("milestone-only ownership stays semantic and never creates an empty execution row", () => {
  const model = adaptEvaluationReport(finiteInjectionReportFixture());
  const ownerRef = "idle_node/idle_module";
  const timeline = reportToViewModels({
    ...model,
    architectureBuffers: model.architectureBuffers.map((buffer) =>
      buffer.id === "magic_compute"
        ? { ...buffer, moduleRef: ownerRef, submoduleRef: null }
        : buffer,
    ),
    architectureBufferSnapshots: [],
  }).timeline;

  assert.equal(timeline.bufferTracks.length, 0);
  const ownerRow = timeline.rows.find(
    (row) => row.id === `module:${ownerRef}`,
  );
  assert.equal(ownerRow, undefined);
  assert.ok(
    timeline.rows
      .flatMap((row) => row.events)
      .flatMap((event) => event.milestones)
      .some(
        (milestone) =>
          milestone.ownerTrackId === `module:${ownerRef}` &&
          milestone.kind === "delivered",
      ),
  );
});

test("shared runtime phases project to two logical parents and one in-place host", () => {
  const model = adaptEvaluationReport(coreReportV2Fixture("dynamic-t-full"));
  const instructionIndex = model.output_program.instructions.findIndex(
    (instruction) => instruction.recipes.length > 0,
  );
  assert.notEqual(instructionIndex, -1);
  const instruction = model.output_program.instructions[instructionIndex];
  const firstRecipe = instruction.recipes[0];
  const secondInvocationId = `${firstRecipe.invocationId}:parallel`;
  const secondRecipe = {
    ...firstRecipe,
    invocationId: secondInvocationId,
    sourceOperationIndex: firstRecipe.sourceOperationIndex + 1,
  };
  const entangle = model.completed_events.find(
    (event) => event.runtime?.lineage?.step === "entangle",
  );
  const measurement = model.completed_events.find(
    (event) => event.runtime?.lineage?.step === "measurement",
  );
  const reaction = model.completed_events.find(
    (event) => event.runtime?.lineage?.step === "reaction",
  );
  assert.ok(entangle);
  assert.ok(measurement);
  assert.ok(reaction);

  const sharedEvents = model.completed_events.map((event) => {
    const lineage = event.runtime?.lineage;
    if (lineage?.step !== "entangle" && lineage?.step !== "measurement") return event;
    return {
      ...event,
      runtime: {
        ...event.runtime!,
        lineage: {
          ...lineage,
          workId:
            lineage.step === "measurement"
              ? `program:${instruction.id}:stage:0:shared-measurement`
              : lineage.workId,
          recipeMembers: [
            ...lineage.recipeMembers,
            { invocationId: secondInvocationId, stageIndex: 0 },
          ],
        },
        measurements:
          lineage.step === "measurement"
            ? [
                ...event.runtime!.measurements,
                {
                  registerId: `${secondInvocationId}:stage:0:bit`,
                  bit: 0,
                },
              ]
            : event.runtime!.measurements,
      },
    };
  });
  const nextEventId = Math.max(...model.completed_events.map((event) => event.event_id)) + 1;
  const secondReaction = {
    ...reaction,
    event_id: nextEventId,
    metadata: { ...reaction.metadata },
    runtime: {
      ...reaction.runtime!,
      lineage: {
        ...reaction.runtime!.lineage!,
        workId: `program:${instruction.id}:recipe:${secondInvocationId}:stage:0:reaction`,
        parentEventId: measurement.event_id,
        recipeMembers: [{ invocationId: secondInvocationId, stageIndex: 0 }],
      },
      continuation: { kind: "complete_source" as const, activatedWorkIds: [] },
    },
  };
  const sharedModel = {
    ...model,
    completed_events: [...sharedEvents, secondReaction],
    output_program: {
      ...model.output_program,
      instructions: model.output_program.instructions.map((item, index) =>
        index === instructionIndex
          ? { ...item, recipes: [...item.recipes, secondRecipe] }
          : item,
      ),
    },
    summary: {
      ...model.summary,
      event_count: model.summary.event_count + 1,
    },
  };

  const views = reportToViewModels(sharedModel);
  const runtimeEvents = views.timeline.rows.flatMap((row) => row.events);
  const sharedEntangle = runtimeEvents.find(
    (event) => event.id === `event:${entangle.event_id}`,
  );
  const sharedMeasurement = runtimeEvents.find(
    (event) => event.id === `event:${measurement.event_id}`,
  );
  assert.deepEqual(sharedEntangle?.recipeInvocationIds, [firstRecipe.invocationId, secondInvocationId]);
  assert.deepEqual(sharedMeasurement?.recipeInvocationIds, [firstRecipe.invocationId, secondInvocationId]);
  assert.equal(
    runtimeEvents.filter((event) => event.id === `event:${entangle.event_id}`).length,
    1,
  );
  assert.equal(
    runtimeEvents.filter((event) => event.id === `event:${measurement.event_id}`).length,
    1,
  );
  assert.equal(new Set(runtimeEvents.map((event) => event.id)).size, runtimeEvents.length);
  assert.equal(runtimeEvents.length, views.timeline.renderedEventCount);
  assert.equal(views.timeline.logicalGadgets.length, 2);
  assert.ok(
    views.timeline.logicalGadgets.every(
      (gadget) =>
        gadget.childEventIds.includes(entangle.event_id) &&
        gadget.childEventIds.includes(measurement.event_id),
    ),
  );
  assert.deepEqual(
    views.timeline.logicalGadgets
      .map((gadget) => gadget.measurement?.bit)
      .sort(),
    [0, 1],
  );
  assert.equal(views.timeline.gadgetHosts.length, 1);
  assert.deepEqual(
    views.timeline.gadgetHosts[0].invocationIds,
    [firstRecipe.invocationId, secondInvocationId],
  );
  assert.equal(views.timeline.gadgetHosts[0].label, "T × 2");
  assert.match(
    views.timeline.gadgetHosts[0].ownerTrackId,
    /^submodule:.+\/na_compute\/compute_region$/,
  );
  assert.ok(views.timeline.rows.some((row) => row.label === "Classical Decoder"));
});

test("Report-v2 parser freezes the Plan-v9 topology and runtime mode", () => {
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

test("Recipe timing is absent from the semantic wire and presentation model", () => {
  const raw = finiteInjectionReportFixture() as PlainRecord;
  const instructions = ((((raw.artifacts as PlainRecord).execution_plan as PlainRecord)
    .program_dag as PlainRecord).instructions as PlainRecord[]);
  const recipe = (instructions.find(
    (instruction) => (instruction.implementation_recipes as PlainRecord[] | undefined)?.length,
  )?.implementation_recipes as PlainRecord[])[0];
  const stage = (recipe.stages as PlainRecord[])[0];

  assert.equal(Object.prototype.hasOwnProperty.call(recipe, "reaction_duration_s"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(recipe, "correction_duration_s"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(stage, "attempt_duration_s"), false);

  const recipeView = reportToViewModels(adaptEvaluationReport(raw))
    .programExecution.outputProgram.instructions
    .flatMap((instruction) => instruction.recipes)[0] as unknown as PlainRecord;
  assert.equal(Object.prototype.hasOwnProperty.call(recipeView, "reactionDurationSeconds"), false);
  assert.equal(Object.prototype.hasOwnProperty.call(recipeView, "correctionDurationSeconds"), false);
  assert.equal(
    Object.prototype.hasOwnProperty.call(
      (recipeView.stages as PlainRecord[])[0],
      "attemptDurationSeconds",
    ),
    false,
  );
});

test("Report-v2 parser strictly validates continuation work templates", () => {
  const templateFrom = (raw: PlainRecord): PlainRecord => {
    const instructions = ((((raw.artifacts as PlainRecord).execution_plan as PlainRecord)
      .program_dag as PlainRecord).instructions as PlainRecord[]);
    const template = instructions
      .flatMap((instruction) =>
        (instruction.continuation_templates as PlainRecord[] | undefined) ?? [],
      )[0];
    assert.ok(template);
    return template;
  };

  const unknownField = finiteInjectionReportFixture() as PlainRecord;
  templateFrom(unknownField).unexpected = true;
  assert.throws(
    () => parseEvaluationReportV2(unknownField),
    /continuation_templates.*unknown \[unexpected\]/,
  );

  const negativeDuration = finiteInjectionReportFixture() as PlainRecord;
  templateFrom(negativeDuration).duration_s = -1;
  assert.throws(
    () => parseEvaluationReportV2(negativeDuration),
    /continuation_templates.*duration_s.*non-negative finite number/,
  );

  const foreignRecipe = finiteInjectionReportFixture() as PlainRecord;
  const foreignMembers = templateFrom(foreignRecipe).recipe_members as PlainRecord[];
  foreignMembers[0].recipe_invocation_id = "not-owned-by-this-instruction";
  assert.throws(
    () => parseEvaluationReportV2(foreignRecipe),
    /recipe_invocation_id.*recipe owned by the same Program instruction/,
  );
});

test("Report-v2 parser strictly validates shared recipe membership", () => {
  const programTransitions = (raw: PlainRecord): PlainRecord[] =>
    ((((raw.artifacts as PlainRecord).execution_trace as PlainRecord)
      .transitions as PlainRecord[]).filter(
      (transition) => transition.plane === "program",
    ));
  const lineageFor = (transition: PlainRecord): PlainRecord => {
    const lineage = transition.program_lineage as PlainRecord | null;
    assert.ok(lineage);
    return lineage;
  };

  const duplicate = finiteInjectionReportFixture() as PlainRecord;
  const duplicateEntangle = programTransitions(duplicate).find(
    (transition) => lineageFor(transition).step === "entangle",
  );
  assert.ok(duplicateEntangle);
  const duplicateMembers = lineageFor(duplicateEntangle).recipe_members as PlainRecord[];
  duplicateMembers.push({ ...duplicateMembers[0] });
  assert.throws(
    () => parseEvaluationReportV2(duplicate),
    /recipe_members.*unique recipe invocation\/stage members/,
  );

  const unknown = finiteInjectionReportFixture() as PlainRecord;
  const unknownEntangle = programTransitions(unknown).find(
    (transition) => lineageFor(transition).step === "entangle",
  );
  assert.ok(unknownEntangle);
  const unknownMembers = lineageFor(unknownEntangle).recipe_members as PlainRecord[];
  unknownMembers[0].recipe_invocation_id = "not-in-this-plan";
  assert.throws(
    () => parseEvaluationReportV2(unknown),
    /recipe_invocation_id.*recipe owned by the source Program instruction/,
  );

  const emptyMeasurement = finiteInjectionReportFixture() as PlainRecord;
  const measurement = programTransitions(emptyMeasurement).find(
    (transition) => lineageFor(transition).step === "measurement",
  );
  assert.ok(measurement);
  lineageFor(measurement).recipe_members = [];
  assert.throws(
    () => parseEvaluationReportV2(emptyMeasurement),
    /continuation work with parent and recipe members/,
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

test("Report-v2 parser rejects malformed Trace-v4 Program causality", () => {
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
    /continuation\.kind.*complete_source/,
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
    /continuation\.kind.*complete_source/,
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
