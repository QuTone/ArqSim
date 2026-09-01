import { Atom, Zap, Link2, CircleDot } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useState } from "react";

interface PerformanceRow {
  duration: string;
  errorRate: string;
}
interface ModalityParams {
  gate1Q: PerformanceRow;
  gate2Q: PerformanceRow;
  spam: PerformanceRow;
}

const defaultParams: Record<string, ModalityParams> = {
  sc: {
    gate1Q: { duration: "10",  errorRate: "1.5e-4" },
    gate2Q: { duration: "40",  errorRate: "8e-4" },
    spam:   { duration: "300", errorRate: "5e-3" },
  },
  na: {
    gate1Q: { duration: "0.1", errorRate: "1e-3" },
    gate2Q: { duration: "0.5", errorRate: "5e-3" },
    spam:   { duration: "500", errorRate: "1e-3" },
  },
  ti: {
    gate1Q: { duration: "10",  errorRate: "8e-6" },
    gate2Q: { duration: "100", errorRate: "3e-4" },
    spam:   { duration: "150", errorRate: "1e-5" },
  },
};
const timeUnits: Record<string, string> = { sc: "ns", na: "μs", ti: "μs" };

const R_PHYBELL_OPTIONS = [
  { value: 1e3,  label: "10³  /s",   note: "near-term" },
  { value: 1e4,  label: "10⁴  /s",   note: "" },
  { value: 1e5,  label: "10⁵  /s",   note: "" },
  { value: 1e6,  label: "10⁶  /s",   note: "" },
  { value: 1e7,  label: "10⁷  /s",   note: "optimistic" },
  { value: 1e8,  label: "10⁸  /s",   note: "" },
];

export interface DeviceLibraryParams {
  rPhybell: number;
}

interface DeviceLibrarySectionProps {
  params: DeviceLibraryParams;
  onParamsChange: (p: DeviceLibraryParams) => void;
}

export function DeviceLibrarySection({ params, onParamsChange }: DeviceLibrarySectionProps) {
  const [modality, setModality] = useState("sc");
  const currentParams = defaultParams[modality];
  const timeUnit = timeUnits[modality];

  const rows: { key: keyof ModalityParams; label: string }[] = [
    { key: "gate1Q", label: "1Q Gate" },
    { key: "gate2Q", label: "2Q Gate" },
    { key: "spam",   label: "SPAM" },
  ];

  return (
    <div className="space-y-4">
      {/* Modality Tabs */}
      <div className="rounded-md border border-amber-400/20 bg-amber-400/5 px-2.5 py-2 font-mono text-[10px] leading-relaxed text-amber-200/80">
        Gate/SPAM values below are catalog references. They are intentionally
        read-only until the public EvaluationConfig exposes calibrated device
        primitive overrides.
      </div>

      <Tabs value={modality} onValueChange={setModality}>
        <TabsList className="w-full bg-muted/50 p-1">
          {[["sc","SC",<Zap className="w-3 h-3 mr-1"/>],["na","NA",<Atom className="w-3 h-3 mr-1"/>],["ti","TI",<CircleDot className="w-3 h-3 mr-1"/>]].map(([val, label, icon]) => (
            <TabsTrigger key={val as string} value={val as string}
              className="flex-1 text-xs data-[state=active]:bg-primary data-[state=active]:text-primary-foreground">
              {icon as React.ReactNode}{label as string}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value={modality} className="mt-3 space-y-3">
          <div className="rounded-md border border-border overflow-hidden">
            <div className="grid grid-cols-3 bg-muted/50 text-xs text-muted-foreground">
              <div className="p-2 border-r border-border">Operation</div>
              <div className="p-2 border-r border-border text-center">Duration ({timeUnit})</div>
              <div className="p-2 text-center">Error Rate</div>
            </div>
            {rows.map((row) => (
              <div key={row.key} className="grid grid-cols-3 border-t border-border">
                <div className="p-2 text-xs text-muted-foreground border-r border-border flex items-center">{row.label}</div>
                <div className="p-1.5 border-r border-border">
                  <Input type="text" value={currentParams[row.key].duration}
                    readOnly disabled
                    className="h-7 text-xs font-mono bg-muted/30 border-0 text-center disabled:opacity-60" />
                </div>
                <div className="p-1.5">
                  <Input type="text" value={currentParams[row.key].errorRate}
                    readOnly disabled
                    className="h-7 text-xs font-mono bg-muted/30 border-0 text-center disabled:opacity-60" />
                </div>
              </div>
            ))}
          </div>
        </TabsContent>
      </Tabs>

      {/* Interconnect / Link Definition */}
      <div className="pt-3 border-t border-border">
        <div className="flex items-center gap-2 mb-3">
          <Link2 className="w-3.5 h-3.5 text-quantum-coral" />
          <span className="text-xs font-medium text-quantum-coral">Link Definition</span>
        </div>

        <p className="mb-3 font-mono text-[10px] leading-relaxed text-muted-foreground/70">
          Link endpoints and modality are fixed by the selected ArchitectureProfile.
          The shared physical Bell-pair supply rate is the only link control
          currently exposed by the canonical evaluator.
        </p>

        {/* Physical Bell Pair Rate */}
        <div className="space-y-1.5 mb-3">
          <Label className="text-xs text-muted-foreground">Physical Bell Pair Rate</Label>
          <Select
            value={String(params.rPhybell)}
            onValueChange={v => onParamsChange({ ...params, rPhybell: Number(v) })}
          >
            <SelectTrigger className="h-8 text-xs font-mono bg-quantum-coral/5 border-quantum-coral/30">
              <SelectValue />
            </SelectTrigger>
            <SelectContent className="bg-popover border-border">
              {R_PHYBELL_OPTIONS.map(opt => (
                <SelectItem key={opt.value} value={String(opt.value)} className="text-xs font-mono">
                  {opt.label}{opt.note ? <span className="text-muted-foreground ml-2">({opt.note})</span> : null}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <div className="rounded-md bg-muted/30 border border-border px-2 py-1.5">
          <p className="text-[10px] text-muted-foreground/70 font-mono mb-0.5">
            Runtime Bell production is resolved from this supply rate and the
            selected catalog protocol.
          </p>
          <p className="text-[10px] text-muted-foreground/50 font-mono">
            Protocol timing, output error, and footprint share one hashed backend binding.
          </p>
        </div>
      </div>
    </div>
  );
}
