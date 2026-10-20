"""Risk scoring and graduated autonomy."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents import crew, deployer, governance, risk

GOOD = dict(
    diagnosis={"confidence": "high"},
    patch={"diff": "--- a/x\n+++ b/x\n-a\n+b\n"},
    validation={"summary": "All 6 tests passed in the sandbox.", "test_output": "6 passed"},
    blast={"metrics": {"files": 1, "added": 1, "removed": 1}},
    acceptance_rate=90.0,
)


def test_small_confident_tested_fix_is_low_risk():
    r = risk.score(**GOOD)
    assert r["level"] == "low" and r["decision"] == "auto_merge_staging"
    assert {f["factor"] for f in r["factors"]} >= {"diff size", "AI confidence", "track record", "test evidence"}


def test_breakdown_sums_to_score():
    r = risk.score(**{**GOOD, "acceptance_rate": None})
    assert r["score"] == max(0, sum(f["points"] for f in r["factors"]))


@pytest.mark.parametrize("change,factor", [
    ({"diagnosis": {"confidence": "low"}}, "AI confidence"),
    ({"validation": {"summary": "", "test_output": ""}}, "test evidence"),
    ({"acceptance_rate": 20.0}, "track record"),
    ({"shield": {"risk": "high"}}, "prompt injection"),
    ({"occurrences": 4}, "recurrence"),
])
def test_each_factor_raises_risk(change, factor):
    base = risk.score(**GOOD)["score"]
    worse = risk.score(**{**GOOD, **change})
    assert worse["score"] > base and any(f["factor"] == factor and f["points"] > 0 for f in worse["factors"])


def test_regression_test_lowers_risk():
    assert risk.score(**GOOD, regression_test=True)["score"] <= risk.score(**GOOD)["score"]


def test_numeric_confidence_understood():
    assert risk.score(**{**GOOD, "diagnosis": {"confidence": 0.98}})["level"] == "low"


def test_stacked_problems_are_high_risk():
    r = risk.score(
        diagnosis={"confidence": "low"}, patch={"diff": ""}, validation={"summary": "", "test_output": ""},
        blast={"metrics": {"files": 1, "added": 30, "removed": 10}}, shield={"risk": "elevated"}, acceptance_rate=10.0,
    )
    assert r["level"] == "high" and r["decision"] == "open_pr_and_page"


def test_score_is_clamped():
    assert 0 <= risk.score(**{**GOOD, "acceptance_rate": 100.0})["score"] <= 100


# ---- delivery by decision ----------------------------------------------------------------
def _deliver(level, *, token="t", staging=True, paused=False, merge_ok=True):
    report = {"level": level, "decision": risk.DECISIONS[level], "score": 10, "factors": []}
    create = MagicMock(return_value={"pr_created": True, "pr_number": 5, "pr_url": "u"})
    with patch.object(deployer, "GITHUB_TOKEN", token), patch.object(deployer, "branch_exists", return_value=staging), \
         patch.object(deployer, "merge_pr", return_value={"merged": merge_ok}) as merge, \
         patch.object(deployer, "label_pr", return_value=True) as label, \
         patch.object(deployer, "page_human", return_value={"paged": True}) as page, \
         patch.object(governance, "is_paused", return_value=paused):
        result = crew._deliver({"root_cause": "x"}, {}, {"passed": True}, report, {}, "run", create)
    return result, create, merge, label, page


def test_low_risk_merges_into_staging():
    result, create, merge, _, _ = _deliver("low")
    assert create.call_args.kwargs["base"] == "staging"
    merge.assert_called_once_with(5)
    assert result["auto_merged"] is True and result["decision"] == "auto_merge_staging"


def test_low_risk_without_staging_branch_is_a_normal_pr():
    result, create, merge, _, _ = _deliver("low", staging=False)
    assert create.call_args.kwargs["base"] is None
    merge.assert_not_called()


def test_kill_switch_blocks_auto_merge():
    result, _, merge, _, _ = _deliver("low", paused=True)
    merge.assert_not_called()
    assert "auto_merged" not in result


def test_medium_risk_just_opens_pr():
    result, create, merge, label, page = _deliver("medium")
    merge.assert_not_called(), label.assert_not_called(), page.assert_not_called()
    assert result["decision"] == "open_pr"


def test_high_risk_labels_and_pages():
    result, _, _, label, page = _deliver("high")
    label.assert_called_once_with(5, ["needs-human", "ghostops-high-risk"])
    page.assert_called_once()
    assert result["paged"] is True and result["labelled"] is True


def test_no_token_never_asks_github_about_branches():
    create = MagicMock(return_value={"pr_created": False})
    with patch.object(deployer, "branch_exists") as exists, patch.object(deployer, "GITHUB_TOKEN", ""):
        crew._deliver({"root_cause": "x"}, {}, {"passed": True}, {"decision": "auto_merge_staging"}, {}, "r", create)
    exists.assert_not_called()


# ---- deployer helpers --------------------------------------------------------------------
def test_page_human_needs_webhook(monkeypatch):
    monkeypatch.delenv("PAGER_WEBHOOK_URL", raising=False)
    assert deployer.page_human("x", {}) == {"paged": False, "reason": "PAGER_WEBHOOK_URL not set"}


def test_page_human_posts(monkeypatch):
    monkeypatch.setenv("PAGER_WEBHOOK_URL", "https://hooks.example/x")
    with patch.object(deployer.requests, "post", return_value=MagicMock(status_code=204)) as post:
        assert deployer.page_human("leak", {"pr": "u"})["paged"] is True
    assert "GhostOps needs a human: leak" in post.call_args.kwargs["json"]["text"]


def test_merge_pr_reports_github_refusal():
    with patch.object(deployer, "_gh", return_value=MagicMock(status_code=405, text="not mergeable")):
        assert deployer.merge_pr(5) == {"merged": False, "reason": "GitHub 405: not mergeable"}


def test_risk_section_in_pr_body():
    text = deployer._risk_section({"level": "low", "score": 12, "decision": "auto_merge_staging", "factors": [{"factor": "diff size", "points": 2, "why": "2 lines"}]})
    assert "Risk: low (12/100)" in text and "| diff size | +2 | 2 lines |" in text
