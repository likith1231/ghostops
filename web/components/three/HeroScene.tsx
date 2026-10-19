"use client";

import { Float, Sparkles, Stars } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Bloom, EffectComposer, Vignette } from "@react-three/postprocessing";
import { useMemo, useRef } from "react";
import * as THREE from "three";

/** A lathe-turned ghost whose skirt ripples like cloth. */
function Ghost() {
  const leftEye = useRef<THREE.Mesh>(null);
  const rightEye = useRef<THREE.Mesh>(null);

  const geometry = useMemo(() => {
    const profile: THREE.Vector2[] = [];
    for (let i = 0; i <= 18; i++) {
      const a = (i / 18) * (Math.PI / 2);
      profile.push(new THREE.Vector2(Math.sin(a), Math.cos(a) + 0.2));
    }
    for (let i = 1; i <= 14; i++) {
      const t = i / 14;
      profile.push(new THREE.Vector2(1 + 0.1 * t, 0.2 - 1.35 * t));
    }
    const g = new THREE.LatheGeometry(profile, 120);
    g.userData.base = Float32Array.from(g.attributes.position.array);
    return g;
  }, []);

  useFrame(({ clock }) => {
    const t = clock.elapsedTime;
    const pos = geometry.attributes.position as THREE.BufferAttribute;
    const base = geometry.userData.base as Float32Array;
    for (let i = 0; i < pos.count; i++) {
      const x = base[i * 3];
      const y = base[i * 3 + 1];
      const z = base[i * 3 + 2];
      const k = THREE.MathUtils.smoothstep(-y, 0.25, 1.15);
      const angle = Math.atan2(z, x);
      const r = 1 + Math.sin(angle * 5 - t * 2) * 0.04 * k;
      pos.setXYZ(i, x * r, y + Math.sin(angle * 7 + t * 3) * 0.13 * k, z * r);
    }
    pos.needsUpdate = true;
    geometry.computeVertexNormals();

    // Blink every few seconds.
    const blink = (t % 4.2) < 0.12 ? 0.1 : 1;
    leftEye.current?.scale.set(1, 1.45 * blink, 0.5);
    rightEye.current?.scale.set(1, 1.45 * blink, 0.5);
  });

  return (
    <group>
      <mesh geometry={geometry}>
        <meshPhysicalMaterial
          color="#e6fffb"
          emissive="#1de9b6"
          emissiveIntensity={0.32}
          roughness={0.22}
          metalness={0.05}
          clearcoat={1}
          transparent
          opacity={0.93}
          side={THREE.DoubleSide}
        />
      </mesh>
      <mesh ref={leftEye} position={[-0.33, 0.62, 0.86]}>
        <sphereGeometry args={[0.12, 24, 24]} />
        <meshBasicMaterial color="#05060a" />
      </mesh>
      <mesh ref={rightEye} position={[0.33, 0.62, 0.86]}>
        <sphereGeometry args={[0.12, 24, 24]} />
        <meshBasicMaterial color="#05060a" />
      </mesh>
    </group>
  );
}

/** Thin glowing orbits with a data packet riding each one. */
function Orbit({ radius, tilt, speed, color }: { radius: number; tilt: [number, number, number]; speed: number; color: string }) {
  const packet = useRef<THREE.Mesh>(null);
  useFrame(({ clock }) => {
    const a = clock.elapsedTime * speed;
    packet.current?.position.set(Math.cos(a) * radius, Math.sin(a) * radius, 0);
  });
  return (
    <group rotation={tilt}>
      <mesh>
        <torusGeometry args={[radius, 0.006, 8, 160]} />
        <meshBasicMaterial color={color} transparent opacity={0.55} />
      </mesh>
      <mesh ref={packet}>
        <sphereGeometry args={[0.05, 16, 16]} />
        <meshBasicMaterial color={color} toneMapped={false} />
      </mesh>
    </group>
  );
}

/** Keeps the ghost clear of the headline: right of it on wide screens, low and small on phones. */
function Placement({ children }: { children: React.ReactNode }) {
  const { size } = useThree();
  const aspect = size.width / size.height;
  const [position, scale]: [[number, number, number], number] =
    aspect < 0.8 ? [[0.6, -1.9, -1.5], 0.8] : aspect < 1.25 ? [[1.4, -0.2, 0], 0.9] : [[2.4, 0, 0], 1];
  return (
    <group position={position} scale={scale}>
      {children}
    </group>
  );
}

function Rig({ children }: { children: React.ReactNode }) {
  const group = useRef<THREE.Group>(null);
  useFrame(({ pointer }, delta) => {
    if (!group.current) return;
    group.current.rotation.y = THREE.MathUtils.damp(group.current.rotation.y, pointer.x * 0.45, 3, delta);
    group.current.rotation.x = THREE.MathUtils.damp(group.current.rotation.x, -pointer.y * 0.2, 3, delta);
  });
  return <group ref={group}>{children}</group>;
}

export default function HeroScene() {
  return (
    <Canvas camera={{ position: [0, 0.2, 6.2], fov: 45 }} dpr={[1, 2]} gl={{ antialias: true, alpha: true }}>
      <ambientLight intensity={0.35} />
      <pointLight position={[3, 3, 3]} intensity={40} color="#7cf7e4" />
      <pointLight position={[-3, -1, 2]} intensity={30} color="#a78bfa" />
      <Stars radius={60} depth={40} count={2500} factor={3} saturation={0} fade speed={0.6} />
      <Placement>
        <Rig>
          <Float speed={1.6} rotationIntensity={0.25} floatIntensity={1.1}>
            <Ghost />
          </Float>
          <Orbit radius={1.95} tilt={[1.2, 0.2, 0]} speed={0.9} color="#7cf7e4" />
          <Orbit radius={2.35} tilt={[1.0, -0.5, 0.3]} speed={-0.6} color="#a78bfa" />
          <Orbit radius={2.8} tilt={[1.4, 0.6, -0.2]} speed={0.4} color="#4ade80" />
          <Sparkles count={70} scale={[7, 5, 4]} size={2.2} speed={0.35} color="#7cf7e4" />
        </Rig>
      </Placement>
      <EffectComposer>
        <Bloom mipmapBlur luminanceThreshold={0.25} intensity={1.1} />
        <Vignette eskil={false} offset={0.2} darkness={0.75} />
      </EffectComposer>
    </Canvas>
  );
}
