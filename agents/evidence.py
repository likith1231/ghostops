"""
Proof-carrying fixes
====================

Every validated fix gets an evidence bundle — what was changed, which gates it
passed and with what output, the regression test's fail-before/pass-after
proof, the risk score, the sandbox image and the GhostOps version — signed
with an Ed25519 key. The private key stays on the machine that runs GhostOps
(``agents/data/evidence.key``, git-ignored, created on first use with its
public half beside it). Publishing the public key is a deliberate step, so
anyone can check that a fix really passed the gates and that neither the
bundle nor the patch was altered afterwards:

    python -m agents.evidence publish                 # -> attestations/ghostops.pub
    python -m agents.evidence verify <bundle.json> [--diff patch.diff]
"""

from __future__ import annotations

import base64
import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from agents import knowledge_base

ROOT = Path(__file__).resolve().parent.parent
PUBLIC_KEY_PATH = ROOT / "attestations" / "ghostops.pub"
FORMAT = "ghostops-evidence/v1"


def _key_path() -> Path:
    return knowledge_base._DB_PATH.parent / "evidence.key"


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(bundle: dict[str, Any]) -> bytes:
    return json.dumps(bundle, sort_keys=True, separators=(",", ":"), default=str).encode()


def _public_pem(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)


def signing_key(key_path: Path | None = None) -> Ed25519PrivateKey:
    """The local signing key, created on first use with its public half beside it (``.pub``)."""
    path = key_path or _key_path()
    if path.exists():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)  # type: ignore[return-value]
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    path.chmod(0o600)
    path.with_suffix(".pub").write_bytes(_public_pem(key))
    return key


def publish(key_path: Path | None = None, target: Path = PUBLIC_KEY_PATH) -> Path:
    """Copy this machine's public key into the repo so reviewers can verify bundles."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_public_pem(signing_key(key_path)))
    return target


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _sandbox_image() -> dict[str, str]:
    from agents.config import SANDBOX_IMAGE

    try:
        import docker

        image = docker.from_env().images.get(SANDBOX_IMAGE)
        return {"name": SANDBOX_IMAGE, "id": image.id}
    except Exception:  # noqa: BLE001
        return {"name": SANDBOX_IMAGE, "id": "unavailable"}


def build(
    *,
    run_ref: str | None,
    alert_type: str,
    diagnosis: dict[str, Any],
    patch: dict[str, Any],
    validation: dict[str, Any],
    blast: dict[str, Any] | None,
    risk: dict[str, Any] | None,
    shield: dict[str, Any] | None,
) -> dict[str, Any]:
    """The unsigned evidence for one validated fix."""
    regression = validation.get("regression_test") or {}
    return {
        "format": FORMAT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "incident": {"run_ref": run_ref, "alert_type": alert_type, "root_cause": diagnosis.get("root_cause"), "confidence": str(diagnosis.get("confidence"))},
        "patch": {"file": patch.get("file_path"), "diff_sha256": sha256(patch.get("diff", "")), "explanation": patch.get("explanation", "")[:500]},
        "gates": {
            "opa_policy": "passed",  # a patch only reaches the sandbox after OPA passes it
            "blast_radius": {"passed": (blast or {}).get("passed"), "metrics": (blast or {}).get("metrics")},
            "sandbox": {"passed": validation.get("passed") is True, "summary": validation.get("summary"), "output_sha256": sha256(validation.get("test_output", ""))},
            "regression_test": {
                "path": regression.get("path"),
                "test_sha256": sha256(regression["content"]) if regression.get("content") else None,
                "failed_before": regression.get("failed_before"),
                "passed_after": regression.get("passed_after"),
            } if regression else None,
            "counterfactual_replay": {k: (validation.get("replay") or {}).get(k) for k in ("verdict", "recorded_requests")} if validation.get("replay") else None,
        },
        "risk": {"score": (risk or {}).get("score"), "level": (risk or {}).get("level"), "decision": (risk or {}).get("decision")},
        "shield": {"risk": (shield or {}).get("risk"), "quarantined": (shield or {}).get("quarantined")},
        "environment": {"sandbox_image": _sandbox_image(), "ghostops_commit": _git_commit(), "python": platform.python_version()},
    }


def sign(bundle: dict[str, Any], key: Ed25519PrivateKey | None = None) -> dict[str, Any]:
    """The bundle wrapped with its Ed25519 signature and the signer's public key fingerprint."""
    key = key or signing_key()
    raw_pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {
        "bundle": bundle,
        "signature": base64.b64encode(key.sign(canonical(bundle))).decode(),
        "key_fingerprint": hashlib.sha256(raw_pub).hexdigest()[:16],
    }


def verify(signed: dict[str, Any], public_key: Ed25519PublicKey | None = None, diff: str | None = None) -> dict[str, Any]:
    """Check the signature (and, given the diff, that the patch is the one that was validated)."""
    if public_key is None:
        source = PUBLIC_KEY_PATH if PUBLIC_KEY_PATH.exists() else _key_path().with_suffix(".pub")
        public_key = serialization.load_pem_public_key(source.read_bytes())  # type: ignore[assignment]
    try:
        public_key.verify(base64.b64decode(signed["signature"]), canonical(signed["bundle"]))
        signature_ok = True
    except (InvalidSignature, KeyError, ValueError):
        signature_ok = False
    result = {"signature_valid": signature_ok}
    if diff is not None:
        result["patch_matches"] = sha256(diff) == signed.get("bundle", {}).get("patch", {}).get("diff_sha256")
    result["valid"] = all(result.values())
    return result


def main(argv: list[str]) -> int:
    if argv[:1] == ["publish"]:
        print(f"public key written to {publish()}")
        return 0
    if len(argv) < 2 or argv[0] != "verify":
        print("usage: python -m agents.evidence publish | verify <bundle.json> [--diff patch.diff]")
        return 2
    signed = json.loads(Path(argv[1]).read_text())
    diff = Path(argv[argv.index("--diff") + 1]).read_text() if "--diff" in argv else None
    result = verify(signed, diff=diff)
    b = signed["bundle"]
    print(f"incident   {b['incident']['root_cause']} ({b['incident']['alert_type']})")
    print(f"patch      {b['patch']['file']}  sha256 {b['patch']['diff_sha256'][:16]}…")
    print(f"gates      sandbox={'pass' if b['gates']['sandbox']['passed'] else 'FAIL'}  blast-radius={b['gates']['blast_radius']['passed']}")
    rt = b["gates"].get("regression_test")
    if rt:
        print(f"regression failed before={rt['failed_before']}  passed after={rt['passed_after']}")
    print(f"signature  {'VALID' if result['signature_valid'] else 'INVALID'}  (key {signed.get('key_fingerprint')})")
    if "patch_matches" in result:
        print(f"patch      {'matches' if result['patch_matches'] else 'DOES NOT MATCH'} the validated diff")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
