import { Cpu, Layers, Scale } from "lucide-react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";

interface ContextControlBarProps {
  selectedProgram: string;
  onProgramChange: (value: string) => void;
  selectedConfig: string;
  onConfigChange: (value: string) => void;
  compareMode?: boolean;
  onCompareModeChange?: (value: boolean) => void;
  showCompare?: boolean;
  programs: Array<{ id: string, label: string }>;
  configs: Array<{ id: string, label: string, type?: string }>;
}

export function ContextControlBar({
  selectedProgram,
  onProgramChange,
  selectedConfig,
  onConfigChange,
  compareMode = false,
  onCompareModeChange,
  showCompare = false,
  programs,
  configs,
}: ContextControlBarProps) {
  return (
    <div className="flex items-center justify-between px-4 py-2.5 bg-black/40 border-b border-white/10 shrink-0">
      {/* Left: Selectors */}
      <div className="flex items-center gap-3">
        {/* Program Selector */}
        <Select value={selectedProgram} onValueChange={onProgramChange}>
          <SelectTrigger className="h-8 w-[150px] bg-background/50 border-border/50 text-xs font-mono">
            <div className="flex items-center gap-2">
              <Cpu className="w-3.5 h-3.5 text-muted-foreground" />
              <SelectValue placeholder="Select Program" />
            </div>
          </SelectTrigger>
          <SelectContent className="bg-popover border-border">
            {programs.map((program) => (
              <SelectItem
                key={program.id}
                value={program.id}
                className="text-xs font-mono"
              >
                {program.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {/* Config Selector */}
        <Select value={selectedConfig} onValueChange={onConfigChange}>
          <SelectTrigger className="h-8 w-[160px] bg-background/50 border-border/50 text-xs font-mono">
            <div className="flex items-center gap-2">
              <Layers className="w-3.5 h-3.5 text-muted-foreground" />
              <SelectValue placeholder="Select Config" />
            </div>
          </SelectTrigger>
          <SelectContent className="bg-popover border-border">
            {configs.map((config) => (
              <SelectItem
                key={config.id}
                value={config.id}
                className="text-xs font-mono"
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`w-1.5 h-1.5 rounded-full ${
                      config.type === "SC"
                        ? "bg-cyan-400"
                        : config.type === "NA"
                        ? "bg-amber-400"
                        : "bg-pink-400"
                    }`}
                  />
                  {config.label}
                </div>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Right: Compare Mode Toggle (only shown when showCompare=true) */}
      {showCompare && onCompareModeChange && (
        <div className="flex items-center gap-2.5">
          <Scale className={`w-3.5 h-3.5 transition-colors ${compareMode ? "text-primary" : "text-muted-foreground"}`} />
          <span className={`text-xs font-mono transition-colors ${compareMode ? "text-foreground" : "text-muted-foreground"}`}>
            Compare
          </span>
          <Switch
            checked={compareMode}
            onCheckedChange={onCompareModeChange}
            className="data-[state=checked]:bg-primary"
          />
        </div>
      )}
    </div>
  );
}
