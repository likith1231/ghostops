"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState } from "react";
import { STAGES, api, streamRun, type Diagnosis, type Patch, type PrResult, type Run, type Scenario, type StageName, type Validation } from "@/lib/api";
import type { BackendState } from "@/lib/useBackend";
import CodeDiff from "./CodeDiff";
import MemoryChart from "./MemoryChart";
import OfflineNotice from "./OfflineNotice";
import type { NodeState } from "./three/PipelineScene";

const PipelineScene = dynamic(() => import("./three/PipelineScene"), {
  ssr: false,
  loading: () => <div className="grid h-full place-items-center font-mono text-xs text-mist">loading scene…</div>,
});

/** Minimum time each pipeline step stays on screen. */
const STEP_MS = 850;

const STAGE_TO_NODE: Record<string, NodeState> = { pending: "idle", running: "running", done: "done", error: "error" };

function nodeStates(run: Run | null): NodeState[] {
  if (!run) return ["idle", "idle", "idle", "idle", "idle"];
  const st = (s: StageName) => STAGE_TO_NODE[run.stages[s].status];
  let validation = st("validation");
  if (validation === "done" && (run.stages.validation.data as Validation).passed === false) validation = "error";
  let deploy = st("deploy");
  if (deploy === "done") {
    const pr = run.stages.deploy.data as PrResult;
    deploy = pr.pr_created ? "done" : pr.reason === "mock_mode" ? "held" : "error";
  }
  return ["done", st("diagnosis"), st("patch"), validation, deploy];
}

function seconds(run: Run): string {
  const ms = new Date(run.updated_at).getTime() - new Date(run.created_at).getTime();
  return `${(ms / 1000).toFixed(1)}s`;
}

function Card({ title, state, children }: { title: string; state: string; children: React.ReactNode }) {
  const tone =
    state === "running" ? "text-arc" : state === "done" ? "text-ok" : state === "error" ? "text-bad" : "text-mist";
  return (
    <div className="glass min-w-0 rounded-2xl p-5">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-semibold text-white">{title}</h3>
        <span className={`font-mono text-[11px] uppercase tracking-widest ${tone}`}>{state}</span>
      </div>
      <div className="space-y-3 text-sm text-white/80">{children}</div>
    </div>
  );
}

function Placeholder() {
  return <p className="text-mist">Waiting for this stage…</p>;
}

