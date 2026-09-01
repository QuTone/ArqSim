import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Clock, Cpu, Factory } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type {
  TimelineEventViewModel,
  TimelineRowViewModel,
  TimelineViewModel,
} from "@/types/evaluationReport";
import {
  MAX_TIMELINE_EVENT_LIMIT,
  MAX_TIMELINE_LAYER_LIMIT,
} from "@/services/reportAdapter";

const DEFAULT_SCALE = 100;
const MIN_SCALE = 1e-6;
const MAX_ZOOM = 20;
const ZOOM_SENSITIVITY = 0.002;
const ROW_HEIGHT = 36;
const DEFAULT_LAYER_STEP = 12;

interface TimelineTraceProps {
  viewModel?: TimelineViewModel | null;
  onProgramLayerLimitChange?: (limit: number) => void;
  layerStep?: number;
}

const planePresentation: Record<
  TimelineEventViewModel["plane"],
  { label: string; icon: ReactNode; text: string; block: string }
> = {
  program: {
    label: "Program",
    icon: <Cpu className="h-3.5 w-3.5" />,
    text: "text-blue-400",
    block: "bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.35)]",
  },
  resource: {
    label: "Resource",
    icon: <Factory className="h-3.5 w-3.5" />,
    text: "text-violet-400",
    block: "bg-violet-500 shadow-[0_0_8px_rgba(139,92,246,0.35)]",
  },
};

const opcodeStyles: Record<string, string> = {
  PREPARE_MAGIC_STATE:
    "bg-violet-500 shadow-[0_0_8px_rgba(139,92,246,0.35)]",
  PREPARE_LOGICAL_BELL:
    "bg-[#ff8b8b] shadow-[0_0_8px_rgba(255,139,139,0.35)]",
  MOVE_QUBITS: "bg-orange-400 shadow-[0_0_8px_rgba(251,146,60,0.3)]",
  STORE_QUBITS: "bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.3)]",
  LOAD_QUBITS: "bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.3)]",
  TELEPORT_QUBITS:
    "bg-[#ff8b8b] shadow-[0_0_8px_rgba(255,139,139,0.35)]",
  CLASSICAL_REACTION:
    "bg-pink-500 shadow-[0_0_8px_rgba(236,72,153,0.35)]",
};

const opcodeLegend = [
  { label: "Move", style: opcodeStyles.MOVE_QUBITS },
  { label: "Store", style: opcodeStyles.STORE_QUBITS },
  { label: "Load", style: opcodeStyles.LOAD_QUBITS },
  { label: "Bell / teleport", style: opcodeStyles.TELEPORT_QUBITS },
  { label: "Reaction", style: opcodeStyles.CLASSICAL_REACTION },
  { label: "Logical correction", style: "bg-emerald-500" },
] as const;

function eventStyle(event: TimelineEventViewModel): string {
  if (event.conditionalCorrection) {
    return "bg-emerald-500 ring-1 ring-emerald-200/60 shadow-[0_0_10px_rgba(16,185,129,0.45)]";
  }
  if (event.opcode === "FENCE" || event.durationSeconds === 0) {
    return "bg-cyan-400 border border-cyan-200/50";
  }
  return opcodeStyles[event.opcode] ?? planePresentation[event.plane].block;
}

function formatSeconds(seconds: number): string {
  if (!Number.isFinite(seconds)) return "—";
  if (seconds === 0) return "0 s";
  const magnitude = Math.abs(seconds);
  if (magnitude < 1e-3) return `${seconds.toExponential(3)} s`;
  if (magnitude < 1) return `${seconds.toFixed(6).replace(/0+$/, "")} s`;
  return `${seconds.toFixed(3).replace(/\.0+$/, "")} s`;
}

