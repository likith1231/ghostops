"use client";

import { useEffect, useRef } from "react";

// GhostOps' own components, placed as named nodes (fractions of the viewport).
const SYSTEM: [string, number, number][] = [
  ["prometheus", 0.08, 0.22],
  ["alertmanager", 0.22, 0.12],
  ["diagnostic-reasoner", 0.42, 0.2],
  ["patch-generator", 0.6, 0.1],
  ["validation-officer", 0.78, 0.24],
  ["opa-gate", 0.92, 0.42],
  ["sandbox", 0.84, 0.68],
  ["github", 0.64, 0.86],
  ["knowledge-base", 0.3, 0.93],
  ["sample-app", 0.12, 0.66],
];
// The order an incident travels through the system.
const PIPELINE = ["sample-app", "prometheus", "alertmanager", "diagnostic-reasoner", "patch-generator", "validation-officer", "opa-gate", "sandbox", "github"];

const CYAN = "56, 217, 255";
const VIOLET = "167, 139, 250";
const RED = "255, 77, 90";
const GREEN = "61, 255, 154";

interface Node {
  bx: number; // base position, px
  by: number;
  x: number;
  y: number;
  phase: number;
  label?: string;
  links: number[];
  flash: number; // 0..1 highlight after a pulse arrives
  flashColor: string;
}

interface Pulse {
  from: number;
  to: number;
  t: number; // 0..1 along the edge
  speed: number;
  color: string;
  hops: number; // how many more edges this signal may continue along
  route?: number[]; // fixed path (incident pulses)
}

/**
 * The page background: GhostOps drawn as a living network. Named nodes are the real
 * components; signals pass node to node like neural activity, an incident periodically
 * runs the pipeline red and arrives green, and the cursor links to nearby nodes.
 */
