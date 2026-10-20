"use client";

import { motion } from "framer-motion";

type Points = { ts?: string; value: number }[];

const MiB = 1024 * 1024;

/**
 * Memory during a leak run: the real samples the pipeline was given, climbing toward the
 * container limit, and, once the fix is validated, the projected bounded line after it.
 */
export default function MemoryChart({ metrics, fixed }: { metrics: Record<string, Points>; fixed: boolean | null }) {
  const usageKey = Object.keys(metrics).find((k) => k.includes("memory") && !k.includes("limit"));
  const limitKey = Object.keys(metrics).find((k) => k.includes("limit"));
  const usage = usageKey ? metrics[usageKey].map((p) => p.value) : [];
  if (usage.length < 2) return null;
  const limit = limitKey ? metrics[limitKey].at(-1)?.value ?? null : null;

  const W = 640;
  const H = 200;
  const pad = { l: 46, r: 16, t: 14, b: 26 };
  const max = Math.max(limit ?? 0, ...usage) * 1.12;
  const steps = usage.length - 1;
  // The projection continues for as many steps again after the last real sample.
  const total = steps * 2;
  const x = (i: number) => pad.l + (i / total) * (W - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - v / max) * (H - pad.t - pad.b);
  const real = usage.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ");
  const area = `${real} L${x(steps)},${y(0)} L${x(0)},${y(0)} Z`;
  const after = `M${x(steps)},${y(usage[steps])} L${x(steps + 0.6)},${y(usage[0])} L${x(total)},${y(usage[0])}`;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * max);

  return (
    <div className="glass rounded-2xl p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold text-white">Memory — container working set</h3>
        <div className="flex gap-4 font-mono text-[10px] uppercase tracking-[0.2em]">
          <span className="text-arc">● observed</span>
          {limit && <span className="text-bad">— limit</span>}
          {fixed && <span className="text-ok">┄ after fix (projected)</span>}
        </div>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Memory usage chart">
        <defs>
          <linearGradient id="memfill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#38d9ff" stopOpacity="0.35" />
            <stop offset="100%" stopColor="#38d9ff" stopOpacity="0" />
          </linearGradient>
        </defs>
        {ticks.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} stroke="rgba(255,255,255,0.06)" />
            <text x={pad.l - 8} y={y(v) + 3} textAnchor="end" className="fill-mist font-mono text-[6.5px]">
              {Math.round(v / MiB)} MiB
            </text>
          </g>
        ))}
        {limit && (
          <>
            <line x1={pad.l} x2={W - pad.r} y1={y(limit)} y2={y(limit)} stroke="#fb7185" strokeDasharray="6 5" strokeOpacity="0.8" />
            <text x={W - pad.r} y={y(limit) - 6} textAnchor="end" className="fill-bad font-mono text-[6.5px]">
              limit {Math.round(limit / MiB)} MiB
            </text>
          </>
        )}
        <motion.path d={area} fill="url(#memfill)" initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 1.2 }} />
        <motion.path
          d={real}
          fill="none"
          stroke="#38d9ff"
          strokeWidth={2.5}
          strokeLinecap="round"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 1.6, ease: "easeOut" }}
        />
        {usage.map((v, i) => (
          <motion.circle key={i} cx={x(i)} cy={y(v)} r={3.5} fill="#38d9ff" initial={{ scale: 0 }} animate={{ scale: 1 }} transition={{ delay: 0.3 + i * 0.4 }} />
        ))}
        {fixed && (
          <motion.path
            d={after}
            fill="none"
            stroke="#4ade80"
            strokeWidth={2.5}
            strokeDasharray="7 6"
            initial={{ pathLength: 0 }}
            animate={{ pathLength: 1 }}
            transition={{ duration: 1.4, ease: "easeInOut" }}
          />
        )}
        <text x={x(steps)} y={H - 8} textAnchor="middle" className="fill-mist font-mono text-[6.5px]">
          alert
        </text>
      </svg>
      <p className="mt-2 text-xs text-mist">
        {fixed === null
          ? "Samples the pipeline received with the alert."
          : fixed
            ? "The validated fix bounds the store, so memory stops growing — the green line is the expected shape once it is deployed."
            : "No validated fix: memory would keep climbing toward the limit."}
      </p>
    </div>
  );
}
