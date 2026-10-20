"use client";

import { motion, useScroll, useTransform } from "framer-motion";
import dynamic from "next/dynamic";
import { useCallback, useRef, useState } from "react";
import Backdrop from "@/components/Backdrop";
import CommandPalette from "@/components/CommandPalette";
import DetectorLab from "@/components/DetectorLab";
import HowItWorks from "@/components/HowItWorks";
import Incidents from "@/components/Incidents";
import LiveRun from "@/components/LiveRun";
import Nav from "@/components/Nav";
import Section from "@/components/Section";
import Stats from "@/components/Stats";
import Ticker from "@/components/Ticker";
import UseCases from "@/components/UseCases";
import { useActivityFeed } from "@/lib/activity";
import { useBackend } from "@/lib/useBackend";

const AiCore = dynamic(() => import("@/components/three/AiCore"), { ssr: false });

const HERO_FACTS = [
  ["17", "catalogued failure types"],
  ["3", "fixed end-to-end"],
  ["2", "automated safety gates"],
  ["0", "self-merged PRs"],
];

export default function Home() {
  const backend = useBackend();
  useActivityFeed();
  const [refreshKey, setRefreshKey] = useState(0);
  const refresh = useCallback(() => setRefreshKey((k) => k + 1), []);

  // Scrolling out of the hero: the hologram drifts up and fades, the headline lifts a little.
  const hero = useRef<HTMLElement>(null);
  const { scrollYProgress } = useScroll({ target: hero, offset: ["start start", "end start"] });
  const coreY = useTransform(scrollYProgress, [0, 1], [0, -160]);
  const coreScale = useTransform(scrollYProgress, [0, 1], [1, 0.82]);
  const coreOpacity = useTransform(scrollYProgress, [0, 0.85], [1, 0]);
  const textY = useTransform(scrollYProgress, [0, 1], [0, -70]);

  return (
    <main id="top" className="relative">
      {/* Nav sits outside the isolated content so nothing inside can paint over it. */}
      <Nav backend={backend} />
      <CommandPalette />

      <Backdrop />

      <div className="relative isolate z-10">
        {/* Hero: headline left, the AI core with its HUD right (below on phones). */}
        <section ref={hero} className="relative grid min-h-[100svh] w-full items-center gap-6 px-4 pb-16 pt-28 sm:px-8 lg:px-[max(2rem,calc((100vw_-_80rem)/2_+_2rem))] lg:pb-10">
          <motion.div style={{ y: textY }} className="relative z-10 max-w-2xl lg:max-w-[46%]">
            <motion.div
              initial={{ opacity: 0, y: 30 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
            >
              <div className="mb-5 inline-flex items-center gap-2 border border-arc/30 bg-arc/10 px-3 py-1 font-mono text-[11px] uppercase tracking-[0.3em] text-arc [clip-path:polygon(8px_0,100%_0,100%_calc(100%-8px),calc(100%-8px)_100%,0_100%,0_8px)]">
                <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-arc" />
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
              <div className="mt-8 flex flex-wrap gap-3">
                <a
                  href="#pipeline"
                  className="cut-corners shimmer bg-arc px-6 py-3 font-semibold text-void shadow-[0_0_40px_rgba(56,217,255,0.45)] transition hover:brightness-110"
                >
                  Trigger a live incident
                </a>
                <a
                  href="https://github.com/likith1231/ghostops"
                  target="_blank"
                  rel="noreferrer"
                  className="cut-corners border border-arc/30 bg-arc/5 px-6 py-3 font-semibold text-white transition hover:bg-arc/15"
                >
                  View the code
                </a>
              </div>
            </motion.div>
            <motion.dl
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: 0.6, duration: 1 }}
              className="mt-12 grid max-w-2xl grid-cols-2 gap-6 border-t border-arc/15 pt-6 sm:grid-cols-4"
            >
              {HERO_FACTS.map(([n, label]) => (
                <div key={label}>
                  <dt className="font-mono text-3xl font-semibold text-white">{n}</dt>
                  <dd className="mt-1 text-xs leading-snug text-mist">{label}</dd>
                </div>
              ))}
            </motion.dl>
          </motion.div>

          {/* The hologram: full-bleed behind the hero on desktop, its own square below the text on phones. */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 1.4, delay: 0.2 }}
            className="relative mx-auto aspect-square w-full max-w-[480px] lg:absolute lg:inset-0 lg:z-0 lg:aspect-auto lg:max-w-none"
          >
            <motion.div style={{ y: coreY, scale: coreScale, opacity: coreOpacity }} className="h-full w-full">
              <AiCore />
            </motion.div>
          </motion.div>
        </section>

        <Ticker refreshKey={refreshKey} />

        <div className="relative">

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

      <footer className="relative z-10 border-t border-white/5">
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
