import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: string | number;
  unit?: string;
  accent?: "blue" | "emerald" | "violet" | "amber" | "cyan";
}

const accentStyles: Record<string, string> = {
  blue: "border-l-blue-500",
  emerald: "border-l-emerald-500",
  violet: "border-l-violet-500",
  amber: "border-l-amber-500",
  cyan: "border-l-cyan-500",
};

export function StatCard({ label, value, unit, accent = "blue" }: StatCardProps) {
  return (
    <div
      className={cn(
        "bg-background/60 border border-border/50 rounded-md p-2 border-l-2",
        accentStyles[accent]
      )}
    >
      <div className="text-[9px] font-mono text-muted-foreground uppercase tracking-wider mb-0.5">
        {label}
      </div>
      <div className="flex items-baseline gap-1">
        <span className="text-sm font-bold font-mono text-foreground">
          {typeof value === "number" ? value.toLocaleString() : value}
        </span>
        {unit && (
          <span className="text-[9px] font-mono text-muted-foreground">{unit}</span>
        )}
      </div>
    </div>
  );
}
