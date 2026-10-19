// One loop of the hero animation: an incident flares, is diagnosed, patched and healed.
// The 3D core and its HUD both derive their state from the wall clock, so they stay in sync.

export const CYCLE_SECONDS = 9;

export type HealStage = "incident" | "diagnosing" | "patching" | "healed" | "steady";

export const INCIDENTS = ["memory leak", "CI test failure", "null reference", "canary regression", "DNS failure"];

export interface HealState {
  cycle: number;
  phase: number; // seconds into the loop
  stage: HealStage;
}

export function healState(nowSeconds: number): HealState {
  const cycle = Math.floor(nowSeconds / CYCLE_SECONDS);
  const phase = nowSeconds - cycle * CYCLE_SECONDS;
  const stage: HealStage =
    phase < 1.6 ? "incident" : phase < 3.0 ? "diagnosing" : phase < 4.4 ? "patching" : phase < 6.0 ? "healed" : "steady";
  return { cycle, phase, stage };
}

/** Deterministic pseudo-random integer per loop, so every view agrees on it. */
export function cycleHash(cycle: number): number {
  let x = (cycle + 1) * 2654435761;
  x ^= x >>> 15;
  x = Math.imul(x, 2246822519);
  x ^= x >>> 13;
  return Math.abs(x);
}