function niceTickInterval(rawSeconds: number): number {
  if (!Number.isFinite(rawSeconds) || rawSeconds <= 0) return 1;
  const exponent = Math.floor(Math.log10(rawSeconds));
  const magnitude = 10 ** exponent;
  const normalized = rawSeconds / magnitude;
  const multiplier =
    normalized < 1.5 ? 1 : normalized < 3.5 ? 2 : normalized < 7.5 ? 5 : 10;
  return multiplier * magnitude;
}

function zoomToSlider(zoom: number): number {
  return Math.round((Math.log(Math.max(zoom, 1)) / Math.log(MAX_ZOOM)) * 100);
}

function sliderToZoom(value: number): number {
  return MAX_ZOOM ** (value / 100);
}

function TimelineBlock({
  event,
  scale,
}: {
  event: TimelineEventViewModel;
  scale: number;
}) {
  const left = Math.max(0, event.startSeconds * scale);
  const width = Math.max(3, event.durationSeconds * scale);
  const showLabel = width >= 110;

  return (
    <Tooltip delayDuration={75}>
      <TooltipTrigger asChild>
        <div
          className={`absolute bottom-1 top-1 cursor-default rounded-sm transition-[filter] hover:brightness-125 ${eventStyle(event)}`}
          style={{ left, width }}
        >
          {showLabel && (
            <span className="absolute inset-0 flex select-none items-center justify-center truncate px-2 font-mono text-[10px] text-white/90">
              {event.label}
            </span>
          )}
        </div>
      </TooltipTrigger>
      <TooltipContent
        side="top"
        sideOffset={8}
        className="max-w-sm border border-white/20 bg-gray-950 px-3 py-2 shadow-xl"
      >
        <div className="space-y-1.5 font-mono text-xs">
          <div className="border-b border-white/10 pb-1.5 text-sm font-semibold text-white">
            {event.label}
          </div>
          <Detail label="Plane" value={planePresentation[event.plane].label} />
          <Detail label="Opcode" value={event.opcode} />
          {event.instructionId !== null && (
            <Detail label="Instruction" value={String(event.instructionId)} />
          )}
          {event.processId !== null && (
            <Detail label="Process" value={event.processId} />
          )}
          {event.layerIndex !== null && (
            <Detail label="Program layer" value={String(event.layerIndex)} />
          )}
          {event.operationSummary !== null && (
            <Detail label="Circuit ops" value={event.operationSummary} />
          )}
          {event.qubits.length > 0 && (
            <Detail label="Logical qubits" value={event.qubits.map((qubit) => `q${qubit}`).join(", ")} />
          )}
          {event.direction !== null && (
            <Detail label="Direction" value={event.direction} />
          )}
          <Detail label="Duration" value={formatSeconds(event.durationSeconds)} />
          <Detail
            label="Span"
            value={`${formatSeconds(event.startSeconds)} → ${formatSeconds(event.endSeconds)}`}
          />
          {event.programReadySeconds !== null && (
            <Detail label="Program ready" value={formatSeconds(event.programReadySeconds)} />
          )}
          {event.resourceWaitSeconds !== null && event.resourceWaitSeconds > 0 && (
            <Detail label="Resource wait" value={formatSeconds(event.resourceWaitSeconds)} />
          )}
          {event.targetModules.length > 0 && (
            <Detail label="Targets" value={event.targetModules.join(", ")} />
          )}
          {event.waitReasons.length > 0 && (
            <Detail label="Wait" value={event.waitReasons.join(", ")} />
          )}
          {event.runtimeLineage !== null && (
            <>
              <div className="my-1 border-t border-white/10" />
              <Detail label="Runtime step" value={event.runtimeLineage.step} />
              <Detail label="Work ID" value={event.runtimeLineage.workId} />
              <Detail
                label="Source instruction"
                value={String(event.runtimeLineage.sourceInstructionId)}
              />
              {event.runtimeLineage.recipeId !== null && (
                <Detail label="Recipe" value={event.runtimeLineage.recipeId} />
              )}
              {event.runtimeLineage.recipeInvocationId !== null && (
                <Detail
                  label="Recipe invocation"
                  value={event.runtimeLineage.recipeInvocationId}
                />
              )}
              {event.runtimeLineage.stageIndex !== null && (
                <Detail label="Recipe stage" value={String(event.runtimeLineage.stageIndex)} />
              )}
              {event.runtimeLineage.parentEventId !== null && (
                <Detail label="Parent event" value={String(event.runtimeLineage.parentEventId)} />
              )}
            </>
          )}
          {event.measurements.length > 0 && (
            <Detail
              label="Measurement"
              value={event.measurements
                .map((measurement) => `${measurement.registerId} = ${measurement.bit}`)
                .join(" · ")}
            />
          )}
          {event.sourceMeasurements.length > 0 && (
            <Detail
              label="Source measurement"
              value={event.sourceMeasurements
                .map((measurement) => `${measurement.registerId} = ${measurement.bit}`)
                .join(" · ")}
            />
          )}
          {event.continuation !== null && (
            <Detail
              label="Continuation"
              value={
                event.continuation.kind === "complete_source"
                  ? "complete source instruction"
                  : `activate ${event.continuation.activatedWorkIds.join(", ")}`
              }
            />
          )}
          {event.conditionalCorrection && (
            <Detail
              label="Conditional correction"
              value={event.operationSummary ?? "materialized logical correction"}
            />
          )}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <span className="shrink-0 text-muted-foreground">{label}:</span>
      <span className="break-all text-right text-white">{value}</span>
    </div>
  );
}

