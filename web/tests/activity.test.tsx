import { act, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { activity, useActivityFeed } from "@/lib/activity";
import { api } from "@/lib/api";

function Probe() {
  useActivityFeed(1000);
  return null;
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  activity.current = { state: "idle" };
});

describe("useActivityFeed", () => {
  it("publishes state changes as ghostops:activity events, once per change", async () => {
    vi.useFakeTimers();
    const states = [{ state: "idle" }, { state: "incident", run_id: "r1", status: "running" }, { state: "incident", run_id: "r1", status: "running" }];
    let i = 0;
    vi.spyOn(api, "activity").mockImplementation(async () => states[Math.min(i++, states.length - 1)] as never);
    const seen: string[] = [];
    const listener = (e: Event) => seen.push((e as CustomEvent).detail.next.state);
    window.addEventListener("ghostops:activity", listener);
    render(<Probe />);
    for (let k = 0; k < 3; k++) await act(async () => void (await vi.advanceTimersByTimeAsync(1000)));
    window.removeEventListener("ghostops:activity", listener);
    expect(seen).toEqual(["incident"]);
    expect(activity.current.state).toBe("incident");
  });

  it("falls back to idle when the backend is unreachable", async () => {
    activity.current = { state: "incident" };
    vi.spyOn(api, "activity").mockRejectedValue(new Error("offline"));
    render(<Probe />);
    await act(async () => void (await Promise.resolve()));
    expect(activity.current.state).toBe("idle");
  });
});
