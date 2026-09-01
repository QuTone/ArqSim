import { useState } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Clock, BarChart3, ChevronDown, ChevronUp, Calculator, ListTree } from "lucide-react";
import { Button } from "@/components/ui/button";
import { TimelineTrace } from "@/components/timeline/TimelineTrace";
import { ResourceEstimatesPanel } from "@/components/resources/ResourceEstimatesPanel";
import { CircuitStatistics } from "@/components/statistics/CircuitStatistics";
import { ProgramExecutionPanel } from "@/components/program/ProgramExecutionPanel";
import type { EvaluationReportModel, EvaluationViewModels } from "@/types/evaluationReport";

interface BottomPanelProps {
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  viewModels?: EvaluationViewModels | null;
  allReports: Record<string, EvaluationReportModel>;
  onTimelineLayerLimitChange: (limit: number) => void;
  activeProgramId: string;
  onProgramChange: (id: string) => void;
  activeConfigId: string;
  onConfigChange: (id: string) => void;
  onCompareSelect: (programId: string, configId: string) => void;
  programs: Array<{ id: string, label: string }>;
  configs: Array<{ id: string, label: string, type?: string }>;
}

export function BottomPanel({
  isCollapsed,
  onToggleCollapse,
  viewModels,
  allReports,
  onTimelineLayerLimitChange,
  activeProgramId,
  onProgramChange,
  activeConfigId,
  onConfigChange,
  onCompareSelect,
  programs,
  configs
}: BottomPanelProps) {
  const [activeTab, setActiveTab] = useState("timeline");

  return (
    <div
      className={`bg-secondary border-t border-border transition-all duration-300 flex flex-col ${
        isCollapsed ? "h-10" : "h-full"
      }`}
    >
      {/* Tab Header */}
      <div className="flex items-center justify-between px-4 h-10 border-b border-border shrink-0">
        <Tabs value={activeTab} onValueChange={setActiveTab} className="h-full">
          <TabsList className="h-full bg-transparent gap-2 p-0">
            <TabsTrigger
              value="resources"
              className="h-full rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent px-3 gap-2"
            >
              <Calculator className="w-4 h-4" />
              <span className="text-sm">Resource Estimates</span>
            </TabsTrigger>
            <TabsTrigger
              value="timeline"
              className="h-full rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent px-3 gap-2"
            >
              <Clock className="w-4 h-4" />
              <span className="text-sm">Timeline Trace</span>
            </TabsTrigger>
            <TabsTrigger
              value="program"
              className="h-full rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent px-3 gap-2"
            >
              <ListTree className="w-4 h-4" />
              <span className="text-sm">Program / Dynamic Work</span>
            </TabsTrigger>
            <TabsTrigger
              value="statistics"
              className="h-full rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent px-3 gap-2"
            >
              <BarChart3 className="w-4 h-4" />
              <span className="text-sm">Circuit Statistics</span>
            </TabsTrigger>
          </TabsList>
        </Tabs>

        <Button
          variant="ghost"
          size="icon"
          onClick={onToggleCollapse}
          className="h-6 w-6 hover:bg-muted"
        >
          {isCollapsed ? (
            <ChevronUp className="w-4 h-4" />
          ) : (
            <ChevronDown className="w-4 h-4" />
          )}
        </Button>
      </div>

      {/* Content */}
      {!isCollapsed && (
        <div className="flex-1 p-4 overflow-hidden">
          <Tabs value={activeTab} className="h-full">
            <TabsContent value="resources" className="h-full m-0">
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
              />
            </TabsContent>
            <TabsContent value="timeline" className="h-full m-0">
              <TimelineTrace
                viewModel={viewModels?.timeline}
                onProgramLayerLimitChange={onTimelineLayerLimitChange}
              />
            </TabsContent>
            <TabsContent value="program" className="h-full m-0">
              <ProgramExecutionPanel viewModel={viewModels?.programExecution} />
            </TabsContent>
            <TabsContent value="statistics" className="h-full m-0">
              <CircuitStatistics viewModel={viewModels?.circuitStatistics} />
            </TabsContent>
          </Tabs>
        </div>
      )}
    </div>
  );
}
