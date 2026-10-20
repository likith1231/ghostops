"use client";

import NeuralBackdrop from "./NeuralBackdrop";

/** The page's living background: soft colour glows, the GhostOps network, and film grain. */
export default function Backdrop() {
  return (
    <div aria-hidden className="backdrop pointer-events-none fixed inset-0 z-0 overflow-hidden">
      <div className="glow glow-a" />
      <div className="glow glow-b" />
      <NeuralBackdrop />
      <div className="grain" />
    </div>
  );
}
