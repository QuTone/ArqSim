import {
  Activity,
  ChevronRight,
  CircleDot,
  GitBranch,
  ListTree,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
import type {
  DynamicProgramWorkViewModel,
  LogicalGadgetSpanViewModel,
  ProgramExecutionViewModel,
  RuntimeMeasurementViewModel,
} from "@/types/evaluationReport";

interface ProgramExecutionPanelProps {
  viewModel?: ProgramExecutionViewModel | null;
}

const PROGRAM_RENDER_LIMIT = 240;
const GADGET_RENDER_LIMIT = 120;

function words(value: string): string {
  return value
    .replace(/[_:/.-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function seconds(value: number): string {
  if (value === 0) return "0 s";
  if (Math.abs(value) < 1e-3) return `${value.toExponential(3)} s`;
  if (Math.abs(value) < 1) return `${value.toFixed(6).replace(/0+$/, "")} s`;
  return `${value.toFixed(3).replace(/\.0+$/, "")} s`;
}

function measurementText(measurements: readonly RuntimeMeasurementViewModel[]): string {
  const bits = measurements.map((measurement) => measurement.bit).join(", ");
  return `${measurements.length === 1 ? "bit" : "bits"} ${bits}`;
}

function stepLabel(item: DynamicProgramWorkViewModel): string {
  switch (item.lineage.step) {
    case "entangle":
      return "T-gadget entangle (logical CX)";
    case "injection":
      return "Correction-stage entangle (logical CX)";
    case "measurement":
      return "Magic-state logical MZ";
    case "reaction":
      return "Classical reaction";
    case "correction":
      return "Conditional logical correction";
    default:
      return words(item.lineage.step);
  }
}

function stepStyle(item: DynamicProgramWorkViewModel): string {
  switch (item.lineage.step) {
    case "reaction":
      return "border-pink-400/30 bg-pink-400/10 text-pink-300";
    case "measurement":
      return "border-fuchsia-400/30 bg-fuchsia-400/10 text-fuchsia-300";
    case "correction":
      return "border-emerald-400/30 bg-emerald-400/10 text-emerald-300";
    case "entangle":
    case "injection":
      return "border-amber-400/30 bg-amber-400/10 text-amber-300";
    default:
      return "border-blue-400/30 bg-blue-400/10 text-blue-300";
  }
}

function conventionSummary(convention: string): string | null {
  switch (convention) {
    case "cx_data_magic_measure_magic_z_v1":
      return "CX(data → magic) · measure magic in Z";
    default:
      return null;
  }
}

function gadgetStatus(gadget: LogicalGadgetSpanViewModel): string {
  if (gadget.measurement === null) return "outcome unavailable";
  return gadget.correctionApplied
    ? `bit ${gadget.measurement.bit} · logical correction applied`
    : `bit ${gadget.measurement.bit} · no correction`;
}

function operationContext(item: DynamicProgramWorkViewModel): string | null {
  if (!item.operationSummary) return null;
  return item.lineage.step === "correction"
    ? `materialized operation · ${item.operationSummary}`
    : `implements · ${item.operationSummary}`;
}

export function ProgramExecutionPanel({ viewModel }: ProgramExecutionPanelProps) {
  if (!viewModel) {
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-white/10 bg-[#0a0a0f]/95">
        <div className="space-y-2 text-center">
          <ListTree className="mx-auto h-8 w-8 text-muted-foreground" />
          <p className="font-mono text-sm text-muted-foreground">
            Run an evaluation to inspect the output Program and runtime realization.
          </p>
        </div>
      </div>
    );
  }

  const { outputProgram, dynamicWork, logicalGadgets } = viewModel;
  const recipeInstructions = outputProgram.instructions.filter(
    (instruction) => instruction.recipes.length > 0,
  );
  const prioritizedInstructions = [
    ...recipeInstructions,
    ...outputProgram.instructions.filter((instruction) => instruction.recipes.length === 0),
  ].slice(0, PROGRAM_RENDER_LIMIT);
  const visibleInstructionIds = new Set(
    prioritizedInstructions.map((instruction) => instruction.id),
  );
  const visibleInstructions = outputProgram.instructions.filter((instruction) =>
    visibleInstructionIds.has(instruction.id),
  );
  const workByEventId = new Map(dynamicWork.map((item) => [item.eventId, item]));
  const visibleGadgets = logicalGadgets.slice(0, GADGET_RENDER_LIMIT);

  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-3 lg:grid-cols-2">
      <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-white/10 bg-[#0a0a0f]/95">
        <header className="flex items-center justify-between border-b border-white/10 px-3 py-2">
          <div className="flex items-center gap-2">
            <ListTree className="h-4 w-4 text-cyan-400" />
            <span className="font-mono text-xs font-semibold text-foreground">Output Program</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="font-mono text-[8px] text-muted-foreground">
              {visibleInstructions.length === outputProgram.instructions.length
                ? `${outputProgram.instructions.length} instructions`
                : `showing ${visibleInstructions.length} of ${outputProgram.instructions.length} · recipe-first window`}
            </span>
            {outputProgram.runtimeInjectionMode && (
              <Badge variant="outline" className="border-cyan-400/30 font-mono text-[9px] text-cyan-300">
                {outputProgram.runtimeInjectionMode}
              </Badge>
            )}
          </div>
        </header>

        {outputProgram.unavailableReason ? (
          <div className="flex flex-1 items-center justify-center p-6 text-center font-mono text-xs text-muted-foreground">
            {outputProgram.unavailableReason}
          </div>
        ) : (
          <ScrollArea className="min-h-0 flex-1">
            <div className="space-y-2 p-3">
              {visibleInstructions.map((instruction) => (
                <article key={instruction.id} className="rounded-md border border-white/10 bg-white/[0.025] p-2.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-[10px] text-muted-foreground">#{instruction.id}</span>
                    <span className="font-mono text-xs font-semibold text-white">{instruction.opcode}</span>
                    {instruction.layerIndex !== null && (
                      <Badge variant="secondary" className="font-mono text-[9px]">L{instruction.layerIndex}</Badge>
                    )}
                    <span className="ml-auto font-mono text-[9px] text-muted-foreground">
                      {instruction.recipes.length > 0 ? "initial phase " : "planned "}
                      {seconds(instruction.durationSeconds)}
                    </span>
                  </div>
                  <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[9px] text-muted-foreground">
                    <span>deps: {instruction.predecessors.length ? instruction.predecessors.join(", ") : "root"}</span>
                    {instruction.qubits.length > 0 && <span>q: {instruction.qubits.join(", ")}</span>}
                    {instruction.operationSummary && <span className="text-blue-300">{instruction.operationSummary}</span>}
                  </div>

                  {instruction.recipes.map((recipe) => (
                    <div key={recipe.invocationId} className="mt-2 rounded border border-amber-400/20 bg-amber-400/[0.04] p-2">
                      <div className="flex flex-wrap items-center gap-1.5">
                        <GitBranch className="h-3 w-3 text-amber-300" />
                        <span className="font-mono text-[10px] font-semibold text-amber-200">
                          {recipe.stages[0]?.stateKind === "t_magic" ? "Logical T gadget" : recipe.recipeId}
                        </span>
                        <Badge variant="outline" className="border-amber-400/25 font-mono text-[8px] text-amber-300">
                          runtime recipe
                        </Badge>
                      </div>
                      <p className="mt-1 break-all font-mono text-[8px] text-muted-foreground">
                        {recipe.invocationId}
                      </p>
                      <div className="mt-1.5 rounded border border-white/10 bg-black/15 px-2 py-1.5 font-mono text-[9px]">
                        <p className="break-all text-foreground/75">{recipe.convention}</p>
                        {conventionSummary(recipe.convention) && (
                          <p className="mt-0.5 text-amber-200">{conventionSummary(recipe.convention)}</p>
                        )}
                        <p className="mt-0.5 text-muted-foreground">
                          Semantic structure only · realized timing is shown at right
                        </p>
                      </div>
                      <div className="mt-1.5 space-y-1">
                        {recipe.stages.map((stage) => (
                          <div key={stage.index} className="flex flex-wrap items-center gap-1.5 font-mono text-[9px] text-foreground/75">
                            <CircleDot className="h-2.5 w-2.5 text-amber-300" />
                            <span>stage {stage.index}</span>
                            <span>· {stage.stateKind}</span>
                            <span className="text-emerald-300">
                              · unfavorable → {stage.unfavorableAction.kind === "next_stage"
                                ? `stage ${stage.unfavorableAction.stageIndex}`
                                : `logical ${stage.unfavorableAction.operation.toUpperCase()}`}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </article>
              ))}
            </div>
          </ScrollArea>
        )}
      </section>

      <section className="flex min-h-0 flex-col overflow-hidden rounded-lg border border-white/10 bg-[#0a0a0f]/95">
        <header className="flex items-center justify-between border-b border-white/10 px-3 py-2">
          <div className="flex items-center gap-2">
            <Activity className="h-4 w-4 text-violet-400" />
            <span className="font-mono text-xs font-semibold text-foreground">Runtime Gadget Realization</span>
          </div>
          <span className="font-mono text-[9px] text-muted-foreground">
            {visibleGadgets.length === logicalGadgets.length
              ? `${logicalGadgets.length} logical operations`
              : `showing ${visibleGadgets.length} of ${logicalGadgets.length}`}
          </span>
        </header>
        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-2 p-3">
            {logicalGadgets.length === 0 ? (
              <p className="rounded-md border border-dashed border-white/10 p-4 text-center font-mono text-xs text-muted-foreground">
                No runtime gadget was materialized. Black-box semantics keep T
                aggregate. For the acceptance demo, select Advanced → Runtime
                semantics → Finite T injection demo.
              </p>
            ) : visibleGadgets.map((gadget, gadgetIndex) => {
              const children = gadget.childEventIds
                .map((eventId) => workByEventId.get(eventId))
                .filter((item): item is DynamicProgramWorkViewModel => item !== undefined);
              return (
                <Collapsible
                  key={gadget.invocationId}
                  defaultOpen={gadgetIndex < 2}
                  className="group rounded-md border border-cyan-400/20 bg-cyan-400/[0.035]"
                >
                  <CollapsibleTrigger className="flex w-full items-start gap-2 p-2.5 text-left hover:bg-white/[0.025]">
                    <ChevronRight className="mt-0.5 h-3.5 w-3.5 shrink-0 text-cyan-300 transition-transform group-data-[state=open]:rotate-90" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs font-semibold text-cyan-100">{gadget.label}</span>
                        <Badge variant="outline" className="border-cyan-400/25 font-mono text-[8px] text-cyan-300">
                          logical operation
                        </Badge>
                        <span className="ml-auto font-mono text-[9px] text-cyan-100">
                          {seconds(gadget.realizationElapsedSeconds)}
                        </span>
                      </div>
                      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[9px] text-muted-foreground">
                        <span>instruction {gadget.sourceInstructionId}</span>
                        <span>{gadgetStatus(gadget)}</span>
                        <span>
                          execution {seconds(gadget.dispatchSeconds)} → {seconds(gadget.completionSeconds)}
                        </span>
                        {gadget.queueWaitSeconds > 0 && (
                          <span>waited {seconds(gadget.queueWaitSeconds)} before dispatch</span>
                        )}
                      </div>
                    </div>
                  </CollapsibleTrigger>
                  <CollapsibleContent className="border-t border-white/10 px-2.5 pb-2.5 pt-2">
                    <p className="mb-2 font-mono text-[8px] text-muted-foreground">
                      The execution span starts at dispatch; waiting is attributed separately.
                      Child events remain the accounting facts.
                    </p>
                    <div className="space-y-1.5">
                      {children.map((item) => {
                        const associatedMeasurements = item.measurements.length
                          ? item.measurements
                          : item.sourceMeasurements;
                        const shared = item.recipeInvocationIds.length > 1;
                        const context = operationContext(item);
                        return (
                          <div key={item.eventId} className="rounded border border-white/10 bg-black/15 px-2 py-1.5">
                            <div className="flex flex-wrap items-center gap-1.5">
                              <Badge variant="outline" className={`font-mono text-[8px] ${stepStyle(item)}`}>
                                {stepLabel(item)}
                              </Badge>
                              <span className="font-mono text-[9px] text-foreground/80">event {item.eventId}</span>
                              {shared && (
                                <Badge variant="outline" className="border-violet-400/25 font-mono text-[8px] text-violet-300">
                                  shared by {item.recipeInvocationIds.length} gadgets · counted once
                                </Badge>
                              )}
                              <span className="ml-auto font-mono text-[9px] text-muted-foreground">
                                {seconds(item.durationSeconds)}
                              </span>
                            </div>
                            <div className="mt-1 font-mono text-[8px] text-muted-foreground">
                              {seconds(item.startSeconds)} → {seconds(item.endSeconds)}
                              {context ? ` · ${context}` : ""}
                            </div>
                            {associatedMeasurements.length > 0 && (
                              <div className="mt-1 font-mono text-[8px] text-fuchsia-200">
                                outcome · {measurementText(associatedMeasurements)}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </CollapsibleContent>
                </Collapsible>
              );
            })}
          </div>
        </ScrollArea>
      </section>
    </div>
  );
}
