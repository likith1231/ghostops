"use client";

import { useEffect, useState } from "react";
import { api, type CostSummary, type Mttr, type VerificationRow } from "@/lib/api";

const fmt = (s: number | null) => (s == null ? "—" : s < 120 ? `${Math.round(s)}s` : `${(s / 60).toFixed(1)}m`);
const usd = (n: number) => `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;

const STATE_TONE: Record<string, string> = { watching: "text-arc", resolved: "text-ok", reopened: "text-bad" };

/** Real MTTR, the incident cost meter, and fixes being watched in production. */
export default function ProductionSection({ refreshKey }: { refreshKey: number }) {
  const [mttr, setMttr] = useState<Mttr | null>(null);
  const [costs, setCosts] = useState<CostSummary | null>(null);
  const [rows, setRows] = useState<VerificationRow[]>([]);

  useEffect(() => {
    api.mttr().then(setMttr).catch(() => setMttr(null));
    api.costs().then(setCosts).catch(() => setCosts(null));
    api.verifications().then(setRows).catch(() => setRows([]));
  }, [refreshKey]);

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <div className="glass rounded-2xl p-5">
        <div className="font-mono text-[11px] uppercase tracking-[0.25em] text-mist">MTTR — alert → verified healthy</div>
        <div className="mt-2 text-4xl font-semibold text-white">{fmt(mttr?.median_s ?? null)}</div>
        <div className="mt-1 text-xs text-mist">median over {mttr?.count ?? 0} verified fix(es) · p90 {fmt(mttr?.p90_s ?? null)}</div>
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-white/5">
          <div className="h-full bg-ok" style={{ width: `${mttr?.under_5_min_pct ?? 0}%` }} />
        </div>
        <div className="mt-1 text-xs text-mist">
          {mttr?.under_5_min_pct == null ? "no verified fixes yet" : `${mttr.under_5_min_pct}% under 5 minutes (the synopsis target)`}
        </div>
      </div>

      <div className="glass rounded-2xl p-5">
        <div className="font-mono text-[11px] uppercase tracking-[0.25em] text-mist">Incident cost meter</div>
        <div className="mt-2 flex items-end gap-6">
          <div>
            <div className="text-4xl font-semibold text-white">{usd(costs?.total_cost_usd ?? 0)}</div>
            <div className="text-xs text-mist">cost of {costs?.incidents ?? 0} real incident(s)</div>
          </div>
          <div>
            <div className="text-2xl font-semibold text-ok">{usd(costs?.total_saved_usd ?? 0)}</div>
            <div className="text-xs text-mist">saved vs manual repair</div>
          </div>
        </div>
        {costs && (
          <p className="mt-4 text-[11px] leading-relaxed text-mist">
            Measured from Prometheus per incident window. Assumed rates: ${costs.rates.per_failed_request}/failed request, $
            {costs.rates.per_degraded_minute}/degraded minute, ${costs.rates.per_gib_hour}/GiB-hour, manual MTTR {costs.rates.manual_mttr_minutes} min.
          </p>
        )}
      </div>

      <div className="glass rounded-2xl p-5">
        <div className="mb-3 font-mono text-[11px] uppercase tracking-[0.25em] text-mist">Watched in production</div>
        {rows.length === 0 ? (
          <p className="text-sm text-mist">Deployed fixes are watched here: the same Prometheus signal, for a window, before they count as resolved.</p>
        ) : (
          <ul className="space-y-2">
            {rows.slice(0, 5).map((v) => (
              <li key={v.id} className="flex flex-wrap items-center gap-2 text-sm">
                <span className={`font-mono text-[11px] uppercase ${STATE_TONE[v.state] ?? "text-mist"}`}>{v.state}</span>
                <span className="text-white/80">{v.kind} · {v.alert_type}</span>
                {v.mttr_s != null && <span className="font-mono text-xs text-ok">MTTR {fmt(v.mttr_s)}</span>}
                {v.chaos && (
                  <span className={`rounded-full px-2 py-0.5 text-[10px] uppercase tracking-widest ${v.chaos.verdict === "survived" ? "bg-ok/15 text-ok" : "bg-bad/15 text-bad"}`}>
                    chaos {v.chaos.verdict}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
