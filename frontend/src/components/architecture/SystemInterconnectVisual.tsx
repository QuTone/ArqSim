import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { Network } from "lucide-react";

import { cn } from "@/lib/utils";
import type {
  ArchitectureInterconnectViewModel,
  ArchitectureNodeViewModel,
} from "@/types/evaluationReport";

interface NodeBox {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface Point {
  x: number;
  y: number;
}

interface ResolvedInterconnect {
  interconnect: ArchitectureInterconnectViewModel;
  sourceNodeId: string;
  targetNodeId: string;
  pairIndex: number;
  pairCount: number;
}

interface InterconnectPath extends ResolvedInterconnect {
  path: string;
  midpoint: Point;
  start: Point;
  end: Point;
}

export interface SystemInterconnectVisualProps {
  nodes: readonly ArchitectureNodeViewModel[];
  interconnects: readonly ArchitectureInterconnectViewModel[];
  renderNode: (node: ArchitectureNodeViewModel) => ReactNode;
  className?: string;
  gridClassName?: string;
  ariaLabel?: string;
}

const BOX_EPSILON_PX = 0.25;

function usePrefersReducedMotion() {
  const [prefersReducedMotion, setPrefersReducedMotion] = useState(() =>
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const updatePreference = () => setPrefersReducedMotion(query.matches);
    updatePreference();
    query.addEventListener("change", updatePreference);
    return () => query.removeEventListener("change", updatePreference);
  }, []);

  return prefersReducedMotion;
}

function normalizedReference(value: string) {
  return value.trim().replace(/^\/+|\/+$/g, "");
}

function resolveNodeReference(
  endpoint: string,
  nodes: readonly ArchitectureNodeViewModel[],
) {
  const normalized = normalizedReference(endpoint);
  const exact = nodes.find(
    (node) => normalized === normalizedReference(node.id),
  );
  if (exact) return exact.id;

  const suffixMatches = nodes.filter((node) => {
    const nodeId = normalizedReference(node.id);
    return normalized.endsWith(`/${nodeId}`) || nodeId.endsWith(`/${normalized}`);
  });
  return suffixMatches.length === 1 ? suffixMatches[0].id : null;
}

function resolveInterconnects(
  nodes: readonly ArchitectureNodeViewModel[],
  interconnects: readonly ArchitectureInterconnectViewModel[],
): ResolvedInterconnect[] {
  const resolved = interconnects.flatMap((interconnect) => {
    const endpointIds = interconnect.endpoints
      .map((endpoint) => resolveNodeReference(endpoint, nodes))
      .filter((nodeId): nodeId is string => nodeId !== null);
    const accessIds = interconnect.access
      .map((access) => resolveNodeReference(access.nodeId, nodes))
      .filter((nodeId): nodeId is string => nodeId !== null);
    const uniqueNodeIds = [...new Set([...endpointIds, ...accessIds])];
    if (uniqueNodeIds.length !== 2 || uniqueNodeIds[0] === uniqueNodeIds[1]) {
      return [];
    }
    return [{
      interconnect,
      sourceNodeId: uniqueNodeIds[0],
      targetNodeId: uniqueNodeIds[1],
      pairIndex: 0,
      pairCount: 1,
    }];
  });

  const grouped = new Map<string, ResolvedInterconnect[]>();
  for (const link of resolved) {
    const pairKey = [link.sourceNodeId, link.targetNodeId].sort().join("\u0000");
    const siblings = grouped.get(pairKey) ?? [];
    siblings.push(link);
    grouped.set(pairKey, siblings);
  }
  for (const siblings of grouped.values()) {
    siblings.forEach((link, index) => {
      link.pairIndex = index;
      link.pairCount = siblings.length;
    });
  }
  return resolved;
}

function boxesAreEqual(
  left: Readonly<Record<string, NodeBox>>,
  right: Readonly<Record<string, NodeBox>>,
) {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) return false;
  return leftKeys.every((key) => {
    const leftBox = left[key];
    const rightBox = right[key];
    if (!rightBox) return false;
    return (
      Math.abs(leftBox.left - rightBox.left) < BOX_EPSILON_PX &&
      Math.abs(leftBox.top - rightBox.top) < BOX_EPSILON_PX &&
      Math.abs(leftBox.width - rightBox.width) < BOX_EPSILON_PX &&
      Math.abs(leftBox.height - rightBox.height) < BOX_EPSILON_PX
    );
  });
}

function cubicMidpoint(start: Point, controlA: Point, controlB: Point, end: Point) {
  return {
    x: (start.x + 3 * controlA.x + 3 * controlB.x + end.x) / 8,
    y: (start.y + 3 * controlA.y + 3 * controlB.y + end.y) / 8,
  };
}

