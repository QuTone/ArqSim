import { Clock } from "lucide-react";

import type { TimeBreakdownViewModel } from "@/types/evaluationReport";

interface TimeBreakdownProps {
  viewModel?: TimeBreakdownViewModel | null;
}

const segmentColors = [
  { bar: "bg-blue-500", dot: "bg-blue-500" },
  { bar: "bg-violet-500", dot: "bg-violet-500" },
  { bar: "bg-cyan-500", dot: "bg-cyan-500" },
  { bar: "bg-orange-400", dot: "bg-orange-400" },
  { bar: "bg-[#ff8b8b]", dot: "bg-[#ff8b8b]" },
  { bar: "bg-emerald-500", dot: "bg-emerald-500" },
] as const;

function formatSeconds(seconds: number): string {
  if (!Number.isFinite(seconds)) return "—";
  if (seconds === 0) return "0 s";
  if (Math.abs(seconds) < 1e-3) return `${seconds.toExponential(3)} s`;
  if (Math.abs(seconds) < 1) {
    return `${seconds.toFixed(6).replace(/0+$/, "")} s`;
  }
  return `${seconds.toFixed(3).replace(/\.0+$/, "")} s`;
}

export function TimeBreakdown({ viewModel }: TimeBreakdownProps) {
  if (!viewModel) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="space-y-2 text-center">
          <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-muted/40">
            <Clock className="h-5 w-5 text-muted-foreground" />
          </div>
          <p className="font-mono text-sm text-muted-foreground">
            Run an evaluation to see exclusive time.
          </p>
        </div>
      </div>
    );
  }

  const visibleSegments = viewModel.segments.filter(
    (segment) => segment.seconds > 0,
  );

  return (
    <div className="flex h-full flex-col gap-4 p-4">
      <div className="flex items-baseline gap-3">
        <span className="font-mono text-3xl font-bold text-foreground">
          {formatSeconds(viewModel.totalSeconds)}
        </span>
        <span className="font-mono text-sm text-muted-foreground">
          total execution time
        </span>
      </div>

      {visibleSegments.length === 0 ? (
        <div className="flex flex-1 items-center justify-center rounded-md border border-dashed border-white/10">
          <p className="font-mono text-xs text-muted-foreground">
            {viewModel.unavailableReason ??
              "The report contains no positive exclusive-time segments."}
          </p>
        </div>
      ) : (
        <>
          <div className="flex h-8 overflow-hidden rounded-md border border-white/10 bg-white/[0.02]">
            {visibleSegments.map((segment, index) => {
              const color = segmentColors[index % segmentColors.length];
              return (
                <div
                  key={segment.key}
                  className={`${color.bar} min-w-px transition-all`}
                  style={{ width: `${Math.max(0, segment.fraction) * 100}%` }}
                  title={`${segment.label}: ${formatSeconds(segment.seconds)} (${(
                    segment.fraction * 100
                  ).toFixed(1)}%)`}
                />
              );
            })}
          </div>

          <div className="flex flex-col gap-1.5 overflow-y-auto">
            {visibleSegments.map((segment, index) => {
              const color = segmentColors[index % segmentColors.length];
              const percent = segment.fraction * 100;
              return (
                <div key={segment.key} className="flex items-center gap-3">
                  <div
                    className={`h-2.5 w-2.5 shrink-0 rounded-sm ${color.dot}`}
                  />
                  <span className="w-48 shrink-0 font-mono text-xs text-muted-foreground">
                    {segment.label}
                  </span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-sm bg-white/5">
                    <div
                      className={`h-full ${color.bar} opacity-75`}
                      style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
                    />
                  </div>
                  <span className="w-28 shrink-0 text-right font-mono text-xs text-foreground">
                    {formatSeconds(segment.seconds)}
                  </span>
                  <span className="w-14 shrink-0 text-right font-mono text-xs text-muted-foreground">
                    {percent.toFixed(1)}%
                  </span>
                </div>
              );
            })}
          </div>

          <p className="mt-auto font-mono text-[10px] text-muted-foreground/60">
            Segments are reported by analysis.exclusive_time_s; the chart does
            not reconstruct latency from trace events.
          </p>
        </>
      )}
    </div>
  );
}
