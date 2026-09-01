import { Suspense, useState, useRef, useCallback, useEffect, Dispatch, SetStateAction } from "react";
import { Canvas } from "@react-three/fiber";
import { MapControls, Grid, OrthographicCamera, Environment } from "@react-three/drei";
import { EffectComposer, Bloom, Vignette } from "@react-three/postprocessing";
import { Atom } from "lucide-react";
import * as THREE from "three";
import type { MapControls as MapControlsImpl } from "three-stdlib";
import { Module, ModulePosition, ModuleLink, InteractionMode } from "@/types/module";
import { ModuleChip } from "@/components/canvas/ModuleChip";
import { ConnectionLine } from "@/components/canvas/ConnectionLine";
import { ArchitectureHierarchy } from "@/components/architecture/ArchitectureHierarchy";
import type { ArchitectureHierarchyViewModel } from "@/types/evaluationReport";
import { toast } from "@/hooks/use-toast";

interface MainCanvasProps {
  is3D: boolean;
  modules: Module[];
  interactionMode: InteractionMode;
  links: ModuleLink[];
  setLinks: Dispatch<SetStateAction<ModuleLink[]>>;
  /** Resolved report view. When present it replaces the authoring canvas. */
  architecture?: ArchitectureHierarchyViewModel | null;
  /** Canonical profile catalog is loading; do not flash the legacy composer. */
  architectureLoading?: boolean;
  architectureError?: string | null;
}

