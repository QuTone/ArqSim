import { useEffect, useMemo, useRef, useState } from "react";
import { Boxes } from "lucide-react";

import type { SpaceBreakdownViewModel } from "@/types/evaluationReport";

interface SpaceTreemapProps {
  viewModel?: SpaceBreakdownViewModel | null;
}

interface LayoutItem {
  key: string;
  label: string;
  physicalQubits: number;
  fraction: number;
  x: number;
  y: number;
  width: number;
  height: number;
  color: string;
}

const COLORS = [
  "#10b981",
  "#3b82f6",
  "#8b5cf6",
  "#06b6d4",
  "#f59e0b",
  "#f43f5e",
  "#84cc16",
] as const;

function formatQubits(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return value.toLocaleString();
}

/**
 * A deterministic binary treemap. Rectangle area is normalized over the
 * categories that the report actually itemizes; percentages remain relative
 * to the report's authoritative total.
 */
function layoutItems(
  items: Omit<LayoutItem, "x" | "y" | "width" | "height">[],
  x: number,
  y: number,
  width: number,
  height: number,
): LayoutItem[] {
  if (items.length === 0) return [];
  if (items.length === 1) {
    return [{ ...items[0], x, y, width, height }];
  }

  const total = items.reduce((sum, item) => sum + item.physicalQubits, 0);
  let splitIndex = 1;
  let leftTotal = items[0].physicalQubits;
  while (
    splitIndex < items.length - 1 &&
    leftTotal + items[splitIndex].physicalQubits <= total / 2
  ) {
    leftTotal += items[splitIndex].physicalQubits;
    splitIndex += 1;
  }

  const ratio = total > 0 ? leftTotal / total : splitIndex / items.length;
  const left = items.slice(0, splitIndex);
  const right = items.slice(splitIndex);

  if (width >= height) {
    const leftWidth = width * ratio;
    return [
      ...layoutItems(left, x, y, leftWidth, height),
      ...layoutItems(right, x + leftWidth, y, width - leftWidth, height),
    ];
  }

  const topHeight = height * ratio;
  return [
    ...layoutItems(left, x, y, width, topHeight),
    ...layoutItems(right, x, y + topHeight, width, height - topHeight),
  ];
}

export function SpaceTreemap({ viewModel }: SpaceTreemapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 640, height: 260 });
  const [hoveredKey, setHoveredKey] = useState<string | null>(null);

  useEffect(() => {
    const element = containerRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setSize({ width, height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const positiveSegments = useMemo(
    () =>
      (viewModel?.segments ?? [])
        .filter((segment) => segment.physicalQubits > 0)
        .sort(
          (left, right) =>
            right.physicalQubits - left.physicalQubits ||
            left.key.localeCompare(right.key),
        ),
    [viewModel],
  );

  const itemizedQubits = useMemo(
    () => positiveSegments.reduce((sum, segment) => sum + segment.physicalQubits, 0),
    [positiveSegments],
  );

  const layout = useMemo(
    () =>
      layoutItems(
        positiveSegments.map((segment, index) => ({
          ...segment,
          color: COLORS[index % COLORS.length],
        })),
        0,
        0,
        size.width,
        size.height,
      ),
    [positiveSegments, size],
  );

  if (!viewModel) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="space-y-2 text-center">
          <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-muted/40">
            <Boxes className="h-5 w-5 text-muted-foreground" />
          </div>
          <p className="font-mono text-sm text-muted-foreground">
            Run an evaluation to see physical-space usage.
          </p>
        </div>
      </div>
    );
  }

  if (positiveSegments.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center">
        <p className="font-mono text-sm text-muted-foreground">
          {viewModel.unavailableReason ??
            "Component-level physical-space breakdown is unavailable in this report."}
        </p>
      </div>
    );
  }

  const hasItemizationDifference =
    Math.abs(itemizedQubits - viewModel.totalPhysicalQubits) > 1e-6;

  return (
    <div className="flex h-full gap-5 p-4">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="mb-2 flex items-end justify-between gap-4">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              Reported component breakdown
            </p>
            <div className="font-mono text-2xl font-bold text-foreground">
              {formatQubits(viewModel.totalPhysicalQubits)}
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                physical qubits total
              </span>
            </div>
          </div>
          <p className="max-w-56 text-right font-mono text-[9px] leading-relaxed text-muted-foreground/60">
            Grouped only by analysis.physical_space_qubits. No modality split is inferred.
          </p>
        </div>

        <div ref={containerRef} className="relative min-h-0 flex-1 overflow-hidden rounded-md bg-black/20">
          {layout.map((item) => {
            const padding = 2;
            const innerWidth = Math.max(0, item.width - padding * 2);
            const innerHeight = Math.max(0, item.height - padding * 2);
            const showLabel = innerWidth >= 72 && innerHeight >= 36;
            const showValue = innerWidth >= 100 && innerHeight >= 58;
            const hovered = hoveredKey === item.key;
            return (
              <div
                key={item.key}
                className="absolute overflow-hidden rounded border transition duration-150"
                style={{
                  left: item.x + padding,
                  top: item.y + padding,
                  width: innerWidth,
                  height: innerHeight,
                  backgroundColor: `${item.color}${hovered ? "35" : "20"}`,
                  borderColor: `${item.color}${hovered ? "dd" : "88"}`,
                  boxShadow: hovered ? `0 0 18px ${item.color}35` : "none",
                  transform: hovered ? "translateY(-1px)" : "none",
                }}
                onMouseEnter={() => setHoveredKey(item.key)}
                onMouseLeave={() => setHoveredKey(null)}
                title={`${item.label}: ${item.physicalQubits.toLocaleString()} physical qubits (${(
                  item.fraction * 100
                ).toFixed(2)}% of total)`}
              >
                {showLabel && (
                  <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center px-2 text-center font-mono">
                    <span className="max-w-full truncate text-[11px] font-semibold" style={{ color: item.color }}>
                      {item.label}
                    </span>
                    {showValue && (
                      <span className="mt-1 text-[10px] text-muted-foreground">
                        {formatQubits(item.physicalQubits)} · {(item.fraction * 100).toFixed(1)}%
                      </span>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {hasItemizationDifference && (
          <p className="mt-1.5 font-mono text-[9px] text-amber-300/70">
            Itemized categories sum to {formatQubits(itemizedQubits)}; rectangle areas are
            normalized over those categories while percentages use the reported total.
          </p>
        )}
      </div>

      <div className="flex w-48 shrink-0 flex-col overflow-y-auto">
        <p className="mb-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          Components
        </p>
        <div className="space-y-1">
          {positiveSegments.map((segment, index) => {
            const color = COLORS[index % COLORS.length];
            return (
              <div
                key={segment.key}
                className={`flex items-center justify-between gap-2 rounded px-2 py-1.5 transition ${
                  hoveredKey === segment.key ? "bg-white/5" : ""
                }`}
                onMouseEnter={() => setHoveredKey(segment.key)}
                onMouseLeave={() => setHoveredKey(null)}
              >
                <div className="flex min-w-0 items-center gap-2">
                  <span className="h-2 w-2 shrink-0 rounded-sm" style={{ backgroundColor: color }} />
                  <span className="truncate font-mono text-[10px] text-muted-foreground">
                    {segment.label}
                  </span>
                </div>
                <span className="shrink-0 font-mono text-[10px] text-foreground">
                  {formatQubits(segment.physicalQubits)}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
