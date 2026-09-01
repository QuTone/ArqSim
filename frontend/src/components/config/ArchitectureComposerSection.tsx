import { useState, useRef, useEffect, Dispatch, SetStateAction } from "react";
import { Plus, Trash2, ChevronDown, ChevronRight, Zap, Atom, CircleDot, Save, Clock, LayoutGrid, FilePlus, Check, X, Edit2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Module,
  ModuleLink,
  Role,
  Modality,
  QECType,
  BBParam,
  MSFProtocol,
  protocolDefaults,
  modalityLabels,
  roleLabels,
  qecLabels,
  calculateNKD,
} from "@/types/module";

interface ArchitectureComposerSectionProps {
  modules: Module[];
  onModulesChange: (modules: Module[]) => void;
  links: ModuleLink[];
  onLinksChange: Dispatch<SetStateAction<ModuleLink[]>>;
}

interface SavedConfig {
  id: string;
  name: string;
  savedAt: Date;
  modules: Module[];
  links: ModuleLink[];
}

interface PresetConfig {
  id: string;
  profileId: string;
  name: string;
  description: string;
  modules: Module[];
  links: ModuleLink[];
}

const presetConfigs: PresetConfig[] = [
  // Group 1: Surface Code Only (No MCSep)
  {
    id: "na-sf",
    profileId: "1.1",
    name: "NA-CF",
    description: "Monolithic neutral-atom compute and magic-state factory",
    modules: [
      {
        id: "na-sf-mem",
        customName: "NA Memory",
        role: "memory",
        modality: "na",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "1000",
      },
      {
        id: "na-sf-comp",
        customName: "NA Compute",
        role: "compute",
        modality: "na",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "100",
      },
      {
        id: "na-sf-fact",
        customName: "NA Factory",
        role: "factory",
        modality: "na",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "100",
      },
    ],
    links: [
      { sourceId: "na-sf-mem", targetId: "na-sf-comp" },
      { sourceId: "na-sf-fact", targetId: "na-sf-comp" },
    ],
  },
  {
    id: "sc-sf",
    profileId: "1.2",
    name: "SC-CF",
    description: "Monolithic superconducting compute and magic-state factory",
    modules: [
      {
        id: "sc-sf-mem",
        customName: "SC Memory",
        role: "memory",
        modality: "sc",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "1000",
      },
      {
        id: "sc-sf-comp",
        customName: "SC Compute",
        role: "compute",
        modality: "sc",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "100",
      },
      {
        id: "sc-sf-fact",
        customName: "SC Factory",
        role: "factory",
        modality: "sc",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "100",
      },
    ],
    links: [
      { sourceId: "sc-sf-mem", targetId: "sc-sf-comp" },
      { sourceId: "sc-sf-fact", targetId: "sc-sf-comp" },
    ],
  },
  {
    id: "ht-sf-macc",
    profileId: "1.3",
    name: "NA-C + SC-F",
    description: "Neutral-atom compute node linked to a superconducting factory node",
    modules: [
      {
        id: "ht-sf-mem",
        customName: "NA Memory",
        role: "memory",
        modality: "na",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "1000",
      },
      {
        id: "ht-sf-comp",
        customName: "NA Compute",
        role: "compute",
        modality: "na",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "100",
      },
      {
        id: "ht-sf-fact",
        customName: "SC Factory",
        role: "factory",
        modality: "sc",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "100",
      },
    ],
    links: [
      { sourceId: "ht-sf-mem", targetId: "ht-sf-comp" },
      { sourceId: "ht-sf-fact", targetId: "ht-sf-comp" },
    ],
  },
  // Group 2: qLDPC + Surface (MCSep)
  {
    id: "na-mcsep",
    profileId: "2.1",
    name: "NA-MCF",
    description: "Monolithic neutral-atom memory, compute, and factory",
    modules: [
      {
        id: "na-mcsep-mem",
        customName: "NA Memory (qLDPC)",
        role: "memory",
        modality: "na",
        qecType: "bb",
        distance: 0,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "1000",
      },
      {
        id: "na-mcsep-comp",
        customName: "NA Compute",
        role: "compute",
        modality: "na",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "100",
      },
      {
        id: "na-mcsep-fact",
        customName: "NA Factory",
        role: "factory",
        modality: "na",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "100",
      },
    ],
    links: [
      { sourceId: "na-mcsep-mem", targetId: "na-mcsep-comp" },
      { sourceId: "na-mcsep-fact", targetId: "na-mcsep-comp" },
    ],
  },
  {
    id: "ht-mcsep",
    profileId: "2.2",
    name: "NA-M + SC-CF",
    description: "Neutral-atom memory node linked to superconducting compute and factory",
    modules: [
      {
        id: "ht-mcsep-mem",
        customName: "NA Memory (qLDPC)",
        role: "memory",
        modality: "na",
        qecType: "bb",
        distance: 0,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "1000",
      },
      {
        id: "ht-mcsep-comp",
        customName: "SC Compute",
        role: "compute",
        modality: "sc",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "100",
      },
      {
        id: "ht-mcsep-fact",
        customName: "SC Factory",
        role: "factory",
        modality: "sc",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "100",
      },
    ],
    links: [
      { sourceId: "ht-mcsep-mem", targetId: "ht-mcsep-comp" },
      { sourceId: "ht-mcsep-fact", targetId: "ht-mcsep-comp" },
    ],
  },
  {
    id: "ht-mcsep-macc",
    profileId: "2.3",
    name: "NA-MC + SC-F",
    description: "Neutral-atom memory/compute node linked to a superconducting factory node",
    modules: [
      {
        id: "ht-mcsep-macc-mem",
        customName: "NA Memory (qLDPC)",
        role: "memory",
        modality: "na",
        qecType: "bb",
        distance: 0,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "1000",
      },
      {
        id: "ht-mcsep-macc-comp",
        customName: "NA Compute",
        role: "compute",
        modality: "na",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "100",
      },
      {
        id: "ht-mcsep-macc-fact",
        customName: "SC Factory",
        role: "factory",
        modality: "sc",
        qecType: "rotated-surface",
        distance: 17,
        bbParam: "[[144, 12, 12]]",
        msfProtocol: "15-to-1",
        physicalQubits: "600",
        unitThroughput: "1000",
        factoryBlocks: "4",
        capacity: "100",
      },
    ],
    links: [
      { sourceId: "ht-mcsep-macc-mem", targetId: "ht-mcsep-macc-comp" },
      { sourceId: "ht-mcsep-macc-fact", targetId: "ht-mcsep-macc-comp" },
    ],
  },
];