function createInterconnectPath(
  source: NodeBox,
  target: NodeBox,
  trackOffset: number,
) {
  const sourceCenter = {
    x: source.left + source.width / 2,
    y: source.top + source.height / 2,
  };
  const targetCenter = {
    x: target.left + target.width / 2,
    y: target.top + target.height / 2,
  };
  const horizontal =
    Math.abs(targetCenter.x - sourceCenter.x) >=
    Math.abs(targetCenter.y - sourceCenter.y);

  let start: Point;
  let end: Point;
  let controlA: Point;
  let controlB: Point;

  if (horizontal) {
    const direction = targetCenter.x >= sourceCenter.x ? 1 : -1;
    start = {
      x: direction > 0 ? source.left + source.width : source.left,
      y: sourceCenter.y + trackOffset,
    };
    end = {
      x: direction > 0 ? target.left : target.left + target.width,
      y: targetCenter.y + trackOffset,
    };
    const handle = Math.max(22, Math.abs(end.x - start.x) * 0.42);
    controlA = { x: start.x + direction * handle, y: start.y };
    controlB = { x: end.x - direction * handle, y: end.y };
  } else {
    const direction = targetCenter.y >= sourceCenter.y ? 1 : -1;
    start = {
      x: sourceCenter.x + trackOffset,
      y: direction > 0 ? source.top + source.height : source.top,
    };
    end = {
      x: targetCenter.x + trackOffset,
      y: direction > 0 ? target.top : target.top + target.height,
    };
    const handle = Math.max(22, Math.abs(end.y - start.y) * 0.42);
    controlA = { x: start.x, y: start.y + direction * handle };
    controlB = { x: end.x, y: end.y - direction * handle };
  }

  return {
    path: `M ${start.x} ${start.y} C ${controlA.x} ${controlA.y}, ${controlB.x} ${controlB.y}, ${end.x} ${end.y}`,
    midpoint: cubicMidpoint(start, controlA, controlB, end),
    start,
    end,
  };
}

/**
 * Renders the architecture's node cards and decorates only declared, inter-node
 * interconnects with an animated energy conduit. Node-local buses deliberately
 * remain outside this component so they retain their static cyan language.
 */
