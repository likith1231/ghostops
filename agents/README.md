# GhostOps — Multi-Agent Diagnosis-and-Patch Pipeline

A three-agent CrewAI pipeline that **diagnoses production failures**, **generates
minimal patches**, and **validates them in an isolated Docker sandbox** — all
without touching the live system.

---

## Architecture

```
failure_context (JSON)
        │
        ▼
┌──────────────────────┐
│  Diagnostic Reasoner │  ← Claude 3.5 Sonnet
│  (classify root cause)│
└──────────┬───────────┘
           │ diagnosis JSON
           ▼
┌──────────────────────┐
│   Patch Generator    │  ← Claude 3.5 Sonnet
│  (unified diff)      │
└──────────┬───────────┘
           │ diff + explanation
           ▼
┌──────────────────────┐
│  Validation Officer  │  ← Docker sandbox + pytest
│  (pass / fail)       │
└──────────────────────┘
           │
           ▼
     final result dict
```

## Files

| File | Purpose |
|------|---------|
| `config.py` | Loads `ANTHROPIC_API_KEY` and `GOOGLE_API_KEY` from `.env`; defines model-name constants (`PRIMARY_MODEL`, `FALLBACK_MODEL`). |
| `diagnostic_reasoner.py` | Agent 1 — classifies root cause as `memory_leak`, `ci_test_failure`, or `unknown` with cited evidence. |
| `patch_generator.py` | Agent 2 — generates a minimal unified diff fixing the diagnosed issue. |
| `validation_officer.py` | Agent 3 — applies the diff inside a throwaway Docker container, runs `pytest`, returns pass/fail verdict. |
| `crew.py` | Wires all three agents into a sequential CrewAI pipeline. Exposes `run_pipeline()`. |
| `requirements.txt` | Python dependencies for the agent subsystem. |

## Quick Start

### 1. Install dependencies

```bash
cd ~/ghostops
pip install -r agents/requirements.txt
```

### 2. Set up your `.env`

Create `~/ghostops/.env` (if it doesn't exist yet):

```env
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...
```

### 3. Make sure Docker is running

The Validation Officer needs Docker to spin up sandbox containers:

```bash
docker info  # should show the Docker daemon is up
```

### 4. Run the pipeline

**From Python:**

```python
from agents.crew import run_pipeline

# B1 — Memory leak / OOM kill
failure_context = {
    "alert_type": "memory_high",
    "logs": [
        "2026-08-07T08:00:12Z app WARNING: /api/data handler allocated 2.3 GB",
        "2026-08-07T08:01:45Z kernel: Out of memory: Killed process 4321 (gunicorn)",
        "2026-08-07T08:01:45Z app ERROR: Worker process terminated (OOM)",
        "2026-08-07T07:55:00Z app INFO: request_cache size: 148201 entries",
    ],
    "metrics": {
        "container_memory_usage_bytes": [
            {"ts": "08:00", "value": 1_900_000_000},
            {"ts": "07:55", "value": 1_600_000_000},
            {"ts": "07:50", "value": 1_200_000_000},
        ],
        "container_memory_limit_bytes": 2_000_000_000,
    },
    "source_files": {
        "app.py": "from flask import Flask, jsonify\n\napp = Flask(__name__)\nrequest_cache = []\n\n@app.route('/api/data')\ndef get_data():\n    result = expensive_computation()\n    request_cache.append(result)\n    return jsonify(result)\n\ndef expensive_computation():\n    return {'value': list(range(10000))}\n\nif __name__ == '__main__':\n    app.run(debug=True)\n",
    },
    "past_incidents": [],
}

result = run_pipeline(failure_context)
print(result["diagnosis"])       # root-cause classification
print(result["patch"]["diff"])   # the generated fix
print(result["validation_result"]["passed"])  # True / False
```

**From the command line** (uses built-in sample contexts):

```bash
# Run with the B1 (memory leak) sample:
python -m agents.crew

# Run with the C4 (CI failure) sample:
python -m agents.crew c4
```

## Supported Use Cases

| Code | Scenario | Root Cause Category |
|------|----------|---------------------|
| B1 | Memory leak / OOM kill (Flask app) | `memory_leak` |
| C4 | CI build failure (GitHub Actions + pytest) | `ci_test_failure` |

## `failure_context` Schema

```json
{
  "alert_type": "memory_high | ci_failure",
  "logs": ["<log line>", "..."],
  "metrics": {
    "<metric_name>": [{"ts": "<timestamp>", "value": 123}, "..."]
  },
  "source_files": {
    "<file_path>": "<file content as string>"
  },
  "past_incidents": [
    {"id": "INC-XXXX", "summary": "...", "resolution": "..."}
  ]
}
```

## How the Validation Sandbox Works

1. A fresh `python:3.11-slim` container is created (no network, 256 MB RAM cap).
2. The original source file is written inside the container.
3. The unified diff is applied via `patch`.
4. `pytest` runs against the patched file.
5. The container is destroyed immediately after — nothing persists.

This guarantees that **no host file or running service is ever modified** during
validation.
