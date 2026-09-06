import { useEffect, useMemo, useState } from "react";
import {
  Atom,
  Box,
  ChevronLeft,
  ChevronRight,
  CircuitBoard,
  Cpu,
  Grid3X3,
  Info,
  Layers3,
  Network,
  Zap,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { SystemInterconnectVisual } from "@/components/architecture/SystemInterconnectVisual";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type {
  ArchitectureConnectionViewModel,
  ArchitectureHierarchyViewModel,
  ArchitectureModuleViewModel,
  ArchitectureNodeViewModel,
  ArchitectureSubmoduleViewModel,
} from "@/types/evaluationReport";

const MAX_LOGICAL_SITE_PX = 72;
const TARGET_COORDINATE_WIDTH_PX = 1120;
const TARGET_COORDINATE_HEIGHT_PX = 640;
const SUBMODULE_MIN_WIDTH = 156;
const SUBMODULE_MIN_HEIGHT = 112;
const SLOT_PAGE_SIZE = 80;

interface ArchitectureHierarchyProps {
  viewModel: ArchitectureHierarchyViewModel;
  className?: string;
  initialNodeId?: string;
  onNodeSelectionChange?: (nodeId: string | null) => void;
}

interface PlaneBox {
  key: string;
  module: ArchitectureModuleViewModel;
  submodule: ArchitectureSubmoduleViewModel;
  left: number;
  top: number;
  width: number;
  height: number;
  anchorLeft: number | null;
  anchorTop: number | null;
}

interface ModulePlaneBox {
  module: ArchitectureModuleViewModel;
  left: number;
  top: number;
  width: number;
  height: number;
}

interface NodePlaneLayout {
  width: number;
  height: number;
  logicalSitePx: number | null;
  submodules: PlaneBox[];
  modules: ModulePlaneBox[];
}

interface CoordinateBounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

function humanize(value: string): string {
  return value.replace(/[._-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function modalityPresentation(modality: string) {
  const normalized = modality.trim().toLowerCase().replace(/[\s-]+/g, "_");
  if (normalized === "neutral_atom" || normalized === "na") {
    return {
      label: "Neutral Atom",
      kind: "neutral_atom" as const,
      accent: "border-quantum-cyan/35 bg-quantum-cyan/10 text-quantum-cyan",
    };
  }
  if (normalized === "superconducting" || normalized === "sc") {
    return {
      label: "Superconducting",
      kind: "superconducting" as const,
      accent: "border-quantum-purple/35 bg-quantum-purple/10 text-quantum-purple",
    };
  }
  return {
    label: humanize(modality || "logical node"),
    kind: "generic" as const,
    accent: "border-border bg-muted/30 text-muted-foreground",
  };
}

function formatCoordinate(coordinate: readonly [number, number]): string {
  return `(${coordinate[0]}, ${coordinate[1]})`;
}

function extendBounds(
  bounds: CoordinateBounds | null,
  coordinate: readonly [number, number],
): CoordinateBounds {
  const [x, y] = coordinate;
  if (!bounds) return { minX: x, maxX: x, minY: y, maxY: y };
  return {
    minX: Math.min(bounds.minX, x),
    maxX: Math.max(bounds.maxX, x),
    minY: Math.min(bounds.minY, y),
    maxY: Math.max(bounds.maxY, y),
  };
}

function submoduleCoordinateBounds(
  submodule: ArchitectureSubmoduleViewModel,
): CoordinateBounds | null {
  let bounds: CoordinateBounds | null = null;
  for (const slot of submodule.slots) {
    if (slot.coordinate) bounds = extendBounds(bounds, slot.coordinate);
  }
  if (!bounds && submodule.logicalOrigin) {
    bounds = extendBounds(null, submodule.logicalOrigin);
  }
  return bounds;
}

function boxesOverlap(left: PlaneBox, right: PlaneBox, gap = 18) {
  return !(
    left.left + left.width + gap <= right.left ||
    right.left + right.width + gap <= left.left ||
    left.top + left.height + gap <= right.top ||
    right.top + right.height + gap <= left.top
  );
}

/**
 * Chip cards are readable labels, not physical footprints. Keep their logical
 * anchor exact, then deterministically displace only the label card when two
 * minimum-size cards would overlap. A leader line preserves the coordinate
 * relationship on the node plane.
 */
function resolveChipLabelCollisions(boxes: PlaneBox[]): PlaneBox[] {
  const placed: PlaneBox[] = [];
  const ordered = [...boxes].sort(
    (left, right) =>
      (left.anchorLeft ?? left.left) - (right.anchorLeft ?? right.left) ||
      (left.anchorTop ?? left.top) - (right.anchorTop ?? right.top) ||
      left.key.localeCompare(right.key),
  );

  for (const source of ordered) {
    const candidate = { ...source };
    for (let attempt = 0; attempt <= placed.length * 2; attempt += 1) {
      const conflict = placed.find((box) => boxesOverlap(candidate, box));
      if (!conflict) break;
      const deltaX = Math.abs(
        (candidate.anchorLeft ?? candidate.left) -
          (conflict.anchorLeft ?? conflict.left),
      );
      const deltaY = Math.abs(
        (candidate.anchorTop ?? candidate.top) -
          (conflict.anchorTop ?? conflict.top),
      );
      if (deltaX > deltaY) {
        candidate.left = conflict.left + conflict.width + 18;
      } else {
        candidate.top = conflict.top + conflict.height + 18;
      }
    }
    placed.push(candidate);
  }

  const byKey = new Map(placed.map((box) => [box.key, box]));
  return boxes.map((box) => byKey.get(box.key) ?? box);
}

function buildNodePlaneLayout(node: ArchitectureNodeViewModel): NodePlaneLayout {
  const entries = node.modules.flatMap((module) =>
    module.submodules.map((submodule) => ({
      module,
      submodule,
      bounds: submoduleCoordinateBounds(submodule),
    })),
  );

  let globalBounds: CoordinateBounds | null = null;
  for (const entry of entries) {
    if (!entry.bounds) continue;
    globalBounds = extendBounds(globalBounds, [entry.bounds.minX, entry.bounds.minY]);
    globalBounds = extendBounds(globalBounds, [entry.bounds.maxX, entry.bounds.maxY]);
  }
  const minX = globalBounds?.minX ?? 0;
  const minY = globalBounds?.minY ?? 0;
  const maxX = globalBounds?.maxX ?? 0;
  const maxY = globalBounds?.maxY ?? 0;
  const coordinateSpanX = maxX - minX;
  const coordinateSpanY = maxY - minY;
  const logicalSitePx = globalBounds
    ? Math.min(
        MAX_LOGICAL_SITE_PX,
        coordinateSpanX > 0 ? TARGET_COORDINATE_WIDTH_PX / coordinateSpanX : MAX_LOGICAL_SITE_PX,
        coordinateSpanY > 0 ? TARGET_COORDINATE_HEIGHT_PX / coordinateSpanY : MAX_LOGICAL_SITE_PX,
      )
    : MAX_LOGICAL_SITE_PX;

  const positioned: PlaneBox[] = [];
  const unpositioned: typeof entries = [];

  for (const entry of entries) {
    if (!entry.bounds) {
      unpositioned.push(entry);
      continue;
    }

    const entryMinX = entry.bounds.minX;
    const entryMaxX = entry.bounds.maxX;
    const entryMinY = entry.bounds.minY;
    const entryMaxY = entry.bounds.maxY;

    positioned.push({
      key: `${entry.module.id}/${entry.submodule.id}`,
      module: entry.module,
      submodule: entry.submodule,
      left: 92 + (entryMinX - minX) * logicalSitePx,
      top: 84 + (entryMinY - minY) * logicalSitePx,
      width: Math.max(
        SUBMODULE_MIN_WIDTH,
        (entryMaxX - entryMinX) * logicalSitePx + 80,
      ),
      height: Math.max(
        SUBMODULE_MIN_HEIGHT,
        (entryMaxY - entryMinY) * logicalSitePx + 72,
      ),
      anchorLeft: 92 + (entryMinX - minX) * logicalSitePx,
      anchorTop: 84 + (entryMinY - minY) * logicalSitePx,
    });
  }

  positioned.splice(0, positioned.length, ...resolveChipLabelCollisions(positioned));

  const moduleBottom = new Map<string, number>();
  const moduleLeft = new Map<string, number>();
  for (const box of positioned) {
    moduleBottom.set(
      box.module.id,
      Math.max(moduleBottom.get(box.module.id) ?? 0, box.top + box.height),
    );
    moduleLeft.set(
      box.module.id,
      Math.min(moduleLeft.get(box.module.id) ?? Number.POSITIVE_INFINITY, box.left),
    );
  }

  const fallbackStartX = 92 + (maxX - minX) * logicalSitePx + SUBMODULE_MIN_WIDTH + 28;
  const moduleFallbackIndex = new Map<string, number>();
  unpositioned.forEach((entry, index) => {
    const withinModule = moduleFallbackIndex.get(entry.module.id) ?? 0;
    moduleFallbackIndex.set(entry.module.id, withinModule + 1);
    const siblingLeft = moduleLeft.get(entry.module.id);
    const siblingBottom = moduleBottom.get(entry.module.id);

    const left = siblingLeft == null
      ? fallbackStartX + (index % 3) * (SUBMODULE_MIN_WIDTH + 28)
      : siblingLeft + withinModule * (SUBMODULE_MIN_WIDTH + 24);
    const top = siblingBottom == null
      ? 84 + Math.floor(index / 3) * (SUBMODULE_MIN_HEIGHT + 48)
      : siblingBottom + 54;

    positioned.push({
      key: `${entry.module.id}/${entry.submodule.id}`,
      module: entry.module,
      submodule: entry.submodule,
      left,
      top,
      width: SUBMODULE_MIN_WIDTH,
      height: SUBMODULE_MIN_HEIGHT,
      anchorLeft: null,
      anchorTop: null,
    });
  });

  positioned.splice(0, positioned.length, ...resolveChipLabelCollisions(positioned));

  const modules = node.modules.flatMap((module) => {
    const children = positioned.filter((box) => box.module.id === module.id);
    if (!children.length) return [];
    const left = Math.min(...children.map((box) => box.left)) - 24;
    const top = Math.min(...children.map((box) => box.top)) - 34;
    const right = Math.max(...children.map((box) => box.left + box.width)) + 24;
    const bottom = Math.max(...children.map((box) => box.top + box.height)) + 24;
    return [{ module, left, top, width: right - left, height: bottom - top }];
  });

  const right = positioned.length
    ? Math.max(...positioned.map((box) => box.left + box.width)) + 96
    : 720;
  const bottom = positioned.length
    ? Math.max(...positioned.map((box) => box.top + box.height)) + 92
    : 420;

  return {
    width: Math.max(720, right),
    height: Math.max(420, bottom),
    logicalSitePx: globalBounds ? logicalSitePx : null,
    submodules: positioned,
    modules,
  };
}

function CoordinateAnchorGuides({ layout }: { layout: NodePlaneLayout }) {
  const displaced = layout.submodules.filter(
    (box) =>
      box.anchorLeft !== null &&
      box.anchorTop !== null &&
      (Math.abs(box.left - box.anchorLeft) > 1 ||
        Math.abs(box.top - box.anchorTop) > 1),
  );
  if (!displaced.length) return null;

  return (
    <svg
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 z-[5]"
      width={layout.width}
      height={layout.height}
      viewBox={`0 0 ${layout.width} ${layout.height}`}
    >
      {displaced.map((box) => (
        <g key={`anchor:${box.key}`}>
          <line
            x1={box.anchorLeft!}
            y1={box.anchorTop!}
            x2={box.left + 8}
            y2={box.top + 8}
            stroke="rgba(250,204,21,.55)"
            strokeWidth="1.5"
            strokeDasharray="3 4"
          />
          <circle
            cx={box.anchorLeft!}
            cy={box.anchorTop!}
            r="4"
            fill="rgba(250,204,21,.9)"
            stroke="rgba(0,0,0,.8)"
            strokeWidth="2"
          />
        </g>
      ))}
    </svg>
  );
}

function formatQecParameter(value: unknown): string {
  if (value === null) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function qecDescription(qec: NonNullable<ArchitectureSubmoduleViewModel["qec"]>) {
  const parameters = Object.entries(qec.parameters);
  return (
    <div className="space-y-2">
      <div>
        <p className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">QEC binding</p>
        <p className="font-data text-sm font-medium text-foreground">{qec.label ?? qec.code}</p>
      </div>
      {parameters.length > 0 ? (
        <dl className="grid grid-cols-[auto_auto] gap-x-4 gap-y-1 border-t border-border/70 pt-2 text-xs">
          {parameters.map(([key, value]) => (
            <div className="contents" key={key}>
              <dt className="text-muted-foreground">{humanize(key)}</dt>
              <dd className="max-w-36 break-words text-right font-data text-foreground">{formatQecParameter(value)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="text-xs text-muted-foreground">No code parameters reported.</p>
      )}
    </div>
  );
}

function NodeHardwareMotif({
  kind,
}: {
  kind: ReturnType<typeof modalityPresentation>["kind"];
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "arch-technology-motif",
        kind === "neutral_atom" && "arch-technology-motif--atom",
        kind === "superconducting" && "arch-technology-motif--sc",
        kind === "generic" && "arch-technology-motif--generic",
      )}
    >
      <span className="arch-technology-motif__reticle" />
      <span className="arch-technology-motif__axis arch-technology-motif__axis--x" />
      <span className="arch-technology-motif__axis arch-technology-motif__axis--y" />
      {kind === "neutral_atom" ? (
        <>
          <span className="arch-atom-orbit arch-atom-orbit--one" />
          <span className="arch-atom-orbit arch-atom-orbit--two" />
          <span className="arch-atom-orbit arch-atom-orbit--three" />
          <span className="arch-atom-nucleus">
            <Atom className="h-7 w-7" />
          </span>
          <span className="arch-atom-site arch-atom-site--one" />
          <span className="arch-atom-site arch-atom-site--two" />
          <span className="arch-atom-site arch-atom-site--three" />
          <span className="arch-atom-site arch-atom-site--four" />
        </>
      ) : kind === "superconducting" ? (
        <>
          <span className="arch-sc-loop arch-sc-loop--outer" />
          <span className="arch-sc-loop arch-sc-loop--inner" />
          <span className="arch-sc-junction arch-sc-junction--left" />
          <span className="arch-sc-junction arch-sc-junction--right" />
          <span className="arch-sc-core">
            <Zap className="h-6 w-6" />
          </span>
        </>
      ) : (
        <span className="arch-generic-core">
          <CircuitBoard className="h-8 w-8" />
        </span>
      )}
      <span className="arch-technology-motif__scan" />
    </span>
  );
}

function NodeCard({
  node,
  onOpen,
}: {
  node: ArchitectureNodeViewModel;
  onOpen: () => void;
}) {
  const modality = modalityPresentation(node.modality);

  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "arch-node-package group relative min-h-[17rem] overflow-hidden text-left",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-quantum-cyan focus-visible:ring-offset-2 focus-visible:ring-offset-background",
      )}
      aria-label={`Open node ${node.label ?? node.id}`}
    >
      <span className="arch-package-edge arch-package-edge--top" aria-hidden="true" />
      <span className="arch-package-edge arch-package-edge--bottom" aria-hidden="true" />
      <span className="arch-package-edge arch-package-edge--left" aria-hidden="true" />
      <span className="arch-package-edge arch-package-edge--right" aria-hidden="true" />
      <span className="arch-package-fastener arch-package-fastener--tl" aria-hidden="true" />
      <span className="arch-package-fastener arch-package-fastener--tr" aria-hidden="true" />
      <span className="arch-package-fastener arch-package-fastener--bl" aria-hidden="true" />
      <span className="arch-package-fastener arch-package-fastener--br" aria-hidden="true" />

      <span className="relative z-10 flex h-full flex-col px-7 py-6">
        <span className="flex items-start justify-between gap-4">
          <span>
            <span className="block font-data text-[8px] uppercase tracking-[0.34em] text-quantum-cyan/65">
              Quantum node package
            </span>
            <span className="mt-1.5 flex items-center gap-2 text-[9px] uppercase tracking-[0.18em] text-muted-foreground">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-300 shadow-[0_0_8px_rgba(110,231,183,.8)]" />
              topology ready
            </span>
          </span>
          <span className="flex items-center gap-2 font-data text-[9px] text-muted-foreground transition-colors group-hover:text-quantum-cyan">
            OPEN
            <ChevronRight className="h-4 w-4 transition-transform group-hover:translate-x-1" aria-hidden="true" />
          </span>
        </span>

        <NodeHardwareMotif kind={modality.kind} />

        <span className="mt-auto flex items-end justify-between gap-4 border-t border-white/[0.08] pt-4">
          <span className="min-w-0">
            <span className="block truncate font-data text-base font-semibold tracking-wide text-foreground">
              {node.label ?? node.id}
            </span>
            <span className="mt-1 block text-[10px] uppercase tracking-[0.19em] text-muted-foreground">
              {modality.label}
              {node.coordinateFrame?.unit ? ` · ${humanize(node.coordinateFrame.unit)}` : ""}
            </span>
          </span>
          <span className={cn("arch-modality-seal", modality.accent)}>
            {modality.kind === "neutral_atom" ? (
              <Atom className="h-4 w-4" aria-hidden="true" />
            ) : modality.kind === "superconducting" ? (
              <Zap className="h-4 w-4" aria-hidden="true" />
            ) : (
              <CircuitBoard className="h-4 w-4" aria-hidden="true" />
            )}
          </span>
        </span>
      </span>
    </button>
  );
}

function InterconnectSummary({ viewModel }: { viewModel: ArchitectureHierarchyViewModel }) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    setExpandedId(null);
  }, [viewModel.logicalArchitectureHash]);

  if (!viewModel.interconnects.length) return null;

  return (
    <section aria-label="Architecture interconnects" className="mt-7 border-t border-border/70 pt-5">
      <div className="mb-3 flex items-center gap-2 text-xs uppercase tracking-[0.18em] text-muted-foreground">
        <Network className="h-4 w-4 text-quantum-cyan" aria-hidden="true" />
        Interconnects
      </div>
      <div className="grid gap-2 lg:grid-cols-2">
        {viewModel.interconnects.map((interconnect) => (
          <div key={interconnect.id} className="rounded-lg border border-border/80 bg-muted/30 p-3 text-xs">
            <button
              type="button"
              className="flex w-full items-center justify-between gap-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-quantum-cyan"
              onClick={() => setExpandedId((current) => current === interconnect.id ? null : interconnect.id)}
              aria-expanded={expandedId === interconnect.id}
            >
              <span>
                <span className="font-data text-foreground">{interconnect.label ?? interconnect.id}</span>
                <span className="ml-3 text-quantum-cyan">{interconnect.endpoints.join(" ↔ ")}</span>
              </span>
              <ChevronRight className={cn("h-4 w-4 text-muted-foreground transition-transform", expandedId === interconnect.id && "rotate-90")} />
            </button>

            {expandedId === interconnect.id ? (
              <div className="mt-3 grid gap-3 border-t border-border/70 pt-3 sm:grid-cols-2">
                <section>
                  <p className="text-[9px] uppercase tracking-[0.16em] text-muted-foreground">Interconnect modules</p>
                  <div className="mt-2 space-y-2">
                    {interconnect.modules.map((module) => (
                      <div key={module.ref} className="rounded border border-border/70 bg-black/20 p-2.5">
                        <div className="flex items-start justify-between gap-2">
                          <span className="font-data text-foreground">{module.label}</span>
                          <span className="text-[9px] uppercase text-muted-foreground">{humanize(module.type)}</span>
                        </div>
                        <div className="mt-2 space-y-1.5 border-t border-white/10 pt-2">
                          {module.submodules.map((submodule) => (
                            <div key={submodule.ref} className="rounded bg-white/[0.025] px-2 py-1.5">
                              <div className="flex items-start justify-between gap-2">
                                <span className="font-data text-foreground">{submodule.label}</span>
                                <span className="text-[9px] uppercase text-muted-foreground">{humanize(submodule.type)}</span>
                              </div>
                              <p className="mt-1 text-[10px] text-muted-foreground">
                                {submodule.payload} · {submodule.slots.length
                                  ? `${submodule.slots.length} logical slots`
                                  : submodule.copyCount != null
                                    ? `${submodule.copyCount} copies`
                                    : "no bindable slots"}
                              </p>
                              {submodule.resourceProtocolId ? (
                                <p className="mt-1 break-all font-data text-[9px] text-quantum-amber">{submodule.resourceProtocolId}</p>
                              ) : null}
                            </div>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
                <section>
                  <p className="text-[9px] uppercase tracking-[0.16em] text-muted-foreground">Node access</p>
                  <div className="mt-2 space-y-2">
                    {interconnect.access.map((access) => (
                      <div key={`${access.nodeId}/${access.interconnectSubmoduleId}`} className="rounded border border-border/70 bg-black/20 p-2.5">
                        <p className="font-data text-foreground">{access.nodeId}</p>
                        <p className="mt-1 text-[10px] text-muted-foreground">
                          {access.localSubmoduleRefs.join(", ") || "No local access declared"}
                          <span className="mx-1 text-quantum-cyan">→</span>
                          {access.interconnectSubmoduleId}
                        </p>
                      </div>
                    ))}
                  </div>
                </section>
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}

function resolveConnectionBox(layout: NodePlaneLayout, endpoint: string) {
  const normalized = endpoint.replace(/^\//, "");
  const qualified = layout.submodules.find((box) => {
    const candidates = [box.submodule.ref, `${box.module.id}/${box.submodule.id}`];
    return candidates.some(
      (candidate) => normalized === candidate || normalized.endsWith(`/${candidate}`),
    );
  });
  if (qualified) return qualified;
  const bareMatches = layout.submodules.filter(
    (box) => normalized === box.submodule.id || normalized.endsWith(`/${box.submodule.id}`),
  );
  return bareMatches.length === 1 ? bareMatches[0] : undefined;
}

function ConnectionLines({
  connections,
  layout,
}: {
  connections: readonly ArchitectureConnectionViewModel[];
  layout: NodePlaneLayout;
}) {
  const lines = connections.flatMap((connection) => {
    const [sourceRef, targetRef] = connection.endpoints;
    if (!sourceRef || !targetRef) return [];
    const source = resolveConnectionBox(layout, sourceRef);
    const target = resolveConnectionBox(layout, targetRef);
    if (!source || !target) return [];
    return [{ connection, source, target }];
  });

  if (!lines.length) return null;

  return (
    <svg
      aria-hidden="true"
      className="pointer-events-none absolute inset-0 z-10"
      width={layout.width}
      height={layout.height}
      viewBox={`0 0 ${layout.width} ${layout.height}`}
    >
      <defs>
        <marker id="architecture-arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L0,6 L7,3 z" fill="rgba(34,211,238,.72)" />
        </marker>
        <marker id="architecture-arrow-start" markerWidth="8" markerHeight="8" refX="1" refY="3" orient="auto">
          <path d="M7,0 L7,6 L0,3 z" fill="rgba(34,211,238,.72)" />
        </marker>
      </defs>
      {lines.map(({ connection, source, target }) => (
        <line
          key={connection.id}
          x1={source.left + source.width / 2}
          y1={source.top + source.height / 2}
          x2={target.left + target.width / 2}
          y2={target.top + target.height / 2}
          stroke="rgba(34,211,238,.58)"
          strokeWidth="2"
          strokeDasharray="6 5"
          markerStart={connection.direction === "bidirectional" ? "url(#architecture-arrow-start)" : undefined}
          markerEnd="url(#architecture-arrow)"
        />
      ))}
    </svg>
  );
}

function SubmoduleChip({
  box,
  selected,
  onSelect,
}: {
  box: PlaneBox;
  selected: boolean;
  onSelect: () => void;
}) {
  const { submodule } = box;
  const capacity = Object.entries(submodule.capacity ?? {});
  const chip = (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        "arch-submodule-chip group absolute z-20 overflow-hidden p-3 text-left",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-quantum-purple focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950",
        selected && "arch-submodule-chip--selected",
      )}
      style={{ left: box.left, top: box.top, width: box.width, height: box.height }}
      aria-label={`Inspect submodule ${submodule.label ?? submodule.id}`}
    >
      <span className="arch-submodule-chip__etch" aria-hidden="true" />
      <span className="arch-die-contact-strip arch-die-contact-strip--top" aria-hidden="true" />
      <span className="arch-die-contact-strip arch-die-contact-strip--bottom" aria-hidden="true" />
      <span className="relative z-10 flex items-start justify-between gap-2">
        <span className="arch-die-core flex h-7 w-7 shrink-0 items-center justify-center text-quantum-purple">
          <Layers3 className="h-4 w-4" aria-hidden="true" />
        </span>
        {submodule.qec ? (
          <span className="inline-flex items-center gap-1 rounded-full border border-quantum-cyan/30 bg-quantum-cyan/10 px-2 py-0.5 font-data text-[9px] uppercase tracking-wider text-quantum-cyan">
            <Info className="h-3 w-3" aria-hidden="true" />
            {submodule.qec.code}
          </span>
        ) : submodule.resourceProtocolId ? (
          <span className="max-w-[8rem] truncate rounded-full border border-quantum-amber/30 bg-quantum-amber/10 px-2 py-0.5 font-data text-[9px] text-quantum-amber">
            {submodule.resourceProtocolId}
          </span>
        ) : null}
      </span>
      <span className="relative z-10 mt-3 block truncate font-data text-xs font-semibold tracking-wide text-foreground">
        {submodule.label ?? submodule.id}
      </span>
      <span className="relative z-10 mt-1 block truncate text-[9px] uppercase tracking-[0.16em] text-muted-foreground">
        {[humanize(submodule.type), submodule.payload].filter(Boolean).join(" · ") || "Submodule"}
      </span>
      <span className="absolute inset-x-3 bottom-2.5 z-10 flex justify-between border-t border-white/[0.06] pt-2 text-[9px] text-muted-foreground">
        <span>{submodule.slots.length ? `${submodule.slots.length} slots` : "No logical slots"}</span>
        {capacity[0] ? <span className="max-w-[48%] truncate">{capacity[0][1]} {humanize(capacity[0][0])}</span> : null}
      </span>
    </button>
  );

  if (!submodule.qec) return chip;

  return (
    <Tooltip>
      <TooltipTrigger asChild>{chip}</TooltipTrigger>
      <TooltipContent side="top" className="w-64 p-3">
        {qecDescription(submodule.qec)}
      </TooltipContent>
    </Tooltip>
  );
}

function SlotDetail({
  module,
  submodule,
  onClose,
}: {
  module: ArchitectureModuleViewModel;
  submodule: ArchitectureSubmoduleViewModel;
  onClose: () => void;
}) {
  const [slotPage, setSlotPage] = useState(0);

  useEffect(() => {
    setSlotPage(0);
  }, [submodule.ref, submodule.slots]);

  const slotStart = slotPage * SLOT_PAGE_SIZE;
  const shownSlots = submodule.slots.slice(slotStart, slotStart + SLOT_PAGE_SIZE);
  const slotPageCount = Math.ceil(submodule.slots.length / SLOT_PAGE_SIZE);
  const positioned = shownSlots.filter((slot) => slot.coordinate);
  const capacity = Object.entries(submodule.capacity ?? {});
  const coordinateSummary = useMemo(() => {
    let bounds: CoordinateBounds | null = null;
    let positionedCount = 0;
    for (const slot of submodule.slots) {
      if (!slot.coordinate) continue;
      positionedCount += 1;
      bounds = extendBounds(bounds, slot.coordinate);
    }
    return {
      bounds,
      allHaveCoordinates:
        submodule.slots.length > 0 && positionedCount === submodule.slots.length,
    };
  }, [submodule.slots]);
  const coordinateBounds = coordinateSummary.bounds;
  const allHaveCoordinates = coordinateSummary.allHaveCoordinates;

  return (
    <aside className="arch-inspector-panel min-w-0 overflow-hidden animate-in slide-in-from-right-4 duration-200" aria-label={`${submodule.label ?? submodule.id} details`}>
      <div className="flex items-start justify-between gap-3 border-b border-border/80 bg-gradient-to-r from-quantum-purple/12 to-transparent p-4">
        <div className="min-w-0">
          <p className="truncate text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
            {module.label ?? module.id} / {humanize(submodule.type)}
          </p>
          <h3 className="mt-1 truncate font-data text-sm font-semibold text-foreground">
            {submodule.label ?? submodule.id}
          </h3>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md border border-border bg-muted/40 px-2 py-1 text-xs text-muted-foreground transition hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          aria-label="Close submodule details"
        >
          Close
        </button>
      </div>

      <div className="max-h-[min(66vh,44rem)] overflow-y-auto p-4">
        {submodule.qec ? (
          <section className="rounded-lg border border-quantum-cyan/25 bg-quantum-cyan/5 p-3">
            {qecDescription(submodule.qec)}
          </section>
        ) : null}

        {submodule.resourceProtocolId ? (
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-3 rounded-lg border border-quantum-amber/20 bg-quantum-amber/5 p-3 text-xs">
            <dt className="text-muted-foreground">Resource protocol</dt>
            <dd className="min-w-0 break-words text-right font-data text-quantum-amber">{submodule.resourceProtocolId}</dd>
          </dl>
        ) : null}

        {capacity.length > 0 ? (
          <section className="mt-4">
            <h4 className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">Capacity</h4>
            <dl className="mt-2 grid grid-cols-2 gap-2">
              {capacity.map(([key, value]) => (
                <div key={key} className="rounded border border-border/80 bg-muted/25 px-2.5 py-2">
                  <dt className="truncate text-[10px] text-muted-foreground">{humanize(key)}</dt>
                  <dd className="mt-0.5 font-data text-sm text-foreground">{value}</dd>
                </div>
              ))}
            </dl>
          </section>
        ) : null}

        <section className="mt-5">
          <div className="flex items-center justify-between gap-2">
            <h4 className="flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
              <Grid3X3 className="h-3.5 w-3.5" aria-hidden="true" />
              Logical slots
            </h4>
            <span className="font-data text-[10px] text-muted-foreground">{submodule.slots.length}</span>
          </div>

          {!submodule.slots.length ? (
            <div className="mt-3 rounded-lg border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
              This submodule exposes no logical slots.
            </div>
          ) : allHaveCoordinates && coordinateBounds ? (
            <div className="relative mt-3 h-72 overflow-hidden rounded-lg border border-border bg-black/40 dot-grid" aria-label="Logical slot coordinate plane">
              {positioned.map((slot) => {
                const [x, y] = slot.coordinate!;
                const xRange = Math.max(1, coordinateBounds.maxX - coordinateBounds.minX);
                const yRange = Math.max(1, coordinateBounds.maxY - coordinateBounds.minY);
                const left = 10 + ((x - coordinateBounds.minX) / xRange) * 80;
                const top = 10 + ((y - coordinateBounds.minY) / yRange) * 80;
                return (
                  <Tooltip key={slot.id}>
                    <TooltipTrigger asChild>
                      <button
                        type="button"
                        className="absolute flex h-9 min-w-9 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded border border-quantum-cyan/45 bg-zinc-900 px-1.5 font-data text-[9px] text-quantum-cyan shadow-[0_4px_12px_rgba(0,0,0,.55)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-quantum-cyan"
                        style={{ left: `${left}%`, top: `${top}%` }}
                        aria-label={`Slot ${slot.id} at ${formatCoordinate(slot.coordinate!)}`}
                      >
                        {slot.id}
                      </button>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-64 text-xs">
                      <p className="font-data text-foreground">{slot.id}</p>
                      <p className="mt-1 text-muted-foreground">Coordinate {formatCoordinate(slot.coordinate!)}</p>
                      {slot.kind ? <p className="text-muted-foreground">Kind {humanize(slot.kind)}</p> : null}
                      {slot.zone ? <p className="text-muted-foreground">Zone {humanize(slot.zone)}</p> : null}
                    </TooltipContent>
                  </Tooltip>
                );
              })}
            </div>
          ) : (
            <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(5.25rem,1fr))] gap-2" aria-label="Abstract logical slots">
              {shownSlots.map((slot) => (
                <Tooltip key={slot.id}>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      className="min-w-0 rounded-md border border-border/80 bg-muted/30 px-2 py-2.5 text-left transition hover:border-quantum-cyan/45 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-quantum-cyan"
                      aria-label={`Logical slot ${slot.id}`}
                    >
                      <span className="block truncate font-data text-[10px] text-foreground">{slot.id}</span>
                      <span className="mt-1 block truncate text-[9px] text-muted-foreground">
                        {slot.coordinate ? formatCoordinate(slot.coordinate) : humanize(slot.kind ?? "abstract slot")}
                      </span>
                    </button>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-64 text-xs">
                    <p className="font-data text-foreground">{slot.id}</p>
                    <p className="mt-1 text-muted-foreground">
                      {slot.coordinate ? `Coordinate ${formatCoordinate(slot.coordinate)}` : "Abstract slot — no spatial placement"}
                    </p>
                    {slot.zone ? <p className="text-muted-foreground">Zone {humanize(slot.zone)}</p> : null}
                  </TooltipContent>
                </Tooltip>
              ))}
            </div>
          )}

          {submodule.slots.length > SLOT_PAGE_SIZE ? (
            <div className="mt-3 flex items-center justify-between gap-3">
              <button
                type="button"
                onClick={() => setSlotPage((page) => Math.max(0, page - 1))}
                disabled={slotPage === 0}
                className="rounded-md border border-border bg-muted/25 px-3 py-2 text-xs text-muted-foreground transition hover:border-primary/45 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-35"
              >
                Previous
              </button>
              <p className="text-center text-[10px] leading-relaxed text-muted-foreground">
                {slotStart + 1}–{slotStart + shownSlots.length} of {submodule.slots.length}
                <span className="block">At most {SLOT_PAGE_SIZE} slots are rendered at once.</span>
              </p>
              <button
                type="button"
                onClick={() => setSlotPage((page) => Math.min(slotPageCount - 1, page + 1))}
                disabled={slotPage >= slotPageCount - 1}
                className="rounded-md border border-border bg-muted/25 px-3 py-2 text-xs text-muted-foreground transition hover:border-primary/45 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-35"
              >
                Next
              </button>
            </div>
          ) : null}
        </section>
      </div>
    </aside>
  );
}

function resolveEndpointModule(
  node: ArchitectureNodeViewModel,
  endpoint: string,
): ArchitectureModuleViewModel | null {
  const normalized = endpoint.replace(/^\//, "");
  for (const module of node.modules) {
    const moduleCandidates = [module.id, module.ref].filter(Boolean);
    if (moduleCandidates.some((candidate) => normalized === candidate || normalized.endsWith(`/${candidate}`))) {
      return module;
    }
    for (const submodule of module.submodules) {
      const submoduleCandidates = [
        submodule.id,
        submodule.ref,
        `${module.id}/${submodule.id}`,
      ].filter(Boolean);
      if (submoduleCandidates.some((candidate) => normalized === candidate || normalized.endsWith(`/${candidate}`))) {
        return module;
      }
    }
  }
  return null;
}

function moduleForEndpointLabel(node: ArchitectureNodeViewModel, endpoint: string) {
  const module = resolveEndpointModule(node, endpoint);
  if (module) return module.label ?? module.id;
  const segments = endpoint.replace(/^\//, "").split("/");
  return humanize(segments.at(-1) ?? endpoint);
}

function ModuleCard({
  module,
  onOpen,
}: {
  module: ArchitectureModuleViewModel;
  onOpen: () => void;
}) {
  const slotCount = module.submodules.reduce((sum, submodule) => sum + submodule.slots.length, 0);

  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "arch-module-die group relative min-h-48 overflow-hidden p-5 text-left",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-quantum-purple focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950",
      )}
      aria-label={`Open module ${module.label ?? module.id}`}
    >
      <span className="arch-module-die__routing" aria-hidden="true" />
      <span className="arch-die-contact-strip arch-die-contact-strip--top" aria-hidden="true" />
      <span className="arch-die-contact-strip arch-die-contact-strip--bottom" aria-hidden="true" />
      <span className="relative z-10 flex h-full flex-col">
        <span className="flex items-start justify-between gap-3">
          <span>
            <span className="block font-data text-[8px] uppercase tracking-[0.28em] text-quantum-purple/65">module die</span>
            <span className="mt-1 block max-w-36 truncate font-data text-[9px] text-muted-foreground">{module.id}</span>
          </span>
          <ChevronRight className="h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-1 group-hover:text-quantum-purple" aria-hidden="true" />
        </span>
        <span className="arch-module-die__core mt-6 flex h-14 w-14 items-center justify-center text-quantum-purple">
            <Cpu className="h-5 w-5" aria-hidden="true" />
        </span>
        <span className="mt-4 font-data text-sm font-semibold tracking-wide text-foreground">{module.label ?? module.id}</span>
        <span className="mt-1 text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
          {humanize(module.type || "module")}
        </span>
        <span className="mt-auto border-t border-white/[0.07] pt-3 font-data text-[9px] text-muted-foreground">
          {module.submodules.length} {module.submodules.length === 1 ? "submodule" : "submodules"} · {slotCount} logical slots
        </span>
      </span>
    </button>
  );
}

function FixedBusSummary({ node }: { node: ArchitectureNodeViewModel }) {
  if (!node.connections.length) {
    return (
      <section className="arch-bus-bank mt-7 p-4 text-xs text-muted-foreground">
        No node-local bus is declared for this monolithic node.
      </section>
    );
  }

  return (
    <section aria-label="Node-local fixed buses" className="arch-bus-bank mt-8 p-4 sm:p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.18em] text-muted-foreground">
          <Network className="h-3.5 w-3.5 text-quantum-cyan" aria-hidden="true" />
          Node-local fixed buses
        </div>
        <span className="text-[9px] text-muted-foreground">Topology, not a compiler-generated route</span>
      </div>
      <div className="space-y-3">
        {node.connections.map((connection) => {
          const endpointLabels = connection.endpoints.map((endpoint) => moduleForEndpointLabel(node, endpoint));
          return (
            <div key={connection.id} className="arch-bus-channel px-3 py-2.5">
              <div className="flex flex-wrap items-center gap-3">
                <span className="min-w-24 truncate font-data text-[10px] text-foreground">
                  {endpointLabels[0] ?? "Unresolved endpoint"}
                </span>
                <span className="arch-bus-trace relative min-w-20 flex-1" aria-hidden="true">
                  <span className="arch-bus-via arch-bus-via--left" />
                  <span className="arch-bus-via arch-bus-via--right" />
                  <span className="arch-bus-trace__lane" />
                </span>
                <span className="min-w-24 truncate text-right font-data text-[10px] text-foreground">
                  {endpointLabels.at(-1) ?? "Unresolved endpoint"}
                </span>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center justify-between gap-2 text-[9px] text-muted-foreground">
                <span className="font-data">{connection.id}</span>
                <span>
                  {connection.direction === "bidirectional" ? "Bidirectional" : "Directed"}
                  {connection.payload ? ` · ${humanize(connection.payload)}` : ""}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

function NodeModuleOverview({
  node,
  onOpenModule,
}: {
  node: ArchitectureNodeViewModel;
  onOpenModule: (moduleId: string) => void;
}) {
  const modality = modalityPresentation(node.modality);

  return (
    <div className="min-h-0 flex-1 overflow-auto p-4 sm:p-6">
      <section className="arch-node-substrate relative mx-auto max-w-6xl overflow-hidden p-6 sm:p-9">
        <span className="arch-node-substrate__traces" aria-hidden="true" />
        <span className="arch-board-mount arch-board-mount--tl" aria-hidden="true" />
        <span className="arch-board-mount arch-board-mount--tr" aria-hidden="true" />
        <span className="arch-board-mount arch-board-mount--bl" aria-hidden="true" />
        <span className="arch-board-mount arch-board-mount--br" aria-hidden="true" />
        <div className="relative z-10">
          <div className="mb-6 flex flex-wrap items-start justify-between gap-4 border-b border-white/10 pb-5">
            <div>
              <p className="text-[10px] uppercase tracking-[0.2em] text-muted-foreground">Monolithic node canvas</p>
              <h2 className="mt-2 font-data text-lg font-semibold text-foreground">{node.label ?? node.id}</h2>
              <p className="mt-1 text-xs text-muted-foreground">Open a module to inspect its submodules and logical slots.</p>
            </div>
            <span className={cn("arch-modality-seal inline-flex items-center gap-2 px-3 py-2 font-data text-xs", modality.accent)}>
              {modality.kind === "neutral_atom" ? (
                <Atom className="h-4 w-4" aria-hidden="true" />
              ) : modality.kind === "superconducting" ? (
                <Zap className="h-4 w-4" aria-hidden="true" />
              ) : (
                <CircuitBoard className="h-4 w-4" aria-hidden="true" />
              )}
              {modality.label}
            </span>
          </div>

          {node.modules.length ? (
            <div className="grid grid-cols-[repeat(auto-fit,minmax(13rem,1fr))] gap-5">
              {node.modules.map((module) => (
                <ModuleCard key={module.id} module={module} onOpen={() => onOpenModule(module.id)} />
              ))}
            </div>
          ) : (
            <div className="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">
              This node contains no resolved modules.
            </div>
          )}

          <FixedBusSummary node={node} />
        </div>
      </section>
    </div>
  );
}

function ModulePlane({
  node,
  module,
}: {
  node: ArchitectureNodeViewModel;
  module: ArchitectureModuleViewModel;
}) {
  const [selectedSubmoduleKey, setSelectedSubmoduleKey] = useState<string | null>(null);
  const scopedNode = useMemo(
    () => ({ ...node, modules: [module] }),
    [module, node],
  );
  const layout = useMemo(() => buildNodePlaneLayout(scopedNode), [scopedNode]);
  const selectedBox = layout.submodules.find((box) => box.key === selectedSubmoduleKey);
  const internalConnections = useMemo(
    () => node.connections.filter((connection) =>
      connection.endpoints.length > 1 &&
      connection.endpoints.every((endpoint) => resolveEndpointModule(node, endpoint)?.id === module.id),
    ),
    [module.id, node],
  );
  const scaleUnit = node.coordinateFrame?.unit
    ? humanize(node.coordinateFrame.unit).toLowerCase()
    : "logical site";
  const viewScaleLabel = layout.logicalSitePx == null
    ? "Automatic non-spatial layout"
    : `${layout.logicalSitePx < 0.01
      ? layout.logicalSitePx.toExponential(2)
      : layout.logicalSitePx.toFixed(layout.logicalSitePx < 10 ? 1 : 0)} px / ${scaleUnit}`;
  const hasDisplacedLabels = layout.submodules.some(
    (box) =>
      box.anchorLeft !== null &&
      box.anchorTop !== null &&
      (Math.abs(box.left - box.anchorLeft) > 1 || Math.abs(box.top - box.anchorTop) > 1),
  );

  useEffect(() => {
    setSelectedSubmoduleKey(null);
  }, [module.ref, module.submodules]);

  return (
    <div className={cn("grid min-h-0 flex-1 gap-3 p-3 animate-in zoom-in-95 fade-in duration-200", selectedBox && "xl:grid-cols-[minmax(0,1fr)_22rem]") }>
        <div className="arch-module-plane-frame min-h-0 min-w-0 overflow-auto">
          <div
            className="arch-module-plane-surface relative"
            style={{ width: layout.width, height: layout.height }}
            aria-label={`Submodule plane for module ${module.label ?? module.id}`}
          >
            <div className="pointer-events-none absolute left-4 top-4 z-30 rounded-md border border-border/70 bg-zinc-950/85 px-2.5 py-1.5 text-muted-foreground backdrop-blur">
              <span className="block text-[9px] uppercase tracking-[0.16em]">Module submodule plane</span>
              <span className="mt-0.5 block font-data text-[9px] text-quantum-cyan/80">View scale · {viewScaleLabel}</span>
              {hasDisplacedLabels ? (
                <span className="mt-0.5 block font-data text-[9px] text-amber-300/80">
                  Yellow point = exact logical anchor; chip card = readable label
                </span>
              ) : null}
            </div>

            <ul className="sr-only" aria-label="Module-internal logical connections">
              {internalConnections.map((connection) => (
                <li key={connection.id}>
                  {connection.id}: {connection.endpoints.join(
                    connection.direction === "bidirectional" ? " bidirectionally connects " : " connects to ",
                  )}
                  {connection.payload ? `; payload ${connection.payload}` : ""}
                </li>
              ))}
            </ul>

            <CoordinateAnchorGuides layout={layout} />
            <ConnectionLines connections={internalConnections} layout={layout} />

            {layout.submodules.map((box) => (
              <SubmoduleChip
                key={box.key}
                box={box}
                selected={box.key === selectedSubmoduleKey}
                onSelect={() => setSelectedSubmoduleKey((current) => current === box.key ? null : box.key)}
              />
            ))}

            {!layout.submodules.length ? (
              <div className="absolute inset-0 flex items-center justify-center p-8 text-center text-sm text-muted-foreground">
                This module contains no resolved submodules.
              </div>
            ) : null}
          </div>
        </div>

        {selectedBox ? (
          <SlotDetail
            module={selectedBox.module}
            submodule={selectedBox.submodule}
            onClose={() => setSelectedSubmoduleKey(null)}
          />
        ) : null}
    </div>
  );
}

function NodePlane({
  node,
  onBack,
}: {
  node: ArchitectureNodeViewModel;
  onBack: () => void;
}) {
  const [selectedModuleId, setSelectedModuleId] = useState<string | null>(null);
  const selectedModule = node.modules.find((module) => module.id === selectedModuleId) ?? null;
  const modality = modalityPresentation(node.modality);

  useEffect(() => {
    setSelectedModuleId(null);
  }, [node.id, node.modules]);

  return (
    <div className="flex h-full min-h-0 flex-col animate-in zoom-in-95 fade-in duration-200">
      <header className="arch-hierarchy-toolbar flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={() => selectedModule ? setSelectedModuleId(null) : onBack()}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border bg-muted/30 text-muted-foreground transition hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            aria-label={selectedModule ? "Back to node modules" : "Back to architecture nodes"}
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
          </button>
          <div className="min-w-0">
            <p className="truncate font-data text-sm font-semibold text-foreground">
              {node.label ?? node.id}{selectedModule ? ` / ${selectedModule.label ?? selectedModule.id}` : ""}
            </p>
            <p className="truncate text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
              {selectedModule
                ? `${humanize(selectedModule.type || "module")} · Submodules`
                : `${modality.label} · Modules`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3 text-[10px] uppercase tracking-wider text-muted-foreground">
          {selectedModule ? (
            <>
              <span>{selectedModule.submodules.length} submodules</span>
              <span>{selectedModule.submodules.reduce((sum, submodule) => sum + submodule.slots.length, 0)} slots</span>
            </>
          ) : (
            <>
              <span>{node.modules.length} modules</span>
              <span>{node.connections.length} local buses</span>
            </>
          )}
        </div>
      </header>

      {selectedModule ? (
        <ModulePlane key={selectedModule.ref} node={node} module={selectedModule} />
      ) : (
        <NodeModuleOverview node={node} onOpenModule={setSelectedModuleId} />
      )}
    </div>
  );
}

export function ArchitectureHierarchy({
  viewModel,
  className,
  initialNodeId,
  onNodeSelectionChange,
}: ArchitectureHierarchyProps) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(() => {
    if (!initialNodeId) return null;
    return viewModel.nodes.some((node) => node.id === initialNodeId) ? initialNodeId : null;
  });
  const selectedNode = viewModel.nodes.find((node) => node.id === selectedNodeId);

  useEffect(() => {
    if (selectedNodeId && !viewModel.nodes.some((node) => node.id === selectedNodeId)) {
      setSelectedNodeId(null);
      onNodeSelectionChange?.(null);
    }
  }, [onNodeSelectionChange, selectedNodeId, viewModel.nodes]);

  const selectNode = (nodeId: string | null) => {
    setSelectedNodeId(nodeId);
    onNodeSelectionChange?.(nodeId);
  };

  return (
    <TooltipProvider delayDuration={180}>
      <section className={cn("architecture-stage h-full min-h-0 overflow-hidden bg-canvas", className)} aria-label="Architecture hierarchy">
        {selectedNode ? (
          <NodePlane
            key={`${viewModel.logicalArchitectureHash}:${selectedNode.id}`}
            node={selectedNode}
            onBack={() => selectNode(null)}
          />
        ) : viewModel.nodes.length ? (
          <div className="relative z-10 h-full overflow-auto p-5 sm:p-7">
            <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
              <div>
                <div className="flex items-center gap-2 text-[10px] uppercase tracking-[0.2em] text-quantum-cyan">
                  <Network className="h-4 w-4" aria-hidden="true" />
                  Architecture hierarchy
                </div>
                <h2 className="mt-2 text-xl font-semibold text-foreground">
                  {viewModel.profileLabel ?? (viewModel.profileId ? `Profile ${viewModel.profileId}` : "Resolved architecture")}
                </h2>
                <p className="mt-1 max-w-2xl text-xs leading-relaxed text-muted-foreground">
                  Nodes describe placement and modality. Functional roles appear only after opening a node at the module and submodule levels.
                </p>
              </div>
              <div className="flex items-center gap-2 rounded-md border border-border/80 bg-muted/25 px-3 py-2 text-xs text-muted-foreground">
                <Box className="h-4 w-4 text-quantum-purple" aria-hidden="true" />
                {viewModel.nodes.length} {viewModel.nodes.length === 1 ? "node" : "nodes"}
              </div>
            </header>

            <SystemInterconnectVisual
              nodes={viewModel.nodes}
              interconnects={viewModel.interconnects}
              gridClassName="grid-cols-[repeat(auto-fit,minmax(18rem,1fr))] gap-8"
              renderNode={(node) => (
                <NodeCard key={node.id} node={node} onOpen={() => selectNode(node.id)} />
              )}
            />

            <InterconnectSummary viewModel={viewModel} />
          </div>
        ) : (
          <div className="flex h-full items-center justify-center p-8">
            <div className="max-w-sm text-center">
              <div className="mx-auto flex h-16 w-16 items-center justify-center rounded-xl border border-dashed border-border bg-muted/25 text-muted-foreground">
                <CircuitBoard className="h-8 w-8" aria-hidden="true" />
              </div>
              <h2 className="mt-4 text-base font-medium text-foreground">No resolved architecture</h2>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                Run an evaluation to render the node, module, submodule, and logical-slot hierarchy from its report.
              </p>
            </div>
          </div>
        )}
      </section>
    </TooltipProvider>
  );
}

export type { ArchitectureHierarchyProps };