function TimelineRow({
  row,
  scale,
}: {
  row: TimelineRowViewModel;
  scale: number;
}) {
  return (
    <div
      className="relative rounded-sm bg-white/[0.025]"
      style={{ height: ROW_HEIGHT }}
    >
      {row.events.map((event) => (
        <TimelineBlock key={event.id} event={event} scale={scale} />
      ))}
    </div>
  );
}

function TimeTicks({
  durationSeconds,
  scale,
  scrollLeft,
  viewportWidth,
}: {
  durationSeconds: number;
  scale: number;
  scrollLeft: number;
  viewportWidth: number;
}) {
  const interval = niceTickInterval(90 / Math.max(scale, MIN_SCALE));
  const visibleStart = scrollLeft / Math.max(scale, MIN_SCALE);
  const visibleEnd = (scrollLeft + viewportWidth) / Math.max(scale, MIN_SCALE);
  const generationStart = Math.max(
    0,
    Math.floor((visibleStart - 3 * interval) / interval) * interval,
  );
  const generationEnd = Math.min(
    durationSeconds,
    Math.ceil((visibleEnd + 3 * interval) / interval) * interval,
  );
  const ticks: number[] = [];
  for (
    let time = generationStart;
    time <= generationEnd + interval * 0.01 && ticks.length < 300;
    time += interval
  ) {
    ticks.push(Math.round((time / interval) * 1e9) * interval / 1e9);
  }

  return (
    <div
      className="relative h-7 border-b border-white/10"
      style={{ width: durationSeconds * scale }}
    >
      {ticks.map((tick) => (
        <div
          key={tick}
          className="absolute bottom-0 top-0 flex flex-col items-center"
          style={{ left: tick * scale }}
        >
          <div className="h-2 w-px bg-white/30" />
          <span className="mt-0.5 whitespace-nowrap font-mono text-[9px] text-muted-foreground">
            {formatSeconds(tick)}
          </span>
        </div>
      ))}
    </div>
  );
}

