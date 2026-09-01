import type { EvaluationConfigDocument } from "@/services/api";

export const FINITE_INJECTION_DEMO_PRESET_ID =
  "finite_t_injection_demo_v1" as const;

/**
 * Browser runs are exploratory evaluations, even when their policy and input
 * mirror the frozen finite-injection System Case. Keep their execution identity out of
 * the System Case namespace so a live result cannot be mistaken for frozen
 * evidence.
 */
export function finiteInjectionExploratoryWorkflowId(
  programId: string,
  profileId: string,
): string {
  return `frontend:${FINITE_INJECTION_DEMO_PRESET_ID}:${programId}__${profileId}`;
}

/**
 * Explicit finite-injection demonstration policy.
 *
 * The core has no wire-level named latency preset, so the two reaction values
 * and their provenance travel together here. OperationLatencyProfile fills
 * all other fields from its public defaults. Keeping this in one helper avoids
 * silently changing the application's global black-box/no-fidelity defaults.
 */
export function finiteInjectionDemoConfig(
  profileId: string,
  programId: string,
): EvaluationConfigDocument {
  return {
    schema_version: "arqsim.evaluation-config.v1",
    profile_id: profileId,
    workflow_id: finiteInjectionExploratoryWorkflowId(programId, profileId),
    latency_profile: {
      reaction_latency_by_modality_s: {
        neutral_atom: 5e-4,
        superconducting: 1e-5,
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
      seed: 5,
    },
    fidelity_profile: { preset: "canonical_reference_v1" },
  };
}
