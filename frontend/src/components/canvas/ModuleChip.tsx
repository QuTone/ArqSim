import { useRef, useMemo, useState, useCallback, useEffect } from "react";
import { Html } from "@react-three/drei";
import { useFrame, useThree, type ThreeEvent } from "@react-three/fiber";
import { Module, roleLabels, ModulePosition, InteractionMode } from "@/types/module";
import { Zap, Atom, CircleDot } from "lucide-react";
import * as THREE from "three";

// ============================================================================
// CHIP_CONFIG - Centralized configuration for all chip sizing parameters
// Adjust these values to customize chip appearance without modifying code logic
// ============================================================================
const CHIP_CONFIG = {
  // === Scale Calculation ===
  // Controls how module capacity/factoryBlocks affects overall chip size
  scale: {
    base: 1.2, // Minimum scale value
    multiplier: 0.4, // How much capacity affects scale (logarithmic)
  },

  // === Chip Dimensions ===
  // Core sizing for the two-layer chip design
  dimensions: {
    sizeMultiplier: 1.2, // chipSize = scale * sizeMultiplier
    baseHeight: 0.08, // Base layer thickness (Z depth)
    capHeight: 0.1, // Cap layer thickness (Z depth)
    capScale: 0.86, // Cap size relative to base (0-1, e.g., 0.92 = 92%)
    baseYOffset: 0.02, // Base layer Y position above ground
    gapBetweenLayers: 0.005, // Vertical gap between base and cap
  },

  // === Corner Radius (Rounded Edges) ===
  // Controls the rounding of chip corners
  corners: {
    baseRadius: 0.1, // Rounding radius on base layer
    capRadius: 0.08, // Rounding radius on cap layer
  },

  // === Bevel (Edge Softness) ===
  // Controls the beveled edges of the extruded shapes
  bevel: {
    base: { thickness: 0.02, size: 0.02, segments: 3 },
    cap: { thickness: 0.015, size: 0.015, segments: 3 },
  },

  // === Corner Dots ===
  // Small accent dots at the corners of the base layer
  cornerDots: {
    positionRatio: 0.36, // Distance from center as ratio of chipSize (0-0.5)
    radius: 0.025, // Dot sphere radius
    yOffset: 0.12, // Height above base layer top
    emissiveIntensity: 0.6,
  },

  // === Center Core ===
  // Central indicator cylinder on top of cap
  centerCore: {
    radius: 0.07, // Cylinder radius
    height: 0.008, // Cylinder height
    yOffset: 0.01, // Height above cap layer top
    emissiveIntensity: 0.4,
    metalness: 0.8,
    roughness: 0.2,
  },

  // === HTML Label ===
  // Floating label above the chip with icon and text
  label: {
    yOffset: 0.04, // Height above cap layer top (lower = closer to chip surface)
    scale: 0.6, // 3D to HTML scale factor
    containerMultiplier: 65, // Container size = chipSize * this (in px)
    iconSizePercent: 55, // Icon size as % of container width/height
    iconOpacity: 0.7, // Icon opacity (0-1)
    fontSizeMin: 5, // Minimum font size in px
    fontSizeMultiplier: 6.5, // Font size = scale * this
    letterSpacing: "0.15em", // Letter spacing for label text
    textBottomRatio: 0.06, // Role label (bottom) inset from edge — original position
    qecTopRatio: 0.13,    // QEC label (top) inset — slightly more toward centre than role label
  },

  // === Color Brightness ===
  // Controls how base/cap colors are derived from accent color
  colors: {
    baseBrightnessMultiplier: 0.5,
    baseBrightnessOffset: 0.15,
    capDarknessMultiplier: 0.25,
  },

  // === Material Properties ===
  // Physical material settings for base and cap layers
  materials: {
    base: {
      metalness: 0.6,
      roughness: 0.35,
      clearcoat: 0.25,
      clearcoatRoughness: 0.45,
      reflectivity: 0.5,
      envMapIntensity: 0.55,
    },
    cap: {
      metalness: 0.65,
      roughness: 0.32,
      clearcoat: 0.3,
      clearcoatRoughness: 0.4,
      reflectivity: 0.45,
      envMapIntensity: 0.55,
    },
  },

  // === Animation ===
  // Floating and pulsing animation parameters
  animation: {
    floatAmplitude: 0.02, // Float height (up/down movement)
    floatSpeed: 0.5, // Float frequency (cycles per second)
    pulseBase: 0.3, // Base glow intensity
    pulseAmplitude: 0.15, // Glow pulse range (+/-)
    pulseSpeed: 0.8, // Pulse frequency
    edgePulseBase: 0.5, // Edge glow base intensity
    edgePulseAmplitude: 0.2, // Edge glow pulse range
    edgePulseOffset: 0.5, // Phase offset from main pulse
    liftDuration: 0.15, // Duration for lift animation in seconds
  },
};

