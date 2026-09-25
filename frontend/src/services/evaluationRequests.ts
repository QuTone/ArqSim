import type { DeviceLibraryParams } from "../components/config/DeviceLibrarySection";
import type { ExperimentSetupParams } from "../types/experiment";
import type {
  Benchmark,
  EvaluationConfigDocument,
  EvaluationRequest,
  WorkloadRepresentation,
} from "./api";
import {
  finiteInjectionDemoConfig,
  finiteInjectionDemoSelectionError,
} from "./evaluationPresets";

export interface EvaluationArchitecture {
  id: string;
  name: string;
  profileId?: string;
}

/** An unselected preset preview is the single architecture to evaluate. */
export function previewEvaluationArchitecture(
  profileId: string | null,
  profileName?: string | null,
): Required<EvaluationArchitecture> {
  const resolvedProfileId = profileId ?? "2.3";
  return {
    id: `profile:${resolvedProfileId}`,
    profileId: resolvedProfileId,
    name: profileName ?? `Profile ${resolvedProfileId}`,
  };
}

export function benchmarkRepresentation(
  program: Benchmark,
  profileId: string,
): WorkloadRepresentation {
  if (!Array.isArray(program.representations)) {
    throw new Error(
      `The benchmark catalog has no representation metadata for ${program.name}. ` +
      "Update the backend and reload the benchmark catalog.",
    );
  }
  if (
    (profileId === "1.2" || profileId === "2.2") &&
    program.representations.includes("pbc")
  ) {
    return "pbc";
  }
  if (program.representations.includes("clifford_t")) return "clifford_t";
  throw new Error(
    `${program.name} has no compatible workload representation for Profile ${profileId}. ` +
    `Available: ${program.representations.join(", ") || "none"}.`,
  );
}

export function buildEvaluationRequest(
  program: Benchmark,
  architecture: EvaluationArchitecture,
  experiment: ExperimentSetupParams,
  device: DeviceLibraryParams,
): EvaluationRequest {
  if (!architecture.profileId) {
    throw new Error(
      `“${architecture.name}” is an authoring-only composition. ` +
      "Live evaluation currently accepts the six canonical ArchitectureProfile presets.",
    );
  }
  const profileId = architecture.profileId;
  if (experiment.evaluationPreset === "finite_t_injection_demo_v1") {
    const selectionError = finiteInjectionDemoSelectionError([program.id], [profileId]);
    if (selectionError) throw new Error(selectionError);
    return {
      benchmark_name: program.id,
      representation: "clifford_t",
      config: finiteInjectionDemoConfig(),
    };
  }

  const config: EvaluationConfigDocument = {
    schema_version: "arqsim.evaluation-config.v1",
    profile_id: profileId,
  };
  const overrides: Record<string, unknown> = {};
  if (experiment.msfCopies !== 1) {
    overrides["protocols.magic_state.copies"] = experiment.msfCopies;
  }
  const protocolIds: Record<ExperimentSetupParams["msfProtocol"], string> = {
    cultivation: "cultivation-d5-d15-p1e3",
    MSD1: "litinski-15to1x20to4-13-5-5-23-11-13-p1e3",
    MSD2: "litinski-15to1-17-7-7-p1e3",
  };
  if (experiment.msfProtocol !== "cultivation") {
    overrides["protocols.magic_state.id"] = protocolIds[experiment.msfProtocol];
  }
  if (device.rPhybell !== 1e4) {
    overrides["protocols.entanglement_distillation.reference_physical_bell_pair_rate_per_s"] =
      device.rPhybell;
  }
  if (experiment.naCycleTimeMs !== 1) {
    overrides["timing.qec_cycle_time_s_by_modality.neutral_atom"] =
      experiment.naCycleTimeMs * 1e-3;
  }
  if (experiment.scCycleTimeUs !== 1) {
    overrides["timing.qec_cycle_time_s_by_modality.superconducting"] =
      experiment.scCycleTimeUs * 1e-6;
  }
  if (Object.keys(overrides).length) config.layout_policy_overrides = overrides;
  const previewMaxLayers = experiment.previewMaxLayers;
  if (
    previewMaxLayers !== null &&
    (!Number.isSafeInteger(previewMaxLayers) || previewMaxLayers < 1 || previewMaxLayers > 256)
  ) {
    throw new Error("Preview layers must be an integer from 1 to 256, or use Full workload.");
  }
  return {
    benchmark_name: program.id,
    representation: benchmarkRepresentation(program, profileId),
    config,
    ...(previewMaxLayers === null ? {} : { preview_max_layers: previewMaxLayers }),
  };
}
