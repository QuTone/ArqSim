export interface ExperimentSetupParams {
  evaluationPreset: "default" | "finite_t_injection_demo_v1";
  msfProtocol: "cultivation" | "MSD1" | "MSD2";
  msfCopies: number;
  naCycleTimeMs: number;
  scCycleTimeUs: number;
}

export const defaultExperimentSetupParams: ExperimentSetupParams = {
  evaluationPreset: "default",
  msfProtocol: "cultivation",
  msfCopies: 1,
  naCycleTimeMs: 1.0,
  scCycleTimeUs: 1.0,
};
