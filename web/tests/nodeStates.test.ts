import { describe, expect, it } from "vitest";
import { nodeStates } from "@/components/LiveRun";
import type { Run, StageName } from "@/lib/api";

function run(stages: Partial<Record<StageName, [string, Record<string, unknown>?]>>, extra: Partial<Run> = {}): Run {
  const all: StageName[] = ["diagnosis", "patch", "validation", "deploy"];
  return {
    id: "r",
    scenario: "b1",
    title: "t",
    status: "running",
    created_at: "",
    updated_at: "",
    version: 0,
    result: null,
    error: null,
    context: null,
    live: null,
    stages: Object.fromEntries(all.map((s) => [s, { status: stages[s]?.[0] ?? "pending", data: stages[s]?.[1] ?? {} }])) as Run["stages"],
    ...extra,
  } as Run;
}

describe("nodeStates", () => {
  it("is all idle without a run", () => {
    expect(nodeStates(null)).toEqual(["idle", "idle", "idle", "idle", "idle"]);
  });

  it("maps stage statuses onto the five nodes", () => {
    expect(nodeStates(run({ diagnosis: ["done"], patch: ["running"] }))).toEqual(["done", "done", "running", "idle", "idle"]);
  });

  it("marks validation as an error when the patch was rejected", () => {
    expect(nodeStates(run({ diagnosis: ["done"], patch: ["done"], validation: ["done", { passed: false }] }))[3]).toBe("error");
  });

  it.each([
    [{ pr_created: true }, "done"],
    [{ pr_created: false, reason: "rollback" }, "done"],
    [{ pr_created: false, reason: "mock_mode" }, "held"],
    [{ pr_created: false, reason: "validation_failed" }, "error"],
    [{ pr_created: false, reason: "needs_human" }, "error"],
  ])("PR node for %j is %s", (pr, expected) => {
    const r = run({ diagnosis: ["done"], patch: ["done"], validation: ["done", { passed: true }], deploy: ["done", pr] });
    expect(nodeStates(r)[4]).toBe(expected);
  });

  it("keeps the Alert node working during a live incident before the pipeline starts", () => {
    const live = run({}, { live: { step: "waiting_alert", alert: "pending" } as Run["live"] });
    expect(nodeStates(live)).toEqual(["running", "idle", "idle", "idle", "idle"]);
  });

  it("shows a failed live incident on the Alert node", () => {
    const failed = run({}, { status: "error", live: { step: "waiting_alert", alert: "inactive" } as Run["live"] });
    expect(nodeStates(failed)[0]).toBe("error");
  });
});
