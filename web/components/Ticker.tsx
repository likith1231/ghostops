"use client";

import { useEffect, useState } from "react";
import { api, type Incident } from "@/lib/api";

const FALLBACK = [
  "B1 memory leak → bounded cache → sandbox ✓",
  "C4 CI failure → operator fixed → PR opened",
  "A1 null reference → guard added → 3/3 tests",
  "OPA policy gate: path-traversal blocked",
  "Human gate: GhostOps never merges its own PRs",
];

function line(i: Incident): string {
  const verdict = i.validation_passed ? "validated ✓" : "blocked by gates ✕";
  const pr = i.pr_created ? " · PR opened" : "";
  return `#${i.id} ${i.alert_type} → ${i.root_cause ?? "unknown"} · ${verdict}${pr}`;
}

/** A slow marquee of recent knowledge-base incidents; it pauses on hover. */
export default function Ticker({ refreshKey }: { refreshKey: number }) {
  const [items, setItems] = useState<string[]>(FALLBACK);

  useEffect(() => {
    api
      .recent(14)
      .then((rows) => setItems(rows.length ? rows.map(line) : FALLBACK))
      .catch(() => setItems(FALLBACK));
  }, [refreshKey]);

  const loop = [...items, ...items]; // two copies: the strip scrolls by exactly one copy
  return (
    <div className="ticker relative z-10 border-y border-arc/15 bg-void/40 py-3 backdrop-blur-sm">
      <div className="ticker-track flex w-max gap-10 font-mono text-[12px] tracking-wide text-mist">
        {loop.map((text, i) => (
          <span key={i} className="flex items-center gap-3 whitespace-nowrap">
            <span className={`h-1.5 w-1.5 rounded-full ${text.includes("✕") ? "bg-bad" : "bg-arc"}`} />
            {text}
          </span>
        ))}
      </div>
    </div>
  );
}
