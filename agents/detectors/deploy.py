"""Deployment-state detectors for the Category C use cases (C1, C2, C3, C5, C6)."""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence

from agents.detectors.base import Detection

_PULL_REASONS = {"ErrImagePull", "ImagePullBackOff"}


def detect_canary_regression(
    canary_error_rate: float, stable_error_rate: float, ratio: float = 10.0, min_rate: float = 0.02
) -> Detection | None:
    """C1: canary pods erroring far more than stable pods."""
    if canary_error_rate < min_rate:
        return None
    if canary_error_rate < ratio * max(stable_error_rate, 0.001):
        return None
    return Detection(
        use_case="C1",
        title="Canary regression",
        severity="critical",
        confidence=0.9,
        evidence=[f"canary {canary_error_rate:.1%} vs stable {stable_error_rate:.1%} error rate"],
        suggested_action="Roll back the canary to the previous revision, then diff the versions for the cause.",
    )


def detect_config_drift(declared: Mapping[str, str], live: Mapping[str, str]) -> Detection | None:
    """C2: live ConfigMap values that differ from those declared in Git."""
    drift = [
        f"{key}: declared={declared.get(key)!r} live={live.get(key)!r}"
        for key in sorted(set(declared) | set(live))
        if declared.get(key) != live.get(key)
    ]
    if not drift:
        return None
    return Detection(
        use_case="C2",
        title="Configuration drift",
        severity="medium",
        confidence=1.0,
        evidence=drift,
        suggested_action="Commit the live change to Git if intended, otherwise re-apply the declared state.",
    )


def detect_crashloop(pods: Sequence[Mapping[str, str]]) -> Detection | None:
    """C3: pods in CrashLoopBackOff, with their last termination reason."""
    bad = [p for p in pods if p.get("waiting_reason") == "CrashLoopBackOff"]
    if not bad:
        return None
    reasons = sorted({p.get("last_terminated_reason", "unknown") for p in bad})
    return Detection(
        use_case="C3",
        title="CrashLoopBackOff",
        severity="critical",
        confidence=0.9,
        evidence=[f"{len(bad)}/{len(pods)} pods crash-looping", f"last termination: {', '.join(reasons)}"],
        suggested_action="Diff the last Helm release; check image tag, missing Secrets and resource limits.",
    )


def detect_image_pull_failure(pods: Sequence[Mapping[str, str]]) -> Detection | None:
    """C5: pods that cannot pull their image."""
    bad = [p for p in pods if p.get("waiting_reason") in _PULL_REASONS]
    if not bad:
        return None
    images = sorted({p.get("image", "?") for p in bad})
    return Detection(
        use_case="C5",
        title="Image pull failure",
        severity="high",
        confidence=0.95,
        evidence=[f"{len(bad)} pods failing to pull", f"images: {', '.join(images)}"],
        suggested_action="Verify the tag exists in the registry and the imagePullSecret is valid in this namespace.",
    )


def detect_hpa_flapping(replicas: Sequence[int], min_direction_changes: int = 4, min_swing: int = 3) -> Detection | None:
    """C6: replica count repeatedly swinging up and down."""
    if len(replicas) < 6:
        return None
    deltas = [b - a for a, b in zip(replicas, replicas[1:]) if b != a]
    changes = sum(1 for a, b in zip(deltas, deltas[1:]) if (a > 0) != (b > 0))
    swing = max(replicas) - min(replicas)
    if changes < min_direction_changes or swing < min_swing:
        return None
    return Detection(
        use_case="C6",
        title="HPA autoscaler thrashing",
        severity="medium",
        confidence=0.85,
        evidence=[f"{changes} direction changes, swing {min(replicas)}-{max(replicas)}, mean {statistics.fmean(replicas):.1f}"],
        suggested_action="Raise scaleDown.stabilizationWindowSeconds and relax the target utilisation.",
    )
