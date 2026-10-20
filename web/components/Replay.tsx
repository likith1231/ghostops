"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useCallback, useEffect, useState } from "react";
import type { Incident } from "@/lib/api";
import CodeDiff from "./CodeDiff";

const STEPS = ["Alert", "Diagnosis", "Patch", "Validation", "Outcome"] as const;
const STEP_SECONDS = 3;

/** "Diagnosis: x | Validation: PASS | (y) | PR: z" -> its labelled parts. */
export function parts(summary: string | null) {
  const out: Record<string, string> = {};
  for (const piece of (summary ?? "").split(" | ")) {
    const m = /^(Diagnosis|Validation|PR skipped|PR): ?(.*)$/.exec(piece.trim());
    if (m) out[m[1]] = m[2];
    else if (piece.startsWith("(")) out.detail = piece.replace(/^\(|\)$/g, "");
  }
  return out;
}

/** Plays a stored incident back stage by stage, like rewinding the pipeline. */
export default function Replay({ incident, onClose }: { incident: Incident; onClose: () => void }) {
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(true);
  const info = parts(incident.outcome_summary);
  const passed = !!incident.validation_passed;

  const go = useCallback((s: number) => setStep(Math.max(0, Math.min(STEPS.length - 1, s))), []);

  useEffect(() => {
    if (!playing) return;
    if (step >= STEPS.length - 1) {
      setPlaying(false);
      return;
    }
    const id = setTimeout(() => setStep((s) => s + 1), STEP_SECONDS * 1000);
    return () => clearTimeout(id);
  }, [playing, step]);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") go(step + 1);
      if (e.key === "ArrowLeft") go(step - 1);
      if (e.key === " ") {
        e.preventDefault();
        setPlaying((p) => !p);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [go, onClose, step]);

  const body = [
    <div key="alert" className="space-y-2">
      <div className="font-mono text-2xl text-bad">{incident.alert_type}</div>
      <p className="text-mist">Fired {new Date(incident.timestamp).toLocaleString()} and handed to the pipeline.</p>
    </div>,
    <div key="diag" className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <span className="rounded-md bg-violet/15 px-2 py-0.5 font-mono text-xs text-violet">{incident.root_cause ?? "unknown"}</span>
        <span className="rounded-md bg-white/5 px-2 py-0.5 font-mono text-xs text-mist">confidence: {incident.confidence ?? "?"}</span>
      </div>
      <p className="text-white/85">{info.Diagnosis || "No diagnosis summary was stored for this run."}</p>
    </div>,
    <CodeDiff key="patch" diff={incident.patch_diff ?? ""} maxHeight="16rem" />,
    <div key="val" className="space-y-2">
      <div className={`text-2xl font-semibold ${passed ? "text-ok" : "text-bad"}`}>{passed ? "Passed" : "Rejected"}</div>
      <p className="text-white/80">{info.detail || (passed ? "OPA policy and sandboxed tests passed." : "Blocked by the policy check or the sandbox tests.")}</p>
    </div>,
    <div key="out" className="space-y-2">
      {incident.pr_url ? (
        <>
          <div className="text-2xl font-semibold text-ok">Pull request opened</div>
          <a href={incident.pr_url} target="_blank" rel="noreferrer" className="break-all text-arc hover:underline">
            {incident.pr_url}
          </a>
        </>
      ) : (
        <>
          <div className="text-2xl font-semibold text-warn">No PR</div>
          <p className="text-white/80">{info["PR skipped"] ? `Skipped: ${info["PR skipped"]}` : "Nothing reached a human — the gates held."}</p>
        </>
      )}
    </div>,
  ];

  return (
    <motion.div
      className="fixed inset-0 z-[200] grid place-items-center bg-void/80 p-4 backdrop-blur-md"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      onClick={onClose}
    >
      <motion.div
        role="dialog"
        aria-label={`Replay of incident ${incident.id}`}
        className="glass w-full max-w-3xl rounded-3xl p-6"
        initial={{ y: 30, scale: 0.97 }}
        animate={{ y: 0, scale: 1 }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-6 flex items-center justify-between">
          <div className="font-mono text-xs uppercase tracking-[0.3em] text-arc">
            <span className="text-arc/40">// </span>replay · incident #{incident.id}
          </div>
          <button onClick={onClose} className="font-mono text-xs text-mist hover:text-white" aria-label="Close replay">
            esc ✕
          </button>
        </div>

        {/* timeline */}
        <div className="relative mb-8">
          <div className="absolute left-0 right-0 top-3 h-px bg-white/10" />
          <motion.div className="absolute left-0 top-3 h-px bg-arc" animate={{ width: `${(step / (STEPS.length - 1)) * 100}%` }} transition={{ duration: 0.5 }} />
          <div className="relative flex justify-between">
            {STEPS.map((name, i) => (
              <button key={name} onClick={() => { go(i); setPlaying(false); }} className="flex flex-col items-center gap-2">
                <span className={`grid h-6 w-6 place-items-center rounded-full border text-[10px] transition ${i <= step ? "border-arc bg-arc/20 text-arc shadow-[0_0_14px_rgba(56,217,255,0.5)]" : "border-white/15 bg-void text-mist"}`}>
                  {i + 1}
                </span>
                <span className={`font-mono text-[10px] uppercase tracking-widest ${i === step ? "text-white" : "text-mist"}`}>{name}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-48">
          <AnimatePresence mode="wait">
            <motion.div key={step} initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -24 }} transition={{ duration: 0.3 }}>
              {body[step]}
            </motion.div>
          </AnimatePresence>
        </div>

        <div className="mt-6 flex items-center justify-between font-mono text-xs">
          <span className="text-mist">← → step · space play/pause</span>
          <div className="flex gap-2">
            <button onClick={() => go(step - 1)} className="cut-corners border border-arc/30 px-3 py-1.5 text-white hover:bg-arc/15">prev</button>
            <button
              onClick={() => {
                if (step === STEPS.length - 1) setStep(0);
                setPlaying((p) => step === STEPS.length - 1 || !p);
              }}
              className="cut-corners bg-arc px-4 py-1.5 font-semibold text-void"
            >
              {playing ? "pause" : step === STEPS.length - 1 ? "replay" : "play"}
            </button>
            <button onClick={() => go(step + 1)} className="cut-corners border border-arc/30 px-3 py-1.5 text-white hover:bg-arc/15">next</button>
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}
