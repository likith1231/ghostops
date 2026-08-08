"""
GhostOps Agent Configuration
=============================

Loads API keys from the project-root .env file and exposes model-name
constants so they can be changed in exactly one place.

Currently running on Gemini (free tier) — swap PRIMARY_MODEL and the LLM
client in diagnostic_reasoner.py / patch_generator.py /
validation_officer.py back to Claude once Anthropic billing is set up.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load environment variables from the project root .env
# Walks up from this file's directory to find the first .env it hits.
# ---------------------------------------------------------------------------
_project_root = Path(__file__).resolve().parent.parent
_env_path = _project_root / ".env"
load_dotenv(dotenv_path=_env_path)

# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
ANTHROPIC_API_KEY: str = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_API_KEY: str = os.environ.get("GOOGLE_API_KEY", "")

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

# ---------------------------------------------------------------------------
# Model constants
# Swap these values to change models across the entire pipeline.
# ---------------------------------------------------------------------------
PRIMARY_MODEL: str = "gemini/gemini-3-flash-preview"         # Google Gemini (free tier) — gemini/ prefix required by litellm
FALLBACK_MODEL: str = "gemini-1.5-pro"              # Google Gemini 1.5 Pro

# ---------------------------------------------------------------------------
# Docker / sandbox settings
# ---------------------------------------------------------------------------
SANDBOX_IMAGE: str = "python:3.11-slim"
SANDBOX_TIMEOUT_SECONDS: int = 120
