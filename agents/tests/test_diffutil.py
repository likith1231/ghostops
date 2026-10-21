"""Hunk-header repair for model-written diffs."""

import subprocess

from agents.diffutil import recount_hunks

SOURCE = '"""Arithmetic helpers."""\n\n\ndef add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a + b\n'
# The diff Sonnet 5.5 wrote in a live run: the change is right, the header says -5,4 +5,4 for a 5-line hunk.
BAD = "--- a/m.py\n+++ b/m.py\n@@ -5,4 +5,4 @@\n     return a + b\n \n \n def multiply(a, b):\n-    return a + b\n+    return a * b\n"


def test_wrong_counts_are_fixed():
    assert "@@ -5,5 +5,5 @@" in recount_hunks(BAD)


def test_correct_diff_is_unchanged():
    good = BAD.replace("-5,4 +5,4", "-5,5 +5,5")
    assert recount_hunks(good) == good


def test_repaired_diff_applies_where_the_original_does_not(tmp_path):
    (tmp_path / "m.py").write_text(SOURCE)

    def apply(diff):
        return subprocess.run(["patch", "-p1", "--batch", "--dry-run"], input=diff, text=True, cwd=tmp_path, capture_output=True).returncode

    assert apply(BAD) != 0
    assert apply(recount_hunks(BAD)) == 0


def test_two_hunks_and_two_files_are_counted_separately():
    diff = ("--- a/x.py\n+++ b/x.py\n@@ -1,9 +1,9 @@ def f\n a\n-b\n+B\n c\n@@ -20,7 +20,7 @@\n-d\n+D\n"
            "--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n-e\n+E\n")
    fixed = recount_hunks(diff).split("\n")
    assert "@@ -1,3 +1,3 @@ def f" in fixed and "@@ -20,1 +20,1 @@" in fixed and "@@ -1,1 +1,1 @@" in fixed


def test_empty_and_headerless_input_pass_through():
    assert recount_hunks("") == "" and recount_hunks("no diff here") == "no diff here"
