import { BarChart3 } from "lucide-react";

import type { CircuitStatisticsViewModel } from "@/types/evaluationReport";
import { GBCPanel } from "./GBCPanel";
import { PBCPanel } from "./PBCPanel";
import { StatCard } from "./StatCard";

interface CircuitStatisticsProps {
  viewModel?: CircuitStatisticsViewModel | null;
}

function representationLabel(representation: string): string {
  return representation === "clifford_t"
    ? "Clifford + T"
    : representation.toUpperCase();
}

export function CircuitStatistics({ viewModel }: CircuitStatisticsProps) {
  if (!viewModel) {
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-border/50 bg-background/30">
        <div className="space-y-2 text-center">
          <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-muted/40">
            <BarChart3 className="h-5 w-5 text-muted-foreground" />
          </div>
          <p className="font-mono text-sm text-muted-foreground">
            Load a report to see workload statistics.
          </p>
        </div>
      </div>
    );
  }

  const operationEntries = Object.entries(viewModel.operationCounts).sort(
    ([left], [right]) => left.localeCompare(right),
  );

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border border-border/50 bg-background/30">
      <div className="shrink-0 border-b border-border/50 bg-card/30 p-3">
        <div className="mb-2 flex items-center justify-between">
          <div>
            <div className="font-mono text-xs font-semibold text-foreground">
              {representationLabel(viewModel.representation)} workload
            </div>
            <div className="font-mono text-[10px] text-muted-foreground">
              Aggregated directly from workload.evaluated; no evaluator metrics are reconstructed.
            </div>
          </div>
          <div className="font-mono text-[10px] text-muted-foreground">
            {operationEntries.map(([name, count]) => `${name}: ${count}`).join(" · ")}
          </div>
        </div>

        <div className="grid grid-cols-5 gap-2">
          <StatCard label="Logical Qubits" value={viewModel.numQubits} accent="blue" />
          <StatCard label="Classical Bits" value={viewModel.numClbits} accent="cyan" />
          <StatCard label="Layers" value={viewModel.layerCount} accent="violet" />
          <StatCard label="Operations" value={viewModel.operationCount} accent="emerald" />
          <StatCard label="Representation" value={representationLabel(viewModel.representation)} accent="amber" />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {viewModel.gbc && (
          <div className="h-[360px] min-h-[320px]">
            <GBCPanel viewModel={viewModel} />
          </div>
        )}
        {viewModel.pbc && (
          <div className="h-[360px] min-h-[320px]">
            <PBCPanel viewModel={viewModel} />
          </div>
        )}
        {!viewModel.gbc && !viewModel.pbc && (
          <div className="flex h-full items-center justify-center">
            <p className="font-mono text-xs text-muted-foreground">
              This workload representation has no specialized statistics view.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
