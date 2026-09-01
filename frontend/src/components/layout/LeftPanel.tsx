import { useState } from "react";
import { ChevronLeft, Cpu, Server, Layers, FlaskConical } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { QuantumProgramSection } from "@/components/config/QuantumProgramSection";
import { DeviceLibrarySection, DeviceLibraryParams } from "@/components/config/DeviceLibrarySection";
import { ArchitectureComposerSection, SelectedConfig } from "@/components/config/ArchitectureComposerSection";
import { ExperimentSetupSection } from "@/components/config/ExperimentSetupSection";
import type { ExperimentSetupParams } from "@/types/experiment";
import { Module, ModuleLink } from "@/types/module";
import { Benchmark } from "@/services/api";
import { Dispatch, SetStateAction } from "react";

interface LeftPanelProps {
  isCollapsed: boolean;
  onToggleCollapse: () => void;
  modules: Module[];
  onModulesChange: (modules: Module[]) => void;
  links: ModuleLink[];
  onLinksChange: Dispatch<SetStateAction<ModuleLink[]>>;
  selectedPrograms: Benchmark[];
  onSelectedProgramsChange: (programs: Benchmark[]) => void;
  activeProgram: string;
  onActiveProgramChange: (programId: string) => void;
  selectedConfigs: SelectedConfig[];
  onSelectedConfigsChange: (configs: SelectedConfig[]) => void;
  onPreviewProfileChange: (profileId: string | null) => void;
  deviceParams: DeviceLibraryParams;
  onDeviceParamsChange: (p: DeviceLibraryParams) => void;
  experimentParams: ExperimentSetupParams;
  onExperimentParamsChange: (p: ExperimentSetupParams) => void;
}

export function LeftPanel({
  isCollapsed,
  onToggleCollapse,
  modules,
  onModulesChange,
  links,
  onLinksChange,
  selectedPrograms,
  onSelectedProgramsChange,
  activeProgram,
  onActiveProgramChange,
  selectedConfigs,
  onSelectedConfigsChange,
  onPreviewProfileChange,
  deviceParams,
  onDeviceParamsChange,
  experimentParams,
  onExperimentParamsChange,
}: LeftPanelProps) {
  const [openSections, setOpenSections] = useState<string[]>(["quantum-program", "device-library", "architecture"]);

  const sections = [
    { id: "quantum-program",  title: "Quantum Program",       icon: Cpu },
    { id: "device-library",   title: "Device Library",        icon: Server },
    { id: "architecture",     title: "Architecture Composer", icon: Layers },
    { id: "experiment-setup", title: "Advanced",              icon: FlaskConical },
  ];

  // Stable rendering logic prevents unmounting on prop changes
  const renderSectionContent = (id: string) => {
    switch (id) {
      case "quantum-program":
        return (
          <QuantumProgramSection
            selectedPrograms={selectedPrograms}
            onSelectedProgramsChange={onSelectedProgramsChange}
            activeProgram={activeProgram}
            onActiveProgramChange={onActiveProgramChange}
          />
        );
      case "experiment-setup":
        return <ExperimentSetupSection params={experimentParams} onParamsChange={onExperimentParamsChange} />;
      case "device-library":
        return <DeviceLibrarySection params={deviceParams} onParamsChange={onDeviceParamsChange} />;
      case "architecture":
        return (
          <ArchitectureComposerSection
            modules={modules}
            onModulesChange={onModulesChange}
            links={links}
            onLinksChange={onLinksChange}
            selectedConfigs={selectedConfigs}
            onSelectedConfigsChange={onSelectedConfigsChange}
            onPreviewProfileChange={onPreviewProfileChange}
          />
        );
      default:
        return null;
    }
  };

  return (
    <div
      className="relative h-full w-full bg-secondary border-r border-border flex flex-col"
    >
      {/* Toggle Button */}
      <Button
        variant="ghost"
        size="icon"
        onClick={onToggleCollapse}
        className="absolute top-4 -right-3 z-10 h-6 w-6 rounded-full bg-muted hover:bg-muted/80"
      >
        <ChevronLeft className="w-4 h-4" />
      </Button>

      {/* Header */}
      <div className="p-4 border-b border-border">
        <h2 className="text-sm font-semibold text-foreground uppercase tracking-wider">
          Experiment Setup
        </h2>
      </div>

      {/* Accordion Sections */}
      <div className="flex-1 overflow-y-auto">
        <Accordion
          type="multiple"
          value={openSections}
          onValueChange={setOpenSections}
          className="w-full"
        >
          {sections.map((section) => (
            <AccordionItem
              key={section.id}
              value={section.id}
              className="border-b border-border"
            >
              <AccordionTrigger className="px-4 py-3 hover:bg-muted/30 hover:no-underline">
                <div className="flex items-center gap-3">
                  <div className="p-1.5 rounded-md bg-primary/10 text-primary">
                    <section.icon className="w-3.5 h-3.5" />
                  </div>
                  <span className="text-sm font-medium">{section.title}</span>
                </div>
              </AccordionTrigger>
              <AccordionContent className="px-4 pb-4">
                {renderSectionContent(section.id)}
              </AccordionContent>
            </AccordionItem>
          ))}
        </Accordion>
      </div>
    </div>
  );
}
