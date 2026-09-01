import { useMemo, useState } from "react";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { usePointerZoom } from "@/hooks/usePointerZoom";
import { downsampleTimeSeries } from "@/lib/utils";
import type { CircuitStatisticsViewModel } from "@/types/evaluationReport";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { StatCard } from "./StatCard";

type ChartType = "activeQubits" | "operationCount" | "nonCliffordCount";

interface GBCPanelProps {
  viewModel?: CircuitStatisticsViewModel | null;
}

const chartLabels: Record<ChartType, string> = {
  activeQubits: "Active Qubits",
  operationCount: "Operations",
  nonCliffordCount: "T Gates",
};

const MAX_ZOOM = 20;

function zoomToSlider(zoom: number): number {
  return Math.round((Math.log(Math.max(zoom, 1)) / Math.log(MAX_ZOOM)) * 100);
}

function sliderToZoom(value: number): number {
  return MAX_ZOOM ** (value / 100);
}

const tooltipStyle = {
  backgroundColor: "hsl(var(--popover))",
  border: "1px solid hsl(var(--border))",
  borderRadius: "6px",
  fontSize: "11px",
  fontFamily: "monospace",
};

export function GBCPanel({ viewModel }: GBCPanelProps) {
  const [primaryChart, setPrimaryChart] = useState<ChartType>("activeQubits");
  const [overlayChart, setOverlayChart] = useState<ChartType | "none">("none");
  const chartData = useMemo(
    () =>
      downsampleTimeSeries(
        [...(viewModel?.series ?? [])],
        500,
        "activeQubits",
      ),
    [viewModel],
  );
  const totalLayers = chartData.length;
  const { scale, setScale, fitScale, chartWidth, scrollContainerRef } =
    usePointerZoom({
      totalCycles: totalLayers,
      pixelsPerCycle: 4,
    });
  const zoomFactor = fitScale > 0 ? scale / fitScale : 1;
  const effectiveOverlay =
    overlayChart !== "none" && overlayChart === primaryChart
      ? "none"
      : overlayChart;
  const tickInterval = useMemo(() => {
    const approximateTicks = chartWidth / 70;
    const raw = Math.ceil(totalLayers / Math.max(1, approximateTicks));
    return Math.max(0, raw - 1);
  }, [chartWidth, totalLayers]);

  const series = (type: ChartType, overlay = false) => {
    const color = overlay ? "hsl(340 80% 60%)" : "hsl(200 80% 55%)";
    if (type === "activeQubits") {
      return overlay ? (
        <Line
          yAxisId={overlay ? "right" : "left"}
          type="monotone"
          dataKey={type}
          stroke={color}
          strokeWidth={2}
          dot={false}
          name={chartLabels[type]}
        />
      ) : (
        <Area
          yAxisId="left"
          type="monotone"
          dataKey={type}
          fill="hsl(200 80% 50% / 0.28)"
          stroke={color}
          strokeWidth={1.5}
          name={chartLabels[type]}
        />
      );
    }
    if (type === "nonCliffordCount") {
      return (
        <Bar
          yAxisId={overlay ? "right" : "left"}
          dataKey={type}
          fill={overlay ? "hsl(340 80% 60% / 0.55)" : "hsl(270 70% 60% / 0.55)"}
          name={chartLabels[type]}
        />
      );
    }
    return (
      <Line
        yAxisId={overlay ? "right" : "left"}
        type="monotone"
        dataKey={type}
        stroke={overlay ? color : "hsl(160 70% 45%)"}
        strokeWidth={overlay ? 2 : 1.5}
        dot={false}
        name={chartLabels[type]}
      />
    );
  };

  if (!viewModel?.gbc) {
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-border/50 bg-card/30">
        <p className="font-mono text-xs text-muted-foreground">
          Gate-based statistics are not present in this workload.
        </p>
      </div>
    );
  }

  const summary = viewModel.gbc;

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border border-border/50 bg-card/30">
      <div className="flex shrink-0 items-center justify-between border-b border-border/50 bg-blue-500/10 px-3 py-1.5">
        <span className="font-mono text-xs font-medium text-blue-400">
          Gate-Based Workload
        </span>
        <span className="font-mono text-[9px] text-muted-foreground">
          {viewModel.layerCount} logical layers
        </span>
      </div>

      <div className="flex min-h-0 flex-1">
        <div className="flex w-32 shrink-0 flex-col gap-1.5 border-r border-border/30 p-2">
          <StatCard label="Total Gates" value={summary.totalGates} accent="blue" />
          <StatCard label="Depth" value={summary.depth} accent="cyan" />
          <StatCard label="T Count" value={summary.tCount} accent="violet" />
          <StatCard label="Max Width" value={summary.maxWidth} accent="emerald" />
        </div>

        <div className="flex min-w-0 flex-1 flex-col">
          <div className="shrink-0 space-y-1.5 border-b border-border/30 bg-background/30 px-3 py-1.5">
            <div className="flex flex-wrap items-center gap-3">
              <ToggleGroup
                type="single"
                value={primaryChart}
                onValueChange={(value) => value && setPrimaryChart(value as ChartType)}
                className="rounded-md border border-border/50 bg-background/50 p-0.5"
              >
                {(Object.keys(chartLabels) as ChartType[]).map((type) => (
                  <ToggleGroupItem
                    key={type}
                    value={type}
                    className="h-5 px-1.5 py-0.5 font-mono text-[9px] data-[state=on]:bg-blue-500/20 data-[state=on]:text-blue-400"
                  >
                    {chartLabels[type]}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>

              <div className="flex items-center gap-1.5">
                <span className="font-mono text-[9px] text-muted-foreground">
                  Overlay
                </span>
                <Select
                  value={effectiveOverlay}
                  onValueChange={(value) => setOverlayChart(value as ChartType | "none")}
                >
                  <SelectTrigger className="h-5 w-28 border-border/50 bg-background/50 px-1.5 font-mono text-[9px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent className="border-border bg-popover font-mono text-xs">
                    <SelectItem value="none">None</SelectItem>
                    {(Object.keys(chartLabels) as ChartType[])
                      .filter((type) => type !== primaryChart)
                      .map((type) => (
                        <SelectItem key={type} value={type}>
                          {chartLabels[type]}
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="flex items-center gap-1.5">
              <span className="font-mono text-[9px] text-muted-foreground">1×</span>
              <div className="w-36">
                <Slider
                  value={[zoomToSlider(zoomFactor)]}
                  onValueChange={([value]) =>
                    setScale(fitScale * sliderToZoom(value))
                  }
                  min={0}
                  max={100}
                  step={1}
                />
              </div>
              <span className="font-mono text-[9px] text-muted-foreground">20×</span>
            </div>
          </div>

          <div
            ref={scrollContainerRef}
            className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden bg-gray-900/50"
          >
            <div style={{ width: chartWidth, minWidth: "100%", height: "100%" }}>
              <ResponsiveContainer width="100%" height="100%">
                <ComposedChart
                  data={chartData}
                  margin={{ top: 8, right: effectiveOverlay === "none" ? 20 : 45, left: 0, bottom: 0 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.3} />
                  <XAxis
                    dataKey="layerIndex"
                    interval={tickInterval}
                    tick={{ fontSize: 9, fill: "hsl(var(--muted-foreground))" }}
                    tickLine={{ stroke: "hsl(var(--border))" }}
                    axisLine={{ stroke: "hsl(var(--border))" }}
                    label={{ value: "logical layer", position: "insideBottomRight", offset: -2, fontSize: 9 }}
                  />
                  <YAxis
                    yAxisId="left"
                    width={38}
                    allowDecimals={false}
                    tick={{ fontSize: 9, fill: "hsl(var(--muted-foreground))" }}
                  />
                  {effectiveOverlay !== "none" && (
                    <YAxis
                      yAxisId="right"
                      orientation="right"
                      width={38}
                      allowDecimals={false}
                      tick={{ fontSize: 9, fill: "hsl(340 80% 60%)" }}
                    />
                  )}
                  <Tooltip contentStyle={tooltipStyle} labelFormatter={(value) => `Layer ${value}`} />
                  {series(primaryChart)}
                  {effectiveOverlay !== "none" && series(effectiveOverlay, true)}
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
