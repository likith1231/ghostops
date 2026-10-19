"use client";

import { Line } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Bloom, EffectComposer } from "@react-three/postprocessing";
import { useMemo, useRef, type RefObject } from "react";
import * as THREE from "three";

export type NodeState = "idle" | "running" | "done" | "error" | "held";

export const NODE_COLORS: Record<NodeState, string> = {
  idle: "#2b3354",
  running: "#38d9ff",
  done: "#4ade80",
  error: "#fb7185",
  held: "#fbbf24",
};

const LABELS = ["Alert", "Diagnose", "Patch", "Validate", "Pull request"];
const STATE_TEXT: Record<NodeState, string> = {
  idle: "waiting",
  running: "working…",
  done: "done",
  error: "blocked",
  held: "held (mock)",
};

/** Nodes run left-to-right on wide canvases and top-to-bottom (labels beside them) on narrow ones. */
function useLayout(count: number): { points: THREE.Vector3[]; vertical: boolean } {
  const { size } = useThree();
  const vertical = size.width / size.height < 0.9;
  const points = useMemo(
    () =>
      Array.from({ length: count }, (_, i) => {
        const u = i / (count - 1) - 0.5;
        return vertical
          ? new THREE.Vector3(Math.sin(u * Math.PI) * 0.5 - 1.3, -u * 9.5 - 0.3, -Math.abs(u) * 1.2)
          : new THREE.Vector3(u * 12, Math.sin(u * Math.PI) * 0.35, -Math.abs(u) * 2.4);
      }),
    [count, vertical],
  );
  return { points, vertical };
}

function PipelineNode({ position, state }: { position: THREE.Vector3; state: NodeState }) {
  const shell = useRef<THREE.Mesh>(null);
  const core = useRef<THREE.Mesh>(null);
  const color = NODE_COLORS[state];

  useFrame(({ clock }, delta) => {
    const t = clock.elapsedTime;
    if (shell.current) {
      shell.current.rotation.x += delta * (state === "running" ? 1.6 : 0.25);
      shell.current.rotation.y += delta * (state === "running" ? 1.1 : 0.2);
    }
    if (core.current) {
      const s = state === "running" ? 1 + Math.sin(t * 6) * 0.18 : 1;
      core.current.scale.setScalar(s);
    }
  });

  return (
    <group position={position}>
      <mesh ref={shell}>
        <icosahedronGeometry args={[0.72, 1]} />
        <meshBasicMaterial color={color} wireframe transparent opacity={state === "idle" ? 0.35 : 0.85} />
      </mesh>
      <mesh ref={core}>
        <sphereGeometry args={[0.34, 32, 32]} />
        <meshStandardMaterial
          color={color}
          emissive={color}
          emissiveIntensity={state === "idle" ? 0.15 : 2.2}
          toneMapped={false}
        />
      </mesh>
    </group>
  );
}

/** A bright packet travelling into the node that is currently working. */
function Packet({ from, to }: { from: THREE.Vector3; to: THREE.Vector3 }) {
  const ref = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    const t = (clock.elapsedTime * 0.9) % 1;
    ref.current?.position.lerpVectors(from, to, t);
  });
  return (
    <mesh ref={ref}>
      <sphereGeometry args={[0.11, 16, 16]} />
      <meshBasicMaterial color="#ffffff" toneMapped={false} />
    </mesh>
  );
}

/**
 * Pins the DOM labels under their nodes by projecting each node to screen space every frame.
 * (Plain DOM rather than drei's <Html>, whose first instance fails to mount with React 19.)
 */
function LabelSync({ points, vertical, labels }: { points: THREE.Vector3[]; vertical: boolean; labels: RefObject<(HTMLDivElement | null)[]> }) {
  const { camera, size } = useThree();
  const v = useMemo(() => new THREE.Vector3(), []);
  useFrame(() => {
    points.forEach((p, i) => {
      const el = labels.current?.[i];
      if (!el) return;
      if (vertical) v.set(p.x + 1.05, p.y, p.z);
      else v.set(p.x, p.y - 1.15, p.z);
      v.project(camera);
      const x = ((v.x + 1) / 2) * size.width;
      const y = ((1 - v.y) / 2) * size.height;
      el.style.transform = `translate3d(${x}px, ${y}px, 0) ${vertical ? "translate(0, -50%)" : "translate(-50%, 0)"}`;
      el.style.textAlign = vertical ? "left" : "center";
      el.style.opacity = v.z < 1 ? "1" : "0";
    });
  });
  return null;
}

function Pipeline({ states, labels }: { states: NodeState[]; labels: RefObject<(HTMLDivElement | null)[]> }) {
  const { points, vertical } = useLayout(states.length);
  const running = states.findIndex((s) => s === "running");

  return (
    <group>
      {points.slice(1).map((p, i) => {
        const lit = states[i] === "done" && states[i + 1] !== "idle";
        return (
          <Line
            key={i}
            points={[points[i], p]}
            color={lit ? NODE_COLORS[states[i + 1]] : "#39406a"}
            lineWidth={lit ? 2.4 : 1.2}
            dashed={!lit}
            dashSize={0.18}
            gapSize={0.14}
            transparent
            opacity={lit ? 0.9 : 0.6}
          />
        );
      })}
      {running > 0 && <Packet from={points[running - 1]} to={points[running]} />}
      {points.map((p, i) => (
        <PipelineNode key={i} position={p} state={states[i]} />
      ))}
      <LabelSync points={points} vertical={vertical} labels={labels} />
    </group>
  );
}

function CameraFit() {
  const { camera, size } = useThree();
  const vertical = size.width / size.height < 0.9;
  useFrame(({ pointer }, delta) => {
    const targetZ = vertical ? 14 : Math.max(10, 15.5 - (size.width / size.height) * 2.3);
    camera.position.x = THREE.MathUtils.damp(camera.position.x, pointer.x * 1.2, 2, delta);
    camera.position.y = THREE.MathUtils.damp(camera.position.y, 1.6 + pointer.y * 0.8, 2, delta);
    camera.position.z = THREE.MathUtils.damp(camera.position.z, targetZ, 2, delta);
    camera.lookAt(0, 0, -0.8);
  });
  return null;
}

export default function PipelineScene({ states }: { states: NodeState[] }) {
  const labels = useRef<(HTMLDivElement | null)[]>([]);
  return (
    <div className="relative h-full w-full">
      <Canvas camera={{ position: [0, 1.6, 12], fov: 42 }} dpr={[1, 2]} gl={{ antialias: true, alpha: true }}>
        <ambientLight intensity={0.4} />
        <pointLight position={[0, 4, 6]} intensity={30} color="#a78bfa" />
        <CameraFit />
        <Pipeline states={states} labels={labels} />
        <EffectComposer>
          <Bloom mipmapBlur luminanceThreshold={0.3} intensity={0.9} />
        </EffectComposer>
      </Canvas>
      <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-live="polite">
        {LABELS.map((label, i) => (
          <div
            key={label}
            ref={(el) => {
              labels.current[i] = el;
            }}
            className="absolute left-0 top-0 select-none whitespace-nowrap text-center opacity-0"
          >
            <div className="font-sans text-[15px] font-semibold tracking-wide text-white">{label}</div>
            <div className="font-mono text-[11px] uppercase tracking-[0.2em]" style={{ color: NODE_COLORS[states[i]] }}>
              {STATE_TEXT[states[i]]}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
