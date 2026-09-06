import { useState, useEffect } from "react";
import { FlaskConical, GitBranch } from "lucide-react";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import type { ExperimentSetupParams } from "@/types/experiment";

// MSF protocol metadata — matches arqsim MSF_CONFIG
const MSF_PROTOCOLS = [
  {
    id: "cultivation",
    label: "Cultivation",
    size: 463,
    cycles: 2187.6809,
    throughput: 1,
    note: "Catalog profile: cultivation-d5-d15-p1e3",
  },
  {
    id: "MSD1",
    label: "Litinski 15-to-1 × 20-to-4",
    size: 43300,
    cycles: 130,
    throughput: 4,
    note: "Catalog profile: four-output Litinski protocol",
  },
  {
    id: "MSD2",
    label: "Litinski 15-to-1",
    size: 4620,
    cycles: 42.6,
    throughput: 1,
    note: "Catalog profile: litinski-15to1-17-7-7-p1e3",
  },
] as const;

interface Props {
  params: ExperimentSetupParams;
  onParamsChange: (p: ExperimentSetupParams) => void;
}

function useNumericInput(
  value: number,
  onCommit: (v: number) => void,
  validate: (v: number) => boolean,
) {
  const [str, setStr] = useState(String(value));
  useEffect(() => { setStr(String(value)); }, [value]);
  return {
    value: str,
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => {
      setStr(e.target.value);
      const v = parseFloat(e.target.value);
      if (!isNaN(v) && validate(v)) onCommit(v);
    },
    onBlur: () => {
      const v = parseFloat(str);
      if (isNaN(v) || !validate(v)) setStr(String(value));
    },
  };
}

export function ExperimentSetupSection({ params, onParamsChange }: Props) {
  const currentMSF = MSF_PROTOCOLS.find(p => p.id === params.msfProtocol)!;

  const naInput = useNumericInput(
    params.naCycleTimeMs,
    v => onParamsChange({ ...params, naCycleTimeMs: v }),
    v => v > 0,
  );
  const scInput = useNumericInput(
    params.scCycleTimeUs,
    v => onParamsChange({ ...params, scCycleTimeUs: v }),
    v => v > 0,
  );
  return (
    <div className="space-y-4">

      <div className="space-y-2">
        <div className="flex items-center gap-1.5">
          <GitBranch className="h-3 w-3 text-cyan-400" />
          <span className="text-xs font-medium text-cyan-400">Runtime semantics</span>
        </div>
        <Select
          value={params.evaluationPreset}
          onValueChange={(value) =>
            onParamsChange({
              ...params,
              evaluationPreset: value as ExperimentSetupParams["evaluationPreset"],
            })
          }
        >
          <SelectTrigger className="h-8 border-cyan-500/30 bg-cyan-500/5 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="bg-popover border-border">
            <SelectItem value="default" className="text-xs">
              Default · black-box injection
            </SelectItem>
            <SelectItem value="finite_t_injection_demo_v1" className="text-xs">
              Finite T injection demo · Profile 2.3
            </SelectItem>
          </SelectContent>
        </Select>
        <p className="rounded-md border border-white/10 bg-muted/20 px-2.5 py-2 font-mono text-[10px] leading-relaxed text-muted-foreground/75">
          {params.evaluationPreset === "default"
            ? "Uses the core defaults: black-box injection and canonical_reference_v1 fidelity. Fidelity is disabled only by explicit opt-out."
            : "Locked acceptance path: ArqSim Timeline Demo + Profile 2.3, full Trace v4, finite-state injection, seed 0, reference reaction latency, and canonical fidelity. Other experiment and device overrides are ignored."}
        </p>
      </div>

      {/* ── MSF Protocol ─────────────────────────────────────── */}
      <div className="space-y-2">
        <div className="flex items-center gap-1.5 mb-1">
          <FlaskConical className="w-3 h-3 text-violet-400" />
          <span className="text-xs font-medium text-violet-400">MSF Protocol</span>
        </div>
        <Select
          value={params.msfProtocol}
          onValueChange={v => onParamsChange({ ...params, msfProtocol: v as ExperimentSetupParams["msfProtocol"] })}
        >
          <SelectTrigger className="h-8 text-xs bg-violet-500/5 border-violet-500/30">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="bg-popover border-border">
            {MSF_PROTOCOLS.map(p => (
              <SelectItem key={p.id} value={p.id} className="text-xs">
                {p.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {/* Protocol metadata card */}
        <div className="rounded-md bg-violet-500/5 border border-violet-500/20 px-2.5 py-2 space-y-1">
          <div className="grid grid-cols-3 gap-1 text-[10px] font-mono">
            <div>
              <span className="text-muted-foreground">Size</span>
              <div className="text-violet-300 font-semibold">
                {currentMSF.size >= 1000 ? `${(currentMSF.size/1000).toFixed(1)}k` : currentMSF.size}
              </div>
            </div>
            <div>
              <span className="text-muted-foreground">Cycles</span>
              <div className="text-violet-300 font-semibold">{currentMSF.cycles}</div>
            </div>
            <div>
              <span className="text-muted-foreground">Throughput</span>
              <div className="text-violet-300 font-semibold">×{currentMSF.throughput}</div>
            </div>
          </div>
          <p className="text-[10px] text-muted-foreground/70 leading-tight">{currentMSF.note}</p>
        </div>
      </div>

      {/* ── MSF Copies ───────────────────────────────────────── */}
      <div className="space-y-1.5">
        <Label className="text-xs text-muted-foreground">
          MSF Copies
          <span className="text-muted-foreground/60 ml-1">(parallel factories)</span>
        </Label>
        <Select
          value={String(params.msfCopies)}
          onValueChange={v => onParamsChange({ ...params, msfCopies: parseInt(v) })}
        >
          <SelectTrigger className="h-8 text-xs bg-muted/30 border-border">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="bg-popover border-border">
            {[1, 2, 3, 4, 6, 8].map(n => (
              <SelectItem key={n} value={String(n)} className="text-xs font-mono">
                {n} {n === 1 ? "copy" : "copies"}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* ── Hardware Timing ──────────────────────────────────── */}
      <div className="pt-2 border-t border-border space-y-2">
        <span className="text-xs font-medium text-muted-foreground">Hardware Timing</span>
        <div className="grid grid-cols-2 gap-2">
          <div className="space-y-1">
            <Label className="text-[10px] text-muted-foreground">NA Cycle</Label>
            <div className="relative">
              <Input {...naInput} type="text"
                className="h-7 text-xs font-mono bg-muted/30 border-border pr-7" />
              <span className="absolute right-1.5 top-1/2 -translate-y-1/2 text-[9px] text-muted-foreground">ms</span>
            </div>
          </div>
          <div className="space-y-1">
            <Label className="text-[10px] text-muted-foreground">SC Cycle</Label>
            <div className="relative">
              <Input {...scInput} type="text"
                className="h-7 text-xs font-mono bg-muted/30 border-border pr-7" />
              <span className="absolute right-1.5 top-1/2 -translate-y-1/2 text-[9px] text-muted-foreground">µs</span>
            </div>
          </div>
        </div>
      </div>

      <div className="pt-2 border-t border-border space-y-2">
        <p className="rounded-md border border-white/10 bg-muted/20 px-2.5 py-2 font-mono text-[10px] leading-relaxed text-muted-foreground/70">
          Additional syndrome-round, O_ED, and repeated-DES controls are hidden
          because the public EvaluationConfig does not expose them as request parameters.
          Stochastic realization and protocol assumptions remain visible in the
          returned configuration and provenance receipts.
        </p>
      </div>
    </div>
  );
}
