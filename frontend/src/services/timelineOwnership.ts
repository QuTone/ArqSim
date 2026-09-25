/** One architecture-backed display anchor per event, never a scheduling decision. */
import type {
  ArchitectureBufferModel,
  ArchitectureHierarchyViewModel,
  CompletedEvaluationEventModel,
  EngineOwnershipViewModel,
} from "../types/evaluationReport";

export interface TimelineOwnership {
  primaryRef: string;
  participantRefs: readonly string[];
  convention: string;
}

export function timelineOwnershipResolver(
  architecture: ArchitectureHierarchyViewModel,
  engineOwners: Readonly<Record<string, EngineOwnershipViewModel>>,
  buffers: readonly ArchitectureBufferModel[],
  events: readonly CompletedEvaluationEventModel[],
  legacy = false,
): (event: CompletedEvaluationEventModel) => TimelineOwnership {
  const modules = new Set<string>();
  const submodules = new Map<string, string>();
  const computeRegions = new Set<string>();
  for (const owner of [...architecture.nodes, ...architecture.interconnects]) {
    for (const module of owner.modules) {
      modules.add(module.ref);
      for (const submodule of module.submodules) {
        submodules.set(submodule.ref, module.ref);
        if (submodule.type === "region" && submodule.payload === "logical_qubit") {
          computeRegions.add(submodule.ref);
        }
      }
    }
  }
  const isOwner = (ref: string) => modules.has(ref) || submodules.has(ref);
  const locationOwner = (location: string): string | null => {
    // Slots may be more specific than a Submodule. Stop at a real declared owner.
    let ref = location;
    while (ref.includes("/")) {
      if (isOwner(ref)) return ref;
      ref = ref.slice(0, ref.lastIndexOf("/"));
    }
    return null;
  };
  const buffersById = new Map(buffers.map((buffer) => [buffer.id, buffer]));
  const eventsById = new Map(events.map((event) => [event.event_id, event]));
  const cache = new Map<number, TimelineOwnership>();
  const visiting = new Set<number>();
  const fail = (event: CompletedEvaluationEventModel, reason: string): never => {
    throw new Error(`Timeline ownership for event ${event.event_id} (${event.opcode}): ${reason}`);
  };
  const strings = (value: unknown): string[] =>
    Array.isArray(value) && value.every((item) => typeof item === "string") ? value : [];
  const distinct = (refs: readonly string[]) => [...new Set(refs)].sort();

  function resolve(event: CompletedEvaluationEventModel): TimelineOwnership {
    const previous = cache.get(event.event_id);
    if (previous) return previous;
    if (visiting.has(event.event_id)) fail(event, "cyclic parent-event ownership");
    visiting.add(event.event_id);
    try {
      const targets = strings(event.metadata.target_modules);
      if (targets.some((ref) => !modules.has(ref))) {
        fail(event, "target Module is absent from the architecture");
      }
      const engines = distinct(Object.entries(event.engineClaims).flatMap(([id, demand]) => {
        if (demand <= 0) return [];
        const owner = engineOwners[id];
        if (!owner) {
          if (legacy) return [];
          return fail(event, `unknown claimed engine ${id}`);
        }
        if (owner.submoduleRef !== null) {
          if (submodules.get(owner.submoduleRef) !== owner.moduleRef) {
            fail(event, "claimed engine must belong to an existing architecture Submodule and its Module");
          }
          return [owner.submoduleRef];
        }
        if (owner.moduleRef !== null && !modules.has(owner.moduleRef)) {
          fail(event, "claimed engine Module is absent from the architecture");
        }
        // A legally unbound engine is not itself an architectural location.
        return owner.moduleRef === null ? [] : [owner.moduleRef];
      }));
      const bufferRefs = (ids: readonly string[]) => ids.flatMap((id) => {
        const buffer = buffersById.get(id);
        if (!buffer) {
          if (legacy) return [];
          return fail(event, `unknown token-flow buffer ${id}`);
        }
        if (buffer.submoduleRef !== null && submodules.get(buffer.submoduleRef) !== buffer.moduleRef) {
          return fail(event, "token-flow buffer must belong to an existing Submodule and its Module");
        }
        const ref = buffer.submoduleRef ?? buffer.moduleRef;
        if (ref !== null && !isOwner(ref)) fail(event, "token-flow buffer owner is absent from the architecture");
        return ref === null ? [] : [ref];
      });
      const consumed = bufferRefs(Object.keys(event.tokenFlow.consumed));
      const produced = bufferRefs(Object.keys(event.tokenFlow.produced));
      const locations = Object.values(event.requiredLocations ?? {}).flatMap((ref) => {
        const owner = locationOwner(ref);
        if (!owner) fail(event, `source location ${ref} has no architecture owner`);
        return [owner];
      });
      const destinations = Object.values(event.completionLocations ?? {}).flatMap((ref) => {
        const owner = locationOwner(ref);
        if (!owner) fail(event, `destination location ${ref} has no architecture owner`);
        return [owner];
      });
      const evidence = distinct([
        ...engines, ...targets, ...consumed, ...produced, ...locations, ...destinations,
      ]);
      // Avoid listing an ancestor Module again when a more precise participant is known.
      const participants = evidence.filter((ref) => !evidence.some((other) => other.startsWith(`${ref}/`)));
      const finish = (
        primaryRef: string,
        convention: string,
        refs: readonly string[] = participants,
      ): TimelineOwnership => {
        if (!isOwner(primaryRef)) fail(event, "primary owner must be an existing Module or Submodule");
        const result = { primaryRef, participantRefs: refs, convention };
        cache.set(event.event_id, result);
        return result;
      };

      if (event.opcode === "TELEPORT_QUBITS" || event.opcode === "MOVE_QUBITS") {
        const sourceModules = distinct(locations.map((ref) => submodules.get(ref) ?? ref));
        const sourceNodes = distinct(locations.map((ref) => ref.split("/")[0]));
        if (event.opcode === "TELEPORT_QUBITS" && sourceNodes.length === 1) {
          const endpoints = engines.filter((ref) => ref.startsWith(`${sourceNodes[0]}/`)
            && targets.some((target) => ref === target || ref.startsWith(`${target}/`)));
          if (endpoints.length === 1) return finish(endpoints[0], "source_endpoint");
        }
        if (event.opcode === "TELEPORT_QUBITS" && sourceModules.length === 1) {
          const local = engines.filter((ref) => ref === sourceModules[0] || ref.startsWith(`${sourceModules[0]}/`));
          if (local.length === 1) return finish(local[0], "source_endpoint");
        }
        if (event.opcode === "TELEPORT_QUBITS" && sourceNodes.length === 1) {
          const sourceEngines = engines.filter((ref) => ref.startsWith(`${sourceNodes[0]}/`));
          if (sourceEngines.length === 1) return finish(sourceEngines[0], "source_endpoint");
        }
        // Resource transfers have no logical-qubit locations. The consumed payload
        // buffer identifies the source; shared link Bell storage is not an endpoint.
        const sources = distinct(consumed.filter((ref) => targets.some((target) => ref === target || ref.startsWith(`${target}/`))));
        if (sources.length === 1) return finish(sources[0], "source_buffer");
        if (engines.length === 0 && locations.length === 1) return finish(locations[0], "source_location");
      }
      const regions = engines.filter((ref) => computeRegions.has(ref) && targets.includes(submodules.get(ref)!));
      if (regions.length === 1) return finish(regions[0], "claimed_execution_region");
      if (engines.length === 1) return finish(engines[0], "single_engine_owner");
      // Multiple owners remain participants. Canonical sorting chooses only a
      // stable display anchor; it does not reduce the event's engine claims.
      if (engines.length > 1) return finish(engines[0], "canonical_engine_owner");
      if (targets.length === 1) return finish(targets[0], "single_target_module");
      if (participants.length > 0) return finish(participants[0], "canonical_participant");

      // Classical reaction is often a delay-only continuation, not a declared
      // decoder device. Anchor at its causal parent's location; claim no engine.
      const parentId = event.runtime?.lineage?.parentEventId;
      if (event.opcode === "CLASSICAL_REACTION" && parentId !== null && parentId !== undefined) {
        const parent = eventsById.get(parentId);
        if (!parent) fail(event, "missing causal parent for classical reaction");
        const anchor = resolve(parent);
        return finish(anchor.primaryRef, "parent_operation", anchor.participantRefs);
      }
      return fail(event, "no architecture-backed primary owner; supply ownership evidence instead of a synthetic track");
    } finally {
      visiting.delete(event.event_id);
    }
  }
  return resolve;
}
