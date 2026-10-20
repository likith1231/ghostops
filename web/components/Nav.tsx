"use client";

import type { BackendState } from "@/lib/useBackend";
import { openPalette } from "./CommandPalette";

const LINKS = [
  ["Live", "#pipeline"],
  ["Impact", "#impact"],
  ["Use cases", "#usecases"],
  ["Detector lab", "#lab"],
  ["Incidents", "#incidents"],
] as const;

export function BackendPill({ backend }: { backend: BackendState }) {
  const { online, status } = backend;
  const [label, color] =
    online === null
      ? ["connecting", "bg-mist"]
      : !online
        ? ["backend offline", "bg-bad"]
        : status?.mock_llm
          ? ["online · mock LLM", "bg-warn"]
          : ["online · live LLM", "bg-ok"];
  return (
    <span className="glass inline-flex items-center gap-2 rounded-full px-3 py-1 font-mono text-[11px] uppercase tracking-widest text-white/80">
      <span className={`h-2 w-2 rounded-full ${color} ${online ? "pulse-ring" : ""}`} />
      {label}
    </span>
  );
}

export default function Nav({ backend }: { backend: BackendState }) {
  return (
    <header className="fixed inset-x-0 top-0 z-[100] isolate">
      <nav className="glass !bg-[#070910]/85 mx-auto mt-3 flex max-w-7xl items-center justify-between gap-4 rounded-2xl px-4 py-3 sm:mx-6 xl:mx-auto">
        <a href="#top" className="flex items-center gap-2 font-semibold tracking-tight text-white">
          <span className="grid h-7 w-7 place-items-center rounded-lg bg-arc/15 text-arc">◐</span>
          GhostOps
        </a>
        <div className="hidden items-center gap-6 text-sm text-mist md:flex">
          {LINKS.map(([label, href]) => (
            <a key={href} href={href} className="transition-colors hover:text-white">
              {label}
            </a>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={openPalette}
            className="hidden items-center gap-1.5 rounded-lg border border-arc/20 bg-arc/5 px-2.5 py-1 font-mono text-[11px] text-mist transition hover:text-white sm:flex"
            aria-label="Open command palette"
          >
            <kbd>Ctrl</kbd>
            <kbd>K</kbd>
          </button>
          <BackendPill backend={backend} />
        </div>
      </nav>
    </header>
  );
}
