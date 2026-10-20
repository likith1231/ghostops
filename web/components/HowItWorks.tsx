"use client";

import { motion } from "framer-motion";

const STEPS = [
  { k: "M", title: "Monitor", body: "Prometheus scrapes the cluster; Alertmanager fires a webhook the moment a rule breaches.", tech: "Prometheus · Alertmanager" },
  { k: "A", title: "Analyze", body: "The backend enriches the alert with pod logs and metric history; the Diagnostic Reasoner cites the evidence.", tech: "FastAPI · Claude" },
  { k: "P", title: "Plan", body: "The Patch Generator writes the smallest diff that fixes only the diagnosed cause — never the tests.", tech: "CrewAI · unified diff" },
  { k: "E", title: "Execute", body: "OPA checks the patch's target, then pytest runs it in a throwaway, network-less container.", tech: "OPA/Gatekeeper · Docker" },
  { k: "K", title: "Knowledge", body: "Every run, pass or fail, is stored and fed back to ground future diagnoses.", tech: "SQLite · Grafana · Jaeger" },
];

const GATES = [
  ["Policy gate", "Patches may only touch allow-listed paths; manifests need limits and can't be privileged."],
  ["Blast-radius gate", "Rejects patches that are too big, weaken tests, add dependencies, touch secrets, or add eval, shell or network calls."],
  ["Sandbox gate", "The real test suite passes in an isolated container, plus a new regression test that fails before the fix and passes after."],
  ["Replay & canary", "The incident's real traffic is replayed against the fix, then a canary pod proves it on the cluster."],
  ["Risk-based autonomy", "Low risk auto-merges to staging, medium opens a PR, high pages a human. Every step is signed and audited."],
  ["Human & kill switch", "Production stays under human control: one switch pauses every automatic action."],
];

export default function HowItWorks() {
  return (
    <div className="space-y-10">
      <ol className="grid gap-4 md:grid-cols-5">
        {STEPS.map((s, i) => (
          <motion.li
            key={s.k}
            initial={{ opacity: 0, y: 28 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }}
            transition={{ duration: 0.55, delay: i * 0.09, ease: [0.22, 1, 0.36, 1] }}
            className="glass relative rounded-2xl p-5"
          >
            <div className="mb-4 flex items-center gap-3">
              <span className="grid h-10 w-10 place-items-center rounded-xl bg-gradient-to-br from-arc/30 to-violet/30 font-mono text-lg font-semibold text-white">
                {s.k}
              </span>
              <span className="font-mono text-xs text-mist">0{i + 1}</span>
            </div>
            <h3 className="font-semibold text-white">{s.title}</h3>
            <p className="mt-2 text-sm leading-relaxed text-mist">{s.body}</p>
            <p className="mt-3 font-mono text-[11px] text-arc/80">{s.tech}</p>
          </motion.li>
        ))}
      </ol>
      <div className="grid gap-4 md:grid-cols-3">
        {GATES.map(([title, body], i) => (
          <motion.div
            key={title}
            initial={{ opacity: 0, y: 28 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, margin: "-60px" }}
            transition={{ duration: 0.55, delay: 0.2 + i * 0.12, ease: [0.22, 1, 0.36, 1] }}
            className="relative overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-br from-white/[0.05] to-transparent p-6"
          >
            <div className="absolute right-4 top-3 font-mono text-6xl font-bold text-white/[0.04]">{i + 1}</div>
            <div className="mb-2 text-sm font-semibold uppercase tracking-[0.2em] text-arc">{title}</div>
            <p className="text-sm leading-relaxed text-white/80">{body}</p>
          </motion.div>
        ))}
      </div>
    </div>
  );
}
