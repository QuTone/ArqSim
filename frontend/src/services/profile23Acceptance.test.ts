import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import test from "node:test";

import { adaptEvaluationReport, reportToViewModels } from "./reportAdapter";

function profile23Reference(): unknown {
  return JSON.parse(
    readFileSync(
      resolve(
        process.cwd(),
        "../system_cases/finite_runtime_injection_demo_measurement_v3/reference/report.v2.json",
      ),
      "utf8",
    ),
  );
}

test("Profile 2.3 end-to-end acceptance matrix stays coherent", () => {
  const model = adaptEvaluationReport(profile23Reference());
  const views = reportToViewModels(model);
  const rows = new Map(views.timeline.rows.map((row) => [row.id, row]));
  const events = views.timeline.rows.flatMap((row) => row.events);
  const eventRows = new Map(
    views.timeline.rows.flatMap((row) =>
      row.events.map((event) => [event.id, row.id] as const),
    ),
  );

  // Architecture skeleton: Memory and Compute are distinct modules; the MSF
  // is a factory with a separate output buffer; the link has two endpoints.
  assert.equal(views.architecture.profileId, "2.3");
  assert.deepEqual(
    views.architecture.nodes.flatMap((node) =>
      node.modules.map((module) => module.ref),
    ),
    [
      "na_compute_node/na_compute",
      "na_compute_node/na_memory",
      "sc_msf_node/sc_msf",
    ],
  );
  const submoduleRefs = views.architecture.nodes.flatMap((node) =>
    node.modules.flatMap((module) =>
      module.submodules.map((submodule) => submodule.ref),
    ),
  );
  assert.ok(submoduleRefs.includes("na_compute_node/na_compute/compute_region"));
  assert.ok(submoduleRefs.includes("na_compute_node/na_compute/store_load_buffer"));
  assert.ok(
    submoduleRefs.includes(
      "na_compute_node/na_compute/magic_state_input_buffer",
    ),
  );
  assert.ok(submoduleRefs.includes("na_compute_node/na_memory/memory_region"));
  assert.ok(submoduleRefs.includes("sc_msf_node/sc_msf/factory_engine"));
  assert.ok(
    submoduleRefs.includes(
      "sc_msf_node/sc_msf/magic_state_output_buffer",
    ),
  );
  const link = views.architecture.interconnects.find(
    (interconnect) => interconnect.id === "compute_msf_link",
  );
  assert.ok(link);
  assert.deepEqual(link.endpoints, ["na_compute_node", "sc_msf_node"]);
  assert.equal(link.access.length, 2);
  assert.deepEqual(
    link.modules.map((module) => [
      module.ref,
      module.submodules.map((submodule) => submodule.ref),
    ]),
    [
      [
        "compute_msf_link/bell_engine",
        ["compute_msf_link/bell_engine/pair_generator"],
      ],
      [
        "compute_msf_link/bell_storage",
        ["compute_msf_link/bell_storage/bell_buffer"],
      ],
    ],
  );

  // Timeline ownership follows semantic locus, not a scheduling-engine claim.
  const computeRow = rows.get(
    "submodule:na_compute_node/na_compute/compute_region",
  );
  const factoryRow = rows.get(
    "submodule:sc_msf_node/sc_msf/factory_engine",
  );
  const pairGeneratorRow = rows.get(
    "submodule:compute_msf_link/bell_engine/pair_generator",
  );
  const linkTransferRow = rows.get("interconnect-transfer:compute_msf_link");
  assert.ok(computeRow?.events.some((event) => event.opcode === "EXECUTE_COMPUTE"));
  assert.ok(factoryRow?.events.some((event) => event.opcode === "PREPARE_MAGIC_STATE"));
  assert.ok(
    pairGeneratorRow?.events.some(
      (event) => event.opcode === "PREPARE_LOGICAL_BELL",
    ),
  );
  assert.ok(
    linkTransferRow?.events.some(
      (event) => event.opcode === "TELEPORT_QUBITS",
    ),
  );
  assert.equal(
    views.timeline.rows.some(
      (row) => row.id.startsWith("module:") || row.id.startsWith("interconnect:"),
    ),
    false,
  );
  const movementRow = rows.get("movement:na_compute_node/na_compute");
  assert.equal(movementRow?.parentTrackId, "module:na_compute_node/na_compute");
  assert.ok(
    movementRow?.events.every((event) => event.opcode === "MOVE_QUBITS"),
  );
  for (const opcode of ["STORE_QUBITS", "LOAD_QUBITS"]) {
    const operation = events.find((event) => event.opcode === opcode);
    assert.ok(operation);
    assert.equal(operation.locus.kind, "transfer");
    assert.equal(
      eventRows.get(operation.id),
      "connection:na_compute_node/na_memory_compute_bus",
    );
  }
  assert.deepEqual(
    views.timeline.groups.map((group) => [group.id, group.label]),
    [
      ["module:na_compute_node/na_compute", "NA Compute"],
      ["module:sc_msf_node/sc_msf", "SC MSF"],
      ["node:na_compute_node", "NA Compute Node"],
      ["interconnect:compute_msf_link", "NA Compute ↔ SC MSF"],
    ],
  );

  // Buffer state is projected separately from execution rows and remains
  // attached to its architectural owner/endpoint.
  assert.deepEqual(
    views.timeline.bufferTracks.map((track) => [
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
  assert.ok(
    events
      .flatMap((event) => event.milestones)
      .some(
        (milestone) =>
          milestone.kind === "delivered" &&
          milestone.bufferId === "magic_compute",
      ),
  );

  // Both T parents survive lowering; their children expose both reaction
  // branches, and each collapsed host belongs to the Compute Region.
  assert.equal(views.timeline.logicalGadgets.length, 2);
  assert.deepEqual(
    views.timeline.logicalGadgets.map((gadget) => gadget.measurement?.bit),
    [1, 0],
  );
  assert.deepEqual(
    views.timeline.logicalGadgets.map((gadget) => gadget.correctionApplied),
    [true, false],
  );
  assert.equal(
    events.filter((event) => event.runtimeLineage?.step === "reaction").length,
    2,
  );
  assert.equal(
    events.filter((event) => event.runtimeLineage?.step === "correction").length,
    1,
  );
  assert.equal(views.timeline.gadgetHosts.length, 2);
  views.timeline.gadgetHosts.forEach((host) => {
    assert.equal(
      host.ownerTrackId,
      "submodule:na_compute_node/na_compute/compute_region",
    );
    assert.ok(host.dispatchSeconds >= host.readySeconds);
    assert.ok(host.completionSeconds > host.dispatchSeconds);
    assert.equal(
      host.realizationElapsedSeconds,
      host.completionSeconds - host.dispatchSeconds,
    );
    assert.equal(host.queueWaitSeconds, host.dispatchSeconds - host.readySeconds);
  });

  // Fidelity is an end-to-end result, including logical and resource idling.
  assert.equal(views.fidelity.completeCoverage, true);
  assert.equal(views.fidelity.unavailableReason, null);
  assert.ok(
    Math.abs(
      (views.fidelity.successProbability ?? 0) - 0.9998943083334262,
    ) < 1e-15,
  );
  assert.deepEqual(
    Object.keys(views.fidelity.logicalIdleCyclesByLocation).sort(),
    [
      "na_compute_node/na_compute",
      "na_compute_node/na_compute/store_load_buffer",
      "na_compute_node/na_memory",
    ],
  );
  assert.deepEqual(Object.keys(views.fidelity.resourceIdleCyclesByLocation), [
    "na_compute_node/na_compute/magic_state_input_buffer",
    "sc_msf_node/sc_msf/magic_state_output_buffer",
  ]);
});
