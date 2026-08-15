from flask import Flask, jsonify
from prometheus_client import Gauge, generate_latest, CONTENT_TYPE_LATEST

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
    return jsonify(status="ok")
    leak_store.append("0" * 10 * 1024 * 1024)
@app.route("/")
def index():
    return jsonify(service="ghostops-sample-app", version="1.1")
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
