"""
GhostOps Agent Configuration
=============================

Loads API keys from the project-root .env file and exposes model-name
constants so they can be changed in exactly one place.

Currently running on Gemini (free tier) — swap PRIMARY_MODEL and the LLM
client in diagnostic_reasoner.py / patch_generator.py /
validation_officer.py back to Claude once Anthropic billing is set up.
"""

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("ghostops.config")

_project_root = Path(__file__).resolve().parent.parent
_env_path = _project_root / ".env"

# ---------------------------------------------------------------------------
# Secrets Management: HashiCorp Vault (Phase 11)
# ---------------------------------------------------------------------------
# Attempt to load secrets from local Vault dev server first.
# If it fails, fall back to the .env file.

VAULT_ADDR = os.environ.get("VAULT_ADDR", "http://localhost:8200")
VAULT_TOKEN = os.environ.get("VAULT_TOKEN", "root")

vault_secrets: dict[str, str] = {}

try:
    req = urllib.request.Request(
        f"{VAULT_ADDR}/v1/secret/data/ghostops",
        headers={"X-Vault-Token": VAULT_TOKEN},
    )
    with urllib.request.urlopen(req, timeout=2.0) as response:
        data = json.loads(response.read())
        # Vault KV v2 structure: data -> data -> actual keys
        vault_secrets = data.get("data", {}).get("data", {})
        logger.info("Successfully loaded secrets from Vault (%s)", VAULT_ADDR)
except urllib.error.URLError as exc:
    logger.warning(
        "Vault is unreachable (%s) — falling back to .env file! "
        "Ensure 'kubectl port-forward svc/vault 8200:8200' is running if you want Vault secrets.",
        exc.reason
    )
    load_dotenv(dotenv_path=_env_path)
except Exception as exc:
    logger.warning("Failed to read from Vault: %s — falling back to .env file!", exc)
    load_dotenv(dotenv_path=_env_path)

def get_secret(key: str, default: str = "") -> str:
    """Fetch secret from Vault first, then OS environment (which holds .env values)."""
    return vault_secrets.get(key) or os.environ.get(key, default)

# Enforce disabling of CrewAI telemetry to prevent TracerProvider collisions
os.environ["CREWAI_DISABLE_TELEMETRY"] = get_secret("CREWAI_DISABLE_TELEMETRY", "true")

# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY: str = get_secret("ANTHROPIC_API_KEY", "")
GOOGLE_API_KEY: str = get_secret("GOOGLE_API_KEY", "")

if not GOOGLE_API_KEY:
    raise EnvironmentError(
        "GOOGLE_API_KEY is not set. "
        f"Make sure it exists in {_env_path} or is exported in your shell."
    )

# Anthropic key is optional for now — warn instead of crashing.
if not ANTHROPIC_API_KEY:
    import warnings
    warnings.warn(
        "ANTHROPIC_API_KEY is not set — Claude models will be unavailable.",
        stacklevel=2,
    )

GITHUB_TOKEN: str = get_secret("GITHUB_TOKEN", "")
GITHUB_REPO: str = get_secret("GITHUB_REPO", "likith1231/ghostops")

if not GITHUB_TOKEN:
    import warnings
    warnings.warn(
        "GITHUB_TOKEN is not set — auto-PR creation will be disabled.",
        stacklevel=2,
    )

# ---------------------------------------------------------------------------
# Model constants
# Swap these values to change models across the entire pipeline.
# ---------------------------------------------------------------------------
CLAUDE_MODEL: str = "anthropic/claude-sonnet-4-6"
PRIMARY_MODEL: str = "gemini/gemini-3-flash-preview"         # Google Gemini (free tier) — gemini/ prefix required by litellm
FALLBACK_MODEL: str = "gemini-1.5-pro"              # Google Gemini 1.5 Pro

# ---------------------------------------------------------------------------
# Docker / sandbox settings
# ---------------------------------------------------------------------------
SANDBOX_IMAGE: str = "python:3.11-slim"
SANDBOX_TIMEOUT_SECONDS: int = 120
