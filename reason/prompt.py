"""Builds the reasoning prompt from what Evidence actually contains.

Critical design rule from the project brief: the prompt must be built
conditionally in code from each field's confidence label — never left to the
model to notice that a field is uncertain. When a field is "unavailable",
its description contains no numeric value and explicitly forbids stating
one. When "estimated", the description is flagged as such rather than
presented as a measured fact.
"""

from __future__ import annotations

from pathlib import Path

from core.contracts import Evidence
from risk.score import RiskAssessment


def _describe_field(name: str, value: float | None, confidence: str, unit: str) -> str:
    if confidence == "unavailable":
        return f"- {name}: unavailable — no {name} data could be derived; do not state a {name} value"
    if value is None:  # pragma: no cover - Evidence's own invariant forbids this combination
        raise ValueError(f"{name} confidence is {confidence!r} but value is None")
    if confidence == "estimated":
        return f"- {name}: {value:.3f}{unit} (ESTIMATED — not calibrated, state this uncertainty explicitly)"
    return f"- {name}: {value:.3f}{unit} (calibrated measurement)"


def build_evidence_block(evidence: Evidence) -> str:
    lines = [
        _describe_field("depth", evidence.depth_m, evidence.depth_confidence, "m"),
        _describe_field("position", evidence.position_m, evidence.position_confidence, "m"),
        _describe_field("amplitude", evidence.amplitude, evidence.amplitude_confidence, ""),
        f"- hyperbola width: {evidence.hyperbola_width_px:.1f}px",
    ]
    lines.append(
        f"- other detections in this frame: {', '.join(evidence.neighbours)}"
        if evidence.neighbours
        else "- other detections in this frame: none"
    )
    lines.append(
        f"- prior survey passes at this location: {len(evidence.prior_passes)} found"
        if evidence.prior_passes
        else "- prior survey passes at this location: none"
    )
    return "\n".join(lines)


def build_prompt(evidence: Evidence, risk: RiskAssessment, template_path: Path) -> str:
    template = template_path.read_text(encoding="utf-8")
    return template.format(
        detection_class=evidence.detection_class,
        detection_confidence=f"{evidence.detection_confidence:.2%}",
        evidence_block=build_evidence_block(evidence),
        risk_level=risk.level,
        risk_score=f"{risk.score:.3f}",
        risk_rules_fired=", ".join(risk.rules_fired) if risk.rules_fired else "none",
    )
