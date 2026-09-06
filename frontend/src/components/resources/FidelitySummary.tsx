import { CircleHelp, ShieldCheck, ShieldX } from "lucide-react";

import type { FidelitySummaryViewModel } from "@/types/evaluationReport";

interface FidelitySummaryProps {
  viewModel?: FidelitySummaryViewModel | null;
}

function words(value: string): string {
  return value.replace(/[_:/.-]+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function compactNumber(value: number): string {
  if (value === 0) return "0";
  if (Math.abs(value) < 1e-3 || Math.abs(value) >= 1e6) return value.toExponential(4);
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

export function FidelitySummary({ viewModel }: FidelitySummaryProps) {
  if (!viewModel) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="space-y-2 text-center">
          <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-muted/40">
            <CircleHelp className="h-5 w-5 text-muted-foreground" />
          </div>
          <p className="font-mono text-sm text-muted-foreground">
            Run an evaluation to see fidelity coverage.
          </p>
        </div>
      </div>
    );
  }

  const probability = viewModel.successProbability;
  const coverage = viewModel.completeCoverage;

  if (probability === null) {
    return (
      <div className="flex h-full items-center justify-center px-6">
        <div className="max-w-lg rounded-lg border border-dashed border-white/10 bg-white/[0.02] p-6 text-center">
          <ShieldX className="mx-auto mb-3 h-8 w-8 text-muted-foreground" />
          <p className="font-mono text-sm font-medium text-foreground">
            Success probability unavailable
          </p>
          <p className="mt-2 font-mono text-xs leading-relaxed text-muted-foreground">
            {viewModel.unavailableReason ?? "The evaluation report did not provide a fidelity result."}
          </p>
          <p className="mt-3 font-mono text-[10px] text-muted-foreground/60">
            No probability is inferred by the frontend.
          </p>
          <p className="mt-2 font-mono text-[10px] text-cyan-300/75">
            Acceptance demo: Advanced → Runtime semantics → Finite T injection demo.
          </p>
        </div>
      </div>
    );
  }

  const percent = probability * 100;
  const causes = Object.entries(viewModel.negativeLogSuccessByCause)
    .sort((left, right) => right[1] - left[1]);
  const logicalIdle = Object.entries(viewModel.logicalIdleCyclesByLocation)
    .sort((left, right) => right[1] - left[1]);
  const resourceIdle = Object.entries(viewModel.resourceIdleCyclesByLocation)
    .sort((left, right) => right[1] - left[1]);
  const unprofiled = [
    ...Object.entries(viewModel.unprofiledLogicalIdleSeconds).map(([key, value]) => [
      `logical · ${key}`,
      value,
    ] as const),
    ...Object.entries(viewModel.unprofiledResourceIdleSeconds).map(([key, value]) => [
      `resource · ${key}`,
      value,
    ] as const),
  ];
  return (
    <div className="h-full overflow-auto px-4 py-3">
      <div className="mx-auto grid w-full max-w-5xl gap-3 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
      <div className="rounded-lg border border-emerald-500/20 bg-emerald-500/[0.04] p-5">
        <div className="flex items-start justify-between gap-6">
          <div>
            <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
              Reported success probability
            </p>
            <p className="mt-1 font-mono text-4xl font-bold text-emerald-300">
              {percent.toFixed(percent >= 99 ? 4 : 2)}%
            </p>
          </div>
          <ShieldCheck className="h-10 w-10 text-emerald-400/70" />
        </div>

        <div className="mt-5 h-2 overflow-hidden rounded-full bg-black/30">
          <div
            className="h-full rounded-full bg-emerald-400"
            style={{ width: `${Math.min(100, Math.max(0, percent))}%` }}
          />
        </div>

        <div className="mt-4 flex items-center justify-between font-mono text-xs">
          <span className="text-muted-foreground">Fidelity model coverage</span>
          <span className={coverage === true ? "text-emerald-300" : "text-amber-300"}>
            {coverage === true
              ? "Complete"
              : coverage === false
                ? "Partial"
                : "Not reported"}
          </span>
        </div>
      </div>

      <div className="rounded-lg border border-white/10 bg-white/[0.02] p-4">
        <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          −log(success) contributions
        </p>
        {causes.length === 0 ? (
          <p className="mt-3 font-mono text-xs text-muted-foreground">No contribution breakdown reported.</p>
        ) : (
          <div className="mt-2 grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1.5 font-mono text-[10px]">
            {causes.map(([key, value]) => (
              <div key={key} className="contents">
                <span className={key.startsWith("idle_") || key.startsWith("resource_idle_") ? "text-cyan-200" : "text-foreground/75"}>
                  {words(key)}
                </span>
                <span className="text-right text-foreground">{compactNumber(value)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rounded-lg border border-cyan-500/20 bg-cyan-500/[0.03] p-4 lg:col-span-2">
        <p className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
          Location-aware idling evidence
        </p>
        <div className="mt-2 grid gap-3 md:grid-cols-2">
          <div>
            <p className="font-mono text-[10px] font-semibold text-cyan-300">Logical qubit idle cycles</p>
            {logicalIdle.length === 0 ? (
              <p className="mt-1 font-mono text-[10px] text-muted-foreground">None reported</p>
            ) : logicalIdle.map(([location, cycles]) => (
              <div key={location} className="mt-1 flex items-start justify-between gap-4 font-mono text-[9px]">
                <span className="break-all text-foreground/70">{location}</span>
                <span className="shrink-0 text-cyan-200">{compactNumber(cycles)} cycles</span>
              </div>
            ))}
          </div>
          <div>
            <p className="font-mono text-[10px] font-semibold text-violet-300">Resource-state idle cycles</p>
            {resourceIdle.length === 0 ? (
              <p className="mt-1 font-mono text-[10px] text-muted-foreground">None reported</p>
            ) : resourceIdle.map(([location, cycles]) => (
              <div key={location} className="mt-1 flex items-start justify-between gap-4 font-mono text-[9px]">
                <span className="break-all text-foreground/70">{location}</span>
                <span className="shrink-0 text-violet-200">{compactNumber(cycles)} cycles</span>
              </div>
            ))}
          </div>
        </div>
        {unprofiled.length > 0 && (
          <div className="mt-3 rounded border border-amber-400/20 bg-amber-400/[0.04] p-2 font-mono text-[9px] text-amber-200">
            Unprofiled idle exposure: {unprofiled.map(([key, value]) => `${key} ${compactNumber(value)} s`).join(" · ")}
          </div>
        )}
      </div>
      </div>
    </div>
  );
}
