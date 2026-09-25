export interface ExperimentSetupParams {
  evaluationPreset: "default" | "finite_t_injection_demo_v1";
  /** null requests a full evaluation; numbers limit the evaluated input layers. */
  previewMaxLayers: number | null;
  msfProtocol: "cultivation" | "MSD1" | "MSD2";
  msfCopies: number;
  naCycleTimeMs: number;
  scCycleTimeUs: number;
}

export const defaultExperimentSetupParams: ExperimentSetupParams = {
  // Ordinary evaluations use the core defaults on any supported workload and
  // canonical profile. The locked finite-injection acceptance demo is opt-in.
  evaluationPreset: "default",
  previewMaxLayers: 12,
  msfProtocol: "cultivation",
  msfCopies: 1,
  naCycleTimeMs: 1.0,
  scCycleTimeUs: 1.0,
};
