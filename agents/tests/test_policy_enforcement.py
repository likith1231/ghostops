"""
Tests for OPA Gatekeeper policy enforcement via Validation Officer.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock
from agents.validation_officer import _run_opa_checks

def test_opa_rejects_unauthorized_file():
    """Ensure that patches to files outside the allowlist are rejected."""
    # A file path that is NOT in sample-app/ or infra/k8s/sample-app.yaml
    file_path = "backend/main.py"
    original_content = "print('hello')"
    diff_text = "--- backend/main.py\n+++ backend/main.py\n@@ -1,1 +1,2 @@\n print('hello')\n+print('hacked')\n"

    result = _run_opa_checks(file_path, original_content, diff_text)
    
    assert result is not None
    assert result["exit_code"] == 1
    assert "Patch targets unauthorized file path: backend/main.py" in result["output"]


def test_opa_rejects_missing_limits():
    """Ensure that Kubernetes Deployments without resource limits are rejected."""
    file_path = "infra/k8s/sample-app.yaml"
    original_content = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: test
spec:
  template:
    spec:
      containers:
        - name: bad-container
          image: nginx
"""
    diff_text = ""  # No patch needed, the original content already violates policy

    result = _run_opa_checks(file_path, original_content, diff_text)
    
    assert result is not None
    assert result["exit_code"] == 1
    assert "does not have a CPU limit set" in result["output"]
    assert "does not have a memory limit set" in result["output"]


def test_opa_rejects_privileged_container():
    """Ensure that Kubernetes Deployments with privileged containers are rejected."""
    file_path = "infra/k8s/sample-app.yaml"
    original_content = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: test
spec:
  template:
    spec:
      containers:
        - name: bad-container
          image: nginx
          securityContext:
            privileged: true
"""
    diff_text = ""

    result = _run_opa_checks(file_path, original_content, diff_text)
    
    assert result is not None
    assert result["exit_code"] == 1
    assert "Privileged container <bad-container> is not allowed" in result["output"]


def test_opa_accepts_compliant_manifest():
    """Ensure that compliant manifests are accepted."""
    file_path = "infra/k8s/sample-app.yaml"
    original_content = """apiVersion: apps/v1
kind: Deployment
metadata:
  name: test
spec:
  template:
    spec:
      containers:
        - name: good-container
          image: nginx
          resources:
            limits:
              cpu: "1"
              memory: "128Mi"
"""
    diff_text = ""

    result = _run_opa_checks(file_path, original_content, diff_text)
    
    assert result is None  # None means passed