function Scene({
  modules,
  positions,
  onPositionChange,
  isDragging,
  setIsDragging,
  draggingChipId,
  setDraggingChipId,
  interactionMode,
  selectedForLinking,
  recentlyConnected,
  onModuleClick,
  links,
}: {
  modules: Module[];
  positions: ModulePosition[];
  onPositionChange: (id: string, x: number, z: number) => void;
  isDragging: boolean;
  setIsDragging: (dragging: boolean) => void;
  draggingChipId: string | null;
  setDraggingChipId: (id: string | null) => void;
  interactionMode: InteractionMode;
  selectedForLinking: string[];
  recentlyConnected: string[];
  onModuleClick: (moduleId: string) => void;
  links: ModuleLink[];
}) {
  const controlsRef = useRef<MapControlsImpl>(null);

  // Disable controls when dragging a chip
  useEffect(() => {
    if (controlsRef.current) {
      controlsRef.current.enabled = !isDragging;
    }
  }, [isDragging]);

  return (
    <>
      {/* Isometric Orthographic Camera - research lab perspective */}
      <OrthographicCamera makeDefault position={[20, 20, 20]} zoom={45} near={-1000} far={2000} />

      {/* Map Controls - damped and restrained, disabled during chip drag */}
      <MapControls
        ref={controlsRef}
        enableRotate={false}
        enableDamping
        dampingFactor={0.08}
        minZoom={15}
        maxZoom={150}
        screenSpacePanning
        enabled={!isDragging}
      />

      {/* HDR Environment for IBL metallic reflections on chips */}
      <Environment preset="studio" environmentIntensity={0.25} />

      {/* Balanced Cinematic Lighting */}

      {/* Higher ambient for visible grid and environment */}
      <ambientLight intensity={0.25} color="#2a2a4e" />

      {/* Key light - strong directional for specular highlights */}
      <directionalLight
        position={[8, 15, 5]}
        intensity={1.6}
        color="#e0e7ff"
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-far={50}
        shadow-camera-left={-15}
        shadow-camera-right={15}
        shadow-camera-top={15}
        shadow-camera-bottom={-15}
        shadow-bias={-0.0001}
      />

      {/* Rim / back light - outlines chip silhouette */}
      <directionalLight position={[-10, 8, -8]} intensity={0.8} color="#3b82f6" />

      {/* Subtle fill light from below */}
      <pointLight position={[0, -5, 0]} intensity={0.3} color="#22d3ee" distance={20} decay={2} />

      {/* Accent spot for quantum glow effect */}
      <spotLight
        position={[0, 10, 0]}
        intensity={0.5}
        color="#8b5cf6"
        angle={0.5}
        penumbra={1}
        distance={25}
        decay={2}
      />

      {/* Minimal grid - research lab floor - brighter colors */}
      <Grid
        args={[100, 100]}
        cellSize={1}
        cellThickness={0.4}
        cellColor="#2a2a35"
        sectionSize={5}
        sectionThickness={0.8}
        sectionColor="#3a3a48"
        fadeDistance={50}
        fadeStrength={1.0}
        infiniteGrid
        position={[0, -0.2, 0]}
      />

      {/* Subtle quantum field grid overlay - more visible */}
      <Grid
        args={[100, 100]}
        cellSize={5}
        cellThickness={0.25}
        cellColor="#3b82f625"
        sectionSize={10}
        sectionThickness={0.5}
        sectionColor="#22d3ee20"
        fadeDistance={40}
        fadeStrength={1.2}
        infiniteGrid
        position={[0, -0.18, 0]}
      />

      {/* Render Module Chips */}
      {modules.map((module) => {
        const pos = positions.find((p) => p.id === module.id);
        const x = pos?.x ?? 0;
        const z = pos?.z ?? 0;
        const isSelected = selectedForLinking.includes(module.id);
        const isRecentlyConnected = recentlyConnected.includes(module.id);

        return (
          <ModuleChip
            key={module.id}
            module={module}
            position={[x, 0.2, z]}
            onPositionChange={onPositionChange}
            allPositions={positions}
            setIsDragging={setIsDragging}
            setDraggingChipId={setDraggingChipId}
            interactionMode={interactionMode}
            isSelected={isSelected}
            isRecentlyConnected={isRecentlyConnected}
            onClick={() => onModuleClick(module.id)}
          />
        );
      })}

      {/* Render Connection Lines */}
      {links.map((link) => {
        const sourcePos = positions.find((p) => p.id === link.sourceId);
        const targetPos = positions.find((p) => p.id === link.targetId);
        const sourceModule = modules.find((m) => m.id === link.sourceId);
        const targetModule = modules.find((m) => m.id === link.targetId);

        if (!sourcePos || !targetPos || !sourceModule || !targetModule) return null;

        // Calculate chip radius using the same scale formula as ModuleChip
        const getChipRadius = (capacity: string) => {
          const capacityNum = parseInt(capacity, 10) || 1;
          const scale = 1.2 + Math.log10(Math.max(1, capacityNum)) * 0.4;
          const chipSize = scale * 1.2; // sizeMultiplier from CHIP_CONFIG
          return chipSize * 0.5; // Half of chipSize for radius
        };

        const sourceRadius = getChipRadius(sourceModule.factoryBlocks);
        const targetRadius = getChipRadius(targetModule.factoryBlocks);

        // Calculate direction vector from source to target
        const dx = targetPos.x - sourcePos.x;
        const dz = targetPos.z - sourcePos.z;
        const distance = Math.sqrt(dx * dx + dz * dz);

        if (distance < 0.01) return null; // Skip if same position

        const dirX = dx / distance;
        const dirZ = dz / distance;

        // Connection height at side surface of base layer
        // Base layer: y starts at 0.2 (chip position) + 0.02 (baseYOffset) with height 0.08
        // Side surface is at mid-height of base: 0.2 + 0.02 + 0.08/2 = 0.26
        // When lifted, add 0.5 to the Y position
        const liftAmount = 0.5;
        const baseConnectionY = 0.26;
        const sourceIsLifted = draggingChipId === link.sourceId;
        const targetIsLifted = draggingChipId === link.targetId;
        const sourceY = baseConnectionY + (sourceIsLifted ? liftAmount : 0);
        const targetY = baseConnectionY + (targetIsLifted ? liftAmount : 0);

        // Calculate edge points at chip boundary (not inside)
        const startX = sourcePos.x + dirX * sourceRadius;
        const startZ = sourcePos.z + dirZ * sourceRadius;
        const endX = targetPos.x - dirX * targetRadius;
        const endZ = targetPos.z - dirZ * targetRadius;

        return (
          <ConnectionLine
            key={`${link.sourceId}-${link.targetId}`}
            start={[startX, sourceY, startZ]}
            end={[endX, targetY, endZ]}
            color="#ff8b8b"
          />
        );
      })}

      {/* Post-processing effects */}
      <EffectComposer>
        {/* Bloom for emissive quantum glow */}
        <Bloom intensity={0.5} luminanceThreshold={0.2} luminanceSmoothing={0.9} mipmapBlur />
        {/* Subtle vignette for cinematic feel */}
        <Vignette offset={0.3} darkness={0.4} eskil={false} />
      </EffectComposer>
    </>
  );
}

