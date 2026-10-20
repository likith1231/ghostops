"use client";

import { useEffect, useState } from "react";
import { api, type AuditTrail, type CorrelatedIncident, type FixSkill } from "@/lib/api";

const TABS = [
  ["audit", "Audit trail"],
  ["incidents", "Correlated incidents"],
  ["skills", "Fix library"],
] as const;

const DECISION_TONE = (d: string) =>
  /blocked|failed|rejected|reopened|paused|high|elevated|not_fixed/.test(d) ? "text-bad" : /passed|opened|resolved|signed|verified|done|survived|promoted|merged|reproduced/.test(d) ? "text-ok" : "text-arc";

/** The tamper-evident audit trail, how alerts were grouped, and skills learned from humans. */
export default function TrustSection({ refreshKey }: { refreshKey: number }) {
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("audit");
  const [audit, setAudit] = useState<AuditTrail | null>(null);
  const [incidents, setIncidents] = useState<CorrelatedIncident[]>([]);
  const [skills, setSkills] = useState<FixSkill[]>([]);

  useEffect(() => {
    api.audit(40).then(setAudit).catch(() => setAudit(null));
    api.correlated().then(setIncidents).catch(() => setIncidents([]));
    api.fixLibrary().then(setSkills).catch(() => setSkills([]));
  }, [refreshKey]);

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`rounded-full border px-4 py-1.5 text-sm transition ${tab === key ? "border-arc/60 bg-arc/15 text-white" : "border-white/10 text-mist hover:text-white"}`}
          >
            {label}
          </button>
        ))}
        {audit && (
          <span className={`ml-auto rounded-full px-3 py-1 font-mono text-[11px] uppercase tracking-widest ${audit.chain.valid ? "bg-ok/15 text-ok" : "bg-bad/15 text-bad"}`}>
            {audit.chain.valid ? `hash chain intact · ${audit.chain.entries} entries` : `chain broken at #${audit.chain.broken_at}`}
          </span>
        )}
      </div>

      <div className="glass scrollbar-thin max-h-[26rem] overflow-y-auto rounded-2xl">
        {tab === "audit" &&
          (audit?.entries.length ? (
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-ink/95 text-mist">
                <tr>{["time", "actor", "action", "decision", "why", "hash"].map((h) => <th key={h} className="px-4 py-2 font-mono font-normal uppercase tracking-widest">{h}</th>)}</tr>
              </thead>
              <tbody>
                {audit.entries.map((e) => (
                  <tr key={e.id} className="border-t border-white/5">
                    <td className="whitespace-nowrap px-4 py-2 font-mono text-mist">{new Date(e.ts).toLocaleTimeString()}</td>
                    <td className="px-4 py-2 text-white/70">{e.actor}</td>
                    <td className="px-4 py-2 font-mono text-white">{e.action}</td>
                    <td className={`px-4 py-2 font-mono ${DECISION_TONE(e.decision)}`}>{e.decision}</td>
                    <td className="max-w-md truncate px-4 py-2 text-white/70" title={e.reason}>{e.reason}</td>
                    <td className="px-4 py-2 font-mono text-mist/60">{e.hash.slice(0, 8)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <p className="p-5 text-sm text-mist">Every decision GhostOps makes is recorded here, each entry hashing the one before it.</p>)}

        {tab === "incidents" &&
          (incidents.length ? (
            <ul className="divide-y divide-white/5">
              {incidents.map((i) => (
                <li key={i.id} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
                  <span className="font-mono text-xs text-mist">#{i.id}</span>
                  <span className={`h-2 w-2 rounded-full ${i.state === "open" ? "bg-warn" : "bg-ok"}`} />
                  <span className="font-medium text-white">{i.workload}</span>
                  {i.alerts.map((a) => <span key={a} className="rounded-md bg-white/5 px-2 py-0.5 font-mono text-[11px] text-mist">{a}</span>)}
                  <span className="ml-auto font-mono text-xs text-mist">{i.occurrences}× · {i.state}</span>
                </li>
              ))}
            </ul>
          ) : <p className="p-5 text-sm text-mist">Repeats of an open incident, and related alerts on the same workload, are grouped here instead of starting new pipelines.</p>)}

        {tab === "skills" &&
          (skills.length ? (
            <ul className="divide-y divide-white/5">
              {skills.map((s) => (
                <li key={s.id} className="flex flex-wrap items-center gap-3 px-5 py-3 text-sm">
                  <span className="font-mono text-white">{s.root_cause}</span>
                  <span className="font-mono text-xs text-arc">{s.file_name}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[10px] uppercase tracking-widest ${s.promoted ? "bg-ok/15 text-ok" : "bg-white/5 text-mist"}`}>
                    {s.promoted ? "promoted skill" : "learning"}
                  </span>
                  <span className="ml-auto font-mono text-xs text-mist">merged {s.merges}× · reused {s.uses}×</span>
                </li>
              ))}
            </ul>
          ) : <p className="p-5 text-sm text-mist">When humans merge the same fix twice it becomes a skill: reused instantly next time, with no AI call.</p>)}
      </div>
    </div>
  );
}
