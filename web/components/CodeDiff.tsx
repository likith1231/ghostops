"use client";

import { useState } from "react";
import DiffView from "./DiffView";

interface Side {
  n: number;
  text: string;
  kind: "ctx" | "del" | "add";
}
type Row = { hunk: string } | { left?: Side; right?: Side };

/** Turn a unified diff into aligned before/after rows (removed lines pair with added ones). */
export function splitRows(diff: string): Row[] {
  const rows: Row[] = [];
  let ln = 0;
  let rn = 0;
  let dels: Side[] = [];
  let adds: Side[] = [];
  const flush = () => {
    for (let i = 0; i < Math.max(dels.length, adds.length); i++) rows.push({ left: dels[i], right: adds[i] });
    dels = [];
    adds = [];
  };
  for (const line of diff.split("\n")) {
    if (line.startsWith("+++") || line.startsWith("---")) continue;
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
    if (hunk) {
      flush();
      ln = Number(hunk[1]);
      rn = Number(hunk[2]);
      rows.push({ hunk: line });
    } else if (line.startsWith("-")) {
      dels.push({ n: ln++, text: line.slice(1), kind: "del" });
    } else if (line.startsWith("+")) {
      adds.push({ n: rn++, text: line.slice(1), kind: "add" });
    } else if (line.startsWith(" ") || (line === "" && ln > 0)) {
      flush();
      const text = line.slice(1);
      rows.push({ left: { n: ln++, text, kind: "ctx" }, right: { n: rn++, text, kind: "ctx" } });
    }
  }
  flush();
  // A trailing empty line from the final newline is not a real context line.
  const last = rows[rows.length - 1];
  if (last && "left" in last && last.left?.text === "" && last.right?.text === "" && last.left.kind === "ctx") rows.pop();
  return rows;
}

const TONE = { ctx: "text-white/70", del: "bg-bad/10 text-bad", add: "bg-ok/10 text-ok" };

function Cell({ side }: { side?: Side }) {
  if (!side) return <div className="bg-white/[0.02]" />;
  return (
    <div className={`flex min-w-0 overflow-hidden ${TONE[side.kind]}`} title={side.text}>
      <span className="w-9 shrink-0 select-none pr-2 text-right text-mist/50">{side.n}</span>
      <span className="overflow-hidden text-ellipsis whitespace-pre">{side.text || " "}</span>
    </div>
  );
}

function SplitView({ diff, maxHeight }: { diff: string; maxHeight: string }) {
  const rows = splitRows(diff);
  return (
    <div className="scrollbar-thin overflow-auto rounded-xl border border-white/5 bg-black/50 font-mono text-[12px] leading-relaxed" style={{ maxHeight }}>
      <div className="grid min-w-[640px] grid-cols-[minmax(0,1fr)_minmax(0,1fr)] border-b border-white/5 text-[10px] uppercase tracking-[0.25em] text-mist">
        <div className="px-3 py-1.5">before</div>
        <div className="border-l border-white/5 px-3 py-1.5">after</div>
      </div>
      <div className="min-w-[640px] py-1">
        {rows.map((row, i) =>
          "hunk" in row ? (
            <div key={i} className="px-3 py-0.5 text-violet">{row.hunk}</div>
          ) : (
            <div key={i} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
              <Cell side={row.left} />
              <div className="border-l border-white/5">
                <Cell side={row.right} />
              </div>
            </div>
          ),
        )}
      </div>
    </div>
  );
}

/** A diff with a Unified / Split toggle. */
export default function CodeDiff({ diff, maxHeight = "18rem" }: { diff: string; maxHeight?: string }) {
  const [split, setSplit] = useState(false);
  if (!diff.trim()) return <DiffView diff={diff} />;
  return (
    <div className="space-y-2">
      <div className="flex gap-1 font-mono text-[10px] uppercase tracking-[0.2em]">
        {(["unified", "split"] as const).map((mode) => (
          <button
            key={mode}
            onClick={() => setSplit(mode === "split")}
            className={`rounded-md px-2 py-1 transition ${(mode === "split") === split ? "bg-arc/15 text-arc" : "text-mist hover:text-white"}`}
          >
            {mode === "split" ? "before / after" : "unified"}
          </button>
        ))}
      </div>
      {split ? <SplitView diff={diff} maxHeight={maxHeight} /> : <DiffView diff={diff} maxHeight={maxHeight} />}
    </div>
  );
}
