"""GhostOps regression test: /leak must not keep memory per request."""
import app as sample


def test_leak_endpoint_memory_stays_bounded():
    client = sample.app.test_client()
    for _ in range(40):
        assert client.get("/leak").status_code == 200
    held = sum(len(chunk) for chunk in sample.leak_store)
    assert held <= 20 * 1024 * 1024, f"{held // (1024 * 1024)} MiB held after 40 requests"
