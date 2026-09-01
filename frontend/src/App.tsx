import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import Index from "./pages/Index";
import NotFound from "./pages/NotFound";

const App = () => {
  const page = window.location.pathname === "/" ? <Index /> : <NotFound />;

  return (
    <TooltipProvider>
      <Toaster />
      <Sonner />
      {page}
    </TooltipProvider>
  );
};

export default App;
