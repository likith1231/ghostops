"use client";

import { useEffect } from "react";
import { api, type Activity } from "./api";

/**
 * What GhostOps is doing right now, polled from /api/activity. Kept in a module-level
 * object so the 3D scenes can read it every frame without re-rendering; changes are
 * also broadcast as a "ghostops:activity" event.
 */
export const activity: { current: Activity } = { current: { state: "idle" } };

export function useActivityFeed(intervalMs = 1200) {
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const next = await api.activity();
        if (!alive) return;
        const prev = activity.current;
        activity.current = next;
        if (prev.state !== next.state || prev.run_id !== next.run_id || prev.status !== next.status) {
          window.dispatchEvent(new CustomEvent<{ prev: Activity; next: Activity }>("ghostops:activity", { detail: { prev, next } }));
        }
      } catch {
        activity.current = { state: "idle" };
      }
    };
    poll();
    const id = setInterval(poll, intervalMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [intervalMs]);
}