export default function NeuralBackdrop() {
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const el = canvas.current;
    const ctx = el?.getContext("2d");
    if (!el || !ctx) return;
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    // Canvas fonts can't read CSS variables: resolve the page's mono font family once.
    const mono = getComputedStyle(document.documentElement).getPropertyValue("--font-jetbrains").trim();
    const font = `10px ${mono || "ui-monospace"}, monospace`;

    let w = 0;
    let h = 0;
    let nodes: Node[] = [];
    let pulses: Pulse[] = [];
    const mouse = { x: -9999, y: -9999 };
    let frame = 0;
    let lastSpawn = 0;
    let lastIncident = 0;

    const build = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = window.innerWidth;
      h = window.innerHeight;
      el.width = w * dpr;
      el.height = h * dpr;
      el.style.width = `${w}px`;
      el.style.height = `${h}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // Generic nodes on a jittered grid, density scaled to the screen.
      const cols = Math.max(5, Math.round(w / 170));
      const rows = Math.max(4, Math.round(h / 150));
      const list: Node[] = [];
      let seed = 7;
      const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          if (rand() < 0.25) continue;
          const bx = ((c + 0.5 + (rand() - 0.5) * 0.7) / cols) * w;
          const by = ((r + 0.5 + (rand() - 0.5) * 0.7) / rows) * h;
          list.push({ bx, by, x: bx, y: by, phase: rand() * Math.PI * 2, links: [], flash: 0, flashColor: CYAN });
        }
      }
      for (const [label, fx, fy] of SYSTEM) {
        const bx = fx * w;
        const by = fy * h;
        list.push({ bx, by, x: bx, y: by, phase: rand() * 6, label, links: [], flash: 0, flashColor: CYAN });
      }
      // Each node links to its nearest neighbours.
      list.forEach((n, i) => {
        const nearest = list
          .map((m, j) => ({ j, d: (m.bx - n.bx) ** 2 + (m.by - n.by) ** 2 }))
          .filter((o) => o.j !== i)
          .sort((a, b) => a.d - b.d)
          .slice(0, 3);
        for (const { j } of nearest) {
          if (!n.links.includes(j)) n.links.push(j);
          if (!list[j].links.includes(i)) list[j].links.push(i);
        }
      });
      nodes = list;
      pulses = [];
    };

    const byLabel = (label: string) => nodes.findIndex((n) => n.label === label);

    const spawn = (now: number) => {
      if (now - lastSpawn > 260 && pulses.length < 26) {
        lastSpawn = now;
        const from = Math.floor(Math.random() * nodes.length);
        const links = nodes[from].links;
        if (links.length) {
          pulses.push({
            from,
            to: links[Math.floor(Math.random() * links.length)],
            t: 0,
            speed: 0.5 + Math.random() * 0.5,
            color: Math.random() < 0.15 ? VIOLET : CYAN,
            hops: 1 + Math.floor(Math.random() * 3),
          });
        }
      }
      // Every ~7s an incident runs the pipeline: red until it reaches github, which lights green.
      if (now - lastIncident > 7000) {
        lastIncident = now;
        const route = PIPELINE.map(byLabel).filter((i) => i >= 0);
        if (route.length > 1) {
          nodes[route[0]].flash = 1;
          nodes[route[0]].flashColor = RED;
          pulses.push({ from: route[0], to: route[1], t: 0, speed: 1.1, color: RED, hops: 0, route: route.slice(1) });
        }
      }
    };

    const draw = (now: number) => {
      ctx.clearRect(0, 0, w, h);
      const t = now / 1000;

      for (const n of nodes) {
        n.x = n.bx + Math.sin(t * 0.3 + n.phase) * 6;
        n.y = n.by + Math.cos(t * 0.25 + n.phase) * 6;
        n.flash = Math.max(0, n.flash - 0.012);
      }

      // Edges.
      ctx.lineWidth = 1;
      nodes.forEach((n, i) => {
        for (const j of n.links) {
          if (j < i) continue;
          const m = nodes[j];
          ctx.strokeStyle = `rgba(${CYAN}, ${n.label && m.label ? 0.13 : 0.06})`;
          ctx.beginPath();
          ctx.moveTo(n.x, n.y);
          ctx.lineTo(m.x, m.y);
          ctx.stroke();
        }
      });

      // Cursor: links to nearby nodes.
      for (const n of nodes) {
        const d = Math.hypot(n.x - mouse.x, n.y - mouse.y);
        if (d < 190) {
          const a = (1 - d / 190) * 0.45;
          ctx.strokeStyle = `rgba(${CYAN}, ${a})`;
          ctx.beginPath();
          ctx.moveTo(mouse.x, mouse.y);
          ctx.lineTo(n.x, n.y);
          ctx.stroke();
          n.flash = Math.max(n.flash, a);
          n.flashColor = CYAN;
        }
      }

      // Pulses with a short trail.
      pulses = pulses.filter((p) => {
        p.t += p.speed * 0.016;
        const a = nodes[p.from];
        const b = nodes[p.to];
        const x = a.x + (b.x - a.x) * p.t;
        const y = a.y + (b.y - a.y) * p.t;
        const tx = a.x + (b.x - a.x) * Math.max(0, p.t - 0.18);
        const ty = a.y + (b.y - a.y) * Math.max(0, p.t - 0.18);
        const grad = ctx.createLinearGradient(tx, ty, x, y);
        grad.addColorStop(0, `rgba(${p.color}, 0)`);
        grad.addColorStop(1, `rgba(${p.color}, 0.9)`);
        ctx.strokeStyle = grad;
        ctx.lineWidth = p.route ? 2 : 1.5;
        ctx.beginPath();
        ctx.moveTo(tx, ty);
        ctx.lineTo(x, y);
        ctx.stroke();
        ctx.fillStyle = `rgba(${p.color}, 1)`;
        ctx.shadowColor = `rgb(${p.color})`;
        ctx.shadowBlur = 10;
        ctx.beginPath();
        ctx.arc(x, y, p.route ? 2.6 : 1.8, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0;

        if (p.t < 1) return true;
        // Arrived: light the node, then carry on along the route or a random link.
        const arrived = nodes[p.to];
        if (p.route) {
          const rest = p.route.slice(1);
          const done = rest.length === 0;
          arrived.flash = 1;
          arrived.flashColor = done ? GREEN : RED;
          if (!done) pulses.push({ ...p, from: p.to, to: rest[0], t: 0, route: rest, color: rest.length === 1 ? GREEN : RED });
        } else {
          arrived.flash = Math.max(arrived.flash, 0.6);
          arrived.flashColor = p.color;
          if (p.hops > 0) {
            const next = arrived.links.filter((l) => l !== p.from);
            if (next.length) pulses.push({ ...p, from: p.to, to: next[Math.floor(Math.random() * next.length)], t: 0, hops: p.hops - 1 });
          }
        }
        return false;
      });

      // Nodes and labels.
      ctx.font = font;
      for (const n of nodes) {
        const r = n.label ? 3.2 : 1.6;
        const glow = n.flash;
        if (glow > 0.05) {
          ctx.fillStyle = `rgba(${n.flashColor}, ${glow * 0.25})`;
          ctx.beginPath();
          ctx.arc(n.x, n.y, r + 10 * glow, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.fillStyle = glow > 0.05 ? `rgba(${n.flashColor}, ${0.5 + glow * 0.5})` : `rgba(${CYAN}, ${n.label ? 0.55 : 0.28})`;
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fill();
        if (n.label) {
          ctx.strokeStyle = `rgba(${CYAN}, 0.35)`;
          ctx.beginPath();
          ctx.arc(n.x, n.y, r + 4, 0, Math.PI * 2);
          ctx.stroke();
          ctx.fillStyle = `rgba(${glow > 0.05 ? n.flashColor : CYAN}, ${0.32 + glow * 0.5})`;
          ctx.fillText(n.label, n.x + 11, n.y + 3.5);
        }
      }
    };

    const loop = (now: number) => {
      spawn(now);
      draw(now);
      frame = requestAnimationFrame(loop);
    };

    const onMove = (e: PointerEvent) => {
      mouse.x = e.clientX;
      mouse.y = e.clientY;
    };
    const onLeave = () => {
      mouse.x = mouse.y = -9999;
    };
    const onVisibility = () => {
      cancelAnimationFrame(frame);
      if (!document.hidden && !reduced) frame = requestAnimationFrame(loop);
    };

    build();
    if (reduced) draw(performance.now());
    else frame = requestAnimationFrame(loop);
    window.addEventListener("resize", build);
    window.addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("pointerleave", onLeave);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", build);
      window.removeEventListener("pointermove", onMove);
      document.removeEventListener("pointerleave", onLeave);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  return <canvas ref={canvas} className="absolute inset-0" />;
}