const ModalityIcon = ({ modality }: { modality: Modality }) => {
  if (modality === "sc") return <Zap className="w-3.5 h-3.5 text-primary" />;
  if (modality === "na") return <Atom className="w-3.5 h-3.5 text-primary" />;
  return <CircleDot className="w-3.5 h-3.5 text-primary" />;
};

function ModuleItem({
  module,
  onUpdate,
  onDelete,
}: {
  module: Module;
  onUpdate: (updated: Module) => void;
  onDelete: () => void;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [isEditingName, setIsEditingName] = useState(false);
  const [editName, setEditName] = useState(module.customName);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isEditingName && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.select();
    }
  }, [isEditingName]);

  const nkd = calculateNKD(module.qecType, module.distance, module.bbParam);
  const summaryText = `${module.customName} (${qecLabels[module.qecType]}, ${nkd})`;

  const handleNameSubmit = () => {
    onUpdate({ ...module, customName: editName || `${modalityLabels[module.modality]} ${roleLabels[module.role]}` });
    setIsEditingName(false);
  };

  const handleModalityChange = (m: Modality) => {
    const newName = `${modalityLabels[m]} ${roleLabels[module.role]}`;
    onUpdate({ ...module, modality: m, customName: newName });
    setEditName(newName);
  };

  const handleRoleChange = (r: Role) => {
    const newName = `${modalityLabels[module.modality]} ${roleLabels[r]}`;
    onUpdate({ ...module, role: r, customName: newName });
    setEditName(newName);
  };

  return (
    <Collapsible open={isOpen} onOpenChange={setIsOpen}>
      <div className="border border-border rounded-md bg-muted/20 overflow-hidden">
        <CollapsibleTrigger asChild>
          <button className="w-full flex items-center justify-between p-3 hover:bg-muted/30 transition-colors text-left gap-2">
            <div className="flex items-center gap-2 min-w-0 flex-1">
              <ModalityIcon modality={module.modality} />
              <div className="flex flex-col min-w-0 flex-1">
                {isEditingName ? (
                  <Input
                    ref={inputRef}
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onBlur={handleNameSubmit}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleNameSubmit();
                      if (e.key === "Escape") {
                        setEditName(module.customName);
                        setIsEditingName(false);
                      }
                    }}
                    onClick={(e) => e.stopPropagation()}
                    className="h-6 text-sm font-medium bg-muted/50 border-primary/50 px-1.5"
                  />
                ) : (
                  <span
                    className="text-sm font-medium text-foreground truncate cursor-text hover:text-primary transition-colors"
                    onClick={(e) => {
                      e.stopPropagation();
                      setIsEditingName(true);
                    }}
                  >
                    {module.customName}
                  </span>
                )}
                {module.role !== "factory" && (
                  <span className="text-xs text-muted-foreground break-words">
                    {qecLabels[module.qecType]}, {nkd}
                  </span>
                )}
              </div>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <div className="w-px h-4 bg-border" />
              <Button
                variant="ghost"
                size="icon"
                className="h-6 w-6 text-zinc-500 hover:text-red-500 transition-colors"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete();
                }}
              >
                <Trash2 className="w-3.5 h-3.5" />
              </Button>
              <ChevronDown
                className={`w-4 h-4 text-muted-foreground transition-transform ${isOpen ? "rotate-180" : ""}`}
              />
            </div>
          </button>
        </CollapsibleTrigger>

        <CollapsibleContent>
          <div className="p-3 pt-0 space-y-3 border-t border-border">
            {/* Role Select */}
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Role</Label>
              <Select value={module.role} onValueChange={(v) => handleRoleChange(v as Role)}>
                <SelectTrigger className="h-8 text-sm bg-muted/30 border-border">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-popover border-border">
                  <SelectItem value="compute">Compute</SelectItem>
                  <SelectItem value="memory">Memory</SelectItem>
                  <SelectItem value="factory">Magic State Factory</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Modality Select */}
            <div className="space-y-1.5">
              <Label className="text-xs text-muted-foreground">Modality</Label>
              <Select value={module.modality} onValueChange={(v) => handleModalityChange(v as Modality)}>
                <SelectTrigger className="h-8 text-sm bg-muted/30 border-border">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="bg-popover border-border">
                  <SelectItem value="sc">
                    <span className="flex items-center gap-2">
                      <Zap className="w-3 h-3" /> Superconducting
                    </span>
                  </SelectItem>
                  <SelectItem value="na">
                    <span className="flex items-center gap-2">
                      <Atom className="w-3 h-3" /> Neutral Atom
                    </span>
                  </SelectItem>
                  <SelectItem value="ti">
                    <span className="flex items-center gap-2">
                      <CircleDot className="w-3 h-3" /> Trapped Ion
                    </span>
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>

            {/* Dynamic Configuration */}
            {module.role !== "factory" ? (
              <>
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">QEC Code</Label>
                  <Select value={module.qecType} onValueChange={(v) => onUpdate({ ...module, qecType: v as QECType })}>
                    <SelectTrigger className="h-8 text-sm bg-muted/30 border-border">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="bg-popover border-border">
                      <SelectItem value="rotated-surface">Rotated Surface Code</SelectItem>
                      <SelectItem value="unrotated-surface">Unrotated Surface Code</SelectItem>
                      <SelectItem value="bb">BB Code</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {module.qecType === "bb" ? (
                  <div className="space-y-1.5">
                    <Label className="text-xs text-muted-foreground">Parameter Set</Label>
                    <Select
                      value={module.bbParam}
                      onValueChange={(v) => onUpdate({ ...module, bbParam: v as BBParam })}
                    >
                      <SelectTrigger className="h-8 text-sm bg-muted/30 border-border">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent className="bg-popover border-border">
                        <SelectItem value="[[144,12,12]]">[[144, 12, 12]]</SelectItem>
                        <SelectItem value="[[288,12,18]]">[[288, 12, 18]]</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                ) : (
                  <div className="space-y-1.5">
                    <Label className="text-xs text-muted-foreground">Distance (d)</Label>
                    <div className="flex items-center gap-2">
                      <Input
                        type="text"
                        inputMode="numeric"
                        value={module.distance === 0 ? "" : module.distance}
                        onChange={(e) => {
                          const raw = e.target.value;
                          if (raw === "") {
                            onUpdate({ ...module, distance: 0 });
                            return;
                          }
                          const val = parseInt(raw);
                          if (!isNaN(val) && val <= 51) {
                            onUpdate({ ...module, distance: val });
                          }
                        }}
                        onBlur={() => {
                          if (module.distance < 3) {
                            onUpdate({ ...module, distance: 3 });
                          }
                        }}
                        className="h-8 w-20 text-sm font-mono bg-muted/30 border-border [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                      />
                      <Badge
                        variant="secondary"
                        className="font-mono text-xs bg-quantum-cyan/10 text-quantum-cyan border-0 px-2"
                      >
                        {calculateNKD(module.qecType, module.distance || 3, module.bbParam)}
                      </Badge>
                    </div>
                  </div>
                )}

                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">Code Blocks</Label>
                  <Input
                    type="text"
                    value={module.capacity}
                    onChange={(e) => onUpdate({ ...module, capacity: e.target.value })}
                    className="h-8 text-sm font-mono bg-muted/30 border-border"
                  />
                </div>
              </>
            ) : (
              <>
                {/* Protocol Selection */}
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">Protocol</Label>
                  <Select
                    value={module.msfProtocol}
                    onValueChange={(v) => {
                      const protocol = v as MSFProtocol;
                      if (protocol !== "custom" && protocolDefaults[protocol]) {
                        onUpdate({
                          ...module,
                          msfProtocol: protocol,
                          physicalQubits: protocolDefaults[protocol].physicalQubits,
                          unitThroughput: protocolDefaults[protocol].unitThroughput,
                        });
                      } else {
                        onUpdate({ ...module, msfProtocol: protocol });
                      }
                    }}
                  >
                    <SelectTrigger className="h-8 text-sm bg-muted/30 border-border">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent className="bg-popover border-border">
                      <SelectItem value="15-to-1">15-to-1 Distillation</SelectItem>
                      <SelectItem value="cultivation">Cultivation</SelectItem>
                      <SelectItem value="custom">Custom</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                {/* Protocol Specifications */}
                <div className="p-2.5 bg-muted/20 rounded-md border border-border/50">
                  <Label className="text-xs text-muted-foreground font-medium block mb-2">
                    Protocol Specifications (per unit)
                  </Label>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <Label className="text-[10px] text-muted-foreground">Physical Qubits</Label>
                      <Input
                        type="text"
                        value={module.physicalQubits}
                        onChange={(e) => onUpdate({ ...module, physicalQubits: e.target.value })}
                        disabled={module.msfProtocol !== "custom"}
                        className="h-7 w-full text-xs font-mono bg-muted/30 border-border disabled:opacity-60"
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-[10px] text-muted-foreground">
                        Throughput <span className="text-muted-foreground/60">(states/s)</span>
                      </Label>
                      <Input
                        type="text"
                        value={module.unitThroughput}
                        onChange={(e) => onUpdate({ ...module, unitThroughput: e.target.value })}
                        disabled={module.msfProtocol !== "custom"}
                        className="h-7 w-full text-xs font-mono bg-muted/30 border-border disabled:opacity-60"
                      />
                    </div>
                  </div>
                </div>

                {/* Module Scale */}
                <div className="space-y-1.5">
                  <Label className="text-xs text-muted-foreground">Factory Blocks</Label>
                  <Input
                    type="text"
                    value={module.factoryBlocks}
                    onChange={(e) => onUpdate({ ...module, factoryBlocks: e.target.value })}
                    className="h-8 text-sm font-mono bg-muted/30 border-border"
                  />
                </div>
              </>
            )}
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  );
}

