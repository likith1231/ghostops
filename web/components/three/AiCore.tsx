"use client";

import { Billboard } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";
import { healState, type HealStage } from "@/lib/healCycle";

// JARVIS blue at rest, Ultron red during an incident, a green flash once healed.
const STAGE_COLOR: Record<HealStage, THREE.Color> = {
  incident: new THREE.Color("#ff2d3d"),
  diagnosing: new THREE.Color("#ff9f1c"),
  patching: new THREE.Color("#38d9ff"),
  healed: new THREE.Color("#2ee88a"),
  steady: new THREE.Color("#38d9ff"),
};
const DEEP = new THREE.Color("#1a8cff");
const now = () => performance.now() / 1000;

/* One shader draws every ring: solid, dashed, segmented blocks or partial arcs. */
const ringVertex = /* glsl */ `
  varying vec2 vPos;
  void main() {
    vPos = position.xy;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;
const ringFragment = /* glsl */ `
  uniform vec3 uColor;
  uniform float uR1;
  uniform float uR2;
  uniform float uStart;
  uniform float uLen;
  uniform float uSegments;
  uniform float uFill;
  uniform float uOpacity;
  uniform float uGlow;
  varying vec2 vPos;
  void main() {
    float r = length(vPos);
    float aa = fwidth(r) * 1.2;
    float band = smoothstep(uR1 - aa, uR1 + aa, r) * (1.0 - smoothstep(uR2 - aa, uR2 + aa, r));
    // Soft halo outside the band: the glow is part of the ring, no post-processing needed.
    float d = max(max(uR1 - r, r - uR2), 0.0);
    float radial = max(band, exp(-d / uGlow) * 0.4);
    if (radial < 0.02) discard;
    float a = mod(atan(vPos.y, vPos.x) - uStart, 6.28318531);
    if (a > uLen) discard;
    if (uSegments > 1.0 && fract(a / 6.28318531 * uSegments) > uFill) discard;
    gl_FragColor = vec4(uColor, uOpacity * radial);
  }
`;

interface RingProps {
  r1: number;
  r2: number;
  color: THREE.Color;
  start?: number;
  len?: number;
  segments?: number;
  fill?: number;
  opacity?: number;
  z?: number;
  spin?: number;
  /** Optional per-frame animation of the arc length (radians). */
  animateLen?: (t: number) => number;
  glow?: number;
  /** Extrusion depth: the ring is stacked this deep so it reads as a solid band at an angle. */
  depth?: number;
}

function Ring({ r1, r2, color, start = 0, len = Math.PI * 2, segments = 1, fill = 1, opacity = 0.9, z = 0, spin = 0, animateLen, glow = 0.035, depth = 0 }: RingProps) {
  const mesh = useRef<THREE.Group>(null);
  const uniforms = useMemo(
    () => ({
      uColor: { value: color }, // shared colour object: stage changes reach every ring
      uR1: { value: r1 },
      uR2: { value: r2 },
      uStart: { value: start },
      uLen: { value: len },
      uSegments: { value: segments },
      uFill: { value: fill },
      uOpacity: { value: opacity },
      uGlow: { value: glow },
    }),
    [color, r1, r2, start, len, segments, fill, opacity, glow],
  );
  useFrame((_, delta) => {
    if (mesh.current) mesh.current.rotation.z += delta * spin;
    if (animateLen) uniforms.uLen.value = animateLen(now());
  });
  const layers = depth > 0 ? 6 : 1;
  return (
    <group ref={mesh} position={[0, 0, z]}>
      {Array.from({ length: layers }, (_, i) => (
        <mesh key={i} position={[0, 0, layers > 1 ? (i / (layers - 1) - 0.5) * depth : 0]}>
          <planeGeometry args={[r2 * 2 + glow * 14, r2 * 2 + glow * 14]} />
          <shaderMaterial
            uniforms={uniforms}
            vertexShader={ringVertex}
            fragmentShader={ringFragment}
            transparent
            depthWrite={false}
            blending={THREE.AdditiveBlending}
            side={THREE.DoubleSide}
          />
        </mesh>
      ))}
    </group>
  );
}

function Ticks({ radius, count, short, long, every, color, opacity, z = 0, spin = 0 }: { radius: number; count: number; short: number; long: number; every: number; color: THREE.Color; opacity: number; z?: number; spin?: number }) {
  const ref = useRef<THREE.LineSegments>(null);
  const geometry = useMemo(() => {
    const pts: number[] = [];
    for (let i = 0; i < count; i++) {
      const a = (i / count) * Math.PI * 2;
      const l = i % every === 0 ? long : short;
      pts.push(Math.cos(a) * radius, Math.sin(a) * radius, 0, Math.cos(a) * (radius + l), Math.sin(a) * (radius + l), 0);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return g;
  }, [radius, count, short, long, every]);
  useFrame((_, delta) => {
    if (ref.current) ref.current.rotation.z += delta * spin;
  });
  return (
    <lineSegments ref={ref} geometry={geometry} position={[0, 0, z]}>
      <lineBasicMaterial color={color} transparent opacity={opacity} toneMapped={false} depthWrite={false} blending={THREE.AdditiveBlending} />
    </lineSegments>
  );
}

function Dots({ radius, count, color, size, z = 0, spin = 0 }: { radius: number; count: number; color: THREE.Color; size: number; z?: number; spin?: number }) {
  const ref = useRef<THREE.Points>(null);
  const geometry = useMemo(() => {
    const arr = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const a = (i / count) * Math.PI * 2;
      arr.set([Math.cos(a) * radius, Math.sin(a) * radius, 0], i * 3);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(arr, 3));
    return g;
  }, [radius, count]);
  useFrame((_, delta) => {
    if (ref.current) ref.current.rotation.z += delta * spin;
  });
  return (
    <points ref={ref} geometry={geometry} position={[0, 0, z]}>
      <pointsMaterial color={color} size={size} sizeAttenuation transparent opacity={0.9} toneMapped={false} depthWrite={false} />
    </points>
  );
}

const sweepFragment = /* glsl */ `
  uniform vec3 uColor;
  varying vec2 vPos;
  void main() {
    float r = length(vPos);
    float a = mod(atan(vPos.y, vPos.x), 6.28318531);
    if (r > 1.42 || r < 0.42 || a > 0.9) discard;
    float tail = 1.0 - a / 0.9;
    gl_FragColor = vec4(uColor, tail * tail * 0.32);
  }
