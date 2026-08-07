"""
Diagnostic Reasoner Agent
=========================

Consumes a structured *failure context* JSON and classifies the root cause
into one of:

    ``memory_leak`` | ``ci_test_failure`` | ``unknown``

It also produces a short structured diagnosis that cites the specific log
lines and metric values that led to the conclusion.

LLM: Claude 3.5 Sonnet (Anthropic) via the CrewAI ``ChatAnthropic`` wrapper.
"""

from __future__ import annotations

import json
from textwrap import dedent

from crewai import Agent, Task
from langchain_anthropic import ChatAnthropic

from agents.config import ANTHROPIC_API_KEY, PRIMARY_MODEL

# ---------------------------------------------------------------------------
# System prompt — intentionally narrow to avoid hallucinated speculation.
# ---------------------------------------------------------------------------
REASONER_SYSTEM_PROMPT: str = dedent("""\
    You are the GhostOps Diagnostic Reasoner, an expert SRE analyst.

    RULES
    -----
    1. You ONLY reason from the failure context provided.  Never speculate
       about causes that are not supported by the logs, metrics, or past
       incidents in the context.
    2. Classify the root cause into EXACTLY ONE of:
       • memory_leak    — evidence of unbounded memory growth or OOM kill.
       • ci_test_failure — a CI/CD test failure (e.g. pytest, GitHub Actions).
       • unknown         — insufficient evidence for either category.
    3. Cite the exact log lines or metric data points that justify the
       classification.  Use the format  `[LOG] <line>`  or  `[METRIC] <name>=<value>`.
    4. Return your answer as a JSON object with these keys:
       {
         "root_cause": "<category>",
         "confidence": "<high|medium|low>",
         "summary": "<one-sentence summary>",
         "evidence": [
           {"type": "log|metric|incident", "reference": "<exact excerpt>", "reasoning": "<why it matters>"}
         ]
       }
    5. Return ONLY the JSON — no markdown fences, no preamble.
""")


def build_reasoner_agent() -> Agent:
    """Instantiate the Diagnostic Reasoner CrewAI Agent."""
    llm = ChatAnthropic(
        model=PRIMARY_MODEL,
        api_key=ANTHROPIC_API_KEY,
        temperature=0.0,
        max_tokens=2048,
    )

    return Agent(
        role="Diagnostic Reasoner",
        goal=(
            "Analyse the failure context and classify the root cause as "
            "memory_leak, ci_test_failure, or unknown with supporting evidence."
        ),
        backstory=(
            "You are a senior SRE who has triaged thousands of production "
            "incidents.  You never guess — you only cite hard evidence from "
            "logs, metrics, and known past incidents."
        ),
        llm=llm,
        verbose=True,
        allow_delegation=False,
    )


def build_reasoner_task(agent: Agent, failure_context: dict) -> Task:
    """Create a CrewAI Task for the Diagnostic Reasoner.

    Parameters
    ----------
    agent:
        The ``Agent`` returned by :func:`build_reasoner_agent`.
    failure_context:
        The raw failure-context dict that the ingestion layer hands in.
        Expected top-level keys: ``alert_type``, ``logs``, ``metrics``,
        ``past_incidents`` (optional).

    Returns
    -------
    Task
        A CrewAI Task whose output will be the JSON diagnosis string.
    """
    context_json = json.dumps(failure_context, indent=2)

    return Task(
        description=dedent(f"""\
            Analyse the following failure context and produce a root-cause
            diagnosis.

            ---BEGIN FAILURE CONTEXT---
            {context_json}
            ---END FAILURE CONTEXT---

            Follow your system instructions exactly.  Return ONLY the JSON
            diagnosis object — no extra commentary.
        """),
        expected_output=(
            "A JSON object with keys: root_cause, confidence, summary, evidence."
        ),
        agent=agent,
    )
