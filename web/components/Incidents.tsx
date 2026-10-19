"use client";

import { useEffect, useState } from "react";
import { api, type Incident } from "@/lib/api";
import DiffView from "./DiffView";

function when(ts: string) {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

export default function Incidents({ refreshKey }: { refreshKey: number }) {
  const [rows, setRows] = useState<Incident[]>([]);
  const [open, setOpen] = useState<Incident | null>(null);

  useEffect(() => {
    api.recent(12).then(setRows).catch(() => setRows([]));
  }, [refreshKey]);

  const toggle = async (id: number) => {
    if (open?.id === id) return setOpen(null);
    try {
      setOpen(await api.incident(id));
    } catch {
      setOpen(null);
    }
  };

  if (!rows.length) return <p className="text-sm text-mist">The knowledge base is empty — every run you trigger lands here.</p>;

  return (
    <div className="glass overflow-hidden rounded-2xl">
      {rows.map((r) => (
        <div key={r.id} className="border-b border-white/5 last:border-0">
          <button onClick={() => toggle(r.id)} className="flex w-full flex-wrap items-center gap-x-4 gap-y-1 px-5 py-4 text-left transition hover:bg-white/[0.03]">
            <span className="font-mono text-xs text-mist">#{r.id}</span>
            <span className={`h-2 w-2 rounded-full ${r.validation_passed ? "bg-ok" : "bg-bad"}`} />
            <span className="font-medium text-white">{r.root_cause ?? "unknown"}</span>
            <span className="rounded-md bg-white/5 px-2 py-0.5 font-mono text-[11px] text-mist">{r.alert_type}</span>
            {r.pr_created ? <span className="text-xs text-violet">PR opened</span> : null}
            <span className="ml-auto font-mono text-xs text-mist">{when(r.timestamp)}</span>
          </button>
          {open?.id === r.id && (
            <div className="space-y-3 px-5 pb-5">
              <p className="text-sm text-white/80">{open.outcome_summary}</p>
              {open.pr_url && (
                <a href={open.pr_url} target="_blank" rel="noreferrer" className="text-sm text-arc hover:underline">
                  {open.pr_url}
                </a>
              )}
              <DiffView diff={open.patch_diff ?? ""} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