export default function LiveRun({ backend, onFinished }: { backend: BackendState; onFinished: () => void }) {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selected, setSelected] = useState("b1");
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState<string | null>(null);
  const stop = useRef<(() => void) | null>(null);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!backend.online) return;
    api.scenarios().then(setScenarios).catch(() => setScenarios([]));
  }, [backend.online]);

  // Incoming run versions are queued and shown one at a time, each for at least
  // STEP_MS, so stages that finish in milliseconds (mock mode) are still seen in order.
  const queue = useRef<Run[]>([]);
  const lastVersion = useRef(-1);
  const pacer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const shown = useRef<Run | null>(null);

  const stopStream = useCallback(() => {
    stop.current?.();
    if (poll.current) clearInterval(poll.current);
    stop.current = null;
    poll.current = null;
  }, []);

  const cleanup = useCallback(() => {
    stopStream();
    if (pacer.current) clearTimeout(pacer.current);
    pacer.current = null;
    queue.current = [];
    lastVersion.current = -1;
  }, [stopStream]);
  useEffect(() => cleanup, [cleanup]);

  const pump = useCallback(() => {
    if (pacer.current) return;
    const next = queue.current.shift();
    if (!next) return;
    const prev = shown.current;
    shown.current = next;
    setRun(next);
    if (next.status !== "running") {
      stopStream();
      onFinished();
      return;
    }
    // Only a visible stage change earns screen time; other updates (telemetry) pass straight through.
    const changed = !prev || STAGES.some((s) => prev.stages[s].status !== next.stages[s].status);
    pacer.current = setTimeout(() => {
      pacer.current = null;
      pump();
    }, changed ? STEP_MS : 0);
  }, [onFinished, stopStream]);

  const update = useCallback(
    (r: Run) => {
      if (r.version <= lastVersion.current) return; // polling can repeat versions
      lastVersion.current = r.version;
      queue.current.push(r);
      pump();
    },
    [pump],
  );

  const trigger = useCallback(async (scenario: string) => {
    setError(null);
    cleanup();
    shown.current = null;
    setRun(null); // clear the previous run at once instead of leaving its result on screen
    try {
      const { run_id } = await api.simulate(scenario);
      // SSE first; if the stream drops, fall back to polling.
      stop.current = streamRun(run_id, update, () => {
        poll.current = setInterval(() => api.run(run_id).then(update).catch(() => undefined), 1000);
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [cleanup, update]);

  // The command palette can start a scenario from anywhere on the page.
  useEffect(() => {
    const onTrigger = (e: Event) => {
      const id = (e as CustomEvent<string>).detail;
      setSelected(id);
      document.querySelector("#pipeline")?.scrollIntoView({ behavior: "smooth" });
      trigger(id);
    };
    window.addEventListener("ghostops:trigger", onTrigger);
    return () => window.removeEventListener("ghostops:trigger", onTrigger);
  }, [trigger]);

  const states = nodeStates(run);
  // Our own run's state is live; the polled busy flag only matters for runs started elsewhere.
  const busy = run ? run.status === "running" : backend.status?.pipeline_busy;
  const d = run?.stages.diagnosis;
  const p = run?.stages.patch;
  const v = run?.stages.validation;
  const dep = run?.stages.deploy;
  const diagnosis = d?.data as Diagnosis | undefined;
  const patch = p?.data as Patch | undefined;
  const validation = v?.data as Validation | undefined;
  const pr = dep?.data as PrResult | undefined;

  return (
    <div className="space-y-6">
      <div className="glass relative isolate overflow-hidden rounded-3xl">
        <div className="relative z-40 flex flex-wrap items-center gap-2 p-4 sm:absolute sm:inset-x-0 sm:top-0">
          {(scenarios.length ? scenarios : [{ id: "b1", title: "Memory leak -> OOM" }]).map((s) => (
            <button
              key={s.id}
              onClick={() => setSelected(s.id)}
              className={`rounded-full border px-3 py-1.5 text-xs transition ${
                selected === s.id
                  ? "border-arc/60 bg-arc/15 text-white"
                  : "border-white/10 bg-black/30 text-mist hover:text-white"
              }`}
            >
              <span className="mr-1.5 font-mono text-arc">{s.id.toUpperCase()}</span>
              {s.title.replace("->", "→")}
            </button>
          ))}
          <button
            onClick={() => trigger(selected)}
            disabled={!backend.online || !!busy}
            className="ml-auto rounded-full bg-arc px-4 py-2 text-sm font-semibold text-void shadow-[0_0_30px_rgba(56,217,255,0.35)] transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busy ? "Pipeline running…" : "Trigger incident"}
          </button>
        </div>
        <div className="h-[600px] sm:h-[440px]">
          <PipelineScene states={states} />
        </div>
        {run && run.status !== "running" && (
          <div className="absolute inset-x-0 bottom-0 z-40 flex flex-wrap items-center justify-between gap-2 bg-gradient-to-t from-void/90 to-transparent p-4 text-sm">
            <span className={run.status === "passed" ? "text-ok" : "text-bad"}>
              {run.status === "passed" ? "✓ Fix validated" : run.status === "failed" ? "✕ Fix rejected by the safety gates" : `✕ ${run.error}`}
            </span>
            <span className="font-mono text-xs text-mist">
              detect → {run.status === "passed" ? "verified fix" : "verdict"} in <span className="text-white">{seconds(run)}</span>
            </span>
          </div>
        )}
      </div>

      {backend.online === false && <OfflineNotice />}
      {error && <div className="rounded-xl border border-bad/30 bg-bad/10 p-3 text-sm text-bad">{error}</div>}

      {run?.context?.metrics && (
        <MemoryChart
          key={run.id}
          metrics={run.context.metrics}
          fixed={v?.status === "done" ? validation?.passed === true : null}
        />
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <Card title="1 · Diagnosis" state={d?.status ?? "pending"}>
          {diagnosis?.root_cause ? (
            <>
              <div className="flex flex-wrap gap-2">
                <span className="rounded-md bg-violet/15 px-2 py-0.5 font-mono text-xs text-violet">{diagnosis.root_cause}</span>
                <span className="rounded-md bg-white/5 px-2 py-0.5 font-mono text-xs text-mist">confidence: {String(diagnosis.confidence)}</span>
              </div>
              <p>{diagnosis.summary}</p>
              <ul className="space-y-1 font-mono text-[12px] text-mist">
                {(diagnosis.evidence ?? []).slice(0, 3).map((e, i) => (
                  <li key={i} className="truncate">› {typeof e === "string" ? e : e.reference}</li>
                ))}
              </ul>
            </>
          ) : (
            <Placeholder />
          )}
        </Card>

        <Card title="2 · Patch" state={p?.status ?? "pending"}>
          {patch?.diff !== undefined ? (
            <>
              {patch.file_path && <div className="font-mono text-xs text-arc">{patch.file_path}</div>}
              <p>{patch.explanation}</p>
              <CodeDiff diff={patch.diff ?? ""} maxHeight="12rem" />
            </>
          ) : (
            <Placeholder />
          )}
        </Card>

        <Card title="3 · Validation — OPA policy + Docker sandbox" state={v?.status ?? "pending"}>
          {validation?.summary ? (
            <>
              <div className={`text-lg font-semibold ${validation.passed ? "text-ok" : "text-bad"}`}>
                {validation.passed ? "Passed" : "Rejected"}
              </div>
              <p>{validation.summary}</p>
              {!!validation.failing_tests?.length && (
                <ul className="font-mono text-[12px] text-bad">
                  {validation.failing_tests.map((t) => <li key={t}>✕ {t}</li>)}
                </ul>
              )}
              {validation.test_output && (
                <details className="text-xs">
                  <summary className="cursor-pointer text-mist hover:text-white">sandbox output</summary>
                  <pre className="scrollbar-thin mt-2 max-h-48 overflow-auto rounded-lg bg-black/50 p-3 font-mono text-[11px] text-white/70">
                    {validation.test_output}
                  </pre>
                </details>
              )}
            </>
          ) : (
            <Placeholder />
          )}
        </Card>

        <Card title="4 · Pull request" state={dep?.status ?? "pending"}>
          {pr?.reason || pr?.pr_created ? (
            pr.pr_created ? (
              <a href={pr.pr_url} target="_blank" rel="noreferrer" className="text-arc underline-offset-4 hover:underline">
                {pr.pr_url}
              </a>
            ) : pr.reason === "mock_mode" ? (
              <p>
                Validated fix ready. In mock mode GhostOps holds the PR instead of opening one; set{" "}
                <code className="font-mono text-warn">GHOSTOPS_MOCK_CREATE_PR=1</code> to open it for real.
              </p>
            ) : (
              <p>No PR — <span className="font-mono text-bad">{pr.reason}</span>. Nothing reaches a human until it passes both gates.</p>
            )
          ) : (
            <Placeholder />
          )}
          <p className="text-xs text-mist">GhostOps never merges its own PRs — a human always approves.</p>
        </Card>
      </div>
    </div>
  );
}
