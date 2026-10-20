"""Fix library: learn from merged fixes, reuse promoted ones without an LLM."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents import crew, feedback, fix_library, knowledge_base, mock_llm

ORIGINAL = "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a + b\n"
FIXED = ORIGINAL.replace("def multiply(a, b):\n    return a + b", "def multiply(a, b):\n    return a * b")
DIFF = mock_llm.unified_diff("sample-app/math_utils.py", ORIGINAL, FIXED)


@pytest.fixture()
def db(tmp_path):
    return tmp_path / "kb.db"


def test_change_key_ignores_paths_line_numbers_and_spacing():
    other = DIFF.replace("sample-app/", "").replace("@@ -4,3 +4,3 @@", "@@ -40,3 +40,3 @@").replace("a * b", "a  *  b")
    assert fix_library.change_key(DIFF) == fix_library.change_key(other)


def test_promoted_after_two_merges(db):
    assert fix_library.learn("ci_test_failure", DIFF, db_path=db)["promoted"] is False
    entry = fix_library.learn("ci_test_failure", DIFF.replace("sample-app/", ""), db_path=db)
    assert entry["merges"] == 2 and entry["promoted"] is True
    assert len(fix_library.entries(db_path=db)) == 1


def test_unpromoted_fix_is_not_suggested(db):
    fix_library.learn("ci_test_failure", DIFF, db_path=db)
    assert fix_library.suggest("ci_test_failure", {"sample-app/math_utils.py": ORIGINAL}, db_path=db) is None


def test_promoted_fix_suggested_when_it_applies(db):
    fix_library.learn("ci_test_failure", DIFF, db_path=db)
    fix_library.learn("ci_test_failure", DIFF, db_path=db)
    patch_ = fix_library.suggest("ci_test_failure", {"sample-app/math_utils.py": ORIGINAL}, db_path=db)
    assert patch_["source"] == "fix_library" and patch_["file_path"] == "sample-app/math_utils.py"
    assert fix_library.entries(db_path=db)[0]["uses"] == 1


def test_not_suggested_when_code_has_changed(db):
    fix_library.learn("ci_test_failure", DIFF, db_path=db)
    fix_library.learn("ci_test_failure", DIFF, db_path=db)
    assert fix_library.suggest("ci_test_failure", {"sample-app/math_utils.py": FIXED}, db_path=db) is None
    assert fix_library.suggest("memory_leak", {"sample-app/math_utils.py": ORIGINAL}, db_path=db) is None


def test_garbage_is_not_learned(db):
    assert fix_library.learn("", DIFF, db_path=db) is None
    assert fix_library.learn("x", "no diff here", db_path=db) is None


def test_merged_pr_feeds_the_library(db):
    knowledge_base.init_db(db)
    for n in (1, 2):
        knowledge_base.log_incident({"alert_type": "ci_failure"}, {"root_cause": "ci_test_failure"}, {"diff": DIFF}, {"passed": True},
                                    {"pr_created": True, "pr_url": f"https://github.com/a/b/pull/{n}"}, db_path=db)
    merged = MagicMock(status_code=200)
    merged.json.return_value = {"state": "closed", "merged_at": "2026-10-01T00:00:00Z", "labels": []}
    with patch.object(feedback.requests, "get", return_value=merged):
        feedback.sync_pr_outcomes(db_path=db)
    assert fix_library.entries(db_path=db)[0]["promoted"] is True


def test_pipeline_uses_learned_fix_without_a_model():
    source = {"sample-app/math_utils.py": ORIGINAL, "sample-app/test_math_utils.py": "..."}
    ctx = {"alert_type": "ci_failure", "logs": ["FAILED x"], "metrics": {}, "source_files": source}
    learned = {"diff": DIFF, "file_path": "sample-app/math_utils.py", "explanation": "learned", "source": "fix_library", "skill_id": 1}
    with patch.object(crew, "MOCK_LLM", False), patch.object(crew.fix_library, "suggest", return_value=learned), \
         patch.object(crew, "build_patch_agent") as agent, patch.object(crew, "Crew") as crew_cls, \
         patch.object(crew, "build_reasoner_agent"), patch.object(crew, "build_reasoner_task"), \
         patch.object(crew, "build_validation_agent"), patch.object(crew, "build_validation_task"), \
         patch("agents.knowledge_base.log_incident"):
        crew_cls.return_value.kickoff.return_value = '{"root_cause": "ci_test_failure", "confidence": "high"}'
        result = crew.run_pipeline(ctx)
    agent.assert_not_called()  # no patch-generation model call
    assert result["patch"]["source"] == "fix_library"
