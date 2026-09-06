import {
  Fragment,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  Binary,
  ChevronDown,
  ChevronRight,
  CircuitBoard,
  Clock,
  Cpu,
  Database,
  Factory,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import type {
  TimelineBackpressureSpanViewModel,
  TimelineBufferStateSegmentViewModel,
  TimelineBufferTrackViewModel,
  TimelineEventViewModel,
  TimelineGadgetHostViewModel,
  TimelineLane,
  TimelineRowViewModel,
  TimelineTrackKind,
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
const ROW_GAP = 4;
const SECTION_HEIGHT = 24;
const STATE_ROW_HEIGHT = 32;
const DEFAULT_LAYER_STEP = 12;

interface TimelineTraceProps {
  viewModel?: TimelineViewModel | null;
  onProgramLayerLimitChange?: (limit: number) => void;
  layerStep?: number;
}

const lanePresentation: Record<
  TimelineLane,
  { label: string; icon: ReactNode; text: string; block: string }
> = {
  program: {
    label: "Program",
    icon: <Cpu className="h-3.5 w-3.5" />,
    text: "text-blue-400",
    block: "bg-blue-500 shadow-[0_0_8px_rgba(59,130,246,0.35)]",
  },
  quantum: {
    label: "Quantum",
    icon: <CircuitBoard className="h-3.5 w-3.5" />,
    text: "text-amber-400",
    block: "bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.35)]",
  },
  classical: {
    label: "Classical",
    icon: <Binary className="h-3.5 w-3.5" />,
    text: "text-pink-400",
    block: "bg-pink-500 shadow-[0_0_8px_rgba(236,72,153,0.35)]",
  },
  resource: {
    label: "Resource",
    icon: <Factory className="h-3.5 w-3.5" />,
    text: "text-violet-400",
    block: "bg-violet-500 shadow-[0_0_8px_rgba(139,92,246,0.35)]",
  },
};

const trackPresentation: Record<
  TimelineTrackKind,
  { icon: ReactNode; text: string }
> = {
  submodule: {
    icon: <CircuitBoard className="h-3.5 w-3.5" />,
    text: "text-cyan-300",
  },
  module: {
    icon: <Cpu className="h-3.5 w-3.5" />,
    text: "text-cyan-400",
  },
  interconnect: {
    icon: <CircuitBoard className="h-3.5 w-3.5" />,
    text: "text-orange-400",
  },
  transfer: {
    icon: <CircuitBoard className="h-3.5 w-3.5" />,
    text: "text-amber-400",
  },
  classical: {
    icon: <Binary className="h-3.5 w-3.5" />,
    text: "text-pink-400",
  },
  control: {
    icon: <Clock className="h-3.5 w-3.5" />,
    text: "text-blue-400",
  },
  resource: {
    icon: <Factory className="h-3.5 w-3.5" />,
    text: "text-violet-400",
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
    "bg-sky-500 shadow-[0_0_8px_rgba(14,165,233,0.35)]",
  CLASSICAL_REACTION:
    "bg-pink-500 shadow-[0_0_8px_rgba(236,72,153,0.35)]",
};

function visualStartSeconds(event: TimelineEventViewModel): number {
  return event.waitingSpan?.startSeconds ?? event.startSeconds;
}

function eventStyle(event: TimelineEventViewModel): string {
  if (event.conditionalCorrection) {
    return "bg-emerald-500 ring-1 ring-emerald-200/60 shadow-[0_0_10px_rgba(16,185,129,0.45)]";
  }
  if (event.opcode === "FENCE" || event.durationSeconds === 0) {
    return "bg-cyan-400 border border-cyan-200/50";
  }
  return opcodeStyles[event.opcode] ?? lanePresentation[event.lane].block;
}

function formatUnit(value: number, unit: string): string {
  const formatted = value.toFixed(3).replace(/\.?0+$/, "");
  return `${formatted === "" || formatted === "-" ? "0" : formatted} ${unit}`;
}

function formatSeconds(seconds: number): string {
  if (!Number.isFinite(seconds)) return "—";
  if (seconds === 0) return "0 s";
  const magnitude = Math.abs(seconds);
  if (magnitude < 1e-6) return formatUnit(seconds * 1e9, "ns");
  if (magnitude < 1e-3) return formatUnit(seconds * 1e6, "µs");
  if (magnitude < 1) return formatUnit(seconds * 1e3, "ms");
  return formatUnit(seconds, "s");
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

function humanize(value: string): string {
  return value
    .replace(/[_:/.-]+/g, " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function eventTime(event: TimelineEventViewModel): string {
  return (
    `${formatSeconds(event.startSeconds)} → ${formatSeconds(event.endSeconds)}` +
    ` · ${formatSeconds(event.durationSeconds)}`
  );
}

function routeSummary(event: TimelineEventViewModel): string | null {
  if (event.targetLinks.length > 0) {
    return event.targetLinks.map(humanize).join(" → ");
  }
  if (event.targetModules.length > 1) {
    return event.targetModules.map(humanize).join(" → ");
  }
  return event.direction === null ? null : humanize(event.direction);
}

function WaitingBlock({
  event,
  scale,
}: {
  event: TimelineEventViewModel;
  scale: number;
}) {
  const waiting = event.waitingSpan;
  if (waiting === null) return null;
  const left = Math.max(0, waiting.startSeconds * scale);
  const width = Math.max(2, waiting.durationSeconds * scale);
  return (
    <Tooltip delayDuration={75}>
      <TooltipTrigger asChild>
        <div
          className="absolute bottom-1 top-1 z-0 cursor-default rounded-sm border border-dashed border-slate-300/35 bg-slate-300/[0.06]"
          style={{
            left,
            width,
            backgroundImage:
              "repeating-linear-gradient(135deg, transparent 0, transparent 5px, rgba(148,163,184,0.16) 5px, rgba(148,163,184,0.16) 7px)",
          }}
          aria-label={`${event.label} waiting`}
        />
      </TooltipTrigger>
      <TooltipContent
        side="top"
        sideOffset={6}
        className="max-w-xs border border-white/20 bg-gray-950 px-2.5 py-2 shadow-xl"
      >
        <div className="space-y-1 font-mono text-xs">
          <div className="font-semibold text-slate-100">Waiting demand</div>
          <div className="text-white">{event.label}</div>
          <Detail label="Delay" value={formatSeconds(waiting.durationSeconds)} />
          <Detail label="Encountered" value={waiting.reason} />
          <p className="border-t border-white/10 pt-1 text-[10px] text-muted-foreground">
            Ready but not dispatched; this does not occupy the execution track.
          </p>
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

function BackpressureBlock({
  span,
  scale,
}: {
  span: TimelineBackpressureSpanViewModel;
  scale: number;
}) {
  const left = Math.max(0, span.startSeconds * scale);
  const width = Math.max(2, span.durationSeconds * scale);
  return (
    <Tooltip delayDuration={75}>
      <TooltipTrigger asChild>
        <div
          className="absolute bottom-1 top-1 z-0 cursor-default rounded-sm border border-violet-300/30 bg-violet-300/[0.045]"
          style={{
            left,
            width,
            backgroundImage:
              "repeating-linear-gradient(45deg, transparent 0, transparent 5px, rgba(196,181,253,0.15) 5px, rgba(196,181,253,0.15) 7px), repeating-linear-gradient(-45deg, transparent 0, transparent 5px, rgba(196,181,253,0.1) 5px, rgba(196,181,253,0.1) 7px)",
          }}
          aria-label={`${humanize(span.processId)} backpressure`}
        >
          {width >= 92 && (
            <span className="absolute inset-0 flex select-none items-center justify-center truncate px-2 font-mono text-[9px] text-violet-100/75">
              Backpressure
            </span>
          )}
        </div>
      </TooltipTrigger>
      <TooltipContent
        side="top"
        sideOffset={6}
        className="max-w-xs border border-white/20 bg-gray-950 px-2.5 py-2 shadow-xl"
      >
        <div className="space-y-1 font-mono text-xs">
          <div className="font-semibold text-violet-100">Producer backpressure</div>
          <Detail label="Producer" value={humanize(span.processId)} />
          <Detail label="Delay" value={formatSeconds(span.durationSeconds)} />
          <Detail label="Cause" value={span.reason} />
          <Detail
            label="Output"
            value={span.bufferIds.map(humanize).join(", ")}
          />
          <p className="border-t border-white/10 pt-1 text-[10px] text-muted-foreground">
            Idle process capacity while declared output capacity was full.
          </p>
        </div>
      </TooltipContent>
    </Tooltip>
  );
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
  const step = event.runtimeLineage?.step ?? null;
  const parent = event.logicalParentLabels.join(" · ");
  const directBit = event.measurements[0]?.bit;
  const sourceBit = event.sourceMeasurements[0]?.bit;
  const route = routeSummary(event);
  const scope =
    event.operationSummary ??
    (event.qubits.length > 0
      ? event.qubits.map((qubit) => `q${qubit}`).join(", ")
      : null);
  const isInstant = event.opcode === "FENCE" || event.durationSeconds === 0;

  return (
    <>
      <WaitingBlock event={event} scale={scale} />
      <Tooltip delayDuration={75}>
        <TooltipTrigger asChild>
          <div
            className={`absolute bottom-1 top-1 z-10 cursor-default transition-[filter] hover:brightness-125 ${
              isInstant ? "rounded-none" : "rounded-sm"
            } ${eventStyle(event)}`}
            style={{ left, width: isInstant ? 2 : width }}
          >
            {showLabel && !isInstant && (
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
            <Detail label="Time" value={eventTime(event)} />
            {parent ? (
              <Detail label="Parent" value={parent} />
            ) : scope ? (
              <Detail label="Scope" value={scope} />
            ) : null}
            {step === "measurement" && directBit !== undefined && (
              <Detail label="Outcome" value={String(directBit)} />
            )}
            {step === "reaction" && sourceBit !== undefined && (
              <Detail label="Outcome" value={`bit ${sourceBit}`} />
            )}
            {step === "reaction" && event.continuation !== null && (
              <Detail
                label="Continuation"
                value={event.continuation.kind === "complete_source"
                  ? "Source complete"
                  : event.continuation.activatedWorkIds.length === 0
                    ? "No child work activated"
                    : `${event.continuation.activatedWorkIds.length} child work item${event.continuation.activatedWorkIds.length === 1 ? "" : "s"} activated`}
              />
            )}
            {step === "correction" && sourceBit !== undefined && (
              <Detail label="Condition" value={`measurement bit ${sourceBit}`} />
            )}
            {route && <Detail label="Route" value={route} />}
            {event.continuesAfterWindow && (
              <div className="text-[10px] text-amber-200">
                Continues beyond the displayed causal window.
              </div>
            )}
          </div>
        </TooltipContent>
      </Tooltip>
    </>
  );
}

function GadgetHostBlock({
  host,
  scale,
  expanded,
  onToggle,
}: {
  host: TimelineGadgetHostViewModel;
  scale: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const left = Math.max(0, host.dispatchSeconds * scale);
  const width = Math.max(4, host.realizationElapsedSeconds * scale);
  const waitLeft = Math.max(0, host.readySeconds * scale);
  const waitWidth = Math.max(2, host.queueWaitSeconds * scale);
  const showFullLabel = width >= 90;
  const showCompactLabel = width >= 16;
  const internalWaitSeconds = Math.max(
    0,
    host.realizationElapsedSeconds - host.activeServiceSeconds,
  );
  return (
    <>
      {!expanded && host.queueWaitSeconds > Number.EPSILON && (
        <Tooltip delayDuration={75}>
          <TooltipTrigger asChild>
            <div
              className="absolute bottom-1 top-1 z-0 cursor-default rounded-sm border border-dashed border-slate-300/35 bg-slate-300/[0.06]"
              style={{
                left: waitLeft,
                width: waitWidth,
                backgroundImage:
                  "repeating-linear-gradient(135deg, transparent 0, transparent 5px, rgba(148,163,184,0.16) 5px, rgba(148,163,184,0.16) 7px)",
              }}
              aria-label={`${host.label} waiting`}
            />
          </TooltipTrigger>
          <TooltipContent
            side="top"
            sideOffset={6}
            className="border border-white/20 bg-gray-950 px-2.5 py-2 shadow-xl"
          >
            <div className="space-y-1 font-mono text-xs">
              <div className="font-semibold text-slate-100">Program wait</div>
              <Detail label="Operation" value={host.label} />
              <Detail label="Delay" value={formatSeconds(host.queueWaitSeconds)} />
            </div>
          </TooltipContent>
        </Tooltip>
      )}
      <Tooltip delayDuration={75}>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-expanded={expanded}
            aria-label={`${expanded ? "Collapse" : "Expand"} ${host.label}`}
            onClick={onToggle}
            className={`absolute bottom-1 top-1 rounded-sm border-2 transition-colors ${
              expanded
                ? "z-[1] border-dashed border-cyan-200/45 bg-cyan-400/[0.025]"
                : "z-10 border-cyan-400/70 bg-cyan-500/25 shadow-[0_0_9px_rgba(34,211,238,0.25)] hover:bg-cyan-500/35"
            }`}
            style={{ left, width }}
          >
            {showCompactLabel && (
              <span className={`absolute inset-0 flex select-none items-center justify-center truncate px-2 font-mono text-[10px] ${
                expanded ? "text-cyan-100/45" : "text-cyan-50"
              }`}>
                {showFullLabel
                  ? `${expanded ? "▾" : "▸"} ${host.label}`
                  : host.label.startsWith("T")
                    ? "T"
                    : "G"}
              </span>
            )}
          </button>
        </TooltipTrigger>
        <TooltipContent
          side="top"
          sideOffset={8}
          className="max-w-sm border border-cyan-300/25 bg-gray-950 px-3 py-2 shadow-xl"
        >
          <div className="space-y-1.5 font-mono text-xs">
            <div className="border-b border-white/10 pb-1.5 text-sm font-semibold text-cyan-100">
              {host.label} · runtime gadget
            </div>
            <Detail
              label="Execution"
              value={
                `${formatSeconds(host.dispatchSeconds)} → ${formatSeconds(host.completionSeconds)}` +
                ` · ${formatSeconds(host.realizationElapsedSeconds)}`
              }
            />
            {host.queueWaitSeconds > Number.EPSILON && (
              <Detail label="Queue wait" value={formatSeconds(host.queueWaitSeconds)} />
            )}
            {internalWaitSeconds > 1e-12 && (
              <Detail label="Active service" value={formatSeconds(host.activeServiceSeconds)} />
            )}
            <Detail
              label="Implementation"
              value={`${host.childEventIds.length} phases`}
            />
            <p className="border-t border-white/10 pt-1 text-[10px] text-muted-foreground">
              Click to {expanded ? "collapse" : "inspect"} the realized phases.
            </p>
          </div>
        </TooltipContent>
      </Tooltip>
    </>
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

function TraceSectionLabel({
  label,
  detail,
  icon,
  expanded,
  onToggle,
}: {
  label: string;
  detail: string;
  icon: ReactNode;
  expanded?: boolean;
  onToggle?: () => void;
}) {
  const content = (
    <>
      <span className="text-muted-foreground">{icon}</span>
      <span className="font-mono text-[9px] font-semibold uppercase tracking-[0.14em] text-foreground/70">
        {label}
      </span>
      <span className="ml-auto truncate font-mono text-[8px] text-muted-foreground/45">
        {detail}
      </span>
      {onToggle &&
        (expanded ? (
          <ChevronDown className="h-3 w-3 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" />
        ))}
    </>
  );
  return onToggle ? (
    <button
      type="button"
      className="flex w-full items-center gap-1.5 rounded-sm bg-white/[0.035] px-2 text-left hover:bg-white/[0.06]"
      style={{ height: SECTION_HEIGHT }}
      aria-expanded={expanded}
      onClick={onToggle}
    >
      {content}
    </button>
  ) : (
    <div
      className="flex items-center gap-1.5 rounded-sm bg-white/[0.035] px-2"
      style={{ height: SECTION_HEIGHT }}
    >
      {content}
    </div>
  );
}

function TraceSectionRule({
  label,
  expanded,
  onToggle,
}: {
  label: string;
  expanded?: boolean;
  onToggle?: () => void;
}) {
  const className =
    "relative flex w-full items-center bg-white/[0.018] px-2 text-left";
  const content = (
    <>
      <div className="h-px w-full bg-white/10" />
      <span className="absolute left-3 bg-[#0a0a0f] px-2 font-mono text-[8px] uppercase tracking-[0.16em] text-muted-foreground/40">
        {label}
      </span>
      {onToggle && (
        <span className="sr-only">
          {expanded ? "Collapse architecture state" : "Expand architecture state"}
        </span>
      )}
    </>
  );
  return onToggle ? (
    <button
      type="button"
      className={className}
      style={{ height: SECTION_HEIGHT }}
      aria-expanded={expanded}
      onClick={onToggle}
    >
      {content}
    </button>
  ) : (
    <div className={className} style={{ height: SECTION_HEIGHT }}>
      {content}
    </div>
  );
}

function bufferStateLabel(
  segment: TimelineBufferStateSegmentViewModel,
): string {
  if (segment.ready > 0 && segment.pendingIncoming > 0) {
    return `${segment.ready}/${segment.capacity} ready · ${segment.pendingIncoming} reserved`;
  }
  if (segment.ready > 0) return `${segment.ready}/${segment.capacity} ready`;
  if (segment.pendingIncoming > 0) return `${segment.pendingIncoming}/${segment.capacity} reserved`;
  return "empty";
}

function BufferStateSegment({
  track,
  segment,
  scale,
}: {
  track: TimelineBufferTrackViewModel;
  segment: TimelineBufferStateSegmentViewModel;
  scale: number;
}) {
  const left = segment.startSeconds * scale;
  const width = Math.max(1, (segment.endSeconds - segment.startSeconds) * scale);
  const hasReady = segment.ready > 0;
  const hasIncoming = segment.pendingIncoming > 0;
  const label = bufferStateLabel(segment);
  const showLabel = width >= 76;
  return (
    <Tooltip delayDuration={75}>
      <TooltipTrigger asChild>
        <div
          className={`absolute bottom-1 top-1 cursor-default overflow-hidden rounded-sm border transition-[filter] hover:brightness-125 ${
            hasReady
              ? "border-violet-300/45 bg-violet-500/35"
              : hasIncoming
                ? "border-cyan-300/35 bg-cyan-400/[0.08]"
                : "border-white/[0.06] bg-white/[0.018]"
          }`}
          style={{
            left,
            width,
            backgroundImage: hasIncoming
              ? "repeating-linear-gradient(135deg, transparent 0, transparent 5px, rgba(34,211,238,0.14) 5px, rgba(34,211,238,0.14) 7px)"
              : undefined,
          }}
        >
          {showLabel && (
            <span className="absolute inset-0 flex items-center justify-center truncate px-1 font-mono text-[9px] text-white/75">
              {label}
            </span>
          )}
        </div>
      </TooltipTrigger>
      <TooltipContent
        side="top"
        sideOffset={6}
        className="max-w-xs border border-white/20 bg-gray-950 px-2.5 py-2 shadow-xl"
      >
        <div className="space-y-1 font-mono text-xs">
          <div className="font-semibold text-white">{track.label}</div>
          <Detail label="State" value={label} />
          <Detail
            label="Time"
            value={`${formatSeconds(segment.startSeconds)} → ${formatSeconds(segment.endSeconds)}`}
          />
          <Detail label="Owner" value={track.ownerLabel} />
        </div>
      </TooltipContent>
    </Tooltip>
  );
}

function BufferStateRow({
  track,
  scale,
}: {
  track: TimelineBufferTrackViewModel;
  scale: number;
}) {
  return (
    <div
      className="relative rounded-sm bg-white/[0.015]"
      style={{ height: STATE_ROW_HEIGHT }}
    >
      {track.segments.map((segment, index) => (
        <BufferStateSegment
          key={`${segment.startSeconds}:${segment.endSeconds}:${index}`}
          track={track}
          segment={segment}
          scale={scale}
        />
      ))}
    </div>
  );
}

function TimelineRow({
  bands,
  gadgetHosts,
  expandedGadgetHosts,
  onToggleGadgetHost,
  scale,
  backpressureSpans,
}: {
  bands: readonly (readonly TimelineEventViewModel[])[];
  gadgetHosts: readonly TimelineGadgetHostViewModel[];
  expandedGadgetHosts: ReadonlySet<string>;
  onToggleGadgetHost: (hostId: string) => void;
  scale: number;
  backpressureSpans: readonly TimelineBackpressureSpanViewModel[];
}) {
  const displayBands = bands.length > 0 ? bands : [[]];
  return (
    <div className="relative">
      <div className="space-y-1">
        {displayBands.map((band, bandIndex) => (
          <div
            key={bandIndex}
            className="relative rounded-sm bg-white/[0.025]"
            style={{ height: ROW_HEIGHT }}
          >
            {bandIndex === 0 &&
              backpressureSpans.map((span) => (
                <BackpressureBlock key={span.id} span={span} scale={scale} />
              ))}
            {bandIndex === 0 &&
              gadgetHosts.map((host) => (
                <GadgetHostBlock
                  key={host.id}
                  host={host}
                  scale={scale}
                  expanded={expandedGadgetHosts.has(host.id)}
                  onToggle={() => onToggleGadgetHost(host.id)}
                />
              ))}
            {band.map((event) => (
              <TimelineBlock key={event.id} event={event} scale={scale} />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function packEventBands(
  events: readonly TimelineEventViewModel[],
): TimelineEventViewModel[][] {
  const bands: TimelineEventViewModel[][] = [];
  const bandEnds: number[] = [];
  const orderedEvents = [...events].sort(
    (left, right) =>
      visualStartSeconds(left) - visualStartSeconds(right) ||
      left.startSeconds - right.startSeconds ||
      left.endSeconds - right.endSeconds ||
      left.id.localeCompare(right.id),
  );
  for (const event of orderedEvents) {
    let bandIndex = bandEnds.findIndex(
      (endSeconds) => visualStartSeconds(event) >= endSeconds - Number.EPSILON,
    );
    if (bandIndex === -1) {
      bandIndex = bands.length;
      bands.push([]);
      bandEnds.push(0);
    }
    bands[bandIndex].push(event);
    bandEnds[bandIndex] = Math.max(bandEnds[bandIndex], event.endSeconds);
  }
  return bands;
}

function packedRowHeight(bandCount: number): number {
  const visibleBandCount = Math.max(1, bandCount);
  return (
    visibleBandCount * ROW_HEIGHT +
    Math.max(0, visibleBandCount - 1) * ROW_GAP
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
  const [zoom, setZoom] = useState(1);
  const [fitScale, setFitScale] = useState(DEFAULT_SCALE);
  const [scrollLeft, setScrollLeft] = useState(0);
  const [viewportWidth, setViewportWidth] = useState(800);
  const [expandedGadgetHosts, setExpandedGadgetHosts] = useState<Set<string>>(
    new Set(),
  );
  const [architectureStateExpanded, setArchitectureStateExpanded] = useState(false);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const durationSeconds = Math.max(
    viewModel?.displayDurationSeconds ?? 0,
    Number.EPSILON,
  );
  const scale = Math.max(MIN_SCALE, fitScale * zoom);

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
      setZoom((previous) => {
        const next = Math.min(Math.max(previous * factor, 1), MAX_ZOOM);
        requestAnimationFrame(() => {
          container.scrollLeft = Math.max(
            0,
            timeUnderPointer * fitScale * next - pointerX,
          );
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
    container.addEventListener("scroll", updateScroll, { passive: true });
    return () => {
      container.removeEventListener("scroll", updateScroll);
    };
  }, [viewModel]);

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!viewModel || !container || durationSeconds <= 0) return;
    const updateSize = () => {
      const width = container.clientWidth;
      setViewportWidth(width);
      setFitScale(
        Math.max(MIN_SCALE, (Math.max(width, 24) - 24) / durationSeconds),
      );
    };
    const resizeObserver = new ResizeObserver(updateSize);
    resizeObserver.observe(container);
    setZoom(1);
    container.scrollLeft = 0;
    updateSize();
    return () => resizeObserver.disconnect();
  }, [durationSeconds, viewModel]);

  useEffect(() => {
    setExpandedGadgetHosts(new Set());
  }, [viewModel]);

  const toggleGadgetHost = useCallback((hostId: string) => {
    setExpandedGadgetHosts((previous) => {
      const next = new Set(previous);
      if (next.has(hostId)) next.delete(hostId);
      else next.add(hostId);
      return next;
    });
  }, []);

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

  const gadgetHostIdByInvocationId = new Map(
    viewModel.gadgetHosts.flatMap((host) =>
      host.invocationIds.map((invocationId) => [invocationId, host.id] as const),
    ),
  );
  const gadgetHostsByOwnerTrackId = new Map<
    string,
    TimelineGadgetHostViewModel[]
  >();
  viewModel.gadgetHosts.forEach((host) => {
    const hosts = gadgetHostsByOwnerTrackId.get(host.ownerTrackId) ?? [];
    hosts.push(host);
    gadgetHostsByOwnerTrackId.set(host.ownerTrackId, hosts);
  });
  const visibleRows = viewModel.rows
    .map((row) => {
      const events = row.events.filter((event) => {
        if (event.recipeInvocationIds.length === 0) return true;
        const hostIds = event.recipeInvocationIds.flatMap((invocationId) => {
          const hostId = gadgetHostIdByInvocationId.get(invocationId);
          return hostId === undefined ? [] : [hostId];
        });
        if (hostIds.length === 0) return true;
        return hostIds.some((hostId) => expandedGadgetHosts.has(hostId));
      });
      const gadgetHosts = gadgetHostsByOwnerTrackId.get(row.id) ?? [];
      return {
        ...row,
        events,
        gadgetHosts,
        bands: packEventBands(events),
      };
    })
    .filter(
      (row) =>
        row.events.length > 0 ||
        row.gadgetHosts.length > 0 ||
        row.backpressureSpans.length > 0,
    );
  const knownGroupIds = new Set(viewModel.groups.map((group) => group.id));
  const groupedSections = viewModel.groups.flatMap((group) => {
    const rows = visibleRows.filter((row) => row.parentTrackId === group.id);
    return rows.length === 0 ? [] : [{ group, rows }];
  });
  const ungroupedRows = visibleRows.filter(
    (row) => row.parentTrackId === null || !knownGroupIds.has(row.parentTrackId),
  );
  const hasArchitectureState = viewModel.bufferTracks.length > 0;

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
            {viewModel.gadgetHosts.length > 0 && (
              <span className="font-mono text-[10px] text-cyan-300/80">
                · {viewModel.gadgetHosts.length} runtime gadget{viewModel.gadgetHosts.length === 1 ? "" : "s"}
              </span>
            )}
          </div>

          <div className="flex min-w-0 flex-1 items-center gap-2">
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
              1×
            </span>
            <Slider
              value={[zoomToSlider(zoom)]}
              onValueChange={([value]) => setZoom(sliderToZoom(value))}
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

        <div className="flex min-h-0 flex-1 items-start overflow-y-auto">
          <div className="w-56 shrink-0 border-r border-white/10 bg-black/20">
            <div className="h-7 border-b border-white/10" />
            <div className="space-y-1 p-1">
              <TraceSectionLabel
                label="Runtime execution"
                detail={`${visibleRows.length} runtime ${visibleRows.length === 1 ? "track" : "tracks"}`}
                icon={<Cpu className="h-3 w-3" />}
              />
              {groupedSections.map(({ group, rows }) => (
                <Fragment key={group.id}>
                  <TraceSectionLabel
                    label={group.label}
                    detail={group.subtitle}
                    icon={group.kind === "interconnect"
                      ? <CircuitBoard className="h-3 w-3" />
                      : <Cpu className="h-3 w-3" />}
                  />
                  {rows.map((row) => (
                    <div
                      key={row.id}
                      className={`flex items-center gap-2 rounded-sm px-2 pl-6 transition-colors ${
                        hoveredRow === row.id ? "bg-white/10" : ""
                      }`}
                      style={{ height: packedRowHeight(row.bands.length) }}
                      onMouseEnter={() => setHoveredRow(row.id)}
                      onMouseLeave={() => setHoveredRow(null)}
                    >
                      <span className={trackPresentation[row.trackKind].text}>
                        {trackPresentation[row.trackKind].icon}
                      </span>
                      <div className="min-w-0">
                        <div className="truncate font-mono text-[11px] text-foreground/85">
                          {row.label}
                        </div>
                        <div className="truncate font-mono text-[9px] uppercase text-muted-foreground/60">
                          {row.subtitle}
                        </div>
                      </div>
                    </div>
                  ))}
                </Fragment>
              ))}
              {ungroupedRows.map((row) => (
                <div
                  key={row.id}
                  className={`flex items-center gap-2 rounded-sm px-2 transition-colors ${
                    hoveredRow === row.id ? "bg-white/10" : ""
                  }`}
                  style={{ height: packedRowHeight(row.bands.length) }}
                  onMouseEnter={() => setHoveredRow(row.id)}
                  onMouseLeave={() => setHoveredRow(null)}
                >
                  <span className={trackPresentation[row.trackKind].text}>
                    {trackPresentation[row.trackKind].icon}
                  </span>
                  <div className="min-w-0">
                    <div className="truncate font-mono text-[11px] text-foreground/85">
                      {row.label}
                    </div>
                    <div className="font-mono text-[9px] uppercase text-muted-foreground/60">
                      {row.subtitle}
                    </div>
                  </div>
                </div>
              ))}
              {hasArchitectureState && (
                <>
                  <TraceSectionLabel
                    label="Architecture state"
                    detail={`${viewModel.bufferTracks.length} buffers`}
                    icon={<Database className="h-3 w-3" />}
                    expanded={architectureStateExpanded}
                    onToggle={() =>
                      setArchitectureStateExpanded((expanded) => !expanded)
                    }
                  />
                  {architectureStateExpanded &&
                    viewModel.bufferTracks.map((track) => {
                      const rowId = track.id;
                      return (
                        <div
                          key={rowId}
                          className={`flex items-center gap-2 rounded-sm px-2 transition-colors ${
                            hoveredRow === rowId ? "bg-white/10" : ""
                          }`}
                          style={{ height: STATE_ROW_HEIGHT }}
                          onMouseEnter={() => setHoveredRow(rowId)}
                          onMouseLeave={() => setHoveredRow(null)}
                        >
                          <span className="text-violet-300">
                            <Database className="h-3.5 w-3.5" />
                          </span>
                          <div className="min-w-0">
                            <div className="truncate font-mono text-[11px] text-foreground/85">
                              {track.label}
                            </div>
                            <div className="truncate font-mono text-[9px] uppercase text-muted-foreground/60">
                              {track.subtitle}
                            </div>
                          </div>
                        </div>
                      );
                    })}
                </>
              )}
            </div>
          </div>

          <div
            ref={scrollContainerRef}
            className="min-w-0 flex-1 overflow-x-auto"
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
                <TraceSectionRule label="Runtime execution" />
                {groupedSections.map(({ group, rows }) => (
                  <Fragment key={group.id}>
                    <TraceSectionRule label={group.label} />
                    {rows.map((row) => (
                      <div
                        key={row.id}
                        onMouseEnter={() => setHoveredRow(row.id)}
                        onMouseLeave={() => setHoveredRow(null)}
                      >
                        <TimelineRow
                          bands={row.bands}
                          gadgetHosts={row.gadgetHosts}
                          expandedGadgetHosts={expandedGadgetHosts}
                          onToggleGadgetHost={toggleGadgetHost}
                          scale={scale}
                          backpressureSpans={row.backpressureSpans}
                        />
                      </div>
                    ))}
                  </Fragment>
                ))}
                {ungroupedRows.map((row) => (
                  <div
                    key={row.id}
                    onMouseEnter={() => setHoveredRow(row.id)}
                    onMouseLeave={() => setHoveredRow(null)}
                  >
                    <TimelineRow
                      bands={row.bands}
                      gadgetHosts={row.gadgetHosts}
                      expandedGadgetHosts={expandedGadgetHosts}
                      onToggleGadgetHost={toggleGadgetHost}
                      scale={scale}
                      backpressureSpans={row.backpressureSpans}
                    />
                  </div>
                ))}
                {hasArchitectureState && (
                  <>
                    <TraceSectionRule
                      label="Architecture state"
                      expanded={architectureStateExpanded}
                      onToggle={() =>
                        setArchitectureStateExpanded((expanded) => !expanded)
                      }
                    />
                    {architectureStateExpanded &&
                      viewModel.bufferTracks.map((track) => (
                        <div
                          key={track.id}
                          onMouseEnter={() => setHoveredRow(track.id)}
                          onMouseLeave={() => setHoveredRow(null)}
                        >
                          <BufferStateRow track={track} scale={scale} />
                        </div>
                      ))}
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </TooltipProvider>
  );
}
