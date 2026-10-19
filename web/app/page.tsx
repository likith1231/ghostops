"use client";

import { motion } from "framer-motion";
import dynamic from "next/dynamic";
import { useCallback, useState } from "react";
import DetectorLab from "@/components/DetectorLab";
import HowItWorks from "@/components/HowItWorks";
import Incidents from "@/components/Incidents";
import LiveRun from "@/components/LiveRun";
import Nav from "@/components/Nav";
import Section from "@/components/Section";
import Stats from "@/components/Stats";
import UseCases from "@/components/UseCases";
import { useBackend } from "@/lib/useBackend";

const HeroScene = dynamic(() => import("@/components/three/HeroScene"), { ssr: false });

const HERO_FACTS = [
  ["17", "catalogued failure types"],
  ["3", "fixed end-to-end"],
  ["2", "automated safety gates"],
  ["0", "self-merged PRs"],
];

export default function Home() {
  const backend = useBackend();
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  return (
    <main id="top" className="relative">
      {/* Nav sits outside the isolated content so nothing inside can paint over it. */}
      <Nav backend={backend} />

      <div className="relative isolate z-0">
        {/* Hero */}
        <section className="relative h-[100svh] min-h-[640px] w-full overflow-hidden">
          <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_60%_40%,rgba(29,233,182,0.12),transparent_55%),radial-gradient(ellipse_at_20%_80%,rgba(167,139,250,0.12),transparent_50%)]" />
          <div className="absolute inset-0">
            <HeroScene />
          </div>
          {/* Keeps the headline readable over the scene on narrow screens. */}
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-b from-void/30 via-void/55 to-void/20 md:bg-gradient-to-r md:from-void/70 md:via-void/20 md:to-transparent" />
          <div className="pointer-events-none relative z-10 mx-auto flex h-full max-w-7xl flex-col justify-center px-4 sm:px-8">
            <motion.div
              initial={{ opacity: 0, y: 30 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
              className="max-w-2xl"
            >
              <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-ghost/30 bg-ghost/10 px-3 py-1 font-mono text-[11px] uppercase tracking-[0.25em] text-ghost">
                Autonomous SRE · MAPE-K
              </div>
              <h1 className="text-5xl font-semibold leading-[1.02] tracking-tight sm:text-7xl">
                <span className="glow-text">Incidents that</span>
                <br />
                <span className="text-white">fix themselves.</span>
              </h1>
              <p className="mt-6 max-w-xl text-lg leading-relaxed text-mist">
                GhostOps detects a failure, diagnoses its root cause, writes the patch, proves it in a sandbox — and hands you a
                pull request. Monitoring that finally closes the loop.
              </p>
              <div className="pointer-events-auto mt-8 flex flex-wrap gap-3">
                <a
                  href="#pipeline"
                  className="rounded-full bg-ghost px-6 py-3 font-semibold text-void shadow-[0_0_40px_rgba(124,247,228,0.4)] transition hover:brightness-110"
                >
                  Trigger a live incident
                </a>
                <a
                  href="https://github.com/likith1231/ghostops"
                  target="_blank"
                  rel="noreferrer"
                  className="glass rounded-full px-6 py-3 font-semibold text-white transition hover:border-white/30"
                >
                  View the code
                </a>
              </div>
            </motion.div>
            <motion.dl
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.6, duration: 1 }}
              className="mt-14 grid max-w-2xl grid-cols-2 gap-6 sm:grid-cols-4"
            >
              {HERO_FACTS.map(([n, label]) => (
                <div key={label}>
                  <dt className="font-mono text-3xl font-semibold text-white">{n}</dt>
                  <dd className="mt-1 text-xs leading-snug text-mist">{label}</dd>
                </div>
              ))}
            </motion.dl>
          </div>
          <div className="absolute bottom-6 left-1/2 z-10 -translate-x-1/2 font-mono text-[10px] uppercase tracking-[0.4em] text-mist/70">
            scroll
          </div>
        </section>

        <div className="relative">
          <div className="grid-floor pointer-events-none absolute inset-x-0 top-0 h-[900px]" />

          <Section
            id="pipeline"
            eyebrow="Live pipeline"
            title="Watch an incident get fixed."
            lede="Pick a scenario and trigger it. The backend runs the real pipeline — OPA policy check and Docker sandbox included — and streams every stage here as it happens."
          >
            <LiveRun backend={backend} onFinished={refresh} />
          </Section>

          <Section
            id="how"
            eyebrow="Architecture"
            title="Monitor. Analyze. Plan. Execute. Learn."
            lede="Built on the MAPE-K autonomic-computing loop, with three independent gates between an AI-written patch and your main branch."
          >
            <HowItWorks />
          </Section>

          <Section id="impact" eyebrow="Impact" title="Every run, on the record." lede="Read straight from the GhostOps knowledge base.">
            <Stats refreshKey={refreshKey} />
          </Section>

          <Section
            id="usecases"
            eyebrow="Coverage"
            title="Seventeen ways production breaks."
            lede="The full use-case catalogue — application logs, system metrics and deployment behaviour — with what GhostOps detects and fixes today."
          >
            <UseCases online={backend.online} />
          </Section>

          <Section
            id="lab"
            eyebrow="Detector lab"
            title="Throw telemetry at it."
            lede="Sixteen deterministic detectors run before any LLM is involved. Try a preset or paste your own logs."
          >
            <DetectorLab online={backend.online} />
          </Section>

          <Section id="incidents" eyebrow="Knowledge base" title="Recent incidents." lede="Click one to see the exact patch GhostOps produced.">
            <Incidents refreshKey={refreshKey} />
          </Section>
        </div>
      </div>

      <footer className="border-t border-white/5">
        <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-10 text-sm text-mist sm:flex-row sm:items-center sm:justify-between sm:px-8">
          <div>
            <span className="font-semibold text-white">GhostOps</span> — Autonomous Remediation from Code to Cloud
            <div className="mt-1 text-xs">Avinash M V · Kruthik N · Likith Lochan U · Lohit Yadav R — APS College of Engineering, Dept. of CSE</div>
          </div>
          <a href="https://github.com/likith1231/ghostops" target="_blank" rel="noreferrer" className="hover:text-white">
            github.com/likith1231/ghostops
          </a>
        </div>
      </footer>
    </main>
  );
}
