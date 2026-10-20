"use client";

import { useEffect, useState } from "react";
import { api, type Governance } from "@/lib/api";

/** The kill switch: pauses every automatic action (PRs, rollbacks, auto-merge, canary promotion, chaos). */
export default function AutonomySwitch({ online }: { online: boolean | null }) {
  const [gov, setGov] = useState<Governance | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!online) return;
    const load = () => api.governance().then(setGov).catch(() => setGov(null));
    load();
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, [online]);

  if (!gov) return null;

  const toggle = async () => {
    if (!gov.paused && !window.confirm("Pause all automatic actions? PRs, rollbacks, auto-merge and chaos experiments stop until you resume.")) return;
    setBusy(true);
    try {
      setGov(gov.paused ? await api.resume() : await api.pause("paused from the dashboard"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      onClick={toggle}
      disabled={busy}
      title={gov.paused ? `Paused${gov.reason ? `: ${gov.reason}` : ""}. Click to resume.` : "Kill switch: click to pause every automatic action"}
      className={`flex items-center gap-2 rounded-lg border px-2.5 py-1 font-mono text-[11px] uppercase tracking-widest transition ${
        gov.paused ? "border-bad/50 bg-bad/15 text-bad" : "border-ok/30 bg-ok/10 text-ok hover:bg-ok/15"
      }`}
    >
      <span className={`relative h-3 w-6 rounded-full ${gov.paused ? "bg-bad/40" : "bg-ok/40"}`}>
        <span className={`absolute top-0.5 h-2 w-2 rounded-full bg-current transition-all ${gov.paused ? "left-0.5" : "left-3.5"}`} />
      </span>
      {gov.paused ? "autonomy paused" : "autonomy on"}
    </button>
  );
}
