import { useRef, useMemo } from "react";
import { useFrame, extend, type MaterialNode } from "@react-three/fiber";
import * as THREE from "three";
import { shaderMaterial } from "@react-three/drei";

// High-Energy 3D Volumetric Tube shader material
const DataStreamMaterial = shaderMaterial(
  {
    uTime: 0,
    uColor: new THREE.Color("#ff8b8b"),
    uSpeed: 8.0,
    uBaseOpacity: 0.4,
    uLength: 1.0,
  },
  // Vertex shader - pass position along tube for pulse effect
  `
    varying vec2 vUv;
    varying vec3 vNormal;
    varying vec3 vPosition;

    void main() {
      vUv = uv;
      vNormal = normalize(normalMatrix * normal);
      vPosition = position;
      gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
    }
  `,
  // Fragment shader - 3D Volumetric Tube with High-Energy Pulse
  `
    uniform float uTime;
    uniform vec3 uColor;
    uniform float uSpeed;
    uniform float uBaseOpacity;
    uniform float uLength;
    varying vec2 vUv;
    varying vec3 vNormal;
    varying vec3 vPosition;

    void main() {
      // Use position.y (along cylinder axis) normalized by length for pulse
      float alongTube = (vPosition.y / uLength) + 0.5;

      // Sharp pulse pattern - pow creates discrete "light bullets"
      float rawPulse = sin(alongTube * 15.0 - uTime * uSpeed) * 0.5 + 0.5;
      float pulse = pow(rawPulse, 10.0);

      // Secondary faster micro-pulses for extra detail
      float microPulse = pow(sin(alongTube * 40.0 - uTime * uSpeed * 1.5) * 0.5 + 0.5, 8.0);

      // Combine pulses
      float combinedPulse = pulse + microPulse * 0.3;

      // 3D volumetric shading - use radial position for tube roundness
      // vNormal points outward from cylinder surface
      float radialShading = 0.6 + 0.4 * abs(dot(vNormal, vec3(0.0, 1.0, 0.0)));

      // Fresnel-like edge glow for glass tube effect
      float fresnel = pow(1.0 - abs(dot(vNormal, normalize(vec3(0.3, 0.5, 0.3)))), 2.0);
      float glassGlow = fresnel * 0.3;

      // Mix base color with white for bright pulses and add glass effect
      vec3 baseGlow = mix(uColor, vec3(1.0), combinedPulse * 0.8);
      vec3 finalColor = baseGlow + vec3(glassGlow);

      // Apply radial shading
      finalColor *= radialShading;

      // Alpha: transparent between pulses, bright at pulse
      float alpha = uBaseOpacity * 0.4 + combinedPulse * 0.6;
      alpha = clamp(alpha, 0.0, 1.0);

      gl_FragColor = vec4(finalColor, alpha);
    }
  `
);

// Extend Three.js with our custom material
extend({ DataStreamMaterial });

interface DataStreamMaterialInstance extends THREE.ShaderMaterial {
  uTime: number;
  uColor: THREE.Color;
  uSpeed: number;
  uBaseOpacity: number;
  uLength: number;
}

declare module "@react-three/fiber" {
  interface ThreeElements {
    dataStreamMaterial: MaterialNode<
      DataStreamMaterialInstance,
      typeof DataStreamMaterial
    >;
  }
}

interface ConnectionLineProps {
  start: [number, number, number];
  end: [number, number, number];
  color?: string;
}