`;

function Sweep({ color }: { color: THREE.Color }) {
  const ref = useRef<THREE.Mesh>(null);
  const uniforms = useMemo(() => ({ uColor: { value: color } }), [color]);
  useFrame((_, delta) => {
    if (ref.current) ref.current.rotation.z -= delta * 1.4;
  });
  return (
    <mesh ref={ref} position={[0, 0, 0.01]}>
      <planeGeometry args={[3, 3]} />
      <shaderMaterial uniforms={uniforms} vertexShader={ringVertex} fragmentShader={sweepFragment} transparent depthWrite={false} blending={THREE.AdditiveBlending} />
    </mesh>
  );
}

const haloFragment = /* glsl */ `
  uniform vec3 uColor;
  varying vec2 vPos;
  void main() {
    float r = length(vPos);
    float a = exp(-r * r * 9.0) * 0.9 + exp(-r * 4.0) * 0.25;
    gl_FragColor = vec4(uColor, a);
  }
`;

function CoreHalo({ color }: { color: THREE.Color }) {
  const uniforms = useMemo(() => ({ uColor: { value: color } }), [color]);
  return (
    <Billboard>
      <mesh>
        <planeGeometry args={[2.4, 2.4]} />
        <shaderMaterial uniforms={uniforms} vertexShader={ringVertex} fragmentShader={haloFragment} transparent depthWrite={false} blending={THREE.AdditiveBlending} />
      </mesh>
    </Billboard>
  );
}

function Core({ color }: { color: THREE.Color }) {
  const shell = useRef<THREE.Mesh>(null);
  const glow = useRef<THREE.MeshBasicMaterial>(null);
  const gyroA = useRef<THREE.Group>(null);
  const gyroB = useRef<THREE.Group>(null);
  useFrame((_, delta) => {
    if (shell.current) {
      shell.current.rotation.x += delta * 0.7;
      shell.current.rotation.y += delta * 0.9;
    }
    if (glow.current) glow.current.color.copy(color).lerp(new THREE.Color("#ffffff"), 0.55).multiplyScalar(2.2 + Math.sin(now() * 4) * 0.3);
    if (gyroA.current) gyroA.current.rotation.x += delta * 0.8;
    if (gyroB.current) gyroB.current.rotation.y += delta * 0.6;
  });
  return (
    <group>
      <CoreHalo color={color} />
      <mesh>
        <sphereGeometry args={[0.13, 32, 32]} />
        <meshBasicMaterial ref={glow} toneMapped={false} />
      </mesh>
      <mesh ref={shell}>
        <icosahedronGeometry args={[0.27, 1]} />
        <meshBasicMaterial color={color} wireframe transparent opacity={0.7} toneMapped={false} />
      </mesh>
      <group ref={gyroA} rotation={[1.2, 0.2, 0]}>
        <mesh>
          <torusGeometry args={[0.4, 0.004, 8, 120]} />
          <meshBasicMaterial color={color} toneMapped={false} />
        </mesh>
      </group>
      <group ref={gyroB} rotation={[0.3, 1.1, 0.6]}>
        <mesh>
          <torusGeometry args={[0.46, 0.003, 8, 120]} />
          <meshBasicMaterial color={DEEP} toneMapped={false} />
        </mesh>
      </group>
    </group>
  );
}

const beamFragment = /* glsl */ `
  uniform vec3 uColor;
  uniform float uTime;
  varying vec2 vUv;
  void main() {
    float streaks = 0.55 + 0.45 * sin(vUv.x * 120.0 + uTime * 0.6);
    float fade = pow(1.0 - vUv.y, 1.6);
    float scan = 0.85 + 0.15 * sin(vUv.y * 40.0 - uTime * 6.0);
    gl_FragColor = vec4(uColor, fade * streaks * scan * 0.22);
  }
