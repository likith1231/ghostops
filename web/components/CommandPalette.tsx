"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Incident, type Scenario } from "@/lib/api";

interface Action {
  id: string;
  group: string;
  label: string;
  hint?: string;
  run: () => void;
}

const SECTIONS: [string, string][] = [
  ["Top", "#top"],
  ["Live pipeline", "#pipeline"],
  ["Architecture", "#how"],
  ["Impact", "#impact"],
  ["Use cases", "#usecases"],
  ["Detector lab", "#lab"],
  ["Recent incidents", "#incidents"],
];

/** Opens the command palette from anywhere (the nav button uses this). */
export const openPalette = () => window.dispatchEvent(new Event("ghostops:palette"));

/** Ctrl/⌘ + K: jump to a section, trigger a scenario, or replay an incident. */
export default function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const show = () => setOpen(true);
    window.addEventListener("keydown", key);
    window.addEventListener("ghostops:palette", show);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener("ghostops:palette", show);
    };
  }, []);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    setTimeout(() => input.current?.focus(), 30);
    api.scenarios().then(setScenarios).catch(() => setScenarios([]));
    api.recent(8).then(setIncidents).catch(() => setIncidents([]));
  }, [open]);

  const actions: Action[] = useMemo(() => {
    const close = () => setOpen(false);
    const jump = (href: string) => () => {
      close();
      document.querySelector(href)?.scrollIntoView({ behavior: "smooth" });
    };
    return [
      ...scenarios.map((s) => ({
        id: `run-${s.id}`,
        group: "Trigger an incident",
        label: `${s.id.toUpperCase()} · ${s.title.replace("->", "→")}`,
        hint: "run",
        run: () => {
          close();
          window.dispatchEvent(new CustomEvent("ghostops:trigger", { detail: s.id }));
        },
      })),
      ...SECTIONS.map(([label, href]) => ({ id: href, group: "Go to", label, hint: "jump", run: jump(href) })),
      ...incidents.map((i) => ({
        id: `inc-${i.id}`,
        group: "Replay an incident",
        label: `#${i.id} ${i.root_cause ?? "unknown"} · ${i.validation_passed ? "passed" : "blocked"}`,
        hint: "replay",
        run: () => {
          close();
          window.dispatchEvent(new CustomEvent("ghostops:replay", { detail: i.id }));
        },
      })),
    ];
  }, [scenarios, incidents]);

  const shown = actions.filter((a) => `${a.group} ${a.label}`.toLowerCase().includes(query.toLowerCase()));
  const pick = Math.min(active, Math.max(0, shown.length - 1));

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") setOpen(false);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, shown.length - 1));
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    }
    if (e.key === "Enter") shown[pick]?.run();
  };

  let lastGroup = "";
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-[300] flex items-start justify-center bg-void/70 px-4 pt-[14vh] backdrop-blur-sm"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          onClick={() => setOpen(false)}
        >
          <motion.div
            role="dialog"
            aria-label="Command palette"
            className="glass w-full max-w-xl overflow-hidden rounded-2xl"
            initial={{ y: -16, scale: 0.98 }}
            animate={{ y: 0, scale: 1 }}
            exit={{ y: -16, scale: 0.98 }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-3 border-b border-arc/15 px-4 py-3">
              <span className="font-mono text-arc">›</span>
              <input
                ref={input}
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setActive(0);
                }}
                onKeyDown={onKey}
                placeholder="Trigger B1, jump to Detector lab, replay #12…"
                className="w-full bg-transparent text-sm text-white outline-none placeholder:text-mist/60"
                aria-label="Search commands"
              />
              <kbd className="rounded border border-white/10 px-1.5 py-0.5 font-mono text-[10px] text-mist">esc</kbd>
            </div>
            <div className="scrollbar-thin max-h-[50vh] overflow-y-auto py-2">
              {shown.length === 0 && <p className="px-4 py-6 text-center text-sm text-mist">No matches.</p>}
              {shown.map((a, i) => {
                const header = a.group !== lastGroup ? (lastGroup = a.group) : null;
                return (
                  <div key={a.id}>
                    {header && <div className="px-4 pb-1 pt-3 font-mono text-[10px] uppercase tracking-[0.25em] text-arc/60">{header}</div>}
                    <button
                      onMouseEnter={() => setActive(i)}
                      onClick={a.run}
                      className={`flex w-full items-center justify-between px-4 py-2 text-left text-sm transition ${i === pick ? "bg-arc/10 text-white" : "text-white/75"}`}
                    >
                      {a.label}
                      <span className="font-mono text-[10px] uppercase tracking-widest text-mist">{a.hint}</span>
                    </button>
                  </div>
                );
              })}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
