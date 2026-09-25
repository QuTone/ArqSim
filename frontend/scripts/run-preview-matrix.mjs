import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const frontend = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const root = resolve(frontend, "..");
const generator = join(root, "server/tests/generate_preview_reports.py");
const python = process.env.ARQSIM_PYTHON || "python";
const work = await mkdtemp(join(tmpdir(), "arqsim-preview-matrix-"));
try {
  const bundle = join(work, "adapter.mjs");
  await build({
    stdin: {
      contents: [
        'export { buildEvaluationRequest, previewEvaluationArchitecture } from "./src/services/evaluationRequests";',
        'export { defaultExperimentSetupParams } from "./src/types/experiment";',
        'export { adaptEvaluationReport, reportToViewModels } from "./src/services/reportAdapter";',
      ].join("\n"),
      resolveDir: frontend,
      sourcefile: "preview-matrix.ts",
    },
    bundle: true, platform: "node", format: "esm", outfile: bundle,
    tsconfig: join(frontend, "tsconfig.app.json"),
  });
  const helpers = await import(pathToFileURL(bundle).href);
  const catalogResult = spawnSync(python, [generator, "--catalog"], {
    cwd: root, encoding: "utf8", maxBuffer: 8 * 1024 * 1024,
  });
  assert.equal(catalogResult.status, 0, catalogResult.error?.message || catalogResult.stderr);
  const catalog = JSON.parse(catalogResult.stdout);
  assert.ok(catalog.length > 0, "The benchmark catalog must not be empty");
  const profiles = ["1.1", "1.2", "1.3", "2.1", "2.2", "2.3"];
  const requests = catalog.flatMap((benchmark) => profiles.map((profile) =>
    helpers.buildEvaluationRequest(benchmark, helpers.previewEvaluationArchitecture(profile),
      helpers.defaultExperimentSetupParams, { rPhybell: 1e4 })));
  assert.equal(new Set(requests.map((request) => JSON.stringify(request))).size, requests.length);
  assert.ok(requests.every((request) => request.preview_max_layers === 12));
  const requestPath = join(work, "requests.json");
  await writeFile(requestPath, JSON.stringify(requests));
  const generated = spawnSync(python, [generator, "--requests", requestPath, "--output", work], {
    cwd: root, stdio: "inherit",
  });
  assert.equal(generated.status, 0, generated.error?.message || "Preview report generation failed");
  const entries = JSON.parse(await readFile(join(work, "index.json"), "utf8"));
  assert.equal(entries.length, requests.length);
  for (const [index, entry] of entries.entries()) {
    assert.deepEqual(entry.request, requests[index]);
    const report = JSON.parse(await readFile(join(work, entry.filename), "utf8"));
    const before = JSON.stringify(report);
    const model = helpers.adaptEvaluationReport(report);
    const views = helpers.reportToViewModels(model, { maxProgramLayers: 12 });
    const { timeline, headline, evaluationScope: scope } = views;
    const summary = report.results.summary;
    assert.equal(headline.profileId, entry.request.config.profile_id);
    assert.equal(headline.totalLatencySeconds, summary.total_latency_s);
    assert.equal(headline.totalPhysicalQubits, summary.total_physical_qubits);
    assert.equal(headline.successProbability, summary.success_probability);
    assert.equal(headline.completedProgramInstructions, summary.completed_program_instructions);
    assert.equal(headline.fidelityCompleteCoverage, true);
    assert.equal(scope.kind, "prefix_preview");
    assert.equal(scope.requestedMaxLayers, 12);
    assert.equal(scope.evaluatedLayerCount, report.request.workload.layers.length);
    assert.equal(scope.evaluatedLayerCount, Math.min(12, scope.sourceLayerCount));
    assert.equal(scope.evaluatedOperationCount, report.request.workload.layers.reduce(
      (count, layer) => count + layer.operations.length, 0));
    assert.equal(views.circuitStatistics.layerCount, scope.evaluatedLayerCount);
    assert.equal(timeline.visibleProgramLayerCount, scope.evaluatedLayerCount);
    assert.equal(timeline.eventTruncated, false);
    assert.ok(timeline.rows.length > 0);
    const validOwners = new Set([...model.architecture.nodes, ...model.architecture.interconnects]
      .flatMap((owner) => owner.modules.flatMap((module) => [
        module.ref, ...module.submodules.map((submodule) => submodule.ref),
      ])));
    const trace = report.artifacts.execution_trace;
    const rawEvents = new Map([
      ...trace.transitions.filter((event) => event.kind === "completion"), ...trace.terminal_inflight,
    ].map((event) => [`event:${event.event_id}`, event]));
    const ids = new Set();
    for (const row of timeline.rows) {
      assert.ok(["module", "submodule"].includes(row.trackKind));
      assert.match(row.id, /^(module|submodule):/);
      assert.ok(validOwners.has(row.id.replace(/^(module|submodule):/, "")));
      for (const event of row.events) {
        assert.ok(!ids.has(event.id), `Duplicate timeline event ${event.id}`);
        ids.add(event.id);
        assert.equal(event.locus.trackId, row.id);
        assert.equal(event.locus.ownerRefs.length, 1);
        assert.ok(validOwners.has(event.locus.ownerRefs[0]));
        assert.ok(event.locus.participantRefs.every((ref) => validOwners.has(ref)));
        const raw = rawEvents.get(event.id);
        assert.ok(raw, `Timeline event ${event.id} has no backend trace record`);
        assert.notEqual(event.opcode, "FENCE");
        assert.equal(event.opcode, raw.opcode);
        assert.equal(event.startSeconds, raw.start_s);
        assert.equal(event.endSeconds, Math.min(raw.end_s, timeline.displayDurationSeconds));
        assert.equal(event.durationSeconds, event.endSeconds - event.startSeconds);
      }
    }
    assert.ok(ids.size > 0);
    assert.equal(ids.size, timeline.renderedEventCount);
    assert.equal(ids.size, timeline.candidateEventCount);
    assert.equal(JSON.stringify(report), before, "Projection must preserve backend report facts");
  }
  console.log(`Verified ${entries.length} real preview timelines (${catalog.length} benchmarks × ${profiles.length} presets).`);
} finally {
  await rm(work, { recursive: true, force: true });
}