export function ConnectionLine({ start, end, color = "#ff8b8b" }: ConnectionLineProps) {
  const materialRef = useRef<DataStreamMaterialInstance>(null);
  const glowMaterialRef = useRef<THREE.MeshBasicMaterial>(null);

  // Calculate the position, rotation, and scale for the cylinder
  const { position, quaternion, length, socketRotation } = useMemo(() => {
    const startVec = new THREE.Vector3(...start);
    const endVec = new THREE.Vector3(...end);

    // Midpoint for position
    const midpoint = new THREE.Vector3().addVectors(startVec, endVec).multiplyScalar(0.5);

    // Length of the connection
    const len = startVec.distanceTo(endVec);

    // Direction vector (in XZ plane for horizontal connections)
    const direction = new THREE.Vector3().subVectors(endVec, startVec).normalize();

    // Calculate quaternion to align cylinder (default cylinder is along Y-axis)
    const quat = new THREE.Quaternion();
    quat.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction);

    // Calculate rotation for sockets to face along the connection direction
    // The torus should be perpendicular to the connection line (facing the connected chip)
    const angle = Math.atan2(direction.x, direction.z);

    return {
      position: midpoint,
      quaternion: quat,
      length: len,
      socketRotation: angle,
    };
  }, [start, end]);

  // Animate the shader
  useFrame((state) => {
    if (materialRef.current) {
      materialRef.current.uTime = state.clock.elapsedTime;
    }
    if (glowMaterialRef.current) {
      // Subtle pulsing for outer glow
      const pulse = Math.sin(state.clock.elapsedTime * 2) * 0.1 + 0.25;
      glowMaterialRef.current.opacity = pulse;
    }
  });

  const beamColor = useMemo(() => new THREE.Color(color), [color]);

  // Don't render if length is 0 (same position)
  if (length < 0.01) return null;

  return (
    <group>
      {/* Main beam with data stream shader */}
      <mesh position={position} quaternion={quaternion}>
        <cylinderGeometry args={[0.04, 0.04, length, 16, 1, true]} />
        <dataStreamMaterial
          ref={materialRef}
          uColor={beamColor}
          uSpeed={8.0}
          uBaseOpacity={0.5}
          uLength={length}
          transparent
          blending={THREE.AdditiveBlending}
          side={THREE.DoubleSide}
          depthWrite={false}
        />
      </mesh>

      {/* Outer glow layer */}
      <mesh position={position} quaternion={quaternion}>
        <cylinderGeometry args={[0.08, 0.08, length, 12, 1, true]} />
        <meshBasicMaterial
          ref={glowMaterialRef}
          color={beamColor}
          transparent
          opacity={0.25}
          blending={THREE.AdditiveBlending}
          side={THREE.DoubleSide}
          depthWrite={false}
        />
      </mesh>

      {/* Terminal Socket at Start - oriented to face the connection */}
      <group position={start} rotation={[0, socketRotation, 0]}>
        {/* Outer ring - rotated to be perpendicular to connection */}
        <mesh rotation={[0, 0, Math.PI / 2]}>
          <torusGeometry args={[0.06, 0.015, 12, 24]} />
          <meshStandardMaterial
            color={beamColor}
            emissive={beamColor}
            emissiveIntensity={1.0}
            transparent
            opacity={0.95}
            metalness={0.7}
            roughness={0.2}
          />
        </mesh>
        {/* Inner glow core */}
        <mesh>
          <sphereGeometry args={[0.04, 16, 16]} />
          <meshBasicMaterial
            color={beamColor}
            transparent
            opacity={0.9}
            blending={THREE.AdditiveBlending}
          />
        </mesh>
        {/* Outer glow halo */}
        <mesh>
          <sphereGeometry args={[0.07, 12, 12]} />
          <meshBasicMaterial
            color={beamColor}
            transparent
            opacity={0.3}
            blending={THREE.AdditiveBlending}
          />
        </mesh>
      </group>

      {/* Terminal Socket at End - oriented to face the connection */}
      <group position={end} rotation={[0, socketRotation + Math.PI, 0]}>
        {/* Outer ring - rotated to be perpendicular to connection */}
        <mesh rotation={[0, 0, Math.PI / 2]}>
          <torusGeometry args={[0.06, 0.015, 12, 24]} />
          <meshStandardMaterial
            color={beamColor}
            emissive={beamColor}
            emissiveIntensity={1.0}
            transparent
            opacity={0.95}
            metalness={0.7}
            roughness={0.2}
          />
        </mesh>
        {/* Inner glow core */}
        <mesh>
          <sphereGeometry args={[0.04, 16, 16]} />
          <meshBasicMaterial
            color={beamColor}
            transparent
            opacity={0.9}
            blending={THREE.AdditiveBlending}
          />
        </mesh>
        {/* Outer glow halo */}
        <mesh>
          <sphereGeometry args={[0.07, 12, 12]} />
          <meshBasicMaterial
            color={beamColor}
            transparent
            opacity={0.3}
            blending={THREE.AdditiveBlending}
          />
        </mesh>
      </group>
    </group>
  );
}
