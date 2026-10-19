"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useState } from "react";
import { api, type Detection } from "@/lib/api";

const PRESETS: { label: string; body: Record<string, unknown> }[] = [
  {
    label: "Null-pointer traceback",
    body: {
      logs: [
        "Traceback (most recent call last):",
        '  File "/app/user_service.py", line 47, in get_profile',
        "    return resp['data']['name']",
        "TypeError: 'NoneType' object is not subscriptable",
      ],
    },
  },
  { label: "Memory leak", body: { memory_samples: [27e6, 64e6, 98e6, 121e6], memory_limit: 134217728 } },
  { label: "Canary regression", body: { canary_error_rate: 0.15, stable_error_rate: 0.001 } },
  {
    label: "Auth cascade",
    body: { auth_events: Array.from({ length: 14 }, (_, i) => ({ ts: 1000 + i * 2, service: ["api", "cart", "billing"][i % 3], line: "401 Unauthorized" })) },
  },
  { label: "HPA thrashing", body: { replicas: [3, 11, 3, 12, 4, 11, 3] } },
  { label: "Config drift", body: { declared_config: { timeout: "30s", retries: "3" }, live_config: { timeout: "120s", retries: "3" } } },
  { label: "SLO breach", body: { p99_seconds: 3.1, errors: 45, requests: 1000 } },
  { label: "Healthy service", body: { p99_seconds: 0.12, errors: 1, requests: 1000, memory_samples: [80e6, 81e6, 80e6, 81e6] } },
];

const SEVERITY: Record<Detection["severity"], string> = {
  critical: "text-bad border-bad/40 bg-bad/10",
  high: "text-warn border-warn/40 bg-warn/10",
  medium: "text-violet border-violet/40 bg-violet/10",
  low: "text-mist border-white/10 bg-white/5",
};

export default function DetectorLab({ online }: { online: boolean | null }) {
  const [body, setBody] = useState<Record<string, unknown> | null>(null);
  const [logs, setLogs] = useState("");
  const [results, setResults] = useState<Detection[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (payload: Record<string, unknown>) => {
    setBody(payload);
    setError(null);
    try {
      setResults((await api.detect(payload)).detections);
    } catch (e) {
      setResults(null);
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="grid gap-6 lg:grid-cols-5">
      <div className="space-y-4 lg:col-span-2">
        <div className="flex flex-wrap gap-2">
          {PRESETS.map((p) => (
            <button
              key={p.label}
              disabled={!online}
              onClick={() => run(p.body)}
              className="rounded-full border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-white/80 transition hover:border-ghost/50 hover:text-white disabled:opacity-40"
            >
              {p.label}
            </button>
          ))}
        </div>
        <div className="glass rounded-2xl p-4">
          <label htmlFor="logs" className="mb-2 block font-mono text-[11px] uppercase tracking-[0.25em] text-mist">
            Or paste raw logs
          </label>
          <textarea
            id="logs"
            value={logs}
            onChange={(e) => setLogs(e.target.value)}
            rows={6}
            placeholder={"ModuleNotFoundError: No module named 'requests'"}
            className="w-full resize-y rounded-xl border border-white/10 bg-black/40 p-3 font-mono text-xs text-white outline-none focus:border-ghost/50"
          />
          <button
            disabled={!online || !logs.trim()}
            onClick={() => run({ logs: logs.split("\n") })}
            className="mt-3 rounded-full bg-ghost px-4 py-1.5 text-sm font-semibold text-void disabled:opacity-40"
          >
            Scan logs
          </button>
        </div>
        {body && (
          <details className="glass rounded-2xl p-4 text-xs">
            <summary className="cursor-pointer font-mono uppercase tracking-[0.2em] text-mist">request sent to /api/detect</summary>
            <pre className="scrollbar-thin mt-3 max-h-56 overflow-auto font-mono text-[11px] text-white/70">{JSON.stringify(body, null, 2)}</pre>
          </details>
        )}
      </div>

      <div className="lg:col-span-3">
        {error && <div className="mb-3 rounded-xl border border-bad/30 bg-bad/10 p-3 text-sm text-bad">{error}</div>}
        {results === null ? (
          <div className="glass grid h-full min-h-56 place-items-center rounded-2xl p-6 text-center text-sm text-mist">
            Pick a scenario. These detectors are deterministic Python — no LLM, no API key.
          </div>
        ) : results.length === 0 ? (
          <div className="glass grid min-h-56 place-items-center rounded-2xl p-6 text-center">
            <div>
              <div className="text-2xl font-semibold text-ok">All clear</div>
              <div className="mt-1 text-sm text-mist">No anomaly crossed a detector threshold.</div>
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            <AnimatePresence initial={false}>
              {results.map((d, i) => (
                <motion.div
                  key={`${d.use_case}-${i}-${JSON.stringify(body)}`}
                  initial={{ opacity: 0, x: 24, rotateY: -12 }}
                  animate={{ opacity: 1, x: 0, rotateY: 0 }}
                  transition={{ delay: i * 0.08, duration: 0.45 }}
                  className="glass rounded-2xl p-5"
                >
                  <div className="flex flex-wrap items-center gap-3">
                    <span className="font-mono text-2xl font-semibold text-white">{d.use_case}</span>
                    <span className="font-semibold text-white">{d.title}</span>
                    <span className={`ml-auto rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-widest ${SEVERITY[d.severity]}`}>
                      {d.severity}
                    </span>
                  </div>
                  <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-white/5">
                    <motion.div
                      className="h-full bg-gradient-to-r from-ghost to-violet"
                      initial={{ width: 0 }}
                      animate={{ width: `${d.confidence * 100}%` }}
                      transition={{ duration: 0.8, delay: 0.2 + i * 0.08 }}
                    />
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-mist">confidence {(d.confidence * 100).toFixed(0)}%</div>
                  <ul className="mt-3 space-y-1 font-mono text-[12px] text-white/70">
                    {d.evidence.map((e) => <li key={e} className="break-words">› {e}</li>)}
                  </ul>
                  <p className="mt-3 text-sm text-ghost">→ {d.suggested_action}</p>
                </motion.div>
              ))}
            </AnimatePresence>
          </div>
        )}
      </div>
    </div>
  );
}
