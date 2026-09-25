import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import { adaptEvaluationReport, reportToViewModels } from "./reportAdapter";
import { timelineOwnershipResolver } from "./timelineOwnership";
import type {
  CompletedEvaluationEventModel,
  EvaluationReportModel,
} from "../types/evaluationReport";

const fixturePaths = [
  "../tests/fixtures/report_v2/static-summary.v2.json",
  "../tests/fixtures/report_v2/dynamic-t-full.v2.json",
  "../system_cases/finite_runtime_injection_demo_measurement_v3/reference/report.v2.json",
];

function fixture(path = fixturePaths[0]): EvaluationReportModel {
  return adaptEvaluationReport(
    JSON.parse(readFileSync(resolve(process.cwd(), path), "utf8")),
  );
}

function owners(model: EvaluationReportModel) {
  return new Set(
    [...model.architecture.nodes, ...model.architecture.interconnects].flatMap(
      (owner) => owner.modules.flatMap((module) => [
        module.ref,
        ...module.submodules.map((submodule) => submodule.ref),
      ]),
    ),
  );
}

function resolver(model: EvaluationReportModel) {
  return timelineOwnershipResolver(
    model.architecture,
    model.engineOwners,
    model.architectureBuffers,
    [...model.completed_events, ...model.inflight_events],
  );
}

function computeEvent(model: EvaluationReportModel): CompletedEvaluationEventModel {
  const event = model.completed_events.find((item) => item.opcode === "EXECUTE_COMPUTE");
  assert.ok(event);
  return structuredClone(event);
}

for (const path of fixturePaths) {
  test(`Real owners preserve event identity, timing and report facts: ${path}`, () => {
    const model = fixture(path);
    const before = JSON.stringify(model);
    const valid = owners(model);
    const timeline = reportToViewModels(model, { maxProgramLayers: 240 }).timeline;
    assert.ok(timeline.rows.length > 0);
    assert.ok(timeline.rows.length <= valid.size);
    for (const row of timeline.rows) {
      assert.ok(["module", "submodule"].includes(row.trackKind));
      assert.ok(valid.has(row.id.replace(/^(submodule|module):/, "")));
      for (const event of row.events) {
        assert.equal(event.locus.ownerRefs.length, 1);
        assert.ok(valid.has(event.locus.ownerRefs[0]));
        assert.equal(event.locus.trackId, row.id);
        assert.ok(event.locus.participantRefs.every((ref) => valid.has(ref)));
        const source = [...model.completed_events, ...model.inflight_events].find(
          (item) => `event:${item.event_id}` === event.id,
        );
        assert.ok(source);
        assert.equal(event.startSeconds, source.start_s);
        assert.equal(event.endSeconds, Math.min(source.end_s, timeline.displayDurationSeconds));
        assert.equal(event.durationSeconds, event.endSeconds - event.startSeconds);
      }
    }
    assert.ok(timeline.groups.every(
      (group) => group.kind === "module" && valid.has(group.id.slice("module:".length)),
    ));
    const events = timeline.rows.flatMap((row) => row.events);
    assert.equal(new Set(events.map((event) => event.id)).size, timeline.renderedEventCount);
    assert.equal(events.some((event) => event.opcode === "FENCE"), false);
    for (const source of model.completed_events.filter((event) => event.opcode !== "FENCE")) {
      assert.equal(events.filter((event) => event.id === `event:${source.event_id}`).length, 1);
    }
    assert.equal(JSON.stringify(model), before, "display ownership must not change timing, claims, tokens, or utilization");
  });
}

test("Generic ownership does not depend on a preset profile name", () => {
  for (const path of fixturePaths) {
    const model = fixture(path);
    const rows = () => reportToViewModels(model).timeline.rows.map((row) => [
      row.id, row.events.map((event) => event.id),
    ]);
    const expected = rows();
    model.profile_id = "custom.profile";
    assert.deepEqual(rows(), expected);
  }
});

test("Teleport source follows recorded locations in both directions, not sorted targets", () => {
  const model = fixture(fixturePaths[2]);
  const endpoints = [
    "na_compute_node/na_compute/magic_state_input_buffer",
    "sc_msf_node/sc_msf/magic_state_output_buffer",
  ];
  const targets = ["na_compute_node/na_compute", "sc_msf_node/sc_msf"];
  const engineOwners = Object.fromEntries(endpoints.map((ref, index) => [
    `endpoint_${index}`, { moduleRef: targets[index], submoduleRef: ref },
  ]));
  const resolveOwner = timelineOwnershipResolver(model.architecture, engineOwners, [], []);
  const source = computeEvent(model);
  for (const direction of [0, 1]) {
    const event = {
      ...source,
      event_id: direction,
      opcode: "TELEPORT_QUBITS",
      engineClaims: { endpoint_0: 1, endpoint_1: 1 },
      tokenFlow: { consumed: {}, produced: {} },
      requiredLocations: { "0": `${endpoints[direction]}/slot-0` },
      completionLocations: { "0": `${endpoints[1 - direction]}/slot-0` },
      metadata: { target_modules: [...targets].reverse(), target_links: ["compute_msf_link"] },
    };
    const owner = resolveOwner(event);
    assert.equal(owner.primaryRef, endpoints[direction]);
    assert.equal(owner.convention, "source_endpoint");
    assert.deepEqual(owner.participantRefs, endpoints);
  }
});

