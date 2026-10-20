"""
Regression test generation
==========================

For a fix, write a new test that pins the bug down. The Validation Officer
then proves it: the test must **fail** on the unpatched code and **pass** on
the patched code (``validation_officer.verify_regression_test``). Only a
verified test is attached to the fix and shipped in the PR.

Offline, tests come from templates for the bug patterns GhostOps knows; the
before/after proof is the same either way.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from textwrap import dedent
from typing import Any

# (root cause, file the fix touches) -> (test module name, test source)
TEMPLATES: dict[tuple[str, str], tuple[str, str]] = {
    ("memory_leak", "app.py"): (
        "test_ghostops_regression_memory.py",
        dedent('''\
            """GhostOps regression test: /leak must not keep memory per request."""
            import app as sample


            def test_leak_endpoint_memory_stays_bounded():
                client = sample.app.test_client()
                for _ in range(40):
                    assert client.get("/leak").status_code == 200
                held = sum(len(chunk) for chunk in sample.leak_store)
                assert held <= 20 * 1024 * 1024, f"{held // (1024 * 1024)} MiB held after 40 requests"
            '''),
    ),
    ("ci_test_failure", "math_utils.py"): (
        "test_ghostops_regression_math.py",
        dedent('''\
            """GhostOps regression test: multiply() must multiply, for every sign and zero."""
            import pytest

            from math_utils import multiply


            @pytest.mark.parametrize("a,b", [(2, 4), (3, 3), (0, 7), (-2, 5), (-3, -3), (1.5, 2)])
            def test_multiply_matches_the_operator(a, b):
                assert multiply(a, b) == a * b
            '''),
    ),
    ("null_reference", "user_service.py"): (
        "test_ghostops_regression_null.py",
        dedent('''\
            """GhostOps regression test: empty upstream payloads must not crash display_name()."""
            import pytest

            from user_service import display_name


            @pytest.mark.parametrize("payload", [None, {}, {"name": None}, {"name": ""}])
            def test_display_name_survives_empty_payloads(payload):
                assert display_name(payload) == "anonymous"
            '''),
    ),
}


def generate(diagnosis: dict[str, Any], patch: dict[str, Any]) -> dict[str, str] | None:
    """A regression test for this fix, or None if GhostOps has no pattern for it."""
    file_path = patch.get("file_path")
    if not file_path:
        return None
    template = TEMPLATES.get((str(diagnosis.get("root_cause", "")), PurePosixPath(file_path).name))
    if template is None:
        return None
    name, source = template
    return {"path": str(PurePosixPath(file_path).parent / name), "content": source, "source": "template"}
