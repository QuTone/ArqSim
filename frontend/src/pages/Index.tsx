import { useEffect, useState, useMemo } from "react";
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from "@/components/ui/resizable";
import { TopBar } from "@/components/layout/TopBar";
import { LeftPanel } from "@/components/layout/LeftPanel";
import { MainCanvas } from "@/components/layout/MainCanvas";
import { EvaluationResultsPage } from "@/pages/EvaluationResultsPage";
import { Module, ModuleLink, InteractionMode, defaultModules } from "@/types/module";
import { SelectedConfig } from "@/components/config/ArchitectureComposerSection";
import { DeviceLibraryParams } from "@/components/config/DeviceLibrarySection";
import {
  defaultExperimentSetupParams,
  type ExperimentSetupParams,
} from "@/types/experiment";
import {
  api,
  type Benchmark,
} from "@/services/api";
import { architectureProfileToViewModel, reportToViewModels } from "@/services/reportAdapter";
import {
  finiteInjectionDemoSelectionError,
} from "@/services/evaluationPresets";
import {
  buildEvaluationRequest,
  previewEvaluationArchitecture,
} from "@/services/evaluationRequests";
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
  const [workspaceView, setWorkspaceView] = useState<"setup" | "results">("setup");
  const [leftPanelCollapsed, setLeftPanelCollapsed] = useState(false);
  const is3D = true;
  const [modules, setModules] = useState<Module[]>(defaultModules);
  const [links, setLinks] = useState<ModuleLink[]>([]);
  const interactionMode: InteractionMode = "drag";

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

  // The architecture shown in an unselected preset preview is also the one run.
  // Share this list with result selectors so their labels describe the request.
  const configsToRun = useMemo<SelectedConfig[]>(() => {
    if (selectedConfigs.length > 0) return selectedConfigs;
    return [{
      ...previewEvaluationArchitecture(
        previewProfileId,
        previewProfileId ? profilePreviews[previewProfileId]?.profileLabel : null,
      ),
      modules: [],
      links: [],
      source: "preset",
    }];
  }, [selectedConfigs, previewProfileId, profilePreviews]);

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
    // A report is evidence for the parameters that produced it. The visible
    // result index is keyed only by program and architecture, so clear it when
    // semantics change rather than letting the selector revive stale evidence.
    // The request-keyed cache remains safe to reuse after a matching rerun.
    setEvaluationResults({});
    setViewingProgramId("");
    setViewingConfigId("");
  };

  const handleRunEvaluation = async () => {
    if (selectedPrograms.length === 0) {
      toast.error("Please select at least one quantum program");
      return;
    }
    setTimelineLayerLimit(
      experimentParams.evaluationPreset === "default"
        ? experimentParams.previewMaxLayers ?? 12
        : 12,
    );

    if (experimentParams.evaluationPreset === "finite_t_injection_demo_v1") {
      const selectionError = finiteInjectionDemoSelectionError(
        selectedPrograms.map((program) => program.id),
        configsToRun.map((config) => config.profileId),
      );
      if (selectionError) {
        toast.error(selectionError);
        return;
      }
    }

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
      // Bound concurrency: each evaluation can generate a large causal report.
      const outcomes = await settleWithConcurrency(
        allCombos,
        MAX_PARALLEL_EVALUATIONS,
        async ({ program, arch }) => {
          const comboId = `${program.id}|${arch.id}`;
          const request = buildEvaluationRequest(program, arch, experimentParams, deviceParams);
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
          const reason = outcome.reason instanceof Error
            ? outcome.reason.message
            : String(outcome.reason);
          failedCombos.push(`${program.name} / ${arch.name}: ${reason}`);
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

      if (failedCombos.length > 0) {
        toast.error(`${failedCombos.length} evaluation(s) failed`, {
          description: failedCombos.join("\n"),
          duration: 12_000,
        });
      }
      if (successCount > 0) {
        toast.success(`${successCount}/${allCombos.length} evaluation(s) completed`);
        setWorkspaceView("results");
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
    const config = configsToRun.find(c => c.id === configId);
    if (config && config.modules.length > 0) {
      setModules(config.modules);
      setLinks(config.links);
    }
  };

  const programOptions = useMemo(() =>
    selectedPrograms.map(p => ({ id: p.id, label: p.name })),
  [selectedPrograms]);

  const configOptions = useMemo(() => {
    return configsToRun.map(c => ({
      id: c.id,
      label: c.name,
      type: c.modules.length > 0 ? c.modules[0].modality.toUpperCase() : "Preset",
    }));
  }, [configsToRun]);

  if (workspaceView === "results" && activeViewModels) {
    return (
      <EvaluationResultsPage
        viewModels={activeViewModels}
        allReports={evaluationResults}
        onBack={() => setWorkspaceView("setup")}
        onTimelineLayerLimitChange={setTimelineLayerLimit}
        activeProgramId={viewingProgramId}
        onProgramChange={setViewingProgramId}
        activeConfigId={viewingConfigId}
        onConfigChange={setViewingConfigId}
        onCompareSelect={handleCompareSelect}
        programs={programOptions}
        configs={configOptions}
      />
    );
  }

  return (
    <div className="h-screen w-full flex flex-col overflow-hidden bg-background">
      {/* Top Navigation Bar */}
      <TopBar
        onToggleSidebar={() => setLeftPanelCollapsed(!leftPanelCollapsed)}
        onRunEvaluation={handleRunEvaluation}
        isEvaluating={isEvaluating}
      />

      <main className="min-h-0 flex-1 p-4">
      <ResizablePanelGroup
        direction="horizontal"
        className="mx-auto h-full max-w-[1600px] overflow-hidden rounded-xl border border-border bg-secondary/20"
      >
        {!leftPanelCollapsed && (
          <>
            <ResizablePanel
              defaultSize={60}
              minSize={42}
              maxSize={72}
              className="min-w-[360px]"
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

        <ResizablePanel defaultSize={leftPanelCollapsed ? 100 : 40} minSize={28}>
          <div className="relative h-full w-full bg-canvas">
            <div className="pointer-events-none absolute left-4 top-4 z-20 rounded-md border border-white/10 bg-black/55 px-3 py-2 backdrop-blur-sm">
              <div className="text-xs font-medium text-foreground">Architecture</div>
              <div className="font-mono text-[9px] text-muted-foreground">
                Canonical profile preview
              </div>
            </div>
            <MainCanvas
              is3D={is3D}
              modules={modules}
              interactionMode={interactionMode}
              links={links}
              setLinks={setLinks}
              architecture={previewArchitecture}
              architectureLoading={profilesLoading && previewProfileId !== null}
              architectureError={previewProfileId !== null ? profileCatalogError : null}
            />
          </div>
        </ResizablePanel>
      </ResizablePanelGroup>
      </main>
    </div>
  );
};

export default Index;
