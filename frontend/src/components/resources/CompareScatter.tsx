import { useMemo, useState } from "react";
import { ArrowDownLeft } from "lucide-react";
import {
  CartesianGrid,
  Cell,
  Label as RechartsLabel,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { EvaluationReportModel } from "@/types/evaluationReport";

interface CompareScatterProps {
  allResults: Record<string, EvaluationReportModel>;
  programs: Array<{ id: string; label: string }>;
  configs: Array<{ id: string; label: string; type?: string }>;
  selectedProgramId: string;
  selectedConfigId: string;
  onSelect: (programId: string, configId: string) => void;
}

interface ComparePoint {
  comboId: string;
  programId: string;
  configId: string;
  programLabel: string;
  configLabel: string;
  profileId: string;
  profileLabel: string;
  qubits: number;
  timeSeconds: number;
}

const PROGRAM_COLORS = [
  "#06b6d4",
  "#f59e0b",
  "#10b981",
  "#8b5cf6",
  "#ef4444",
  "#ec4899",
  "#f97316",
  "#84cc16",
] as const;

const PROFILE_SHAPES = [
  "circle",
  "square",
  "diamond",
  "triangle",
  "cross",
  "star",
] as const;

function formatQubits(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(0)}k`;
  return value.toLocaleString();
}

function formatTime(seconds: number): string {
  if (seconds >= 3_600) return `${(seconds / 3_600).toFixed(1)}h`;
  if (seconds >= 1) return `${seconds.toFixed(2)}s`;
  if (seconds >= 1e-3) return `${(seconds * 1e3).toFixed(2)}ms`;
  return `${(seconds * 1e6).toFixed(1)}µs`;
}

function axisDomain(values: number[]): [number, number] {
  if (values.length === 0) return [0, 1];
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = maximum - minimum;
  const padding = span > 0 ? span * 0.2 : Math.max(maximum * 0.2, 1e-9);
  return [Math.max(0, minimum - padding), maximum + padding];
}

function ProfileGlyph({ shape }: { shape: (typeof PROFILE_SHAPES)[number] }) {
  if (shape === "circle") return <span className="h-2.5 w-2.5 rounded-full bg-muted-foreground" />;
  if (shape === "square") return <span className="h-2.5 w-2.5 rounded-[1px] bg-muted-foreground" />;
  if (shape === "diamond") return <span className="h-2.5 w-2.5 rotate-45 rounded-[1px] bg-muted-foreground" />;
  if (shape === "triangle") {
    return (
      <span
        className="h-0 w-0 border-x-[6px] border-b-[10px] border-x-transparent border-b-muted-foreground"
        aria-hidden="true"
      />
    );
  }
  return (
    <span className="w-3 text-center text-xs leading-none text-muted-foreground" aria-hidden="true">
      {shape === "star" ? "★" : "+"}
    </span>
  );
}

function CompareTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{ payload?: ComparePoint }>;
}) {
  const point = payload?.[0]?.payload;
  if (!active || !point) return null;
  return (
    <div className="rounded-lg border border-border bg-popover/95 px-3 py-2 font-mono text-xs shadow-xl backdrop-blur-sm">
      <div className="mb-1.5 border-b border-border pb-1 font-semibold text-foreground">
        {point.programLabel}
        <span className="mx-1 text-muted-foreground">×</span>
        <span className="text-muted-foreground">{point.profileLabel}</span>
      </div>
      <div className="space-y-0.5">
        <div className="flex justify-between gap-4">
          <span className="text-muted-foreground">Space</span>
          <span>{formatQubits(point.qubits)} qubits</span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-muted-foreground">Time</span>
          <span>{formatTime(point.timeSeconds)}</span>
        </div>
        <div className="flex justify-between gap-4">
          <span className="text-muted-foreground">Profile</span>
          <span>{point.profileId}</span>
        </div>
      </div>
    </div>
  );
}

export function CompareScatter({
  allResults,
  programs,
  configs,
  selectedProgramId,
  selectedConfigId,
  onSelect,
}: CompareScatterProps) {
  const [hiddenPrograms, setHiddenPrograms] = useState<Set<string>>(() => new Set());

  const allPoints = useMemo(
    () =>
      Object.entries(allResults).map(([comboId, report]) => {
        const separatorIndex = comboId.indexOf("|");
        const keyProgramId = separatorIndex >= 0 ? comboId.slice(0, separatorIndex) : "";
        const keyConfigId = separatorIndex >= 0 ? comboId.slice(separatorIndex + 1) : "";
        const workflowId = report.workflow_id;
        const profileId = report.profile_id;
        const programId = keyProgramId || workflowId;
        const configId = keyConfigId || profileId;
        return {
          comboId,
          programId,
          configId,
          programLabel:
            programs.find((program) => program.id === programId)?.label ?? workflowId,
          configLabel:
            configs.find((config) => config.id === configId)?.label ??
            report.profile_label,
          profileId,
          profileLabel: report.profile_label,
          qubits: report.summary.total_physical_qubits,
          timeSeconds: report.summary.total_latency_s,
        } satisfies ComparePoint;
      }),
    [allResults, configs, programs],
  );

  const presentProgramIds = useMemo(
    () => [...new Set(allPoints.map((point) => point.programId))],
    [allPoints],
  );
  const programColors = useMemo(
    () =>
      Object.fromEntries(
        presentProgramIds.map((id, index) => [
          id,
          PROGRAM_COLORS[index % PROGRAM_COLORS.length],
        ]),
      ),
    [presentProgramIds],
  );

  const profileMetadata = useMemo(() => {
    const profiles = new Map<string, { label: string; shape: (typeof PROFILE_SHAPES)[number] }>();
    for (const point of allPoints) {
      if (!profiles.has(point.profileId)) {
        profiles.set(point.profileId, {
          label: point.profileLabel,
          shape: PROFILE_SHAPES[profiles.size % PROFILE_SHAPES.length],
        });
      }
    }
    return profiles;
  }, [allPoints]);

  const visiblePoints = allPoints.filter((point) => !hiddenPrograms.has(point.programId));
  const groupedByProfile = useMemo(() => {
    const groups = new Map<string, ComparePoint[]>();
    for (const point of visiblePoints) {
      const group = groups.get(point.profileId) ?? [];
      group.push(point);
      groups.set(point.profileId, group);
    }
    return [...groups.entries()];
  }, [visiblePoints]);

  const selectedPoint = visiblePoints.find(
    (point) =>
      point.programId === selectedProgramId && point.configId === selectedConfigId,
  );
  const xDomain = axisDomain(
    (visiblePoints.length > 0 ? visiblePoints : allPoints).map((point) => point.qubits),
  );
  const yDomain = axisDomain(
    (visiblePoints.length > 0 ? visiblePoints : allPoints).map(
      (point) => point.timeSeconds,
    ),
  );

  const toggleProgram = (programId: string) => {
    setHiddenPrograms((current) => {
      const next = new Set(current);
      if (next.has(programId)) next.delete(programId);
      else next.add(programId);
      return next;
    });
  };

  if (allPoints.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center">
        <p className="font-mono text-sm text-muted-foreground">
          Run evaluations for multiple workloads or architecture profiles to compare them.
        </p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-1.5 px-3 pb-1 pt-2">
      <div className="flex shrink-0 flex-wrap gap-1.5">
        <span className="self-center font-mono text-[10px] text-muted-foreground">Programs:</span>
        {presentProgramIds.map((programId) => {
          const point = allPoints.find((candidate) => candidate.programId === programId);
          const color = programColors[programId];
          const visible = !hiddenPrograms.has(programId);
          return (
            <button
              key={programId}
              onClick={() => toggleProgram(programId)}
              className={`flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[10px] transition-all ${
                visible ? "border-transparent" : "border-border text-muted-foreground opacity-40"
              }`}
              style={visible ? { backgroundColor: `${color}25`, borderColor: `${color}80`, color } : {}}
            >
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: visible ? color : "#6b7280" }} />
              {point?.programLabel ?? programId}
            </button>
          );
        })}
      </div>

      <div className="flex shrink-0 flex-wrap gap-2">
        <span className="self-center font-mono text-[10px] text-muted-foreground">Profiles:</span>
        {[...profileMetadata.entries()].map(([profileId, metadata]) => (
          <span key={profileId} className="flex items-center gap-1.5 font-mono text-[10px] text-muted-foreground">
            <ProfileGlyph shape={metadata.shape} />
            {profileId} {metadata.label}
          </span>
        ))}
      </div>

      <div className="relative min-h-0 flex-1">
        <div className="pointer-events-none absolute bottom-10 left-20 z-10 flex items-center gap-1 text-muted-foreground/15">
          <ArrowDownLeft className="h-4 w-4" strokeWidth={1.5} />
          <span className="font-mono text-[9px] italic leading-tight">Less space<br />and faster</span>
        </div>

        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 8, right: 20, bottom: 42, left: 60 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" opacity={0.3} />
            <XAxis
              type="number"
              dataKey="qubits"
              domain={xDomain}
              tickFormatter={formatQubits}
              tick={{ fontSize: 9, fontFamily: "monospace", fill: "hsl(var(--muted-foreground))" }}
              axisLine={{ stroke: "hsl(var(--border))" }}
              tickLine={{ stroke: "hsl(var(--border))" }}
            >
              <RechartsLabel
                value="Physical qubits"
                position="bottom"
                offset={28}
                style={{ fontSize: 10, fontFamily: "monospace", fill: "hsl(var(--muted-foreground))" }}
              />
            </XAxis>
            <YAxis
              type="number"
              dataKey="timeSeconds"
              domain={yDomain}
              tickFormatter={formatTime}
              tick={{ fontSize: 9, fontFamily: "monospace", fill: "hsl(var(--muted-foreground))" }}
              axisLine={{ stroke: "hsl(var(--border))" }}
              tickLine={{ stroke: "hsl(var(--border))" }}
            >
              <RechartsLabel
                value="Total latency"
                angle={-90}
                position="left"
                offset={48}
                style={{ fontSize: 10, fontFamily: "monospace", fill: "hsl(var(--muted-foreground))" }}
              />
            </YAxis>
            <Tooltip content={<CompareTooltip />} cursor={{ strokeDasharray: "3 3" }} />

            {selectedPoint && (
              <>
                <ReferenceLine x={selectedPoint.qubits} stroke="hsl(var(--primary))" strokeDasharray="4 4" strokeOpacity={0.5} />
                <ReferenceLine y={selectedPoint.timeSeconds} stroke="hsl(var(--primary))" strokeDasharray="4 4" strokeOpacity={0.5} />
              </>
            )}

            {groupedByProfile.map(([profileId, points]) => (
              <Scatter
                key={profileId}
                name={profileMetadata.get(profileId)?.label ?? profileId}
                data={points}
                shape={profileMetadata.get(profileId)?.shape ?? "circle"}
              >
                {points.map((point) => {
                  const selected = point === selectedPoint;
                  const color = programColors[point.programId];
                  return (
                    <Cell
                      key={point.comboId}
                      fill={`${color}${selected ? "dd" : "77"}`}
                      stroke={color}
                      strokeWidth={selected ? 3 : 1}
                      style={{ cursor: "pointer" }}
                      onClick={() => onSelect(point.programId, point.configId)}
                    />
                  );
                })}
              </Scatter>
            ))}
          </ScatterChart>
        </ResponsiveContainer>
      </div>

      {selectedPoint && (
        <div className="flex shrink-0 items-center gap-3 rounded-md border border-primary/20 bg-primary/5 px-3 py-1 font-mono text-xs">
          <span style={{ color: programColors[selectedPoint.programId] }}>{selectedPoint.programLabel}</span>
          <span className="text-muted-foreground/40">×</span>
          <span className="text-muted-foreground">{selectedPoint.profileLabel}</span>
          <span className="text-border">|</span>
          <span className="text-muted-foreground">Space: <span className="text-foreground">{formatQubits(selectedPoint.qubits)}</span></span>
          <span className="text-muted-foreground">Time: <span className="text-foreground">{formatTime(selectedPoint.timeSeconds)}</span></span>
        </div>
      )}
    </div>
  );
}