export function SystemInterconnectVisual({
  nodes,
  interconnects,
  renderNode,
  className,
  gridClassName,
  ariaLabel = "Architecture nodes and inter-node connections",
}: SystemInterconnectVisualProps) {
  const rootRef = useRef<HTMLDivElement>(null);
  const nodeElements = useRef(new Map<string, HTMLDivElement>());
  const [nodeBoxes, setNodeBoxes] = useState<Record<string, NodeBox>>({});
  const reducedMotion = usePrefersReducedMotion();
  const rawId = useId();
  const definitionId = rawId.replace(/[^a-zA-Z0-9_-]/g, "");

  const resolvedInterconnects = useMemo(
    () => resolveInterconnects(nodes, interconnects),
    [interconnects, nodes],
  );

  const registerNode = useCallback((nodeId: string, element: HTMLDivElement | null) => {
    if (element) nodeElements.current.set(nodeId, element);
    else nodeElements.current.delete(nodeId);
  }, []);

  const measureNodes = useCallback(() => {
    const root = rootRef.current;
    if (!root) return;
    const rootBounds = root.getBoundingClientRect();
    const measured: Record<string, NodeBox> = {};
    for (const node of nodes) {
      const element = nodeElements.current.get(node.id);
      if (!element) continue;
      const bounds = element.getBoundingClientRect();
      measured[node.id] = {
        left: bounds.left - rootBounds.left,
        top: bounds.top - rootBounds.top,
        width: bounds.width,
        height: bounds.height,
      };
    }
    setNodeBoxes((current) => boxesAreEqual(current, measured) ? current : measured);
  }, [nodes]);

  useLayoutEffect(() => {
    measureNodes();
    const root = rootRef.current;
    if (!root || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measureNodes);
    observer.observe(root);
    for (const element of nodeElements.current.values()) observer.observe(element);
    return () => observer.disconnect();
  }, [measureNodes]);

  const paths = useMemo<InterconnectPath[]>(() =>
    resolvedInterconnects.flatMap((link) => {
      const source = nodeBoxes[link.sourceNodeId];
      const target = nodeBoxes[link.targetNodeId];
      if (!source || !target) return [];
      const trackOffset = (link.pairIndex - (link.pairCount - 1) / 2) * 12;
      return [{ ...link, ...createInterconnectPath(source, target, trackOffset) }];
    }), [nodeBoxes, resolvedInterconnects]);

  const hasInterNodeLinks = resolvedInterconnects.length > 0;

  return (
    <div
      ref={rootRef}
      className={cn("relative isolate", className)}
      role="group"
      aria-label={ariaLabel}
    >
      {paths.length > 0 ? (
        <svg
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 z-10 h-full w-full overflow-visible"
        >
          <defs>
            <linearGradient id={`${definitionId}-energy`} x1="0" y1="0" x2="1" y2="1">
              <stop offset="0%" stopColor="#fb7185" />
              <stop offset="48%" stopColor="#ef4444" />
              <stop offset="100%" stopColor="#fda4af" />
            </linearGradient>
            <filter id={`${definitionId}-glow`} x="-80%" y="-80%" width="260%" height="260%">
              <feGaussianBlur stdDeviation="5" result="blur" />
              <feMerge>
                <feMergeNode in="blur" />
                <feMergeNode in="SourceGraphic" />
              </feMerge>
            </filter>
          </defs>

          {paths.map((link, index) => {
            const pathId = `${definitionId}-path-${index}`;
            const animationDuration = 2.15 + (index % 3) * 0.3;
            return (
              <g key={link.interconnect.id}>
                <path
                  d={link.path}
                  fill="none"
                  stroke="rgba(239,68,68,.13)"
                  strokeWidth="18"
                  strokeLinecap="round"
                  filter={`url(#${definitionId}-glow)`}
                />
                <path
                  d={link.path}
                  fill="none"
                  stroke="rgba(9,9,11,.94)"
                  strokeWidth="9"
                  strokeLinecap="round"
                />
                <path
                  id={pathId}
                  d={link.path}
                  fill="none"
                  stroke={`url(#${definitionId}-energy)`}
                  strokeWidth="2.5"
                  strokeLinecap="round"
                />
                <path
                  d={link.path}
                  fill="none"
                  stroke="rgba(254,202,202,.72)"
                  strokeWidth="1"
                  strokeDasharray="2 13"
                  strokeLinecap="round"
                >
                  {!reducedMotion ? (
                    <animate
                      attributeName="stroke-dashoffset"
                      from="0"
                      to="-30"
                      dur="1.1s"
                      repeatCount="indefinite"
                    />
                  ) : null}
                </path>

                {[link.start, link.end].map((terminal, terminalIndex) => (
                  <g key={`terminal:${terminalIndex}`}>
                    <circle
                      cx={terminal.x}
                      cy={terminal.y}
                      r="7"
                      fill="rgba(9,9,11,.96)"
                      stroke="rgba(248,113,113,.7)"
                      strokeWidth="1.5"
                    />
                    <circle
                      cx={terminal.x}
                      cy={terminal.y}
                      r="2.7"
                      fill="#fff1f2"
                      filter={`url(#${definitionId}-glow)`}
                    />
                  </g>
                ))}

                {!reducedMotion ? [0, 1].map((direction) => (
                  <circle
                    key={`packet:${direction}`}
                    r="3.2"
                    fill="#fff1f2"
                    stroke="#fb7185"
                    strokeWidth="1.6"
                    filter={`url(#${definitionId}-glow)`}
                  >
                      <animateMotion
                        dur={`${animationDuration}s`}
                        begin={`${direction === 0 ? -index * 0.27 : -animationDuration / 2}s`}
                        repeatCount="indefinite"
                        keyPoints={direction === 0 ? "0;1" : "1;0"}
                        keyTimes="0;1"
                        calcMode="linear"
                      >
                        <mpath href={`#${pathId}`} />
                      </animateMotion>
                  </circle>
                )) : null}
              </g>
            );
          })}
        </svg>
      ) : null}

      <div
        className={cn(
          "relative z-20 grid grid-cols-[repeat(auto-fit,minmax(15rem,1fr))]",
          hasInterNodeLinks
            ? "gap-x-[clamp(4.5rem,10vw,9rem)] gap-y-20"
            : "gap-6",
          gridClassName,
        )}
      >
        {nodes.map((node) => (
          <div
            key={node.id}
            ref={(element) => registerNode(node.id, element)}
            className="relative min-w-0"
          >
            {renderNode(node)}
          </div>
        ))}
      </div>

      {paths.map((link) => (
        <div
          key={`label:${link.interconnect.id}`}
          aria-hidden="true"
          className="pointer-events-none absolute z-30 flex -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 rounded-full border border-red-400/40 bg-zinc-950/95 px-2.5 py-1 font-data text-[9px] uppercase tracking-[0.12em] text-red-200 shadow-[0_0_18px_rgba(239,68,68,.28)]"
          style={{ left: link.midpoint.x, top: link.midpoint.y }}
        >
          <Network className="h-3 w-3 text-red-400" />
          <span className="max-w-32 truncate">{link.interconnect.label || link.interconnect.id}</span>
        </div>
      ))}

      {resolvedInterconnects.length > 0 ? (
        <ul className="sr-only" aria-label="Declared inter-node connections">
          {resolvedInterconnects.map((link) => (
            <li key={link.interconnect.id}>
              {link.interconnect.label || link.interconnect.id} connects {link.sourceNodeId} and {link.targetNodeId}.
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
