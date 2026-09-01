import { useState } from "react";
import { Clock, Cpu, Scale, Target } from "lucide-react";

import type {
  EvaluationReportModel,
  EvaluationViewModels,
} from "@/types/evaluationReport";

import { CompareScatter } from "./CompareScatter";
import { ContextControlBar } from "./ContextControlBar";
import { FidelitySummary } from "./FidelitySummary";
import { SpaceTreemap } from "./SpaceTreemap";
import { TimeBreakdown } from "./TimeBreakdown";

type MetricTab = "space" | "time" | "fidelity";

interface TabConfig {
  id: MetricTab;
  label: string;
  sublabel: string;
  icon: typeof Cpu;
}

const tabs: TabConfig[] = [
  { id: "space", label: "Space", sublabel: "Qubits", icon: Cpu },
  { id: "time", label: "Time", sublabel: "Duration", icon: Clock },
  { id: "fidelity", label: "Fidelity", sublabel: "Success", icon: Target },
];

interface ResourceEstimatesPanelProps {
  viewModels?: EvaluationViewModels | null;
  allResults: Record<string, EvaluationReportModel>;
  onCompareSelect: (programId: string, configId: string) => void;
  activeProgramId: string;
  onProgramChange: (id: string) => void;
  activeConfigId: string;
  onConfigChange: (id: string) => void;
  programs: Array<{ id: string; label: string }>;
  configs: Array<{ id: string; label: string; type?: string }>;
}

export function ResourceEstimatesPanel({
  viewModels,
  allResults,
  onCompareSelect,
  activeProgramId,
  onProgramChange,
  activeConfigId,
  onConfigChange,
  programs,
  configs,
}: ResourceEstimatesPanelProps) {
  const [activeTab, setActiveTab] = useState<MetricTab>("space");
  const [compareMode, setCompareMode] = useState(false);

  const comparison = (
    <CompareScatter
      allResults={allResults}
      programs={programs}
      configs={configs}
      selectedProgramId={activeProgramId}
      selectedConfigId={activeConfigId}
      onSelect={onCompareSelect}
    />
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between px-4 py-2.5">
        <div className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-black/40 p-1">
          {tabs.map((tab) => {
            const Icon = tab.icon;
            const selected = activeTab === tab.id && !compareMode;
            return (
              <button
                key={tab.id}
                onClick={() => {
                  setActiveTab(tab.id);
                  setCompareMode(false);
                }}
                className={`flex items-center gap-2 rounded-full px-4 py-1.5 font-mono text-xs transition-all duration-200 ${
                  selected
                    ? "bg-white/10 text-foreground shadow-sm"
                    : "text-muted-foreground hover:bg-white/5 hover:text-foreground"
                }`}
              >
                <Icon className="h-3.5 w-3.5" />
                <span className="font-medium">{tab.label}</span>
                <span className={`text-[10px] ${selected ? "text-muted-foreground" : "text-muted-foreground/60"}`}>
                  ({tab.sublabel})
                </span>
              </button>
            );
          })}
        </div>

        <button
          onClick={() => setCompareMode((value) => !value)}
          className={`flex items-center gap-2 rounded-full border px-3 py-1.5 font-mono text-xs transition-all ${
            compareMode
              ? "border-primary/40 bg-primary/20 text-primary"
              : "border-white/10 text-muted-foreground hover:bg-white/5 hover:text-foreground"
          }`}
        >
          <Scale className="h-3.5 w-3.5" />
          Compare space × time
        </button>
      </div>

      <ContextControlBar
        selectedProgram={activeProgramId}
        onProgramChange={onProgramChange}
        selectedConfig={activeConfigId}
        onConfigChange={onConfigChange}
        programs={programs}
        configs={configs}
      />

      <div className="min-h-0 flex-1 overflow-hidden">
        {compareMode && comparison}
        {!compareMode && activeTab === "space" && (
          <SpaceTreemap viewModel={viewModels?.spaceBreakdown} />
        )}
        {!compareMode && activeTab === "time" && (
          <TimeBreakdown viewModel={viewModels?.timeBreakdown} />
        )}
        {!compareMode && activeTab === "fidelity" && (
          <FidelitySummary viewModel={viewModels?.fidelity} />
        )}
      </div>
    </div>
  );
}