export function TimelineTrace({
  viewModel,
  onProgramLayerLimitChange,
  layerStep = DEFAULT_LAYER_STEP,
}: TimelineTraceProps) {
  const [hoveredRow, setHoveredRow] = useState<string | null>(null);
  const [scale, setScale] = useState(DEFAULT_SCALE);
  const [fitScale, setFitScale] = useState(DEFAULT_SCALE);
  const [scrollLeft, setScrollLeft] = useState(0);
  const [viewportWidth, setViewportWidth] = useState(800);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const durationSeconds = Math.max(
    viewModel?.displayDurationSeconds ?? 0,
    Number.EPSILON,
  );

  const handleWheel = useCallback(
    (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const container = scrollContainerRef.current;
      if (!container) return;
      const containerRect = container.getBoundingClientRect();
      const pointerX = event.clientX - containerRect.left;
      const timeUnderPointer = (container.scrollLeft + pointerX) / scale;
      const factor = Math.exp(-event.deltaY * ZOOM_SENSITIVITY);
      setScale((previous) => {
        const next = Math.min(
          Math.max(previous * factor, MIN_SCALE),
          fitScale * MAX_ZOOM,
        );
        requestAnimationFrame(() => {
          container.scrollLeft = Math.max(0, timeUnderPointer * next - pointerX);
        });
        return next;
      });
    },
    [fitScale, scale],
  );

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    container.addEventListener("wheel", handleWheel, { passive: false });
    return () => container.removeEventListener("wheel", handleWheel);
  }, [handleWheel]);

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    const updateScroll = () => setScrollLeft(container.scrollLeft);
    const updateWidth = () => setViewportWidth(container.clientWidth);
    container.addEventListener("scroll", updateScroll, { passive: true });
    window.addEventListener("resize", updateWidth, { passive: true });
    updateWidth();
    return () => {
      container.removeEventListener("scroll", updateScroll);
      window.removeEventListener("resize", updateWidth);
    };
  }, [viewModel]);

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!viewModel || !container || durationSeconds <= 0) return;
    const nextFitScale = Math.max(
      MIN_SCALE,
      (container.clientWidth - 24) / durationSeconds,
    );
    setFitScale(nextFitScale);
    setScale(nextFitScale);
    container.scrollLeft = 0;
  }, [durationSeconds, viewModel]);

  if (!viewModel) {
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-white/10 bg-[#0a0a0f]/95">
        <div className="space-y-2 text-center">
          <div className="mx-auto flex h-10 w-10 items-center justify-center rounded-full bg-muted/40">
            <Clock className="h-5 w-5 text-muted-foreground" />
          </div>
          <p className="font-mono text-sm text-muted-foreground">
            Run an evaluation to see completed events.
          </p>
        </div>
      </div>
    );
  }

  const nextLayerLimit = Math.min(
    viewModel.totalProgramLayerCount,
    MAX_TIMELINE_LAYER_LIMIT,
    viewModel.visibleProgramLayerCount + Math.max(1, layerStep),
  );
  const reachedPresentationLimit =
    viewModel.visibleProgramLayerCount >= MAX_TIMELINE_LAYER_LIMIT;

  return (
    <TooltipProvider>
      <div className="flex h-full flex-col overflow-hidden rounded-lg border border-white/10 bg-[#0a0a0f]/95">
        <div className="flex shrink-0 items-center gap-4 border-b border-white/10 px-4 py-2">
          <div className="flex shrink-0 items-baseline gap-2">
            <span className="font-mono text-xs text-muted-foreground">Trace:</span>
            <span className="font-mono text-sm font-semibold text-quantum-cyan">
              {formatSeconds(viewModel.displayDurationSeconds)}
            </span>
            <span className="font-mono text-[10px] text-muted-foreground/60">
              first {viewModel.visibleProgramLayerCount} of {viewModel.totalProgramLayerCount} program layers
            </span>
            <span className="font-mono text-[10px] text-muted-foreground/60">
              · {viewModel.renderedEventCount.toLocaleString()} of {viewModel.candidateEventCount.toLocaleString()} causal events
            </span>
          </div>

          <div className="flex min-w-0 flex-1 items-center gap-2">
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
              1×
            </span>
            <Slider
              value={[zoomToSlider(fitScale > 0 ? scale / fitScale : 1)]}
              onValueChange={([value]) =>
                setScale(fitScale * sliderToZoom(value))
              }
              min={0}
              max={100}
              step={1}
              className="flex-1"
            />
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
              20×
            </span>
          </div>

          {viewModel.truncated && onProgramLayerLimitChange && !reachedPresentationLimit && (
            <div className="flex shrink-0 items-center gap-1.5">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-7 font-mono text-[10px]"
                onClick={() => onProgramLayerLimitChange(nextLayerLimit)}
              >
                Show next {nextLayerLimit - viewModel.visibleProgramLayerCount}
              </Button>
            </div>
          )}
          {viewModel.truncated && reachedPresentationLimit && (
            <span className="shrink-0 font-mono text-[10px] text-amber-300/80">
              View capped at {MAX_TIMELINE_LAYER_LIMIT} layers
            </span>
          )}
        </div>

        <div className="flex shrink-0 items-center gap-3 overflow-x-auto border-b border-white/10 px-4 py-1.5">
          <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            Plane
          </span>
          {(Object.keys(planePresentation) as TimelineEventViewModel["plane"][]).map(
            (plane) => (
              <div key={plane} className="flex items-center gap-1.5">
                <div
                  className={`h-2.5 w-4 rounded-sm ${planePresentation[plane].block}`}
                />
                <span className="font-mono text-[10px] text-foreground/70">
                  {planePresentation[plane].label}
                </span>
              </div>
            ),
          )}
          <span className="ml-2 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            Opcode
          </span>
          {opcodeLegend.map((item) => (
            <div key={item.label} className="flex items-center gap-1.5">
              <div className={`h-2.5 w-4 rounded-sm ${item.style}`} />
              <span className="font-mono text-[10px] text-foreground/70">
                {item.label}
              </span>
            </div>
          ))}
          {viewModel.truncated && (
            <span className="ml-auto font-mono text-[10px] text-amber-300/80">
              Causal window truncated at the selected program-layer boundary
            </span>
          )}
          {viewModel.eventTruncated && (
            <span className="ml-auto font-mono text-[10px] text-amber-300/80">
              Rendering capped at {MAX_TIMELINE_EVENT_LIMIT.toLocaleString()} events; Program work is retained first
            </span>
          )}
        </div>

        <div className="flex min-h-0 flex-1 overflow-hidden">
          <div className="w-56 shrink-0 border-r border-white/10 bg-black/20">
            <div className="h-7 border-b border-white/10" />
            <div className="space-y-1 p-1">
              {viewModel.rows.map((row) => (
                <div
                  key={row.id}
                  className={`flex items-center gap-2 rounded-sm px-2 transition-colors ${
                    hoveredRow === row.id ? "bg-white/10" : ""
                  }`}
                  style={{ height: ROW_HEIGHT }}
                  onMouseEnter={() => setHoveredRow(row.id)}
                  onMouseLeave={() => setHoveredRow(null)}
                >
                  <span className={planePresentation[row.plane].text}>
                    {planePresentation[row.plane].icon}
                  </span>
                  <div className="min-w-0">
                    <div className="truncate font-mono text-[11px] text-foreground/85">
                      {row.label}
                    </div>
                    <div className="font-mono text-[9px] uppercase text-muted-foreground/60">
                      {planePresentation[row.plane].label}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div
            ref={scrollContainerRef}
            className="min-w-0 flex-1 overflow-x-auto overflow-y-hidden"
          >
            <div
              style={{
                width: durationSeconds * scale + 40,
                minWidth: "100%",
              }}
            >
              <TimeTicks
                durationSeconds={durationSeconds}
                scale={scale}
                scrollLeft={scrollLeft}
                viewportWidth={viewportWidth}
              />
              <div className="space-y-1 p-1">
                {viewModel.rows.map((row) => (
                  <div
                    key={row.id}
                    onMouseEnter={() => setHoveredRow(row.id)}
                    onMouseLeave={() => setHoveredRow(null)}
                  >
                    <TimelineRow row={row} scale={scale} />
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </TooltipProvider>
  );
}
