// Typed client for the GhostOps FastAPI backend.

export const API_BASE = process.env.NEXT_PUBLIC_GHOSTOPS_API ?? "http://localhost:8000";

export type StageName = "diagnosis" | "patch" | "validation" | "deploy";
export type StageStatus = "pending" | "running" | "done" | "error";
export type RunStatus = "running" | "passed" | "failed" | "error";

export const STAGES: StageName[] = ["diagnosis", "patch", "validation", "deploy"];

export interface Evidence {
  type?: string;
  reference?: string;
  reasoning?: string;
}

export interface Diagnosis {
  root_cause?: string;
  confidence?: string | number;
  summary?: string;
  evidence?: Evidence[] | string[];
  mock?: boolean;
}

export interface Patch {
  diff?: string;
  explanation?: string;
  file_path?: string;
}

export interface Validation {
  passed?: boolean;
  summary?: string;
  failing_tests?: string[];
  test_output?: string;
}

export interface PrResult {
  pr_created?: boolean;
  pr_url?: string;
  reason?: string;
}

export interface Run {
  id: string;
  scenario: string;
  title: string;
  status: RunStatus;
  created_at: string;
  updated_at: string;
  version: number;
  stages: Record<StageName, { status: StageStatus; data: Record<string, unknown> }>;
  result: { diagnosis: Diagnosis; patch: Patch; validation_result: Validation; pr_result: PrResult } | null;
  error: string | null;
  /** What the pipeline was given: telemetry only, never source files. */
  context: { alert_type?: string; metrics?: Record<string, { ts?: string; value: number }[]>; logs?: string[] } | null;
  /** A live cluster incident before the pipeline starts: leaking, then waiting for the real alert. */
  live: LiveProgress | null;
}

export interface LiveProgress {
  /** "leak" (B1, default) or "canary" (C1: a bad release). */
  kind?: "leak" | "canary";
  step: "starting" | "resetting" | "leaking" | "deploying" | "waiting_alert" | "alert_firing";
  reset?: boolean;
  pod?: string;
  leaked_mib?: number;
  memory?: { ts: number; value: number }[];
  limit?: number | null;
  release?: string;
  error_rate?: { ts: number; value: number }[];
  requests?: { ok?: number; failed?: number };
  alert: "inactive" | "pending" | "firing";
}

export type ActivityState = "idle" | "preparing" | "incident" | "diagnosing" | "patching" | "validating" | "healed" | "failed";

export interface Activity {
  state: ActivityState;
  run_id?: string;
  title?: string;
  status?: RunStatus;
}

export interface LiveStatus {
  ready: boolean;
  pod?: string;
  alert?: string;
  busy: boolean;
  error?: string;
}

export interface Status {
  mock_llm: boolean;
  pipeline_busy: boolean;
}

export interface Scenario {
  id: string;
  title: string;
}

export interface Summary {
  total: number;
  passed: number;
  failed: number;
  pass_rate: number;
  prs_created: number;
}

export interface Feedback {
  prs: number;
  merged: number;
  closed: number;
  superseded: number;
  open: number;
  unknown: number;
  acceptance_rate: number | null;
  last_checked: string | null;
}

export interface DayCount {
  date: string;
  passed: number;
  failed: number;
}

export interface Incident {
  id: number;
  timestamp: string;
  alert_type: string;
  root_cause: string | null;
  confidence: string | null;
  patch_diff: string | null;
  validation_passed: number;
  pr_created: number;
  pr_url: string | null;
  outcome_summary: string | null;
}

export interface UseCase {
  id: string;
  category: "logs" | "metrics" | "deploy";
  title: string;
  signal: string;
  remediation: string;
  detector: string | null;
  end_to_end: boolean;
}

export interface Detection {
  use_case: string;
  title: string;
  severity: "low" | "medium" | "high" | "critical";
  confidence: number;
  evidence: string[];
  suggested_action: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  status: () => request<Status>("/api/status"),
  scenarios: () => request<Scenario[]>("/api/scenarios"),
  simulate: (scenario: string) =>
    request<{ run_id: string; mock_llm: boolean }>("/api/simulate", {
      method: "POST",
      body: JSON.stringify({ scenario }),
    }),
  run: (id: string) => request<Run>(`/api/runs/${id}`),
  summary: () => request<Summary>("/api/incidents/summary"),
  timeseries: () => request<DayCount[]>("/api/incidents/timeseries"),
  feedback: () => request<Feedback>("/api/incidents/feedback"),
  syncFeedback: () => request<Feedback & { checked: number; failed: number }>("/api/incidents/feedback/sync", { method: "POST" }),
  recent: (limit = 12) => request<Incident[]>(`/api/incidents/recent?limit=${limit}`),
  incident: (id: number) => request<Incident>(`/api/incidents/${id}`),
  usecases: () => request<UseCase[]>("/api/usecases"),
  activity: () => request<Activity>("/api/activity"),
  liveStatus: () => request<LiveStatus>("/api/live/status"),
  liveLeak: () => request<{ run_id: string }>("/api/live/leak", { method: "POST" }),
  liveCanary: () => request<{ run_id: string }>("/api/live/canary", { method: "POST" }),
  detect: (body: Record<string, unknown>) =>
    request<{ detections: Detection[] }>("/api/detect", { method: "POST", body: JSON.stringify(body) }),
};

/** Follow a run over Server-Sent Events. Returns a function that stops listening. */
export function streamRun(id: string, onUpdate: (run: Run) => void, onError?: () => void): () => void {
  const source = new EventSource(`${API_BASE}/api/runs/${id}/events`);
  source.onmessage = (event) => {
    const run = JSON.parse(event.data) as Run;
    onUpdate(run);
    if (run.status !== "running") source.close();
  };
  source.onerror = () => {
    source.close();
    onError?.();
  };
  return () => source.close();
}