test("Resource teleport uses its consumed payload buffer without inventing link hardware", () => {
  const model = fixture(fixturePaths[2]);
  const teleports = model.completed_events.filter((event) => event.opcode === "TELEPORT_QUBITS");
  assert.ok(teleports.length > 0);
  const resolveOwner = resolver(model);
  for (const event of teleports) {
    const owner = resolveOwner(event);
    assert.equal(owner.primaryRef, "sc_msf_node/sc_msf/magic_state_output_buffer");
    assert.equal(owner.convention, "source_buffer");
    assert.ok(owner.participantRefs.includes("na_compute_node/na_compute/magic_state_input_buffer"));
    assert.ok(owner.participantRefs.includes("compute_msf_link/bell_storage/bell_buffer"));
  }
});

test("Delay-only classical reaction uses its causal parent without fabricating a decoder claim", () => {
  const model = fixture(fixturePaths[1]);
  const reaction = model.completed_events.find((event) => event.opcode === "CLASSICAL_REACTION");
  assert.ok(reaction);
  assert.deepEqual(reaction.engineClaims, {});
  assert.equal(resolver(model)(reaction).primaryRef, "na_node/na_compute/compute_region");
  assert.equal(resolver(model)(reaction).convention, "parent_operation");
  assert.deepEqual(reaction.engineClaims, {});
  const missing = structuredClone(reaction);
  missing.runtime.lineage.parentEventId = 999999;
  assert.throws(() => resolver(model)(missing), /missing causal parent/);
  const cycle = structuredClone(reaction);
  cycle.runtime.lineage.parentEventId = cycle.event_id;
  assert.throws(
    () => resolver({ ...model, completed_events: [cycle] })(cycle),
    /cyclic parent-event/,
  );
});

test("Missing ownership and fabricated targets, engines or locations fail closed", () => {
  const model = fixture();
  const event = computeEvent(model);
  event.engineClaims = {};
  event.requiredLocations = {};
  event.completionLocations = {};
  event.metadata = {};
  event.tokenFlow = { consumed: {}, produced: {} };
  assert.throws(() => resolver(model)(event), /no architecture-backed primary owner/);
  event.metadata.target_modules = ["fake/module"];
  assert.throws(() => resolver(model)(event), /target Module is absent/);
  event.metadata.target_modules = [];
  event.engineClaims = { ghost: 1 };
  assert.throws(() => resolver(model)(event), /unknown claimed engine/);
  event.engineClaims = {};
  event.requiredLocations = { "0": "fake/module/slot-0" };
  assert.throws(() => resolver(model)(event), /source location.*no architecture owner/);
});

test("A buffer bound to a missing Module is not accepted as ownership evidence", () => {
  const model = fixture(fixturePaths[2]);
  const transfer = model.completed_events.find((event) => event.opcode === "TELEPORT_QUBITS");
  assert.ok(transfer);
  const invalid = {
    ...model,
    architectureBuffers: model.architectureBuffers.map((buffer) => buffer.id === "msf_output"
      ? { ...buffer, moduleRef: "fake/module", submoduleRef: null }
      : buffer),
  };
  assert.throws(() => resolver(invalid)(transfer), /buffer.*owner|buffer.*Module|buffer.*architecture/i);
});

test("Pairwise interactions use stable existing owners without pairwise track growth", () => {
  const model = fixture();
  const template = model.architecture.nodes[0];
  const module = template.modules[0];
  const nodes = Array.from({ length: 8 }, (_, index) => ({
    ...template,
    id: `n${index}`,
    modules: [{ ...module, ref: `n${index}/${module.id}`, submodules: [] }],
  }));
  const architecture = { ...model.architecture, nodes, interconnects: [] };
  const resolveOwner = timelineOwnershipResolver(architecture, {}, [], []);
  const event = computeEvent(model);
  const seen = new Set<string>();
  let id = 0;
  for (let first = 0; first < nodes.length; first++) {
    for (let second = first + 1; second < nodes.length; second++) {
      const targets = [nodes[first].modules[0].ref, nodes[second].modules[0].ref];
      const work = {
        ...event,
        event_id: id++,
        engineClaims: {},
        requiredLocations: {},
        completionLocations: {},
        tokenFlow: { consumed: {}, produced: {} },
        metadata: { target_modules: targets },
      };
      const chosen = resolveOwner(work);
      const reversed = resolveOwner({
        ...work, event_id: id++, metadata: { target_modules: [...targets].reverse() },
      });
      assert.equal(chosen.primaryRef, reversed.primaryRef);
      assert.deepEqual(chosen.participantRefs, targets);
      seen.add(chosen.primaryRef);
    }
  }
  assert.equal(id / 2, 28);
  assert.ok(seen.size <= nodes.length);
});
