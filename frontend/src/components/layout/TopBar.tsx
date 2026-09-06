import {
  Play,
  PanelLeft,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface TopBarProps {
  onToggleSidebar: () => void;
  onRunEvaluation?: () => void;
  isEvaluating?: boolean;
}

export function TopBar({
  onToggleSidebar,
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

        <div>
          <div className="text-sm font-semibold text-foreground">
            ArqSim
          </div>
          <div className="font-mono text-[10px] text-muted-foreground">
            Experiment setup
          </div>
        </div>
      </div>

      <div className="flex items-center">
        <Button
          onClick={handleRunEvaluation}
          disabled={isEvaluating}
          className="bg-primary hover:bg-primary/90 text-primary-foreground glow-purple transition-all duration-300"
        >
          <Play className={`w-4 h-4 mr-2 ${isEvaluating ? "animate-pulse" : ""}`} />
          {isEvaluating ? "Running..." : "Run Evaluation"}
        </Button>
      </div>
    </header>
  );
}
