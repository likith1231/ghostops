"""
Patch Generator Agent
=====================

Takes the structured diagnosis from the Diagnostic Reasoner plus the relevant
source file content and generates a minimal, targeted unified diff that fixes
the identified root cause.

Supported root causes
---------------------
- **memory_leak** (B1) — e.g. add cache eviction, bound a growing list, close
  resources properly.
- **ci_test_failure** (C4) — fix the regression in the application code (NOT
  the test) unless the test itself is genuinely wrong.

LLM: Gemini (Google, free tier) via CrewAI's native ``LLM`` class.
"""

from __future__ import annotations

import json
from textwrap import dedent

from crewai import Agent, LLM, Task

from agents.config import GOOGLE_API_KEY, PRIMARY_MODEL

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
PATCHGEN_SYSTEM_PROMPT: str = dedent("""\
    You are the GhostOps Patch Generator, an expert software engineer who
    writes the smallest possible fix for a diagnosed issue.

    RULES
    -----
    1. You receive a JSON diagnosis and one or more source files.
    2. Generate a MINIMAL unified diff (``--- a/path`` / ``+++ b/path`` format)
       that addresses ONLY the diagnosed root cause.  Do not refactor, do not
       optimise, do not touch unrelated code.
    3. For memory_leak issues:
       • Prefer adding bounded caches (e.g. ``collections.OrderedDict`` with
         ``maxlen``, ``functools.lru_cache``, or explicit eviction logic).
       • Close resources (file handles, DB cursors) that were left open.
    4. For ci_test_failure issues:
       • Fix the APPLICATION code that causes the test to fail.
       • Only modify the test if the test expectation is demonstrably wrong.
    5. After the diff, include a one-paragraph ``## Explanation`` section
       describing WHAT you changed and WHY.
    6. Return your answer as a JSON object:
       {
         "diff": "<unified diff string>",
         "explanation": "<one-paragraph explanation>"
       }
    7. Return ONLY the JSON — no markdown fences, no preamble.
""")


def build_patch_agent() -> Agent:
    """Instantiate the Patch Generator CrewAI Agent."""
    llm = LLM(
        model=PRIMARY_MODEL,
        api_key=GOOGLE_API_KEY,
        temperature=0.0,
    )

    return Agent(
        role="Patch Generator",
        goal=(
            "Generate a minimal, correct unified diff that fixes the diagnosed "
            "root cause without touching unrelated code."
        ),
        backstory=(
            "You are a meticulous staff engineer known for surgical one-liner "
            "fixes.  You never over-engineer — you change only what is strictly "
            "necessary to resolve the issue."
        ),
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )


def build_patch_task(
    agent: Agent,
    diagnosis_json: str,
    source_files: dict[str, str],
) -> Task:
    """Create a CrewAI Task for the Patch Generator.

    Parameters
    ----------
    agent:
        The ``Agent`` returned by :func:`build_patch_agent`.
    diagnosis_json:
        The JSON string emitted by the Diagnostic Reasoner.
    source_files:
        A mapping of ``{file_path: file_content}`` for every file the
        patch might need to touch.

    Returns
    -------
    Task
        A CrewAI Task whose output is a JSON string with ``diff`` and
        ``explanation`` keys.
    """
    files_block = "\n\n".join(
        f"### {path}\n```\n{content}\n```"
        for path, content in source_files.items()
    )

    return Task(
        description=dedent(f"""\
            Using the diagnosis below, generate a minimal unified diff that
            fixes the root cause.

            ---BEGIN DIAGNOSIS---
            {diagnosis_json}
            ---END DIAGNOSIS---

            ---BEGIN SOURCE FILES---
            {files_block}
            ---END SOURCE FILES---

            Follow your system instructions exactly.  Return ONLY the JSON
            object with "diff" and "explanation" keys.
        """),
        expected_output=(
            "A JSON object with keys: diff (unified diff string), "
            "explanation (one paragraph)."
        ),
        agent=agent,
    )
