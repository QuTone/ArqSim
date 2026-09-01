import { useState, useRef, useCallback, useEffect } from "react";

const ZOOM_SENSITIVITY = 0.002;
const ABS_MIN_SCALE = 0.001; // safety floor; never clamp auto-fit here

interface UsePointerZoomOptions {
  maxZoom?: number;        // max zoom factor relative to fit-view (default 20)
  totalCycles: number;
  pixelsPerCycle?: number;
}

export function usePointerZoom({
  maxZoom = 20,
  totalCycles,
  pixelsPerCycle = 4,
}: UsePointerZoomOptions) {
  const [fitScale, setFitScale] = useState(1); // px/cycle when fully zoomed out to fit
  const [scale, setScale] = useState(1);
  const scrollContainerRef = useRef<HTMLDivElement>(null);

  const chartWidth = Math.max(
    Math.round(totalCycles * pixelsPerCycle * scale),
    scrollContainerRef.current?.clientWidth ?? 0,
  );

  // Auto-fit: when data arrives, set scale so full trace fills container (1× = show all)
  useEffect(() => {
    if (totalCycles <= 0) return;
    const containerW = scrollContainerRef.current?.clientWidth || 600;
    const fs = Math.max(containerW / (totalCycles * pixelsPerCycle), ABS_MIN_SCALE);
    setFitScale(fs);
    setScale(fs);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [totalCycles]);

  // Ctrl+scroll zoom (pointer-centered)
  const handleWheel = useCallback(
    (e: WheelEvent) => {
      if (!e.ctrlKey && !e.metaKey) return;
      e.preventDefault();
      e.stopPropagation();
      const container = scrollContainerRef.current;
      if (!container) return;
      const containerRect = container.getBoundingClientRect();
      const mouseXInContainer = e.clientX - containerRect.left;
      const mouseXInContent = container.scrollLeft + mouseXInContainer;
      const cycleUnderCursor = mouseXInContent / (pixelsPerCycle * scale);
      const factor = Math.exp(-e.deltaY * ZOOM_SENSITIVITY);
      setScale((prev) => {
        const newScale = Math.min(
          Math.max(prev * factor, ABS_MIN_SCALE),
          fitScale * maxZoom,
        );
        const newScrollLeft = cycleUnderCursor * pixelsPerCycle * newScale - mouseXInContainer;
        requestAnimationFrame(() => {
          if (container) container.scrollLeft = Math.max(0, newScrollLeft);
        });
        return newScale;
      });
    },
    [scale, pixelsPerCycle, fitScale, maxZoom],
  );

  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container) return;
    container.addEventListener("wheel", handleWheel, { passive: false });
    return () => container.removeEventListener("wheel", handleWheel);
  }, [handleWheel]);

  return { scale, setScale, fitScale, chartWidth, scrollContainerRef };
}
