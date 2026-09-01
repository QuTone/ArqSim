import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * Downsamples a time series array to a target size using Max Pooling.
 * This preserves peaks, which is critical for resource estimation (e.g. max qubits).
 */
export function downsampleTimeSeries<T>(data: T[], targetCount: number, valueKey: keyof T): T[] {
  if (!data || data.length <= targetCount) return data;

  const blockSize = Math.ceil(data.length / targetCount);
  const sampled: T[] = [];

  for (let i = 0; i < data.length; i += blockSize) {
    const chunk = data.slice(i, i + blockSize);
    if (chunk.length === 0) continue;

    // Find the item with the maximum value for the given key in this chunk
    const maxItem = chunk.reduce((prev, current) =>
      Number(current[valueKey]) > Number(prev[valueKey]) ? current : prev
    );
    sampled.push(maxItem);
  }

  return sampled;
}
