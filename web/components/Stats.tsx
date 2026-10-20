"use client";

import { useEffect, useState } from "react";
import { api, type DayCount, type Feedback, type Summary } from "@/lib/api";

function CountUp({ value, decimals = 0, suffix = "" }: { value: number; decimals?: number; suffix?: string }) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    let frame = 0;
    const start = performance.now();
    const from = 0;
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / 900);
      const eased = 1 - Math.pow(1 - t, 3);
      setShown(from + (value - from) * eased);
      if (t < 1) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [value]);
  return (
    <>
      {shown.toFixed(decimals)}
      {suffix}
    </>
  );
}

function Kpi({ label, value, hint, accent, decimals, suffix }: { label: string; value: number; hint: string; accent: string; decimals?: number; suffix?: string }) {
  return (
    <div className="glass group relative overflow-hidden rounded-2xl p-5">
      <div className={`absolute -right-10 -top-10 h-32 w-32 rounded-full opacity-20 blur-2xl transition group-hover:opacity-40 ${accent}`} />
      <div className="font-mono text-[11px] uppercase tracking-[0.25em] text-mist">{label}</div>
      <div className="mt-2 text-4xl font-semibold tracking-tight text-white sm:text-5xl">
        <CountUp value={value} decimals={decimals} suffix={suffix} />
      </div>
      <div className="mt-2 text-xs text-mist">{hint}</div>
    </div>
  );
}

function Bars({ days }: { days: DayCount[] }) {
  const series = [...days].reverse().slice(-14);
  const max = Math.max(1, ...series.map((d) => d.passed + d.failed));
  if (!series.length) return <p className="text-sm text-mist">No incidents yet — trigger one above.</p>;
  return (
    <div className="flex h-40 items-end gap-2">
      {series.map((d) => {
        const total = d.passed + d.failed;
        return (
          <div key={d.date} className="flex h-full flex-1 flex-col items-center gap-2" title={`${d.passed} passed · ${d.failed} failed`}>
            <div className="flex w-full flex-1 items-end justify-center">
              <div className="flex w-full max-w-10 flex-col-reverse overflow-hidden rounded-md" style={{ height: `${(total / max) * 100}%` }}>
                <div className="bg-ok/80" style={{ flex: d.passed }} />
                <div className="bg-bad/80" style={{ flex: d.failed }} />
              </div>
            </div>
            <span className="font-mono text-[10px] text-mist">{d.date.slice(5)}</span>
          </div>
        );
      })}
    </div>
  );
}

/** What human reviewers did with GhostOps' PRs, synced from GitHub on demand. */
function HumanReview() {
  const [fb, setFb] = useState<Feedback | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    api.feedback().then(setFb).catch(() => setFb(null));
  }, []);

  const sync = async () => {
    setSyncing(true);
    setNote(null);
    try {
      const r = await api.syncFeedback();
      setFb(r);
      setNote(r.failed ? `${r.failed} PR(s) could not be read from GitHub` : `checked ${r.checked} PR(s) on GitHub`);
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setSyncing(false);
    }
  };

  const segments: [keyof Feedback, string, string][] = [
    ["merged", "merged", "bg-ok"],
    ["open", "awaiting review", "bg-arc"],
    ["closed", "rejected", "bg-bad"],
    ["superseded", "duplicates", "bg-white/25"],
  ];
  const total = fb?.prs || 1;

  return (
    <div className="glass rounded-2xl p-5 lg:col-span-3">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="font-mono text-[11px] uppercase tracking-[0.25em] text-mist">Human review of GhostOps&apos; PRs</div>
          <div className="mt-2 text-4xl font-semibold text-white">
            {fb?.acceptance_rate == null ? "—" : `${fb.acceptance_rate}%`}
            <span className="ml-2 text-sm font-normal text-mist">accepted (merged ÷ merged + rejected)</span>
          </div>
        </div>
        <button onClick={sync} disabled={syncing} className="cut-corners border border-arc/30 bg-arc/5 px-4 py-2 font-mono text-xs text-white transition hover:bg-arc/15 disabled:opacity-50">
          {syncing ? "syncing…" : "↻ sync from GitHub"}
        </button>
      </div>
      <div className="mt-4 flex h-2.5 overflow-hidden rounded-full bg-white/5">
        {fb && segments.map(([key, , color]) => <div key={key} className={color} style={{ width: `${((fb[key] as number) / total) * 100}%` }} />)}
      </div>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-mist">
        {segments.map(([key, label, color]) => (
          <span key={key} className="flex items-center gap-1.5">
            <i className={`h-2 w-2 rounded-sm ${color}`} />
            {fb ? (fb[key] as number) : 0} {label}
          </span>
        ))}
        <span className="ml-auto font-mono">
          {note ?? (fb?.last_checked ? `last synced ${new Date(fb.last_checked).toLocaleString()}` : "never synced")}
        </span>
      </div>
      <p className="mt-3 text-xs text-mist">
        Every new diagnosis is given past incidents together with what the reviewer did with their fix. Label a PR <code className="text-arc">duplicate</code> on GitHub to keep it out of the rate.
      </p>
    </div>
  );
}

export default function Stats({ refreshKey }: { refreshKey: number }) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [days, setDays] = useState<DayCount[]>([]);

  useEffect(() => {
    api.summary().then(setSummary).catch(() => setSummary(null));
    api.timeseries().then(setDays).catch(() => setDays([]));
  }, [refreshKey]);

  const s = summary ?? { total: 0, passed: 0, failed: 0, pass_rate: 0, prs_created: 0 };
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="grid grid-cols-2 gap-4 lg:col-span-2">
        <Kpi label="Incidents handled" value={s.total} hint="every run is logged, pass or fail" accent="bg-arc" />
        <Kpi label="Validation pass rate" value={s.pass_rate} decimals={1} suffix="%" hint="OPA policy + sandboxed tests" accent="bg-ok" />
        <Kpi label="Pull requests" value={s.prs_created} hint="opened for human review" accent="bg-violet" />
        <Kpi label="Blocked by gates" value={s.failed} hint="bad patches that never reached anyone" accent="bg-bad" />
      </div>
      <div className="glass rounded-2xl p-5">
        <div className="mb-4 flex items-center justify-between">
          <span className="font-mono text-[11px] uppercase tracking-[0.25em] text-mist">Daily outcomes</span>
          <span className="flex gap-3 text-[11px] text-mist">
            <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-sm bg-ok" />passed</span>
            <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-sm bg-bad" />failed</span>
          </span>
        </div>
        <Bars days={days} />
      </div>
      <HumanReview />
    </div>
  );
}
