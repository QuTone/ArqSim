import { useEffect, useState, useMemo } from "react";
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from "@/components/ui/resizable";
import { TopBar } from "@/components/layout/TopBar";
import { LeftPanel } from "@/components/layout/LeftPanel";
import { BottomPanel } from "@/components/layout/BottomPanel";
import { MainCanvas } from "@/components/layout/MainCanvas";
import { Module, ModuleLink, InteractionMode, defaultModules } from "@/types/module";
import { SelectedConfig } from "@/components/config/ArchitectureComposerSection";
import { DeviceLibraryParams } from "@/components/config/DeviceLibrarySection";
import {
  defaultExperimentSetupParams,
  type ExperimentSetupParams,
} from "@/types/experiment";
import {
  api,
  minimalEvaluationConfig,
  type Benchmark,
  type EvaluationConfigDocument,
  type WorkloadRepresentation,
} from "@/services/api";
import { architectureProfileToViewModel, reportToViewModels } from "@/services/reportAdapter";
import { finiteInjectionDemoConfig } from "@/services/evaluationPresets";
import type {
  ArchitectureHierarchyViewModel,
  EvaluationReportModel,
} from "@/types/evaluationReport";
import { toast } from "sonner";

// A canonical report can temporarily occupy hundreds of MB while its causal
// ledger is validated and hashed. Queue interactive jobs to keep the backend
// responsive instead of multiplying that peak across architecture sweeps.
const MAX_PARALLEL_EVALUATIONS = 1;

async function settleWithConcurrency<T, R>(
  items: readonly T[],
  concurrency: number,
  task: (item: T, index: number) => Promise<R>,
): Promise<PromiseSettledResult<R>[]> {
  const outcomes = new Array<PromiseSettledResult<R>>(items.length);
  let nextIndex = 0;

  const worker = async () => {
    while (nextIndex < items.length) {
      const index = nextIndex++;
      try {
        outcomes[index] = { status: "fulfilled", value: await task(items[index], index) };
      } catch (reason) {
        outcomes[index] = { status: "rejected", reason };
      }
    }
  };

  const workerCount = Math.min(items.length, Math.max(1, concurrency));
  await Promise.all(Array.from({ length: workerCount }, () => worker()));
  return outcomes;
}

