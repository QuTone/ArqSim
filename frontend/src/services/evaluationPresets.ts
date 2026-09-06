import type { EvaluationConfigDocument } from "@/services/api";

export const FINITE_INJECTION_DEMO_PRESET_ID =
  "finite_t_injection_demo_v1" as const;
export const FINITE_INJECTION_DEMO_PROGRAM_ID =
  "arqsim_timeline_demo" as const;
export const FINITE_INJECTION_DEMO_PROFILE_ID = "2.3" as const;

/**
 * Browser runs are exploratory evaluations, even when their policy and input
 * mirror the frozen finite-injection System Case. Keep their execution identity out of
 * the System Case namespace so a live result cannot be mistaken for frozen
 * evidence.
 */
export function finiteInjectionExploratoryWorkflowId(): string {
  return (
    `frontend:${FINITE_INJECTION_DEMO_PRESET_ID}:` +
    `${FINITE_INJECTION_DEMO_PROGRAM_ID}__${FINITE_INJECTION_DEMO_PROFILE_ID}`
  );
}

/** Keep the acceptance path tied to the frozen demo's workload/profile pair. */
export function finiteInjectionDemoSelectionError(
  programIds: readonly string[],
  profileIds: readonly (string | null | undefined)[],
): string | null {
  const hasExpectedProgram =
    programIds.length === 1 &&
    programIds[0] === FINITE_INJECTION_DEMO_PROGRAM_ID;
  const hasExpectedProfile =
    profileIds.length === 1 &&
    profileIds[0] === FINITE_INJECTION_DEMO_PROFILE_ID;
  if (hasExpectedProgram && hasExpectedProfile) return null;
  return (
    "The finite-T acceptance demo requires exactly ArqSim Timeline Demo and " +
    "Architecture Profile 2.3. Remove other selections or switch Runtime " +
    "semantics to Default."
  );
}

/**
 * Explicit finite-injection demonstration policy.
 *
 * The core has no wire-level named latency preset, so the two reaction values
 * and their provenance travel together here. OperationLatencyProfile fills
 * all other fields from its public defaults. Keeping this in one helper avoids
 * silently changing the application's global black-box/canonical-fidelity
 * defaults.
 */
export function finiteInjectionDemoConfig(): EvaluationConfigDocument {
  return {
    schema_version: "arqsim.evaluation-config.v1",
    profile_id: FINITE_INJECTION_DEMO_PROFILE_ID,
    workflow_id: finiteInjectionExploratoryWorkflowId(),
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
      seed: 0,
    },
    fidelity_profile: { preset: "canonical_reference_v1" },
  };
}
