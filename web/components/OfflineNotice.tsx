export default function OfflineNotice() {
  return (
    <div className="glass rounded-2xl border-bad/30 p-5 text-sm text-mist">
      <div className="mb-2 font-semibold text-white">The GhostOps backend isn&apos;t reachable.</div>
      Start it from the repo root — no API key needed in mock mode:
      <pre className="mt-3 overflow-x-auto rounded-lg bg-black/50 p-3 font-mono text-xs text-ghost">
        GHOSTOPS_MOCK_LLM=1 uvicorn backend.main:app --port 8000
      </pre>
    </div>
  );
}
