"""Shared result type for the GhostOps anomaly detectors."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Detection:
    """One detected anomaly, tied to a use case from the use-case catalogue."""

    use_case: str  # "A1", "B1", "C5", ...
    title: str
    severity: str  # "low" | "medium" | "high" | "critical"
    confidence: float  # 0.0 - 1.0
    evidence: list[str] = field(default_factory=list)
    suggested_action: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
