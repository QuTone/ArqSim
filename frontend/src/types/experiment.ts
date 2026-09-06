export interface ExperimentSetupParams {
  evaluationPreset: "default" | "finite_t_injection_demo_v1";
  msfProtocol: "cultivation" | "MSD1" | "MSD2";
  msfCopies: number;
  naCycleTimeMs: number;
  scCycleTimeUs: number;
}

export const defaultExperimentSetupParams: ExperimentSetupParams = {
  // The browser owns an acceptance-demo default because its initial workload
  // and architecture are the ArqSim Timeline Demo on Profile 2.3. This does
  // not change the core API's minimal-config defaults, which remain black-box
  // injection with canonical_reference_v1 fidelity. Disabling fidelity is an
  // explicit opt-out.
  evaluationPreset: "finite_t_injection_demo_v1",
  msfProtocol: "cultivation",
  msfCopies: 1,
  naCycleTimeMs: 1.0,
  scCycleTimeUs: 1.0,
};