function LoadingFallback() {
  return (
    <div className="w-full h-full flex items-center justify-center">
      <div className="animate-pulse text-muted-foreground">Loading 3D Scene...</div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="w-full h-full flex items-center justify-center">
      <div className="text-center max-w-md">
        <div className="w-20 h-20 mx-auto mb-6 rounded-2xl bg-muted/50 border border-border flex items-center justify-center">
          <Atom className="w-10 h-10 text-primary animate-pulse-glow" />
        </div>
        <h3 className="text-lg font-medium text-foreground mb-2">Configure parameters to generate architecture</h3>
        <p className="text-sm text-muted-foreground">
          Select a <span className="text-foreground font-medium">Quantum Program</span> and add modules via the{" "}
          <span className="text-foreground font-medium">Architecture Composer</span> to begin visualization.
        </p>
      </div>
    </div>
  );
}

function ComposerCanvas({ is3D, modules, interactionMode, links, setLinks }: MainCanvasProps) {
  // Track positions for each module
  const [positions, setPositions] = useState<ModulePosition[]>([]);
  // Track if any chip is being dragged
  const [isDragging, setIsDragging] = useState(false);
  // Track which chip is currently being dragged (for link lift)
  const [draggingChipId, setDraggingChipId] = useState<string | null>(null);
  // Track selected modules for linking
  const [selectedForLinking, setSelectedForLinking] = useState<string[]>([]);
  // Track recently connected modules for fade-out animation
  const [recentlyConnected, setRecentlyConnected] = useState<string[]>([]);

  // Clear selection when switching modes
  useEffect(() => {
    if (interactionMode === "drag") {
      setSelectedForLinking([]);
    }
  }, [interactionMode]);

  // Handle module click for linking
  const handleModuleClick = useCallback((moduleId: string) => {
    if (interactionMode !== "connect") return;

    setSelectedForLinking((prev) => {
      // If already selected, deselect it
      if (prev.includes(moduleId)) {
        return prev.filter((id) => id !== moduleId);
      }

      // If we have 1 item selected and clicking a different one, create link
      if (prev.length === 1 && prev[0] !== moduleId) {
        const sourceId = prev[0];
        const targetId = moduleId;

        // Check if link already exists (in either direction)
        const existingLinkIndex = links.findIndex(
          (link) =>
            (link.sourceId === sourceId && link.targetId === targetId) ||
            (link.sourceId === targetId && link.targetId === sourceId)
        );

        // Set both modules as recently connected for fade-out animation
        setRecentlyConnected([sourceId, targetId]);

        // Clear recently connected after animation (2000ms for smoother fade)
        setTimeout(() => {
          setRecentlyConnected([]);
        }, 2000);

        if (existingLinkIndex !== -1) {
          // Link exists - REMOVE it (toggle off)
          setLinks((prevLinks) => prevLinks.filter((_, index) => {
            const link = prevLinks[index];
            return !((link.sourceId === sourceId && link.targetId === targetId) ||
                     (link.sourceId === targetId && link.targetId === sourceId));
          }));
          toast({
            title: "Link Disconnected",
            description: `Modules unlinked successfully`,
          });
        } else {
          // Link doesn't exist - ADD it (toggle on)
          setLinks((prevLinks) => [...prevLinks, { sourceId, targetId }]);
          toast({
            title: "Link Connected",
            description: `Modules linked successfully`,
          });
        }

        // Clear selection after creating link
        return [];
      }

      // Otherwise add to selection
      return [...prev, moduleId];
    });
  }, [interactionMode, links, setLinks]);

  // Initialize positions when modules change
  useEffect(() => {
    setPositions((prevPositions) => {
      const newPositions: ModulePosition[] = [];

      modules.forEach((module, index) => {
        const existing = prevPositions.find((p) => p.id === module.id);
        if (existing) {
          newPositions.push(existing);
        } else {
          // Position new modules in a grid layout
          const gridCols = 3;
          const spacing = 4;
          const row = Math.floor(index / gridCols);
          const col = index % gridCols;
          newPositions.push({
            id: module.id,
            x: col * spacing - ((gridCols - 1) * spacing) / 2,
            z: row * spacing,
          });
        }
      });

      return newPositions;
    });
  }, [modules]);

  const handlePositionChange = useCallback((id: string, x: number, z: number) => {
    setPositions((prev) => prev.map((p) => (p.id === id ? { ...p, x, z } : p)));
  }, []);

  const hasModules = modules.length > 0;

  return (
    <div className="w-full h-full relative bg-background z-0">
      {/* 2D fallback — dot-grid with empty state */}
      {!is3D && (
        <div className="w-full h-full dot-grid">
          <EmptyState />
        </div>
      )}

      {/* 3D Canvas — always pre-mounted so WebGL initialises at page load, not on first module add */}
      {is3D && (
        <div className="absolute inset-0 z-0">
          <Suspense fallback={<LoadingFallback />}>
            <Canvas
              className="w-full h-full"
              shadows
              gl={{
                antialias: true,
                alpha: false,
                toneMapping: THREE.ACESFilmicToneMapping,
                toneMappingExposure: 1.0,
              }}
              onCreated={({ gl }) => {
                gl.setClearColor("#050508");
              }}
            >
              <Scene
                modules={modules}
                positions={positions}
                onPositionChange={handlePositionChange}
                isDragging={isDragging}
                setIsDragging={setIsDragging}
                draggingChipId={draggingChipId}
                setDraggingChipId={setDraggingChipId}
                interactionMode={interactionMode}
                selectedForLinking={selectedForLinking}
                recentlyConnected={recentlyConnected}
                onModuleClick={handleModuleClick}
                links={links}
              />
            </Canvas>
          </Suspense>

          {/* EmptyState overlay — shown until first module is added */}
          {!hasModules && (
            <div className="absolute inset-0 z-10 flex items-center justify-center pointer-events-none">
              <EmptyState />
            </div>
          )}
        </div>
      )}

      {/* Coordinates overlay */}
      {is3D && hasModules && (
        <div className="absolute bottom-4 left-4 z-50 font-mono text-xs text-muted-foreground bg-background/80 backdrop-blur-sm px-3 py-2 rounded-md border border-border">
          <span className="text-quantum-cyan">Architecture Schematic</span> • Scroll to zoom • Pan to navigate
        </div>
      )}
    </div>
  );
}

export function MainCanvas(props: MainCanvasProps) {
  if (props.architecture) {
    return <ArchitectureHierarchy viewModel={props.architecture} />;
  }
  if (props.architectureLoading) {
    return (
      <div className="flex h-full items-center justify-center bg-background text-sm text-muted-foreground">
        Loading canonical architecture hierarchy…
      </div>
    );
  }
  if (props.architectureError) {
    return (
      <div className="flex h-full items-center justify-center bg-background p-8 text-center">
        <div>
          <p className="text-sm font-medium text-destructive">Architecture profile unavailable</p>
          <p className="mt-2 max-w-md text-xs text-muted-foreground">{props.architectureError}</p>
        </div>
      </div>
    );
  }
  return <ComposerCanvas {...props} />;
}