// Type for selected configs (can be preset or saved)
export interface SelectedConfig {
  id: string;
  /** Public ArqSim ArchitectureProfile selected by a canonical preset. */
  profileId?: string;
  name: string;
  modules: Module[];
  links: ModuleLink[];
  source: "preset" | "history";
}

interface ArchitectureComposerSectionProps {
  modules: Module[];
  onModulesChange: (modules: Module[]) => void;
  links: ModuleLink[];
  onLinksChange: Dispatch<SetStateAction<ModuleLink[]>>;
  selectedConfigs: SelectedConfig[];
  onSelectedConfigsChange: (configs: SelectedConfig[]) => void;
  onPreviewProfileChange: (profileId: string | null) => void;
}

export function ArchitectureComposerSection({
  modules,
  onModulesChange,
  links,
  onLinksChange,
  selectedConfigs,
  onSelectedConfigsChange,
  onPreviewProfileChange,
}: ArchitectureComposerSectionProps) {
  const [isComposing, setIsComposing] = useState(false);
  const [activeTab, setActiveTab] = useState<string>("presets");
  const [savedConfigs, setSavedConfigs] = useState<SavedConfig[]>([]);
  const [saveDialogOpen, setSaveDialogOpen] = useState(false);
  const [configName, setConfigName] = useState("");

  // Unified "viewing" state - only one config can be viewed at a time across all sections
  // This controls the purple highlight and canvas synchronization
  const [viewingConfigId, setViewingConfigId] = useState<string | null>("na-sf");
  const [viewingSource, setViewingSource] = useState<"preset" | "history" | "selected" | null>("preset");

  // Expanded state for viewing config details (independent of selection)
  const [expandedPresetId, setExpandedPresetId] = useState<string | null>(null);
  const [expandedHistoryId, setExpandedHistoryId] = useState<string | null>(null);

  // Track which history config is being edited (for save dialog)
  const [editingHistoryConfig, setEditingHistoryConfig] = useState<SavedConfig | null>(null);

  const addModule = () => {
    const newModule: Module = {
      id: Date.now().toString(),
      customName: "SC Compute",
      role: "compute",
      modality: "sc",
      qecType: "rotated-surface",
      distance: 11,
      bbParam: "[[144, 12, 12]]",
      msfProtocol: "15-to-1",
      physicalQubits: "600",
      unitThroughput: "1000",
      factoryBlocks: "4",
      capacity: "50",
    };
    onModulesChange([...modules, newModule]);
  };

  const updateModule = (id: string, updated: Module) => {
    onModulesChange(modules.map((m) => (m.id === id ? updated : m)));
  };

  const deleteModule = (id: string) => {
    onModulesChange(modules.filter((m) => m.id !== id));
  };

  const addPresetToSelection = (preset: PresetConfig) => {
    onPreviewProfileChange(preset.profileId);
    setViewingConfigId(preset.id);
    setViewingSource("preset");
    setExpandedPresetId(preset.id);
    setIsComposing(false);
    onModulesChange([]);
    onLinksChange([]);
    // Check if already selected
    if (selectedConfigs.find((c) => c.id === preset.id)) return;

    const newSelected: SelectedConfig = {
      id: preset.id,
      profileId: preset.profileId,
      name: preset.name,
      modules: preset.modules,
      links: preset.links,
      source: "preset",
    };
    onSelectedConfigsChange([...selectedConfigs, newSelected]);
  };

  const addSavedConfigToSelection = (config: SavedConfig) => {
    // Check if already selected
    if (selectedConfigs.find((c) => c.id === config.id)) return;

    const newSelected: SelectedConfig = {
      id: config.id,
      name: config.name,
      modules: config.modules,
      links: config.links,
      source: "history",
    };
    onSelectedConfigsChange([...selectedConfigs, newSelected]);
  };

  const loadConfigToCanvas = (configModules: Module[], configLinks: ModuleLink[]) => {
    // Generate new IDs for the modules and update links accordingly
    const idMap: Record<string, string> = {};
    const newModules = configModules.map((m) => {
      const newId = `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
      idMap[m.id] = newId;
      return { ...m, id: newId };
    });

    // Update links with new IDs
    const newLinks = configLinks.map((link) => ({
      sourceId: idMap[link.sourceId] || link.sourceId,
      targetId: idMap[link.targetId] || link.targetId,
    }));

    onModulesChange(newModules);
    onLinksChange(newLinks);
    setIsComposing(true);
  };

  // Helper to set viewing state and sync canvas
  const setViewingConfig = (configId: string | null, source: "preset" | "history" | "selected" | null, configModules?: Module[], configLinks?: ModuleLink[]) => {
    setViewingConfigId(configId);
    setViewingSource(source);

    // Clear other expanded states when viewing changes
    if (source !== "preset") setExpandedPresetId(null);
    if (source !== "history") setExpandedHistoryId(null);

    // Sync canvas if modules provided
    if (configModules && configLinks) {
      loadConfigToCanvas(configModules, configLinks);
    }
  };

  const removeConfigFromSelection = (configId: string) => {
    const filtered = selectedConfigs.filter((c) => c.id !== configId);
    onSelectedConfigsChange(filtered);

    // Clear viewing if removed
    if (viewingConfigId === configId && viewingSource === "selected") {
      setViewingConfigId(null);
      setViewingSource(null);
    }
  };

  const handleSave = () => {
    if (configName.trim() && modules.length > 0) {
      const newConfig: SavedConfig = {
        id: Date.now().toString(),
        name: configName.trim(),
        savedAt: new Date(),
        modules: [...modules],
        links: [...links],
      };
      setSavedConfigs([newConfig, ...savedConfigs]);
      setConfigName("");
      setSaveDialogOpen(false);
      setEditingHistoryConfig(null);
    }
  };

  const handleUpdateExisting = () => {
    if (editingHistoryConfig && modules.length > 0) {
      const updated: SavedConfig = {
        ...editingHistoryConfig,
        modules: [...modules],
        links: [...links],
        savedAt: new Date(),
      };
      setSavedConfigs(savedConfigs.map((c) => (c.id === editingHistoryConfig.id ? updated : c)));

      // Also update in selectedConfigs if present
      onSelectedConfigsChange(selectedConfigs.map((c) =>
        c.id === editingHistoryConfig.id
          ? { ...c, modules: [...modules], links: [...links] }
          : c
      ));

      setSaveDialogOpen(false);
      setEditingHistoryConfig(null);
      setConfigName("");
    }
  };

  const renderCompositionArea = () => (
    <div className="mt-3 space-y-3">
      {/* Add Module Button */}
      <Button
        variant="outline"
        className="w-full h-9 border-dashed border-border text-muted-foreground hover:text-foreground hover:border-primary/50"
        onClick={addModule}
      >
        <Plus className="w-4 h-4 mr-2" />
        Add Module
      </Button>

      {/* Module List */}
      <div className="space-y-2">
        {modules.map((module) => (
          <ModuleItem
            key={module.id}
            module={module}
            onUpdate={(updated) => updateModule(module.id, updated)}
            onDelete={() => deleteModule(module.id)}
          />
        ))}
      </div>

      {modules.length === 0 && (
        <div className="p-4 text-center text-xs text-muted-foreground border border-dashed border-border rounded-md">
          No modules configured. Add one to get started.
        </div>
      )}

      {/* Save Configuration Button */}
      {modules.length > 0 && (
        <Button
          variant="outline"
          className="w-full h-9 border-border text-foreground hover:bg-primary/10 hover:border-primary/50"
          onClick={() => setSaveDialogOpen(true)}
        >
          <Save className="w-4 h-4 mr-2" />
          Save Configuration
        </Button>
      )}
    </div>
  );

  return (
    <div className="space-y-3">
      {/* New Configuration Button - Above tabs */}
      <Button
        onClick={() => {
          onPreviewProfileChange(null);
          onModulesChange([]);
          onLinksChange([]);
          setIsComposing(true);
          setViewingConfigId(null);
          setViewingSource(null);
          setExpandedPresetId(null);
          setExpandedHistoryId(null);
        }}
        variant="outline"
        className="w-full h-10 border-primary/40 text-primary hover:bg-primary/10 hover:border-primary/60"
      >
        <FilePlus className="w-4 h-4 mr-2" />
        New Configuration
      </Button>

      {/* Tabs: Presets / History */}
      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList className="w-full bg-muted/50 p-1">
          <TabsTrigger
            value="presets"
            className="flex-1 text-xs data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            <LayoutGrid className="w-3 h-3 mr-1.5" />
            Presets
          </TabsTrigger>
          <TabsTrigger
            value="history"
            className="flex-1 text-xs data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            <Clock className="w-3 h-3 mr-1.5" />
            History
          </TabsTrigger>
        </TabsList>

        <TabsContent value="presets" className="mt-3">
          <div className="space-y-2">
            {presetConfigs.map((preset) => {
              const isSelected = selectedConfigs.some((c) => c.id === preset.id);
              const isExpanded = expandedPresetId === preset.id;
              const isViewing = viewingConfigId === preset.id && viewingSource === "preset";
              return (
                <div
                  key={preset.id}
                  className={`rounded-md border overflow-hidden transition-colors ${
                    isViewing
                      ? "border-primary bg-primary/10"
                      : "border-border bg-muted/20"
                  }`}
                >
                  {/* Clickable header row - toggles expand and syncs canvas */}
                  <div
                    className={`flex items-start gap-2 p-2.5 cursor-pointer transition-colors ${
                      isViewing ? "hover:bg-primary/15" : "hover:bg-muted/40"
                    }`}
                    onClick={() => {
                      const newExpandedId = isExpanded ? null : preset.id;
                      setExpandedPresetId(newExpandedId);

                      // Canonical presets preview their Profile hierarchy.
                      // They never enter the legacy flat module/link composer.
                      setViewingConfigId(preset.id);
                      setViewingSource("preset");
                      setIsComposing(false);
                      onPreviewProfileChange(preset.profileId);
                      onModulesChange([]);
                      onLinksChange([]);
                    }}
                  >
                    {/* Chevron indicator */}
                    <div className="mt-0.5 flex-shrink-0">
                      {isExpanded ? (
                        <ChevronDown className={`w-4 h-4 ${isViewing ? "text-primary" : "text-muted-foreground"}`} />
                      ) : (
                        <ChevronRight className={`w-4 h-4 ${isViewing ? "text-primary" : "text-muted-foreground"}`} />
                      )}
                    </div>

                    {/* Name and description */}
                    <div className="flex-1 min-w-0">
                      <div className={`text-sm font-medium ${isViewing ? "text-primary" : ""}`}>{preset.name}</div>
                      <div className="text-xs text-muted-foreground">{preset.description}</div>
                    </div>

                    {/* Select button */}
                    <Button
                      variant={isSelected ? "secondary" : "outline"}
                      size="sm"
                      className={`h-7 text-xs flex-shrink-0 ${
                        isSelected ? "opacity-60 cursor-not-allowed" : ""
                      }`}
                      disabled={isSelected}
                      onClick={(e) => {
                        e.stopPropagation();
                        addPresetToSelection(preset);
                      }}
                    >
                      {isSelected ? (
                        <>
                          <Check className="w-3 h-3 mr-1" />
                          Added
                        </>
                      ) : (
                        <>
                          <Plus className="w-3 h-3 mr-1" />
                          Select
                        </>
                      )}
                    </Button>
                  </div>

                  {/* Expanded content - module details */}
                  {isExpanded && (
                    <div className="px-2.5 pb-2.5 pt-0 border-t border-border/50">
                      <div className="mt-2 rounded-md border border-primary/20 bg-primary/5 p-2.5 text-xs text-muted-foreground">
                        Profile {preset.profileId} is shown on the canvas as the canonical
                        Node → Module → Submodule hierarchy. Capacities, QEC bindings, and
                        logical slots appear after evaluation resolves the workload.
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </TabsContent>

        <TabsContent value="history" className="mt-3">
          {savedConfigs.length === 0 ? (
            <div className="p-4 text-center text-xs text-muted-foreground border border-dashed border-border rounded-md">
              No saved configurations yet.
            </div>
          ) : (
            <div className="space-y-2">
              {savedConfigs.map((config) => {
                const isSelected = selectedConfigs.some((c) => c.id === config.id);
                const isExpanded = expandedHistoryId === config.id;
                const isViewing = viewingConfigId === config.id && viewingSource === "history";
                return (
                  <div
                    key={config.id}
                    className={`rounded-md border overflow-hidden transition-colors ${
                      isViewing
                        ? "border-primary bg-primary/10"
                        : "border-border bg-muted/20"
                    }`}
                  >
                    {/* Clickable header row - toggles expand and syncs canvas */}
                    <div
                      className={`flex items-start gap-2 p-2.5 cursor-pointer transition-colors ${
                        isViewing ? "hover:bg-primary/15" : "hover:bg-muted/40"
                      }`}
                      onClick={() => {
                        const newExpandedId = isExpanded ? null : config.id;
                        setExpandedHistoryId(newExpandedId);

                        // Toggle viewing state and sync canvas
                        if (!isExpanded) {
                          setViewingConfig(config.id, "history", config.modules, config.links);
                          // Track this as the config being edited
                          setEditingHistoryConfig(config);
                        } else {
                          setViewingConfig(null, null);
                          setEditingHistoryConfig(null);
                          setIsComposing(false);
                          onModulesChange([]);
                          onLinksChange([]);
                        }
                      }}
                    >
                      {/* Chevron indicator */}
                      <div className="mt-0.5 flex-shrink-0">
                        {isExpanded ? (
                          <ChevronDown className={`w-4 h-4 ${isViewing ? "text-primary" : "text-muted-foreground"}`} />
                        ) : (
                          <ChevronRight className={`w-4 h-4 ${isViewing ? "text-primary" : "text-muted-foreground"}`} />
                        )}
                      </div>

                      {/* Name and metadata */}
                      <div className="flex-1 min-w-0">
                        <div className={`text-sm font-medium ${isViewing ? "text-primary" : ""}`}>{config.name}</div>
                        <div className="text-xs text-muted-foreground">
                          {config.modules.length} module{config.modules.length !== 1 ? "s" : ""} • {config.savedAt.toLocaleDateString()}
                        </div>
                      </div>

                      {/* Select button */}
                      <Button
                        variant={isSelected ? "secondary" : "outline"}
                        size="sm"
                        className={`h-7 text-xs flex-shrink-0 ${
                          isSelected ? "opacity-60 cursor-not-allowed" : ""
                        }`}
                        disabled={isSelected}
                        onClick={(e) => {
                          e.stopPropagation();
                          addSavedConfigToSelection(config);
                        }}
                      >
                        {isSelected ? (
                          <>
                            <Check className="w-3 h-3 mr-1" />
                            Added
                          </>
                        ) : (
                          <>
                            <Plus className="w-3 h-3 mr-1" />
                            Select
                          </>
                        )}
                      </Button>
                    </div>

                    {/* Expanded content - module details */}
                    {isExpanded && (
                      <div className="px-2.5 pb-2.5 pt-0 border-t border-border/50">
                        {isViewing ? (
                          renderCompositionArea()
                        ) : (
                          <div className="mt-2 space-y-1.5">
                            <div className="text-xs text-muted-foreground font-medium">Modules:</div>
                            {config.modules.map((mod) => (
                              <div
                                key={mod.id}
                                className="flex items-center gap-2 p-2 bg-muted/30 rounded-md text-xs"
                              >
                                <ModalityIcon modality={mod.modality} />
                                <span className="font-medium">{mod.customName}</span>
                                <span className="text-muted-foreground">
                                  - {qecLabels[mod.qecType]}, d={mod.distance}
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* Composition Area */}
      {!isComposing ? (
        <div className="p-4 text-center border border-dashed border-border rounded-md bg-muted/10">
          <p className="text-xs text-muted-foreground">
            Click "New Configuration" above to start composing, or select a preset/history config.
          </p>
        </div>
      ) : (
        (!viewingConfigId || viewingSource === "selected") && renderCompositionArea()
      )}

      {/* Selected Arch Configs List - At the bottom */}
      <div className="space-y-2">
        <label className="text-xs text-muted-foreground font-medium">Selected Arch Configs</label>
        <div className="border border-border rounded-md bg-muted/20 overflow-hidden">
          {selectedConfigs.length === 0 ? (
            <div className="p-3 text-center text-xs text-muted-foreground">
              No configs selected. Add from Presets or History above.
            </div>
          ) : (
            <div className="divide-y divide-border max-h-[200px] overflow-y-auto">
              {selectedConfigs.map((config) => {
                const isViewing = viewingConfigId === config.id && viewingSource === "selected";
                return (
                  <div
                    key={config.id}
                    className={`p-2.5 cursor-pointer transition-colors ${
                      isViewing
                        ? "bg-primary/10 border-l-2 border-l-primary"
                        : "hover:bg-muted/30 border-l-2 border-l-transparent"
                    }`}
                      onClick={() => {
                        if (isViewing && !config.profileId) {
                          setViewingConfig(null, null);
                          onPreviewProfileChange(null);
                        setIsComposing(false);
                        onModulesChange([]);
                        onLinksChange([]);
                        } else {
                          if (config.profileId) {
                            setViewingConfigId(config.id);
                            setViewingSource("selected");
                            setIsComposing(false);
                            onPreviewProfileChange(config.profileId);
                            onModulesChange([]);
                            onLinksChange([]);
                          } else {
                            onPreviewProfileChange(null);
                            setViewingConfig(config.id, "selected", config.modules, config.links);
                          }
                        }
                    }}
                  >
                    {/* Row 1: Checkmark + Name + Remove button */}
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 min-w-0 flex-1">
                        {isViewing && (
                          <Check className="w-3 h-3 text-primary flex-shrink-0" />
                        )}
                        <span className={`text-sm font-medium truncate ${isViewing ? "text-primary" : ""}`}>{config.name}</span>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-5 w-5 text-muted-foreground hover:text-destructive flex-shrink-0"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeConfigFromSelection(config.id);
                        }}
                      >
                        <X className="w-3 h-3" />
                      </Button>
                    </div>
                    {/* Row 2: Modality icon + module count + source badge */}
                    <div className={`flex items-center gap-1.5 mt-1 ${isViewing ? "ml-5" : ""}`}>
                      <Badge
                        variant="secondary"
                        className="text-[10px] bg-muted/50 text-muted-foreground border-0 px-1.5 py-0"
                      >
                        {config.profileId
                          ? `Profile ${config.profileId}`
                          : `${config.modules.length} ${config.modules.length === 1 ? "module" : "modules"}`}
                      </Badge>
                      <Badge
                        variant="outline"
                        className="text-[10px] px-1.5 py-0 border-border"
                      >
                        {config.source === "preset" ? "Preset" : "History"}
                      </Badge>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* Save Dialog */}
      <Dialog open={saveDialogOpen} onOpenChange={(open) => {
        setSaveDialogOpen(open);
        if (!open) {
          setConfigName("");
        }
      }}>
        <DialogContent className="sm:max-w-[400px] bg-background border-border">
          <DialogHeader>
            <DialogTitle>Save Configuration</DialogTitle>
            <DialogDescription>
              {editingHistoryConfig
                ? `You're editing "${editingHistoryConfig.name}". Choose how to save your changes.`
                : "Give your architecture configuration a name to save it to history."
              }
            </DialogDescription>
          </DialogHeader>
          <div className="py-4 space-y-4">
            {/* Option 1: Update existing (only for History configs) */}
            {editingHistoryConfig && (
              <Button
                variant="outline"
                className="w-full h-12 justify-start gap-3 border-border hover:bg-primary/10 hover:border-primary/50"
                onClick={handleUpdateExisting}
              >
                <Edit2 className="w-4 h-4 text-primary" />
                <div className="text-left">
                  <div className="text-sm font-medium">Update "{editingHistoryConfig.name}"</div>
                  <div className="text-xs text-muted-foreground">Overwrite the existing configuration</div>
                </div>
              </Button>
            )}

            {/* Option 2: Save as new */}
            <div className="space-y-2">
              {editingHistoryConfig && (
                <div className="text-xs text-muted-foreground font-medium">Or save as a new configuration:</div>
              )}
              <div className="space-y-2">
                <Label htmlFor="config-name" className="text-sm">
                  {editingHistoryConfig ? "New Name" : "Configuration Name"}
                </Label>
                <Input
                  id="config-name"
                  value={configName}
                  onChange={(e) => setConfigName(e.target.value)}
                  placeholder="e.g., My Hybrid Architecture"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && configName.trim()) handleSave();
                  }}
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSaveDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={!configName.trim()}>
              <FilePlus className="w-4 h-4 mr-2" />
              Save as New
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
