import { useState } from "react";
import {
  ChevronDown,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  Box,
  Square,
  Play,
  Settings,
  User,
  PanelLeft,
  Cable,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { InteractionMode } from "@/types/module";

interface TopBarProps {
  is3D: boolean;
  onToggle3D: () => void;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onResetView: () => void;
  onToggleSidebar: () => void;
  interactionMode: InteractionMode;
  onToggleInteractionMode: () => void;
  onRunEvaluation?: () => void;
  isEvaluating?: boolean;
}

export function TopBar({
  is3D,
  onToggle3D,
  onZoomIn,
  onZoomOut,
  onResetView,
  onToggleSidebar,
  interactionMode,
  onToggleInteractionMode,
  onRunEvaluation,
  isEvaluating = false,
}: TopBarProps) {

  const handleRunEvaluation = () => {
    if (onRunEvaluation) {
      onRunEvaluation();
    }
  };

  return (
    <header className="relative z-50 h-[60px] flex items-center justify-between px-4 border-b border-border bg-secondary/50 backdrop-blur-sm">
      {/* Left: Sidebar Toggle + Product Switcher */}
      <div className="flex items-center gap-3">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-9 w-9 hover:bg-muted"
              onClick={onToggleSidebar}
            >
              <PanelLeft className="w-5 h-5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Toggle Sidebar</TooltipContent>
        </Tooltip>

        <div className="flex items-center gap-2 cursor-pointer group">
          <span className="text-muted-foreground group-hover:text-foreground transition-colors">
            ArqSim · Architecture Execution Explorer
          </span>
          <ChevronDown className="w-4 h-4 text-muted-foreground group-hover:text-foreground transition-colors" />
        </div>
      </div>

      {/* Center: Canvas Controls */}
      <div className="flex items-center gap-1 bg-muted/50 rounded-md p-1">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 hover:bg-muted"
              onClick={onZoomIn}
            >
              <ZoomIn className="w-4 h-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Zoom In</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 hover:bg-muted"
              onClick={onZoomOut}
            >
              <ZoomOut className="w-4 h-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Zoom Out</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8 hover:bg-muted"
              onClick={onResetView}
            >
              <RotateCcw className="w-4 h-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Reset View</TooltipContent>
        </Tooltip>

        <div className="w-px h-5 bg-border mx-1" />

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className={`h-8 w-8 hover:bg-muted ${is3D ? "text-quantum-cyan" : ""}`}
              onClick={onToggle3D}
            >
              {is3D ? <Box className="w-4 h-4" /> : <Square className="w-4 h-4" />}
            </Button>
          </TooltipTrigger>
          <TooltipContent>{is3D ? "Switch to 2D" : "Switch to 3D"}</TooltipContent>
        </Tooltip>

        <div className="w-px h-5 bg-border mx-1" />

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className={`h-8 w-8 hover:bg-muted ${interactionMode === "connect" ? "text-primary bg-primary/20" : ""}`}
              onClick={onToggleInteractionMode}
            >
              <Cable className="w-4 h-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>{interactionMode === "connect" ? "Switch to Drag Mode" : "Switch to Connect Mode"}</TooltipContent>
        </Tooltip>
      </div>

      {/* Right: Global Actions */}
      <div className="flex items-center gap-3">
        <Button
          onClick={handleRunEvaluation}
          disabled={isEvaluating}
          className="bg-primary hover:bg-primary/90 text-primary-foreground glow-purple transition-all duration-300"
        >
          <Play className={`w-4 h-4 mr-2 ${isEvaluating ? "animate-pulse" : ""}`} />
          {isEvaluating ? "Running..." : "Run Evaluation"}
        </Button>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" className="h-9 w-9 hover:bg-muted">
              <Settings className="w-5 h-5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Settings</TooltipContent>
        </Tooltip>

        <div className="w-9 h-9 rounded-full bg-gradient-to-br from-primary to-quantum-cyan flex items-center justify-center cursor-pointer hover:opacity-90 transition-opacity">
          <User className="w-5 h-5 text-primary-foreground" />
        </div>
      </div>
    </header>
  );
}
