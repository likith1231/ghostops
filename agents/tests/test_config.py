"""agents/config.py: Vault first, then .env/environment; mock mode; the API-key guard.

config runs at import time, so each case imports it in a fresh interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PRINT = "import json, agents.config as c; print(json.dumps({'model': c.PRIMARY_MODEL, 'mock': c.MOCK_LLM, 'repo': c.GITHUB_REPO, 'vault': bool(c.vault_secrets)}))"


def load_config(env: dict) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC", "GOOGLE", "GHOSTOPS", "GITHUB", "VAULT", "CLAUDE"))}
    # A .env in the repo would leak real values in; point dotenv at an empty file.
    return subprocess.run(
        [sys.executable, "-c", f"import dotenv; dotenv.load_dotenv = lambda *a, **k: None; {PRINT}"],
        cwd=ROOT, env={**base, **env}, capture_output=True, text=True,
    )


@pytest.fixture()
def vault():
    secrets = {"ANTHROPIC_API_KEY": "from-vault", "GITHUB_REPO": "vault/repo"}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            ok = self.path == "/v1/secret/data/ghostops" and self.headers.get("X-Vault-Token") == "root"
            self.send_response(200 if ok else 403)
            self.end_headers()
            if ok:
                self.wfile.write(json.dumps({"data": {"data": secrets}}).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_secrets_come_from_vault_first(vault):
    out = load_config({"VAULT_ADDR": vault, "ANTHROPIC_API_KEY": "from-env"})
    data = json.loads(out.stdout)
    assert data["vault"] is True and data["repo"] == "vault/repo"
    assert data["model"].startswith("anthropic/")


def test_env_used_when_vault_unreachable():
    out = load_config({"VAULT_ADDR": "http://127.0.0.1:9", "ANTHROPIC_API_KEY": "k", "GITHUB_REPO": "env/repo"})
    data = json.loads(out.stdout)
    assert data["vault"] is False and data["repo"] == "env/repo"


def test_missing_key_refuses_to_start():
    out = load_config({"VAULT_ADDR": "http://127.0.0.1:9"})
    assert out.returncode != 0 and "ANTHROPIC_API_KEY is not set" in out.stderr


def test_mock_mode_needs_no_key():
    out = load_config({"VAULT_ADDR": "http://127.0.0.1:9", "GHOSTOPS_MOCK_LLM": "1"})
    assert out.returncode == 0 and json.loads(out.stdout)["mock"] is True


def test_gemini_used_without_anthropic_key():
    out = load_config({"VAULT_ADDR": "http://127.0.0.1:9", "GOOGLE_API_KEY": "g"})
    assert json.loads(out.stdout)["model"].startswith("gemini/")
