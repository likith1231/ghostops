import os

from flask import Flask, jsonify, request
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)
leak_store = []

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
LEAK_STORE_SIZE = Gauge(
    "sample_app_leak_store_size_bytes",
    "Total bytes currently held in the in-memory leak store",
)
LEAK_STORE_COUNT = Gauge(
    "sample_app_leak_store_objects",
    "Number of leaked objects in memory",
)
REQUESTS = Counter(
    "sample_app_requests_total",
    "Requests served, by HTTP status and release",
    ["status", "release"],
)

# Release identity and a fault knob for the canary-regression scenario (C1): a "bad
# release" sets FAULTY_RATE so a share of checkout requests fail. Defaults are healthy.
RELEASE = os.getenv("RELEASE", "stable")
FAULTY_RATE = float(os.getenv("FAULTY_RATE", "0"))
_checkouts = 0


@app.after_request
def _count(response):
    if request.path != "/metrics":
        REQUESTS.labels(status=str(response.status_code), release=RELEASE).inc()
    return response


def _update_metrics():
    """Refresh gauge values from current leak_store state."""
    LEAK_STORE_COUNT.set(len(leak_store))
    LEAK_STORE_SIZE.set(len(leak_store) * 10 * 1024 * 1024)  # each chunk is 10 MiB


@app.route("/metrics")
def metrics():
    """Prometheus scrape endpoint."""
    _update_metrics()
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


@app.route("/health")
def health():
    return jsonify(status="ok", leaked_objects=len(leak_store))


@app.route("/leak")
def leak():
    # Fixed: do not retain per-request allocations in the module-level leak_store.
    return jsonify(status="ok")


@app.route("/api/checkout")
def checkout():
    """A business endpoint. With FAULTY_RATE set, every 1/FAULTY_RATE-th request fails."""
    global _checkouts
    _checkouts += 1
    if FAULTY_RATE > 0 and _checkouts % max(1, round(1 / FAULTY_RATE)) == 0:
        return jsonify(error="checkout failed", release=RELEASE), 500
    return jsonify(status="ok", release=RELEASE)


@app.route("/")
def index():
    return jsonify(service="ghostops-sample-app", version="1.2", release=RELEASE)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
