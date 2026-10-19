"use client";

import { useEffect, useState } from "react";
import { api, type UseCase } from "@/lib/api";

const FILTERS = [
  ["all", "All 17"],
  ["logs", "App logs"],
  ["metrics", "Metrics"],
  ["deploy", "Deployments"],
] as const;

function TiltCard({ uc }: { uc: UseCase }) {
  const [style, setStyle] = useState<React.CSSProperties>({});
  return (
    <div
      className={`tilt glass relative rounded-2xl p-5 hover:border-arc/40 ${uc.end_to_end ? "shadow-[0_0_40px_-12px_rgba(56,217,255,0.5)]" : ""}`}
      style={style}
      onMouseMove={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        const x = (e.clientX - r.left) / r.width - 0.5;
        const y = (e.clientY - r.top) / r.height - 0.5;
        setStyle({ transform: `perspective(700px) rotateX(${-y * 8}deg) rotateY(${x * 10}deg) translateZ(0)` });
      }}
      onMouseLeave={() => setStyle({})}
    >
      <div className="flex items-start justify-between">
        <span className="font-mono text-3xl font-semibold text-white/90">{uc.id}</span>
        <div className="flex flex-col items-end gap-1">
          {uc.end_to_end && <span className="rounded-full bg-arc/15 px-2 py-0.5 text-[10px] uppercase tracking-widest text-arc">end-to-end</span>}
          {uc.detector && <span className="rounded-full bg-violet/15 px-2 py-0.5 text-[10px] uppercase tracking-widest text-violet">detector</span>}
        </div>
      </div>
      <h3 className="mt-3 font-semibold text-white">{uc.title}</h3>
      <dl className="mt-3 space-y-2 text-xs">
        <div>
          <dt className="text-mist">Signal</dt>
          <dd className="text-white/80">{uc.signal}</dd>
        </div>
        <div>
          <dt className="text-mist">Remediation</dt>
          <dd className="text-white/80">{uc.remediation}</dd>
        </div>
      </dl>
    </div>
  );
}

export default function UseCases({ online }: { online: boolean | null }) {
  const [all, setAll] = useState<UseCase[]>([]);
  const [filter, setFilter] = useState<(typeof FILTERS)[number][0]>("all");

  useEffect(() => {
    if (online) api.usecases().then(setAll).catch(() => undefined);
  }, [online]);

  const shown = filter === "all" ? all : all.filter((u) => u.category === filter);
  const e2e = all.filter((u) => u.end_to_end).length;
  const detectors = all.filter((u) => u.detector).length;

  return (
    <div>
      <div className="mb-6 flex flex-wrap items-center gap-2">
        {FILTERS.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={`rounded-full border px-4 py-1.5 text-sm transition ${
              filter === key ? "border-arc/60 bg-arc/15 text-white" : "border-white/10 text-mist hover:text-white"
            }`}
          >
            {label}
          </button>
        ))}
        {!!all.length && (
          <span className="ml-auto font-mono text-xs text-mist">
            {e2e} end-to-end · {detectors} detectors
          </span>
        )}
      </div>
      {all.length ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {shown.map((uc) => <TiltCard key={uc.id} uc={uc} />)}
        </div>
      ) : (
        <p className="text-sm text-mist">Start the backend to load the use-case catalogue.</p>
      )}
    </div>
  );
}
