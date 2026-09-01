import {
  ArchitectureProfileV3Document,
  EvaluationReportModel,
  FTCircuitDocument,
} from "@/types/evaluationReport";
import {
  adaptEvaluationReport,
  parseArchitectureProfile,
} from "@/services/reportAdapter";

const API_URL = import.meta.env.VITE_API_URL ?? "";

export type WorkloadRepresentation = "clifford_t" | "pbc";

/** Public arqsim.evaluation-config.v1 request; omitted fields use core defaults. */
export interface EvaluationConfigDocument {
  schema_version: "arqsim.evaluation-config.v1";
  profile_id: string;
  workflow_id?: string | null;
  layout_policy_overrides?: Record<string, unknown>;
  logical_layout?: Record<string, unknown>;
  latency_profile?: Record<string, unknown>;
  evaluation_policy?: Record<string, unknown>;
  compiler_spec?: Record<string, unknown>;
  fidelity_profile?: Record<string, unknown> | null;
  footprint_model?: Record<string, unknown>;
  runtime_components?: Record<string, unknown>;
  [key: string]: unknown;
}

interface EvaluationRequestBase {
  representation: WorkloadRepresentation;
  config: EvaluationConfigDocument;
}

export type EvaluationRequest = EvaluationRequestBase &
  (
    | { benchmark_name: string; workload?: never }
    | { workload: FTCircuitDocument; benchmark_name?: never }
  );

export interface Benchmark {
  id: string;
  name: string;
  tGates: string;
  depth: number;
  category: string;
}

export function minimalEvaluationConfig(
  profileId: string,
  workflowId?: string,
): EvaluationConfigDocument {
  return {
    schema_version: "arqsim.evaluation-config.v1",
    profile_id: profileId,
    ...(workflowId ? { workflow_id: workflowId } : {}),
  };
}

async function errorDetail(response: Response): Promise<string> {
  const body = await response.json().catch(() => null);
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    return typeof detail === "string" ? detail : JSON.stringify(detail);
  }
  return response.statusText || `HTTP ${response.status}`;
}

export const api = {
  getArchitectureProfiles: async (): Promise<ArchitectureProfileV3Document[]> => {
    const response = await fetch(`${API_URL}/architecture-profiles`);
    if (!response.ok) {
      throw new Error(
        `Failed to fetch architecture profiles: ${await errorDetail(response)}`,
      );
    }
    const body: unknown = await response.json();
    if (!Array.isArray(body)) {
      throw new Error("Invalid architecture profile collection: expected an array");
    }
    return body.map(parseArchitectureProfile);
  },

  getBenchmarks: async (): Promise<Benchmark[]> => {
    const response = await fetch(`${API_URL}/benchmarks`);
    if (!response.ok) {
      throw new Error(`Failed to fetch benchmarks: ${await errorDetail(response)}`);
    }
    return response.json();
  },

  runEvaluation: async (request: EvaluationRequest): Promise<EvaluationReportModel> => {
    const response = await fetch(`${API_URL}/evaluate-v2`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    });
    if (!response.ok) {
      throw new Error(`Evaluation failed: ${await errorDetail(response)}`);
    }
    return adaptEvaluationReport(await response.json());
  },
};
