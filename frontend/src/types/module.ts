export type Role = "compute" | "memory" | "factory";
export type InteractionMode = "drag" | "connect";

export interface ModuleLink {
  sourceId: string;
  targetId: string;
}
export type Modality = "sc" | "na" | "ti";
export type QECType = "rotated-surface" | "unrotated-surface" | "bb";
export type BBParam = "[[144, 12, 12]]" | "[[288, 12, 18]]";
export type MSFProtocol = "15-to-1" | "cultivation" | "custom";

export interface Module {
  id: string;
  customName: string;
  role: Role;
  modality: Modality;
  qecType: QECType;
  distance: number;
  bbParam: BBParam;
  msfProtocol: MSFProtocol;
  physicalQubits: string;
  unitThroughput: string;
  factoryBlocks: string;
  capacity: string;
}

export interface ModulePosition {
  id: string;
  x: number;
  z: number;
}

export const protocolDefaults: Record<Exclude<MSFProtocol, "custom">, { physicalQubits: string; unitThroughput: string }> = {
  "15-to-1": { physicalQubits: "600", unitThroughput: "1000" },
  cultivation: { physicalQubits: "400", unitThroughput: "1500" },
};

export const modalityLabels: Record<Modality, string> = {
  sc: "SC",
  na: "NA",
  ti: "TI",
};

export const roleLabels: Record<Role, string> = {
  compute: "Compute",
  memory: "Memory",
  factory: "Factory",
};

export const qecLabels: Record<QECType, string> = {
  "rotated-surface": "Rotated Surface",
  "unrotated-surface": "Unrotated Surface",
  bb: "BB Code",
};

export function calculateNKD(qecType: QECType, d: number, bbParam: BBParam): string {
  if (qecType === "bb") {
    return bbParam;
  }
  if (qecType === "rotated-surface") {
    const n = d * d;
    return `[[${n}, 1, ${d}]]`;
  }
  // unrotated-surface
  const n = d * d + (d - 1) * (d - 1);
  return `[[${n}, 1, ${d}]]`;
}

export const defaultModules: Module[] = [];