// Grid snapping configuration
const GRID_STEP = 2;
const MIN_DISTANCE = 2.5; // Minimum distance between chips to prevent overlap

interface ModuleChipProps {
  module: Module;
  position: [number, number, number];
  onPositionChange: (id: string, x: number, z: number) => void;
  allPositions: ModulePosition[];
  setIsDragging: (dragging: boolean) => void;
  setDraggingChipId: (id: string | null) => void;
  interactionMode: InteractionMode;
  isSelected: boolean;
  isRecentlyConnected: boolean;
  onClick: () => void;
}

export function ModuleChip({
  module,
  position,
  onPositionChange,
  allPositions,
  setIsDragging,
  setDraggingChipId,
  interactionMode,
  isSelected,
  isRecentlyConnected,
  onClick,
}: ModuleChipProps) {
  const groupRef = useRef<THREE.Group>(null);
  const containerRef = useRef<THREE.Group>(null);
  const glowRef = useRef<THREE.Mesh>(null);
  const edgeGlowRef = useRef<THREE.Mesh>(null);

  const [isLifted, setIsLifted] = useState(false);
  const [localPosition, setLocalPosition] = useState<[number, number, number]>(position);
  const [targetLiftY, setTargetLiftY] = useState(0);

  // Sync localPosition with position prop when not dragging
  useEffect(() => {
    if (!isDraggingRef.current && !isLifted) {
      setLocalPosition(position);
    }
  }, [position, isLifted]);
  const currentLiftY = useRef(0);
  const { camera, gl, raycaster, pointer } = useThree();

  // Track if we're actively dragging
  const isDraggingRef = useRef(false);
  const dragPlane = useRef(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0));
  const dragOffset = useRef(new THREE.Vector3());

  // Snap to grid
  const snapToGrid = useCallback((value: number) => {
    return Math.round(value / GRID_STEP) * GRID_STEP;
  }, []);

  // Get current positions from allPositions for real-time collision check
  const getCurrentOtherPositions = useCallback(() => {
    return allPositions.filter((pos) => pos.id !== module.id);
  }, [allPositions, module.id]);

  // Find nearest valid position if collision detected
  const findValidPosition = useCallback(
    (targetX: number, targetZ: number) => {
      const snappedX = snapToGrid(targetX);
      const snappedZ = snapToGrid(targetZ);

      // Check collision with current snapped position
      const otherPositions = getCurrentOtherPositions();
      const hasCollision = otherPositions.some((pos) => {
        const dx = snappedX - pos.x;
        const dz = snappedZ - pos.z;
        const distance = Math.sqrt(dx * dx + dz * dz);
        return distance < MIN_DISTANCE;
      });

      if (!hasCollision) {
        return { x: snappedX, z: snappedZ };
      }

      // Search in expanding circles for a valid position
      for (let radius = GRID_STEP; radius <= GRID_STEP * 4; radius += GRID_STEP) {
        const candidates = [
          { x: snappedX + radius, z: snappedZ },
          { x: snappedX - radius, z: snappedZ },
          { x: snappedX, z: snappedZ + radius },
          { x: snappedX, z: snappedZ - radius },
          { x: snappedX + radius, z: snappedZ + radius },
          { x: snappedX - radius, z: snappedZ - radius },
          { x: snappedX + radius, z: snappedZ - radius },
          { x: snappedX - radius, z: snappedZ + radius },
        ];

        for (const candidate of candidates) {
          const candidateCollision = otherPositions.some((pos) => {
            const dx = candidate.x - pos.x;
            const dz = candidate.z - pos.z;
            const distance = Math.sqrt(dx * dx + dz * dz);
            return distance < MIN_DISTANCE;
          });
          if (!candidateCollision) {
            return candidate;
          }
        }
      }

      // Fallback to original position
      return { x: position[0], z: position[2] };
    },
    [snapToGrid, getCurrentOtherPositions, position],
  );

  // Handle pointer down - start drag or trigger click in connect mode
  const handlePointerDown = useCallback(
    (e: ThreeEvent<PointerEvent>) => {
      e.stopPropagation();

      // In connect mode, just trigger click - no dragging
      if (interactionMode === "connect") {
        onClick();
        return;
      }

      // Drag mode behavior
      isDraggingRef.current = true;
      setIsDragging(true);
      setDraggingChipId(module.id);
      setIsLifted(true);
      setTargetLiftY(0.5);
      gl.domElement.style.cursor = "grabbing";

      // Set up the drag plane at the current Y position
      dragPlane.current.setFromNormalAndCoplanarPoint(
        new THREE.Vector3(0, 1, 0),
        new THREE.Vector3(localPosition[0], 0, localPosition[2]),
      );

      // Calculate offset from click point to object center
      const intersection = new THREE.Vector3();
      raycaster.ray.intersectPlane(dragPlane.current, intersection);
      dragOffset.current.copy(intersection).sub(new THREE.Vector3(localPosition[0], 0, localPosition[2]));

      // Capture pointer for smooth dragging
      gl.domElement.setPointerCapture(e.pointerId);
    },
    [
      gl.domElement,
      interactionMode,
      localPosition,
      module.id,
      onClick,
      raycaster,
      setDraggingChipId,
      setIsDragging,
    ],
  );

  // Handle pointer move - update position during drag
  const handlePointerMove = useCallback(
    (e: ThreeEvent<PointerEvent>) => {
      // Disable drag in connect mode
      if (interactionMode === "connect") return;
      if (!isDraggingRef.current) return;
      e.stopPropagation();

      // Update raycaster with current pointer position
      raycaster.setFromCamera(pointer, camera);

      // Find intersection with drag plane
      const intersection = new THREE.Vector3();
      if (raycaster.ray.intersectPlane(dragPlane.current, intersection)) {
        // Subtract the offset to maintain grab point
        intersection.sub(dragOffset.current);
        setLocalPosition([intersection.x, position[1], intersection.z]);
        // Update parent position in real-time for connection lines
        onPositionChange(module.id, intersection.x, intersection.z);
      }
    },
    [camera, pointer, raycaster, position, interactionMode, onPositionChange, module.id],
  );

  // Handle pointer up - end drag
  const handlePointerUp = useCallback(
    (e: ThreeEvent<PointerEvent>) => {
      // Skip in connect mode
      if (interactionMode === "connect") return;
      if (!isDraggingRef.current) return;
      e.stopPropagation();

      isDraggingRef.current = false;
      setIsDragging(false);
      setDraggingChipId(null);
      setIsLifted(false);
      setTargetLiftY(0);
      gl.domElement.style.cursor = "auto";

      // Release pointer capture
      if (gl.domElement.hasPointerCapture(e.pointerId)) {
        gl.domElement.releasePointerCapture(e.pointerId);
      }

      // Find valid snapped position
      const validPos = findValidPosition(localPosition[0], localPosition[2]);
      setLocalPosition([validPos.x, position[1], validPos.z]);
      onPositionChange(module.id, validPos.x, validPos.z);
    },
    [
      gl.domElement,
      setIsDragging,
      findValidPosition,
      localPosition,
      position,
      onPositionChange,
      module.id,
      interactionMode,
      setDraggingChipId,
    ],
  );

  // Role-based accent colors (quantum field tones)
  const getAccentColor = (role: string) => {
    switch (role) {
      case "compute":
        return "#60a5fa"; // Soft blue - processing/logic
      case "memory":
        return "#4ade80"; // Soft green - storage
      case "factory":
        return "#a855f7"; // Darker purple - rich violet
      default:
        return "#64748b";
    }
  };

  // Modality icon component
  const getModalityIcon = (modality: string) => {
    switch (modality) {
      case "sc":
        return <Zap className="w-full h-full" strokeWidth={1.5} />;
      case "na":
        return <Atom className="w-full h-full" strokeWidth={1.5} />;
      case "ti":
        return <CircleDot className="w-full h-full" strokeWidth={1.5} />;
      default:
        return <CircleDot className="w-full h-full" strokeWidth={1.5} />;
    }
  };

  // Dynamic scale based on capacity/factoryBlocks
  const getScale = () => {
    let count: number;
    if (module.role === "factory") {
      count = parseInt(module.factoryBlocks) || 10;
    } else {
      count = parseInt(module.capacity) || 10;
    }
    return CHIP_CONFIG.scale.base + Math.log10(count || 10) * CHIP_CONFIG.scale.multiplier;
  };

  const accentColor = getAccentColor(module.role);
  const scale = getScale();

  // Derive chip colors from accent color with different brightness
  const getChipColors = (accent: string) => {
    const hex = accent.replace("#", "");
    const r = parseInt(hex.substring(0, 2), 16) / 255;
    const g = parseInt(hex.substring(2, 4), 16) / 255;
    const b = parseInt(hex.substring(4, 6), 16) / 255;

    const { baseBrightnessMultiplier, baseBrightnessOffset, capDarknessMultiplier } = CHIP_CONFIG.colors;

    // Base: slightly brighter than accent
    const baseColor = `rgb(${Math.round((r * baseBrightnessMultiplier + baseBrightnessOffset) * 255)}, ${Math.round((g * baseBrightnessMultiplier + baseBrightnessOffset) * 255)}, ${Math.round((b * baseBrightnessMultiplier + baseBrightnessOffset) * 255)})`;

    // Cap: darker than accent
    const capColor = `rgb(${Math.round(r * capDarknessMultiplier * 255)}, ${Math.round(g * capDarknessMultiplier * 255)}, ${Math.round(b * capDarknessMultiplier * 255)})`;

    return { baseColor, capColor };
  };

  const { baseColor, capColor } = getChipColors(accentColor);

  // Chip dimensions from config
  const { dimensions, corners, bevel } = CHIP_CONFIG;
  const chipSize = scale * dimensions.sizeMultiplier;
  const baseLayerHeight = dimensions.baseHeight;
  const capLayerHeight = dimensions.capHeight;
  const capScale = dimensions.capScale;
  const baseYPos = dimensions.baseYOffset;

  // Create rounded box geometry for base layer
  const baseGeometry = useMemo(() => {
    const shape = new THREE.Shape();
    const radius = corners.baseRadius;
    const w = chipSize / 2;
    const d = chipSize / 2;

    shape.moveTo(-w + radius, -d);
    shape.lineTo(w - radius, -d);
    shape.quadraticCurveTo(w, -d, w, -d + radius);
    shape.lineTo(w, d - radius);
    shape.quadraticCurveTo(w, d, w - radius, d);
    shape.lineTo(-w + radius, d);
    shape.quadraticCurveTo(-w, d, -w, d - radius);
    shape.lineTo(-w, -d + radius);
    shape.quadraticCurveTo(-w, -d, -w + radius, -d);

    const extrudeSettings = {
      depth: baseLayerHeight,
      bevelEnabled: true,
      bevelThickness: bevel.base.thickness,
      bevelSize: bevel.base.size,
      bevelSegments: bevel.base.segments,
    };

    return new THREE.ExtrudeGeometry(shape, extrudeSettings);
  }, [chipSize, baseLayerHeight, corners.baseRadius, bevel.base]);

  // Create rounded box geometry for cap layer
  const capGeometry = useMemo(() => {
    const shape = new THREE.Shape();
    const radius = corners.capRadius;
    const w = (chipSize * capScale) / 2;
    const d = (chipSize * capScale) / 2;

    shape.moveTo(-w + radius, -d);
    shape.lineTo(w - radius, -d);
    shape.quadraticCurveTo(w, -d, w, -d + radius);
    shape.lineTo(w, d - radius);
    shape.quadraticCurveTo(w, d, w - radius, d);
    shape.lineTo(-w + radius, d);
    shape.quadraticCurveTo(-w, d, -w, d - radius);
    shape.lineTo(-w, -d + radius);
    shape.quadraticCurveTo(-w, -d, -w + radius, -d);

    const extrudeSettings = {
      depth: capLayerHeight,
      bevelEnabled: true,
      bevelThickness: bevel.cap.thickness,
      bevelSize: bevel.cap.size,
      bevelSegments: bevel.cap.segments,
    };

    return new THREE.ExtrudeGeometry(shape, extrudeSettings);
  }, [chipSize, capScale, capLayerHeight, corners.capRadius, bevel.cap]);

  // Animation parameters from config
  const { animation } = CHIP_CONFIG;

  // Ref for base layer material to animate emissive
  const baseMaterialRef = useRef<THREE.MeshPhysicalMaterial>(null);

  // Selection emissive intensity (animated when selected)
  const selectionEmissiveIntensity = useRef(0.4);

  // Smooth lift animation
  useFrame((state, delta) => {
    const time = state.clock.elapsedTime;

    // Smooth lift interpolation
    const liftSpeed = 8; // Higher = faster lift
    currentLiftY.current += (targetLiftY - currentLiftY.current) * Math.min(delta * liftSpeed, 1);

    if (groupRef.current) {
      const floatY = Math.sin(time * animation.floatSpeed) * animation.floatAmplitude;
      groupRef.current.position.y = floatY + currentLiftY.current;
    }
    if (glowRef.current) {
      const basePulse = animation.pulseBase + Math.sin(time * animation.pulseSpeed) * animation.pulseAmplitude;
      const pulse = isLifted ? basePulse * 1.5 : basePulse;
      (glowRef.current.material as THREE.MeshStandardMaterial).emissiveIntensity = pulse;
    }
    if (edgeGlowRef.current) {
      const baseEdgePulse =
        animation.edgePulseBase +
        Math.sin(time * animation.pulseSpeed + animation.edgePulseOffset) * animation.edgePulseAmplitude;
      const edgePulse = isLifted ? baseEdgePulse * 1.3 : baseEdgePulse;
      (edgeGlowRef.current.material as THREE.MeshStandardMaterial).emissiveIntensity = edgePulse;
    }

    // Animate base layer emissive for selection glow
    if (baseMaterialRef.current) {
      // Use a slightly brighter version of base color for selection glow
      const brighterBaseColor = new THREE.Color(baseColor).lerp(new THREE.Color("#ffffff"), 0.25);

      if (isSelected) {
        // Softer pulse using brighter base color
        const selectedPulse = 1.2 + Math.sin(time * 3) * 0.2;
        selectionEmissiveIntensity.current += (selectedPulse - selectionEmissiveIntensity.current) * 0.1;
        baseMaterialRef.current.emissive.copy(brighterBaseColor);
        baseMaterialRef.current.emissiveIntensity = selectionEmissiveIntensity.current;
      } else if (isRecentlyConnected) {
        // Eased fade out - exponential decay with longer tail
        const diff = selectionEmissiveIntensity.current - 0.4;
        // Faster initial decay (0.92) for quicker start, creates longer tail effect
        selectionEmissiveIntensity.current = 0.4 + diff * 0.92;
        baseMaterialRef.current.emissive.copy(brighterBaseColor);
        baseMaterialRef.current.emissiveIntensity = selectionEmissiveIntensity.current;
      } else {
        // Normal subtle glow
        selectionEmissiveIntensity.current += (0.4 - selectionEmissiveIntensity.current) * 0.1;
        baseMaterialRef.current.emissive.setStyle(baseColor);
        baseMaterialRef.current.emissiveIntensity = selectionEmissiveIntensity.current;
      }
    }
  });

  // Corner dots config
  const { cornerDots, centerCore, label, materials } = CHIP_CONFIG;

  // Use local position during drag, otherwise use prop position
  const displayPosition = isLifted ? localPosition : position;

  // Calculate total chip height for invisible drag area
  const totalChipHeight = baseYPos + baseLayerHeight + capLayerHeight + 0.2;

  // Cursor based on interaction mode
  const getCursor = () => (interactionMode === "connect" ? "crosshair" : "grab");

  return (
    <group
      ref={containerRef}
      position={displayPosition}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={handlePointerUp}
      onPointerLeave={handlePointerUp}
      onPointerOver={() => {
        gl.domElement.style.cursor = getCursor();
      }}
      onPointerOut={() => {
        if (!isLifted) gl.domElement.style.cursor = "auto";
      }}
    >
      {/* Animated wrapper for floating effect */}
      <group ref={groupRef}>
        {/* Drag hitbox - inside animated group so it lifts with chip */}
        <mesh
          position={[0, totalChipHeight / 2, 0]}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerLeave={handlePointerUp}
          onPointerOver={() => {
            gl.domElement.style.cursor = getCursor();
          }}
          onPointerOut={() => {
            if (!isLifted) gl.domElement.style.cursor = "auto";
          }}
          renderOrder={999}
        >
          <boxGeometry args={[chipSize * 1.1, totalChipHeight * 1.1, chipSize * 1.1]} />
          <meshBasicMaterial transparent opacity={0} />
        </mesh>
        {/* Base layer - lighter color, larger, glows when selected */}
        <mesh
          geometry={baseGeometry}
          position={[0, baseYPos, 0]}
          rotation={[-Math.PI / 2, 0, 0]}
          castShadow
          receiveShadow
          raycast={() => null}
        >
          <meshPhysicalMaterial
            ref={baseMaterialRef}
            color={baseColor}
            emissive={baseColor}
            emissiveIntensity={0.4}
            metalness={materials.base.metalness}
            roughness={materials.base.roughness}
            clearcoat={materials.base.clearcoat}
            clearcoatRoughness={materials.base.clearcoatRoughness}
            reflectivity={materials.base.reflectivity}
            envMapIntensity={materials.base.envMapIntensity}
          />
        </mesh>

        {/* Cap layer - darker color, smaller, sits on top */}
        <mesh
          geometry={capGeometry}
          position={[0, baseYPos + baseLayerHeight + dimensions.gapBetweenLayers, 0]}
          rotation={[-Math.PI / 2, 0, 0]}
          castShadow
          receiveShadow
          raycast={() => null}
        >
          <meshPhysicalMaterial
            color={capColor}
            metalness={materials.cap.metalness}
            roughness={materials.cap.roughness}
            clearcoat={materials.cap.clearcoat}
            clearcoatRoughness={materials.cap.clearcoatRoughness}
            reflectivity={materials.cap.reflectivity}
            envMapIntensity={materials.cap.envMapIntensity}
          />
        </mesh>

        {/* Corner accent dots on base layer - quantum readout points */}
        {[
          [-chipSize * cornerDots.positionRatio, chipSize * cornerDots.positionRatio],
          [chipSize * cornerDots.positionRatio, chipSize * cornerDots.positionRatio],
          [-chipSize * cornerDots.positionRatio, -chipSize * cornerDots.positionRatio],
          [chipSize * cornerDots.positionRatio, -chipSize * cornerDots.positionRatio],
        ].map((pos, i) => (
          <mesh
            key={i}
            position={[pos[0], baseYPos + baseLayerHeight + cornerDots.yOffset, pos[1]]}
            raycast={() => null}
          >
            <sphereGeometry args={[cornerDots.radius, 16, 16]} />
            <meshStandardMaterial
              color={accentColor}
              emissive={accentColor}
              emissiveIntensity={cornerDots.emissiveIntensity}
            />
          </mesh>
        ))}

        {/* Center processing core indicator on cap */}
        <mesh position={[0, baseYPos + baseLayerHeight + capLayerHeight + centerCore.yOffset, 0]} raycast={() => null}>
          <cylinderGeometry args={[centerCore.radius, centerCore.radius, centerCore.height, 32]} />
          <meshStandardMaterial
            color={accentColor}
            emissive={accentColor}
            emissiveIntensity={centerCore.emissiveIntensity}
            metalness={centerCore.metalness}
            roughness={centerCore.roughness}
          />
        </mesh>

        {/* PCB Label - floating above cap */}
        <Html
          transform
          position={[0, baseYPos + baseLayerHeight + capLayerHeight + label.yOffset, 0]}
          rotation={[-Math.PI / 2, 0, 0]}
          scale={label.scale}
          center
          pointerEvents="none"
        >
          <div
            className="relative flex flex-col items-center justify-center pointer-events-none select-none"
            style={{
              width: `${chipSize * label.containerMultiplier}px`,
              height: `${chipSize * label.containerMultiplier}px`,
              fontFamily: "'JetBrains Mono', 'SF Mono', monospace",
            }}
          >
            {/* QEC type label — slightly inset from top of chip */}
            <div
              className="absolute tracking-widest uppercase font-semibold"
              style={{
                top: `${chipSize * label.containerMultiplier * label.qecTopRatio}px`,
                color: accentColor,
                textShadow: `0 0 6px ${accentColor}60`,
                fontSize: `${Math.max(label.fontSizeMin, scale * label.fontSizeMultiplier * 0.85)}px`,
                letterSpacing: label.letterSpacing,
                opacity: 0.85,
              }}
            >
              {module.qecType === "bb"
                ? "BB CODE"
                : module.qecType === "rotated-surface"
                ? "SURFACE"
                : "SURF"}
            </div>

            {/* Modality Icon — center */}
            <div
              className="flex items-center justify-center"
              style={{
                color: accentColor,
                width: `${label.iconSizePercent}%`,
                height: `${label.iconSizePercent}%`,
                opacity: label.iconOpacity,
                filter: `drop-shadow(0 0 4px ${accentColor})`,
              }}
            >
              {getModalityIcon(module.modality)}
            </div>

            {/* Role label — bottom of chip */}
            <div
              className="absolute tracking-widest uppercase font-semibold"
              style={{
                bottom: `${chipSize * label.containerMultiplier * label.textBottomRatio}px`,
                color: "rgba(255, 255, 255, 0.85)",
                textShadow: `0 0 8px ${accentColor}40`,
                fontSize: `${Math.max(label.fontSizeMin, scale * label.fontSizeMultiplier)}px`,
                letterSpacing: label.letterSpacing,
              }}
            >
              {roleLabels[module.role].toUpperCase()}
            </div>
          </div>
        </Html>
      </group>
    </group>
  );
}
