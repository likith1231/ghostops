# GhostOps Web

The 3D dashboard for GhostOps: Next.js (App Router), React Three Fiber and Tailwind.

| Section | What it shows | Backend endpoints |
|---|---|---|
| Hero | Animated 3D ghost | — |
| Live pipeline | Trigger a scenario and watch the stages light up in 3D, with the diagnosis, diff, sandbox verdict and PR outcome | `/api/scenarios`, `/api/simulate`, `/api/runs/{id}/events` (SSE) |
| Architecture | The MAPE-K loop and the three safety gates | — |
| Impact | Incident totals, pass rate and daily outcomes | `/api/incidents/summary`, `/api/incidents/timeseries` |
| Use cases | All 17 catalogued use cases and their status | `/api/usecases` |
| Detector lab | Run the deterministic detectors on presets or pasted logs | `/api/detect` |
| Incidents | Recent knowledge-base entries with their patches | `/api/incidents/recent`, `/api/incidents/{id}` |

## Run it

Start the backend from the repo root (mock mode needs no API key):

```bash
GHOSTOPS_MOCK_LLM=1 uvicorn backend.main:app --port 8000
```

Then, in `web/`:

```bash
npm install
npm run dev        # http://localhost:3100
```

The site runs on port 3100 because Grafana uses 3000 during the demo. To point it at another
backend, copy `.env.example` to `.env.local` and set `NEXT_PUBLIC_GHOSTOPS_API`; add the
site's origin to the backend's `GHOSTOPS_CORS_ORIGINS`.

`npm run build` and `npm run typecheck` are what CI runs.
