import { useState } from "react";
import {
  ArrowLeft,
  BarChart3,
  Calculator,
  Clock,
  ListTree,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ContextControlBar } from "@/components/resources/ContextControlBar";
import { ResourceEstimatesPanel } from "@/components/resources/ResourceEstimatesPanel";
import { CircuitStatistics } from "@/components/statistics/CircuitStatistics";
import { TimelineTrace } from "@/components/timeline/TimelineTrace";
import { ProgramExecutionPanel } from "@/components/program/ProgramExecutionPanel";
import type {
  EvaluationReportModel,
  EvaluationViewModels,
} from "@/types/evaluationReport";

type ResultsTab = "timeline" | "program" | "resources" | "statistics";

interface EvaluationResultsPageProps {
  viewModels: EvaluationViewModels;
  allReports: Record<string, EvaluationReportModel>;
  onBack: () => void;
  onTimelineLayerLimitChange: (limit: number) => void;
  activeProgramId: string;
  onProgramChange: (id: string) => void;
  activeConfigId: string;
  onConfigChange: (id: string) => void;
  onCompareSelect: (programId: string, configId: string) => void;
  programs: Array<{ id: string; label: string }>;
  configs: Array<{ id: string; label: string; type?: string }>;
}

function compactDuration(seconds: number): string {
  if (seconds < 1e-6) return `${(seconds * 1e9).toFixed(1)} ns`;
  if (seconds < 1e-3) return `${(seconds * 1e6).toFixed(1)} µs`;
  if (seconds < 1) return `${(seconds * 1e3).toFixed(2)} ms`;
  return `${seconds.toFixed(3)} s`;
}

function compactProbability(value: number | null): string {
  if (value === null) return "Not evaluated";
  if (value >= 0.9999) return `${(value * 100).toFixed(4)}%`;
  return `${(value * 100).toFixed(2)}%`;
}

const tabs: Array<{
  id: ResultsTab;
  label: string;
  icon: typeof Clock;
}> = [
  { id: "timeline", label: "Timeline Trace", icon: Clock },
  { id: "program", label: "Program", icon: ListTree },
  { id: "resources", label: "Resource Estimates", icon: Calculator },
  { id: "statistics", label: "Circuit Statistics", icon: BarChart3 },
];

export function EvaluationResultsPage({
  viewModels,
  allReports,
  onBack,
  onTimelineLayerLimitChange,
  activeProgramId,
  onProgramChange,
  activeConfigId,
  onConfigChange,
  onCompareSelect,
  programs,
  configs,
}: EvaluationResultsPageProps) {
  const [activeTab, setActiveTab] = useState<ResultsTab>("timeline");
  const headline = viewModels.headline;

  return (
    <div className="flex h-screen min-h-0 w-full flex-col overflow-hidden bg-background">
      <header className="flex h-[60px] shrink-0 items-center justify-between border-b border-border bg-secondary/60 px-4 backdrop-blur-sm">
        <div className="flex min-w-0 items-center gap-3">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onBack}
            className="gap-2"
          >
            <ArrowLeft className="h-4 w-4" />
            Experiment setup
          </Button>
          <div className="h-6 w-px bg-border" />
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-foreground">
              Evaluation result
            </div>
            <div className="truncate font-mono text-[10px] text-muted-foreground">
              Profile {headline.profileId} · causal runtime report
            </div>
          </div>
        </div>

        <div className="flex items-center gap-5">
          <div className="text-right">
            <div className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
              Latency
            </div>
            <div className="font-mono text-sm font-semibold text-cyan-300">
              {compactDuration(headline.totalLatencySeconds)}
            </div>
          </div>
          <div className="text-right">
            <div className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
              Fidelity
            </div>
            <div className="font-mono text-sm font-semibold text-emerald-300">
              {compactProbability(headline.successProbability)}
            </div>
          </div>
          <div className="text-right">
            <div className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
              Physical qubits
            </div>
            <div className="font-mono text-sm font-semibold text-violet-300">
              {headline.totalPhysicalQubits.toLocaleString()}
            </div>
          </div>
        </div>
      </header>

      <ContextControlBar
        selectedProgram={activeProgramId}
        onProgramChange={onProgramChange}
        selectedConfig={activeConfigId}
        onConfigChange={onConfigChange}
        programs={programs}
        configs={configs}
      />

      <Tabs
        value={activeTab}
        onValueChange={(value) => setActiveTab(value as ResultsTab)}
        className="flex min-h-0 flex-1 flex-col"
      >
        <div className="shrink-0 border-b border-border bg-secondary/25 px-4">
          <TabsList className="h-11 gap-1 bg-transparent p-0">
            {tabs.map((tab) => {
              const Icon = tab.icon;
              return (
                <TabsTrigger
                  key={tab.id}
                  value={tab.id}
                  className="h-11 gap-2 rounded-none border-b-2 border-transparent bg-transparent px-4 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent"
                >
                  <Icon className="h-3.5 w-3.5" />
                  {tab.label}
                </TabsTrigger>
              );
            })}
          </TabsList>
        </div>

        <div className="min-h-0 flex-1 p-4">
          <TabsContent value="timeline" className="m-0 h-full">
            <TimelineTrace
              viewModel={viewModels.timeline}
              onProgramLayerLimitChange={onTimelineLayerLimitChange}
            />
          </TabsContent>
          <TabsContent value="program" className="m-0 h-full">
            <ProgramExecutionPanel viewModel={viewModels.programExecution} />
          </TabsContent>
          <TabsContent value="resources" className="m-0 h-full">
            <ResourceEstimatesPanel
              viewModels={viewModels}
              allResults={allReports}
              onCompareSelect={onCompareSelect}
              activeProgramId={activeProgramId}
              onProgramChange={onProgramChange}
              activeConfigId={activeConfigId}
              onConfigChange={onConfigChange}
              programs={programs}
              configs={configs}
              showContextControls={false}
            />
          </TabsContent>
          <TabsContent value="statistics" className="m-0 h-full">
            <CircuitStatistics viewModel={viewModels.circuitStatistics} />
          </TabsContent>
        </div>
      </Tabs>
    </div>
  );
}
