"""Proof-carrying fixes: signed evidence bundles."""

from __future__ import annotations

import copy
import json
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agents import evidence

DIFF = "--- a/sample-app/math_utils.py\n+++ b/sample-app/math_utils.py\n-    return a + b\n+    return a * b\n"


@pytest.fixture()
def bundle():
    with patch.object(evidence, "_sandbox_image", return_value={"name": "ghostops-sandbox:py3.11", "id": "sha256:abc"}), \
         patch.object(evidence, "_git_commit", return_value="deadbeef"):
        return evidence.build(
            run_ref="run-1", alert_type="ci_failure",
            diagnosis={"root_cause": "ci_test_failure", "confidence": "high"},
            patch={"file_path": "sample-app/math_utils.py", "diff": DIFF, "explanation": "use *"},
            validation={"passed": True, "summary": "All 2 tests passed", "test_output": "2 passed",
                        "regression_test": {"path": "sample-app/test_r.py", "content": "def test(): ...", "failed_before": True, "passed_after": True}},
            blast={"passed": True, "metrics": {"files": 1, "added": 1, "removed": 1}},
            risk={"score": 12, "level": "low", "decision": "auto_merge_staging"},
            shield={"risk": "none", "quarantined": 0},
        )


@pytest.fixture()
def key():
    return Ed25519PrivateKey.generate()


def test_bundle_records_every_gate(bundle):
    assert bundle["format"] == evidence.FORMAT
    assert bundle["patch"]["diff_sha256"] == evidence.sha256(DIFF)
    gates = bundle["gates"]
    assert gates["opa_policy"] == "passed" and gates["blast_radius"]["passed"] and gates["sandbox"]["passed"]
    assert gates["regression_test"]["failed_before"] and gates["regression_test"]["passed_after"]
    assert bundle["environment"] == {"sandbox_image": {"name": "ghostops-sandbox:py3.11", "id": "sha256:abc"}, "ghostops_commit": "deadbeef", "python": bundle["environment"]["python"]}


def test_signature_verifies(bundle, key):
    signed = evidence.sign(bundle, key)
    assert evidence.verify(signed, key.public_key()) == {"signature_valid": True, "valid": True}


@pytest.mark.parametrize("tamper", [
    lambda b: b["gates"]["sandbox"].update(passed=False),
    lambda b: b["patch"].update(diff_sha256="0" * 64),
    lambda b: b["risk"].update(level="high"),
    lambda b: b["gates"]["regression_test"].update(failed_before=False),
])
def test_any_edit_breaks_the_signature(bundle, key, tamper):
    signed = evidence.sign(bundle, key)
    forged = copy.deepcopy(signed)
    tamper(forged["bundle"])
    assert evidence.verify(forged, key.public_key())["valid"] is False


def test_another_key_cannot_vouch(bundle, key):
    signed = evidence.sign(bundle, key)
    assert evidence.verify(signed, Ed25519PrivateKey.generate().public_key())["valid"] is False


def test_patch_must_match_the_validated_diff(bundle, key):
    signed = evidence.sign(bundle, key)
    assert evidence.verify(signed, key.public_key(), diff=DIFF)["patch_matches"] is True
    swapped = evidence.verify(signed, key.public_key(), diff=DIFF.replace("a * b", "a ** b"))
    assert swapped["patch_matches"] is False and swapped["valid"] is False


def test_signing_key_created_once_with_public_half(tmp_path):
    path = tmp_path / "evidence.key"
    k1 = evidence.signing_key(path)
    k2 = evidence.signing_key(path)
    assert evidence._public_pem(k1) == evidence._public_pem(k2)
    assert path.with_suffix(".pub").read_bytes() == evidence._public_pem(k1)
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_publish_writes_only_where_asked(tmp_path):
    target = tmp_path / "attest" / "ghostops.pub"
    evidence.publish(tmp_path / "k.key", target)
    assert target.read_bytes().startswith(b"-----BEGIN PUBLIC KEY-----")


def test_cli_verify(tmp_path, bundle, key, capsys):
    signed = evidence.sign(bundle, key)
    f = tmp_path / "bundle.json"
    f.write_text(json.dumps(signed))
    (tmp_path / "pub").write_bytes(evidence._public_pem(key))
    with patch.object(evidence, "PUBLIC_KEY_PATH", tmp_path / "pub"):
        assert evidence.main(["verify", str(f)]) == 0
        assert "signature  VALID" in capsys.readouterr().out
        signed["bundle"]["gates"]["sandbox"]["passed"] = False
        f.write_text(json.dumps(signed))
        assert evidence.main(["verify", str(f)]) == 1
