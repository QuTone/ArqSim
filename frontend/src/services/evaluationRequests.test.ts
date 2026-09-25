import assert from "node:assert/strict";
import test from "node:test";

import type { Benchmark } from "./api";
import { defaultExperimentSetupParams } from "../types/experiment";
import { finiteInjectionDemoConfig } from "./evaluationPresets";
import {
  benchmarkRepresentation,
  buildEvaluationRequest,
  previewEvaluationArchitecture,
} from "./evaluationRequests";

const demo: Benchmark = {
  id: "arqsim_timeline_demo",
  name: "ArqSim Timeline Demo",
  tGates: "2",
  depth: 4,
  category: "other",
  representations: ["clifford_t"],
};
const benchmark: Benchmark = {
  ...demo,
  id: "qaoa_30",
  name: "QAOA 30",
  representations: ["clifford_t", "pbc"],
};
const device = { rPhybell: 1e4 };
const profiles = ["1.1", "1.2", "1.3", "2.1", "2.2", "2.3"];

test("default requests accept a non-demo benchmark across every canonical preset", () => {
  for (const profileId of profiles) {
    const request = buildEvaluationRequest(
      benchmark,
      previewEvaluationArchitecture(profileId),
      defaultExperimentSetupParams,
      device,
    );
    assert.equal(request.benchmark_name, "qaoa_30");
    assert.equal(request.preview_max_layers, 12);
    assert.equal(request.representation, ["1.2", "2.2"].includes(profileId) ? "pbc" : "clifford_t");
    assert.deepEqual(request.config, {
      schema_version: "arqsim.evaluation-config.v1",
      profile_id: profileId,
    });
  }
});

test("full and prefix evaluations have distinct request identities", () => {
  const request = (limit: number | null) => buildEvaluationRequest(
    benchmark,
    previewEvaluationArchitecture("2.3"),
    { ...defaultExperimentSetupParams, previewMaxLayers: limit },
    device,
  );
  const full = request(null);
  assert.equal(Object.prototype.hasOwnProperty.call(full, "preview_max_layers"), false);
  assert.equal(request(24).preview_max_layers, 24);
  assert.equal(new Set([request(12), request(24), full].map((value) => JSON.stringify(value))).size, 3);
  for (const limit of [0, -1, 1.5, 257, Number.NaN, Number.POSITIVE_INFINITY]) {
    assert.throws(() => request(limit), /Preview layers must be an integer/);
  }
});

test("the Clifford+T-only demo runs on SC presets without requesting a missing PBC file", () => {
  for (const profileId of profiles) {
    const request = buildEvaluationRequest(
      demo,
      previewEvaluationArchitecture(profileId),
      defaultExperimentSetupParams,
      device,
    );
    assert.equal(request.representation, "clifford_t");
    assert.equal(request.config.profile_id, profileId);
  }
});

test("request routing fails explicitly when catalog representations cannot serve a preset", () => {
  assert.throws(
    () => benchmarkRepresentation({ ...benchmark, representations: ["pbc"] }, "2.3"),
    /no compatible workload representation for Profile 2\.3/,
  );
  assert.throws(
    () => benchmarkRepresentation({ ...benchmark, representations: [] }, "1.2"),
    /Available: none/,
  );
  const missingMetadata = { ...demo };
  Reflect.deleteProperty(missingMetadata, "representations");
  assert.throws(
    () => benchmarkRepresentation(missingMetadata, "1.2"),
    /Update the backend and reload/,
  );
});

test("generic requests preserve experiment and device overrides", () => {
  const request = buildEvaluationRequest(
    benchmark,
    previewEvaluationArchitecture("2.3"),
    {
      ...defaultExperimentSetupParams,
      msfCopies: 3,
      msfProtocol: "MSD2",
      naCycleTimeMs: 2,
      scCycleTimeUs: 4,
    },
    { rPhybell: 1e5 },
  );
  assert.deepEqual(request.config.layout_policy_overrides, {
    "protocols.magic_state.copies": 3,
    "protocols.magic_state.id": "litinski-15to1-17-7-7-p1e3",
    "protocols.entanglement_distillation.reference_physical_bell_pair_rate_per_s": 1e5,
    "timing.qec_cycle_time_s_by_modality.neutral_atom": 0.002,
    "timing.qec_cycle_time_s_by_modality.superconducting": 0.000004,
  });
});

test("finite injection stays opt-in with its fixed policy and incompatible selections fail", () => {
  const experiment = {
    ...defaultExperimentSetupParams,
    evaluationPreset: "finite_t_injection_demo_v1" as const,
    msfCopies: 9,
  };
  assert.deepEqual(
    buildEvaluationRequest(demo, previewEvaluationArchitecture("2.3"), experiment, { rPhybell: 1e5 }),
    { benchmark_name: demo.id, representation: "clifford_t", config: finiteInjectionDemoConfig() },
  );
  assert.throws(
    () => buildEvaluationRequest(demo, previewEvaluationArchitecture("1.2"), experiment, device),
    /requires exactly ArqSim Timeline Demo and Architecture Profile 2\.3/,
  );
  assert.throws(
    () => buildEvaluationRequest(benchmark, previewEvaluationArchitecture("2.3"), experiment, device),
    /requires exactly ArqSim Timeline Demo/,
  );
});

test("the unselected preview keeps its profile identity and label in the request", () => {
  const architecture = previewEvaluationArchitecture("1.2", "SC-CF");
  assert.deepEqual(architecture, { id: "profile:1.2", profileId: "1.2", name: "SC-CF" });
  assert.equal(
    buildEvaluationRequest(demo, architecture, defaultExperimentSetupParams, device).config.profile_id,
    "1.2",
  );
  assert.equal(previewEvaluationArchitecture(null).profileId, "2.3");
  assert.throws(
    () => buildEvaluationRequest(demo, { id: "custom", name: "Custom" }, defaultExperimentSetupParams, device),
    /authoring-only composition/,
  );
});