`;
const beamVertex = /* glsl */ `
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

/** The projector: a light cone from a small base up to the lowest tier, with particles rising through it. */
function Projector({ color }: { color: THREE.Color }) {
  const uniforms = useMemo(() => ({ uColor: { value: color }, uTime: { value: 0 } }), [color]);
  const motes = useRef<THREE.Points>(null);
  const { geometry, seeds } = useMemo(() => {
    const n = 160;
    const arr = new Float32Array(n * 3);
    const seeds = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) seeds.set([Math.random() * Math.PI * 2, Math.random(), Math.random()], i * 3);
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(arr, 3));
    return { geometry: g, seeds };
  }, []);
  useFrame(() => {
    const t = now();
    uniforms.uTime.value = t;
    const pos = geometry.attributes.position as THREE.BufferAttribute;
    for (let i = 0; i < pos.count; i++) {
      const [a, r, off] = [seeds[i * 3], seeds[i * 3 + 1], seeds[i * 3 + 2]];
      const h = (t * 0.25 + off) % 1; // 0 at the base, 1 at the top
      const radius = (0.25 + h * 1.25) * r;
      pos.setXYZ(i, Math.cos(a + t * 0.2) * radius, Math.sin(a + t * 0.2) * radius, -1.6 + h * 1.6);
    }
    pos.needsUpdate = true;
  });
  return (
    <group>
      <mesh rotation={[Math.PI / 2, 0, 0]} position={[0, 0, -0.8]}>
        <cylinderGeometry args={[1.5, 0.22, 1.6, 64, 1, true]} />
        <shaderMaterial uniforms={uniforms} vertexShader={beamVertex} fragmentShader={beamFragment} transparent depthWrite={false} blending={THREE.AdditiveBlending} side={THREE.DoubleSide} />
      </mesh>
      <points ref={motes} geometry={geometry}>
        <pointsMaterial color={color} size={0.025} transparent opacity={0.9} toneMapped={false} depthWrite={false} />
      </points>
      {/* base */}
      <Ring r1={0.16} r2={0.24} color={color} opacity={0.9} z={-1.6} />
      <Ring r1={0.32} r2={0.335} color={DEEP} segments={36} fill={0.5} spin={0.8} opacity={0.8} z={-1.6} />
      <Ring r1={0.42} r2={0.425} color={color} opacity={0.4} z={-1.6} />
    </group>
  );
}

