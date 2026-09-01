import { useState, useEffect, useCallback } from "react";
import { FileCode, Upload, X, Plus, Check, Loader2 } from "lucide-react";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { api, Benchmark } from "@/services/api";

interface QuantumProgramSectionProps {
  selectedPrograms: Benchmark[];
  onSelectedProgramsChange: (programs: Benchmark[]) => void;
  activeProgram: string;
  onActiveProgramChange: (programId: string) => void;
}

function numericTCount(value: string): number | null {
  const normalized = value.replace(/,/g, "");
  if (!/^\d+$/.test(normalized)) return null;
  return Number(normalized);
}

function isLargeInteractiveWorkload(benchmark: Benchmark): boolean {
  const tCount = numericTCount(benchmark.tGates);
  return benchmark.depth >= 5_000 || (tCount !== null && tCount >= 10_000);
}

export function QuantumProgramSection({
  selectedPrograms,
  onSelectedProgramsChange,
  activeProgram,
  onActiveProgramChange
}: QuantumProgramSectionProps) {
  const [mode, setMode] = useState<string>("benchmarks");
  const [benchmarks, setBenchmarks] = useState<Record<string, Benchmark[]>>({
    arithmetic: [],
    qft: [],
    simulation: [],
    cryptography: [],
    other: []
  });
  const [allBenchmarks, setAllBenchmarks] = useState<Benchmark[]>([]);
  const [addingProgram, setAddingProgram] = useState(false);
  const [programToAdd, setProgramToAdd] = useState<string>("");
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const fetchBenchmarks = useCallback(async () => {
    setLoadError(null);
    try {
      const data = await api.getBenchmarks();
      setAllBenchmarks(data);

      const grouped: Record<string, Benchmark[]> = {
        arithmetic: [],
        qft: [],
        simulation: [],
        cryptography: [],
        other: []
      };

      data.forEach(b => {
        if (grouped[b.category]) {
          grouped[b.category].push(b);
        } else {
          grouped.other.push(b);
        }
      });

      setBenchmarks(grouped);

      if (data.length > 0 && selectedPrograms.length === 0) {
        const defaultBench =
          data.find(b => b.id === "arqsim_timeline_demo") ||
          data.find(b => b.id === "adder_n64") ||
          data[0];
        onSelectedProgramsChange([defaultBench]);
        onActiveProgramChange(defaultBench.id);
      }
    } catch (error) {
      console.error("Failed to fetch benchmarks:", error);
      setLoadError("Benchmarks unavailable. Check that the backend is running.");
      setAllBenchmarks([]);
      setBenchmarks({
        arithmetic: [],
        qft: [],
        simulation: [],
        cryptography: [],
        other: []
      });
    } finally {
      setIsLoading(false);
    }
  }, [onActiveProgramChange, onSelectedProgramsChange, selectedPrograms.length]);

  useEffect(() => {
    fetchBenchmarks();
  }, [fetchBenchmarks]);

  const handleOpenAddProgram = async () => {
    setAddingProgram(true);
    if (allBenchmarks.length === 0) {
      setIsLoading(true);
      await fetchBenchmarks();
    }
  };

  const handleAddProgram = () => {
    if (programToAdd) {
      const program = allBenchmarks.find((b) => b.id === programToAdd);
      if (program && !selectedPrograms.find((p) => p.id === program.id)) {
        onSelectedProgramsChange([...selectedPrograms, program]);
        if (selectedPrograms.length === 0) {
          onActiveProgramChange(program.id);
        }
      }
      setProgramToAdd("");
      setAddingProgram(false);
    }
  };

  const handleRemoveProgram = (id: string) => {
    const filtered = selectedPrograms.filter((p) => p.id !== id);
    onSelectedProgramsChange(filtered);
    if (activeProgram === id && filtered.length > 0) {
      onActiveProgramChange(filtered[0].id);
    } else if (filtered.length === 0) {
      onActiveProgramChange("");
    }
  };

  const handleSetActive = (id: string) => {
    onActiveProgramChange(id);
  };

  const activeDetails = selectedPrograms.find((p) => p.id === activeProgram);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-32 text-muted-foreground">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        <span>Loading benchmarks...</span>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Mode Toggle */}
      <ToggleGroup
        type="single"
        value={mode}
        onValueChange={(v) => v && setMode(v)}
        className="w-full bg-muted/50 p-1 rounded-md"
      >
        <ToggleGroupItem
          value="benchmarks"
          className="flex-1 text-xs data-[state=on]:bg-primary data-[state=on]:text-primary-foreground"
        >
          <FileCode className="w-3 h-3 mr-1.5" />
          Benchmarks
        </ToggleGroupItem>
        <ToggleGroupItem
          value="upload"
          className="flex-1 text-xs data-[state=on]:bg-primary data-[state=on]:text-primary-foreground"
        >
          <Upload className="w-3 h-3 mr-1.5" />
          Upload QASM
        </ToggleGroupItem>
      </ToggleGroup>

      {mode === "benchmarks" ? (
        <>
          {/* Selected Programs List */}
          <div className="space-y-2">
            <label className="text-xs text-muted-foreground font-medium">Selected Programs</label>
            <div className="border border-border rounded-md bg-muted/20 overflow-hidden">
              {selectedPrograms.length === 0 ? (
                <div className="p-3 text-center text-xs text-muted-foreground">
                  No programs selected. Add one below.
                </div>
              ) : (
                <ScrollArea className="max-h-[140px]">
                  <div className="divide-y divide-border">
                    {selectedPrograms.map((program) => (
                      <div
                        key={program.id}
                        className={`flex items-center justify-between p-2.5 cursor-pointer transition-colors ${
                          activeProgram === program.id
                            ? "bg-primary/10 border-l-2 border-l-primary"
                            : "hover:bg-muted/30 border-l-2 border-l-transparent"
                        }`}
                        onClick={() => handleSetActive(program.id)}
                      >
                        <div className="flex items-center gap-2 min-w-0 flex-1">
                          {activeProgram === program.id && (
                            <Check className="w-3 h-3 text-primary flex-shrink-0" />
                          )}
                          <span className="text-sm font-medium truncate">{program.name}</span>
                          <Badge
                            variant="secondary"
                            className="text-[10px] bg-muted/50 text-muted-foreground border-0 px-1.5 py-0"
                          >
                            {program.tGates} T
                          </Badge>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-5 w-5 text-muted-foreground hover:text-red-500 flex-shrink-0"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleRemoveProgram(program.id);
                          }}
                        >
                          <X className="w-3 h-3" />
                        </Button>
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              )}
            </div>
          </div>

          {/* Add Program */}
          {addingProgram ? (
            <div className="space-y-2">
              <Select value={programToAdd} onValueChange={setProgramToAdd}>
                <SelectTrigger className="w-full h-9 text-sm bg-muted/30 border-border">
                  <SelectValue placeholder="Select benchmark" />
                </SelectTrigger>
                <SelectContent className="z-[1000] bg-popover border-border">
                  {allBenchmarks.length === 0 ? (
                    <div className="px-3 py-2 text-xs text-muted-foreground">
                      No benchmarks loaded.
                    </div>
                  ) : (
                    Object.entries(benchmarks).map(([category, items]) => (
                      items.length > 0 && (
                        <SelectGroup key={category}>
                          <SelectLabel className="text-xs text-muted-foreground capitalize">{category}</SelectLabel>
                          {items.map((b) => (
                            <SelectItem
                              key={b.id}
                              value={b.id}
                              className="text-sm"
                              disabled={selectedPrograms.some((p) => p.id === b.id)}
                          >
                              {b.name} · {b.tGates} T · depth {b.depth.toLocaleString()}
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      )
                    ))
                  )}
                </SelectContent>
              </Select>
              {loadError && (
                <div className="text-xs text-destructive">{loadError}</div>
              )}
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  className="flex-1 h-8 text-xs"
                  onClick={() => {
                    setAddingProgram(false);
                    setProgramToAdd("");
                  }}
                >
                  Cancel
                </Button>
                <Button
                  size="sm"
                  className="flex-1 h-8 text-xs"
                  onClick={handleAddProgram}
                  disabled={!programToAdd}
                >
                  Add
                </Button>
              </div>
            </div>
          ) : (
            <Button
              variant="outline"
              className="w-full h-8 text-xs border-dashed border-border text-muted-foreground hover:text-foreground hover:border-primary/50"
              onClick={handleOpenAddProgram}
            >
              <Plus className="w-3 h-3 mr-1.5" />
              Add Program
            </Button>
          )}

          {/* Active Program Info */}
          {activeDetails && (
            <div className="p-3 rounded-md bg-muted/30 border border-border space-y-2">
              <div className="text-xs font-medium text-foreground mb-2">
                Active: {activeDetails.name}
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">T gates</span>
                <Badge variant="secondary" className="font-mono text-xs bg-quantum-cyan/10 text-quantum-cyan border-0">
                  {activeDetails.tGates}
                </Badge>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Circuit Depth</span>
                <Badge variant="secondary" className="font-mono text-xs bg-quantum-purple/10 text-quantum-purple border-0">
                  {activeDetails.depth}
                </Badge>
              </div>
              {isLargeInteractiveWorkload(activeDetails) ? (
                <p className="rounded-md border border-amber-400/25 bg-amber-400/5 px-2 py-1.5 text-[10px] leading-relaxed text-amber-200/80">
                  Large analytical workload: the complete causal report can take minutes.
                  The timeline view remains capped, but evaluation itself is not truncated.
                </p>
              ) : null}
            </div>
          )}
        </>
      ) : (
        <div className="p-6 border-2 border-dashed border-border rounded-lg text-center">
          <Upload className="w-8 h-8 mx-auto mb-2 text-muted-foreground" />
          <p className="text-xs text-muted-foreground">
            Drop .qasm file or click to browse
          </p>
        </div>
      )}
    </div>
  );
}
