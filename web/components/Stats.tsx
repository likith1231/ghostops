"use client";

import { useEffect, useState } from "react";
import { api, type DayCount, type Summary } from "@/lib/api";

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
        <Kpi label="Incidents handled" value={s.total} hint="every run is logged, pass or fail" accent="bg-ghost" />
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
    </div>
  );
}