const Index = () => {
  const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false);
  const [bottomPanelCollapsed, setBottomPanelCollapsed] = useState(false);
  const [is3D, setIs3D] = useState(true);
  const [modules, setModules] = useState<Module[]>(defaultModules);
  const [links, setLinks] = useState<ModuleLink[]>([]);
  const [interactionMode, setInteractionMode] = useState<InteractionMode>("drag");

  // Device / interconnect parameters (from Device Library panel)
  const [deviceParams, setDeviceParams] = useState<DeviceLibraryParams>({
    rPhybell: 1e4,
  });

  // Evaluation / QEC protocol parameters (from Experiment Setup panel)
  const [experimentParams, setExperimentParams] = useState<ExperimentSetupParams>(defaultExperimentSetupParams);

  // Selected States
  const [selectedPrograms, setSelectedPrograms] = useState<Benchmark[]>([]);
  const [activeProgramId, setActiveProgramId] = useState<string>("");
  const [selectedConfigs, setSelectedConfigs] = useState<SelectedConfig[]>([]);

  // Selected States for viewing results
  const [viewingProgramId, setViewingProgramId] = useState<string>("");
  const [viewingConfigId, setViewingConfigId] = useState<string>("");
  const [previewProfileId, setPreviewProfileId] = useState<string | null>("2.3");
  const [profilePreviews, setProfilePreviews] = useState<
    Record<string, ArchitectureHierarchyViewModel>
  >({});
  const [profilesLoading, setProfilesLoading] = useState(true);
  const [profileCatalogError, setProfileCatalogError] = useState<string | null>(null);

  // Evaluation result state
  const [evaluationResults, setEvaluationResults] = useState<Record<string, EvaluationReportModel>>({});
  const [isEvaluating, setIsEvaluating] = useState(false);
  const [timelineLayerLimit, setTimelineLayerLimit] = useState(12);

  // Cache for avoiding redundant API calls
  const [resultCache] = useState<Map<string, EvaluationReportModel>>(new Map());

  useEffect(() => {
    let cancelled = false;
    api.getArchitectureProfiles()
      .then((profiles) => {
        if (cancelled) return;
        setProfilePreviews(Object.fromEntries(
          profiles.map((profile) => [profile.id, architectureProfileToViewModel(profile)]),
        ));
      })
      .catch((error) => {
        if (!cancelled) {
          console.error("Failed to load architecture profiles:", error);
          setProfileCatalogError(error instanceof Error ? error.message : String(error));
          toast.error("Could not load the canonical architecture profiles");
        }
      })
      .finally(() => {
        if (!cancelled) setProfilesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handlePreviewProfileChange = (profileId: string | null) => {
    setPreviewProfileId(profileId);
    // A newly requested preset preview takes precedence over an older report.
    setViewingProgramId("");
    setViewingConfigId("");
  };

  const handleExperimentParamsChange = (params: ExperimentSetupParams) => {
    setExperimentParams(params);
    // A report is evidence for the parameters that produced it. Keep cached
    // results available, but stop presenting an old report as the new setup.
    setViewingProgramId("");
    setViewingConfigId("");
  };

  const handleRunEvaluation = async () => {
    if (selectedPrograms.length === 0) {
      toast.error("Please select at least one quantum program");
      return;
    }
    setTimelineLayerLimit(12);

    // Default to one public ArchitectureProfile if no preset is selected.
    const configsToRun = selectedConfigs.length > 0
      ? selectedConfigs
      : [{
          id: "default",
          profileId: "2.3",
          name: "NA-MC + SC-F (Profile 2.3)",
          modules,
          links,
          source: "preset" as const,
        }];

    setIsEvaluating(true);
    // Do not leave a prior report visible while a replacement run is pending
    // or if request construction fails before per-combination settlement.
    setViewingProgramId("");
    setViewingConfigId("");
    const results: Record<string, EvaluationReportModel> = { ...evaluationResults };

    // Build all (program, arch) combinations
    const allCombos = selectedPrograms.flatMap(program =>
      configsToRun.map(arch => ({ program, arch }))
    );

    toast.info(
      allCombos.length > 1
        ? `Queued ${allCombos.length} evaluations`
        : "Starting evaluation…",
    );

    try {
      const buildEvaluationRequest = (program: typeof selectedPrograms[0], arch: typeof configsToRun[0]) => {
        if (!arch.profileId) {
          throw new Error(
            `“${arch.name}” is an authoring-only composition. ` +
            "Live evaluation currently accepts the six canonical ArchitectureProfile presets.",
          );
        }
        const profileId = arch.profileId;
        const representation: WorkloadRepresentation =
          profileId === "1.2" || profileId === "2.2" ? "pbc" : "clifford_t";
        if (
          experimentParams.evaluationPreset === "finite_t_injection_demo_v1" &&
          representation !== "clifford_t"
        ) {
          throw new Error(
            "The finite-T injection demo requires a Clifford+T architecture profile " +
            "(1.1, 1.3, 2.1, or 2.3).",
          );
        }
        const config: EvaluationConfigDocument =
          experimentParams.evaluationPreset === "finite_t_injection_demo_v1"
            ? finiteInjectionDemoConfig(profileId, program.id)
            : minimalEvaluationConfig(profileId);
        const overrides: Record<string, unknown> = {};
        if (experimentParams.msfCopies !== 1) {
          overrides["protocols.magic_state.copies"] = experimentParams.msfCopies;
        }
        const protocolIds: Record<string, string> = {
          cultivation: "cultivation-d5-d15-p1e3",
          MSD1: "litinski-15to1x20to4-13-5-5-23-11-13-p1e3",
          MSD2: "litinski-15to1-17-7-7-p1e3",
        };
        if (experimentParams.msfProtocol !== "cultivation") {
          overrides["protocols.magic_state.id"] = protocolIds[experimentParams.msfProtocol];
        }
        if (deviceParams.rPhybell !== 1e4) {
          overrides["protocols.entanglement_distillation.reference_physical_bell_pair_rate_per_s"] =
            deviceParams.rPhybell;
        }
        if (experimentParams.naCycleTimeMs !== 1) {
          overrides["timing.qec_cycle_time_s_by_modality.neutral_atom"] =
            experimentParams.naCycleTimeMs * 1e-3;
        }
        if (experimentParams.scCycleTimeUs !== 1) {
          overrides["timing.qec_cycle_time_s_by_modality.superconducting"] =
            experimentParams.scCycleTimeUs * 1e-6;
        }
        if (Object.keys(overrides).length) config.layout_policy_overrides = overrides;
        return { benchmark_name: program.id, representation, config } as const;
      };

      // Bound concurrency: each evaluation can generate a large causal report.
      const outcomes = await settleWithConcurrency(
        allCombos,
        MAX_PARALLEL_EVALUATIONS,
        async ({ program, arch }) => {
          const comboId = `${program.id}|${arch.id}`;
          const request = buildEvaluationRequest(program, arch);
          const cacheKey = JSON.stringify(request);
          if (resultCache.has(cacheKey)) {
            return { comboId, result: resultCache.get(cacheKey)! };
          }
          const result = await api.runEvaluation(request);
          resultCache.set(cacheKey, result);
          return { comboId, result };
        },
      );

      let successCount = 0;
      const failedCombos: string[] = [];
      let firstSuccessfulCombo: { programId: string; configId: string } | null = null;
      for (let i = 0; i < outcomes.length; i++) {
        const outcome = outcomes[i];
        const { program, arch } = allCombos[i];
        if (outcome.status === "fulfilled") {
          results[outcome.value.comboId] = outcome.value.result;
          firstSuccessfulCombo ??= { programId: program.id, configId: arch.id };
          successCount++;
        } else {
          const comboId = `${program.id}|${arch.id}`;
          // A failed rerun invalidates an older result under the same UI key.
          // Otherwise it would look as though the new parameter set succeeded.
          delete results[comboId];
          failedCombos.push(comboId);
          console.error(`Evaluation failed for ${comboId}:`, outcome.reason);
        }
      }

      setEvaluationResults(results);

      if (firstSuccessfulCombo) {
        setViewingProgramId(firstSuccessfulCombo.programId);
        setViewingConfigId(firstSuccessfulCombo.configId);
      } else {
        setViewingProgramId("");
        setViewingConfigId("");
      }

      setBottomPanelCollapsed(false);

      if (failedCombos.length > 0) {
        toast.error(`${failedCombos.length} evaluation(s) failed: ${failedCombos.join(", ")}`);
      }
      if (successCount > 0) {
        toast.success(`${successCount}/${allCombos.length} evaluation(s) completed`);
      }
    } catch (error) {
      console.error(error);
      toast.error(`Evaluation failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setIsEvaluating(false);
    }
  };

  const activeResult = useMemo(() => {
    if (!viewingProgramId || !viewingConfigId) return null;
    return evaluationResults[`${viewingProgramId}|${viewingConfigId}`] || null;
  }, [viewingProgramId, viewingConfigId, evaluationResults]);

  const activeViewModels = useMemo(
    () => activeResult
      ? reportToViewModels(activeResult, { maxProgramLayers: timelineLayerLimit })
      : null,
    [activeResult, timelineLayerLimit],
  );

  const previewArchitecture = previewProfileId
    ? profilePreviews[previewProfileId] ?? null
    : null;

  // When Compare Mode selects a point, update both the viewing state AND the canvas architecture
  const handleCompareSelect = (programId: string, configId: string) => {
    setViewingProgramId(programId);
    setViewingConfigId(configId);
    // Find the architecture config and update canvas to show its modules
    const allConfigs = selectedConfigs.length > 0
      ? selectedConfigs
      : [{
          id: "default",
          profileId: "2.3",
          name: "NA-MC + SC-F (Profile 2.3)",
          modules,
          links,
          source: "preset" as const,
        }];
    const config = allConfigs.find(c => c.id === configId);
    if (config && config.modules.length > 0) {
      setModules(config.modules);
      setLinks(config.links);
    }
  };

  const programOptions = useMemo(() =>
    selectedPrograms.map(p => ({ id: p.id, label: p.name })),
  [selectedPrograms]);

  const configOptions = useMemo(() => {
    const options = selectedConfigs.map(c => ({
      id: c.id,
      label: c.name,
      type: c.modules.length > 0 ? c.modules[0].modality.toUpperCase() : "SC"
    }));
    if (!options.length) {
      options.push({
        id: "default",
        label: "NA-MC + SC-F (Profile 2.3)",
        type: "NA + SC",
      });
    }
    return options;
  }, [selectedConfigs]);

  return (
    <div className="h-screen w-full flex flex-col overflow-hidden bg-background">
      {/* Top Navigation Bar */}
      <TopBar
        is3D={is3D}
        onToggle3D={() => setIs3D(!is3D)}
        onZoomIn={() => console.log("Zoom in")}
        onZoomOut={() => console.log("Zoom out")}
        onResetView={() => console.log("Reset view")}
        onToggleSidebar={() => setLeftPanelCollapsed(!leftPanelCollapsed)}
        interactionMode={interactionMode}
        onToggleInteractionMode={() => setInteractionMode(interactionMode === "drag" ? "connect" : "drag")}
        onRunEvaluation={handleRunEvaluation}
        isEvaluating={isEvaluating}
      />

      {/* Main Content Area */}
      <ResizablePanelGroup direction="horizontal" className="flex-1">
        {/* Left Configuration Panel - Resizable */}
        {!leftPanelCollapsed && (
          <>
            <ResizablePanel
              defaultSize={20}
              minSize={15}
              maxSize={30}
              className="min-w-[280px] max-w-[450px]"
            >
              <LeftPanel
                isCollapsed={leftPanelCollapsed}
                onToggleCollapse={() => setLeftPanelCollapsed(!leftPanelCollapsed)}
                modules={modules}
                onModulesChange={setModules}
                links={links}
                onLinksChange={setLinks}
                selectedPrograms={selectedPrograms}
                onSelectedProgramsChange={setSelectedPrograms}
                activeProgram={activeProgramId}
                onActiveProgramChange={setActiveProgramId}
                selectedConfigs={selectedConfigs}
                onSelectedConfigsChange={setSelectedConfigs}
                onPreviewProfileChange={handlePreviewProfileChange}
                deviceParams={deviceParams}
                onDeviceParamsChange={setDeviceParams}
                experimentParams={experimentParams}
                onExperimentParamsChange={handleExperimentParamsChange}
              />
            </ResizablePanel>
            <ResizableHandle withHandle />
          </>
        )}

        {/* Center + Bottom Resizable Layout */}
        <ResizablePanel defaultSize={leftPanelCollapsed ? 100 : 80}>
          <div className="flex-1 flex flex-col overflow-hidden h-full">
            <ResizablePanelGroup direction="vertical" className="flex-1">
              {/* Main Canvas */}
              <ResizablePanel defaultSize={70} minSize={30}>
                <div className="relative h-full w-full">
                  <MainCanvas
                    is3D={is3D}
                    modules={modules}
                    interactionMode={interactionMode}
                    links={links}
                    setLinks={setLinks}
                    architecture={activeViewModels?.architecture ?? previewArchitecture}
                    architectureLoading={profilesLoading && previewProfileId !== null}
                    architectureError={previewProfileId !== null ? profileCatalogError : null}
                  />
                </div>
              </ResizablePanel>

              {/* Resize Handle */}
              <ResizableHandle
                withHandle
                className="resize-handle h-1 hover:h-2 transition-all"
              />

              {/* Bottom Analysis Panel */}
              <ResizablePanel
                defaultSize={30}
                minSize={5}
                collapsible
                collapsedSize={5}
                onCollapse={() => setBottomPanelCollapsed(true)}
                onExpand={() => setBottomPanelCollapsed(false)}
              >
                <BottomPanel
                  isCollapsed={bottomPanelCollapsed}
                  onToggleCollapse={() => setBottomPanelCollapsed(!bottomPanelCollapsed)}
                  viewModels={activeViewModels}
                  allReports={evaluationResults}
                  onTimelineLayerLimitChange={setTimelineLayerLimit}
                  activeProgramId={viewingProgramId}
                  onProgramChange={setViewingProgramId}
                  activeConfigId={viewingConfigId}
                  onConfigChange={setViewingConfigId}
                  onCompareSelect={handleCompareSelect}
                  programs={programOptions}
                  configs={configOptions}
                />
              </ResizablePanel>
            </ResizablePanelGroup>
          </div>
        </ResizablePanel>
      </ResizablePanelGroup>
    </div>
  );
};

export default Index;
