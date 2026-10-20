"""backend/main.py helpers: pod logs, Prometheus memory, source files, alert without a pod."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from backend import main


# ---- _fetch_pod_logs ------------------------------------------------------------------------
def _k8s(log_text=None, error=None, incluster=False):
    k8s = MagicMock()
    api = k8s.client.CoreV1Api.return_value
    if error:
        api.read_namespaced_pod_log.side_effect = error
    else:
        api.read_namespaced_pod_log.return_value = log_text
    exc = type("ConfigException", (Exception,), {})
    k8s.config.config_exception.ConfigException = exc
    if not incluster:
        k8s.config.load_incluster_config.side_effect = exc()
    return k8s, api


def test_pod_logs_returns_non_empty_lines():
    k8s, api = _k8s("a\n\nb\nc\n")
    with patch.dict("sys.modules", {"kubernetes": k8s}):
        assert main._fetch_pod_logs("sample-app-1") == ["a", "b", "c"]
    kwargs = api.read_namespaced_pod_log.call_args.kwargs
    assert kwargs["name"] == "sample-app-1" and kwargs["tail_lines"] == main.POD_LOG_LINES
    k8s.config.load_kube_config.assert_called_once()  # outside a cluster, use ~/.kube/config


def test_pod_logs_in_cluster_config_preferred():
    k8s, _ = _k8s("x", incluster=True)
    with patch.dict("sys.modules", {"kubernetes": k8s}):
        main._fetch_pod_logs("p")
    k8s.config.load_kube_config.assert_not_called()


def test_pod_logs_error_becomes_context_line():
    k8s, _ = _k8s(error=RuntimeError("pods not found"))
    with patch.dict("sys.modules", {"kubernetes": k8s}):
        out = main._fetch_pod_logs("gone")
    assert len(out) == 1 and out[0].startswith("[ERROR]") and "pods not found" in out[0]


# ---- _query_prometheus_memory --------------------------------------------------------------
def _resp(payload, status=200):
    r = MagicMock(status_code=status)
    r.json.return_value = payload
    r.raise_for_status.side_effect = None if status == 200 else RuntimeError(f"HTTP {status}")
    return r


def instant(value):
    return _resp({"status": "success", "data": {"result": [{"value": [1700000000, str(value)]}]}})


def ranged(values):
    return _resp({"status": "success", "data": {"result": [{"values": [[1700000000 + 15 * i, str(v)] for i, v in enumerate(values)]}]}})


def test_prometheus_uses_history_for_working_set():
    responses = {"query": [instant(118e6), instant(134217728)], "query_range": [ranged([27e6, 80e6, 118e6])]}
    with patch.object(main.requests, "get", side_effect=lambda url, **kw: responses[url.rsplit("/", 1)[1]].pop(0)):
        m = main._query_prometheus_memory("sample-app-1")
    assert [p["value"] for p in m["container_memory_working_set_bytes"]] == [27e6, 80e6, 118e6]
    assert m["container_spec_memory_limit_bytes"][0]["value"] == 134217728


def test_prometheus_range_failure_keeps_instant_value():
    def get(url, **kw):
        if url.endswith("query_range"):
            raise ConnectionError("timeout")
        return instant(118e6)
    with patch.object(main.requests, "get", side_effect=get):
        m = main._query_prometheus_memory("p")
    assert m["container_memory_working_set_bytes"] == [{"ts": "1700000000", "value": 118e6}]


def test_prometheus_single_point_range_does_not_replace_instant():
    responses = {"query": [instant(5), instant(10)], "query_range": [ranged([7])]}
    with patch.object(main.requests, "get", side_effect=lambda url, **kw: responses[url.rsplit("/", 1)[1]].pop(0)):
        m = main._query_prometheus_memory("p")
    assert m["container_memory_working_set_bytes"][0]["value"] == 5


def test_prometheus_down_returns_empty_metrics():
    with patch.object(main.requests, "get", side_effect=ConnectionError("refused")):
        assert main._query_prometheus_memory("p") == {}


def test_prometheus_error_status_skips_metric():
    err = _resp({"status": "error", "error": "bad query"})
    with patch.object(main.requests, "get", return_value=err):
        assert main._query_prometheus_memory("p") == {}


def test_prometheus_query_targets_the_pod():
    with patch.object(main.requests, "get", side_effect=ConnectionError()) as get:
        main._query_prometheus_memory("sample-app-xyz")
    assert all('pod="sample-app-xyz"' in c.kwargs["params"]["query"] for c in get.call_args_list)


# ---- _read_source_file ---------------------------------------------------------------------
def test_source_file_keyed_repo_relative():
    out = main._read_source_file(str(main._PROJECT_ROOT / "sample-app" / "math_utils.py"))
    assert list(out) == ["sample-app/math_utils.py"]


def test_source_file_outside_repo_keyed_by_name(tmp_path):
    f = tmp_path / "other.py"
    f.write_text("x = 1\n")
    assert main._read_source_file(str(f)) == {"other.py": "x = 1\n"}


def test_source_file_missing_is_empty(tmp_path):
    assert main._read_source_file(str(tmp_path / "nope.py")) == {}


# ---- alert without a pod label ---------------------------------------------------------------
def test_alert_without_pod_skips_cluster_calls():
    with patch.object(main, "_fetch_pod_logs") as logs, patch.object(main, "_query_prometheus_memory") as prom, \
         patch.object(main, "run_pipeline", return_value={"validation_result": {"passed": False}}) as run:
        main._run_alert_pipeline({"labels": {"alertname": "SampleAppHighMemory"}}, "")
    logs.assert_not_called()
    prom.assert_not_called()
    ctx = run.call_args.args[0]
    assert ctx["logs"][0].startswith("[WARN] No pod name") and ctx["metrics"] == {}