function Hologram() {
  const { viewport } = useThree();
  const group = useRef<THREE.Group>(null);
  const turn = useRef<THREE.Group>(null);
  // Shared colour objects: every layer reads them, so a stage change recolours the whole core.
  const accent = useMemo(() => new THREE.Color("#38d9ff"), []);
  const accentSoft = useMemo(() => new THREE.Color("#38d9ff"), []);
  const flicker = useRef(1);

  useFrame(({ pointer }, delta) => {
    const g = group.current;
    if (!g) return;
    const { stage } = healState(now());
    accent.lerp(STAGE_COLOR[stage], 1 - Math.exp(-delta * 5));
    accentSoft.copy(accent).lerp(DEEP, 0.45);
    // Hologram flicker: rare, brief dips in brightness.
    flicker.current = Math.random() < 0.015 ? 0.55 : THREE.MathUtils.damp(flicker.current, 1, 12, delta);
    accent.multiplyScalar(0.9 + 0.1 * flicker.current);

    // Wide canvas (desktop hero): float right of the headline. Square canvas (phones): centred.
    const wide = viewport.width / viewport.height > 1.1;
    const size = wide ? viewport.height / 5 : viewport.width / 4.8;
    g.position.set(wide ? viewport.width * 0.24 : 0, wide ? 0.1 : 0.2, 0);
    g.scale.setScalar(size);

    // Faces the viewer; leans a little toward the mouse so the layered depths shift (parallax).
    g.rotation.x = THREE.MathUtils.damp(g.rotation.x, -0.2 - pointer.y * 0.3, 3, delta);
    g.rotation.y = THREE.MathUtils.damp(g.rotation.y, -0.38 + pointer.x * 0.35, 3, delta);
    if (turn.current) turn.current.rotation.z += delta * 0.08;
  });

  const breathe = (base: number, amp: number, speed: number) => (t: number) => base + Math.sin(t * speed) * amp;

  return (
    <group ref={group}>
      <group ref={turn} scale={[1, 1, 1.8]}>
        <group position={[0, 0, 0.55]}>
          <Core color={accent} />
        </group>
        {/* top tier */}
        <Ring r1={0.52} r2={0.535} color={accent} opacity={0.9} z={0.4} />
        <Ring r1={0.56} r2={0.6} color={accent} start={0.3} animateLen={breathe(4.2, 1.4, 0.9)} spin={0.6} opacity={0.85} z={0.4} />
        <Ring r1={0.66} r2={0.74} color={accent} segments={48} fill={0.62} len={5.4} spin={-0.25} opacity={0.4} z={0.3} depth={0.12} />
        <Ticks radius={0.79} count={120} short={0.025} long={0.06} every={10} color={accentSoft} opacity={0.8} spin={0.12} z={0.3} />
        {/* middle tier */}
        <Ring r1={0.9} r2={0.905} color={accentSoft} opacity={0.7} z={0.05} />
        <Ring r1={0.95} r2={1.0} color={accent} start={1.2} len={1.3} spin={0.35} opacity={0.9} z={0.05} />
        <Ring r1={0.95} r2={1.0} color={accent} start={3.6} len={0.7} spin={0.35} opacity={0.9} z={0.05} />
        <Ring r1={0.95} r2={1.0} color={accent} start={5.0} len={0.45} spin={0.35} opacity={0.9} z={0.05} />
        <Dots radius={1.08} count={140} color={accentSoft} size={0.016} spin={-0.18} z={0} />
        <Ring r1={1.15} r2={1.16} color={accentSoft} segments={90} fill={0.5} spin={0.08} opacity={0.6} z={-0.05} />
        <Sweep color={accent} />
        {/* bottom tier */}
        <Ring r1={1.26} r2={1.33} color={accent} segments={72} fill={0.78} start={0.8} len={4.6} spin={-0.1} opacity={0.3} z={-0.25} depth={0.14} />
        <Ticks radius={1.4} count={240} short={0.02} long={0.08} every={20} color={accentSoft} opacity={0.6} spin={-0.04} z={-0.3} />
        <Ring r1={1.52} r2={1.53} color={accentSoft} opacity={0.5} z={-0.35} />
        {[0, 1, 2, 3].map((i) => (
          <Ring key={i} r1={1.56} r2={1.6} color={accent} start={i * (Math.PI / 2) - 0.12} len={0.24} spin={0.05} opacity={0.95} z={-0.35} />
        ))}
        <Ring r1={1.7} r2={1.705} color={DEEP} segments={180} fill={0.35} spin={-0.03} opacity={0.6} z={-0.45} />
        <Projector color={accent} />
      </group>
    </group>
  );
}

export default function AiCore() {
  return (
    // Events come from the page so the hologram follows the mouse anywhere in the hero.
    <Canvas
      camera={{ position: [0, 0.6, 6], fov: 40 }}
      dpr={[1, 1.5]}
      gl={{ antialias: true, alpha: true }}
      eventSource={document.documentElement}
      eventPrefix="client"
    >
      <Hologram />
    </Canvas>
  );
}
